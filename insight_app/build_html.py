"""Bundle InsightIQ into one self-contained HTML file that runs in the browser.

    py insight_app/build_html.py            -> writes insightiq.html in the project root
    py insight_app/build_html.py --package  -> also writes InsightIQ_GitHub/, a clean folder to push to
                                               GitHub: index.html, the app source, README, .gitignore.
                                               No .env, no cache, no data files.

The page loads stlite (Streamlit on Pyodide) from the jsDelivr CDN and mounts every module of
insight_app/ into the browser's virtual file system, so no server and no Python install are needed.
Nothing secret goes in: no .env, no API key, no data files. Viewers paste their own Anthropic key
in the sidebar if they want the AI features.
"""

import json
import shutil
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "insightiq.html"
PACKAGE_DIR = ROOT / "InsightIQ_GitHub"
PACKAGE_REQUIREMENTS = ["streamlit", "pandas", "plotly", "openpyxl", "python-docx", "pdfplumber", "anthropic",
                        "python-dotenv", "requests"]

STLITE_VERSION = "1.11.0"
# plotly 5 avoids the plotly/altair version clash stlite documents; the rest are pure-Python wheels
REQUIREMENTS = ["plotly==5.24.1", "openpyxl", "python-docx", "requests"]
SKIP = {"build_html.py"}


def js_string(text: str) -> str:
    # JSON is a valid JS string literal; "</" is escaped so app code can't close the <script> tag
    return json.dumps(text).replace("</", "<\\/")


def build() -> Path:
    files = {f"insight_app/{p.name}": p.read_text(encoding="utf-8")
             for p in sorted(HERE.glob("*.py")) if p.name not in SKIP}
    file_entries = ",\n".join(f"        {json.dumps(name)}: {js_string(src)}" for name, src in files.items())
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />
  <meta name="description" content="InsightIQ: upload a sales, HR, support or any other table and get a domain-aware KPI dashboard. Runs fully in your browser." />
  <title>InsightIQ</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@stlite/browser@{STLITE_VERSION}/build/stlite.css" />
  <style>
    html, body {{ margin: 0; background: #F7F6FB; }}
    #iq-loading {{ font-family: 'Segoe UI', sans-serif; color: #171A5F; text-align: center; padding: 18vh 16px 0; }}
    #iq-loading h1 {{ color: #7D11A4; margin-bottom: 6px; }}
    #iq-loading p {{ color: #5F6180; max-width: 520px; margin: 6px auto; line-height: 1.5; }}
  </style>
</head>
<body>
  <div id="iq-loading">
    <h1>InsightIQ</h1>
    <p>Loading Python in your browser. The first visit takes 20 to 40 seconds; later visits are faster.</p>
    <p>Your files never leave this browser. If you paste an Anthropic API key, it is sent only to Anthropic.</p>
  </div>
  <div id="root"></div>
  <script type="module">
    import {{ mount }} from "https://cdn.jsdelivr.net/npm/@stlite/browser@{STLITE_VERSION}/build/stlite.js";
    document.getElementById("iq-loading").remove();
    mount(
      {{
        requirements: {json.dumps(REQUIREMENTS)},
        entrypoint: "insight_app/app.py",
        files: {{
{file_entries}
        }},
        streamlitConfig: {{
          "client.toolbarMode": "viewer",
          "theme.base": "light",
          "theme.primaryColor": "#7D11A4",
        }},
      }},
      document.getElementById("root"),
    );
  </script>
  <!-- Built from insight_app/ on {date.today():%d %b %Y}. Rebuild with: py insight_app/build_html.py -->
</body>
</html>
"""
    OUT.write_text(page, encoding="utf-8")
    return OUT


README = """# InsightIQ

Upload a sales, HR, finance, customer support, marketing or any other table and get a KPI dashboard
built for that department. InsightIQ reads the column names and values, works out the domain, and
computes every KPI, chart and insight with pandas.

## Two ways to run it

**In the browser, nothing to install.** Open `index.html` (or the GitHub Pages link). Python runs inside
the page, so the first load takes 20 to 40 seconds. Excel, CSV and Word tables work; PDF upload needs
the Python version below.

**As a Python app, full features including PDF.**

```bash
pip install -r requirements.txt
streamlit run insight_app/app.py
```

## AI features (optional)

Without a key the dashboard uses built-in rules. With an Anthropic API key, Claude reads the column
names and a few sample rows, plans the KPIs and charts, and answers open questions such as
"why did returns go up?". Claude never sees the full table; pandas computes every number.

- Browser version: paste your key in the sidebar under "AI (optional)". It stays in your browser
  session and is sent only to Anthropic.
- Python version: put `ANTHROPIC_API_KEY=sk-ant-...` in a `.env` file next to this README.

Never commit a key. `.env` is already in `.gitignore`.

## Features

- Upload Excel (every sheet), CSV and Word tables (plus PDF in the Python version); title rows,
  several tables per sheet, note lines and total rows are handled.
- Domain detection with a dropdown to change it.
- KPI cards with month-on-month change, up to 16 charts across Overview, Breakdown and Distribution.
- Sales order lines: revenue from quantity x price, returns and postage split out, return rate,
  repeat customers, basket size.
- "Ask a question" box: direct questions are answered by pandas, open questions by Claude.
- Downloadable HTML report that works offline.

## Hosting on GitHub Pages

Push this folder to a GitHub repository, then Settings > Pages > Deploy from branch > `main` / root.
The app will be at `https://<your-user>.github.io/<repo>/`.

## Rebuilding index.html

`index.html` is generated from `insight_app/`. After changing the code, run
`python insight_app/build_html.py --package` from the original project and copy the folder again.
"""

GITIGNORE = """.env
.streamlit/secrets.toml
insight_app/cache/
__pycache__/
*.pyc
"""


def package() -> Path:
    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR)
    (PACKAGE_DIR / "insight_app").mkdir(parents=True)
    shutil.copy(OUT, PACKAGE_DIR / "index.html")
    for p in sorted(HERE.glob("*.py")):
        shutil.copy(p, PACKAGE_DIR / "insight_app" / p.name)
    (PACKAGE_DIR / "requirements.txt").write_text("\n".join(PACKAGE_REQUIREMENTS) + "\n", encoding="utf-8")
    (PACKAGE_DIR / "README.md").write_text(README, encoding="utf-8")
    (PACKAGE_DIR / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    return PACKAGE_DIR


if __name__ == "__main__":
    out = build()
    print(f"Wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
    if "--package" in sys.argv:
        folder = package()
        print(f"Wrote {folder}: " + ", ".join(sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())))
