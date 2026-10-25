"""Ask a question. Step 1 parses it with rules and answers with pandas (no LLM). Step 2, only for
open questions the rules can't handle, sends Claude the schema, KPI values and the aggregated
chart tables (never the raw rows). Claude replies with text and/or a query spec that pandas runs."""

import json
import re

import pandas as pd
import plotly.express as px

from insight_app import theme
from insight_app.domain_ai import ai_available, call_claude
from insight_app.domain_rules import primary_measure
from insight_app.engine import _usable_dims as usable_dims
from insight_app.engine import DIM_NOUNS, aggregate, fmt_value, measure_agg, period_label, time_bucket
from insight_app.profiler import MONTH_LOOKUP

MEASURE_WORDS = {
    "revenue": ["revenue", "sales", "turnover", "income"], "profit": ["profit", "margin"],
    "cost": ["cost", "expense", "spend"], "quantity": ["units", "quantity", "qty", "volume"],
    "salary": ["salary", "salaries", "pay", "ctc", "compensation"], "csat": ["csat", "satisfaction"],
    "tickets": ["tickets", "cases"], "resolution_time": ["resolution", "response time", "handle time"],
    "tenure": ["tenure", "experience"], "age": ["age"], "discount": ["discount"],
    "conversions": ["conversion", "conversions"], "clicks": ["clicks"], "impressions": ["impressions"],
    "stock": ["stock", "inventory"], "rating": ["rating", "score", "performance"],
}
DIM_WORDS = {
    "region": ["region", "state", "city", "location", "country", "zone", "territory", "branch"],
    "product": ["product", "products", "item", "sku"], "category": ["category", "categories", "segment"],
    "department": ["department", "departments", "dept", "team"], "channel": ["channel", "channels", "source"],
    "agent": ["agent", "agents"], "salesperson": ["rep", "reps", "salesperson", "seller", "sales person"],
    "job": ["job", "role", "roles", "designation", "title", "position"],
    "customer": ["customer", "customers", "client", "clients", "account"], "gender": ["gender"],
    "priority": ["priority"], "status": ["status"], "stage": ["stage"],
}
OPEN_ENDED = re.compile(r"\b(why|explain|recommend|should|suggest|insight|improve|reason|predict|forecast|"
                        r"correlat|relationship|compare|versus|vs|summari[sz]e|what does|meaning|risk|anomal|unusual)\b", re.I)
TREND = re.compile(r"\b(trend|over time|monthly|by month|per month|weekly|by week|daily|by day|growth|grow|quarterly|by quarter|timeline)\b", re.I)
TOP = re.compile(r"\b(top|highest|most|best|max|maximum|largest|biggest|leading|leader)\b", re.I)
BOTTOM = re.compile(r"\b(bottom|lowest|least|worst|min|minimum|smallest|weakest)\b", re.I)
COUNT = re.compile(r"\b(how many|number of|count)\b", re.I)
AVG = re.compile(r"\b(average|avg|mean|typical)\b", re.I)
UNIQUE = re.compile(r"\b(unique|distinct)\b", re.I)
RETURNS = re.compile(r"\b(returns?|returned|refunds?|refunded|cancel\w*)\b", re.I)
FEES = re.compile(r"\b(postage|shipping|delivery charges?|fees?|carriage)\b", re.I)
# friendly words for suggested questions; each one is in MEASURE_WORDS / DIM_WORDS so the parser maps it back
FRIENDLY = {"revenue": "revenue", "quantity": "units", "salary": "salary", "tickets": "tickets", "csat": "CSAT",
            "cost": "cost", "profit": "profit", "conversions": "conversions", "stock": "stock", "rating": "rating",
            "product": "product", "customer": "customer", "region": "region", "category": "category",
            "department": "department", "channel": "channel", "agent": "agent", "salesperson": "rep", "job": "job role"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _has_word(q: str, word: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word.lower())}(?![a-z0-9])", q) is not None


# ---------- shared executor ----------

