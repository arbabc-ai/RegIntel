"""Eval harness: hit-rate@5 + Claude-as-judge faithfulness scoring.

Run: python -m eval.eval
Run as a CI gate: python -m eval.eval --gate   (exits 1 on regression below
the thresholds below; prints the same report either way — --gate only
changes the exit code, not the output, so `> eval/results.md` still works.)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.embeddings import get_embedding_function
from src.generate import active_model, answer, llm_call
from src.retrieve import retrieve

load_dotenv()

QUESTIONS_PATH = Path("eval/questions.yaml")

# CI gate thresholds. Set below the 8/8 and 3/3 this eval currently gets, not
# at 100%: local LLM generation isn't perfectly deterministic run to run even
# at temperature 0 (context caching, minor Ollama version drift), so a hard
# 100% gate would occasionally fail CI for no real regression. These catch a
# genuine break (e.g. the corpus failing to download, or a prompt change that
# actually breaks grounding) without flaking on a single-question wobble.
RETRIEVAL_HIT_RATE_MIN = 0.75  # 6/8
REFUSAL_ACCURACY_MIN = 0.66    # 2/3
MEAN_FAITHFULNESS_MIN = 1.5    # observed 2.00; half the 0-3 scale as a floor

JUDGE_SYSTEM = "You are a strict faithfulness judge. Respond with ONLY a single digit."

FAITHFULNESS_PROMPT = """Evaluate whether an AI-generated answer is faithful to the source context.

Question: {question}

Answer: {answer}

Source context that was retrieved:
{context}

Rate the answer's faithfulness on a 0-3 scale:
- 3: Every factual claim in the answer is directly supported by the source context.
- 2: Most claims are supported; minor inferences are reasonable.
- 1: Some claims are unsupported or contradicted by the source.
- 0: The answer hallucinates substantially beyond the source.

Respond with ONLY a single digit (0, 1, 2, or 3). No explanation, no other text."""


def _judge_faithfulness(question: str, ans_text: str, hits: list[dict]) -> int:
    context = "\n".join(f"({h['source']}) {h['text']}" for h in hits)
    user_content = FAITHFULNESS_PROMPT.format(
        question=question, answer=ans_text, context=context
    )
    raw = llm_call(JUDGE_SYSTEM, user_content, max_tokens=4).strip()
    try:
        return int(raw[0])
    except (ValueError, IndexError):
        return -1


REFUSAL_MARKERS = ("don't contain enough information", "do not contain enough information")


def _is_refusal(ans_text: str) -> bool:
    low = ans_text.lower()
    return any(m in low for m in REFUSAL_MARKERS)


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG eval (Phase 1).")
    parser.add_argument("--gate", action="store_true",
                         help="Exit 1 if results fall below the CI thresholds.")
    args = parser.parse_args()

    questions = yaml.safe_load(QUESTIONS_PATH.read_text())["questions"]

    results: list[dict] = []
    for q in questions:
        question = q["question"]
        expected_sources = set(q.get("expected_sources", []))
        is_refusal_test = not expected_sources

        # Retrieval + generation
        hits_for_eval = retrieve(question, k=5, mode="hybrid")
        retrieved_sources = {h["source"] for h in hits_for_eval}
        gen = answer(question, k=5, mode="hybrid")
        faith_score = _judge_faithfulness(question, gen["answer"], hits_for_eval)

        # Hit semantics differ for refusal-expected rows
        if is_refusal_test:
            hit_at_5 = _is_refusal(gen["answer"])
        else:
            hit_at_5 = bool(expected_sources & retrieved_sources)

        results.append(
            {
                "question": question,
                "is_refusal_test": is_refusal_test,
                "hit_at_5": hit_at_5,
                "faithfulness": faith_score,
                "retrieved": list(retrieved_sources),
                "expected": list(expected_sources),
                "answer": gen["answer"][:200],
            }
        )

    # Aggregate, split by question type
    retrieval_rows = [r for r in results if not r["is_refusal_test"]]
    refusal_rows = [r for r in results if r["is_refusal_test"]]
    r_hits = sum(1 for r in retrieval_rows if r["hit_at_5"])
    refusal_hits = sum(1 for r in refusal_rows if r["hit_at_5"])
    faith_pool = [r["faithfulness"] for r in results if r["faithfulness"] >= 0]
    avg_faith = sum(faith_pool) / max(1, len(faith_pool))

    # Emit markdown report
    print("# RAG Eval Report\n")
    print(f"- **Generation + judge model:** `{active_model()}`")
    print(f"- **Embeddings:** `{get_embedding_function().name()}`")
    print(f"- **Total questions:** {len(results)} "
          f"({len(retrieval_rows)} retrieval, {len(refusal_rows)} refusal)")
    if retrieval_rows:
        print(f"- **Retrieval hit-rate@5:** {r_hits}/{len(retrieval_rows)} = "
              f"{r_hits / len(retrieval_rows):.0%}")
    if refusal_rows:
        print(f"- **Refusal accuracy:** {refusal_hits}/{len(refusal_rows)} = "
              f"{refusal_hits / len(refusal_rows):.0%}")
    print(f"- **Mean faithfulness (0-3):** {avg_faith:.2f}")
    print()
    print("| # | Type | Hit | Faith | Question | Retrieved | Expected |")
    print("|---|---|---|---|---|---|---|")
    for i, r in enumerate(results, 1):
        retrieved_str = ", ".join(sorted(r["retrieved"]))
        expected_str = ", ".join(sorted(r["expected"])) if r["expected"] else "(refuse)"
        qtype = "refusal" if r["is_refusal_test"] else "retrieval"
        print(
            f"| {i} | {qtype} | {'✅' if r['hit_at_5'] else '❌'} | {r['faithfulness']} | "
            f"{r['question'][:60]} | {retrieved_str[:50]} | {expected_str[:40]} |"
        )

    if args.gate:
        retrieval_rate = r_hits / len(retrieval_rows) if retrieval_rows else 1.0
        refusal_rate = refusal_hits / len(refusal_rows) if refusal_rows else 1.0
        failures = []
        if retrieval_rate < RETRIEVAL_HIT_RATE_MIN:
            failures.append(f"retrieval hit-rate {retrieval_rate:.0%} < {RETRIEVAL_HIT_RATE_MIN:.0%}")
        if refusal_rate < REFUSAL_ACCURACY_MIN:
            failures.append(f"refusal accuracy {refusal_rate:.0%} < {REFUSAL_ACCURACY_MIN:.0%}")
        if avg_faith < MEAN_FAITHFULNESS_MIN:
            failures.append(f"mean faithfulness {avg_faith:.2f} < {MEAN_FAITHFULNESS_MIN:.2f}")
        if failures:
            print(f"\nGATE FAILED: {'; '.join(failures)}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
