"""
WareDoc Step 6: Eval Question Generation
=========================================

Writes eval/questions.csv with 25 questions, each having an exact expected
answer, expected document, and expected page number.

Question mix:
  4 text, 4 table, 4 chart-only, 3 text+table+chart,
  4 cross-document, 3 math, 2 scanned-page, 1 unanswerable

All answers are derived programmatically from the generated data so they
are exact and reproducible.
"""
import os
import csv
import json
import math
import yaml
import pandas as pd

DATA_DIR = "data"
EVAL_DIR = "eval"
META_PATH = os.path.join(DATA_DIR, "pdf_metadata.json")


def load_data():
    products = pd.read_csv(os.path.join(DATA_DIR, "products.csv"))
    with open(os.path.join(DATA_DIR, "policy.yaml")) as f:
        policy = yaml.safe_load(f)
    with open(os.path.join(DATA_DIR, "quarterly_sales.json")) as f:
        quarterly = json.load(f)
    with open(META_PATH) as f:
        meta = json.load(f)
    return products, policy, quarterly, meta


def fmt_inr(v):
    """Format a number as Indian Rupees."""
    return f"Rs {int(round(v)):,}"


def discount_rate(n_boxes, policy):
    """Return the best applicable discount percentage for n_boxes."""
    best = 0
    for tier in policy["volume_discount_tiers"]:
        if n_boxes >= tier["min_boxes"]:
            best = max(best, tier["discount_percent"])
    return best


