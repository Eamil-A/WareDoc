"""
ask.py — Query engine for WareDoc.
Retrieval: BM25 (rank_bm25) + optional sentence-transformers embeddings.
Answer: Gemini API with structured JSON output.
"""
from __future__ import annotations
import json, logging, math, os, re, ast, operator
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("waredoc.ask")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

CHUNKS_PATH = Path("data/chunks.json")
API_KEY = (os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY") or "").strip()

# ── Load chunks ────────────────────────────────────────────────────────────────
def load_chunks() -> list[dict]:
    with open(CHUNKS_PATH, encoding="utf-8") as f:
        return json.load(f)

# ── BM25 retrieval ─────────────────────────────────────────────────────────────
def build_bm25(chunks: list[dict]):
    from rank_bm25 import BM25Okapi
    tokenized = [_tokenize(c["text"]) for c in chunks]
    return BM25Okapi(tokenized)

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9À-ÿ']+", text.lower())

def bm25_search(bm25, chunks: list[dict], query: str, top_k: int = 8) -> list[dict]:
    tokens = _tokenize(query)
    scores = bm25.get_scores(tokens)
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    results = []
    for idx, score in ranked[:top_k]:
        if score > 0:
            c = dict(chunks[idx])
            c["_score"] = score
            results.append(c)
    return results

# ── Embedding retrieval (optional) ─────────────────────────────────────────────
_embed_model = None
_embed_matrix = None
_embed_chunks = None

def _load_embeddings(chunks: list[dict]):
    global _embed_model, _embed_matrix, _embed_chunks
    if _embed_model is not None:
        return
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
        logger.info("Loading sentence-transformers model...")
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        texts = [c["text"][:512] for c in chunks]
        _embed_matrix = _embed_model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        _embed_chunks = chunks
        logger.info("Embeddings built for %d chunks", len(chunks))
    except Exception as e:
        logger.warning("Embeddings unavailable: %s", type(e).__name__)
        _embed_model = None

def embed_search(chunks: list[dict], query: str, top_k: int = 5) -> list[dict]:
    global _embed_model, _embed_matrix, _embed_chunks
    if _embed_model is None:
        return []
    try:
        import numpy as np
        q_vec = _embed_model.encode([query], normalize_embeddings=True)[0]
        scores = _embed_matrix @ q_vec
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results = []
        for idx, score in ranked[:top_k]:
            c = dict(_embed_chunks[idx])
            c["_score_emb"] = float(score)
            results.append(c)
        return results
    except Exception:
        return []

def hybrid_search(bm25, chunks: list[dict], query: str, top_k: int = 8) -> list[dict]:
    """Combine BM25 + embedding results, deduplicated by chunk_id."""
    bm_results = bm25_search(bm25, chunks, query, top_k=top_k)
    emb_results = embed_search(chunks, query, top_k=5)

    seen: set[str] = set()
    combined: list[dict] = []
    for c in bm_results + emb_results:
        cid = c["chunk_id"]
        if cid not in seen:
            seen.add(cid)
            combined.append(c)
    return combined[:top_k]

# ── Chunk metadata resolver ─────────────────────────────────────────────────────
def resolve_chunk_ids(chunk_ids: list[str], chunks_by_id: dict) -> list[dict]:
    out = []
    for cid in chunk_ids:
        c = chunks_by_id.get(cid)
        if c:
            out.append({
                "chunk_id": cid,
                "document": c["doc"] + ".pdf",
                "page": c["page"],
                "section": c.get("section", ""),
            })
    return out

# ── Restricted arithmetic evaluator ────────────────────────────────────────────
_SAFE_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

def safe_eval(expr: str) -> float | None:
    """Evaluate a simple arithmetic expression safely (no builtins, no variables)."""
    if not expr or not expr.strip():
        return None
    try:
        tree = ast.parse(expr.strip(), mode="eval")
        return _eval_node(tree.body)
    except Exception:
        return None

def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("non-numeric constant")
    if isinstance(node, ast.BinOp):
        op_fn = _SAFE_OPS.get(type(node.op))
        if op_fn is None: raise ValueError("unsupported op")
        return op_fn(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op_fn = _SAFE_OPS.get(type(node.op))
        if op_fn is None: raise ValueError("unsupported unary op")
        return op_fn(_eval_node(node.operand))
    raise ValueError(f"unsupported node {type(node)}")

# ── Gemini answer generation ────────────────────────────────────────────────────
ANSWER_SYSTEM = """You are a warehouse document assistant. Answer ONLY from the provided context chunks.

Return a JSON object with these exact keys:
{
  "answer": "concise answer in 1-3 sentences",
  "claims": [{"text": "one factual claim", "chunk_ids": ["chunk_id1"]}],
  "calculation": "Python arithmetic expression or empty string if not applicable",
  "not_found": true/false
}

Rules:
- If the answer cannot be found, set not_found=true and answer="Not found in documents."
- chunk_ids must reference actual chunk_id values from the context.
- calculation must be a plain arithmetic expression (e.g., "1075 * 120 * 0.92 * 1.18") evaluable by Python eval. No variables.
- Never include the API key or sensitive info.
"""

def answer_question(question: str, context_chunks: list[dict],
                    image_path: str | None = None) -> dict:
    """Call Gemini to answer a question from context chunks. Returns parsed JSON dict."""
    context_text = "\n\n".join(
        f"[{c['chunk_id']}] (doc={c['doc']}, page={c['page']}, section={c.get('section','')})\n{c['text'][:800]}"
        for c in context_chunks
    )

    prompt = f"""{ANSWER_SYSTEM}

CONTEXT:
{context_text}

QUESTION: {question}

JSON answer:"""

    try:
        import httpx
        from google import genai
        from google.genai.types import HttpOptions
        http_client = httpx.Client(verify=False, timeout=60.0)
        client = genai.Client(api_key=API_KEY,
                              http_options=HttpOptions(httpx_client=http_client))

        contents: list = [prompt]
        if image_path and Path(image_path).exists():
            from google.genai import types
            contents.append(types.Part.from_bytes(
                data=Path(image_path).read_bytes(), mime_type="image/png"))

        models_to_try = [
            "gemini-3.1-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-flash-latest",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
        ]
        retry_waits = [3, 8]  # 2 attempts per model then try next (eval speed)
        for model in models_to_try:
            for attempt, wait in enumerate(retry_waits):
                try:
                    resp = client.models.generate_content(model=model, contents=contents)
                    raw = resp.text or ""
                    return _parse_json_answer(raw)
                except Exception as e:
                    import time as _time, random as _rand
                    msg = str(e)
                    if any(c in msg for c in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")):
                        jitter = _rand.uniform(0, wait * 0.3)
                        logger.warning("model=%s attempt=%d, retrying in %.1fs", model, attempt + 1, wait + jitter)
                        _time.sleep(wait + jitter)
                        continue
                    if any(c in msg for c in ("404", "NOT_FOUND")):
                        break  # next model
                    logger.warning("answer model %s error: %s", model, type(e).__name__)
                    break  # next model
        return {"answer": "VLM unavailable. Falling back to text chunks.",
                "claims": [], "calculation": "", "not_found": True}
    except Exception as e:
        logger.error("answer_question failed: %s", type(e).__name__)
        return {"answer": "Error generating answer.",
                "claims": [], "calculation": "", "not_found": True}

def _parse_json_answer(raw: str) -> dict:
    """Extract JSON from LLM output (may have ```json fences)."""
    # Strip markdown fences
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE)
    try:
        return json.loads(raw.strip())
    except Exception:
        # Try to extract first {...} block
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {"answer": raw.strip(), "claims": [], "calculation": "", "not_found": False}

# ── Image-page detection ────────────────────────────────────────────────────────
_CHART_KEYWORDS = ["chart", "graph", "figure", "trend", "quarterly", "q1", "q2", "q3", "q4",
                   "tfm", "scan", "catalog c", "catalogue c", "scanned"]

def _needs_image(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in _CHART_KEYWORDS)

def _find_page_image(chunks: list[dict], doc: str | None = None,
                     page: int | None = None) -> str | None:
    if doc and page:
        doc_stem = doc.replace(".pdf", "")
        p = Path(f"data/page_images/{doc_stem}_p{page}.png")
        if p.exists(): return str(p)
    return None

# ── Main ask function ──────────────────────────────────────────────────────────
_bm25 = None
_chunks: list[dict] = []
_chunks_by_id: dict = {}

def init():
    global _bm25, _chunks, _chunks_by_id
    _chunks = load_chunks()
    _chunks_by_id = {c["chunk_id"]: c for c in _chunks}
    _bm25 = build_bm25(_chunks)
    try:
        _load_embeddings(_chunks)
    except Exception:
        pass

def ask(question: str) -> dict:
    """Full pipeline: retrieve → answer → resolve citations → evaluate calculation."""
    global _bm25, _chunks, _chunks_by_id
    if _bm25 is None:
        init()

    top_chunks = hybrid_search(_bm25, _chunks, question, top_k=8)

    # Determine if image is needed
    image_path = None
    if _needs_image(question):
        # Pick image from best matching chunk
        for c in top_chunks:
            ip = c.get("image_path")
            if ip and Path(ip).exists():
                image_path = ip
                break

    result = answer_question(question, top_chunks, image_path)

    # Resolve chunk_ids in claims
    all_chunk_ids: list[str] = []
    for claim in result.get("claims", []):
        all_chunk_ids.extend(claim.get("chunk_ids", []))
    all_chunk_ids = list(dict.fromkeys(all_chunk_ids))  # deduplicate

    result["citations"] = resolve_chunk_ids(all_chunk_ids, _chunks_by_id)
    result["retrieved_chunks"] = [c["chunk_id"] for c in top_chunks]

    # Execute calculation
    calc = result.get("calculation", "")
    calc_result = None
    if calc:
        calc_result = safe_eval(calc)
    result["calculation_result"] = calc_result

    # Image used
    result["image_path"] = image_path

    return result


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What is the return policy?"
    init()
    out = ask(q)
    print(json.dumps(out, indent=2, ensure_ascii=False))
