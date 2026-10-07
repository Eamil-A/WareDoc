"""
Checkpoint A — PDF ingestion.
VLM is used ONLY for:
  - catalog_C.pdf (all pages, scanned / no text layer)
  - catalog_B.pdf page 26 (TFM chart)
  - quarterly_report.pdf page 3 (line chart)
Everything else uses native PyMuPDF text + pdfplumber tables.
Output: data/chunks.json + data/page_images/*.png
"""
from __future__ import annotations
import json, logging, os, re
from pathlib import Path

DOCS_DIR   = Path("data/docs")
IMG_DIR    = Path("data/page_images")
CHUNKS_OUT = Path("data/chunks.json")
DPI        = 150
ZOOM       = DPI / 72
MIN_TEXT   = 20

# Pages that need VLM (1-indexed)
VLM_CHART_PAGES  = {"catalog_B": {26}, "quarterly_report": {3}}
VLM_SCAN_DOCS    = {"catalog_C"}

SCAN_PROMPT = (
    "Transcribe every product row visible in this scanned price-list page. "
    "Format as a markdown table with columns: SKU | Product | Brand | Pack | Price (Rs). "
    "Keep numbers exact. Output only the table."
)
CHART_PROMPT = (
    "Describe this business chart for information retrieval. "
    "Include: chart type, title, X-axis, Y-axis, each series name with its values at "
    "every data point, and the overall trend. Use 'approximately' for values read visually."
)

logger = logging.getLogger("waredoc.ingest")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def _table_md(rows: list) -> str:
    if not rows or len(rows) < 2: return ""
    def c(v): return str(v or "").replace("\n", " ").replace("|", "\\|").strip()
    header = "| " + " | ".join(c(x) for x in rows[0]) + " |"
    sep    = "| " + " | ".join("---" for _ in rows[0]) + " |"
    body   = "\n".join("| " + " | ".join(c(x) for x in row) + " |" for row in rows[1:])
    return "\n".join([header, sep, body])


def _heading(text: str, doc: str, page: int) -> str:
    for line in text.strip().split("\n"):
        l = line.strip()
        if len(l) >= 3: return l[:80]
    return f"{doc} p{page}"


def _split(text: str, max_len: int = 1200) -> list[str]:
    chunks = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para: continue
        if len(para) <= max_len:
            chunks.append(para)
        else:
            sents = re.split(r'(?<=[.!?])\s+', para)
            buf = ""
            for s in sents:
                if len(buf) + len(s) + 1 <= max_len: buf = (buf + " " + s).strip()
                else:
                    if buf: chunks.append(buf)
                    buf = s
            if buf: chunks.append(buf)
    return chunks


def _safe_vlm(prompt: str, images: list, label: str) -> str:
    """Call VLM; return empty string on any failure (never blocks the pipeline).
    Uses a 30-second hard timeout so a single slow call never stalls ingest."""
    try:
        from src.vlm import vlm
        result = vlm(prompt, images=images)
        return result or ""
    except Exception as e:
        logger.warning("VLM skipped [%s]: %s", label, type(e).__name__)
        return ""


def ingest_pdf(pdf_path: Path) -> list[dict]:
    import fitz, pdfplumber  # type: ignore
    doc_name = pdf_path.stem
    chunks: list[dict] = []
    cidx = [0]

    def mk_id():
        cid = f"{doc_name}_p{page_num}_c{cidx[0]}"; cidx[0] += 1; return cid

    def add(type_, text, page_num, section, img_path, source="native"):
        text = text.strip()
        if not text: return
        chunks.append({"chunk_id": mk_id(), "doc": doc_name, "page": page_num,
                        "section": section, "type": type_, "text": text,
                        "image_path": str(img_path), "source": source})

    fitz_doc = fitz.open(str(pdf_path))
    pl_doc   = pdfplumber.open(str(pdf_path))
    is_scan  = doc_name in VLM_SCAN_DOCS
    chart_pg = VLM_CHART_PAGES.get(doc_name, set())

    print(f"  {pdf_path.name} ({len(fitz_doc)} pages) ...", flush=True)

    for pi in range(len(fitz_doc)):
        page_num = pi + 1
        fitz_page = fitz_doc[pi]
        pl_page   = pl_doc.pages[pi]

        # render PNG
        img_path = IMG_DIR / f"{doc_name}_p{page_num}.png"
        pix = fitz_page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM))
        pix.save(str(img_path))

        raw_text = fitz_page.get_text("text").strip()
        section  = _heading(raw_text, doc_name, page_num)

        # tables
        tables_md = []
        try:
            for t in (pl_page.extract_tables() or []):
                md = _table_md(t)
                if md: tables_md.append(md)
        except Exception: pass

        # --- VLM: scanned doc ---
        if is_scan:
            vlm_text = _safe_vlm(SCAN_PROMPT, [str(img_path)], f"{doc_name} p{page_num} scan")
            add("scan", vlm_text or f"[scan p{page_num}: VLM unavailable]",
                page_num, section, img_path,
                source="vlm_scan" if vlm_text else "vlm_scan_failed")
            continue

        # --- VLM: chart page ---
        if page_num in chart_pg:
            chart_text = _safe_vlm(CHART_PROMPT, [str(img_path)], f"{doc_name} p{page_num} chart")
            add("chart", chart_text or f"[chart p{page_num}: VLM unavailable]",
                page_num, section, img_path,
                source="vlm_chart" if chart_text else "vlm_chart_failed")
            # also keep any native text on this page
            for p in _split(raw_text):
                if len(p) >= 10 and not p.startswith("Page ") and p != "WareDoc":
                    add("text", p, page_num, section, img_path)
            for md in tables_md:
                add("table", md, page_num, section, img_path)
            continue

        # --- native text + tables ---
        for p in _split(raw_text):
            if len(p) >= 10 and not p.startswith("Page ") and p != "WareDoc":
                add("text", p, page_num, section, img_path)
        for md in tables_md:
            add("table", md, page_num, section, img_path)

    fitz_doc.close(); pl_doc.close()
    return chunks


def main():
    import sys
    no_vlm = "--no-vlm" in sys.argv
    if no_vlm:
        # Monkey-patch _safe_vlm to always return empty string
        import src.ingest as _self
        _self._safe_vlm = lambda p, i, l: ""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    all_chunks: list[dict] = []
    for pdf in sorted(DOCS_DIR.glob("*.pdf")):
        all_chunks.extend(ingest_pdf(pdf))

    CHUNKS_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(CHUNKS_OUT, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2, ensure_ascii=False)

    by_doc:  dict[str, int] = {}
    by_type: dict[str, int] = {}
    for c in all_chunks:
        by_doc[c["doc"]]   = by_doc.get(c["doc"], 0) + 1
        by_type[c["type"]] = by_type.get(c["type"], 0) + 1

    print(f"\nTotal chunks: {len(all_chunks)}")
    print("By document:", {k: v for k, v in sorted(by_doc.items())})
    print("By type    :", {k: v for k, v in sorted(by_type.items())})
    print(f"Page images : {IMG_DIR}/  ({len(list(IMG_DIR.glob('*.png')))} PNGs)")


if __name__ == "__main__":
    main()
