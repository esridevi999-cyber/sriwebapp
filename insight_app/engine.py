"""Runs validated KPI / chart specs with pandas. Nothing the AI returns is ever executed as code:
specs only name columns and pick from the whitelisted aggregations in kpi_library."""

import re

import numpy as np
import pandas as pd

from insight_app.domain_rules import LOWER_IS_BETTER, MEAN_ROLES, primary_measure
from insight_app.kpi_library import CHART_AGGS, CHART_TEMPLATES, KPI_AGGS, KPI_TEMPLATES, TAB_OF
from insight_app.profiler import is_truthy

MAX_KPIS = 8
# KPIs that already say how many rows/entities there are, so "Records" would repeat them
COUNT_TITLES = {"Headcount", "Orders", "Transactions", "Tickets", "Patients", "Students"}
MAX_CHARTS = 16
CALC_REVENUE = "Revenue (Qty x Price)"
DIM_NOUNS = {"product": "products", "customer": "customers", "region": "regions", "category": "categories",
             "salesperson": "reps", "agent": "agents", "department": "departments", "channel": "channels", "job": "roles"}
# order ids like C536379 mark cancellations in most e-commerce exports
RETURN_ID = re.compile(r"^\s*(c|cn|cr|r|ret|rtn)[-_ ]?\d", re.I)
FEE_LINE = re.compile(r"(?:postage|shipping|carriage|delivery charge|freight|bank charge|amazon fee|manual|discount|adjust)", re.I)


# ---------- derived columns ----------

def add_derived(df: pd.DataFrame, roles: dict, profiles: list) -> tuple:
    """Fills obvious gaps with pandas so templates still work: revenue = qty x price,
    profit = revenue - cost, tenure from hire date, attrition from exit date, and a
    Line Type (Product / Return / Shipping & fees) for order-line sales exports."""
    df = df.copy()
    roles = dict(roles)
    added = []
    if "revenue" not in roles and {"quantity", "unit_price"} <= roles.keys():
        df[CALC_REVENUE] = df[roles["quantity"]] * df[roles["unit_price"]]
        roles["revenue"] = CALC_REVENUE
        added.append((CALC_REVENUE, "numeric", True))
    if "line_type" not in roles and "revenue" in roles and ("order_id" in roles or "quantity" in roles):
        is_return = pd.Series(False, index=df.index)
        if "order_id" in roles:
            is_return |= df[roles["order_id"]].astype(str).str.match(RETURN_ID)
        if "quantity" in roles:
            is_return |= pd.to_numeric(df[roles["quantity"]], errors="coerce") < 0
        is_fee = df[roles["product"]].astype(str).str.contains(FEE_LINE) if "product" in roles else pd.Series(False, index=df.index)
        if is_return.any() or is_fee.any():
            df["Line Type"] = np.where(is_return, "Return", np.where(is_fee, "Shipping & fees", "Product"))
            roles["line_type"] = "Line Type"
            added.append(("Line Type", "categorical", False))
    if "profit" not in roles and {"revenue", "cost"} <= roles.keys():
        df["Profit (calc)"] = df[roles["revenue"]] - df[roles["cost"]]
        roles["profit"] = "Profit (calc)"
        added.append(("Profit (calc)", "numeric", True))
    if "tenure" not in roles and "hire_date" in roles:
        end = df[roles["exit_date"]].fillna(pd.Timestamp.today()) if "exit_date" in roles else pd.Timestamp.today()
        df["Tenure (yrs, calc)"] = ((end - df[roles["hire_date"]]).dt.days / 365.25).round(1)
        roles["tenure"] = "Tenure (yrs, calc)"
        added.append(("Tenure (yrs, calc)", "numeric", False))
    if "attrition" not in roles and "exit_date" in roles:
        df["Exited (calc)"] = df[roles["exit_date"]].notna()
        roles["attrition"] = "Exited (calc)"
        added.append(("Exited (calc)", "boolean", False))
    profiles = list(profiles)
    for name, kind, currency in added:
        s = df[name]
        p = {"name": name, "rows": len(df), "kind": kind, "nulls_pct": 0.0, "nunique": int(s.nunique()), "currency": currency,
             "percent": False, "percent_fraction": False, "unit": None, "period_order": None,
             "samples": s.dropna().astype(str).head(3).tolist()}
        if kind == "numeric":
            p.update(min=float(s.min()), max=float(s.max()), mean=float(s.mean()), sum=float(s.sum()))
        profiles.append(p)
    return df, roles, profiles


