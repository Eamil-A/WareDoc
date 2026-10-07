"""
WareDoc Step 5: PDF Generation
==============================

Generates five PDFs in data/docs/:

  catalog_A.pdf       – two-column dense price tables (~40 products)
  catalog_B.pdf       – one product per section with generated image,
                        ingredient list, spec table, and TFM bar chart
                        (~25 products)
  catalog_C.pdf       – scanned-style price list (~30 products), rotated,
                        blurred, noised, no text layer; ~8 overlap with A
  quarterly_report.pdf – narrative + table + line chart (consistent numbers)
  policy.pdf          – discount tiers, return rules, delivery terms

Saves page-tracking metadata to data/pdf_metadata.json for eval generation.
"""
import os
import io
import json
import math
import random

import numpy as np
import pandas as pd
import yaml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer,
    Table, TableStyle, Image as RLImage, PageBreak, KeepTogether,
)
from reportlab.pdfgen import canvas as rlcanvas

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
DATA_DIR = "data"
DOCS_DIR = os.path.join(DATA_DIR, "docs")
META_PATH = os.path.join(DATA_DIR, "pdf_metadata.json")
SEED = 42
random.seed(SEED)
np.random.seed(SEED)

PAGE_W, PAGE_H = A4
MARGIN = 15 * mm
COL_GAP = 8 * mm
HEADER_H = 22 * mm
FOOTER_H = 12 * mm

# Colour palette
NAVY = colors.HexColor("#1a3a5c")
LIGHT_BLUE = colors.HexColor("#3b6ea5")
GREY = colors.HexColor("#666666")
LIGHT_GREY = colors.HexColor("#eeeeee")
ROW_ALT = colors.HexColor("#f5f7fa")
ACCENT = colors.HexColor("#c9a227")
WHITE = colors.white

# Category colours (for generated product images)
CAT_COLORS = {
    "soap":      "#8B4513",
    "hand wash": "#4169E1",
    "body wash": "#2E8B57",
    "shampoo":   "#9932CC",
    "face wash": "#FF6347",
}


# ---------------------------------------------------------------------------
# Page-tracking document template
# ---------------------------------------------------------------------------
class TrackingDocTemplate(BaseDocTemplate):
    """Records the page number on which each tracked flowable lands."""

    def __init__(self, *args, **kwargs):
        self.page_map = {}          # tracker_id -> page number (1-based)
        self.tracker_sequence = []  # list of (tracker_id, page)
        super().__init__(*args, **kwargs)

    def afterFlowable(self, flowable):
        tid = getattr(flowable, "tracker_id", None)
        if tid is not None:
            page = self.page
            self.page_map[tid] = page
            self.tracker_sequence.append((tid, page))


def make_styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("CatHeading", fontName="Helvetica-Bold",
                          fontSize=16, textColor=NAVY, spaceAfter=6,
                          alignment=TA_LEFT))
    ss.add(ParagraphStyle("SectionHeading", fontName="Helvetica-Bold",
                          fontSize=13, textColor=WHITE, alignment=TA_LEFT,
                          backColor=NAVY, borderPadding=4, spaceBefore=6,
                          spaceAfter=6, leftIndent=0))
    ss.add(ParagraphStyle("BodyText2", fontName="Helvetica", fontSize=9.5,
                          textColor=colors.black, leading=13, alignment=TA_JUSTIFY,
                          spaceAfter=6))
    ss.add(ParagraphStyle("SmallCell", fontName="Helvetica", fontSize=8,
                          textColor=colors.black, leading=10))
    ss.add(ParagraphStyle("SmallCellBold", fontName="Helvetica-Bold",
                          fontSize=8, textColor=colors.black, leading=10))
    ss.add(ParagraphStyle("CellHead", fontName="Helvetica-Bold", fontSize=8,
                          textColor=WHITE, leading=10))
    ss.add(ParagraphStyle("Narrative", fontName="Helvetica", fontSize=10.5,
                          textColor=colors.black, leading=16, alignment=TA_JUSTIFY,
                          spaceAfter=10))
    ss.add(ParagraphStyle("NarrHead", fontName="Helvetica-Bold", fontSize=14,
                          textColor=NAVY, spaceBefore=10, spaceAfter=8))
    ss.add(ParagraphStyle("PolicyTitle", fontName="Helvetica-Bold", fontSize=20,
                          textColor=NAVY, alignment=TA_CENTER, spaceAfter=16))
    ss.add(ParagraphStyle("PolicySection", fontName="Helvetica-Bold",
                          fontSize=13, textColor=NAVY, spaceBefore=12,
                          spaceAfter=6))
    ss.add(ParagraphStyle("PolicyBody", fontName="Helvetica", fontSize=10.5,
                          textColor=colors.black, leading=15, spaceAfter=6))
    return ss


