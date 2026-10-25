"""Plotly builders. Each takes a resolved chart spec and returns (figure, tidy table) or (None, None)."""

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from insight_app import theme
from insight_app.engine import aggregate, apply_where, measure_label, time_bucket

TOP_N = 12
DONUT_N = 6
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def build(df: pd.DataFrame, spec: dict, profiles_by_name: dict):
    df = apply_where(df, spec)
    if df.empty:
        return None, None
    builder = BUILDERS.get(spec["type"])
    try:
        fig, table = builder(df, spec, profiles_by_name)
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        return None, None
    if fig is None:
        return None, None
    fig.update_layout(title=spec["title"], height=380)
    return fig, table


def _pct_axis(spec, profiles_by_name, fig, axis="y"):
    p = profiles_by_name.get(spec.get("y"), {})
    if spec["agg"] in ("rate", "ratio") or p.get("percent_fraction"):
        fig.update_layout(**{f"{axis}axis_tickformat": ".0%"})


def _base(df, spec, profiles_by_name):
    order = profiles_by_name.get(spec["x"], {}).get("period_order")
    d = df
    if profiles_by_name.get(spec["x"], {}).get("kind") == "datetime":
        bucket, _ = time_bucket(d[spec["x"]])
        d = d.assign(**{spec["x"]: bucket})
    elif order:
        d = d.assign(**{spec["x"]: pd.Categorical(d[spec["x"]], categories=order, ordered=True)})
    return d


def line(df, spec, pb):
    d = _base(df, spec, pb)
    t = aggregate(d, spec["x"], spec["y"], spec["agg"], spec.get("y2", "")).sort_values(spec["x"])
    if len(t) < 2 or (spec["agg"] == "count" and t["value"].nunique() == 1):
        return None, None
    label = measure_label(spec)
    t = t.rename(columns={"value": label})
    fig = px.area(t, x=spec["x"], y=label, markers=True)
    fig.update_traces(line={"color": theme.PURPLE, "width": 2.5}, fillcolor="rgba(125,17,164,0.12)",
                      marker={"size": 6, "color": theme.PURPLE})
    if len(t) >= 4:
        peak = t.loc[t[label].idxmax()]
        fig.add_annotation(x=peak[spec["x"]], y=peak[label], text="Peak", showarrow=True, arrowhead=2,
                           arrowcolor=theme.GREEN, font={"color": "#3E8A0A"}, ay=-30)
    _pct_axis(spec, pb, fig)
    return fig, t


def bar(df, spec, pb):
    t = aggregate(df, spec["x"], spec["y"], spec["agg"], spec.get("y2", "")).dropna()
    t = t.sort_values("value", ascending=False).head(TOP_N)
    if t.empty:
        return None, None
    label = measure_label(spec)
    t[spec["x"]] = t[spec["x"]].astype(str)
    t = t.rename(columns={"value": label})
    long_labels = t[spec["x"]].str.len().max() > 12 or len(t) > 7
    colors = [theme.GREEN if i == 0 else theme.PURPLE for i in range(len(t))]
    if long_labels:
        fig = go.Figure(go.Bar(x=t[label][::-1], y=t[spec["x"]][::-1], orientation="h", marker_color=colors[::-1],
                               text=t[label][::-1], texttemplate="%{text:.3s}", textposition="outside"))
        lo, hi = t[label].min(), t[label].max()
        pad = 0.18 * (max(hi, 0) - min(lo, 0))  # room for outside labels on both sides
        fig.update_layout(xaxis_title=label, yaxis_title="", xaxis_range=[min(0, lo - pad) if lo < 0 else 0, hi + pad])
        _pct_axis(spec, pb, fig, "x")
        if spec["agg"] in ("rate", "ratio"):
            fig.update_traces(texttemplate="%{text:.1%}")
    else:
        fig = go.Figure(go.Bar(x=t[spec["x"]], y=t[label], marker_color=colors, text=t[label],
                               texttemplate="%{text:.3s}", textposition="outside"))
        fig.update_layout(xaxis_title=spec["x"], yaxis_title=label, yaxis_range=[min(0, t[label].min() * 1.2), t[label].max() * 1.15])
        _pct_axis(spec, pb, fig)
        if spec["agg"] in ("rate", "ratio"):
            fig.update_traces(texttemplate="%{text:.1%}")
    fig.update_traces(cliponaxis=False)
    return fig, t


