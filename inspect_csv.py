"""Inspect the Open Beauty Facts CSV structure using only the standard library."""
import gzip
import csv

CSV_PATH = "en.openbeautyfacts.org.products.csv.gz"

with gzip.open(CSV_PATH, "rt", encoding="utf-8", errors="replace") as f:
    reader = csv.reader(f, delimiter="\t")
    header = next(reader)
    print(f"Total columns: {len(header)}")
    print("\n=== ALL COLUMN NAMES ===")
    for i, col in enumerate(header):
        print(f"  [{i:3d}] {col}")

    # Read first 5 rows to see data format
    print("\n=== FIRST 3 ROWS (selected columns) ===")
    rows = []
    for i, row in enumerate(reader):
        rows.append(row)
        if i >= 4:
            break

    # Show a few interesting columns
    interesting_keywords = ["name", "brand", "quantity", "categor", "ingredient", "image", "url", "code"]
    interesting_indices = []
    for idx, col in enumerate(header):
        if any(kw in col.lower() for kw in interesting_keywords):
            interesting_indices.append((idx, col))

    print("\nInteresting columns found:")
    for idx, col in interesting_indices:
        print(f"  [{idx:3d}] {col}")

    print("\nSample values from first 3 rows:")
    for idx, col in interesting_indices:
        print(f"\n  Column [{idx}] '{col}':")
        for ri, row in enumerate(rows):
            val = row[idx] if idx < len(row) else "<MISSING>"
            if len(val) > 120:
                val = val[:120] + "..."
            print(f"    Row {ri}: {repr(val)}")
