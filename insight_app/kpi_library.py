"""Domain KPI and chart templates. "@role" is swapped for the mapped column at run time; a spec
whose roles are missing is skipped. KPIs sharing a "group" are alternatives: first one that
resolves wins.

KPI aggs:   sum, mean, median, min, max, count_rows, nunique, ratio_sum (sum col / sum column2),
            per_unique (sum col / unique column2), share_true, share_value (rows where col matches
            filter_value, a regex), share_of_gross (|sum col over the where-rows| / sum of positive col),
            repeat_rate (share of col values with more than one distinct column2), sum_abs
Row filter (KPIs and charts): where_column + where_value (regex) + where_not. Optional: dropped when
            the column is missing, unless a chart sets where_required.
Chart types: line, bar, donut, stacked_bar, treemap, heatmap, pareto, histogram, box, scatter,
            waterfall, funnel, weekday_heatmap, grouped_bar, metric_funnel
Chart aggs:  sum, mean, median, count, nunique, rate (share of truthy y), ratio (sum y / sum y2), min, max
A histogram with `color` first sums x per color value (e.g. order value per invoice).
"""

KPI_AGGS = ["sum", "mean", "median", "min", "max", "count_rows", "nunique", "ratio_sum", "per_unique",
            "share_true", "share_value", "share_of_gross", "repeat_rate", "sum_abs"]
CHART_TYPES = ["line", "bar", "donut", "stacked_bar", "treemap", "heatmap", "pareto", "histogram", "box",
               "scatter", "waterfall", "funnel", "weekday_heatmap", "grouped_bar", "metric_funnel"]
CHART_AGGS = ["sum", "mean", "median", "count", "nunique", "rate", "ratio", "min", "max"]
FORMATS = ["auto", "number", "currency", "percent", "decimal", "multiple"]

TAB_OF = {
    "line": "Overview", "bar": "Overview", "donut": "Overview", "stacked_bar": "Overview", "funnel": "Overview",
    "metric_funnel": "Overview", "grouped_bar": "Overview",
    "treemap": "Breakdown", "heatmap": "Breakdown", "pareto": "Breakdown", "waterfall": "Breakdown",
    "weekday_heatmap": "Breakdown",
    "histogram": "Distribution", "box": "Distribution", "scatter": "Distribution",
}


def K(title, agg, column="", column2="", filter_value="", fmt="auto", why="", group=None, **extra):
    return {"title": title, "agg": agg, "column": column, "column2": column2, "filter_value": filter_value,
            "fmt": fmt, "why": why, "group": group or title, **extra}


def C(type_, title, x="", y="", agg="sum", color="", y2="", why="", **extra):
    return {"type": type_, "title": title, "x": x, "y": y, "agg": agg, "color": color, "y2": y2, "why": why, **extra}


# order lines that are not returns / cancellations
NOT_RETURN = {"where_column": "@line_type", "where_value": "return", "where_not": True}
ONLY_PRODUCTS = {"where_column": "@line_type", "where_value": "product"}
ONLY_RETURNS = {"where_column": "@line_type", "where_value": "return", "where_required": True}

