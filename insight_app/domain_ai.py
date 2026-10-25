"""One Claude call per uploaded table: domain, column roles, KPI and chart plan as strict JSON.

The call sees only the schema (column names, types, a few sample values) and five sample rows,
never the full data. Results are cached on disk by file hash, so reloads, filter changes and
re-opening the same file cost nothing.
"""

import json
import os
import sys
from pathlib import Path

from insight_app import domain_rules
from insight_app.kpi_library import CHART_AGGS, CHART_TYPES, FORMATS, KPI_AGGS

MODEL = os.getenv("INSIGHT_MODEL", "claude-opus-5-5")
CACHE_DIR = Path(__file__).resolve().parent / "cache"
PLAN_VERSION = 2

SYSTEM = f"""You are a senior data analyst who builds KPI dashboards for business leaders.
You receive the schema of one table a user uploaded: column names, detected types, unique counts and sample values,
plus a few sample rows. Work out which business domain the data comes from, what each column means, and plan the
dashboard the head of that department would want to see first.

Pandas computes every number from your plan, so:
- Use exact column names from the schema, character for character. Never invent columns.
- Use "" for any field that does not apply.
- Prefer KPIs and charts a department head acts on (growth, share, rates, top/bottom performers), not trivia.

column_roles: map the columns you recognise to one of these roles: {", ".join(domain_rules.ROLE_NAMES)}.
Skip columns that fit none.

kpis (4 to 8). agg is one of:
  sum, mean, median, min, max: of `column`
  count_rows: number of rows (no column)
  nunique: distinct values of `column`
  ratio_sum: sum(`column`) / sum(`column2`), e.g. profit / revenue for margin
  per_unique: sum(`column`) / distinct(`column2`), e.g. revenue per order
  share_true: share of truthy values (yes/true/1/left) in `column`
  share_value: share of rows where `column` matches `filter_value` (a case-insensitive regex)
  share_of_gross: |sum(`column`) over the where-rows| / sum of positive `column`, e.g. return rate
  repeat_rate: share of `column` values (e.g. customers) with more than one distinct `column2` (e.g. orders)
  sum_abs: absolute value of sum(`column`)
fmt is one of {", ".join(FORMATS)}.
Row filter (KPIs and charts): where_column + where_value (case-insensitive regex) keep matching rows;
where_not = true keeps the rows that do not match. Leave where_column "" for no filter.

Columns ending "(calc)" or "(Qty x Price)" and a "Line Type" column (Product / Return / Shipping & fees) were
derived by pandas; use them. When Line Type exists, rank products on Line Type = product, count orders,
customers and units with where_not on return, and report a return rate.

charts (6 to 12). type is one of {", ".join(CHART_TYPES)}:
  line: trend over time, x = date/period column
  bar: top values of category x by y
  donut: share of category x
  stacked_bar: x (date or category) split by `color`
  treemap / heatmap: x and `color` are two categories
  pareto: 80/20 concentration of y across category x
  histogram: distribution of numeric x
  box: spread of numeric y across category x
  scatter: numeric x vs numeric y, optional `color` category
  waterfall: x = revenue column, y = cost column, y2 = profit column
  funnel: stages in category x
  weekday_heatmap: x = datetime column, activity by weekday and hour (short ranges) or month
  grouped_bar: x = label column, y = 2 to 4 numeric column names joined with "|"
  metric_funnel: x, y, y2 = three numeric stage columns (e.g. impressions, clicks, conversions)
agg is one of {", ".join(CHART_AGGS)}. Use "count" with y = "" to count rows; "rate" = share of truthy y;
"ratio" = sum(y) / sum(y2). Average (mean) salaries, scores, ratings, times and percentages; sum amounts and counts.
A histogram with `color` set first sums x per `color` value (e.g. x = line revenue, color = invoice id gives order values).

why: one short plain sentence a manager understands. summary: one or two sentences describing the dataset.
questions: 5 specific questions a manager would ask about this data, answerable from its columns.
Write plainly. No em dashes, no hype words."""


def _schema():
    s = lambda **kw: {"type": "string", **kw}  # noqa: E731
    where = {"where_column": s(), "where_value": s(), "where_not": {"type": "boolean"}}
    kpi = {"type": "object", "additionalProperties": False,
           "properties": {"title": s(), "agg": s(enum=KPI_AGGS), "column": s(), "column2": s(),
                          "filter_value": s(), "fmt": s(enum=FORMATS), "why": s(), **where},
           "required": ["title", "agg", "column", "column2", "filter_value", "fmt", "why", *where]}
    chart = {"type": "object", "additionalProperties": False,
             "properties": {"type": s(enum=CHART_TYPES), "title": s(), "x": s(), "y": s(), "y2": s(), "color": s(),
                            "agg": s(enum=CHART_AGGS), "why": s(), **where},
             "required": ["type", "title", "x", "y", "y2", "color", "agg", "why", *where]}
    role = {"type": "object", "additionalProperties": False,
            "properties": {"column": s(), "role": s(enum=domain_rules.ROLE_NAMES)}, "required": ["column", "role"]}
    return {"type": "object", "additionalProperties": False,
            "properties": {"domain": s(enum=domain_rules.DOMAINS), "confidence": {"type": "number"}, "summary": s(),
                           "column_roles": {"type": "array", "items": role},
                           "kpis": {"type": "array", "items": kpi}, "charts": {"type": "array", "items": chart},
                           "questions": {"type": "array", "items": s()}},
            "required": ["domain", "confidence", "summary", "column_roles", "kpis", "charts", "questions"]}


