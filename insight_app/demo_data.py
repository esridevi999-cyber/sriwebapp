"""Synthetic demo table so the app has something to show before anyone uploads a file.
Made-up order lines (no real customers): products, regions, reps, a few returns and postage lines."""

import io

import numpy as np
import pandas as pd

DEMO_NAME = "demo_sales.xlsx"

PRODUCTS = {"Laptop": 900, "Monitor": 220, "Keyboard": 45, "Mouse": 25, "Headset": 80, "Webcam": 60, "Dock": 150, "Tablet": 400}


def _skewed(n: int, rng) -> np.ndarray:
    w = 1 / np.arange(1, n + 1) ** 0.9
    rng.shuffle(w)
    return w / w.sum()


def demo_sales_bytes(rows: int = 1500, seed: int = 7) -> bytes:
    rng = np.random.default_rng(seed)
    dates = pd.Timestamp("2025-01-06") + pd.to_timedelta(rng.integers(0, 540, rows), unit="D") \
        + pd.to_timedelta(rng.integers(9, 19, rows), unit="h")
    product = rng.choice(list(PRODUCTS), rows, p=[.2, .15, .15, .15, .1, .1, .05, .1])
    qty = rng.integers(1, 12, rows)
    price = np.array([PRODUCTS[p] for p in product]) * rng.uniform(0.9, 1.1, rows).round(2)
    order = [f"SO-{10000 + i // 2}" for i in range(rows)]  # about two lines per order
    df = pd.DataFrame({
        "Order ID": order,
        "Order Date": dates.floor("min"),
        "Region": rng.choice(["North", "South", "East", "West"], rows, p=[.35, .25, .2, .2]),
        "Sales Rep": rng.choice(["Asha", "Ravi", "Meera", "John", "Li", "Sara"], rows),
        "Channel": rng.choice(["Online", "Retail", "Partner"], rows),
        # a few big accounts and a long tail, like real customer bases
        "Customer": rng.choice([f"Customer {i:03d}" for i in range(500)], rows, p=_skewed(500, rng)),
        "Product": product,
        "Category": np.where(np.isin(product, ["Laptop", "Tablet", "Monitor"]), "Hardware", "Accessories"),
        "Quantity": qty,
        "Unit Price": price.round(2),
    })
    # a few cancelled lines (negative qty, C-prefixed order) and postage lines
    returns = df.sample(40, random_state=seed).assign(Quantity=lambda d: -d["Quantity"].clip(upper=3),
                                                      **{"Order ID": lambda d: "C" + d["Order ID"]})
    postage = df.drop_duplicates("Order ID").sample(200, random_state=seed).assign(
        Product="POSTAGE", Category="Shipping", Quantity=1, **{"Unit Price": 12.0})
    df = pd.concat([df, returns, postage]).sort_values("Order Date").reset_index(drop=True)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()
