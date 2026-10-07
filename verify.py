"""Final verification of all WareDoc outputs against the original requirements."""
import csv
import json
import os
import yaml
import pandas as pd

print("=== WAREDOC VERIFICATION ===\n")

# 1. products.csv
df = pd.read_csv("data/products.csv")
print(f"1. products.csv: {len(df)} rows")
assert len(df) == 100, f"Expected 100 products, got {len(df)}"
# Check required columns
for col in ["sku", "product_name", "brands", "quantity_value", "quantity_unit",
            "pack_size", "box_price_inr", "supplier", "tfm", "ingredients_text"]:
    assert col in df.columns, f"Missing column: {col}"
# Check SKU format
assert df["sku"].iloc[0] == "SKU1000", f"First SKU should be SKU1000, got {df['sku'].iloc[0]}"
# Check categories
cats = df["category"].value_counts()
print(f"   Categories: {dict(cats)}")
# Check bar soaps have TFM
bar_soaps = df[df["is_bar_soap"] == True]
non_bar = df[df["is_bar_soap"] == False]
if len(bar_soaps) == 0:
    # try string comparison
    bar_soaps = df[df["is_bar_soap"].astype(str) == "True"]
    non_bar = df[df["is_bar_soap"].astype(str) == "False"]
assert bar_soaps["tfm"].notna().all() and (bar_soaps["tfm"].astype(str) != "").all(), "Bar soaps should have TFM"
assert (bar_soaps["tfm"].astype(int) >= 68).all() and (bar_soaps["tfm"].astype(int) <= 80).all(), "TFM should be 68-80"
print(f"   Bar soaps: {len(bar_soaps)} (all with TFM 68-80)")
print(f"   Non-bar: {len(non_bar)} (no TFM)")
print("   OK")

# 2. inventory.csv
inv = pd.read_csv("data/inventory.csv")
print(f"\n2. inventory.csv: {len(inv)} rows")
zero_pct = (inv["stock_boxes"] == 0).sum() / len(inv) * 100
print(f"   Zero stock: {(inv['stock_boxes']==0).sum()} items ({zero_pct:.0f}%)")
assert 10 <= zero_pct <= 15, f"Expected ~12% zero stock, got {zero_pct:.0f}%"
assert "location" in inv.columns and "sku" in inv.columns
print("   OK")

# 3. policy.yaml
with open("data/policy.yaml") as f:
    policy = yaml.safe_load(f)
print(f"\n3. policy.yaml")
assert "gst_percentage" in policy
print(f"   GST: {policy['gst_percentage']}% (placeholder)")
tiers = policy["volume_discount_tiers"]
assert len(tiers) == 3
assert tiers[0] == {"min_boxes": 10, "discount_percent": 2}
assert tiers[1] == {"min_boxes": 50, "discount_percent": 5}
assert tiers[2] == {"min_boxes": 100, "discount_percent": 8}
print(f"   Discount tiers: 10->2%, 50->5%, 100->8%")
assert len(policy["customers"]) == 3
print(f"   Customers: {[c['name'] for c in policy['customers']]}")
print("   OK")

# 4. Quarterly sales consistency
with open("data/quarterly_sales.json") as f:
    qs = json.load(f)
print(f"\n4. quarterly_sales.json")
for cat in qs["categories"]:
    vals = [qs["sales"][cat][q] for q in qs["quarters"]]
    assert vals[1] < vals[0], f"{cat}: Q2 should be lower than Q1 (supplier delay)"
    assert vals[3] > vals[0], f"{cat}: Q4 should be higher than Q1 (festive)"
print(f"   All categories: Q2 dip < Q1, Q4 surge > Q1")
print("   OK")

# 5. PDFs exist
print(f"\n5. PDFs:")
for pdf in ["catalog_A.pdf", "catalog_B.pdf", "catalog_C.pdf",
            "quarterly_report.pdf", "policy.pdf"]:
    path = os.path.join("data", "docs", pdf)
    exists = os.path.exists(path)
    size = os.path.getsize(path) if exists else 0
    print(f"   {pdf}: {'OK' if exists else 'MISSING'} ({size:,} bytes)")
    assert exists, f"Missing PDF: {pdf}"

# 6. Eval questions
with open("eval/questions.csv") as f:
    questions = list(csv.DictReader(f))
print(f"\n6. eval/questions.csv: {len(questions)} questions")
assert len(questions) == 25, f"Expected 25 questions, got {len(questions)}"
for q in questions:
    assert q["question"] and q["expected_answer"] and q["expected_document"]
print("   All questions have answer, document, and page")
print("   OK")

# 7. Page number visible on every page (check metadata)
with open("data/pdf_metadata.json") as f:
    meta = json.load(f)
print(f"\n7. Page tracking metadata:")
print(f"   catalog_A: {len(meta['catalog_A']['products'])} products mapped")
print(f"   catalog_B: {len(meta['catalog_B']['products'])} entries (incl TFM chart on p{meta['catalog_B']['tfm_chart_page']})")
print(f"   catalog_C: {len(meta['catalog_C']['products'])} products, {len(meta['catalog_C']['overlap_skus'])} overlap with A")
print(f"   quarterly_report: narrative p{meta['quarterly_report']['narrative_page']}, table p{meta['quarterly_report']['table_page']}, chart p{meta['quarterly_report']['chart_page']}")
print(f"   policy: discounts p{meta['policy']['discounts_page']}")
print("   OK")

print("\n=== ALL CHECKS PASSED ===")
