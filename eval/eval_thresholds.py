"""Eval harness for the Phase 2 warehouse: does extraction reproduce values
that are actually in the source text?

Run: python -m eval.eval_thresholds
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.warehouse import lookup_threshold

CHECKS_PATH = Path("eval/thresholds_questions.yaml")


def main() -> None:
    checks = yaml.safe_load(CHECKS_PATH.read_text())["checks"]

    results = []
    for c in checks:
        rows = lookup_threshold(c["query"])
        match = next(
            (
                r for r in rows
                if r["unit"] == c["expected_unit"]
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


if __name__ == "__main__":
    main()
