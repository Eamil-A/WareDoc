"""
WareDoc Step 1-3: Data Generation
=================================

Reads the Open Beauty Facts CSV, filters for soap / hand wash / body wash /
shampoo / face wash products with complete fields, adds synthetic warehouse
columns, and writes:
  - data/products.csv
  - data/inventory.csv
  - data/policy.yaml
  - data/quarterly_sales.json   (used later by PDF + eval generation)

All synthetic values use a fixed random seed (42) for reproducibility.
"""
import gzip
import csv
import re
import json
import os
import random
import math

import yaml

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CSV_PATH = "en.openbeautyfacts.org.products.csv.gz"
DATA_DIR = "data"
SEED = 42
TARGET_PRODUCTS = 100

random.seed(SEED)

# Category keyword mapping.  Each entry maps a list of keywords (checked
# against categories_en and product_name) to a primary category label.
CATEGORY_RULES = [
    # order matters: most specific first
    ("hand wash",  ["hand wash", "handwash", "hand-wash", "liquid soap"]),
    ("face wash",  ["face wash", "facewash", "face-wash", "face cleanser",
                    "cleansing water", "micellar water", "cleanser"]),
    ("body wash",  ["body wash", "bodywash", "body-wash", "shower gel",
                    "shower gel", "bath", "douche"]),
    ("shampoo",    ["shampoo", "shampoing", "shampo"]),
    ("soap",       ["soap", "savon", "bar soap", "marseille soap",
                    "shaving soap"]),
]