def run_query(df: pd.DataFrame, spec: dict, profiles_by_name: dict, roles: dict) -> dict:
    """spec: measure, agg, dim, trend, filters [(col, op, value)], sort, top_n, chart.
    Returns {headline, table, fig, filters_text}."""
    d, notes = df, []
    for col, op, value in spec.get("filters", []):
        if col not in d.columns:
            continue
        s = d[col]
        try:
            if op == "contains":
                mask = s.astype(str).str.contains(str(value), case=False, regex=False)
            elif op == "in":
                vals = [str(v).lower() for v in (value if isinstance(value, list) else str(value).split(","))]
                mask = s.astype(str).str.lower().str.strip().isin([v.strip() for v in vals])
            elif op in (">", "<", ">=", "<="):
                num = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
                target = num if pd.notna(num) else pd.to_datetime(value, errors="coerce")
                mask = {"<": s < target, ">": s > target, "<=": s <= target, ">=": s >= target}[op]
            elif op == "year":
                mask = s.dt.year == int(value)
            elif op == "month":
                mask = s.dt.month == int(value)
            elif op == "day":
                mask = s.dt.day == int(value)
            elif op == "!=":
                mask = s.astype(str).str.lower() != str(value).lower()
            else:
                mask = s.astype(str).str.lower().str.strip() == str(value).lower().strip()
        except (TypeError, ValueError, AttributeError):
            continue
        d = d[mask.fillna(False)]
        if op == "year":
            notes.append(f"{col} in {value}")
        elif op == "month":
            notes.append(f"{col} in {pd.Timestamp(2000, int(value), 1):%B}")
        elif op == "day":
            notes.append(f"day {value}")
        else:
            notes.append(f"{col} {'=' if op == '==' else op} {value}")
    filters_text = "; ".join(notes)

    measure, agg, dim = spec.get("measure") or "", spec.get("agg") or "sum", spec.get("dim") or ""
    if measure and measure not in d.columns:
        measure = ""
    if not measure and agg not in ("count",):
        agg = "count"
    if agg in ("sum", "mean", "median", "min", "max") and measure and not pd.api.types.is_numeric_dtype(d[measure]):
        agg = "nunique" if agg != "count" else agg
    label = "Records" if agg == "count" else (f"Avg {measure}" if agg == "mean" else f"{agg.title()} {measure}" if agg != "sum" else measure)
    scope = f" ({filters_text})" if filters_text else ""
    vfmt = "percent" if agg in ("mean", "median", "min", "max") and profiles_by_name.get(measure, {}).get("percent_fraction") else "number"

    if d.empty:
        return {"headline": f"No rows match{scope}.", "table": None, "fig": None, "filters_text": filters_text}

    date = roles.get("date")
    if spec.get("trend") and date in d.columns:
        order = profiles_by_name.get(date, {}).get("period_order")
        if order or pd.api.types.is_datetime64_any_dtype(d[date]):
            bucket, period = time_bucket(d[date], order)
            t = aggregate(d.assign(_p=bucket), "_p", measure, agg).rename(columns={"_p": period.title(), "value": label})
            t = t.sort_values(period.title())
            fig = px.line(t, x=period.title(), y=label, markers=True, color_discrete_sequence=[theme.PURPLE])
            first, last = t[label].iloc[0], t[label].iloc[-1]
            peak = t.loc[t[label].idxmax()]
            peak_lbl = period_label(peak[period.title()], period)
            change = f", {((last - first) / abs(first)):+.0%} from first to last {period}" if first and period != "day" else ""
            return {"headline": f"{label} by {period}{scope}: peak {fmt_value(peak[label], vfmt)} in {peak_lbl}{change}.",
                    "table": t, "fig": fig, "filters_text": filters_text}

    if dim and dim in d.columns:
        t = aggregate(d, dim, measure, agg).dropna()
        asc = spec.get("sort") == "asc"
        t = t.sort_values("value", ascending=asc).rename(columns={"value": label})
        n = int(spec.get("top_n") or 10)
        shown = t.head(n)
        total = t[label].sum()
        lead = shown.iloc[0]
        share = f", {lead[label] / total:.0%} of the total" if agg in ("sum", "count") and total else ""
        word = "lowest" if asc else "highest"
        if agg == "count":
            headline = f"{lead[dim]} has the {'fewest' if asc else 'most'} records{scope}: {fmt_value(lead[label], vfmt)}{share}."
        else:
            headline = f"{lead[dim]} has the {word} {label}{scope}: {fmt_value(lead[label], vfmt)}{share}."
        if len(shown) > 1:
            headline += f" Next: {shown.iloc[1][dim]} ({fmt_value(shown.iloc[1][label], vfmt)})."
        chart = spec.get("chart") or "bar"
        if chart == "donut" and agg in ("sum", "count"):
            fig = px.pie(shown, names=dim, values=label, hole=0.55, color_discrete_sequence=theme.CHART_COLORS)
        else:
            fig = px.bar(shown.assign(**{dim: shown[dim].astype(str)}), x=dim, y=label, color_discrete_sequence=[theme.PURPLE])
        return {"headline": headline, "table": shown, "fig": fig, "filters_text": filters_text}

    if agg == "count":
        value = len(d)
    elif agg == "nunique":
        value = d[measure].nunique()
    else:
        value = getattr(pd.to_numeric(d[measure], errors="coerce").dropna(), agg)()
    if agg == "count":
        headline = f"{len(d):,} records match{scope}." if filters_text else f"There are {len(d):,} records."
    else:
        headline = f"{label}{scope}: {fmt_value(float(value), vfmt)} across {len(d):,} records."
    return {"headline": headline,
            "table": d.head(50), "fig": None, "filters_text": filters_text}


