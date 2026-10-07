"""Explore categories in the CSV to find soap/hand wash/body wash/shampoo/face wash products."""
import gzip
import csv
from collections import Counter

CSV_PATH = "en.openbeautyfacts.org.products.csv.gz"

keywords = ["soap", "hand wash", "handwash", "body wash", "bodywash",
            "shampoo", "face wash", "facewash", "face cleanser", "cleanser"]

cat_counter = Counter()
name_counter = Counter()
qty_counter = Counter()
matching_rows = []
total_rows = 0

with gzip.open(CSV_PATH, "rt", encoding="utf-8", errors="replace") as f:
    reader = csv.DictReader(f, delimiter="\t")
    for row in reader:
        total_rows += 1
        cat = row.get("categories_en", "") or ""
        cat_lower = cat.lower()
        name = row.get("product_name", "") or ""
        name_lower = name.lower()
        combined = cat_lower + " | " + name_lower

        if any(kw in combined for kw in keywords):
            cat_counter[cat] += 1
            matching_rows.append(row)
            qty_counter[row.get("quantity", "")] += 1

print(f"Total rows scanned: {total_rows}")
print(f"Matching rows (by keyword in category or name): {len(matching_rows)}")
print(f"\n=== TOP 40 CATEGORIES among matching rows ===")
for cat, cnt in cat_counter.most_common(40):
    print(f"  {cnt:5d}  {cat[:100]}")

print(f"\n=== TOP 20 QUANTITY VALUES among matching rows ===")
for qty, cnt in qty_counter.most_common(20):
    print(f"  {cnt:5d}  {repr(qty)}")

# Now check how many have all required fields: product_name, brands, quantity, ingredients
print("\n=== FIELD COMPLETENESS among matching rows ===")
has_all = 0
for row in matching_rows:
    has_name = bool(row.get("product_name", "").strip())
    has_brand = bool(row.get("brands", "").strip())
    has_qty = bool(row.get("quantity", "").strip())
    has_ingredients = bool(row.get("ingredients_text", "").strip())
    if has_name and has_brand and has_qty and has_ingredients:
        has_all += 1
print(f"Rows with name+brand+quantity+ingredients: {has_all}")

# Show quantity format examples with g or ml
print("\n=== QUANTITY VALUES containing g or ml (sample) ===")
g_ml_count = 0
g_ml_examples = []
for row in matching_rows:
    qty = row.get("quantity", "").strip()
    if qty and ("g" in qty.lower() or "ml" in qty.lower()):
        g_ml_count += 1
        if len(g_ml_examples) < 30:
            g_ml_examples.append((row.get("product_name", "")[:50], qty))
print(f"Matching rows with g/ml in quantity: {g_ml_count}")
for name, qty in g_ml_examples[:30]:
    print(f"  {qty:20s}  {name}")
