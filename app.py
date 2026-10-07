"""
app.py — WareDoc Streamlit UI
Tabs: Q&A Assistant | Order Desk
"""
from __future__ import annotations
import json, os, re, time
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="WareDoc", page_icon="📦", layout="wide")

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📦 WareDoc")
    st.caption("Warehouse Document Intelligence")
    st.markdown("---")
    st.markdown("**Documents ingested:**")
    st.markdown("- `catalog_A.pdf` — Wholesale price list")
    st.markdown("- `catalog_B.pdf` — Product specs & TFM chart")
    st.markdown("- `catalog_C.pdf` — Scanned price list (VLM)")
    st.markdown("- `policy.pdf` — Trade policy")
    st.markdown("- `quarterly_report.pdf` — Sales data")

# ── Load backend ───────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner="Loading document index…")
def get_engine():
    import ask as ask_mod
    ask_mod.init()
    return ask_mod

engine = get_engine()

# ── Helper functions ──────────────────────────────────────────────────────────
def _needs_image_heuristic(q: str) -> bool:
    kws = ["chart", "graph", "figure", "scan", "catalog c", "tfm", "quarterly"]
    return any(k in q.lower() for k in kws)


@st.cache_data(show_spinner=False)
def _load_products():
    import pandas as pd
    return pd.read_csv("data/products.csv")

@st.cache_data(show_spinner=False)
def _load_inventory():
    import pandas as pd
    return pd.read_csv("data/inventory.csv")

@st.cache_data(show_spinner=False)
def _load_policy():
    import yaml
    with open("data/policy.yaml") as f:
        return yaml.safe_load(f)


def _apply_discount(qty: int, unit_price: float, policy: dict) -> tuple[float, float, float]:
    """Returns (discount_pct, discounted_price, total)."""
    discount_pct = 0.0
    for tier in reversed(policy.get("volume_discount_tiers", [])):
        if qty >= tier["min_boxes"]:
            discount_pct = tier["discount_percent"]
            break
    discounted = unit_price * (1 - discount_pct / 100)
    total = discounted * qty
    return discount_pct, discounted, total


def _parse_order_with_vlm(text: str) -> list[dict]:
    """Use VLM to extract order lines as JSON."""
    from src.vlm import vlm
    prompt = f"""Extract order lines from this text. Return a JSON array, each element: 
{{"sku": "SKU1000", "product": "Savon brut", "qty": 50}}
If SKU not mentioned, leave sku as "".
Order text: {text}
JSON array only:"""
    try:
        raw = vlm(prompt, images=[])
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE)
        return json.loads(raw)
    except Exception:
        return []


def _fuzzy_match_sku(sku: str, name: str, products_df) -> dict | None:
    """Match SKU or product name with rapidfuzz."""
    try:
        from rapidfuzz import process, fuzz
        import pandas as pd
        if sku:
            sku_clean = sku.replace(" ", "").upper()
            if "sku" in products_df.columns:
                matches = products_df[products_df["sku"].str.upper() == sku_clean]
                if not matches.empty:
                    return matches.iloc[0].to_dict()
        col = "product_name" if "product_name" in products_df.columns else "product"
        if name and col in products_df.columns:
            choices = products_df[col].dropna().tolist()
            match = process.extractOne(name, choices, scorer=fuzz.WRatio, score_cutoff=60)
            if match:
                row = products_df[products_df[col] == match[0]].iloc[0]
                return row.to_dict()
    except ImportError:
        pass
    return None


def _find_substitute(sku: str, products_df, inventory_df) -> dict | None:
    """Find a substitute with overlapping ingredients and positive stock."""
    try:
        if "sku" not in products_df.columns:
            return None
        matches = products_df[products_df["sku"].str.upper() == sku.upper()]
        if matches.empty: return None
        row0 = matches.iloc[0]
        ing_col = "ingredients_text" if "ingredients_text" in products_df.columns else "ingredients"
        target_ing_raw = str(row0.get(ing_col, "") or "")
        target_ing = set(re.split(r"[,;]+", target_ing_raw.lower())) - {"", " "}

        stock_col = "stock_boxes" if "stock_boxes" in inventory_df.columns else "stock"
        inv_skus = set(inventory_df[inventory_df[stock_col] > 0]["sku"].str.upper().tolist()) if stock_col in inventory_df.columns else set()

        best = None
        best_overlap = 0
        for _, row in products_df.iterrows():
            curr_sku = str(row["sku"]).upper()
            if curr_sku == sku.upper(): continue
            if curr_sku not in inv_skus: continue
            row_ing_raw = str(row.get(ing_col, "") or "")
            row_ing = set(re.split(r"[,;]+", row_ing_raw.lower())) - {"", " "}
            overlap = len(target_ing & row_ing)
            if overlap > best_overlap:
                best_overlap = overlap
                best = row.to_dict()
        return best if best_overlap > 0 else None
    except Exception:
        return None