# ---------- rule-based parse ----------

def parse_rules(question: str, df: pd.DataFrame, roles: dict, profiles: list, domain: str):
    """Returns (spec, matched_something)."""
    q = question.lower()
    qn = _norm(question)
    by_name = {p["name"]: p for p in profiles}
    matched = False

    def mentioned(cols):
        hits = [c for c in cols if (len(_norm(c)) >= 3 and _norm(c) in qn) or _has_word(q, str(c).lower())]
        return max(hits, key=lambda c: len(_norm(c))) if hits else None

    numeric = [p["name"] for p in profiles if p["kind"] == "numeric"]
    dims = [p["name"] for p in profiles if p["kind"] in ("categorical", "boolean", "identifier", "period")]

    measure = mentioned(numeric)
    if not measure:
        for role, words in MEASURE_WORDS.items():
            if role in roles and roles[role] in numeric and any(_has_word(q, w) for w in words):
                measure = roles[role]
                break
    matched |= bool(measure)
    explicit_measure = bool(measure)

    dim = mentioned([c for c in dims if c != measure])
    if not dim:
        for role, words in DIM_WORDS.items():
            if role in roles and roles[role] in df.columns and any(_has_word(q, w) for w in words):
                dim = roles[role]
                break
    matched |= bool(dim)

    filters = []
    # Line Type values ("Product") collide with ordinary words, so it is matched by RETURNS / FEES below instead
    for col in [p["name"] for p in profiles if p["kind"] in ("categorical", "boolean") and p["nunique"] <= 500
                and p["name"] != roles.get("line_type")]:
        values = df[col].dropna().astype(str).unique()
        hits = [v for v in values if len(v) >= 3 and (_has_word(q, v) or _has_word(q, v + "s") or _has_word(q, v + "ed"))]
        if hits:
            best = max(hits, key=len)
            if col == dim and not (TOP.search(q) or BOTTOM.search(q)):
                dim = ""
            filters.append((col, "==", best))
            matched = True

    line_type = roles.get("line_type")
    if line_type in df.columns and RETURNS.search(q):
        filters.append((line_type, "==", "Return"))
        matched = True
    elif line_type in df.columns and FEES.search(q):
        filters.append((line_type, "==", "Shipping & fees"))
        matched = True
    elif line_type in df.columns and dim and dim == roles.get("product"):
        # product rankings leave out postage/fee lines and returns, like the dashboard does
        filters.append((line_type, "==", "Product"))

    date = roles.get("date")
    if date and pd.api.types.is_datetime64_any_dtype(df.get(date, pd.Series(dtype=float))):
        year = re.search(r"\b(19|20)\d{2}\b", q)
        if year:
            filters.append((date, "year", int(year.group())))
            matched = True
        for name, num in MONTH_LOOKUP.items():
            if len(name) >= 3 and _has_word(q, name) and not (name == "may" and "may i" in q):
                filters.append((date, "month", num))
                day = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{name}\b|\b{name}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", q)
                if day:
                    filters.append((date, "day", int(day.group(1) or day.group(2))))
                matched = True
                break

    if COUNT.search(q) or (RETURNS.search(q) and line_type in df.columns and not explicit_measure):
        agg = "count"
        matched = True
    elif UNIQUE.search(q) and dim:
        agg, measure, dim = "nunique", dim, ""
    elif AVG.search(q):
        agg = "mean"
    else:
        agg = measure_agg(measure, roles, by_name) if measure else "sum"
    if not measure and agg != "count":
        measure = primary_measure(domain, roles, profiles) or ""
        if not measure:
            agg = "count"
        elif agg != "mean":
            agg = measure_agg(measure, roles, by_name)

    top_n = re.search(r"\b(?:top|bottom)\s+(\d{1,2})\b", q)
    spec = {"measure": measure, "agg": agg, "dim": dim, "trend": bool(TREND.search(q)), "filters": filters,
            "sort": "asc" if BOTTOM.search(q) else "desc", "top_n": int(top_n.group(1)) if top_n else 10, "chart": "bar"}
    matched |= spec["trend"] or bool(TOP.search(q) or BOTTOM.search(q)) and bool(dim)
    return spec, matched