def donut(df, spec, pb):
    t = aggregate(df, spec["x"], spec["y"], spec["agg"]).dropna().sort_values("value", ascending=False)
    if (t["value"] < 0).any():
        return bar(df, spec, pb)  # negative slices (returns) can't be a donut, bars show them
    if len(t) < 2:
        return None, None
    t[spec["x"]] = t[spec["x"]].astype(str)
    # a donut of equal slices, or one dominated by "Other", says nothing
    if (len(t) > 4 and t["value"].nunique() == 1) or (len(t) > DONUT_N and t["value"].iloc[DONUT_N - 1:].sum() > 0.5 * t["value"].sum()):
        return None, None
    if len(t) > DONUT_N:
        other = pd.DataFrame({spec["x"]: ["Other"], "value": [t["value"].iloc[DONUT_N - 1:].sum()]})
        t = pd.concat([t.head(DONUT_N - 1), other], ignore_index=True)
    label = measure_label(spec)
    t = t.rename(columns={"value": label})
    fig = px.pie(t, names=spec["x"], values=label, hole=0.55, color_discrete_sequence=theme.CHART_COLORS)
    fig.update_traces(textinfo="percent", textposition="outside", sort=False)
    fig.update_layout(legend={"orientation": "v", "x": 1, "y": 0.5, "xanchor": "left", "yanchor": "middle"})
    return fig, t


def stacked_bar(df, spec, pb):
    d = _base(df, spec, pb)
    top = df[spec["color"]].astype(str).value_counts().head(DONUT_N).index
    d = d.assign(**{spec["color"]: np.where(d[spec["color"]].astype(str).isin(top), d[spec["color"]].astype(str), "Other")})
    t = aggregate(d, [spec["x"], spec["color"]], spec["y"], spec["agg"]).dropna()
    if t.empty or t[spec["x"]].nunique() < 2:
        return None, None
    label = measure_label(spec)
    t = t.rename(columns={"value": label}).sort_values(spec["x"])
    if pb.get(spec["x"], {}).get("kind") not in ("datetime", "period"):
        keep = t.groupby(spec["x"], observed=True)[label].sum().nlargest(TOP_N).index
        t = t[t[spec["x"]].isin(keep)]
        t[spec["x"]] = t[spec["x"]].astype(str)
    fig = px.bar(t, x=spec["x"], y=label, color=spec["color"], color_discrete_sequence=theme.CHART_COLORS)
    fig.update_layout(barmode="stack", bargap=0.25)
    return fig, t


def treemap(df, spec, pb):
    d = df.assign(**{spec["x"]: df[spec["x"]].astype(str), spec["color"]: df[spec["color"]].astype(str)})
    t = aggregate(d, [spec["x"], spec["color"]], spec["y"], spec["agg"]).dropna()
    t = t[t["value"] > 0]
    if t.empty:
        return None, None
    t = t.sort_values("value", ascending=False).head(60)
    label = measure_label(spec)
    t = t.rename(columns={"value": label})
    fig = px.treemap(t, path=[px.Constant("All"), spec["x"], spec["color"]], values=label,
                     color=spec["x"], color_discrete_sequence=theme.CHART_COLORS)
    fig.update_traces(root_color="#F4ECF8", textinfo="label+percent root")
    fig.update_layout(margin={"l": 8, "r": 8, "t": 56, "b": 8})
    return fig, t


