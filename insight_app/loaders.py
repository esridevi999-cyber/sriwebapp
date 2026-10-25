"""Read Excel / CSV / Word / PDF uploads into clean pandas tables.

One sheet can hold several tables (title rows, blank-row gaps, notes underneath). Every sheet or
document is split into blocks on fully empty rows, each block gets its real header row detected,
and note lines and total rows are dropped so they never double-count.
"""

import io
import re

import pandas as pd

TOTAL_ROW = re.compile(r"^\s*(grand\s+)?(total|sum|overall|subtotal)\b|\btotal\s*/\s*avg\b|^\s*q\d\s+total", re.I)


def load_file(name: str, data: bytes) -> dict:
    """Returns {table_label: DataFrame}, largest table first. Raises ValueError if nothing usable."""
    ext = name.lower().rsplit(".", 1)[-1]
    if ext == "csv":
        raws = {"CSV": _read_csv_raw(data)}
    elif ext in ("xlsx", "xlsm", "xls"):
        raws = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None)
    elif ext == "docx":
        raws = _read_docx_raw(data)
    elif ext == "pdf":
        raws = _read_pdf_raw(data)
    else:
        raise ValueError(f"Unsupported file type: .{ext}")

    tables = {}
    for label, raw in raws.items():
        blocks = _split_blocks(raw)
        for i, block in enumerate(blocks, start=1):
            df = _apply_header(block)
            if df is None:
                continue
            key = label if len(blocks) == 1 else f"{label} - table {i} ({df.columns[0]})"
            tables[key] = df

    if not tables:
        raise ValueError("No table with a header row and at least two data rows was found in this file.")
    return dict(sorted(tables.items(), key=lambda kv: kv[1].size, reverse=True))


def _read_csv_raw(data: bytes) -> pd.DataFrame:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(data), header=None, sep=None, engine="python", encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("Couldn't decode this CSV file.")


def _read_docx_raw(data: bytes) -> dict:
    from docx import Document

    doc = Document(io.BytesIO(data))
    raws = {}
    for i, table in enumerate(doc.tables, start=1):
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            # merged cells repeat their text in every grid slot; keep it once
            if len(cells) > 1 and len(set(cells)) == 1:
                cells = [cells[0]] + [""] * (len(cells) - 1)
            rows.append(cells)
        if rows:
            raws[f"Word table {i}"] = pd.DataFrame(rows).replace("", None)
    if not raws:
        raise ValueError("This Word document has no tables. Add the data as a table and upload again.")
    return raws


def _read_pdf_raw(data: bytes) -> dict:
    import pdfplumber

    raws, current, width = {}, [], None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                rows = [[(c or "").strip() for c in row] for row in table if row]
                if not rows:
                    continue
                # a table that continues on the next page keeps its width; repeated headers are dropped
                if current and len(rows[0]) == width:
                    if rows[0] == current[0]:
                        rows = rows[1:]
                    current.extend(rows)
                else:
                    if current:
                        raws[f"PDF table {len(raws) + 1}"] = pd.DataFrame(current).replace("", None)
                    current, width = rows, len(rows[0])
    if current:
        raws[f"PDF table {len(raws) + 1}"] = pd.DataFrame(current).replace("", None)
    if not raws:
        raise ValueError("No tables found in this PDF. Scanned PDFs need OCR first.")
    return raws


def _split_blocks(raw: pd.DataFrame) -> list:
    raw = raw.dropna(axis=1, how="all")
    if raw.empty:
        return []
    empty = raw.isna().all(axis=1).to_numpy()
    blocks, start = [], None
    for i, is_empty in enumerate(empty):
        if not is_empty and start is None:
            start = i
        elif is_empty and start is not None:
            blocks.append(raw.iloc[start:i])
            start = None
    if start is not None:
        blocks.append(raw.iloc[start:])
    return [b.dropna(axis=1, how="all") for b in blocks]


def _is_label(value) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    if isinstance(value, (int, float, pd.Timestamp)):
        return False
    text = str(value).strip()
    return bool(text) and not re.fullmatch(r"[-+$₹€£%.,\d\s]+", text)


def _apply_header(block: pd.DataFrame):
    width = block.shape[1]
    if width < 2:
        return None
    header_idx = None
    for i in range(min(len(block), 15)):
        row = block.iloc[i].tolist()
        filled = sum(v is not None and not (isinstance(v, float) and pd.isna(v)) for v in row)
        labels = sum(_is_label(v) for v in row)
        if filled >= max(2, 0.6 * width) and labels >= 0.6 * filled:
            header_idx = i
            break
    if header_idx is None:
        return None

    names, seen = [], {}
    for j, v in enumerate(block.iloc[header_idx].tolist()):
        name = str(v).strip() if _is_label(v) or (v is not None and not pd.isna(v)) else f"Column {j + 1}"
        name = re.sub(r"\s+", " ", name)
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name} ({seen[name]})")

    df = block.iloc[header_idx + 1 :].copy()
    df.columns = names
    df = df.dropna(axis=1, how="all")
    if df.shape[1] < 2:
        return None
    # note lines: a single filled cell in a wide table
    if df.shape[1] >= 3:
        df = df[df.notna().sum(axis=1) > 1]
    first = df.iloc[:, 0].astype(str)
    df = df[~first.str.match(TOTAL_ROW)]
    df = df.reset_index(drop=True)
    if len(df) < 2:
        return None
    return df.infer_objects()
