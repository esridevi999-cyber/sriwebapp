"""InsightIQ entrypoint: upload any table, get a domain-aware KPI dashboard.

Run:  py -m streamlit run insight_app/app.py
Or open insightiq.html (built by insight_app/build_html.py), which runs this same app in the browser.
"""

import hashlib
import html
import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # the browser build has no .env; viewers paste a key instead
    pass

from insight_app import charts, demo_data, domain_ai, domain_rules, engine, loaders, profiler, qa, report, theme  # noqa: E402

st.set_page_config(page_title="InsightIQ", page_icon="📊", layout="wide")
domain_ai.key_getter = lambda: st.session_state.get("iq_api_key", "")
theme.register_plotly_template()
theme.inject_css()

SAMPLE_GLOBS = ["data/*.xlsx", "data/*.csv", "*Sample*.xlsx", "*Sample*.docx", "*Sample*.pdf", "*[Ss]ales*.xlsx", "*[Ss]ales*.csv"]
# pdfplumber needs compiled libraries that don't run in the browser build
UPLOAD_TYPES = ["xlsx", "xls", "csv", "docx"] + ([] if domain_ai.IN_BROWSER else ["pdf"])


# ---------- cached steps ----------

@st.cache_data(show_spinner="Reading the file...")
def load_tables(name: str, data: bytes) -> dict:
    return loaders.load_file(name, data)


@st.cache_data(show_spinner=False)
def prepare(raw: pd.DataFrame) -> tuple:
    """Types the columns and adds the derived ones (revenue = qty x price, Line Type...) up front,
    so the AI plan can use them too."""
    df, meta = profiler.clean_types(raw)
    profiles = profiler.profile(df, meta)
    rule_roles = domain_rules.assign_roles(profiles, domain_rules.detect_domain(profiles)[0])
    df, _, profiles = engine.add_derived(df, rule_roles, profiles)
    return df, profiles


@st.cache_data(show_spinner="Understanding your columns...")
def understand(file_key: str, filename: str, hint, schema_json: str, ai_on: bool, _df, _profiles) -> dict:
    # ai_on is part of the cache key, so pasting a key later re-plans instead of reusing the rules result
    return domain_ai.understand(_df, _profiles, json.loads(schema_json), filename, file_key, hint)


def sample_files() -> list:
    found = []
    for pattern in SAMPLE_GLOBS:
        for p in sorted(ROOT.glob(pattern)):
            if p.name.startswith("~$") or p in found or p.name in ("connections.xlsx", "jobs_output.xlsx"):
                continue
            found.append(p)
    return found


# ---------- sidebar: data ----------

with st.sidebar:
    st.markdown("## 📊 InsightIQ")
    kinds_text = "Excel, CSV or Word" if domain_ai.IN_BROWSER else "Excel, CSV, Word or PDF"
    st.caption(f"Upload {kinds_text}. Tables are read, columns understood, KPIs built.")
    uploaded = st.file_uploader("Upload data", type=UPLOAD_TYPES, help="Word and PDF files need the data inside a table.")
    samples = [] if domain_ai.IN_BROWSER else sample_files()
    sample_choice = None
    if uploaded is None and samples:
        sample_choice = st.selectbox("Or open a file from this folder", ["None"] + [str(p.relative_to(ROOT)) for p in samples])
    if uploaded is None and not (sample_choice and sample_choice != "None"):
        if st.button("Try demo sales data", width="stretch", type="secondary"):
            st.session_state["iq_demo"] = True

    if not os.getenv("ANTHROPIC_API_KEY"):
        with st.expander("AI (optional)", expanded=False):
            key = st.text_input("Anthropic API key", type="password", key="iq_api_key", placeholder="sk-ant-...",
                                help="Used only in this browser session and sent only to Anthropic. Never saved in the file.")
            st.caption("AI on." if key else "Without a key the dashboard uses built-in rules.")

if uploaded is not None:
    file_name, file_bytes = uploaded.name, uploaded.getvalue()
elif sample_choice and sample_choice != "None":
    file_name, file_bytes = Path(sample_choice).name, (ROOT / sample_choice).read_bytes()
elif st.session_state.get("iq_demo"):
    file_name, file_bytes = demo_data.DEMO_NAME, demo_data.demo_sales_bytes()