def heatmap(df, spec, pb):
    t = aggregate(df.assign(**{spec["x"]: df[spec["x"]].astype(str), spec["color"]: df[spec["color"]].astype(str)}),
                  [spec["x"], spec["color"]], spec["y"], spec["agg"]).dropna()
    if t.empty:
        return None, None
    rows = t.groupby(spec["x"])["value"].sum().nlargest(TOP_N).index
    cols = t.groupby(spec["color"])["value"].sum().nlargest(TOP_N).index
    pivot = t.pivot_table(index=spec["x"], columns=spec["color"], values="value", aggfunc="sum").reindex(index=rows, columns=cols)
    if pivot.shape[0] < 2 or pivot.shape[1] < 2:
        return None, None
    fig = px.imshow(pivot, text_auto=".3s", aspect="auto", color_continuous_scale=theme.SEQUENTIAL)
    fig.update_layout(coloraxis_colorbar={"title": measure_label(spec)})
    return fig, pivot.reset_index()


def pareto(df, spec, pb):
    t = aggregate(df, spec["x"], spec["y"], "sum").dropna().sort_values("value", ascending=False)
    t = t[t["value"] > 0]
    if len(t) < 4:
        return None, None
    t["cum_pct"] = t["value"].cumsum() / t["value"].sum()
    # show items until the 80% line is crossed, so the cut-off is visible
    t = t.head(min(50, max(10, int((t["cum_pct"] < 0.85).sum()) + 1)))
    t[spec["x"]] = t[spec["x"]].astype(str)
    fig = go.Figure()
    fig.add_bar(x=t[spec["x"]], y=t["value"], name=spec["y"], marker_color=theme.PURPLE)
    fig.add_scatter(x=t[spec["x"]], y=t["cum_pct"], name="Cumulative %", yaxis="y2", mode="lines+markers",
                    line={"color": theme.ORANGE, "width": 2.5})
    fig.add_hline(y=0.8, yref="y2", line_dash="dot", line_color=theme.GREEN, annotation_text="80%",
                  annotation_font_color="#3E8A0A")
    fig.update_layout(yaxis2={"overlaying": "y", "side": "right", "tickformat": ".0%", "range": [0, 1.05],
                              "showgrid": False}, yaxis_title=spec["y"])
    return fig, t.rename(columns={"value": spec["y"]})