def suggest_questions(roles: dict, profiles: list, domain: str, metric_table: dict = None, df: pd.DataFrame = None) -> list:
    """Starter questions built from the columns, all answerable by the rule parser."""
    if metric_table and df is not None and len(df):
        names = df[metric_table["label"]].astype(str).tolist()
        out = [f"What was {n} in {metric_table['latest']}?" for n in names[:3]]
        out.append(f"Which {metric_table['label']} has the highest {metric_table['latest']}?")
        return out
    by_name = {p["name"]: p for p in profiles}
    inverse = {v: k for k, v in roles.items()}
    measure = primary_measure(domain, roles, profiles)
    dims = [d for d in usable_dims(profiles, roles) if inverse.get(d) != "line_type"]
    name = lambda col: FRIENDLY.get(inverse.get(col), col)  # noqa: E731
    out = []
    if measure:
        word = "average " if measure_agg(measure, roles, by_name) == "mean" else "total "
        m = name(measure)
        if dims:
            out.append(f"Which {name(dims[0])} has the highest {word}{m}?")
        if roles.get("date"):
            out.append(f"Show the {m} trend over time")
        plural = lambda col: DIM_NOUNS.get(inverse.get(col), col)  # noqa: E731
        if len(dims) > 1:
            out.append(f"Top 5 {plural(dims[1])} by {m}")
        elif dims:
            out.append(f"Top 5 {plural(dims[0])} by {m}")
    if roles.get("line_type"):
        out.append("Which products were returned?")
    elif dims and len(out) < 5:
        top_value = by_name[dims[0]]["samples"][0] if by_name[dims[0]]["samples"] else ""
        if top_value:
            out.append(f"How many records are in {top_value}?")
    if not out:
        out.append("How many records are there?")
    return out[:5]


# ---------- AI step ----------

AI_SYSTEM = """You answer a manager's question about a dashboard built from their data.
You get the table schema, the KPI values on screen, aggregated chart tables and pandas-computed insights.
You never see the raw rows.

If the context already holds what is needed, answer in `answer` (2 to 4 short sentences, plain English,
specific numbers from the context) and set use_query to false.
If the answer needs a calculation the context lacks, set use_query to true and fill `query`; pandas will run it
and show the result under your text, so write `answer` as one sentence saying what the table shows and do not
invent numbers.
Query fields: metric = exact numeric column ("" to count rows); agg = sum|mean|median|min|max|count|nunique;
group_by = exact column or ""; time_trend = true for a trend over the date column; filters = list of
{column, op (==, !=, >, <, >=, <=, contains, in), value}; sort = desc|asc; top_n = 1 to 25; chart = bar|line|donut|none.
Write plainly. No em dashes. If the data cannot answer the question, say so and suggest what data would."""