else:
    st.markdown(
        """<div class="iq-hero"><h1>InsightIQ</h1>
        <p>Upload a sales, HR, finance, support, marketing or any other table. InsightIQ reads the columns,
        works out the department it belongs to and builds the KPIs and charts that department tracks.</p></div>""",
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    tables_from = "Word files" if domain_ai.IN_BROWSER else "Word and PDF files"
    c1.markdown(f"<div class='iq-card'><h4>1. Upload</h4>Excel (every sheet), CSV, or tables inside {tables_from}. "
                "Or press <b>Try demo sales data</b>.</div>", unsafe_allow_html=True)
    c2.markdown("<div class='iq-card'><h4>2. Confirm the domain</h4>Detected automatically. Change it in the sidebar if needed.</div>", unsafe_allow_html=True)
    c3.markdown("<div class='iq-card'><h4>3. Explore and ask</h4>Filter, ask questions in plain English, download the report.</div>", unsafe_allow_html=True)
    st.stop()

try:
    tables = load_tables(file_name, file_bytes)
except ValueError as e:
    st.error(f"Couldn't read {file_name}: {e}")
    st.stop()

with st.sidebar:
    labels = list(tables.keys())
    table_label = labels[0]
    if len(labels) > 1:
        table_label = st.selectbox(f"Table ({len(labels)} found)", labels,
                                   format_func=lambda k: f"{k}  [{tables[k].shape[0]} x {tables[k].shape[1]}]")

file_key = hashlib.sha256(file_bytes + table_label.encode()).hexdigest()[:16]
df_full, profiles = prepare(tables[table_label])
schema_json = json.dumps(profiler.schema_for_ai(df_full, profiles), default=str)
auto = understand(file_key, file_name, None, schema_json, domain_ai.ai_available(), df_full, profiles)

# ---------- sidebar: domain ----------

with st.sidebar:
    st.markdown("### Domain")
    source = "AI" if auto["source"] == "ai" else "rules"
    st.caption(f"Detected: **{auto['domain']}** ({auto['confidence']:.0%}, {source})")
    domains = domain_rules.DOMAINS
    chosen = st.selectbox("Analyse as", domains, index=domains.index(auto["domain"]) if auto["domain"] in domains else len(domains) - 1,
                          key=f"domain_{file_key}")
    replan_key = f"replan_{file_key}_{chosen}"
    if chosen != auto["domain"] and domain_ai.ai_available():
        if st.button(f"Re-plan with AI as {chosen}", width="stretch"):
            st.session_state[replan_key] = True
    if auto.get("error"):
        st.caption(f"⚠️ {auto['error']}")

if chosen == auto["domain"]:
    understood = auto
elif st.session_state.get(replan_key):
    understood = understand(file_key, file_name, chosen, schema_json, domain_ai.ai_available(), df_full, profiles)
else:
    # override without a new AI call: keep what columns mean, swap in this domain's templates
    roles = dict(auto["roles"])
    for role, col in domain_rules.assign_roles(profiles, chosen).items():
        if role not in roles and col not in roles.values():
            roles[role] = col
    understood = {**auto, "domain": chosen, "kpis": [], "charts": [], "questions": [], "roles": roles,
                  "summary": domain_rules.summarize(chosen, profiles, len(df_full))}

domain = understood["domain"]
df_all, roles, profiles_all = engine.add_derived(df_full, understood["roles"], profiles)
pb = {p["name"]: p for p in profiles_all}
plan = engine.build_plan(domain, roles, profiles_all, understood["kpis"], understood["charts"], df=df_all)

# ---------- sidebar: filters ----------

with st.sidebar:
    st.markdown("### Filters")
    fdf = df_all
    active = []
    date_col = roles.get("date")
    if date_col and pd.api.types.is_datetime64_any_dtype(df_all[date_col]) and df_all[date_col].notna().any():
        lo, hi = df_all[date_col].min().date(), df_all[date_col].max().date()
        if lo < hi:
            picked = st.date_input(date_col, value=(lo, hi), min_value=lo, max_value=hi, key=f"date_{file_key}")
            if isinstance(picked, tuple) and len(picked) == 2 and (picked[0], picked[1]) != (lo, hi):
                fdf = fdf[(fdf[date_col].dt.date >= picked[0]) & (fdf[date_col].dt.date <= picked[1])]
                active.append(f"{date_col} {picked[0]:%d %b %Y} to {picked[1]:%d %b %Y}")
    for col in engine.filter_candidates(profiles_all, roles):
        options = sorted(df_all[col].dropna().astype(str).unique().tolist())
        sel = st.multiselect(col, options, key=f"f_{file_key}_{col}")
        if sel:
            fdf = fdf[fdf[col].astype(str).isin(sel)]
            active.append(f"{col}: {', '.join(sel[:3])}{'...' if len(sel) > 3 else ''}")
    filters_text = "; ".join(active)
    st.caption(f"{len(fdf):,} of {len(df_all):,} rows in view")

# ---------- header ----------

chips = f"<span class='iq-chip'>{html.escape(domain)}</span><span class='iq-chip ghost'>{html.escape(file_name)}</span>"
chips += f"<span class='iq-chip ghost'>{html.escape(table_label)}</span>" if len(tables) > 1 else ""
chips += "<span class='iq-chip ghost'>AI plan</span>" if understood["source"] == "ai" else "<span class='iq-chip ghost'>Rule-based plan</span>"
st.markdown(
    f"<div class='iq-hero'>{chips}<h1>{html.escape(domain)} Dashboard</h1><p>{html.escape(understood['summary'])}</p></div>",
    unsafe_allow_html=True,
)

# ---------- KPIs ----------

kpi_cards = []
for spec in plan["kpis"]:
    value = engine.kpi_value(fdf, spec)
    kpi_cards.append({**spec, "value": value, "display": engine.fmt_value(value, engine.kpi_format(spec, pb)),
                      "delta": engine.kpi_delta(fdf, spec, roles.get("date"))})


def kpi_html(k: dict) -> str:
    delta = ""
    if k["delta"]:
        pct = k["delta"]["pct"]
        good = (pct < 0) if k.get("lower_better") else (pct > 0)
        cls = "up" if good else "down" if pct else ""
        arrow = "▲" if pct > 0 else "▼" if pct < 0 else "▬"
        delta = f"<div class='sub'><span class='{cls}'>{arrow} {abs(pct):.1%}</span> {html.escape(k['delta']['label'])}</div>"
    sub = f"<div class='sub'>{html.escape(k['sub'])}</div>" if k.get("sub") else ""
    tip = html.escape(k.get("why") or "")
    return (f"<div class='iq-kpi' title='{tip}'><div class='lbl'>{html.escape(k['title'])}</div>"
            f"<div class='val'>{html.escape(k['display'])}</div>{delta}{sub}</div>")


per_row = 4
for i in range(0, len(kpi_cards), per_row):
    cols = st.columns(per_row)
    for col, card in zip(cols, kpi_cards[i:i + per_row]):
        col.markdown(kpi_html(card), unsafe_allow_html=True)

# ---------- charts (built once per run, reused by tabs, Q&A context and report) ----------

built = []
for spec in plan["charts"]:
    fig, table = charts.build(fdf, spec, pb)
    if fig is not None:
        built.append((spec["tab"], spec, fig, table))

insight_lines = engine.insights(fdf, plan, roles, profiles_all, domain)

# ---------- insights + ask ----------

history_key = f"qa_{file_key}"
st.session_state.setdefault(history_key, [])
suggestions = understood["questions"] or qa.suggest_questions(roles, profiles_all, domain, plan.get("metric_table"), fdf)


def _queue(q: str):
    st.session_state["iq_pending"] = q


left, right = st.columns([1, 1], gap="medium")
with left:
    items = "".join(f"<li>{html.escape(line)}</li>" for line in insight_lines) or "<li>No insights for this view.</li>"
    st.markdown(f"<div class='iq-card'><h4>Key insights</h4><ul>{items}</ul></div>", unsafe_allow_html=True)
with right:
    st.markdown("<div class='iq-card'><h4>Ask a question</h4>Ask about any chart, number or column in this data.</div>", unsafe_allow_html=True)
    with st.form(f"ask_{file_key}", clear_on_submit=True, border=False):
        c1, c2 = st.columns([5, 1])
        typed = c1.text_input("Question", placeholder=suggestions[0] if suggestions else "Which region has the highest revenue?",
                              label_visibility="collapsed")
        asked = c2.form_submit_button("Ask", width="stretch")
    for i, q in enumerate(suggestions[:5]):
        st.button(q, key=f"sugg_{file_key}_{i}", on_click=_queue, args=(q,), type="secondary")

question = st.session_state.pop("iq_pending", None) or (typed.strip() if asked and typed.strip() else None)
if question:
    ctx = {
        "domain": domain, "filename": file_name, "roles": roles, "profiles": profiles_all,
        "schema": profiler.schema_for_ai(fdf, profiles_all, max_rows=0)["columns"], "rows": len(fdf),
        "filters_text": filters_text, "insights": insight_lines,
        "kpi_values": [{"kpi": k["title"], "value": k["display"]} for k in kpi_cards],
        "chart_tables": [(s["title"], t) for _, s, _, t in built],
    }
    with st.spinner("Working it out..."):
        st.session_state[history_key].insert(0, qa.answer(question, fdf, ctx))

history = st.session_state[history_key]
if history:
    latest = history[0]
    tag = {"pandas": "Answered with pandas", "ai": "Answered by AI from dashboard data", "ai+pandas": "AI plan, computed with pandas"}[latest["source"]]
    st.markdown(f"<div class='iq-answer'><b>Q: {html.escape(latest['question'])}</b><br>{html.escape(latest['text'])}"
                f"<div class='iq-why' style='margin-top:4px'>{tag}</div></div>", unsafe_allow_html=True)
    if latest.get("note"):
        st.caption(latest["note"])
    a1, a2 = st.columns([3, 2])
    if latest.get("fig") is not None:
        latest["fig"].update_layout(height=320, title_text="", margin={"t": 24})
        a1.plotly_chart(latest["fig"], width="stretch", key=f"qa_fig_{len(history)}")
    if latest.get("table") is not None:
        (a2 if latest.get("fig") is not None else a1).dataframe(latest["table"], width="stretch", hide_index=True, height=300)
    if len(history) > 1:
        with st.expander(f"Earlier questions ({len(history) - 1})"):
            for a in history[1:]:
                st.markdown(f"**Q: {a['question']}**  \n{a['text']}")

# ---------- tabs ----------

tab_names = ["Overview", "Breakdown", "Distribution", "Data"]
tabs = st.tabs(tab_names)
for name, tab in zip(tab_names[:3], tabs[:3]):
    with tab:
        figs = [(s, f) for t, s, f, _ in built if t == name]
        if not figs:
            st.info("No charts of this kind fit the columns in this table.")
        for i in range(0, len(figs), 2):
            cols = st.columns(2, gap="medium")
            for col, (spec, fig) in zip(cols, figs[i:i + 2]):
                col.plotly_chart(fig, width="stretch", key=f"{name}_{i}_{spec['title']}")
                if spec.get("why"):
                    col.markdown(f"<div class='iq-why'>{html.escape(spec['why'])}</div>", unsafe_allow_html=True)

inverse_roles = {v: k for k, v in roles.items()}
profile_df = pd.DataFrame([{
    "Column": p["name"], "Type": p["kind"], "Role": inverse_roles.get(p["name"], ""), "Missing %": p["nulls_pct"],
    "Unique": p["nunique"], "Examples": ", ".join(p["samples"][:3]),
} for p in profiles_all])

with tabs[3]:
    st.markdown("#### How each column was read")
    st.dataframe(profile_df, width="stretch", hide_index=True)
    st.markdown(f"#### Data in view ({len(fdf):,} rows)")
    st.dataframe(fdf.head(2000), width="stretch", hide_index=True)
    st.download_button("Download filtered data (CSV)", fdf.to_csv(index=False).encode("utf-8"),
                       file_name=f"{Path(file_name).stem}_filtered.csv", mime="text/csv")

# ---------- report ----------

with st.sidebar:
    st.markdown("### Report")
    if st.button("Build HTML report", width="stretch", type="primary"):
        meta = {"domain": domain, "summary": understood["summary"], "filename": file_name, "rows": len(fdf), "filters": filters_text}
        st.session_state["iq_report"] = (file_key, report.build_html(
            meta, kpi_cards, insight_lines, [(t, s, f) for t, s, f, _ in built], history, profile_df))
    rep = st.session_state.get("iq_report")
    if rep and rep[0] == file_key:
        st.download_button("Download report", rep[1].encode("utf-8"), width="stretch",
                           file_name=f"{Path(file_name).stem}_{domain.split()[0].lower()}_report.html", mime="text/html")
        st.caption("Interactive charts, works offline. Rebuild after changing filters.")
