"""Electric Carnival palette, Plotly template and Streamlit CSS for InsightIQ."""

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

GREEN = "#5CB714"
PURPLE = "#7D11A4"
NAVY = "#171A5F"
YELLOW = "#F1CC22"
ORANGE = "#F08C24"

# Yellow is a fill colour only: it fails contrast as text on white.
CHART_COLORS = [PURPLE, GREEN, ORANGE, NAVY, YELLOW, "#B04FD1", "#8FD45A", "#F5B46B", "#4A4F9C", "#F7DE6E"]
SEQUENTIAL = ["#F4ECF8", "#D9B3E8", "#B06BCC", PURPLE, NAVY]
DIVERGING = [ORANGE, "#F7C894", "#F5F5F7", "#B5DE91", GREEN]

PAGE_BG = "#F7F6FB"
CARD_BG = "#FFFFFF"
TEXT = "#1E1F3B"
MUTED = "#5F6180"
KPI_GRADIENT = f"linear-gradient(135deg, {PURPLE} 0%, {NAVY} 100%)"


def register_plotly_template():
    template = go.layout.Template()
    template.layout = go.Layout(
        colorway=CHART_COLORS,
        font={"family": "Poppins, Segoe UI, sans-serif", "color": TEXT, "size": 12},
        title={"font": {"size": 15, "color": NAVY}, "x": 0.01, "xanchor": "left"},
        paper_bgcolor=CARD_BG,
        plot_bgcolor=CARD_BG,
        margin={"l": 48, "r": 20, "t": 56, "b": 44},
        xaxis={"gridcolor": "#ECEBF3", "linecolor": "#D8D7E3", "zeroline": False},
        yaxis={"gridcolor": "#ECEBF3", "linecolor": "#D8D7E3", "zeroline": False},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
        hoverlabel={"bgcolor": NAVY, "font": {"color": "white"}},
        colorscale={"sequential": [[i / (len(SEQUENTIAL) - 1), c] for i, c in enumerate(SEQUENTIAL)]},
    )
    pio.templates["carnival"] = template
    pio.templates.default = "plotly_white+carnival"


def inject_css():
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap');
        html, body, [class*="css"], .stApp {{ font-family: 'Poppins', 'Segoe UI', sans-serif; }}
        .stApp {{ background: {PAGE_BG}; }}
        [data-testid="stSidebar"] {{ background: {NAVY}; }}
        [data-testid="stSidebar"] * {{ color: #FFFFFF; }}
        [data-testid="stSidebar"] [data-baseweb="select"] *,
        [data-testid="stSidebar"] [data-baseweb="input"] *,
        [data-testid="stSidebar"] input {{ color: {TEXT} !important; }}
        [data-testid="stSidebar"] [data-baseweb="tag"] {{ background: {PURPLE} !important; }}
        [data-testid="stSidebar"] [data-baseweb="tag"] * {{ color: #FFFFFF !important; }}
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {{
            background: rgba(255,255,255,0.06) !important; border: 1px dashed rgba(255,255,255,0.35) !important;
        }}
        [data-testid="stSidebar"] [data-testid="stFileChip"] {{ background: rgba(255,255,255,0.12) !important; }}
        [data-testid="stSidebar"] [data-testid="stFileChip"] * {{ color: #FFFFFF !important; }}
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button {{ color: {TEXT} !important; }}
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button * {{ color: {TEXT} !important; }}
        [data-testid="stMainBlockContainer"] {{ padding-top: 28px !important; }}
        .stButton > button[kind="primary"], .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {{
            background: {PURPLE}; border-color: {PURPLE}; color: #FFFFFF !important;
        }}
        .stButton > button[kind="primary"] p, .stDownloadButton > button p, [data-testid="stFormSubmitButton"] > button p {{ color: #FFFFFF !important; }}
        .stButton > button[kind="secondary"] {{ border-color: {PURPLE}; color: {PURPLE}; background: #FFFFFF; }}
        [data-testid="stTab"][aria-selected="true"] p {{ color: {PURPLE} !important; font-weight: 600; }}
        .react-aria-SelectionIndicator, [data-baseweb="tab-highlight"] {{ background-color: {PURPLE} !important; }}
        .iq-hero {{ background: {KPI_GRADIENT}; border-radius: 16px; padding: 22px 26px; color: #FFFFFF; margin-bottom: 14px; }}
        .iq-hero h1 {{ color: #FFFFFF; font-size: 28px; margin: 0 0 4px 0; padding: 0; }}
        .iq-hero p {{ color: rgba(255,255,255,0.88); margin: 0; font-size: 14px; }}
        .iq-chip {{ display: inline-block; background: {YELLOW}; color: {NAVY}; font-weight: 600; font-size: 12px;
                    border-radius: 999px; padding: 3px 12px; margin-right: 8px; margin-bottom: 8px; }}
        .iq-chip.ghost {{ background: rgba(255,255,255,0.14); color: #FFFFFF; }}
        .iq-kpi {{ background: {CARD_BG}; border-radius: 14px; padding: 14px 16px; border-top: 5px solid {PURPLE};
                   box-shadow: 0 1px 3px rgba(23,26,95,0.08); min-height: 112px; margin-bottom: 12px; }}
        .iq-kpi .lbl {{ color: {MUTED}; font-size: 12.5px; font-weight: 500; }}
        .iq-kpi .val {{ color: {NAVY}; font-size: 26px; font-weight: 700; line-height: 1.25; font-variant-numeric: tabular-nums; }}
        .iq-kpi .sub {{ color: {MUTED}; font-size: 11.5px; }}
        .iq-kpi .up {{ color: #3E8A0A; font-weight: 600; }}
        .iq-kpi .down {{ color: #C2620C; font-weight: 600; }}
        .iq-card {{ background: {CARD_BG}; border-radius: 14px; padding: 14px 18px; box-shadow: 0 1px 3px rgba(23,26,95,0.08); margin-bottom: 12px; }}
        .iq-card h4 {{ color: {NAVY}; margin: 0 0 6px 0; font-size: 15px; }}
        .iq-card li {{ margin-bottom: 4px; }}
        .iq-answer {{ background: {CARD_BG}; border-left: 5px solid {GREEN}; border-radius: 10px; padding: 12px 16px; margin: 8px 0; }}
        .iq-why {{ color: {MUTED}; font-size: 12px; margin-top: -6px; margin-bottom: 14px; }}
        </style>
        """,
        unsafe_allow_html=True,
    )