def process_order(text: str) -> dict:
    products_df = _load_products()
    inventory_df = _load_inventory()
    policy = _load_policy()

    lines = _parse_order_with_vlm(text)

    order_lines = []
    subtotal = 0.0
    for line in lines:
        sku = line.get("sku", "")
        name = line.get("product", "")
        qty = int(line.get("qty", 1))

        product = _fuzzy_match_sku(sku, name, products_df)
        if not product:
            order_lines.append({"error": f"Product not found: {sku or name}", "qty": qty})
            continue

        matched_sku = product.get("sku", sku)
        price_val = product.get("box_price_inr") if product.get("box_price_inr") is not None else product.get("price", 0)
        unit_price = float(price_val or 0)
        prod_title = product.get("product_name") or product.get("product", name)

        # Check stock
        in_stock = 0
        stock_col = "stock_boxes" if "stock_boxes" in inventory_df.columns else "stock"
        if "sku" in inventory_df.columns and stock_col in inventory_df.columns:
            inv_row = inventory_df[inventory_df["sku"].str.upper() == str(matched_sku).upper()]
            if not inv_row.empty:
                in_stock = int(inv_row.iloc[0][stock_col])

        substitute = None
        if in_stock == 0:
            substitute = _find_substitute(matched_sku, products_df, inventory_df)

        disc_pct, disc_price, total = _apply_discount(qty, unit_price, policy)
        subtotal += total

        order_lines.append({
            "sku": matched_sku,
            "product": prod_title,
            "qty": qty,
            "unit_price": unit_price,
            "discount_pct": disc_pct,
            "discounted_price": disc_price,
            "line_total": total,
            "in_stock": in_stock,
            "substitute": substitute,
            "calc_expr": f"{unit_price} * {qty} * (1 - {disc_pct}/100)",
        })

    gst_pct = policy.get("gst_percentage", 18)
    gst_amount = subtotal * gst_pct / 100
    grand_total = subtotal + gst_amount

    return {
        "lines": order_lines,
        "subtotal": subtotal,
        "gst_pct": gst_pct,
        "gst_amount": gst_amount,
        "grand_total": grand_total,
        "calc": f"{subtotal:.2f} * (1 + {gst_pct}/100)",
    }


def display_order(result: dict):
    import pandas as pd
    lines = result.get("lines", [])

    if not lines:
        st.warning("No order lines extracted. Please rephrase with SKUs or product names.")
        return

    rows = []
    for line in lines:
        if "error" in line:
            rows.append({"SKU": "—", "Product": line["error"], "Qty": line["qty"],
                         "Unit Price": "—", "Discount": "—", "Line Total": "—", "Stock": "—"})
        else:
            stock_str = str(line["in_stock"]) if line["in_stock"] > 0 else "⚠️ OUT"
            rows.append({
                "SKU": line["sku"],
                "Product": line["product"],
                "Qty": line["qty"],
                "Unit Price": f"Rs {line['unit_price']:,.0f}",
                "Discount": f"{line['discount_pct']:.0f}%",
                "Line Total": f"Rs {line['line_total']:,.2f}",
                "Stock": stock_str,
            })

    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    # Substitutes
    for line in lines:
        if line.get("substitute"):
            sub = line["substitute"]
            st.info(f"⚠️ **{line['product']}** is out of stock. "
                    f"Suggested substitute: **{sub.get('product', sub.get('sku', 'Unknown'))}** "
                    f"(SKU: {sub.get('sku', '—')})")

    # Bill summary
    st.markdown("---")
    st.markdown("### 💰 Bill Summary")
    col1, col2, col3 = st.columns(3)
    col1.metric("Subtotal", f"Rs {result['subtotal']:,.2f}")
    col2.metric(f"GST ({result['gst_pct']}%)", f"Rs {result['gst_amount']:,.2f}")
    col3.metric("Grand Total", f"Rs {result['grand_total']:,.2f}")

    st.markdown("**Calculation:**")
    st.code(result.get("calc", ""), language="python")

    # Per-line calculations
    with st.expander("Line-item calculations"):
        for line in lines:
            if "calc_expr" in line:
                st.markdown(f"• **{line['product']}** ({line['qty']} boxes): `{line['calc_expr']}` = Rs {line['line_total']:,.2f}")


# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_qa, tab_order = st.tabs(["🔍 Q&A Assistant", "🛒 Order Desk"])

# ═══════════════════════════════════════════════════════════
# TAB 1 — Q&A
# ═══════════════════════════════════════════════════════════
with tab_qa:
    st.header("Q&A Assistant")
    st.caption("Ask anything about the catalogs, pricing, policy, or sales data.")

    example_qs = [
        "What is the return policy for damaged goods?",
        "Which bar soap has the highest TFM value?",
        "What were Q1 sales for Soap?",
        "What is the price of Savon brut (SKU1000) in Catalog A?",
        "Calculate cost for 120 boxes of SKU1000 with GST.",
        "Which category had highest Q4 sales?",
    ]
    selected = st.selectbox("Example questions", ["— type your own —"] + example_qs)
    user_q = st.text_input("Your question", value="" if selected == "— type your own —" else selected)

    if st.button("Ask", type="primary") and user_q.strip():
        with st.spinner("Retrieving and answering…"):
            t0 = time.time()
            result = engine.ask(user_q.strip())
            elapsed = time.time() - t0

        # ── Answer ──
        st.markdown("### Answer")
        not_found = result.get("not_found", False)
        if not_found:
            st.warning(result.get("answer", "Not found."))
        else:
            st.success(result.get("answer", ""))

        # ── Calculation ──
        calc = result.get("calculation", "")
        calc_result = result.get("calculation_result")
        if calc:
            st.markdown("### 🧮 Calculation")
            col1, col2 = st.columns([2, 1])
            with col1:
                st.code(calc, language="python")
            with col2:
                if calc_result is not None:
                    st.metric("Result", f"Rs {calc_result:,.2f}" if calc_result > 100 else f"{calc_result:,.4f}")
                else:
                    st.warning("Could not evaluate expression")

        # ── Citations ──
        citations = result.get("citations", [])
        claims = result.get("claims", [])
        if citations or claims:
            st.markdown("### 📚 Citations")
            for cit in citations:
                st.markdown(f"- **{cit['document']}** · Page {cit['page']} · *{cit['section']}* `[{cit['chunk_id']}]`")

            if claims:
                with st.expander("Detailed claims"):
                    for claim in claims:
                        cids = ", ".join(claim.get("chunk_ids", []))
                        st.markdown(f"• {claim['text']} — `{cids}`")

        # ── Page image ──
        image_path = result.get("image_path")
        if image_path and Path(image_path).exists():
            st.markdown("### 🖼 Cited Page Image")
            st.image(image_path, caption=Path(image_path).name, use_container_width=True)
        elif _needs_image_heuristic(user_q) and not image_path:
            st.info("No page image available for this query.")

        st.caption(f"⏱ Answered in {elapsed:.1f}s | {len(result.get('retrieved_chunks', []))} chunks retrieved")

# ═══════════════════════════════════════════════════════════
# TAB 2 — Order Desk
# ═══════════════════════════════════════════════════════════
with tab_order:
    st.header("🛒 Order Desk")
    st.caption("Paste an order in natural language. We'll extract items, check stock, and compute the bill.")

    order_text = st.text_area(
        "Paste order text",
        height=150,
        placeholder="e.g. 'Please send 50 boxes Savon brut SKU1000, 20 boxes Pompelmo SKU1001, 100 boxes Baby SKU1009'",
    )

    if st.button("Process Order", type="primary") and order_text.strip():
        with st.spinner("Processing order…"):
            try:
                order_result = process_order(order_text.strip())
                display_order(order_result)
            except Exception as e:
                st.error(f"Order processing failed: {type(e).__name__}: {e}")
