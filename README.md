# WareDoc — Warehouse Document Intelligence

A document Q&A system for wholesale soap & personal care distributors. Ingests PDF catalogs, policy documents, and quarterly reports; answers natural language questions with citations, calculations, and page images.

---

## What This Does

| Feature | Details |
|---|---|
| **PDF Ingestion** | 5 PDFs via PyMuPDF (native text) + pdfplumber (tables). VLM used only for scanned/chart pages |
| **Retrieval** | BM25 (`rank_bm25`) + sentence-transformer embeddings (`all-MiniLM-L6-v2`) |
| **Q&A** | Gemini API → structured JSON: `answer`, `claims` (with `chunk_ids`), `calculation`, `not_found` |
| **Citations** | `chunk_id` → `document`, `page`, `section` mapping in code |
| **Calculations** | Arithmetic expression in answer JSON, evaluated by a restricted Python `ast` evaluator |
| **UI** | Streamlit app with Q&A tab (answers, citations, page image) and Order Desk tab |
| **Eval** | 25-question eval with BM25 scoring, per-category accuracy, cached answers for re-runs |

---

## Tech Stack

- **VLM / LLM:** [Gemini API](https://ai.google.dev/) via `google-genai` SDK
- **PDF parsing:** [PyMuPDF](https://pymupdf.readthedocs.io/) (text), [pdfplumber](https://github.com/jsvine/pdfplumber) (tables)
- **Retrieval:** [rank_bm25](https://github.com/dorianbrown/rank_bm25) + [sentence-transformers](https://www.sbert.net/)
- **Fuzzy matching:** [rapidfuzz](https://github.com/maxbachmann/RapidFuzz)
- **UI:** [Streamlit](https://streamlit.io/)
- **Data:** pandas, pyyaml, PyYAML

---

## Install

```bash
git clone <repo-url>
cd WareDoc

# Create and activate a virtual environment (recommended)
python -m venv venv
venv\Scripts\activate  # Windows

pip install -r requirements.txt
```

---

## Setup

```bash
# Copy the example env file and fill in your key
copy .env.example .env
# Edit .env and set: API_KEY=your-google-ai-studio-key
```

Get a free API key at [Google AI Studio](https://aistudio.google.com/apikey).

---

## Run Commands

### 1. Ingest PDFs (build chunks.json + page images)

```bash
python -m src.ingest
```

> VLM is called only for `catalog_C.pdf` (all pages — scanned) and two chart pages
> (`quarterly_report.pdf` p3, `catalog_B.pdf` p26). Everything else uses native text.

### 2. Ask a question (CLI)

```bash
python ask.py "What is the return policy for damaged goods?"
python ask.py "Calculate total cost for 120 boxes of SKU1000 with 18% GST"
```

### 3. Run the Streamlit UI

```bash
streamlit run app.py
```

Opens at http://localhost:8501 with two tabs:
- **Q&A Assistant** — ask questions, see citations and page images
- **Order Desk** — paste order text, check stock, compute bill

### 4. Run the evaluation

```bash
# Subset (~12 questions, every category, faster):
python eval/run_eval.py

# Full 25-question eval:
python eval/run_eval.py --full
```

Results written to `eval/results.md`. Answers are cached in `eval/answer_cache.json` — re-runs are instant.

---

## How to Reproduce the Eval

```bash
# Delete cache to force fresh answers
del eval\answer_cache.json

# Run full eval
python eval/run_eval.py --full
```

Scoring is keyword + number overlap (threshold 0.4). See `eval/run_eval.py` for `_score_answer()`.

---

## VLM Robustness

`src/vlm.py` implements:
- **Model fallback:** tries `gemini-flash-latest` first, then discovers other flash models from the API
- **Retry:** 6 retries on 503/429 with exponential backoff (2, 4, 8, 16, 30, 30 s) + jitter
- **Disk cache:** every successful result cached in `data/vlm_cache/` keyed by `sha256(prompt + image bytes)`
- **No key exposure:** API key is never printed, logged, or surfaced in the UI

---

## Declared Resources

| Resource | Source | Synthetic? |
|---|---|---|
| Product list | [Open Beauty Facts](https://world.openbeautyfacts.org/) | ❌ Real (CC0) |
| Prices | Generated (`generate_pdfs.py`) | ✅ Synthetic |
| Stock / inventory | Generated (`generate_data.py`) | ✅ Synthetic |
| TFM values | Generated | ✅ Synthetic |
| Quarterly sales report | Generated | ✅ Synthetic |

---

## Scope Note — Implemented vs. Not Implemented

### ✅ Implemented
- Robust VLM with retry + disk cache (`src/vlm.py`)
- PDF ingestion: native text + tables; VLM only for scan/chart pages (`src/ingest.py`)
- BM25 + embedding retrieval (`ask.py`)
- Structured JSON answers with claims, citations, calculation (`ask.py`)
- Restricted arithmetic evaluator via `ast` (`ask.py`)
- Streamlit UI: Q&A tab with citations, page image, calculation display (`app.py`)
- Order Desk tab: VLM extraction, rapidfuzz SKU matching, inventory check, substitute suggestion, bill computation (`app.py`)
- Eval runner with caching, per-category scoring, subset mode (`eval/run_eval.py`)

### ❌ Not Implemented / Skipped
- Sentence-transformer embeddings may fall back to BM25-only if the download is slow (handled gracefully)
- Order Desk requires `rapidfuzz`; if not installed, matching falls back to exact SKU lookup
- Full 25-question eval may not have run if time was insufficient (see `eval/results.md` for actuals)

---

## Files

```
WareDoc/
├── src/
│   ├── vlm.py          # VLM wrapper (retry, cache, fallback)
│   ├── ingest.py       # PDF ingestion pipeline
│   └── gemini_config.py# API key helper
├── ask.py              # QA engine (retrieval + answer + citations)
├── app.py              # Streamlit UI
├── eval/
│   ├── questions.csv   # 25 evaluation questions
│   ├── run_eval.py     # Eval runner
│   └── results.md      # Eval output
├── data/
│   ├── chunks.json     # Ingested chunks
│   ├── page_images/    # Rendered PDF pages (PNG)
│   ├── vlm_cache/      # VLM response cache
│   ├── docs/           # Source PDFs
│   ├── products.csv    # Product catalog
│   ├── inventory.csv   # Stock levels
│   └── policy.yaml     # Trade policy
├── requirements.txt
├── .env.example        # Template — copy to .env
└── .gitignore          # .env and CSV excluded
```

---

## .gitignore Note

Both `.env` (real API key) and the raw Open Beauty Facts CSV (`en.openbeautyfacts.org.products.csv.gz`) are excluded from version control via `.gitignore`.
