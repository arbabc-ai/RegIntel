"""CLI entry: python -m src.lookup "<metric text>"

Deterministic warehouse lookup — no LLM call, no retrieval. This is the
`lookup_threshold` tool contract from docs/architecture.md's Phase 3 design,
usable standalone today. Compare with `python -m src.cli` (Phase 1, RAG):
this answers "what's the number" from a structured fact table with a full
citation trail; the CLI answers open-ended guidance questions from text.
"""
from __future__ import annotations

import argparse
import json

from src.warehouse import lookup_threshold


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Look up a regulatory threshold from the warehouse (Phase 2)."
    )
    parser.add_argument("metric", nargs="+", help="Metric text to search for, e.g. 'CET1' or 'liquidity coverage'.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = parser.parse_args()
    query = " ".join(args.metric)

    rows = lookup_threshold(query)
    if args.json:
        print(json.dumps(rows, indent=2))
        return

    if not rows:
        print(f"No threshold found matching {query!r}. Run `python -m scripts.extract_thresholds` first "
              "if the warehouse is empty, or try a different metric term.")
        return

    print(f"\n=== THRESHOLDS MATCHING {query!r} ===\n")
    for r in rows:
        if r["is_formula"]:
            print(f"  {r['metric']}: FORMULA (not a flat number) — {r['formula_expr']}")
        else:
            unit_str = "%" if r["unit"] == "percent" else f" {r['unit']}"
            print(f"  {r['metric']}: {r['value']}{unit_str}")
        print(f"    Scope: {r['institution_tier']}"
              + (f" ({r['condition_text']})" if r.get("condition_text") else ""))
        print(f"    Source: {r['regulation']} — {r['regulation_title']}")
        print(f"    Citation: {r['chunk_source']}::chunk-{r['chunk_index']:04d}")
        print(f"    As of: {r['as_of_date'][:10]}")
        print()


if __name__ == "__main__":
    main()
