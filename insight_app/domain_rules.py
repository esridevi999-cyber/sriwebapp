"""No-LLM understanding: score the domain from column names, map columns to business roles."""

import re

from insight_app.profiler import name_words

DOMAINS = ["Sales", "HR", "Finance", "Customer Support", "Marketing", "Operations / Inventory",
           "Education", "Healthcare", "Generic"]

DOMAIN_KEYWORDS = {
    "Sales": ["sales", "revenue", "order", "invoice", "customer", "product", "sku", "units", "quantity", "qty",
              "discount", "deal", "rep", "salesperson", "territory", "region", "price", "profit", "pipeline", "quota"],
    "HR": ["employee", "emp", "salary", "ctc", "department", "dept", "designation", "job", "hire", "joining",
           "attrition", "tenure", "gender", "manager", "grade", "performance", "leave", "resign", "headcount", "age"],
    "Finance": ["account", "ledger", "debit", "credit", "balance", "expense", "budget", "actual", "variance",
                "cost center", "gl", "tax", "payable", "receivable", "cash", "ebitda", "income", "asset"],
    "Customer Support": ["ticket", "csat", "resolution", "response", "sla", "agent", "priority", "escalat",
                         "case", "nps", "first contact", "backlog", "support", "handle time", "aht"],
    "Marketing": ["campaign", "impression", "click", "ctr", "conversion", "lead", "spend", "cpc", "cpa", "roas",
                  "channel", "source", "medium", "engagement", "followers", "reach", "open rate", "bounce"],
    "Operations / Inventory": ["stock", "inventory", "warehouse", "supplier", "vendor", "reorder", "shipment",
                               "delivery", "lead time", "on hand", "backorder", "production", "defect", "po"],
    "Education": ["student", "grade", "marks", "score", "course", "subject", "attendance", "semester", "class",
                  "enrol", "teacher", "exam", "gpa"],
    "Healthcare": ["patient", "diagnosis", "doctor", "admission", "discharge", "ward", "bed", "treatment",
                   "readmission", "claim", "hospital", "procedure", "los"],
}

# role -> (accepted kinds, regex on the column name). Order matters: earlier roles claim columns first.
ROLE_PATTERNS = [
    ("order_id", {"identifier", "categorical", "numeric"}, r"(order|invoice|transaction|bill|receipt)\s*(id|no|number|#)?"),
    ("ticket_id", {"identifier", "categorical", "numeric"}, r"(ticket|case|incident)\s*(id|no|number|#)"),
    ("employee_id", {"identifier", "numeric", "categorical"}, r"(emp|employee|staff)\s*(id|no|number|code|#)"),
    ("hire_date", {"datetime"}, r"(hire|join|doj|start|onboard)"),
    ("exit_date", {"datetime"}, r"(exit|termination|leav|resign|separat|relieving|last working)"),
    ("date", {"datetime", "period"}, r"(order|invoice|transaction|sale|created|ticket|txn|posting|date|month|period|week|quarter|year|day)"),
    ("revenue", {"numeric"}, r"(revenue|sales|turnover|gmv|amount|total|net sales|gross sales|value|income|billing)"),
    ("profit", {"numeric"}, r"(profit|margin|earnings|ebitda)"),
    ("cost", {"numeric"}, r"(cost|cogs|expense|spend)"),
    ("quantity", {"numeric"}, r"(qty|quantity|units|unit sold|volume|pieces|pcs)"),
    ("unit_price", {"numeric"}, r"(price|rate|mrp|unit cost)"),
    ("discount", {"numeric"}, r"(discount|rebate|markdown)"),
    ("target", {"numeric"}, r"(target|quota|budget|goal|plan)"),
    ("salary", {"numeric"}, r"(salary|ctc|compensation|pay|wage|income|stipend)"),
    ("age", {"numeric"}, r"^age$|\bage\b"),
    ("tenure", {"numeric"}, r"(tenure|experience|years of service|yrs|service)"),
    ("rating", {"numeric", "categorical"}, r"(rating|performance|appraisal|score|grade)"),
    ("csat", {"numeric"}, r"(csat|satisfaction|nps)"),
    ("resolution_time", {"numeric"}, r"(resolution|handle|response|aht|turnaround|\btat\b|duration|time to)(?!.*rate)"),
    ("tickets", {"numeric"}, r"(tickets|cases|requests|calls|chats|contacts)"),
    ("sla_met", {"boolean", "numeric"}, r"(sla|within sla|met sla)"),
    ("impressions", {"numeric"}, r"(impression|reach|views)"),
    ("clicks", {"numeric"}, r"(click)"),
    ("conversions", {"numeric"}, r"(conversion|signup|purchase|leads?)\b"),
    ("stock", {"numeric"}, r"(\bstock\b(?!\s*code)|inventory|on hand|available)"),
    ("attrition", {"boolean", "categorical"}, r"(attrition|left|exited|churn|terminated|active)"),
    ("gender", {"categorical", "boolean"}, r"(gender|sex)"),
    ("department", {"categorical"}, r"(department|dept|division|function|team|business unit|bu)"),
    ("job", {"categorical", "identifier"}, r"(job|role|designation|title|position|grade|level)"),
    ("product", {"categorical", "identifier"}, r"(product|item|\bsku\b|model|service|plan|course|subject|^description$|^desc$)"),
    ("line_type", {"categorical"}, r"^(line|transaction|row) type$"),
    ("category", {"categorical"}, r"(category|segment|type|class|group|line|brand|sub.?category)"),
    ("region", {"categorical"}, r"(region|state|city|country|location|zone|territory|branch|area|market|store)"),
    ("customer", {"categorical", "identifier"}, r"(customer|client|account|buyer|company|patient|student)"),
    ("employee", {"identifier", "categorical"}, r"(employee|\bemp\b|staff|^name$|^full name$)"),
    ("agent", {"categorical", "identifier"}, r"(agent|analyst|assignee|handled by|resolver)"),
    ("salesperson", {"categorical"}, r"(rep|salesperson|sales person|executive|owner|seller|agent|associate|manager)"),
    ("channel", {"categorical"}, r"(channel|source|medium|platform|mode)"),
    ("priority", {"categorical"}, r"(priority|severity|urgency)"),
    ("stage", {"categorical"}, r"(stage|funnel|pipeline|phase)"),
    ("status", {"categorical", "boolean"}, r"(status|state|outcome|result)"),
    ("metric_name", {"categorical", "identifier"}, r"^(metric|kpi|measure|indicator|parameter|item)s?$"),
]

