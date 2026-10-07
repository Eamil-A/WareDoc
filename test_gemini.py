"""
Test script: verify the Gemini client can be initialised safely.

Run:  python test_gemini.py

This script:
  1. Confirms .env exists and is git-ignored.
  2. Confirms no API key is hardcoded in source.
  3. Attempts to initialise the Gemini client.
  4. Prints only safe status messages (never the key).
"""
import os
import sys

# Ensure src/ is importable
sys.path.insert(0, os.path.dirname(__file__))

from src.gemini_config import test_connection, get_api_key, GeminiConfigError


def check_env_file():
    """Verify .env exists and contains the key placeholder."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        print("  [FAIL] .env file not found")
        return False
    with open(env_path, "r") as f:
        content = f.read()
    if "GEMINI_API_KEY" not in content:
        print("  [FAIL] GEMINI_API_KEY not found in .env")
        return False
    print("  [OK] .env file exists with GEMINI_API_KEY")
    return True


def check_gitignore():
    """Verify .env is in .gitignore."""
    gitignore_path = os.path.join(os.path.dirname(__file__), ".gitignore")
    if not os.path.exists(gitignore_path):
        print("  [FAIL] .gitignore not found")
        return False
    with open(gitignore_path, "r") as f:
        content = f.read()
    if ".env" not in content:
        print("  [FAIL] .env not in .gitignore")
        return False
    print("  [OK] .env is in .gitignore")
    return True


def check_no_hardcoded_key():
    """Scan source files for anything that looks like a hardcoded API key."""
    import re
    key_pattern = re.compile(r"AIza[0-9A-Za-z\-_]{35}")
    src_dir = os.path.join(os.path.dirname(__file__), "src")
    files_to_check = [
        os.path.join(src_dir, "gemini_config.py"),
        __file__,
    ]
    for filepath in files_to_check:
        if not os.path.exists(filepath):
            continue
        with open(filepath, "r") as f:
            content = f.read()
        matches = key_pattern.findall(content)
        if matches:
            print(f"  [FAIL] Hardcoded key found in {filepath}")
            return False
    print("  [OK] No hardcoded API keys in source files")
    return True


def check_key_not_printed():
    """Verify the key is never printed by the config module."""
    import io
    import contextlib

    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            try:
                get_api_key()
            except GeminiConfigError:
                pass  # expected if key not set yet
    except Exception:
        pass
    output = captured.getvalue()
    key = os.environ.get("GEMINI_API_KEY", "")
    if key and key in output:
        print("  [FAIL] API key was printed to stdout")
        return False
    print("  [OK] API key is not printed to stdout")
    return True


def main():
    print("=== Gemini Setup Verification ===\n")

    all_ok = True

    print("1. .env file:")
    all_ok &= check_env_file()

    print("2. .gitignore:")
    all_ok &= check_gitignore()

    print("3. No hardcoded keys:")
    all_ok &= check_no_hardcoded_key()

    print("4. Key not printed:")
    all_ok &= check_key_not_printed()

    print("5. Client initialisation:")
    # Only test if the key is actually set
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print("  [SKIP] GEMINI_API_KEY not set yet — add your key to .env and re-run")
        print("         (This is expected on first setup before you paste your key)")
    else:
        ok = test_connection()
        all_ok &= ok

    print()
    if all_ok:
        print("=== ALL CHECKS PASSED ===")
    else:
        print("=== SOME CHECKS FAILED — review above ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