# ---------------------------------------------------------------------------
# Header / footer drawer (shared)
# ---------------------------------------------------------------------------
def draw_header_footer(canvas, doc, section_title, subtitle=""):
    canvas.saveState()
    # Header bar
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_H - HEADER_H + MARGIN, PAGE_W, HEADER_H - MARGIN,
                fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(MARGIN, PAGE_H - HEADER_H + MARGIN + 6, section_title)
    if subtitle:
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(PAGE_W - MARGIN,
                               PAGE_H - HEADER_H + MARGIN + 6, subtitle)
    # Footer with page number
    canvas.setStrokeColor(NAVY)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, FOOTER_H, PAGE_W - MARGIN, FOOTER_H)
    canvas.setFillColor(GREY)
    canvas.setFont("Helvetica", 8)
    page_str = f"Page {doc.page}"
    canvas.drawCentredString(PAGE_W / 2, FOOTER_H - 9, page_str)
    canvas.drawString(MARGIN, FOOTER_H - 9, "WareDoc")
    canvas.drawRightString(PAGE_W - MARGIN, FOOTER_H - 9,
                           section_title)
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Helpers: load data
# ---------------------------------------------------------------------------
def load_data():
    products = pd.read_csv(os.path.join(DATA_DIR, "products.csv"))
    inventory = pd.read_csv(os.path.join(DATA_DIR, "inventory.csv"))
    with open(os.path.join(DATA_DIR, "policy.yaml")) as f:
        policy = yaml.safe_load(f)
    with open(os.path.join(DATA_DIR, "quarterly_sales.json")) as f:
        quarterly = json.load(f)
    return products, inventory, policy, quarterly


# ---------------------------------------------------------------------------
# Catalog A: two-column dense price tables (~40 products)
# ---------------------------------------------------------------------------
def gen_catalog_a(products, styles):
    path = os.path.join(DOCS_DIR, "catalog_A.pdf")
    n = min(40, len(products))
    cat_products = products.iloc[:n].copy()

    col_w = (PAGE_W - 2 * MARGIN - COL_GAP) / 2
    body_h = PAGE_H - 2 * MARGIN - HEADER_H - FOOTER_H

    left_frame = Frame(MARGIN, MARGIN + FOOTER_H, col_w, body_h, id="left",
                       leftPadding=0, rightPadding=0, topPadding=0,
                       bottomPadding=0)
    right_frame = Frame(MARGIN + col_w + COL_GAP, MARGIN + FOOTER_H, col_w,
                        body_h, id="right",
                        leftPadding=0, rightPadding=0, topPadding=0,
                        bottomPadding=0)

    doc = TrackingDocTemplate(path, pagesize=A4)
    template = PageTemplate(
        id="two_col", frames=[left_frame, right_frame],
        onPage=lambda c, d: draw_header_footer(c, d, "Catalog A - Price List",
                                                "Wholesale Soap & Personal Care"))
    doc.addPageTemplates([template])

    story = []
    # Table column widths inside each column
    tcol_w = [col_w * 0.12, col_w * 0.48, col_w * 0.12, col_w * 0.28]

    # Build compact tables, 10 products per table
    batch_size = 10
    for batch_start in range(0, n, batch_size):
        batch = cat_products.iloc[batch_start:batch_start + batch_size]
        header = [Paragraph("<b>SKU</b>", styles["SmallCellBold"]),
                  Paragraph("<b>Product</b>", styles["SmallCellBold"]),
                  Paragraph("<b>Pack</b>", styles["SmallCellBold"]),
                  Paragraph("<b>Price (INR)</b>", styles["SmallCellBold"])]
        rows = [header]
        for _, p in batch.iterrows():
            name = str(p["product_name"])
            if len(name) > 28:
                name = name[:25] + "..."
            rows.append([
                Paragraph(p["sku"], styles["SmallCell"]),
                Paragraph(name, styles["SmallCell"]),
                Paragraph(str(int(p["pack_size"])), styles["SmallCell"]),
                Paragraph(f"{int(p['box_price_inr']):,}", styles["SmallCell"]),
            ])

        tbl = Table(rows, colWidths=tcol_w, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (2, 0), (3, -1), "CENTER"),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ROW_ALT]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        # Tag an invisible spacer before the table so page tracking survives
        # multi-column frame splitting (tables can split; spacers won't).
        tracker_spacer = Spacer(1, 0)
        tracker_spacer.tracker_id = f"catA_batch_{batch_start}_{min(batch_start+batch_size, n)}"
        story.append(tracker_spacer)
        story.append(tbl)
        story.append(Spacer(1, 6))

    doc.build(story)

    # Build page map: product index -> page
    page_map = {}
    for tid, page in doc.tracker_sequence:
        if tid.startswith("catA_batch_"):
            parts = tid.split("_")
            start_idx = int(parts[2])
            end_idx = int(parts[3])
            for i in range(start_idx, end_idx):
                sku = cat_products.iloc[i]["sku"]
                page_map[sku] = page

    meta = {
        "file": "catalog_A.pdf",
        "products": {sku: page for sku, page in page_map.items()},
        "product_list": cat_products[["sku", "product_name", "brands",
                                       "box_price_inr", "pack_size",
                                       "supplier"]].to_dict("records"),
    }
    return meta