def _ai_schema():
    s = {"type": "string"}
    flt = {"type": "object", "additionalProperties": False,
           "properties": {"column": s, "op": {"type": "string", "enum": ["==", "!=", ">", "<", ">=", "<=", "contains", "in"]}, "value": s},
           "required": ["column", "op", "value"]}
    query = {"type": "object", "additionalProperties": False,
             "properties": {"metric": s, "agg": {"type": "string", "enum": ["sum", "mean", "median", "min", "max", "count", "nunique"]},
                            "group_by": s, "time_trend": {"type": "boolean"}, "filters": {"type": "array", "items": flt},
                            "sort": {"type": "string", "enum": ["desc", "asc"]}, "top_n": {"type": "integer"},
                            "chart": {"type": "string", "enum": ["bar", "line", "donut", "none"]}},
             "required": ["metric", "agg", "group_by", "time_trend", "filters", "sort", "top_n", "chart"]}
    return {"type": "object", "additionalProperties": False,
            "properties": {"answer": s, "use_query": {"type": "boolean"}, "query": query},
            "required": ["answer", "use_query", "query"]}


def _context(ctx: dict) -> str:
    tables = []
    for title, t in ctx.get("chart_tables", [])[:12]:
        if t is None or t.empty:
            continue
        tables.append(f"## {title}\n{t.head(12).to_csv(index=False)}")
    payload = {
        "domain": ctx["domain"], "file": ctx.get("filename"), "active_filters": ctx.get("filters_text") or "none",
        "rows_in_view": ctx.get("rows"), "column_roles": ctx["roles"], "schema": ctx["schema"],
        "kpis": ctx.get("kpi_values", []), "insights": ctx.get("insights", []),
    }
    return json.dumps(payload, default=str, indent=1) + "\n\nChart tables (CSV):\n" + "\n".join(tables)


def answer(question: str, df: pd.DataFrame, ctx: dict) -> dict:
    roles, profiles, domain = ctx["roles"], ctx["profiles"], ctx["domain"]
    by_name = {p["name"]: p for p in profiles}
    spec, matched = parse_rules(question, df, roles, profiles, domain)
    open_ended = bool(OPEN_ENDED.search(question))

    if (matched and not open_ended) or not ai_available():
        res = run_query(df, spec, by_name, roles)
        note = "" if matched else "I couldn't match this to specific columns, so here is the headline number. Add a column or value name from your data, or add an API key for open questions."
        return {"question": question, "text": res["headline"], "note": note, "table": res["table"], "fig": res["fig"], "source": "pandas"}

    try:
        out = call_claude(AI_SYSTEM, f"{_context(ctx)}\n\nQuestion: {question}", _ai_schema(), max_tokens=4000)
    except Exception as e:  # noqa: BLE001
        res = run_query(df, spec, by_name, roles)
        return {"question": question, "text": res["headline"], "note": f"AI answer unavailable ({type(e).__name__}); showing the pandas result.",
                "table": res["table"], "fig": res["fig"], "source": "pandas"}

    result = {"question": question, "text": out.get("answer", ""), "note": "", "table": None, "fig": None, "source": "ai"}
    if out.get("use_query"):
        qy = out["query"]
        q_spec = {"measure": qy.get("metric", ""), "agg": qy.get("agg", "sum"), "dim": qy.get("group_by", ""),
                  "trend": bool(qy.get("time_trend")), "sort": qy.get("sort", "desc"),
                  "top_n": max(1, min(25, int(qy.get("top_n") or 10))), "chart": qy.get("chart", "bar"),
                  "filters": [(f["column"], f["op"], f["value"]) for f in qy.get("filters", [])]}
        res = run_query(df, q_spec, by_name, roles)
        result.update(table=res["table"], fig=res["fig"] if qy.get("chart") != "none" else None,
                      text=f"{result['text']} {res['headline']}".strip(), source="ai+pandas")
    return result
