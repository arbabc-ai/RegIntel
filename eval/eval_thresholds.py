"""Eval harness for the Phase 2 warehouse: does extraction reproduce values
that are actually in the source text?

Run: python -m eval.eval_thresholds
Run as a CI gate: python -m eval.eval_thresholds --gate   (exits 1 on
regression below the threshold below; --gate only changes the exit code,
not the output, so `> eval/thresholds_results.md` still works.)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from src.warehouse import lookup_threshold

CHECKS_PATH = Path("eval/thresholds_questions.yaml")

# CI gate threshold. This eval currently scores 4/5 (the 2.5% conservation
# buffer passage ranks outside the extraction candidate cap; see README), so
# 0.8 is the current score, not a margin below it: any further miss fails the
# gate. Deliberate -- lowering it would mask a real regression. Extraction is
# itself an LLM pass (scripts/extract_thresholds.py), so coverage can vary
# run to run; if the gate flakes, raise the candidate cap before the threshold.
CHECKS_MIN_RATE = 0.8  # 4/5


def main() -> None:
    parser = argparse.ArgumentParser(description="Warehouse extraction eval (Phase 2).")
    parser.add_argument("--gate", action="store_true",
                         help="Exit 1 if results fall below the CI threshold.")
    args = parser.parse_args()

    checks = yaml.safe_load(CHECKS_PATH.read_text())["checks"]

    results = []
    for c in checks:
        rows = lookup_threshold(c["query"])
        match = next(
            (
                r for r in rows
                if not r["is_formula"]  # a formula row has no flat value to compare
                and r["unit"] == c["expected_unit"]
                and abs(r["value"] - c["expected_value"]) <= c["tolerance"]
            ),
            None,
        )
        results.append({"query": c["query"], "expected": c["expected_value"],
                         "unit": c["expected_unit"], "found_rows": len(rows),
                         "match": match})

    hits = sum(1 for r in results if r["match"])
    print("# Warehouse Extraction Eval\n")
    print(f"- **Checks:** {hits}/{len(results)} matched a ground-truth value found directly in the raw text\n")
    print("| Query | Expected | Rows found | Match | Citation |")
    print("|---|---|---|---|---|")
    for r in results:
        unit_str = "%" if r["unit"] == "percent" else f" {r['unit']}"
        expected_str = f"{r['expected']}{unit_str}"
        if r["match"]:
            m = r["match"]
            citation = f"{m['chunk_source']}::chunk-{m['chunk_index']:04d}"
            print(f"| {r['query']} | {expected_str} | {r['found_rows']} | ✅ | {citation} |")
        else:
            print(f"| {r['query']} | {expected_str} | {r['found_rows']} | ❌ | — |")

    if args.gate:
        rate = hits / len(results) if results else 1.0
        if rate < CHECKS_MIN_RATE:
            print(f"\nGATE FAILED: {hits}/{len(results)} = {rate:.0%} < {CHECKS_MIN_RATE:.0%}",
                  file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