# ---------------------------------------------------------------------------
# Catalog B: one product per section + generated image + TFM chart (~25)
# ---------------------------------------------------------------------------
def make_product_image(product_row, size=(300, 200)):
    """Generate a simple but attractive product mockup image with PIL."""
    cat = product_row["category"]
    bg_color = CAT_COLORS.get(cat, "#444444")
    img = Image.new("RGB", size, bg_color)
    draw = ImageDraw.Draw(img)

    # Gradient overlay (top lighter)
    overlay = Image.new("RGB", size, (255, 255, 255))
    for y in range(size[1]):
        alpha = int(40 * (y / size[1]))
        for x in range(size[0]):
            pass  # skip per-pixel; use a simpler approach
    # Simple lighter band at top
    for y in range(size[1] // 3):
        shade = tuple(min(255, c + (size[1] // 3 - y) // 3) for c in
                      tuple(int(bg_color[i:i+2], 16) for i in (1, 3, 5)))
        draw.line([(0, y), (size[0], y)], fill=shade)

    # Product icon (circle)
    cx, cy = size[0] // 2, size[1] // 2 - 10
    r = 35
    draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                 fill=(255, 255, 255, 80), outline=(255, 255, 255), width=2)

    # Try to load a font
    try:
        font_big = ImageFont.truetype("arial.ttf", 18)
        font_med = ImageFont.truetype("arial.ttf", 13)
        font_small = ImageFont.truetype("arial.ttf", 10)
    except (IOError, OSError):
        font_big = ImageFont.load_default()
        font_med = ImageFont.load_default()
        font_small = ImageFont.load_default()

    name = str(product_row["product_name"])
    if len(name) > 30:
        name = name[:27] + "..."
    brand = str(product_row["brands"])

    # Draw text
    draw.text((10, 10), brand, fill=(255, 255, 255), font=font_med)
    draw.text((cx - len(name) * 4, cy + r + 8), name,
              fill=(255, 255, 255), font=font_small)
    draw.text((10, size[1] - 20), f"SKU: {product_row['sku']}",
              fill=(255, 255, 255), font=font_small)
    draw.text((size[0] - 80, size[1] - 20),
              f"{int(product_row['quantity_value'])}{product_row['quantity_unit']}",
              fill=(255, 255, 255), font=font_small)
    return img


def gen_tfm_chart(products, path):
    """Create a matplotlib bar chart comparing TFM across bar soaps."""
    soaps = products[products["tfm"].notna() & (products["tfm"] != "")].copy()
    soaps["tfm"] = soaps["tfm"].astype(int)
    soaps = soaps.sort_values("tfm", ascending=True)

    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
    names = [str(n)[:20] for n in soaps["product_name"]]
    vals = soaps["tfm"].tolist()
    bars = ax.barh(names, vals, color="#8B4513", edgecolor="#5C2E0C", height=0.6)
    # Colour highest bar differently
    if vals:
        max_idx = vals.index(max(vals))
        bars[max_idx].set_color("#DAA520")

    ax.set_xlabel("TFM (Total Fatty Matter %)", fontsize=11)
    ax.set_title("TFM Comparison Across Bar Soaps", fontsize=13,
                 fontweight="bold")
    ax.set_xlim(60, 85)
    for bar, val in zip(bars, vals):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                str(val), va="center", fontsize=9)
    ax.axvline(x=76, color="green", linestyle="--", alpha=0.5, linewidth=1)
    ax.text(76.5, 0, "Grade 1 (>=76)", fontsize=8, color="green")
    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    return len(soaps)


def gen_catalog_b(products, styles):
    path = os.path.join(DOCS_DIR, "catalog_B.pdf")
    n = min(25, len(products))
    cat_products = products.iloc[:n].copy()

    body_h = PAGE_H - 2 * MARGIN - HEADER_H - FOOTER_H
    frame = Frame(MARGIN, MARGIN + FOOTER_H, PAGE_W - 2 * MARGIN, body_h,
                  id="main", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)

    doc = TrackingDocTemplate(path, pagesize=A4)
    template = PageTemplate(
        id="single", frames=[frame],
        onPage=lambda c, d: draw_header_footer(c, d, "Catalog B - Product Details",
                                                "Soap & Personal Care Catalog"))
    doc.addPageTemplates([template])

    # Generate product images
    img_dir = os.path.join(DATA_DIR, "product_images")
    os.makedirs(img_dir, exist_ok=True)

    story = []
    for idx, (_, p) in enumerate(cat_products.iterrows()):
        # Section heading
        heading = Paragraph(f"{p['product_name']}", styles["SectionHeading"])
        heading.tracker_id = f"catB_product_{p['sku']}"
        story.append(heading)
        story.append(Spacer(1, 4))

        # Generated product image
        img = make_product_image(p)
        img_path = os.path.join(img_dir, f"{p['sku']}.png")
        img.save(img_path)
        story.append(RLImage(img_path, width=8 * cm, height=5.3 * cm))
        story.append(Spacer(1, 4))

        # Ingredient list
        ing_text = str(p["ingredients_text"])
        if len(ing_text) > 400:
            ing_text = ing_text[:397] + "..."
        story.append(Paragraph(f"<b>Ingredients:</b> {ing_text}",
                                styles["BodyText2"]))
        story.append(Spacer(1, 4))

        # Spec table
        spec_data = [
            ["SKU", p["sku"], "Brand", str(p["brands"])],
            ["Category", str(p["category"]).title(),
             "Pack Size", f"{int(p['pack_size'])} units/box"],
            ["Quantity", f"{int(p['quantity_value'])} {p['quantity_unit']}",
             "Box Price", f"Rs {int(p['box_price_inr']):,}"],
            ["Supplier", str(p["supplier"]),
             "TFM", str(p["tfm"]) if p["tfm"] != "" and pd.notna(p["tfm"]) else "N/A"],
        ]
        spec_tbl = Table(spec_data,
                         colWidths=[3 * cm, 5 * cm, 3 * cm, 5 * cm])
        spec_tbl.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (0, -1), LIGHT_GREY),
            ("BACKGROUND", (2, 0), (2, -1), LIGHT_GREY),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(spec_tbl)
        story.append(Spacer(1, 6))

        # Page break between products (except last)
        if idx < n - 1:
            story.append(PageBreak())

    # TFM chart page
    chart_heading = Paragraph("TFM Comparison Across Bar Soaps",
                              styles["SectionHeading"])
    chart_heading.tracker_id = "catB_tfm_chart"
    story.append(PageBreak())
    story.append(chart_heading)
    story.append(Spacer(1, 8))
    chart_path = os.path.join(DATA_DIR, "tfm_chart.png")
    n_soaps = gen_tfm_chart(products, chart_path)
    story.append(RLImage(chart_path, width=16 * cm, height=10 * cm))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "The chart above compares the Total Fatty Matter (TFM) percentage "
        "across all bar soaps in this catalog. TFM indicates the quality of "
        "soap; Grade 1 soaps have TFM >= 76%. Soaps with higher TFM contain "
        "more fatty matter and are generally considered better quality.",
        styles["BodyText2"]))

    doc.build(story)

    # Build product → page map
    page_map = {}
    for tid, page in doc.tracker_sequence:
        if tid.startswith("catB_product_"):
            sku = tid.replace("catB_product_", "")
            page_map[sku] = page
        elif tid == "catB_tfm_chart":
            page_map["__tfm_chart__"] = page

    meta = {
        "file": "catalog_B.pdf",
        "products": page_map,
        "product_list": cat_products[["sku", "product_name", "brands",
                                      "box_price_inr", "tfm", "category",
                                      "ingredients_text"]].to_dict("records"),
        "tfm_chart_page": page_map.get("__tfm_chart__", 1),
        "num_soaps_in_chart": n_soaps,
    }
    return meta