# ---------- spec resolution ----------

def _resolve_field(value: str, roles: dict, columns) -> tuple:
    """Returns (column_or_empty, ok). '@role' maps via roles; plain names must exist."""
    if not value:
        return "", True
    if value.startswith("@"):
        col = roles.get(value[1:])
        return (col, True) if col in columns else ("", False)
    return (value, True) if value in columns else ("", False)


def _resolve_where(spec: dict, out: dict, roles: dict, columns):
    """Row filter (where_column / where_value regex / where_not). Optional: drops out if its column is missing."""
    col, ok = _resolve_field(spec.get("where_column", "") or "", roles, columns)
    if ok and col and spec.get("where_value"):
        out.update(where_column=col, where_value=spec["where_value"], where_not=bool(spec.get("where_not")))
    else:
        out.update(where_column="", where_value="", where_not=False)


def apply_where(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    col = spec.get("where_column")
    if not col or col not in df.columns:
        return df
    try:
        hit = df[col].astype(str).str.strip().str.fullmatch(spec["where_value"], case=False)
    except re.error:
        hit = df[col].astype(str).str.lower() == str(spec["where_value"]).lower()
    return df[~hit] if spec.get("where_not") else df[hit]


def resolve_kpi(spec: dict, roles: dict, columns):
    if spec.get("agg") not in KPI_AGGS:
        return None
    if any(not _resolve_field(r, roles, columns)[1] for r in spec.get("requires", [])):
        return None
    out = dict(spec)
    for f in ("column", "column2"):
        out[f], ok = _resolve_field(spec.get(f, ""), roles, columns)
        if not ok:
            return None
    _resolve_where(spec, out, roles, columns)
    needs_col = spec["agg"] != "count_rows"
    needs_col2 = spec["agg"] in ("ratio_sum", "per_unique", "repeat_rate")
    if (needs_col and not out["column"]) or (needs_col2 and not out["column2"]):
        return None
    return out


def resolve_chart(spec: dict, roles: dict, columns, kinds: dict):
    if spec.get("type") not in TAB_OF and spec.get("type") != "change_bar" or spec.get("agg", "sum") not in CHART_AGGS:
        return None
    out = dict(spec)
    out.setdefault("agg", "sum")
    if out["type"] == "change_bar":
        out["tab"] = "Overview"
        return out if {out["x"], out["y"], out["y2"]} <= set(columns) else None
    if out["type"] == "grouped_bar":
        x, ok = _resolve_field(spec.get("x", ""), roles, columns)
        ys = [_resolve_field(v.strip(), roles, columns)[0] for v in str(spec.get("y", "")).split("|") if v.strip()]
        ys = [v for v in ys if v and kinds.get(v) == "numeric"]
        if not ok or not x or len(ys) < 2:
            return None
        out.update(x=x, y="|".join(ys), y2="", color="", tab=TAB_OF["grouped_bar"])
        return out
    for f in ("x", "y", "y2", "color"):
        out[f], ok = _resolve_field(spec.get(f, ""), roles, columns)
        if not ok:
            # an optional colour split that's missing just drops out
            if f == "color":
                out[f] = ""
                continue
            return None
    _resolve_where(spec, out, roles, columns)
    if spec.get("where_required") and not out["where_column"]:
        return None
    t = out["type"]
    if not out["x"]:
        return None
    if not out["y"] and out["agg"] not in ("count",) and t not in ("histogram",):
        return None
    if out["agg"] == "ratio" and not out["y2"]:
        return None
    if t in ("waterfall", "metric_funnel") and not (out["y"] and out["y2"]):
        return None
    if t in ("stacked_bar", "heatmap", "treemap") and not out["color"]:
        return None
    if t in ("line", "weekday_heatmap") and kinds.get(out["x"]) not in ("datetime", "period"):
        return None
    if t == "weekday_heatmap" and kinds.get(out["x"]) != "datetime":
        return None
    if t in ("histogram", "scatter") and kinds.get(out["x"]) != "numeric":
        return None
    if t in ("bar", "donut", "pareto", "box", "funnel", "treemap", "heatmap", "stacked_bar") and \
            kinds.get(out["x"]) not in ("categorical", "identifier", "boolean", "period") and t != "stacked_bar":
        return None
    if out["y"] and out["agg"] in ("sum", "mean", "median", "ratio", "min", "max") and kinds.get(out["y"]) != "numeric":
        return None
    out["tab"] = TAB_OF[t]
    return out


# ---------- KPI math ----------

def kpi_value(df: pd.DataFrame, spec: dict):
    agg, col, col2 = spec["agg"], spec.get("column"), spec.get("column2")
    if df.empty:
        return None
    if agg == "cell":
        hit = df[df[spec["label_col"]].astype(str) == spec["filter_value"]]
        v = pd.to_numeric(hit[col], errors="coerce").dropna() if len(hit) else []
        return float(v.iloc[0]) if len(v) else None
    if agg == "share_of_gross":
        # value of the filtered rows (e.g. returns) as a share of all positive value
        gross = pd.to_numeric(df[col], errors="coerce").clip(lower=0).sum()
        part = pd.to_numeric(apply_where(df, spec)[col], errors="coerce").sum() if spec.get("where_column") else None
        return float(abs(part) / gross) if gross and part is not None else None
    df = apply_where(df, spec)
    if df.empty:
        return None
    if agg == "count_rows":
        return float(len(df))
    s = df[col]
    if agg == "repeat_rate":
        orders = df.groupby(col)[col2].nunique()
        return float((orders > 1).mean()) if len(orders) else None
    if agg == "sum_abs":
        return float(pd.to_numeric(s, errors="coerce").sum().__abs__())
    if agg in ("sum", "mean", "median", "min", "max"):
        s = pd.to_numeric(s, errors="coerce").dropna()
        return float(getattr(s, agg)()) if len(s) else None
    if agg == "nunique":
        return float(s.nunique())
    if agg == "ratio_sum":
        den = pd.to_numeric(df[col2], errors="coerce").sum()
        return float(pd.to_numeric(s, errors="coerce").sum() / den) if den else None
    if agg == "per_unique":
        den = df[col2].nunique()
        return float(pd.to_numeric(s, errors="coerce").sum() / den) if den else None
    if agg == "share_true":
        return float(is_truthy(s.dropna()).mean()) if s.notna().any() else None
    if agg == "share_value":
        pattern = spec.get("filter_value") or ""
        try:
            hits = s.dropna().astype(str).str.strip().str.fullmatch(pattern, case=False)
        except re.error:
            hits = s.dropna().astype(str).str.lower() == pattern.lower()
        return float(hits.mean()) if len(hits) else None
    return None


def kpi_format(spec: dict, profiles_by_name: dict) -> str:
    fmt = spec.get("fmt") or "auto"
    if fmt != "auto":
        return fmt
    if spec["agg"] in ("share_true", "share_value", "share_of_gross", "repeat_rate"):
        return "percent"
    p = profiles_by_name.get(spec.get("column"), {})
    if spec["agg"] in ("count_rows", "nunique"):
        return "number"
    if p.get("percent_fraction"):
        return "percent"
    if p.get("percent"):
        return "percent_points"
    if p.get("currency"):
        return "currency"
    return "decimal" if spec["agg"] in ("mean", "median", "ratio_sum", "per_unique") else "number"


def fmt_value(v, fmt: str) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    if fmt == "percent":
        return f"{v * 100:.1f}%"
    if fmt == "percent_points":
        return f"{v:.1f}%"
    if fmt == "multiple":
        return f"{v:.2f}x"
    av = abs(v)
    if av >= 1e9:
        text = f"{v / 1e9:.2f}B"
    elif av >= 1e6:
        text = f"{v / 1e6:.2f}M"
    elif av >= 1e4:
        text = f"{v / 1e3:.1f}K"
    elif fmt == "decimal" or (av < 100 and v % 1):
        text = f"{v:,.2f}"
    else:
        text = f"{v:,.0f}"
    return text


def kpi_delta(df: pd.DataFrame, spec: dict, date_col: str):
    """Change of the latest full calendar month vs the month before. None when not meaningful."""
    if spec["agg"] == "repeat_rate":
        return None  # "bought more than once" inside a single month is a different number, not a trend
    if spec["agg"] == "cell":
        cur = kpi_value(df, spec)
        prev = kpi_value(df, {**spec, "column": spec["column2"]})
        if cur is None or not prev:
            return None
        return {"pct": (cur - prev) / abs(prev), "label": f"vs {spec['column2']}"}
    if not date_col or date_col not in df.columns or not pd.api.types.is_datetime64_any_dtype(df[date_col]):
        return None
    months = df[date_col].dt.to_period("M")
    uniq = sorted(months.dropna().unique())
    # a month with data only up to the 24th is probably still running; leave it out
    if uniq and df[date_col].max().day < 25:
        uniq = uniq[:-1]
    if len(uniq) >= 2:
        cur = kpi_value(df[months == uniq[-1]], spec)
        prev = kpi_value(df[months == uniq[-2]], spec)
        label = f"{uniq[-1].strftime('%b %Y')} vs {uniq[-2].strftime('%b %Y')}"
    else:
        # short ranges: compare the last two complete weeks (Mon to Sun), if there are two
        d = df[date_col].dropna()
        if (d.max() - d.min()).days < 14:
            return None
        weeks = df[date_col].dt.to_period("W-SUN")
        uniq_w = sorted(weeks.dropna().unique())
        if d.min().dayofweek != 0:
            uniq_w = uniq_w[1:]
        if d.max().dayofweek < 5:
            uniq_w = uniq_w[:-1]
        if len(uniq_w) < 2:
            return None
        cur = kpi_value(df[weeks == uniq_w[-1]], spec)
        prev = kpi_value(df[weeks == uniq_w[-2]], spec)
        label = f"week of {uniq_w[-1].start_time:%d %b} vs week before"
    if cur is None or not prev:
        return None
    return {"pct": (cur - prev) / abs(prev), "label": label}


# ---------- plan assembly ----------

CHANGE_COL = re.compile(r"(change|diff|delta|variance|growth|chg|vs\.?|better|status|trend)", re.I)
PERIOD_COL = re.compile(r"^(q[1-4]|h[12]|fy\s*\d{2,4}|\d{4}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|actual|target|plan|budget|before|after|last|this|current|previous|prior)", re.I)


def metric_table(df, roles: dict, profiles: list):
    """Detects a KPI summary sheet (one row per metric, period columns). Returns
    {label, latest, prev, unit, better} or None."""
    if df is None or len(df) > 30:
        return None
    by_name = {p["name"]: p for p in profiles}
    numeric = [p["name"] for p in profiles if p["kind"] == "numeric" and not CHANGE_COL.search(p["name"])]
    label = roles.get("metric_name")
    if not label:
        periodish = [c for c in numeric if PERIOD_COL.match(str(c).strip())]
        if len(periodish) < 2:
            return None
        label = next((p["name"] for p in profiles if p["kind"] in ("categorical", "identifier") and p["nunique"] == len(df)), None)
    if not label or len(numeric) < 1 or by_name[label]["nunique"] != len(df):
        return None
    unit = next((p["name"] for p in profiles if re.fullmatch(r"units?|uom|measure", str(p["name"]).strip(), re.I)), None)
    better = next((p["name"] for p in profiles if re.search(r"better|direction|good when", str(p["name"]), re.I)), None)
    return {"label": label, "latest": numeric[-1], "prev": numeric[-2] if len(numeric) >= 2 else "", "unit": unit, "better": better}


def _metric_table_kpis(df, mt: dict) -> list:
    out = []
    for _, row in df.head(MAX_KPIS).iterrows():
        name = str(row[mt["label"]])
        unit = str(row[mt["unit"]]).strip() if mt["unit"] and pd.notna(row[mt["unit"]]) else ""
        val = pd.to_numeric(pd.Series([row[mt["latest"]]]), errors="coerce").iloc[0]
        fmt = "percent" if unit == "%" and pd.notna(val) and abs(val) <= 1.5 else "percent_points" if unit == "%" else "number"
        better = str(row[mt["better"]]).lower() if mt["better"] and pd.notna(row[mt["better"]]) else ""
        sub = unit
        if mt["prev"]:
            prev = pd.to_numeric(pd.Series([row[mt["prev"]]]), errors="coerce").iloc[0]
            prev_text = fmt_value(float(prev), fmt) if pd.notna(prev) else str(row[mt["prev"]])
            sub = f"{mt['prev']}: {prev_text}" if unit == "%" else f"{unit} | {mt['prev']}: {prev_text}"
        out.append({"title": name, "agg": "cell", "column": mt["latest"], "column2": mt["prev"], "filter_value": name,
                    "label_col": mt["label"], "fmt": fmt, "why": f"{mt['latest']} value", "sub": sub.strip(" |"),
                    "lower_better": "lower" in better, "group": name})
    return out


def build_plan(domain: str, roles: dict, profiles: list, ai_kpis=(), ai_charts=(), df=None) -> dict:
    columns = {p["name"] for p in profiles}
    kinds = {p["name"]: p["kind"] for p in profiles}
    inverse_roles = {v: k for k, v in roles.items()}

    mt = metric_table(df, roles, profiles)
    kpis, seen_groups, seen_titles = [], set(), set()
    if mt:
        kpis = _metric_table_kpis(df, mt)
    candidates = [] if mt else list(ai_kpis) + KPI_TEMPLATES.get(domain, []) + _auto_kpis(profiles, roles, domain)
    for spec in candidates:
        r = resolve_kpi(spec, roles, columns)
        if not r:
            continue
        key = (r["agg"], r.get("column") or "", r.get("column2") or "", r.get("filter_value") or "")
        group = spec.get("group") or spec["title"]
        if group in seen_groups:
            continue
        if r["title"].lower() in seen_titles or key in seen_titles:
            # the AI already covers this KPI, so its template alternatives (same group) are covered too
            seen_groups.add(group)
            continue
        if group == "records" and any(k["agg"] == "count_rows" or k["title"] in COUNT_TITLES for k in kpis):
            continue
        seen_groups.add(group)
        seen_titles.update({r["title"].lower(), key})
        r["lower_better"] = spec.get("lower_better", inverse_roles.get(r.get("column")) in LOWER_IS_BETTER)
        kpis.append(r)
        if len(kpis) >= MAX_KPIS:
            break

    charts, seen = [], set()
    if mt:
        # rows carry different units, so sums across them mean nothing: compare periods per metric instead
        chart_candidates = [
            {"type": "change_bar", "title": f"Change from {mt['prev']} to {mt['latest']}", "x": mt["label"], "y": mt["latest"],
             "y2": mt["prev"], "color": mt["better"] or "", "agg": "sum", "why": "Green bars moved the right way, orange the wrong way"},
        ] if mt["prev"] else []
        chart_candidates += [{"type": "grouped_bar", "title": f"{mt['prev']} vs {mt['latest']} by {mt['label']}", "x": mt["label"],
                              "y": f"{mt['prev']}|{mt['latest']}", "agg": "sum", "color": "", "y2": "", "why": "Side-by-side values per metric"}]
        if "status" in roles:
            chart_candidates.append({"type": "donut", "title": "Status of metrics", "x": roles["status"], "y": "", "agg": "count",
                                     "color": "", "y2": "", "why": ""})
    else:
        chart_candidates = list(ai_charts) + CHART_TEMPLATES.get(domain, [])
    nunique = {p["name"]: p["nunique"] for p in profiles}
    auto_added = bool(mt)
    i = 0
    while True:
        if i >= len(chart_candidates):
            # generic charts only top up a thin plan
            if auto_added or len(charts) >= 8:
                break
            chart_candidates += _auto_charts(profiles, roles, domain)
            auto_added = True
            continue
        spec = chart_candidates[i]
        i += 1
        r = resolve_chart(spec, roles, columns, kinds)
        if not r:
            continue
        # a split by a column with one value (e.g. a single country) shows nothing
        if r["type"] not in ("histogram", "scatter", "line", "weekday_heatmap", "waterfall", "metric_funnel", "change_bar") and \
                (nunique.get(r["x"], 2) < 2 or (r.get("color") and nunique.get(r["color"], 2) < 2)):
            continue
        # one histogram per column and one weekday heatmap per date, whatever measure they show
        key = (r["type"], r["x"]) if r["type"] in ("histogram", "weekday_heatmap") else (r["type"], r["x"], r["y"], r["agg"], r["color"])
        if key in seen or r["title"].lower() in seen:
            continue
        seen.update({key, r["title"].lower()})
        charts.append(r)
        if len(charts) >= MAX_CHARTS:
            break
    return {"kpis": kpis, "charts": charts, "metric_table": mt}


def measure_agg(measure: str, roles: dict, profiles_by_name: dict) -> str:
    inverse = {v: k for k, v in roles.items()}
    p = profiles_by_name.get(measure, {})
    if inverse.get(measure) in MEAN_ROLES or p.get("percent") or \
            re.search(r"(avg|average|mean|rate|score|rating|csat|nps|%)", str(measure), re.I):
        return "mean"
    return "sum"


def _usable_dims(profiles, roles, max_card=30):
    role_priority = ["region", "product", "category", "department", "channel", "agent", "salesperson", "job",
                     "status", "priority", "gender", "customer", "stage", "metric_name"]
    by_name = {p["name"]: p for p in profiles}
    dims = [roles[r] for r in role_priority if r in roles and by_name.get(roles[r], {}).get("kind") in ("categorical", "boolean", "identifier")
            and 2 <= by_name[roles[r]]["nunique"] <= max(max_card, 1000)]
    # names and ids make poor groupings: one row each
    skip = {roles[r] for r in ("employee", "order_id", "ticket_id", "employee_id") if r in roles}
    for p in profiles:
        if p["kind"] in ("categorical", "boolean") and 2 <= p["nunique"] <= max_card and p["name"] not in dims \
                and p["name"] not in skip:
            dims.append(p["name"])
    return dims


def _auto_kpis(profiles, roles, domain):
    out = [{"title": "Records", "agg": "count_rows", "column": "", "column2": "", "fmt": "number", "why": "Rows in the current view", "group": "records"}]
    assigned = set(roles.values())
    numeric = [p for p in profiles if p["kind"] == "numeric"]
    numeric.sort(key=lambda p: (p["name"] not in assigned, -abs(p.get("sum", 0))))
    by_name = {p["name"]: p for p in profiles}
    for p in numeric[:5]:
        agg = measure_agg(p["name"], roles, by_name)
        if agg == "sum" and re.search(r"(price|age|time|hrs|duration|tenure)", p["name"], re.I):
            agg = "mean"
        title = f"{'Avg' if agg == 'mean' else 'Total'} {p['name']}"
        out.append({"title": title, "agg": agg, "column": p["name"], "column2": "", "fmt": "auto", "why": "", "group": title})
    return out


def _auto_charts(profiles, roles, domain):
    by_name = {p["name"]: p for p in profiles}
    measure = primary_measure(domain, roles, profiles)
    numeric = [p["name"] for p in profiles if p["kind"] == "numeric"]
    dims = _usable_dims(profiles, roles)
    dates = [p["name"] for p in profiles if p["kind"] in ("datetime", "period")]
    m_agg = measure_agg(measure, roles, by_name) if measure else "count"
    out = []
    date = roles.get("date") or (dates[0] if dates else None)

    # small wide tables (one row per metric / month / item) read best as grouped bars
    label = next((p["name"] for p in profiles if p["kind"] in ("categorical", "identifier", "period")
                  and p["nunique"] == p.get("rows") and p["nunique"] <= 40), None)
    # only put measures on one axis when they share a scale (all percentages or none)
    same_scale = [n for n in numeric if bool(by_name[n].get("percent")) == bool(by_name[numeric[0]].get("percent"))] if numeric else []
    if label and len(same_scale) >= 2:
        out.append({"type": "grouped_bar", "title": f"{', '.join(same_scale[:3])} by {label}", "x": label,
                    "y": "|".join(same_scale[:4]), "agg": "sum", "color": "", "y2": "", "why": "Side-by-side comparison of each measure"})

    if date:
        if measure:
            out.append(C_("line", f"{'Avg ' if m_agg == 'mean' else ''}{measure} over time", date, measure, m_agg))
        out.append(C_("line", "Records over time", date, "", "count"))
        if by_name[date]["kind"] == "datetime":
            out.append(C_("weekday_heatmap", "Activity by weekday and month", date, measure or "", m_agg if measure else "count"))
    m_title = f"Avg {measure}" if m_agg == "mean" else measure
    for d in dims[:4]:
        if measure:
            out.append(C_("bar", f"{m_title} by {d}", d, measure, m_agg))
        out.append(C_("donut", f"Share of records by {d}", d, "", "count"))
    if dims and measure and m_agg == "sum":
        out.append(C_("pareto", f"{measure} concentration by {dims[0]}", dims[0], measure, "sum"))
    if len(dims) >= 2:
        out.append(C_("heatmap", f"{dims[0]} vs {dims[1]}", dims[0], measure or "", m_agg if measure else "count", dims[1]))
        out.append(C_("treemap", f"{dims[0]} and {dims[1]} breakdown", dims[0], measure or "", m_agg if measure else "count", dims[1]))
        if date:
            out.append(C_("stacked_bar", f"{dims[0]} mix over time", date, measure or "", m_agg if measure else "count", dims[0]))
    for n in numeric[:3]:
        out.append(C_("histogram", f"Distribution of {n}", n, "", "count"))
    if dims and numeric:
        out.append(C_("box", f"{numeric[0]} spread by {dims[0]}", dims[0], numeric[0], "sum"))
    if len(numeric) >= 2:
        out.append(C_("scatter", f"{numeric[0]} vs {numeric[1]}", numeric[0], numeric[1], "sum", dims[0] if dims else ""))
    return out


def C_(type_, title, x, y, agg, color=""):
    return {"type": type_, "title": title, "x": x, "y": y, "agg": agg, "color": color, "y2": "", "why": ""}


# ---------- chart data ----------

def time_bucket(s: pd.Series, order=None) -> tuple:
    """Returns (bucketed series, label). Datetimes pick D/W/M/Q from the span; periods keep order."""
    if order:
        return pd.Categorical(s, categories=order, ordered=True), "period"
    span = (s.max() - s.min()).days if s.notna().any() else 0
    freq, label = ("D", "day") if span <= 45 else ("W", "week") if span <= 200 else ("M", "month") if span <= 1100 else ("Q", "quarter")
    return s.dt.to_period(freq).dt.start_time, label


def period_label(value, unit: str) -> str:
    if not hasattr(value, "strftime"):
        return str(value)
    if unit == "day":
        return f"{value:%a %d %b %Y}"
    if unit == "week":
        return f"week of {value:%d %b %Y}"
    if unit == "quarter":
        return f"Q{(value.month - 1) // 3 + 1} {value:%Y}"
    return f"{value:%b %Y}"


def aggregate(df: pd.DataFrame, by, y: str, agg: str, y2: str = "") -> pd.DataFrame:
    """Group and aggregate into a tidy frame with a 'value' column."""
    by = [by] if isinstance(by, str) else list(by)
    g = df.groupby(by, observed=True, dropna=True)
    if agg == "count":
        out = g.size()
    elif agg == "nunique":
        out = g[y].nunique()
    elif agg == "rate":
        out = g[y].apply(lambda s: is_truthy(s.dropna()).mean() if s.notna().any() else np.nan)
    elif agg == "ratio":
        num = g[y].sum()
        den = g[y2].sum().replace(0, np.nan)
        out = num / den
    else:
        out = getattr(g[y], agg)()
    return out.rename("value").reset_index()


def measure_label(spec: dict) -> str:
    agg, y = spec["agg"], spec.get("y")
    if agg == "count":
        return "Records"
    if agg == "rate":
        return f"{y} rate"
    if agg == "ratio":
        return f"{y} / {spec.get('y2')}"
    return {"mean": f"Avg {y}", "median": f"Median {y}", "nunique": f"Unique {y}", "min": f"Min {y}", "max": f"Max {y}"}.get(agg, y)


# ---------- filters ----------

def filter_candidates(profiles: list, roles: dict, limit: int = 4) -> list:
    return _usable_dims(profiles, roles, max_card=40)[:limit]


# ---------- insights ----------

def insights(df: pd.DataFrame, plan: dict, roles: dict, profiles: list, domain: str) -> list:
    """Plain-English findings computed with pandas. No LLM call."""
    out = []
    by_name = {p["name"]: p for p in profiles}
    measure = primary_measure(domain, roles, profiles)
    if df.empty:
        return ["No rows match the current filters."]
    mt = plan.get("metric_table")
    if mt and mt["prev"]:
        return _metric_table_insights(df, mt)
    m_is_sum = bool(measure) and measure_agg(measure, roles, by_name) == "sum"

    line_type = roles.get("line_type")
    sold = df[df[line_type] == "Product"] if line_type in df.columns else df
    if line_type in df.columns and measure:
        returns = df[df[line_type] == "Return"]
        gross = pd.to_numeric(df[measure], errors="coerce").clip(lower=0).sum()
        if len(returns) and gross:
            lost = abs(returns[measure].sum())
            invoices = f" across {returns[roles['order_id']].nunique()} invoices" if "order_id" in roles else ""
            out.append(f"Returns and cancellations took back {fmt_value(lost, 'number')} "
                       f"({lost / gross:.1%} of gross sales){invoices}.")
        fees = df[df[line_type] == "Shipping & fees"]
        if len(fees) and gross:
            out.append(f"Shipping and fee lines add {fmt_value(fees[measure].sum(), 'number')} "
                       f"({fees[measure].sum() / gross:.1%} of gross sales), kept out of product rankings.")

    dims = _usable_dims(profiles, roles)
    if measure and dims and m_is_sum:
        d = dims[0]
        t = aggregate(sold, d, measure, "sum").sort_values("value", ascending=False)
        total = t["value"].sum()
        if len(t) >= 2 and total:
            top = t.iloc[0]
            out.append(f"{top[d]} leads {measure} with {top['value'] / total:.0%} of the total "
                       f"({fmt_value(top['value'], 'number')}), ahead of {t.iloc[1][d]} at {t.iloc[1]['value'] / total:.0%}.")
            cum = t["value"].cumsum() / total
            k = int((cum < 0.8).sum()) + 1
            if len(t) >= 5:
                noun = DIM_NOUNS.get({v: r for r, v in roles.items()}.get(d), f"{d} values")
                out.append(f"{k} of {len(t)} {noun} ({k / len(t):.0%}) make up 80% of {measure}.")

    date = roles.get("date")
    if date and date in df.columns and measure:
        s = df[[date, measure]].dropna()
        if len(s):
            order = by_name.get(date, {}).get("period_order")
            bucket, label = time_bucket(s[date], order)
            series = s.assign(_b=bucket).groupby("_b", observed=True)[measure].agg("sum" if m_is_sum else "mean")
            if label == "month" and s[date].max().day < 25:
                series = series.iloc[:-1]  # latest month still running
            if len(series) >= 2:
                last, prev = series.iloc[-1], series.iloc[-2]
                if prev and label != "day":  # day-to-day swings are mostly noise
                    change = (last - prev) / abs(prev)
                    word = "rose" if change > 0 else "fell"
                    out.append(f"{measure} {word} {abs(change):.1%} in the latest {label} compared with the one before.")
                peak = series.idxmax()
                peak_label = period_label(peak, label)
                out.append(f"Peak {label} for {measure}: {peak_label} ({fmt_value(series.max(), 'number')}).")

    for c in plan["charts"]:
        if c["agg"] in ("mean", "rate", "ratio") and c["type"] == "bar":
            t = aggregate(df, c["x"], c["y"], c["agg"], c.get("y2", "")).dropna().sort_values("value")
            if len(t) >= 3:
                fmt = "percent" if c["agg"] in ("rate", "ratio") else "number"
                out.append(f"{measure_label(c)} is highest for {t.iloc[-1][c['x']]} ({fmt_value(t.iloc[-1]['value'], fmt)}) "
                           f"and lowest for {t.iloc[0][c['x']]} ({fmt_value(t.iloc[0]['value'], fmt)}).")
            if len(out) >= 5:
                break

    if measure and not m_is_sum and dims:
        t = aggregate(df, dims[0], measure, "mean").dropna().sort_values("value")
        if len(t) >= 3:
            out.append(f"Avg {measure} is highest for {t.iloc[-1][dims[0]]} ({fmt_value(t.iloc[-1]['value'], 'number')}) "
                       f"and lowest for {t.iloc[0][dims[0]]} ({fmt_value(t.iloc[0]['value'], 'number')}).")

    gaps = [p["name"] for p in profiles if p["nulls_pct"] >= 20]
    if gaps:
        out.append(f"Data gaps: {', '.join(gaps[:4])} {'is' if len(gaps) == 1 else 'are'} at least 20% empty, so read related charts with care.")
    return list(dict.fromkeys(out))[:6]


def _metric_table_insights(df, mt) -> list:
    d = df[[mt["label"], mt["prev"], mt["latest"]] + ([mt["better"]] if mt["better"] else [])].copy()
    d["_prev"] = pd.to_numeric(d[mt["prev"]], errors="coerce")
    d["_cur"] = pd.to_numeric(d[mt["latest"]], errors="coerce")
    d = d.dropna(subset=["_prev", "_cur"])
    d["_pct"] = (d["_cur"] - d["_prev"]) / d["_prev"].abs().replace(0, np.nan)
    out = []
    for _, r in d.reindex(d["_pct"].abs().sort_values(ascending=False).index).head(4).iterrows():
        if pd.isna(r["_pct"]) or r["_pct"] == 0:
            continue
        direction = "up" if r["_pct"] > 0 else "down"
        verdict = ""
        if mt["better"] and pd.notna(r[mt["better"]]):
            good = (r["_pct"] < 0) == ("lower" in str(r[mt["better"]]).lower())
            verdict = ", an improvement" if good else ", a decline worth a look"
        out.append(f"{r[mt['label']]} is {direction} {abs(r['_pct']):.0%} ({fmt_value(r['_prev'], 'number')} in {mt['prev']} "
                   f"to {fmt_value(r['_cur'], 'number')} in {mt['latest']}){verdict}.")
    flat = d[d["_pct"] == 0][mt["label"]].astype(str).tolist()
    if flat:
        out.append(f"No movement on {', '.join(flat[:3])}.")
    return out
