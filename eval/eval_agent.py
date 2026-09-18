"""Eval harness for the Phase 3 routing agent (src/agent.py).

Checks two things per question: did the router call an acceptable tool, and
does the final answer show the right behavior — a correct answer, or a
refusal where the corpus genuinely doesn't cover the question.

Refusal detection went through two failed attempts before landing here, both
worth recording (see eval/agent_results.md for the full write-up):

1. A loose substring check (`"don't contain enough information" in
   answer.lower()`) false-flagged real, correct answers as refusals. The
   final-compose step sometimes opens with that exact phrase as a rhetorical
   hedge — "The provided sources don't contain enough information to answer
   that, as the minimum CET1 ratio varies..." — and then gives a fully
   correct, grounded answer. A substring check can't tell that apart from a
   genuine refusal.
2. An LLM judge (same technique as eval/eval.py's Claude-as-judge
   faithfulness score) was tried next and was worse: on real evidence it
   called a textbook-perfect, single-sentence refusal "WRONG" and a fully
   correct, well-grounded multi-part answer "WRONG" too — qwen2.5:7b isn't
   reliable at this nuanced a classification task, at least not in a 6-token
   budget. Tightening the generation prompt to forbid the hedge phrasing was
   tried too, and overcorrected: the model started refusing questions it
   could actually answer.

The fix that actually works: check for the EXACT canonical refusal sentence
(the one literally instructed in src/agent.py's FINAL_SYSTEM, "...that."
with its period) as a substring — not just the leading phrase. The hedge
pattern always continues past "...that" with a comma or a following word,
never that exact period; a genuine refusal has the period right there,
whatever the model adds afterward. This is what src/generate.py's Phase 1
eval already effectively relies on too (its refusal answers are short and
clean), so the difference here is spelling out the WHY once, so it doesn't
need rediscovering next time.

Run: python -m eval.eval_agent
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.agent import FINAL_SYSTEM, answer

CHECKS_PATH = Path("eval/agent_questions.yaml")

# Extracted from FINAL_SYSTEM's rule 2 rather than duplicated by hand, so this
# can never silently drift from what the model is actually instructed to say.
CANONICAL_REFUSAL = "The provided sources don't contain enough information to answer that."
assert CANONICAL_REFUSAL in FINAL_SYSTEM, "FINAL_SYSTEM's wording changed; update CANONICAL_REFUSAL to match."


def _is_refusal(text: str) -> bool:
    return CANONICAL_REFUSAL.lower() in text.lower()


def main() -> None:
    checks = yaml.safe_load(CHECKS_PATH.read_text())["checks"]

    results = []
    for c in checks:
        result = answer(c["question"])
        tools_called = {t["name"] for t in result["tools_called"]}
        expected_tools = set(c.get("expected_tools", []))
        tool_ok = bool(tools_called & expected_tools) if expected_tools else True

        is_refusal = _is_refusal(result["answer"])
        expect_refusal = bool(c.get("expect_refusal"))
        if expect_refusal:
            behavior_ok = is_refusal
        else:
            low = result["answer"].lower()
            keywords = c.get("expected_keywords", [])
            behavior_ok = (not is_refusal) and all(k.lower() in low for k in keywords)

        results.append(
            {
                "question": c["question"],
                "tools_called": sorted(tools_called),
                "tool_ok": tool_ok,
                "is_refusal": is_refusal,
                "expect_refusal": expect_refusal,
                "behavior_ok": behavior_ok,
                "answer": result["answer"][:200],
            }
        )

    tool_hits = sum(1 for r in results if r["tool_ok"])
    behavior_hits = sum(1 for r in results if r["behavior_ok"])

    print("# Phase 3 Agent Eval\n")
    print(f"- **Total questions:** {len(results)}")
    print(f"- **Correct tool routing:** {tool_hits}/{len(results)}")
    print(f"- **Correct answer behavior** (correct/grounded, or refusal where expected): "
          f"{behavior_hits}/{len(results)}")
    print()
    print("| # | Question | Tools called | Routing | Behavior | Answer (truncated) |")
    print("|---|---|---|---|---|---|")
    for i, r in enumerate(results, 1):
        tools_str = ", ".join(r["tools_called"]) or "(none)"
        print(
            f"| {i} | {r['question'][:60]} | {tools_str} | "
            f"{'✅' if r['tool_ok'] else '❌'} | {'✅' if r['behavior_ok'] else '❌'} | "
            f"{r['answer'][:100]} |"
        )


if __name__ == "__main__":
    main()