def histogram(df, spec, pb):
    s = pd.to_numeric(df[spec["x"]], errors="coerce")
    if spec.get("color"):
        # e.g. line amounts summed per invoice = order value
        s = s.groupby(df[spec["color"]]).sum()
    s = s.dropna()
    if s.nunique() < 3 or len(s) < 8:
        return None, None
    fig = px.histogram(s.rename(spec["x"]).to_frame(), x=spec["x"], nbins=min(30, max(8, s.nunique() // 2)))
    fig.update_traces(marker_color=theme.PURPLE, marker_line_color="white", marker_line_width=1)
    fig.add_vline(x=s.median(), line_dash="dot", line_color=theme.ORANGE, annotation_text=f"Median {s.median():,.3g}",
                  annotation_font_color="#C2620C")
    fig.update_layout(yaxis_title="Count", bargap=0.04)
    counts, edges = np.histogram(s, bins=min(30, max(8, s.nunique() // 2)))
    table = pd.DataFrame({"from": edges[:-1], "to": edges[1:], "count": counts})
    return fig, table


def box(df, spec, pb):
    top = df[spec["x"]].astype(str).value_counts().head(TOP_N).index
    d = df[df[spec["x"]].astype(str).isin(top)].assign(**{spec["x"]: lambda x: x[spec["x"]].astype(str)})
    if d[spec["y"]].dropna().nunique() < 3:
        return None, None
    fig = px.box(d, x=spec["x"], y=spec["y"], color=spec["x"], color_discrete_sequence=theme.CHART_COLORS, points="outliers")
    fig.update_layout(showlegend=False)
    table = d.groupby(spec["x"])[spec["y"]].describe()[["count", "min", "25%", "50%", "75%", "max"]].reset_index()
    return fig, table


def scatter(df, spec, pb):
    cols = [spec["x"], spec["y"]] + ([spec["color"]] if spec.get("color") else [])
    d = df[cols].dropna(subset=[spec["x"], spec["y"]])
    if len(d) < 5:
        return None, None
    if spec.get("color"):
        top = d[spec["color"]].astype(str).value_counts().head(DONUT_N).index
        d = d.assign(**{spec["color"]: np.where(d[spec["color"]].astype(str).isin(top), d[spec["color"]].astype(str), "Other")})
    sample = d.sample(min(len(d), 3000), random_state=1)
    fig = px.scatter(sample, x=spec["x"], y=spec["y"], color=spec.get("color") or None,
                     color_discrete_sequence=theme.CHART_COLORS, opacity=0.75)
    x, y = d[spec["x"]].astype(float), d[spec["y"]].astype(float)
    if x.nunique() > 1:
        slope, intercept = np.polyfit(x, y, 1)
        xs = np.linspace(x.min(), x.max(), 50)
        r = np.corrcoef(x, y)[0, 1]
        fig.add_scatter(x=xs, y=slope * xs + intercept, mode="lines", name=f"Trend (r = {r:.2f})",
                        line={"color": theme.NAVY, "dash": "dash"})
    if not spec.get("color"):
        fig.update_traces(marker={"color": theme.PURPLE}, selector={"mode": "markers"})
    return fig, d.describe().T.reset_index()


def waterfall(df, spec, pb):
    rev = pd.to_numeric(df[spec["x"]], errors="coerce").sum()
    cost = pd.to_numeric(df[spec["y"]], errors="coerce").sum()
    profit = pd.to_numeric(df[spec["y2"]], errors="coerce").sum()
    other = rev - cost - profit
    steps = [(spec["x"], rev, "absolute"), (spec["y"], -cost, "relative")]
    if abs(other) > 0.005 * abs(rev):
        steps.append(("Other", -other, "relative"))
    steps.append((spec["y2"], profit, "total"))
    fig = go.Figure(go.Waterfall(x=[s[0] for s in steps], y=[s[1] for s in steps], measure=[s[2] for s in steps],
                                 increasing={"marker": {"color": theme.GREEN}}, decreasing={"marker": {"color": theme.ORANGE}},
                                 totals={"marker": {"color": theme.PURPLE}}, connector={"line": {"color": "#C9C8D6"}},
                                 texttemplate="%{y:.3s}", textposition="outside"))
    return fig, pd.DataFrame(steps, columns=["step", "value", "type"])


def funnel(df, spec, pb):
    t = aggregate(df, spec["x"], spec["y"], spec["agg"]).dropna()
    order = pb.get(spec["x"], {}).get("period_order")
    t = t.sort_values("value", ascending=False) if not order else t
    if len(t) < 2:
        return None, None
    fig = go.Figure(go.Funnel(y=t[spec["x"]].astype(str), x=t["value"], textinfo="value+percent initial",
                              marker={"color": theme.CHART_COLORS[: len(t)]}))
    return fig, t


def metric_funnel(df, spec, pb):
    steps = [spec["x"], spec["y"], spec["y2"]]
    vals = [pd.to_numeric(df[c], errors="coerce").sum() for c in steps]
    fig = go.Figure(go.Funnel(y=steps, x=vals, textinfo="value+percent previous",
                              marker={"color": [theme.NAVY, theme.PURPLE, theme.GREEN]}))
    return fig, pd.DataFrame({"step": steps, "value": vals})


def weekday_heatmap(df, spec, pb):
    d = df.dropna(subset=[spec["x"]])
    ts = d[spec["x"]]
    if ts.dt.dayofweek.nunique() < 3:
        return None, None
    span = (ts.max() - ts.min()).days
    has_time = (ts.dt.hour + ts.dt.minute).gt(0).mean() > 0.5
    # short ranges with timestamps read best as weekday x hour; longer ones as weekday x month
    if span <= 92 and has_time:
        d = d.assign(_wd=ts.dt.dayofweek.map(dict(enumerate(WEEKDAYS))), _m=ts.dt.hour)
        col_label = "Hour of day"
    elif ts.dt.to_period("M").nunique() >= 2:
        d = d.assign(_wd=ts.dt.dayofweek.map(dict(enumerate(WEEKDAYS))), _m=ts.dt.to_period("M").astype(str))
        col_label = "Month"
    else:
        return None, None
    t = aggregate(d, ["_m", "_wd"], spec["y"], spec["agg"])
    pivot = t.pivot_table(index="_wd", columns="_m", values="value", aggfunc="sum").reindex(WEEKDAYS).dropna(how="all")
    if col_label == "Month":
        pivot = pivot.iloc[:, -18:]
    else:
        # every hour gets its own column, so quiet hours show as gaps instead of stretched cells
        pivot = pivot.reindex(columns=range(int(min(pivot.columns)), int(max(pivot.columns)) + 1))
        pivot.columns = [f"{h:02d}:00" for h in pivot.columns]
    fig = px.imshow(pivot, aspect="auto", color_continuous_scale=theme.SEQUENTIAL,
                    labels={"x": col_label, "y": "Weekday", "color": measure_label(spec)})
    return fig, pivot.reset_index().rename(columns={"_wd": "Weekday"})


def grouped_bar(df, spec, pb):
    ys = spec["y"].split("|")
    d = df[[spec["x"]] + ys].dropna(subset=[spec["x"]]).copy()
    d[spec["x"]] = d[spec["x"]].astype(str)
    long = d.melt(id_vars=spec["x"], value_vars=ys, var_name="Measure", value_name="Value")
    fig = px.bar(long, x=spec["x"], y="Value", color="Measure", barmode="group", color_discrete_sequence=theme.CHART_COLORS)
    return fig, d


def change_bar(df, spec, pb):
    d = df[[spec["x"], spec["y"], spec["y2"]] + ([spec["color"]] if spec.get("color") else [])].copy()
    d["cur"] = pd.to_numeric(d[spec["y"]], errors="coerce")
    d["prev"] = pd.to_numeric(d[spec["y2"]], errors="coerce")
    d = d.dropna(subset=["cur", "prev"])
    d = d[d["prev"] != 0]
    if d.empty:
        return None, None
    d["Change %"] = (d["cur"] - d["prev"]) / d["prev"].abs()
    lower = d[spec["color"]].astype(str).str.lower().str.contains("lower") if spec.get("color") else False
    good = (d["Change %"] < 0) == lower if spec.get("color") else d["Change %"] >= 0
    colors = [theme.GREEN if g else theme.ORANGE for g in good]
    d = d.assign(_c=colors).sort_values("Change %")
    fig = go.Figure(go.Bar(x=d["Change %"], y=d[spec["x"]].astype(str), orientation="h", marker_color=d["_c"],
                           text=d["Change %"], texttemplate="%{text:+.0%}", textposition="outside", cliponaxis=False))
    span = d["Change %"].abs().max() * 1.3
    fig.update_layout(xaxis_tickformat=".0%", xaxis_range=[-span if (d["Change %"] < 0).any() else 0, span], yaxis_title="")
    return fig, d[[spec["x"], spec["y2"], spec["y"], "Change %"]]


BUILDERS = {
    "change_bar": change_bar,
    "line": line, "bar": bar, "donut": donut, "stacked_bar": stacked_bar, "treemap": treemap, "heatmap": heatmap,
    "pareto": pareto, "histogram": histogram, "box": box, "scatter": scatter, "waterfall": waterfall,
    "funnel": funnel, "metric_funnel": metric_funnel, "weekday_heatmap": weekday_heatmap, "grouped_bar": grouped_bar,
}