# ---------------------------------------------------------------------------
# Catalog C: scanned-style price list (~30 products, no text layer)
# ---------------------------------------------------------------------------
def render_price_page_image(products_batch, page_num, width=1240, height=1754):
    """Render a price-list page as a PIL image (simulating a printed page)."""
    img = Image.new("RGB", (width, height), (250, 248, 245))
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("arial.ttf", 36)
        font_head = ImageFont.truetype("arialbd.ttf", 16)
        font_cell = ImageFont.truetype("arial.ttf", 14)
        font_small = ImageFont.truetype("arial.ttf", 11)
    except (IOError, OSError):
        font_title = ImageFont.load_default()
        font_head = font_title
        font_cell = font_title
        font_small = font_title

    # Title
    draw.text((60, 40), "CATALOG C - Price List", fill=(20, 20, 20),
              font=font_title)
    draw.text((60, 90), "Wholesale Soap & Personal Care - Scanned Copy",
              fill=(80, 80, 80), font=font_small)

    # Table header
    y = 140
    draw.rectangle([50, y, width - 50, y + 30], fill=(30, 60, 100))
    draw.text((60, y + 6), "SKU", fill=(255, 255, 255), font=font_head)
    draw.text((180, y + 6), "Product Name", fill=(255, 255, 255), font=font_head)
    draw.text((620, y + 6), "Brand", fill=(255, 255, 255), font=font_head)
    draw.text((820, y + 6), "Pack", fill=(255, 255, 255), font=font_head)
    draw.text((920, y + 6), "Price (Rs)", fill=(255, 255, 255), font=font_head)
    y += 30

    # Rows
    for i, (_, p) in enumerate(products_batch.iterrows()):
        if i % 2 == 0:
            draw.rectangle([50, y, width - 50, y + 26], fill=(240, 240, 238))
        name = str(p["product_name"])
        if len(name) > 40:
            name = name[:37] + "..."
        draw.text((60, y + 5), str(p["sku"]), fill=(20, 20, 20), font=font_cell)
        draw.text((180, y + 5), name, fill=(20, 20, 20), font=font_cell)
        draw.text((620, y + 5), str(p["brands"])[:18], fill=(20, 20, 20),
                  font=font_cell)
        draw.text((820, y + 5), str(int(p["pack_size"])), fill=(20, 20, 20),
                  font=font_cell)
        draw.text((920, y + 5), f"{int(p['box_price_inr']):,}",
                  fill=(20, 20, 20), font=font_cell)
        y += 26

    # Page number text (printed on page, not a PDF text layer)
    draw.text((width // 2 - 30, height - 60),
              f"Page {page_num}", fill=(100, 100, 100), font=font_cell)
    return img


def apply_scan_effects(img, rotation_deg=2):
    """Apply rotation, blur, and noise to simulate a scanned document."""
    # Rotate
    angle = rotation_deg if rotation_deg != 0 else random.uniform(-3, 3)
    if abs(angle) < 0.5:
        angle = random.choice([-1, 1, -2, 2, 3, -3])
    img = img.rotate(angle, fillcolor=(240, 238, 235),
                     expand=False, resample=Image.BICUBIC)

    # Gaussian blur (slight)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.8))

    # Add noise
    arr = np.array(img).astype(np.float32)
    noise = np.random.normal(0, 8, arr.shape[:2])
    noise = np.stack([noise] * 3, axis=-1)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)

    # Slight brightness/contrast adjustment (scan look)
    img = ImageChops.multiply(img, Image.new("RGB", img.size, (235, 235, 235)))
    return img