KPI_TEMPLATES = {
    "Sales": [
        K("Net Revenue", "sum", "@revenue", fmt="currency", why="Sales after returns and cancellations",
          group="revenue", requires=["@line_type"]),
        K("Total Revenue", "sum", "@revenue", fmt="currency", why="Top-line sales value", group="revenue"),
        K("Orders", "nunique", "@order_id", group="volume", why="Distinct invoices, cancellations excluded", **NOT_RETURN),
        K("Transactions", "count_rows", group="volume"),
        K("Avg Order Value", "per_unique", "@revenue", "@order_id", fmt="currency", group="aov",
          why="Revenue per invoice, cancellations excluded", **NOT_RETURN),
        K("Avg Sale Value", "mean", "@revenue", fmt="currency", group="aov"),
        K("Customers", "nunique", "@customer", why="Distinct buying customers", **NOT_RETURN),
        K("Gross Profit", "sum", "@profit", fmt="currency"),
        K("Profit Margin", "ratio_sum", "@profit", "@revenue", fmt="percent"),
        K("Return Rate", "share_of_gross", "@revenue", fmt="percent", lower_better=True, requires=["@line_type"],
          why="Value returned or cancelled, as a share of gross sales", where_column="@line_type", where_value="return"),
        K("Units Sold", "sum", "@quantity", fmt="number", **NOT_RETURN),
        K("Repeat Customers", "repeat_rate", "@customer", "@order_id", fmt="percent",
          why="Customers with more than one order", **NOT_RETURN),
        K("Units per Order", "per_unique", "@quantity", "@order_id", fmt="decimal", why="Average basket size", **NOT_RETURN),
        K("Target Achieved", "ratio_sum", "@revenue", "@target", fmt="percent"),
        K("Avg Discount", "mean", "@discount"),
        K("Products Sold", "nunique", "@product", **ONLY_PRODUCTS),
    ],
    "HR": [
        K("Headcount", "nunique", "@employee_id", group="headcount"),
        K("Headcount", "count_rows", group="headcount"),
        K("Avg Salary", "mean", "@salary", fmt="currency"),
        K("Total Payroll", "sum", "@salary", fmt="currency"),
        K("Attrition Rate", "share_true", "@attrition", fmt="percent"),
        K("Avg Tenure", "mean", "@tenure", fmt="decimal"),
        K("Avg Age", "mean", "@age", fmt="decimal"),
        K("Women in Workforce", "share_value", "@gender", filter_value="f|female|woman|women", fmt="percent"),
        K("Avg Rating", "mean", "@rating", fmt="decimal"),
        K("Departments", "nunique", "@department"),
        K("Locations", "nunique", "@region"),
        K("Job Roles", "nunique", "@job"),
    ],
    "Customer Support": [
        K("Tickets", "sum", "@tickets", group="tickets"),
        K("Tickets", "nunique", "@ticket_id", group="tickets"),
        K("Tickets", "count_rows", group="tickets"),
        K("Avg Resolution Time", "mean", "@resolution_time", fmt="decimal"),
        K("CSAT", "mean", "@csat"),
        K("SLA Met", "share_true", "@sla_met", fmt="percent"),
        K("High Priority Share", "share_value", "@priority", filter_value="high|urgent|critical|p1|p0", fmt="percent"),
        K("Agents", "nunique", "@agent"),
        K("Tickets per Agent", "per_unique", "@tickets", "@agent", fmt="decimal"),
    ],
    "Marketing": [
        K("Spend", "sum", "@cost", fmt="currency"),
        K("Impressions", "sum", "@impressions"),
        K("Clicks", "sum", "@clicks"),
        K("CTR", "ratio_sum", "@clicks", "@impressions", fmt="percent"),
        K("Conversions", "sum", "@conversions"),
        K("Conversion Rate", "ratio_sum", "@conversions", "@clicks", fmt="percent"),
        K("Cost per Conversion", "ratio_sum", "@cost", "@conversions", fmt="currency"),
        K("Revenue", "sum", "@revenue", fmt="currency"),
        K("ROAS", "ratio_sum", "@revenue", "@cost", fmt="multiple"),
        K("Campaigns", "nunique", "@product"),
    ],
    "Finance": [
        K("Revenue", "sum", "@revenue", fmt="currency"),
        K("Expenses", "sum", "@cost", fmt="currency"),
        K("Profit", "sum", "@profit", fmt="currency"),
        K("Margin", "ratio_sum", "@profit", "@revenue", fmt="percent"),
        K("Budget", "sum", "@target", fmt="currency"),
        K("Spend vs Budget", "ratio_sum", "@cost", "@target", fmt="percent"),
        K("Accounts", "nunique", "@customer"),
    ],
    "Operations / Inventory": [
        K("Stock on Hand", "sum", "@stock"),
        K("Units", "sum", "@quantity"),
        K("SKUs", "nunique", "@product"),
        K("Inventory Cost", "sum", "@cost", fmt="currency"),
        K("Locations", "nunique", "@region"),
        K("Avg Unit Price", "mean", "@unit_price", fmt="currency"),
    ],
    "Education": [
        K("Students", "nunique", "@customer", group="students"),
        K("Records", "count_rows", group="students"),
        K("Avg Score", "mean", "@rating", fmt="decimal"),
        K("Courses", "nunique", "@product"),
        K("Classes", "nunique", "@category"),
    ],
    "Healthcare": [
        K("Patients", "nunique", "@customer", group="patients"),
        K("Records", "count_rows", group="patients"),
        K("Total Cost", "sum", "@cost", fmt="currency"),
        K("Revenue", "sum", "@revenue", fmt="currency"),
        K("Departments", "nunique", "@department"),
    ],
    "Generic": [],
}