ROLE_NAMES = [r for r, _, _ in ROLE_PATTERNS]

# roles that hold amounts or durations, never a percentage column
NO_PERCENT = {"revenue", "profit", "cost", "quantity", "unit_price", "salary", "tickets", "resolution_time", "stock",
              "impressions", "clicks", "conversions", "target", "age", "tenure"}

# primary measure per domain, first available wins
PRIMARY_MEASURE = {
    "Sales": ["revenue", "profit", "quantity"],
    "HR": ["salary", "tenure", "rating"],
    "Finance": ["revenue", "cost", "profit"],
    "Customer Support": ["tickets", "csat", "resolution_time"],
    "Marketing": ["conversions", "clicks", "cost", "impressions"],
    "Operations / Inventory": ["stock", "quantity", "cost"],
    "Education": ["rating"],
    "Healthcare": ["cost", "revenue"],
}

# measures that are averaged, never summed, when grouped
MEAN_ROLES = {"salary", "csat", "rating", "resolution_time", "tenure", "age", "unit_price", "discount"}
# for these a falling number is good news
LOWER_IS_BETTER = {"cost", "resolution_time", "attrition", "discount"}

EXIT_WORDS =re.compile(r"(resign|terminat|left|exit|inactive|separated|relieved|churn)", re.I)


def detect_domain(profiles: list) -> tuple:
    """Returns (domain, confidence 0..1)."""
    text = " ".join(name_words(p["name"]).lower() for p in profiles)
    samples = " ".join(" ".join(p["samples"]).lower() for p in profiles if p["kind"] in ("categorical", "boolean"))
    scores = {}
    for domain, words in DOMAIN_KEYWORDS.items():
        scores[domain] = sum(2 for w in words if w in text) + sum(0.5 for w in words if w in samples)
    best = max(scores, key=scores.get)
    total = sum(scores.values())
    if scores[best] < 2:
        return "Generic", 0.3
    return best, round(min(0.95, scores[best] / total + 0.25), 2)


def assign_roles(profiles: list, domain: str = None) -> dict:
    """Returns {role: column}. Each column takes at most one role."""
    roles, used = {}, set()
    kinds = {p["name"]: p["kind"] for p in profiles}
    order = ROLE_PATTERNS
    if domain == "Customer Support":
        # in support data an "agent" is the person handling tickets, not a sales rep
        order = sorted(ROLE_PATTERNS, key=lambda r: 0 if r[0] in ("tickets", "csat", "resolution_time", "agent") else 1)
    for role, accepted, pattern in order:
        rx = re.compile(pattern, re.I)
        for p in profiles:
            col = p["name"]
            if col in used or kinds[col] not in accepted:
                continue
            if role in NO_PERCENT and p.get("percent"):
                continue
            if rx.search(name_words(col)):
                roles[role] = col
                used.add(col)
                break

    # first datetime column becomes the main date if nothing named matched
    if "date" not in roles:
        for p in profiles:
            if p["kind"] in ("datetime", "period") and p["name"] not in used:
                roles["date"] = p["name"]
                used.add(p["name"])
                break
    # HR: a status column whose values say "resigned / left" doubles as attrition
    if "attrition" not in roles and "status" in roles:
        prof = next(p for p in profiles if p["name"] == roles["status"])
        if any(EXIT_WORDS.search(s) for s in prof["samples"]):
            roles["attrition"] = roles["status"]
    return roles


def primary_measure(domain: str, roles: dict, profiles: list):
    for role in PRIMARY_MEASURE.get(domain, []):
        if role in roles:
            return roles[role]
    numeric = [p for p in profiles if p["kind"] == "numeric"]
    if not numeric:
        return None
    # the column with the biggest total is usually the headline number
    return max(numeric, key=lambda p: abs(p.get("sum", 0)))["name"]


def summarize(domain: str, profiles: list, rows: int) -> str:
    kinds = {}
    for p in profiles:
        kinds.setdefault(p["kind"], []).append(p["name"])
    parts = [f"{rows:,} rows and {len(profiles)} columns"]
    if kinds.get("numeric"):
        parts.append(f"measures such as {', '.join(kinds['numeric'][:3])}")
    if kinds.get("categorical"):
        parts.append(f"broken down by {', '.join(kinds['categorical'][:3])}")
    if kinds.get("datetime") or kinds.get("period"):
        parts.append(f"over {(kinds.get('datetime') or kinds.get('period'))[0]}")
    return f"{domain} data with " + ", ".join(parts) + "."