def gen_catalog_c(products, cat_a_meta, styles):
    from fpdf import FPDF

    path = os.path.join(DOCS_DIR, "catalog_C.pdf")
    n = min(30, len(products))
    # Start with products not in catalog A, then add overlap
    cat_a_skus = set(cat_a_meta["products"].keys())
    overlap_skus = set(random.sample(list(cat_a_skus),
                                     min(8, len(cat_a_skus))))

    non_overlap = products[~products["sku"].isin(cat_a_skus)]
    overlap = products[products["sku"].isin(overlap_skus)]

    # Build catalog C product list
    cat_c_products = pd.concat([non_overlap.iloc[:n - len(overlap)], overlap])
    cat_c_products = cat_c_products.iloc[:n].copy()

    # For overlap products, set slightly different prices (±3-7%)
    price_overrides = {}
    for _, p in overlap.iterrows():
        orig = int(p["box_price_inr"])
        change = random.uniform(0.03, 0.07) * random.choice([-1, 1])
        new_price = max(50, int(round(orig * (1 + change) / 5) * 5))
        price_overrides[p["sku"]] = new_price

    cat_c_products["box_price_inr"] = cat_c_products.apply(
        lambda r: price_overrides.get(r["sku"], r["box_price_inr"]), axis=1)

    # Generate page images
    pdf = FPDF(unit="pt", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_title("Catalog C - Scanned Price List")

    products_per_page = 22
    page_map = {}
    for page_start in range(0, n, products_per_page):
        page_num = page_start // products_per_page + 1
        batch = cat_c_products.iloc[page_start:page_start + products_per_page]
        img = render_price_page_image(batch, page_num)
        img = apply_scan_effects(img)

        # Save to temp buffer
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        pdf.add_page()
        pdf.image(buf, x=0, y=0, w=595.28, h=841.89)

        # Track products on this page
        for _, p in batch.iterrows():
            page_map[p["sku"]] = page_num

    pdf.output(path)

    meta = {
        "file": "catalog_C.pdf",
        "products": page_map,
        "product_list": cat_c_products[["sku", "product_name", "brands",
                                         "box_price_inr", "pack_size",
                                         "supplier"]].to_dict("records"),
        "overlap_skus": list(overlap_skus),
        "price_overrides": price_overrides,
    }
    return meta


# ---------------------------------------------------------------------------
# Quarterly report: narrative + table + line chart
# ---------------------------------------------------------------------------
def gen_quarterly_chart(quarterly, path):
    """Create a line chart of quarterly sales by category."""
    cats = quarterly["categories"]
    quarters = quarterly["quarters"]
    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=150)
    markers = ["o", "s", "^", "D", "v"]
    colors_list = ["#1a3a5c", "#c9a227", "#2E8B57", "#9932CC", "#FF6347"]
    for i, cat in enumerate(cats):
        vals = [quarterly["sales"][cat][q] for q in quarters]
        ax.plot(quarters, vals, marker=markers[i % len(markers)],
                color=colors_list[i % len(colors_list)], linewidth=2,
                markersize=8, label=cat)
        for q, v in zip(quarters, vals):
            ax.annotate(f"{v:,}", (q, v), textcoords="offset points",
                        xytext=(0, 10), ha="center", fontsize=8)

    ax.set_xlabel("Quarter", fontsize=11)
    ax.set_ylabel("Boxes Sold", fontsize=11)
    ax.set_title("Quarterly Sales by Category (FY 2024-25)", fontsize=13,
                 fontweight="bold")
    ax.legend(fontsize=9, loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, max(quarterly["sales"][c][q]
                       for c in cats for q in quarters) * 1.15)
    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()