SUPPLIERS = ["Distributor A", "Brand B", "Distributor C"]
WAREHOUSE_AISLES = ["A", "B", "C", "D", "E", "F"]
WAREHOUSE_RACKS = list(range(1, 21))  # 1-20
WAREHOUSE_LEVELS = [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_quantity(raw):
    """
    Extract numeric value + unit (g or ml) from a messy quantity string.

    Handles formats such as:
      '250 ml', '250ml', '800ML', '100 g', '100g',
      '1.7 kg', '10*106 g', '250 g, 9 oz'
    Returns (value, unit) or (None, None) if unparseable.
    """
    if not raw:
        return None, None
    text = raw.strip().lower()
    # remove anything in parentheses
    text = re.sub(r"\(.*?\)", "", text)
    # remove trailing junk after comma/semicolon
    text = text.split(",")[0].split(";")[0].strip()

    # Try kg → convert to g
    m = re.search(r"([\d.]+)\s*\*\s*([\d.]+)\s*(kg|g)", text)
    if m:
        v = float(m.group(1)) * float(m.group(2))
        if m.group(3) == "kg":
            v *= 1000
        return int(round(v)), "g"

    m = re.search(r"([\d.]+)\s*(kg)", text)
    if m:
        return int(round(float(m.group(1)) * 1000)), "g"

    m = re.search(r"([\d.]+)\s*(l)\b", text)
    if m and "ml" not in text:
        return int(round(float(m.group(1)) * 1000)), "ml"

    m = re.search(r"([\d.]+)\s*(ml|milliliter)", text)
    if m:
        return int(round(float(m.group(1)))), "ml"

    m = re.search(r"([\d.]+)\s*(g|gram|gr)\b", text)
    if m:
        return int(round(float(m.group(1)))), "g"

    return None, None


def assign_category(cat_en, product_name):
    """Return the primary product category or None."""
    combined = f"{cat_en} | {product_name}".lower()
    for label, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in combined:
                return label
    return None


def is_bar_soap(category, cat_en, product_name):
    """Determine if a soap product is a bar (solid) soap."""
    combined = f"{cat_en} | {product_name}".lower()
    if category != "soap":
        return False
    # Bar soaps: contains 'bar soap', 'bar', 'marseille', 'savon de', 'shaving soap'
    # but NOT 'liquid soap', 'liquid'
    bar_keywords = ["bar soap", "bar", "marseille", "savon de", "shaving soap",
                    "soap bar", "savo", "hard soap"]
    liquid_keywords = ["liquid soap", "liquid", "gel", "savon liquide",
                       "liquef", "handwash", "hand wash"]
    if any(kw in combined for kw in liquid_keywords):
        return False
    if any(kw in combined for kw in bar_keywords):
        return True
    # Default: soaps that don't say liquid are probably bar soaps
    return True


# ---------------------------------------------------------------------------
# Step 1: Filter + categorise
# ---------------------------------------------------------------------------
def filter_products():
    """Read the OBF CSV and return a list of qualifying product dicts."""
    seen = set()  # (product_name_lower, brand_lower) for dedup
    products = []
    with gzip.open(CSV_PATH, "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            name = (row.get("product_name") or "").strip()
            brand = (row.get("brands") or "").strip()
            qty_raw = (row.get("quantity") or "").strip()
            ingredients = (row.get("ingredients_text") or "").strip()
            cat_en = row.get("categories_en") or row.get("categories") or ""

            # Required fields
            if not name or not brand or not qty_raw or not ingredients:
                continue

            # Quantity must be in g or ml
            qty_val, qty_unit = parse_quantity(qty_raw)
            if qty_val is None or qty_unit not in ("g", "ml"):
                continue
            # Reasonable range: 20 g/ml to 5000 g/ml
            if qty_val < 20 or qty_val > 5000:
                continue

            # Must match one of our target categories
            category = assign_category(cat_en, name)
            if category is None:
                continue

            # Dedup by name+brand
            key = (name.lower().strip(), brand.lower().split(",")[0].strip())
            if key in seen:
                continue
            seen.add(key)

            bar = is_bar_soap(category, cat_en, name)

            products.append({
                "code": row.get("code", ""),
                "product_name": name,
                "brands": brand.split(",")[0].strip(),
                "categories_en": cat_en[:200],
                "quantity_text": qty_raw,
                "quantity_value": qty_val,
                "quantity_unit": qty_unit,
                "ingredients_text": ingredients,
                "image_url": row.get("image_url") or "",
                "category": category,
                "is_bar_soap": bar,
            })

    return products


def select_balanced(products, n=100):
    """Select ~n products with a roughly balanced mix of categories."""
    by_cat = {}
    for p in products:
        by_cat.setdefault(p["category"], []).append(p)

    # Shuffle each category list for variety
    for cat in by_cat:
        random.shuffle(by_cat[cat])

    # Target distribution (rough):
    #   soap 25, shampoo 25, body wash 20, face wash 15, hand wash 15
    targets = {
        "soap": 25,
        "shampoo": 25,
        "body wash": 20,
        "face wash": 15,
        "hand wash": 15,
    }

    selected = []
    for cat, target in targets.items():
        pool = by_cat.get(cat, [])
        take = min(target, len(pool))
        selected.extend(pool[:take])

    # If we're short, fill from remaining
    if len(selected) < n:
        used_codes = {p["code"] for p in selected}
        for p in products:
            if p["code"] not in used_codes:
                selected.append(p)
                if len(selected) >= n:
                    break

    # If we're over, trim
    selected = selected[:n]
    return selected


# ---------------------------------------------------------------------------
# Step 2: Synthetic columns
# ---------------------------------------------------------------------------
def add_synthetic_columns(products):
    """Add sku, pack_size, box_price_inr, supplier, tfm (bar soaps only)."""
    for i, p in enumerate(products):
        p["sku"] = f"SKU{1000 + i}"
        # pack_size: 6, 12, 24, 48 units per box (common wholesale sizes)
        p["pack_size"] = random.choice([6, 12, 12, 24, 24, 48])
        # box_price_inr: depends on quantity and category, 180-2200 INR
        base = p["quantity_value"] * (0.8 if p["quantity_unit"] == "ml" else 1.2)
        category_factor = {
            "soap": 0.6, "hand wash": 0.9, "body wash": 1.0,
            "shampoo": 1.1, "face wash": 1.3,
        }.get(p["category"], 1.0)
        raw_price = base * category_factor * random.uniform(2.5, 5.0)
        # round to nearest 5
        p["box_price_inr"] = int(math.ceil(raw_price / 5) * 5)
        p["supplier"] = random.choice(SUPPLIERS)
        # tfm only for bar soaps, 68-80
        if p["is_bar_soap"]:
            p["tfm"] = random.randint(68, 80)
        else:
            p["tfm"] = ""
    return products


# ---------------------------------------------------------------------------
# Step 3: Inventory
# ---------------------------------------------------------------------------
def generate_inventory(products):
    """Create inventory rows; ~12 % of items have zero stock_boxes."""
    rows = []
    n_zero = max(1, round(len(products) * 0.12))
    zero_skus = set(random.sample([p["sku"] for p in products], n_zero))
    for p in products:
        if p["sku"] in zero_skus:
            stock = 0
        else:
            stock = random.randint(5, 200)
        aisle = random.choice(WAREHOUSE_AISLES)
        rack = random.choice(WAREHOUSE_RACKS)
        level = random.choice(WAREHOUSE_LEVELS)
        rows.append({
            "sku": p["sku"],
            "stock_boxes": stock,
            "location": f"{aisle}-{rack:02d}-L{level}",
        })
    return rows


# ---------------------------------------------------------------------------
# Step 4: Policy YAML
# ---------------------------------------------------------------------------
def generate_policy():
    """Return policy dict to be written as YAML."""
    return {
        "gst_percentage": 18,   # placeholder – user will verify
        "gst_note": "PLACEHOLDER – verify against actual GST rates",
        "volume_discount_tiers": [
            {"min_boxes": 10,  "discount_percent": 2},
            {"min_boxes": 50,  "discount_percent": 5},
            {"min_boxes": 100, "discount_percent": 8},
        ],
        "customers": [
            {"name": "Sharma General Store",   "credit_limit_inr": 500000},
            {"name": "Metro Retail Chain",     "credit_limit_inr": 2000000},
            {"name": "Kiran Super Mart",       "credit_limit_inr": 800000},
        ],
        "return_rules": [
            "Damaged goods may be returned within 7 days of delivery with photographic proof.",
            "Wrong items may be returned within 14 days for full credit.",
            "Non-saleable returned goods are subject to a 5% restocking fee.",
            "Clearance items are non-returnable.",
        ],
        "delivery_terms": [
            "Orders are dispatched within 48 hours of confirmation.",
            "Standard delivery: 3-5 business days within state, 5-7 days interstate.",
            "Express delivery available at additional cost (Rs 500 per box).",
            "Free delivery on orders above 50 boxes.",
            "Delivery charges for orders below 10 boxes: Rs 250 flat.",
        ],
    }


# ---------------------------------------------------------------------------
# Step 5: Quarterly sales (synthetic, for quarterly_report.pdf)
# ---------------------------------------------------------------------------
def generate_quarterly_sales():
    """
    Synthetic quarterly sales by category (in boxes sold).
    Narrative reasons baked in:
      1. Q2 dip  – Distributor B supplier delay
      2. Q3-Q4 surge – Festive season (Diwali)
      3. Q4 bump – 8-12 % price increase on bar soaps
    """
    # Base figures (boxes) – Q1 is the baseline
    data = {
        "categories": ["Soap", "Hand Wash", "Body Wash", "Shampoo", "Face Wash"],
        "quarters": ["Q1", "Q2", "Q3", "Q4"],
        # boxes sold per category per quarter
        "sales": {
            "Soap":      {"Q1": 12000, "Q2":  9500, "Q3": 14000, "Q4": 18500},
            "Hand Wash": {"Q1":  8000, "Q2":  7200, "Q3":  9500, "Q4": 11500},
            "Body Wash": {"Q1": 10000, "Q2":  8500, "Q3": 12000, "Q4": 15200},
            "Shampoo":   {"Q1": 11000, "Q2":  9000, "Q3": 13500, "Q4": 16000},
            "Face Wash": {"Q1":  6000, "Q2":  5500, "Q3":  7000, "Q4":  8500},
        },
    }
    return data


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "docs"), exist_ok=True)

    print("[1/5] Filtering Open Beauty Facts CSV …")
    all_products = filter_products()
    print(f"      Qualifying rows: {len(all_products)}")

    print("[2/5] Selecting ~100 balanced products …")
    products = select_balanced(all_products, TARGET_PRODUCTS)
    print(f"      Selected: {len(products)}")
    cat_counts = {}
    for p in products:
        cat_counts[p["category"]] = cat_counts.get(p["category"], 0) + 1
    print(f"      Category distribution: {cat_counts}")
    bar_count = sum(1 for p in products if p["is_bar_soap"])
    print(f"      Bar soaps (with TFM): {bar_count}")

    print("[3/5] Adding synthetic columns …")
    products = add_synthetic_columns(products)

    # Write products.csv
    pcols = ["sku", "product_name", "brands", "category", "is_bar_soap",
             "quantity_text", "quantity_value", "quantity_unit",
             "pack_size", "box_price_inr", "supplier", "tfm",
             "ingredients_text", "image_url", "categories_en", "code"]
    with open(os.path.join(DATA_DIR, "products.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pcols)
        w.writeheader()
        for p in products:
            row = {c: p.get(c, "") for c in pcols}
            row["is_bar_soap"] = str(p["is_bar_soap"])
            w.writerow(row)
    print(f"      Wrote data/products.csv ({len(products)} rows)")

    print("[4/5] Generating inventory …")
    inv = generate_inventory(products)
    with open(os.path.join(DATA_DIR, "inventory.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["sku", "stock_boxes", "location"])
        w.writeheader()
        w.writerows(inv)
    n_zero = sum(1 for r in inv if r["stock_boxes"] == 0)
    print(f"      Wrote data/inventory.csv ({len(inv)} rows, {n_zero} at zero stock)")

    print("[5/5] Generating policy + quarterly sales …")
    policy = generate_policy()
    with open(os.path.join(DATA_DIR, "policy.yaml"), "w", encoding="utf-8") as f:
        yaml.dump(policy, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print("      Wrote data/policy.yaml")

    qdata = generate_quarterly_sales()
    with open(os.path.join(DATA_DIR, "quarterly_sales.json"), "w", encoding="utf-8") as f:
        json.dump(qdata, f, indent=2)
    print("      Wrote data/quarterly_sales.json")

    print("\nDone!  Data files created in data/")


if __name__ == "__main__":
    main()
