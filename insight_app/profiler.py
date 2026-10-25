"""Pandas-only column typing: turns raw cells into numbers/dates and describes every column."""

import re

import pandas as pd

MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august",
               "september", "october", "november", "december"]
MONTH_LOOKUP = {m: i for i, m in enumerate(MONTH_NAMES, start=1)} | {m[:3]: i for i, m in enumerate(MONTH_NAMES, start=1)}
QUARTER = re.compile(r"^(q[1-4])(\s*[-' ]?\s*(\d{2}|\d{4}))?$", re.I)
NUMERIC_CELL = re.compile(r"^\s*\(?\s*[-+]?\s*[$₹€£¥]?\s*[-+]?[\d,]*\.?\d+\s*\)?\s*(%|[a-zA-Z]{1,6}\.?)?\s*$")
CURRENCY = re.compile(r"[$₹€£¥]")
ID_HINT = re.compile(r"(\bid\b|\b(no\.?|num|number|#)$|\bcode\b|#|\bsku\b|\bzip\b|\bpin\s*code\b|\bphone\b)", re.I)
DATE_HINT = re.compile(r"(date|time|day|month|year|period|dob|joined|created|updated|on$|_at$)", re.I)


def name_words(col) -> str:
    """'InvoiceNo' -> 'Invoice No', 'CustomerID' -> 'Customer ID', 'unit_price' -> 'unit price',
    so name rules work on CamelCase and snake_case headers."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(col))
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return re.sub(r"[_\s]+", " ", s).strip()
TRUTHY = {"yes", "y", "true", "1", "active", "left", "churned", "exited"}
BOOL_VALUES = {"yes", "no", "y", "n", "true", "false", "0", "1"}


def clean_types(df: pd.DataFrame) -> tuple:
    """Returns (typed_df, meta) where meta[col] has currency/percent/unit/period-order hints."""
    df = df.copy()
    meta = {}
    for col in df.columns:
        s = df[col]
        info = {}
        if s.dtype == object or pd.api.types.is_string_dtype(s):
            s = s.astype(object)
            s = s.map(lambda v: v.strip() if isinstance(v, str) else v).replace({"": None, "-": None, "NA": None, "N/A": None, "n/a": None})
            non_null = s.dropna()
            as_text = non_null.astype(str)
            words = name_words(col)
            if ID_HINT.search(words):
                # codes like 536527 / C536379 / 85123A stay text, so the C prefix (cancellation) survives
                s = s.map(lambda v: str(int(v)) if isinstance(v, float) and v.is_integer() else (str(v) if v is not None else None))
            elif len(non_null) and as_text.str.match(NUMERIC_CELL).mean() >= 0.9:
                info["currency"] = bool(as_text.str.contains(CURRENCY).mean() > 0.5)
                info["percent"] = bool(as_text.str.contains("%").mean() > 0.5)
                units = as_text.str.extract(r"([a-zA-Z]{1,6})\.?\s*$")[0].dropna()
                if len(units) and units.str.lower().nunique() == 1:
                    info["unit"] = units.iloc[0]
                negative = as_text.str.match(r"^\s*\(.*\)\s*$")
                nums = pd.to_numeric(as_text.str.replace(r"[^\d.\-+]", "", regex=True), errors="coerce")
                nums[negative] = -nums[negative].abs()
                s = nums.reindex(s.index)
            elif len(non_null) and _month_order(as_text) is not None:
                info["period_order"] = _month_order(as_text)
            elif len(non_null) and as_text.str.match(QUARTER).mean() >= 0.9:
                info["period_order"] = sorted(as_text.unique(), key=_quarter_key)
            elif len(non_null) and (DATE_HINT.search(words) or as_text.str.match(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}").mean() > 0.8):
                parsed = pd.to_datetime(s, errors="coerce", format="mixed", dayfirst=False)
                if parsed.notna().sum() >= 0.9 * len(non_null):
                    s = parsed
        elif pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s) and ID_HINT.search(name_words(col)) \
                and s.dropna().apply(lambda v: float(v).is_integer()).all():
            # numeric codes (customer id, invoice no) are labels, never summed
            s = s.map(lambda v: str(int(v)) if pd.notna(v) else None).astype(object)
        elif pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            # 0..1 fractions in a column whose name says percent read better as %
            if re.search(r"(%|percent|rate|ratio|share|csat|margin)", name_words(col), re.I) and s.dropna().between(0, 1).all():
                info["percent_fraction"] = True
            if re.search(r"(revenue|sales|amount|price|cost|salary|ctc|profit|spend|budget|value|income|expense|pay)", name_words(col), re.I) \
                    and not ID_HINT.search(name_words(col)):
                info["currency"] = True
        df[col] = s
        meta[col] = info
    return df, meta


def _month_order(values: pd.Series):
    lowered = values.str.lower().str.strip()
    if lowered.isin(MONTH_LOOKUP.keys()).mean() >= 0.9:
        return sorted(values.unique(), key=lambda v: MONTH_LOOKUP.get(str(v).lower().strip(), 99))
    return None


def _quarter_key(v: str):
    m = QUARTER.match(str(v).strip())
    year = int(m.group(3)) if m and m.group(3) else 0
    return (year, str(v).strip().upper()[:2])


def profile(df: pd.DataFrame, meta: dict) -> list:
    """One dict per column: name, kind, stats, samples. kind is one of
    numeric, datetime, period, boolean, categorical, identifier, text."""
    rows = len(df)
    out = []
    for col in df.columns:
        s = df[col]
        non_null = s.dropna()
        nunique = int(non_null.nunique())
        info = {
            "name": col,
            "rows": rows,
            "nulls_pct": round(100 * (1 - len(non_null) / rows), 1) if rows else 0.0,
            "nunique": nunique,
            "currency": bool(meta.get(col, {}).get("currency")),
            "percent": bool(meta.get(col, {}).get("percent") or meta.get(col, {}).get("percent_fraction")),
            "percent_fraction": bool(meta.get(col, {}).get("percent_fraction")),
            "unit": meta.get(col, {}).get("unit"),
            "period_order": meta.get(col, {}).get("period_order"),
        }
        lowered = set(non_null.astype(str).str.lower().str.strip().unique()) if nunique <= 2 else set()

        if pd.api.types.is_datetime64_any_dtype(s):
            kind = "datetime"
            info["min"], info["max"] = (str(non_null.min().date()), str(non_null.max().date())) if len(non_null) else ("", "")
        elif info["period_order"]:
            kind = "period"
        elif pd.api.types.is_bool_dtype(s) or (lowered and lowered <= BOOL_VALUES):
            kind = "boolean"
        elif pd.api.types.is_numeric_dtype(s):
            integer_like = len(non_null) and (non_null % 1 == 0).all()
            if re.fullmatch(r"(fy\s*)?year", str(col).strip(), re.I) and integer_like and non_null.between(1900, 2100).all():
                kind = "period"
                info["period_order"] = sorted(non_null.unique().tolist())
            else:
                kind = "numeric"
                if len(non_null):
                    info.update(min=float(non_null.min()), max=float(non_null.max()),
                                mean=float(non_null.mean()), sum=float(non_null.sum()))
        else:
            avg_len = non_null.astype(str).str.len().mean() if len(non_null) else 0
            if avg_len > 60:
                kind = "text"
            elif ID_HINT.search(name_words(col)) or (nunique > 0.9 * len(non_null) and len(non_null) > 15):
                kind = "identifier"
            else:
                kind = "categorical"
        info["kind"] = kind
        samples = non_null.astype(str).value_counts().head(5).index.tolist() if kind != "datetime" else non_null.astype(str).head(3).tolist()
        info["samples"] = [x[:40] for x in samples]
        out.append(info)
    return out


def schema_for_ai(df: pd.DataFrame, profiles: list, max_rows: int = 5) -> dict:
    cols = []
    for p in profiles:
        entry = {"name": p["name"], "kind": p["kind"], "unique": p["nunique"], "nulls_pct": p["nulls_pct"], "samples": p["samples"]}
        for k in ("min", "max", "mean"):
            if k in p:
                entry[k] = round(p[k], 3) if isinstance(p[k], float) else p[k]
        for k in ("currency", "percent", "unit"):
            if p.get(k):
                entry[k] = p[k]
        cols.append(entry)
    sample = df.head(max_rows).astype(str).map(lambda v: v[:40]).to_dict(orient="records")
    return {"rows": len(df), "columns": cols, "sample_rows": sample}


def is_truthy(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0) > 0
    return series.astype(str).str.lower().str.strip().isin(TRUTHY)