CHART_TEMPLATES = {
    "Sales": [
        C("line", "Revenue trend", "@date", "@revenue", why="How sales move over time"),
        C("bar", "Top products by revenue", "@product", "@revenue", why="Which products earn the most", **ONLY_PRODUCTS),
        C("bar", "Top products by units", "@product", "@quantity", why="Which products move the most volume", **ONLY_PRODUCTS),
        C("pareto", "Customer concentration (80/20)", "@customer", "@revenue", why="How few customers drive most revenue", **NOT_RETURN),
        C("donut", "Revenue share by region", "@region", "@revenue", why="Where sales come from", **NOT_RETURN),
        C("donut", "Revenue by channel", "@channel", "@revenue", **NOT_RETURN),
        C("bar", "Sales rep leaderboard", "@salesperson", "@revenue", why="Revenue closed by each rep"),
        C("bar", "Profit margin by category", "@category", "@profit", agg="ratio", y2="@revenue", why="Profit as a share of revenue"),
        C("bar", "Revenue by line type", "@line_type", "@revenue", why="Products vs shipping and fees, with returns netted off"),
        C("stacked_bar", "Revenue by category over time", "@date", "@revenue", color="@category"),
        C("treemap", "Revenue by region and product", "@region", "@revenue", color="@product", **NOT_RETURN),
        C("heatmap", "Revenue: region vs category", "@region", "@revenue", color="@category"),
        C("weekday_heatmap", "When sales happen", "@date", "@revenue", why="Busy days and hours (months for longer ranges)", **NOT_RETURN),
        C("bar", "Most returned products", "@product", agg="count", why="Products on the most cancelled lines", **ONLY_RETURNS),
        C("waterfall", "Revenue to profit bridge", "@revenue", "@cost", y2="@profit"),
        C("funnel", "Pipeline by stage", "@stage", "@revenue"),
        C("histogram", "Order value distribution", "@revenue", color="@order_id", why="Spread of invoice totals", **NOT_RETURN),
        C("scatter", "Unit price vs quantity", "@unit_price", "@quantity", why="Do cheaper items sell in bigger quantities?", **ONLY_PRODUCTS),
        C("scatter", "Discount vs profit", "@discount", "@profit", color="@category", why="Does discounting hurt profit?"),
        C("box", "Sale value spread by region", "@region", "@revenue"),
    ],
    "HR": [
        C("bar", "Headcount by department", "@department", agg="count"),
        C("bar", "Avg salary by department", "@department", "@salary", agg="mean"),
        C("bar", "Avg salary by job role", "@job", "@salary", agg="mean"),
        C("donut", "Gender mix", "@gender", agg="count"),
        C("donut", "Headcount by location", "@region", agg="count"),
        C("bar", "Attrition rate by department", "@department", "@attrition", agg="rate", why="Share of people who left"),
        C("line", "Hiring trend", "@hire_date", agg="count"),
        C("line", "Exits over time", "@exit_date", agg="count"),
        C("stacked_bar", "Gender mix by department", "@department", color="@gender", agg="count"),
        C("treemap", "Workforce by department and role", "@department", agg="count", color="@job"),
        C("heatmap", "Ratings across departments", "@department", agg="count", color="@rating"),
        C("histogram", "Salary bands", "@salary"),
        C("histogram", "Tenure bands", "@tenure"),
        C("box", "Salary spread by department", "@department", "@salary"),
        C("box", "Salary spread by location", "@region", "@salary"),
        C("scatter", "Tenure vs salary", "@tenure", "@salary", color="@department"),
    ],
    "Customer Support": [
        C("line", "Ticket volume trend", "@date", "@tickets"),
        C("line", "Ticket volume trend", "@date", agg="count"),
        C("line", "CSAT trend", "@date", "@csat", agg="mean"),
        C("line", "Resolution time trend", "@date", "@resolution_time", agg="mean"),
        C("bar", "Tickets by agent", "@agent", "@tickets"),
        C("bar", "Tickets by agent", "@agent", agg="count"),
        C("bar", "Avg resolution time by agent", "@agent", "@resolution_time", agg="mean"),
        C("bar", "CSAT by agent", "@agent", "@csat", agg="mean"),
        C("donut", "Tickets by priority", "@priority", agg="count"),
        C("donut", "Tickets by category", "@category", agg="count"),
        C("donut", "Ticket status", "@status", agg="count"),
        C("heatmap", "Category vs priority", "@category", agg="count", color="@priority"),
        C("weekday_heatmap", "Tickets by weekday and month", "@date", agg="count"),
        C("histogram", "Resolution time distribution", "@resolution_time"),
        C("box", "Resolution time by priority", "@priority", "@resolution_time"),
        C("scatter", "Resolution time vs CSAT", "@resolution_time", "@csat", color="@agent"),
    ],
    "Marketing": [
        C("line", "Spend trend", "@date", "@cost"),
        C("line", "Conversions trend", "@date", "@conversions"),
        C("bar", "Conversions by channel", "@channel", "@conversions"),
        C("bar", "Conversion rate by channel", "@channel", "@conversions", agg="ratio", y2="@clicks"),
        C("donut", "Spend by channel", "@channel", "@cost"),
        C("bar", "Top campaigns by conversions", "@product", "@conversions"),
        C("metric_funnel", "Impressions to conversions", "@impressions", "@clicks", y2="@conversions"),
        C("scatter", "Spend vs conversions", "@cost", "@conversions", color="@channel"),
        C("pareto", "Campaign concentration", "@product", "@conversions"),
    ],
    "Finance": [
        C("line", "Revenue trend", "@date", "@revenue"),
        C("line", "Expense trend", "@date", "@cost"),
        C("bar", "Expenses by category", "@category", "@cost"),
        C("bar", "Revenue by account", "@customer", "@revenue"),
        C("donut", "Expense mix", "@category", "@cost"),
        C("waterfall", "Revenue to profit bridge", "@revenue", "@cost", y2="@profit"),
        C("pareto", "Expense concentration", "@category", "@cost"),
    ],
    "Operations / Inventory": [
        C("bar", "Stock by product", "@product", "@stock"),
        C("bar", "Stock by location", "@region", "@stock"),
        C("donut", "Stock by category", "@category", "@stock"),
        C("line", "Units trend", "@date", "@quantity"),
        C("pareto", "Stock concentration", "@product", "@stock"),
        C("histogram", "Stock level distribution", "@stock"),
    ],
    "Education": [
        C("bar", "Avg score by course", "@product", "@rating", agg="mean"),
        C("bar", "Avg score by class", "@category", "@rating", agg="mean"),
        C("histogram", "Score distribution", "@rating"),
        C("box", "Score spread by course", "@product", "@rating"),
    ],
    "Healthcare": [
        C("line", "Patient volume trend", "@date", agg="count"),
        C("bar", "Patients by department", "@department", agg="count"),
        C("bar", "Cost by department", "@department", "@cost"),
        C("histogram", "Cost distribution", "@cost"),
    ],
    "Generic": [],
}