def gen_quarterly_report(quarterly, styles):
    path = os.path.join(DOCS_DIR, "quarterly_report.pdf")
    body_h = PAGE_H - 2 * MARGIN - HEADER_H - FOOTER_H
    frame = Frame(MARGIN, MARGIN + FOOTER_H, PAGE_W - 2 * MARGIN, body_h,
                  id="main", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)

    doc = TrackingDocTemplate(path, pagesize=A4)
    template = PageTemplate(
        id="single", frames=[frame],
        onPage=lambda c, d: draw_header_footer(c, d, "Quarterly Performance Report",
                                                "FY 2024-25"))
    doc.addPageTemplates([template])

    cats = quarterly["categories"]
    quarters = quarterly["quarters"]

    story = []

    # --- Narrative section ---
    narr_head = Paragraph("Executive Summary & Analysis", styles["NarrHead"])
    narr_head.tracker_id = "qr_narrative"
    story.append(narr_head)

    narrative_paragraphs = [
        "This report presents the quarterly sales performance for the "
        "fiscal year 2024-25 across five product categories: Soap, Hand Wash, "
        "Body Wash, Shampoo, and Face Wash. The year was marked by significant "
        "volatility driven by external supply-chain factors and seasonal "
        "demand patterns.",

        "<b>Reason 1 - Festive Season Demand (Q3-Q4):</b> The festive season "
        "spanning Diwali in Q3 and the year-end celebrations in Q4 drove a "
        "sharp increase in demand across all categories. Soap sales rose from "
        "12,000 boxes in Q1 to 18,500 boxes in Q4, a 54% increase. Body Wash "
        "similarly jumped from 10,000 to 15,200 boxes. The festive period is "
        "traditionally the strongest sales window for personal care products "
        "in the Indian wholesale market, as retailers stock up in anticipation "
        "of higher consumer spending on grooming and gifting.",

        "<b>Reason 2 - Supplier Delay in Q2 (Distributor B):</b> Q2 saw a "
        "noticeable dip across all categories. Distributor B experienced "
        "logistics and customs delays that reduced supply by approximately "
        "20-25% for two key product lines. Soap fell from 12,000 boxes in Q1 "
        "to 9,500 in Q2, and Shampoo dropped from 11,000 to 9,000. The supply "
        "gap was resolved by Q3 as alternative sourcing arrangements were put "
        "in place.",

        "<b>Reason 3 - Price Increase in Q4 (8-12% on Bar Soaps):</b> A price "
        "revision was implemented in Q4, increasing bar soap prices by 8-12% "
        "due to rising raw material costs. Despite the price increase, volume "
        "continued to grow due to festive demand, resulting in higher revenue. "
        "The price change contributed to an estimated 15% revenue uplift on "
        "soap alone. Face Wash, which had a smaller price adjustment of 5%, "
        "saw the most modest Q4 increase, going from 6,000 boxes in Q1 to "
        "8,500 in Q4.",
    ]
    for para in narrative_paragraphs:
        story.append(Paragraph(para, styles["Narrative"]))

    story.append(PageBreak())

    # --- Table section ---
    table_head = Paragraph("Quarterly Sales by Category (Boxes)", styles["NarrHead"])
    table_head.tracker_id = "qr_table"
    story.append(table_head)
    story.append(Spacer(1, 6))

    # Build table
    header_row = ["Category"] + quarters + ["Total"]
    table_rows = [header_row]
    totals = {q: 0 for q in quarters}
    grand_total = 0
    for cat in cats:
        row = [cat]
        cat_total = 0
        for q in quarters:
            v = quarterly["sales"][cat][q]
            row.append(f"{v:,}")
            totals[q] += v
            cat_total += v
        row.append(f"{cat_total:,}")
        grand_total += cat_total
        table_rows.append(row)
    total_row = ["Total"] + [f"{totals[q]:,}" for q in quarters] + [f"{grand_total:,}"]
    table_rows.append(total_row)

    col_widths = [3.5 * cm] + [2.6 * cm] * 4 + [2.6 * cm]
    qtbl = Table(table_rows, colWidths=col_widths, repeatRows=1)
    qtbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [WHITE, ROW_ALT]),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, -1), (-1, -1), WHITE),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#aaaaaa")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    qtbl.tracker_id = "qr_table_flowable"
    story.append(qtbl)
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "Table 1: Quarterly sales volumes (in boxes) for each product "
        "category. The total column shows annual volume per category.",
        styles["BodyText2"]))

    story.append(PageBreak())

    # --- Chart section ---
    chart_head = Paragraph("Quarterly Sales Trend", styles["NarrHead"])
    chart_head.tracker_id = "qr_chart"
    story.append(chart_head)
    story.append(Spacer(1, 8))
    chart_path = os.path.join(DATA_DIR, "quarterly_chart.png")
    gen_quarterly_chart(quarterly, chart_path)
    story.append(RLImage(chart_path, width=16 * cm, height=9.8 * cm))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Figure 1: Line chart of quarterly sales by category. The data shown "
        "matches Table 1 exactly. All five categories follow the same pattern: "
        "a Q2 dip due to the Distributor B supply delay, followed by strong "
        "recovery in Q3 and Q4 driven by festive season demand and the Q4 "
        "price adjustment.",
        styles["BodyText2"]))

    doc.build(story)

    page_map = {}
    for tid, page in doc.tracker_sequence:
        page_map[tid] = page

    meta = {
        "file": "quarterly_report.pdf",
        "narrative_page": page_map.get("qr_narrative", 1),
        "table_page": page_map.get("qr_table", page_map.get("qr_table_flowable", 2)),
        "chart_page": page_map.get("qr_chart", 3),
        "quarterly_data": quarterly,
        "narrative_reasons": [
            "Festive season demand (Diwali and year-end celebrations) drove Q3-Q4 surge",
            "Distributor B supplier delay caused Q2 dip across all categories",
            "Price increase of 8-12% on bar soaps in Q4 boosted revenue",
        ],
    }
    return meta


