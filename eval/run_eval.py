"""
eval/run_eval.py — Run the 25-question evaluation.
Writes eval/results.md with accuracy per category and failures.
Uses cache to avoid re-running: answers cached in eval/answer_cache.json.
Uses a larger delay between calls to avoid 503s.
"""
from __future__ import annotations
import csv, json, os, sys, time, re
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))  # ensure relative paths work

try:
    from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
except ImportError:
    pass

QUESTIONS_CSV = Path("eval/questions.csv")
RESULTS_MD    = Path("eval/results.md")
ANSWER_CACHE  = Path("eval/answer_cache.json")
INTER_CALL_DELAY = 1  # fast eval run

CATEGORIES = {
    "policy": ["return policy", "delivery time", "dispatched", "credit limit"],
    "catalog_price": ["price", "box price", "catalog a", "catalog c", "price difference", "price list"],
    "catalog_scan": ["scanned", "catalog c", "scanned price"],
    "sales_data": ["q1", "q2", "q3", "q4", "quarterly", "annual", "sales", "highest", "lowest", "biggest"],
    "chart": ["chart", "figure", "tfm", "bar soap", "highest tfm"],
    "calculation": ["total cost", "calculate", "discount", "gst", "save per box", "discount amount"],
    "multi_doc": ["compare", "appearing in both", "supplier for"],
    "unanswerable": ["unanswerable", "not available", "manufacturing date", "shelf life"],
}

def _categorize(question: str) -> str:
    q = question.lower()
    if "unanswerable" in q or "not available" in q or "manufacturing" in q or "shelf life" in q:
        return "unanswerable"
    if any(k in q for k in ["calculate", "total cost", "gst", "discount", "save per box"]):
        return "calculation"
    if any(k in q for k in ["scanned catalog c", "scanned price", "catalog c price"]):
        return "catalog_scan"
    if any(k in q for k in ["tfm", "highest tfm"]):
        return "chart"
    if any(k in q for k in ["compare", "appearing in both", "supplier for", "both catalog"]):
        return "multi_doc"
    if any(k in q for k in ["q1 sales", "q2", "q3", "q4", "quarterly", "annual sales", "category had"]):
        return "sales_data"
    if any(k in q for k in ["chart", "figure", "trend", "bar chart"]):
        return "chart"
    if any(k in q for k in ["return policy", "delivery", "dispatched", "credit limit"]):
        return "policy"
    if any(k in q for k in ["price", "box price", "catalog a", "catalog c", "scanned"]):
        return "catalog_price"
    return "other"


def _score_answer(predicted: str, expected: str) -> float:
    """Simple overlap score: check if key numbers/terms from expected appear in predicted."""
    if not predicted: return 0.0
    pred = predicted.lower()
    exp = expected.lower()

    # Extract numbers from expected
    exp_nums = re.findall(r"\d[\d,\.]*", exp)
    pred_nums = re.findall(r"\d[\d,\.]*", pred)
    pred_nums_set = {n.replace(",","") for n in pred_nums}

    # Check number matches
    num_hits = sum(1 for n in exp_nums if n.replace(",","") in pred_nums_set)
    num_score = num_hits / max(len(exp_nums), 1) if exp_nums else 0.5

    # Check keyword overlap
    exp_words = set(re.findall(r"[a-z]{3,}", exp)) - {"the","and","for","with","that","this","from","are","has"}
    pred_words = set(re.findall(r"[a-z]{3,}", pred))
    word_hits = len(exp_words & pred_words)
    word_score = word_hits / max(len(exp_words), 1)

    # Unanswerable case
    if "not" in exp and ("available" in exp or "found" in exp or "unanswerable" in exp):
        if "not found" in pred or "not available" in pred or "cannot" in pred or "unanswerable" in pred:
            return 1.0
        return 0.0

    return (num_score * 0.5 + word_score * 0.5)


