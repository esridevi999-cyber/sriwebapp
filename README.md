# InsightIQ

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
