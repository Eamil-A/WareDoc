"""
Gemini client configuration helper.

Loads GEMINI_API_KEY from the environment (or a local .env file) and provides
a factory function that initialises the Google GenAI client.

Security rules:
  - The API key is never printed, logged, or surfaced in the UI.
  - The key is read from the environment variable GEMINI_API_KEY.
  - No key is hardcoded anywhere in source code.
"""
from __future__ import annotations

import os
import sys
import logging
from typing import Optional

logger = logging.getLogger("waredoc.gemini")

# Attempt to load .env if python-dotenv is available (dev convenience).
try:
    from dotenv import load_dotenv

    # Load .env from the project root (parent of src/)
    _env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    load_dotenv(_env_path)
except ImportError:
    pass  # dotenv not installed — rely on real environment variables


# --- Constants ---------------------------------------------------------------
DEFAULT_MODEL = "gemini-2.0-flash"  # free tier model


class GeminiConfigError(RuntimeError):
    """Raised when the Gemini API key is missing or invalid."""


def get_api_key() -> str:
    """
    Return the Gemini API key from the environment.

    Raises GeminiConfigError if the key is missing or empty.
    The key is never logged or printed.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise GeminiConfigError(
            "GEMINI_API_KEY is not set. "
            "Add it to your .env file (GEMINI_API_KEY=your-key-here) "
            "or export it as an environment variable."
        )
    return key


def get_client():
    """
    Initialise and return a Google GenAI Client.

    Uses the current google-genai SDK.  The API key is read from the
    GEMINI_API_KEY environment variable and is never exposed.

    Returns:
        google.genai.Client
    """
    from google import genai  # imported lazily so the module loads without the SDK

    api_key = get_api_key()

    # The new google-genai SDK accepts api_key directly in the Client.
    client = genai.Client(api_key=api_key)
    logger.debug("Gemini client initialised successfully.")
    return client


def get_model_name() -> str:
    """Return the default model identifier for inference."""
    return DEFAULT_MODEL


def test_connection() -> bool:
    """
    Attempt to initialise the Gemini client without revealing the key.

    Returns True on success, False on failure.  Prints only a generic
    success/failure message — never the key or full exception details.
    """
    try:
        client = get_client()
        # A lightweight call to verify the key works without exposing it.
        # We list models — a read-only, no-cost operation on the free tier.
        models = client.models.list()
        _ = list(models)
        print("[gemini_config] OK — Gemini client initialised and key verified.")
        return True
    except GeminiConfigError:
        print("[gemini_config] FAIL — GEMINI_API_KEY is not set.")
        return False
    except Exception as exc:  # noqa: BLE001
        # Log only the exception class name, never the message (which might
        # contain the key in some SDK error paths).
        print(f"[gemini_config] FAIL — {type(exc).__name__}")
        logger.debug("Connection test failed: %s", type(exc).__name__)
        return False


if __name__ == "__main__":
    # Standalone test: run `python -m src.gemini_config` to verify the setup.
    success = test_connection()
    sys.exit(0 if success else 1)
