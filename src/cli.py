"""CLI entry: python -m src.cli "your question here" [-k 5] [-m hybrid|dense|bm25]"""
from __future__ import annotations

import argparse
import json

from src.generate import answer


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask RegIntel (banking/financial-regulation RAG) a question.")
    parser.add_argument("question", nargs="+", help="The question to answer.")
    parser.add_argument("-k", "--top-k", type=int, default=5, help="Top-k chunks to retrieve (default: 5).")
    parser.add_argument(
        "-m",
        "--mode",
        choices=["hybrid", "dense", "bm25"],
        default="hybrid",
        help="Retrieval mode (default: hybrid).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable output."
    )
    args = parser.parse_args()
    question = " ".join(args.question)
    result = answer(question, k=args.top_k, mode=args.mode)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print("\n=== QUESTION ===")
    print(question)
    print("\n=== ANSWER ===")
    print(result["answer"])
    print("\n=== SOURCES ===")
    for s in result["sources"]:
        print(f"  - {s}")
    print(f"\n[retrieval mode: {args.mode}, top-k: {args.top_k}, chunks used: {len(result['context_used'])}]")


if __name__ == "__main__":
    main()