# ---------------------------------------------------------------------------
# Policy PDF
# ---------------------------------------------------------------------------
def gen_policy_pdf(policy, styles):
    path = os.path.join(DOCS_DIR, "policy.pdf")
    body_h = PAGE_H - 2 * MARGIN - HEADER_H - FOOTER_H
    frame = Frame(MARGIN, MARGIN + FOOTER_H, PAGE_W - 2 * MARGIN, body_h,
                  id="main", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)

    doc = TrackingDocTemplate(path, pagesize=A4)
    template = PageTemplate(
        id="single", frames=[frame],
        onPage=lambda c, d: draw_header_footer(c, d, "Business Policy Document",
                                                "WareDoc Wholesale"))
    doc.addPageTemplates([template])

    story = []
    title = Paragraph("Business Policy & Terms", styles["PolicyTitle"])
    title.tracker_id = "policy_title"
    story.append(title)

    # Discount tiers
    disc_head = Paragraph("Volume Discount Tiers", styles["PolicySection"])
    disc_head.tracker_id = "policy_discounts"
    story.append(disc_head)
    story.append(Paragraph(
        "The following volume-based discounts apply to all bulk orders. "
        "Discounts are calculated on the base box price and applied at checkout:",
        styles["PolicyBody"]))

    disc_rows = [["Min. Boxes", "Discount %"]]
    for tier in policy["volume_discount_tiers"]:
        disc_rows.append([str(tier["min_boxes"]),
                          f"{tier['discount_percent']}%"])
    dtbl = Table(disc_rows, colWidths=[5 * cm, 5 * cm])
    dtbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ROW_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#aaaaaa")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(dtbl)
    story.append(Spacer(1, 10))

    story.append(Paragraph(
        f"GST rate: {policy['gst_percentage']}% (placeholder - to be verified). "
        "GST is applied on the discounted price.",
        styles["PolicyBody"]))

    # Return rules
    ret_head = Paragraph("Return Rules", styles["PolicySection"])
    ret_head.tracker_id = "policy_returns"
    story.append(ret_head)
    for rule in policy["return_rules"]:
        story.append(Paragraph(f"• {rule}", styles["PolicyBody"]))
    story.append(Spacer(1, 6))

    # Delivery terms
    del_head = Paragraph("Delivery Terms", styles["PolicySection"])
    del_head.tracker_id = "policy_delivery"
    story.append(del_head)
    for term in policy["delivery_terms"]:
        story.append(Paragraph(f"• {term}", styles["PolicyBody"]))
    story.append(Spacer(1, 6))

    # Customers
    cust_head = Paragraph("Approved Customers & Credit Limits", styles["PolicySection"])
    cust_head.tracker_id = "policy_customers"
    story.append(cust_head)
    cust_rows = [["Customer", "Credit Limit (INR)"]]
    for c in policy["customers"]:
        cust_rows.append([c["name"], f"{c['credit_limit_inr']:,}"])
    ctbl = Table(cust_rows, colWidths=[8 * cm, 5 * cm])
    ctbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ROW_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#aaaaaa")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(ctbl)

    doc.build(story)

    page_map = {}
    for tid, page in doc.tracker_sequence:
        page_map[tid] = page

    meta = {
        "file": "policy.pdf",
        "discounts_page": page_map.get("policy_discounts", page_map.get("policy_title", 1)),
        "returns_page": page_map.get("policy_returns", 1),
        "delivery_page": page_map.get("policy_delivery", 1),
        "customers_page": page_map.get("policy_customers", 1),
    }
    return meta


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    os.makedirs(DOCS_DIR, exist_ok=True)
    styles = make_styles()

    print("Loading data ...")
    products, inventory, policy, quarterly = load_data()
    print(f"  {len(products)} products loaded")

    all_meta = {}

    print("[1/5] Generating catalog_A.pdf ...")
    all_meta["catalog_A"] = gen_catalog_a(products, styles)
    print(f"  Done. Products mapped: {len(all_meta['catalog_A']['products'])}")

    print("[2/5] Generating catalog_B.pdf ...")
    all_meta["catalog_B"] = gen_catalog_b(products, styles)
    print(f"  Done. Products mapped: {len(all_meta['catalog_B']['products'])}")

    print("[3/5] Generating catalog_C.pdf (scanned) ...")
    all_meta["catalog_C"] = gen_catalog_c(products, all_meta["catalog_A"], styles)
    print(f"  Done. Products mapped: {len(all_meta['catalog_C']['products'])}")
    print(f"  Overlap with A: {len(all_meta['catalog_C']['overlap_skus'])} products")

    print("[4/5] Generating quarterly_report.pdf ...")
    all_meta["quarterly_report"] = gen_quarterly_report(quarterly, styles)
    print(f"  Done. Narrative: p{all_meta['quarterly_report']['narrative_page']}, "
          f"Table: p{all_meta['quarterly_report']['table_page']}, "
          f"Chart: p{all_meta['quarterly_report']['chart_page']}")

    print("[5/5] Generating policy.pdf ...")
    all_meta["policy"] = gen_policy_pdf(policy, styles)
    print(f"  Done. Discounts: p{all_meta['policy']['discounts_page']}")

    # Save metadata (NaN-safe)
    import math

    def sanitize(obj):
        """Replace NaN/inf with None so json.dump doesn't produce invalid JSON."""
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return obj
        if isinstance(obj, dict):
            return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [sanitize(v) for v in obj]
        return obj

    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(sanitize(all_meta), f, indent=2, allow_nan=False)
    print(f"\nMetadata saved to {META_PATH}")
    print("Done!  All PDFs generated in data/docs/")


if __name__ == "__main__":
    main()
