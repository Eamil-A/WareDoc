"""
VLM wrapper — provider-agnostic, disk-cached.
    vlm(prompt, images=[]) -> str
Tries models in order, retries on 503/429 with exponential backoff + jitter (6 retries).
Caches every successful result on disk keyed by prompt+image hash.
API key read from API_KEY env var; never printed.
"""
from __future__ import annotations
import hashlib, io, logging, os, time, random
from pathlib import Path
from typing import Union

logger = logging.getLogger("waredoc.vlm")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PROVIDER   = os.environ.get("PROVIDER", "gemini").lower()
API_KEY    = (os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY") or "").strip()
CACHE_DIR  = Path("data/vlm_cache")

# Retry schedule: 6 retries with exponential backoff (2,4,8,16,30,30s) + jitter
RETRY_WAITS = [2, 4, 8, 16, 30, 30]

ImageInput = Union[str, Path, bytes, "object"]

# ── helpers ────────────────────────────────────────────────────────────────────
def _img_bytes(img: ImageInput) -> bytes:
    if isinstance(img, (bytes, bytearray)): return bytes(img)
    if isinstance(img, (str, Path)):
        with open(img, "rb") as f: return f.read()
    buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()

def _cache_key(prompt: str, images: list) -> str:
    """Cache key = sha256(prompt + all image bytes). Model-agnostic so hits work across fallbacks."""
    h = hashlib.sha256()
    h.update(prompt.encode())
    for img in images:
        h.update(_img_bytes(img))
    return h.hexdigest()

def _get_cached(key: str) -> str | None:
    p = CACHE_DIR / f"{key}.txt"
    return p.read_text("utf-8") if p.exists() else None

def _set_cached(key: str, text: str):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}.txt").write_text(text, "utf-8")


def _get_gemini_models() -> list[str]:
    """
    Build model list: gemini-flash-latest first (known to work for this key),
    then other flash/text models discovered from the API.
    """
    # Verified models for this API key — gemini-3.1-flash-lite confirmed working
    static_models = [
        "gemini-3.1-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-flash-latest",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
        "gemini-3.7-flash",
        "gemini-3.1-flash-lite-preview",
    ]
    # Try to discover models from API and prepend any flash model not in static list
    if API_KEY:
        try:
            import httpx
            from google import genai
            from google.genai.types import HttpOptions
            http_client = httpx.Client(verify=False, timeout=30.0)
            client = genai.Client(api_key=API_KEY,
                                  http_options=HttpOptions(httpx_client=http_client))
            discovered = []
            for m in client.models.list():
                name = (getattr(m, "name", "") or "").replace("models/", "")
                if "flash" in name.lower() and name not in static_models:
                    discovered.append(name)
            static_models = static_models + discovered
        except Exception:
            pass

    # Deduplicate preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for m in static_models:
        if m not in seen:
            seen.add(m)
            ordered.append(m)
    return ordered


# ── public ────────────────────────────────────────────────────────────────────
def vlm(prompt: str, images: list | None = None) -> str:
    """Call VLM with fallback models and disk cache. Never exposes the API key."""
    images = images or []

    if PROVIDER != "gemini":
        return _call_other(prompt, images)

    # Check cache first (model-agnostic key)
    key = _cache_key(prompt, images)
    cached = _get_cached(key)
    if cached is not None:
        logger.debug("cache hit %s", key[:10])
        return cached

    if not API_KEY:
        raise RuntimeError("API_KEY not set in .env")

    models = _get_gemini_models()
    for model in models:
        result = _try_gemini(prompt, images, model)
        if result is not None:
            _set_cached(key, result)
            return result
        logger.info("model %s failed, trying next", model)

    raise RuntimeError("All Gemini models exhausted after retries.")


def _try_gemini(prompt: str, images: list, model: str) -> str | None:
    """Try one model with up to 6 retries on 503/429. Returns None → try next model."""
    try:
        import httpx
        from google import genai
        from google.genai import types
        from google.genai.types import HttpOptions
    except ImportError as e:
        logger.error("google-genai not installed: %s", type(e).__name__)
        return None

    http_client = httpx.Client(verify=False, timeout=120.0)
    client = genai.Client(api_key=API_KEY,
                          http_options=HttpOptions(httpx_client=http_client))

    contents: list = [prompt]
    for img in images:
        raw = _img_bytes(img)
        contents.append(types.Part.from_bytes(data=raw, mime_type="image/png"))

    for attempt, wait in enumerate(RETRY_WAITS):
        try:
            resp = client.models.generate_content(model=model, contents=contents)
            return resp.text
        except Exception as exc:
            msg = str(exc)
            # Transient errors → retry with backoff
            if any(c in msg for c in ("503", "429", "UNAVAILABLE",
                                       "RESOURCE_EXHAUSTED", "high demand",
                                       "quota", "overloaded")):
                jitter = random.uniform(0, wait * 0.3)
                total = wait + jitter
                logger.warning("model=%s attempt=%d/%d transient error, retry in %.1fs",
                               model, attempt + 1, len(RETRY_WAITS), total)
                time.sleep(total)
                continue
            # Hard errors → skip to next model immediately
            if any(c in msg for c in ("404", "NOT_FOUND", "no longer available",
                                       "INVALID_ARGUMENT", "is not supported",
                                       "does not exist")):
                logger.debug("model=%s not available: %s", model, type(exc).__name__)
                return None
            logger.warning("VLM %s unexpected error: %s", model, type(exc).__name__)
            return None
    logger.warning("model=%s exhausted %d retries, trying next model", model, len(RETRY_WAITS))
    return None


def _call_other(prompt: str, images: list) -> str:
    """Stub for anthropic/openai — not needed for hackathon."""
    raise NotImplementedError(f"Provider {PROVIDER!r} not wired up.")