IN_BROWSER = sys.platform == "emscripten"  # running as insightiq.html (stlite / Pyodide)
API_URL = "https://api.anthropic.com/v1/messages"
# Returns the key the viewer pasted in the sidebar. The app sets it to a reader of st.session_state,
# so each viewer's key stays in their own session and is never shared between sessions.
key_getter = lambda: ""  # noqa: E731


def _pasted_key() -> str:
    return (key_getter() or "").strip()


def ai_available() -> bool:
    return bool(_pasted_key() or os.getenv("ANTHROPIC_API_KEY"))


def no_key_message() -> str:
    if IN_BROWSER:
        return "Paste an Anthropic API key in the sidebar to turn on AI. Until then the dashboard uses built-in rules."
    return "No ANTHROPIC_API_KEY in .env (or pasted in the sidebar), so the dashboard uses built-in rules."


def call_claude(system: str, user: str, schema: dict, max_tokens: int = 12000) -> dict:
    """Single structured-output request. Raises on API errors; callers fall back to rules."""
    if IN_BROWSER:
        return _call_from_browser(system, user, schema, max_tokens)
    import anthropic

    client = anthropic.Anthropic(api_key=_pasted_key()) if _pasted_key() else anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off.")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def _call_from_browser(system: str, user: str, schema: dict, max_tokens: int) -> dict:
    """Same request as above over plain HTTP: the SDK's HTTP client can't run in Pyodide, while
    `requests` can (stlite patches it onto the browser's XMLHttpRequest). The key goes from the
    viewer's browser straight to Anthropic and nowhere else."""
    import requests

    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": _pasted_key(),
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "server-side-fallback-2026-07-01",
            "anthropic-dangerous-direct-browser-access": "true",
            "content-type": "application/json",
        },
        data=json.dumps({
            "model": MODEL,
            "max_tokens": max_tokens,
            "fallbacks": "default",
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }),
        timeout=600,
    )
    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except ValueError:
            detail = resp.text[:160]
        raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
    body = resp.json()
    if body.get("stop_reason") == "refusal":
        raise RuntimeError("Claude declined this request.")
    if body.get("stop_reason") == "max_tokens":
        raise RuntimeError("Claude's answer was cut off.")
    text = next(b["text"] for b in body["content"] if b.get("type") == "text")
    return json.loads(text)


def analyze(schema_payload: dict, filename: str, file_key: str, domain_hint: str = None) -> dict:
    """Returns the AI plan dict, or raises. Cached per (file, table, hint)."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_path = CACHE_DIR / f"{file_key}_{(domain_hint or 'auto').replace('/', '-').replace(' ', '_')}_v{PLAN_VERSION}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    hint = f"\nThe user says this is {domain_hint} data. Plan the dashboard for that department." if domain_hint else ""
    user = f"File name: {filename}{hint}\n\nSchema and sample rows:\n{json.dumps(schema_payload, default=str, indent=1)}"
    plan = call_claude(SYSTEM, user, _schema())
    cache_path.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    return plan


def _unit(v: float) -> float:
    return max(0.0, min(1.0, v / 100 if v > 1 else v))


def understand(df, profiles: list, schema_payload: dict, filename: str, file_key: str, domain_hint: str = None) -> dict:
    """Rules first (instant, free), then Claude on top when a key is set. Never raises."""
    rule_domain, rule_conf = domain_rules.detect_domain(profiles)
    domain = domain_hint or rule_domain
    result = {
        "domain": domain, "confidence": rule_conf if not domain_hint else 1.0,
        "summary": domain_rules.summarize(domain, profiles, len(df)),
        "roles": domain_rules.assign_roles(profiles, domain),
        "kpis": [], "charts": [], "questions": [], "source": "rules", "error": None,
    }
    if not ai_available():
        result["error"] = no_key_message()
        return result
    try:
        plan = analyze(schema_payload, filename, file_key, domain_hint)
    except Exception as e:  # noqa: BLE001  any API or parsing problem falls back to rules
        result["error"] = f"AI step failed ({type(e).__name__}: {str(e)[:160]}). Using built-in rules."
        return result

    columns = {p["name"] for p in profiles}
    ai_roles = {r["role"]: r["column"] for r in plan.get("column_roles", []) if r.get("column") in columns}
    # AI roles win; rule roles fill gaps where the column isn't already taken
    roles = dict(ai_roles)
    for role, col in result["roles"].items():
        if role not in roles and col not in roles.values():
            roles[role] = col
    result.update(
        domain=domain_hint or plan.get("domain") or rule_domain,
        confidence=1.0 if domain_hint else _unit(float(plan.get("confidence") or rule_conf)),
        summary=plan.get("summary") or result["summary"],
        roles=roles, kpis=plan.get("kpis", []), charts=plan.get("charts", []),
        questions=[q for q in plan.get("questions", []) if q][:6], source="ai",
    )
    return result