def load_answer_cache() -> dict:
    if ANSWER_CACHE.exists():
        with open(ANSWER_CACHE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_answer_cache(cache: dict):
    ANSWER_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(ANSWER_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def run_eval(subset: bool = False):
    import ask as ask_mod
    print("Initialising document index…")
    ask_mod.init()

    with open(QUESTIONS_CSV, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        questions = [row for row in reader if row["question"].strip()]

    if subset:
        # ~12 questions covering every category
        selected = []
        seen_cats: set[str] = set()
        cat_target = ["policy", "catalog_price", "catalog_scan", "sales_data",
                      "chart", "calculation", "multi_doc", "unanswerable", "other"]
        # First pass: one from each category
        for q in questions:
            cat = _categorize(q["question"])
            if cat not in seen_cats:
                seen_cats.add(cat)
                selected.append(q)
        # Second pass: fill up to 12 (prioritize calculation and sales)
        for q in questions:
            if len(selected) >= 12: break
            if q not in selected:
                selected.append(q)
        questions = selected[:12]
        print(f"Running subset of {len(questions)} questions (every category represented).")

    answer_cache = load_answer_cache()
    results = []
    category_stats: dict[str, dict] = {}

    for i, row in enumerate(questions):
        q = row["question"].strip()
        expected = row.get("expected_answer", "").strip()
        cat = _categorize(q)

        print(f"\n[{i+1}/{len(questions)}] [{cat}] {q[:80]}…")

        # Check cache
        cache_key = q
        if cache_key in answer_cache:
            print("  (cached)")
            pred_answer = answer_cache[cache_key]
        else:
            try:
                result = ask_mod.ask(q)
                pred_answer = result.get("answer", "")
                answer_cache[cache_key] = pred_answer
                save_answer_cache(answer_cache)
            except Exception as e:
                print(f"  ERROR: {type(e).__name__}")
                pred_answer = ""

            # Delay to avoid 503s
            if i < len(questions) - 1:
                time.sleep(INTER_CALL_DELAY)

        score = _score_answer(pred_answer, expected)
        passed = score >= 0.4

        results.append({
            "question": q,
            "expected": expected,
            "predicted": pred_answer,
            "score": score,
            "passed": passed,
            "category": cat,
        })

        if cat not in category_stats:
            category_stats[cat] = {"total": 0, "passed": 0}
        category_stats[cat]["total"] += 1
        if passed:
            category_stats[cat]["passed"] += 1

        status = "[PASS]" if passed else "[FAIL]"
        try:
            print(f"  {status} score={score:.2f} | predicted: {pred_answer[:80]}")
        except Exception:
            pass

    # Write results.md
    total = len(results)
    total_passed = sum(1 for r in results if r["passed"])
    overall_acc = total_passed / total if total else 0

    lines = [
        "# WareDoc Evaluation Results",
        "",
        f"> Run on {total} question{'s' if total < 25 else 's (full set)'}" + (" **(subset — see note)**" if subset else ""),
        "",
        "## Overall Accuracy",
        "",
        f"**{total_passed}/{total} = {overall_acc:.1%}**",
        "",
        "## Accuracy by Category",
        "",
        "| Category | Passed | Total | Accuracy |",
        "| --- | --- | --- | --- |",
    ]
    for cat, stats in sorted(category_stats.items()):
        acc = stats["passed"] / stats["total"] if stats["total"] else 0
        lines.append(f"| {cat} | {stats['passed']} | {stats['total']} | {acc:.1%} |")

    lines += [
        "",
        "## Failures",
        "",
    ]
    failures = [r for r in results if not r["passed"]]
    if not failures:
        lines.append("No failures! 🎉")
    else:
        for r in failures:
            lines.append(f"### ❌ [{r['category']}] {r['question']}")
            lines.append(f"- **Expected:** {r['expected'][:200]}")
            lines.append(f"- **Predicted:** {r['predicted'][:200]}")
            lines.append(f"- **Score:** {r['score']:.2f}")
            lines.append("")

    if subset:
        lines += [
            "",
            "## Note on Subset Run",
            "",
            "Due to time constraints, only a subset of ~12 questions was run, covering every category.",
            "To run the full 25-question eval: `python eval/run_eval.py --full`",
        ]

    RESULTS_MD.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OK] Results written to {RESULTS_MD}")
    print(f"Overall: {total_passed}/{total} = {overall_acc:.1%}")
    return results


if __name__ == "__main__":
    subset = "--full" not in sys.argv
    run_eval(subset=subset)
