"""Single-file HTML report: KPIs, insights, every chart (interactive, works offline), Q&A, data profile."""

import html
from datetime import datetime

import pandas as pd

from insight_app import theme


def _kpi_html(k: dict) -> str:
    delta = ""
    if k.get("delta"):
        pct = k["delta"]["pct"]
        good = (pct < 0) if k.get("lower_better") else (pct > 0)
        cls = "up" if good else "down" if pct else ""
        arrow = "&#9650;" if pct > 0 else "&#9660;" if pct < 0 else "&#9644;"
        delta = f'<div class="sub"><span class="{cls}">{arrow} {abs(pct):.1%}</span> {html.escape(k["delta"]["label"])}</div>'
    sub = f'<div class="sub">{html.escape(k["sub"])}</div>' if k.get("sub") else ""
    return (f'<div class="kpi"><div class="lbl">{html.escape(k["title"])}</div>'
            f'<div class="val">{html.escape(k["display"])}</div>{delta}{sub}</div>')


def build_html(meta: dict, kpis: list, insights: list, charts: list, qa_history: list, profile_df: pd.DataFrame) -> str:
    """charts: list of (tab, spec, fig). qa_history: list of answer dicts from qa.answer."""
    parts, first = [], True
    for tab in ("Overview", "Breakdown", "Distribution"):
        figs = [(s, f) for t, s, f in charts if t == tab]
        if not figs:
            continue
        parts.append(f"<h2>{tab}</h2><div class='grid'>")
        for spec, fig in figs:
            div = fig.to_html(full_html=False, include_plotlyjs="inline" if first else False,
                              config={"displaylogo": False, "responsive": True})
            first = False
            why = f"<p class='why'>{html.escape(spec['why'])}</p>" if spec.get("why") else ""
            parts.append(f"<div class='card'>{div}{why}</div>")
        parts.append("</div>")

    qa = ""
    if qa_history:
        items = []
        for a in qa_history:
            table = a["table"].head(15).to_html(index=False, classes="tbl", border=0) if a.get("table") is not None else ""
            fig = ""
            if a.get("fig") is not None:
                fig = a["fig"].to_html(full_html=False, include_plotlyjs="inline" if first else False, config={"displaylogo": False})
                first = False
            items.append(f"<div class='card'><p class='q'>Q: {html.escape(a['question'])}</p>"
                         f"<p>{html.escape(a['text'])}</p>{fig}{table}</div>")
        qa = "<h2>Questions and answers</h2>" + "".join(items)

    insight_html = "".join(f"<li>{html.escape(i)}</li>" for i in insights)
    filters = html.escape(meta.get("filters") or "None")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(meta['domain'])} Dashboard Report</title>
<style>
body {{ font-family: Poppins, 'Segoe UI', sans-serif; background: {theme.PAGE_BG}; color: {theme.TEXT}; margin: 0; }}
.wrap {{ max-width: 1240px; margin: 0 auto; padding: 24px 16px 48px; }}
.hero {{ background: {theme.KPI_GRADIENT}; color: #fff; border-radius: 16px; padding: 22px 26px; }}
.hero h1 {{ margin: 0 0 6px; font-size: 26px; }} .hero p {{ margin: 2px 0; opacity: .9; }}
.chip {{ display: inline-block; background: {theme.YELLOW}; color: {theme.NAVY}; font-weight: 600; border-radius: 999px; padding: 2px 12px; font-size: 12px; }}
h2 {{ color: {theme.NAVY}; margin: 28px 0 12px; font-size: 19px; border-left: 5px solid {theme.PURPLE}; padding-left: 10px; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-top: 16px; }}
.kpi {{ background: #fff; border-radius: 14px; padding: 14px 16px; border-top: 5px solid {theme.PURPLE}; box-shadow: 0 1px 3px rgba(23,26,95,.08); }}
.kpi .lbl {{ color: {theme.MUTED}; font-size: 12.5px; }} .kpi .val {{ color: {theme.NAVY}; font-size: 24px; font-weight: 700; }}
.kpi .sub {{ color: {theme.MUTED}; font-size: 11.5px; }} .up {{ color: #3E8A0A; font-weight: 600; }} .down {{ color: #C2620C; font-weight: 600; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(480px, 1fr)); gap: 14px; }}
@media (max-width: 560px) {{ .grid {{ grid-template-columns: 1fr; }} }}
.card {{ background: #fff; border-radius: 14px; padding: 12px 14px; box-shadow: 0 1px 3px rgba(23,26,95,.08); margin-bottom: 12px; overflow-x: auto; }}
.why {{ color: {theme.MUTED}; font-size: 12px; margin: 4px 6px; }} .q {{ font-weight: 600; color: {theme.PURPLE}; }}
.tbl {{ border-collapse: collapse; font-size: 12.5px; width: 100%; }} .tbl th {{ background: {theme.NAVY}; color: #fff; text-align: left; padding: 6px 8px; }}
.tbl td {{ padding: 5px 8px; border-bottom: 1px solid #ECEBF3; }}
ul.ins li {{ margin-bottom: 6px; }} footer {{ color: {theme.MUTED}; font-size: 12px; margin-top: 30px; }}
</style></head><body><div class="wrap">
<div class="hero"><span class="chip">{html.escape(meta['domain'])}</span>
<h1>{html.escape(meta['domain'])} Dashboard</h1>
<p>{html.escape(meta.get('summary', ''))}</p>
<p>Source: {html.escape(meta.get('filename', ''))} | {meta.get('rows', 0):,} rows in view | Filters: {filters}</p></div>
<div class="kpis">{''.join(_kpi_html(k) for k in kpis)}</div>
<h2>Key insights</h2><div class="card"><ul class="ins">{insight_html or '<li>No insights for this view.</li>'}</ul></div>
{''.join(parts)}
{qa}
<h2>Data profile</h2><div class="card">{profile_df.to_html(index=False, classes='tbl', border=0)}</div>
<footer>Generated by InsightIQ on {datetime.now():%d %b %Y, %H:%M}. Numbers computed with pandas from the uploaded file.</footer>
</div></body></html>"""