def main():
    os.makedirs(EVAL_DIR, exist_ok=True)
    products, policy, quarterly, meta = load_data()
    qs = []

    # ---- Helper to find a product by SKU in a catalog's product_list ----
    def find_in_catalog(cat_key, sku):
        for p in meta[cat_key]["product_list"]:
            if p["sku"] == sku:
                return p
        return None

    # Helper: page lookup
    def page_of(cat_key, sku):
        return meta[cat_key]["products"].get(sku, 1)

    # Get specific products for questions
    # catalog A products (SKU1000..SKU1039)
    ca_products = meta["catalog_A"]["product_list"]
    # catalog B products (SKU1000..SKU1024)
    cb_products = meta["catalog_B"]["product_list"]
    # catalog C products
    cc_products = meta["catalog_C"]["product_list"]
    overlap_skus = meta["catalog_C"]["overlap_skus"]
    price_overrides = meta["catalog_C"]["price_overrides"]

    # Quarterly totals
    cats = quarterly["categories"]
    quarters = quarterly["quarters"]
    q_totals = {}
    for q in quarters:
        q_totals[q] = sum(quarterly["sales"][c][q] for c in cats)
    cat_totals = {}
    for c in cats:
        cat_totals[c] = sum(quarterly["sales"][c][q] for q in quarters)

    # ===================================================================
    # 4 TEXT QUESTIONS (answered from narrative / policy text)
    # ===================================================================

    # T1: Reason for Q2 dip
    qs.append({
        "question": "What reason is given for the Q2 sales dip in the quarterly report?",
        "expected_answer": ("Distributor B experienced logistics and customs delays "
                            "that reduced supply by approximately 20-25% for two key "
                            "product lines, causing a noticeable dip across all "
                            "categories in Q2."),
        "expected_document": "quarterly_report.pdf",
        "expected_page": str(meta["quarterly_report"]["narrative_page"]),
    })

    # T2: Return policy for damaged goods
    qs.append({
        "question": "What is the return policy for damaged goods?",
        "expected_answer": ("Damaged goods may be returned within 7 days of "
                            "delivery with photographic proof."),
        "expected_document": "policy.pdf",
        "expected_page": str(meta["policy"]["returns_page"]),
    })

    # T3: Standard delivery time within state
    qs.append({
        "question": "What is the standard delivery time for orders within state?",
        "expected_answer": "3-5 business days within state, 5-7 days interstate.",
        "expected_document": "policy.pdf",
        "expected_page": str(meta["policy"]["delivery_page"]),
    })

    # T4: Order dispatch time
    qs.append({
        "question": "Within how many hours are orders dispatched after confirmation?",
        "expected_answer": "Orders are dispatched within 48 hours of confirmation.",
        "expected_document": "policy.pdf",
        "expected_page": str(meta["policy"]["delivery_page"]),
    })

    # ===================================================================
    # 4 TABLE QUESTIONS (answered from tables in PDFs)
    # ===================================================================

    # TB1: Q1 sales for Soap
    qs.append({
        "question": "What were the Q1 sales volume (in boxes) for Soap?",
        "expected_answer": f"{quarterly['sales']['Soap']['Q1']:,} boxes",
        "expected_document": "quarterly_report.pdf",
        "expected_page": str(meta["quarterly_report"]["table_page"]),
    })

    # TB2: Box price of a product in Catalog A
    p_tb2 = ca_products[0]  # SKU1000
    qs.append({
        "question": (f"What is the box price of {p_tb2['product_name']} "
                     f"({p_tb2['sku']}) in Catalog A?"),
        "expected_answer": f"Rs {int(p_tb2['box_price_inr']):,}",
        "expected_document": "catalog_A.pdf",
        "expected_page": str(page_of("catalog_A", p_tb2["sku"])),
    })

    # TB3: Total annual sales for Shampoo
    qs.append({
        "question": "What is the total annual sales volume (in boxes) for Shampoo?",
        "expected_answer": f"{cat_totals['Shampoo']:,} boxes",
        "expected_document": "quarterly_report.pdf",
        "expected_page": str(meta["quarterly_report"]["table_page"]),
    })

    # TB4: Credit limit for Metro Retail Chain
    metro = [c for c in policy["customers"] if "Metro" in c["name"]][0]
    qs.append({
        "question": "What is the credit limit for Metro Retail Chain?",
        "expected_answer": f"Rs {metro['credit_limit_inr']:,}",
        "expected_document": "policy.pdf",
        "expected_page": str(meta["policy"]["customers_page"]),
    })

    # ===================================================================
    # 4 CHART-ONLY QUESTIONS (answered from charts in PDFs)
    # ===================================================================

    # C1: Which category had the highest Q4 sales?
    q4_max_cat = max(cats, key=lambda c: quarterly["sales"][c]["Q4"])
    q4_max_val = quarterly["sales"][q4_max_cat]["Q4"]
    qs.append({
        "question": "Which product category had the highest sales in Q4?",
        "expected_answer": f"{q4_max_cat} ({q4_max_val:,} boxes)",
        "expected_document": "quarterly_report.pdf",
        "expected_page": str(meta["quarterly_report"]["chart_page"]),
    })

    # C2: Which soap has the highest TFM?
    bar_soaps = [p for p in cb_products if p["tfm"] is not None]
    max_tfm_soap = max(bar_soaps, key=lambda p: p["tfm"])
    qs.append({
        "question": (f"Which bar soap has the highest TFM value in the "
                     f"TFM comparison chart?"),
        "expected_answer": (f"{max_tfm_soap['product_name']} "
                            f"(TFM {int(max_tfm_soap['tfm'])}%)"),
        "expected_document": "catalog_B.pdf",
        "expected_page": str(meta["catalog_B"]["tfm_chart_page"]),
    })

    # C3: Which quarter had lowest Hand Wash sales?
    hw_q = {q: quarterly["sales"]["Hand Wash"][q] for q in quarters}
    min_q = min(hw_q, key=hw_q.get)
    qs.append({
        "question": "Which quarter showed the lowest sales for Hand Wash?",
        "expected_answer": f"{min_q} ({hw_q[min_q]:,} boxes)",
        "expected_document": "quarterly_report.pdf",
        "expected_page": str(meta["quarterly_report"]["chart_page"]),
    })

    # C4: Biggest absolute increase Q1→Q4
    deltas = {c: quarterly["sales"][c]["Q4"] - quarterly["sales"][c]["Q1"]
              for c in cats}
    max_delta_cat = max(deltas, key=deltas.get)
    qs.append({
        "question": ("Which category showed the biggest absolute increase in "
                     "sales from Q1 to Q4?"),
        "expected_answer": (f"{max_delta_cat} (+{deltas[max_delta_cat]:,} boxes, "
                            f"from {quarterly['sales'][max_delta_cat]['Q1']:,} "
                            f"to {quarterly['sales'][max_delta_cat]['Q4']:,})"),
        "expected_document": "quarterly_report.pdf",
        "expected_page": str(meta["quarterly_report"]["chart_page"]),
    })

    # ===================================================================
    # 3 TEXT + TABLE + CHART QUESTIONS
    # ===================================================================

    # TTC1: The big comparison question
    q2_total = q_totals["Q2"]
    q4_total = q_totals["Q4"]
    diff = q4_total - q2_total
    pct = round(diff / q2_total * 100, 1)
    qs.append({
        "question": ("Compare Q2 and Q4 total sales across all categories, "
                     "give the three biggest reasons for the difference, and "
                     "show the proof from the report."),
        "expected_answer": (
            f"Q2 total: {q2_total:,} boxes; Q4 total: {q4_total:,} boxes; "
            f"Q4 is {diff:,} boxes higher ({pct}% increase). "
            f"Three reasons: (1) Festive season demand (Diwali and year-end "
            f"celebrations) drove Q3-Q4 surge; (2) Distributor B supplier delay "
            f"caused the Q2 dip; (3) Price increase of 8-12% on bar soaps in "
            f"Q4 boosted revenue. Proof: Table 1 (page "
            f"{meta['quarterly_report']['table_page']}) shows the Q2 dip and Q4 "
            f"surge; Figure 1 (page {meta['quarterly_report']['chart_page']}) "
            f"shows the same trend visually; the narrative (page "
            f"{meta['quarterly_report']['narrative_page']}) explains all three "
            f"reasons."
        ),
        "expected_document": "quarterly_report.pdf",
        "expected_page": (f"{meta['quarterly_report']['narrative_page']},"
                          f"{meta['quarterly_report']['table_page']},"
                          f"{meta['quarterly_report']['chart_page']}"),
    })

    # TTC2: Total Q4 sales and what drove Q3→Q4 increase
    q3_total = q_totals["Q3"]
    q3_q4_diff = q4_total - q3_total
    qs.append({
        "question": ("What is the total Q4 sales across all categories, and "
                     "what drove the increase from Q3 to Q4?"),
        "expected_answer": (
            f"Q4 total: {q4_total:,} boxes (up {q3_q4_diff:,} from Q3's "
            f"{q3_total:,}). The increase was driven by festive season demand "
            f"continuing into Q4 and the 8-12% price increase on bar soaps. "
            f"The table on page {meta['quarterly_report']['table_page']} and "
            f"chart on page {meta['quarterly_report']['chart_page']} confirm "
            f"the numbers, and the narrative on page "
            f"{meta['quarterly_report']['narrative_page']} explains the reasons."
        ),
        "expected_document": "quarterly_report.pdf",
        "expected_page": (f"{meta['quarterly_report']['narrative_page']},"
                          f"{meta['quarterly_report']['table_page']},"
                          f"{meta['quarterly_report']['chart_page']}"),
    })

    # TTC3: Largest % growth Q1→Q4 + reasons
    pct_growth = {c: round((quarterly["sales"][c]["Q4"] - quarterly["sales"][c]["Q1"])
                          / quarterly["sales"][c]["Q1"] * 100, 1)
                  for c in cats}
    max_pct_cat = max(pct_growth, key=pct_growth.get)
    qs.append({
        "question": ("Which category had the largest percentage growth from "
                     "Q1 to Q4, and what reasons explain this growth?"),
        "expected_answer": (
            f"{max_pct_cat} with {pct_growth[max_pct_cat]}% growth (from "
            f"{quarterly['sales'][max_pct_cat]['Q1']:,} to "
            f"{quarterly['sales'][max_pct_cat]['Q4']:,} boxes). Reasons: (1) "
            f"Festive season demand driving Q4 surge; (2) Q2 supplier delay "
            f"making the recovery more dramatic; (3) Price increase of 8-12% "
            f"on bar soaps boosting revenue. The table (page "
            f"{meta['quarterly_report']['table_page']}) shows the numbers, "
            f"the chart (page {meta['quarterly_report']['chart_page']}) shows "
            f"the trend, and the narrative (page "
            f"{meta['quarterly_report']['narrative_page']}) explains the reasons."
        ),
        "expected_document": "quarterly_report.pdf",
        "expected_page": (f"{meta['quarterly_report']['narrative_page']},"
                          f"{meta['quarterly_report']['table_page']},"
                          f"{meta['quarterly_report']['chart_page']}"),
    })

    # ===================================================================
    # 4 CROSS-DOCUMENT QUESTIONS
    # ===================================================================

    # XD1: Price difference for an overlap product between Catalog A and C
    overlap_sku = overlap_skus[0]
    p_a = find_in_catalog("catalog_A", overlap_sku)
    p_c = find_in_catalog("catalog_C", overlap_sku)
    price_a = int(p_a["box_price_inr"])
    price_c = int(p_c["box_price_inr"])
    diff_ac = price_c - price_a
    cheaper = "Catalog A" if price_a < price_c else "Catalog C"
    qs.append({
        "question": (f"For {p_a['product_name']} ({overlap_sku}), what is the "
                     f"price difference between Catalog A and Catalog C, and "
                     f"which catalog is cheaper?"),
        "expected_answer": (f"Catalog A: Rs {price_a:,}; Catalog C: Rs {price_c:,}; "
                            f"Difference: Rs {abs(diff_ac):,}. {cheaper} is cheaper."),
        "expected_document": f"catalog_A.pdf,catalog_C.pdf",
        "expected_page": (f"{page_of('catalog_A', overlap_sku)},"
                          f"{page_of('catalog_C', overlap_sku)}"),
    })

    # XD2: Cost for 55 boxes of a product after discount (A + policy)
    p_xd2 = ca_products[1]  # SKU1001
    price_xd2 = int(p_xd2["box_price_inr"])
    disc = discount_rate(55, policy)
    after_disc = price_xd2 * 55 * (1 - disc / 100)
    qs.append({
        "question": (f"What is the total cost for 55 boxes of "
                     f"{p_xd2['product_name']} ({p_xd2['sku']}) after applying "
                     f"the volume discount? (Price: Rs {price_xd2:,}/box from "
                     f"Catalog A; discount from policy)"),
        "expected_answer": (f"55 boxes qualifies for {disc}% discount. "
                            f"Total = Rs {price_xd2:,} x 55 x (1 - {disc/100}) "
                            f"= {fmt_inr(after_disc)}"),
        "expected_document": f"catalog_A.pdf,policy.pdf",
        "expected_page": (f"{page_of('catalog_A', p_xd2['sku'])},"
                          f"{meta['policy']['discounts_page']}"),
    })

    # XD3: Which supplier offers a product and at what price (A + B)
    p_xd3_a = find_in_catalog("catalog_A", "SKU1002")  # has supplier field
    p_xd3_b = find_in_catalog("catalog_B", "SKU1002")
    price_xd3 = int(p_xd3_a["box_price_inr"])
    qs.append({
        "question": (f"Who is the supplier for {p_xd3_b['product_name']} "
                     f"({p_xd3_a['sku']}) and what is its box price?"),
        "expected_answer": (f"Supplier: {p_xd3_a['supplier']}; Box price: "
                            f"Rs {price_xd3:,} (from Catalog A). "
                            f"Ingredients and spec details are in Catalog B."),
        "expected_document": f"catalog_A.pdf,catalog_B.pdf",
        "expected_page": (f"{page_of('catalog_A', p_xd3_a['sku'])},"
                          f"{page_of('catalog_B', p_xd3_a['sku'])}"),
    })

    # XD4: Which overlap product has the biggest price discrepancy between A and C
    max_disc_sku = None
    max_disc_val = 0
    for sku in overlap_skus:
        pa = find_in_catalog("catalog_A", sku)
        pc = find_in_catalog("catalog_C", sku)
        d = abs(int(pc["box_price_inr"]) - int(pa["box_price_inr"]))
        if d > max_disc_val:
            max_disc_val = d
            max_disc_sku = sku
    p_xd4_a = find_in_catalog("catalog_A", max_disc_sku)
    p_xd4_c = find_in_catalog("catalog_C", max_disc_sku)
    qs.append({
        "question": (f"Among the products appearing in both Catalog A and "
                     f"Catalog C, which has the largest price discrepancy, and "
                     f"what are the two prices?"),
        "expected_answer": (f"{p_xd4_a['product_name']} ({max_disc_sku}): "
                            f"Catalog A = Rs {int(p_xd4_a['box_price_inr']):,}, "
                            f"Catalog C = Rs {int(p_xd4_c['box_price_inr']):,}, "
                            f"difference = Rs {max_disc_val:,}."),
        "expected_document": f"catalog_A.pdf,catalog_C.pdf",
        "expected_page": (f"{page_of('catalog_A', max_disc_sku)},"
                          f"{page_of('catalog_C', max_disc_sku)}"),
    })

    # ===================================================================
    # 3 MATH QUESTIONS
    # ===================================================================

    # M1: Total cost for 120 boxes including GST after discount
    p_m1 = ca_products[0]  # SKU1000
    price_m1 = int(p_m1["box_price_inr"])
    disc_m1 = discount_rate(120, policy)
    gst = policy["gst_percentage"]
    after_disc_m1 = price_m1 * 120 * (1 - disc_m1 / 100)
    with_gst_m1 = after_disc_m1 * (1 + gst / 100)
    qs.append({
        "question": (f"Calculate the total cost for 120 boxes of "
                     f"{p_m1['product_name']} ({p_m1['sku']}) at "
                     f"Rs {price_m1:,}/box, including {gst}% GST after the "
                     f"volume discount. (Price from Catalog A, discount and "
                     f"GST from policy)"),
        "expected_answer": (f"120 boxes → {disc_m1}% discount. "
                            f"Base: Rs {price_m1:,} x 120 = Rs {price_m1*120:,}. "
                            f"After {disc_m1}% discount: {fmt_inr(after_disc_m1)}. "
                            f"After {gst}% GST: {fmt_inr(with_gst_m1)}."),
        "expected_document": f"catalog_A.pdf,policy.pdf",
        "expected_page": (f"{page_of('catalog_A', p_m1['sku'])},"
                          f"{meta['policy']['discounts_page']}"),
    })

    # M2: Savings when ordering 100 boxes vs 10 boxes
    p_m2 = ca_products[3]  # SKU1003
    price_m2 = int(p_m2["box_price_inr"])
    disc_10 = discount_rate(10, policy)
    disc_100 = discount_rate(100, policy)
    cost_10 = price_m2 * 10 * (1 - disc_10 / 100)
    cost_100 = price_m2 * 100 * (1 - disc_100 / 100)
    # Per-box savings
    per_box_10 = cost_10 / 10
    per_box_100 = cost_100 / 100
    savings_per_box = per_box_10 - per_box_100
    qs.append({
        "question": (f"How much do you save per box when ordering 100 boxes "
                     f"instead of 10 boxes of {p_m2['product_name']} "
                     f"({p_m2['sku']}) at Rs {price_m2:,}/box? (Discount tiers "
                     f"from policy)"),
        "expected_answer": (f"10 boxes → {disc_10}% discount: Rs {per_box_10:.2f}/box. "
                            f"100 boxes → {disc_100}% discount: Rs {per_box_100:.2f}/box. "
                            f"Savings per box: Rs {savings_per_box:.2f}."),
        "expected_document": f"catalog_A.pdf,policy.pdf",
        "expected_page": (f"{page_of('catalog_A', p_m2['sku'])},"
                          f"{meta['policy']['discounts_page']}"),
    })

    # M3: Total discount amount for a specific order
    p_m3 = ca_products[5]  # SKU1005
    price_m3 = int(p_m3["box_price_inr"])
    n_boxes_m3 = 50
    disc_m3 = discount_rate(n_boxes_m3, policy)
    discount_amount = price_m3 * n_boxes_m3 * disc_m3 / 100
    final_cost = price_m3 * n_boxes_m3 * (1 - disc_m3 / 100)
    qs.append({
        "question": (f"Calculate the discount amount and final cost when "
                     f"ordering {n_boxes_m3} boxes of "
                     f"{p_m3['product_name']} ({p_m3['sku']}) at "
                     f"Rs {price_m3:,}/box. (Discount tiers from policy)"),
        "expected_answer": (f"{n_boxes_m3} boxes → {disc_m3}% discount. "
                            f"Discount amount: Rs {int(round(discount_amount)):,}. "
                            f"Final cost: {fmt_inr(final_cost)}."),
        "expected_document": f"catalog_A.pdf,policy.pdf",
        "expected_page": (f"{page_of('catalog_A', p_m3['sku'])},"
                          f"{meta['policy']['discounts_page']}"),
    })

    # ===================================================================
    # 2 SCANNED-PAGE QUESTIONS (Catalog C, image-only PDF)
    # ===================================================================

    # S1: Price of an overlap product in the scanned Catalog C
    s1_sku = overlap_skus[1]
    s1_p = find_in_catalog("catalog_C", s1_sku)
    qs.append({
        "question": (f"What is the price of {s1_p['product_name']} ({s1_sku}) "
                     f"in the scanned price list (Catalog C)?"),
        "expected_answer": f"Rs {int(s1_p['box_price_inr']):,}",
        "expected_document": "catalog_C.pdf",
        "expected_page": str(page_of("catalog_C", s1_sku)),
    })

    # S2: Another scanned-page product price
    s2_sku = overlap_skus[3]
    s2_p = find_in_catalog("catalog_C", s2_sku)
    qs.append({
        "question": (f"In the scanned Catalog C, what is the box price of "
                     f"{s2_p['product_name']} ({s2_sku})?"),
        "expected_answer": f"Rs {int(s2_p['box_price_inr']):,}",
        "expected_document": "catalog_C.pdf",
        "expected_page": str(page_of("catalog_C", s2_sku)),
    })

    # ===================================================================
    # 1 UNANSWERABLE QUESTION
    # ===================================================================

    qs.append({
        "question": ("What is the manufacturing date and shelf life of "
                     f"{ca_products[0]['product_name']} ({ca_products[0]['sku']})?"),
        "expected_answer": ("Unanswerable - manufacturing date and shelf life "
                            "information is not available in any of the provided "
                            "documents."),
        "expected_document": "N/A",
        "expected_page": "N/A",
    })

    # ---- Write CSV ----
    qpath = os.path.join(EVAL_DIR, "questions.csv")
    with open(qpath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["question", "expected_answer",
                                           "expected_document", "expected_page"])
        w.writeheader()
        w.writerows(qs)

    # Summary
    types = {
        "text": 0, "table": 0, "chart": 0, "text+table+chart": 0,
        "cross-document": 0, "math": 0, "scanned": 0, "unanswerable": 0,
    }
    for i, q in enumerate(qs):
        if i < 4:
            types["text"] += 1
        elif i < 8:
            types["table"] += 1
        elif i < 12:
            types["chart"] += 1
        elif i < 15:
            types["text+table+chart"] += 1
        elif i < 19:
            types["cross-document"] += 1
        elif i < 22:
            types["math"] += 1
        elif i < 24:
            types["scanned"] += 1
        else:
            types["unanswerable"] += 1

    print(f"Wrote {len(qs)} questions to {qpath}")
    print(f"Type distribution: {types}")


if __name__ == "__main__":
    main()
