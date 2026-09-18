"""Phase 3 — the routing agent (docs/architecture.md, "Phase 3 — Agent layer").

Real regulatory queries mix structured and unstructured: "what LCR must my
institution hold, and what's the supervisory rationale?" — the first half is
a threshold lookup (Phase 2's warehouse), the second is guidance text (Phase
1's RAG). Pure RAG can't answer a threshold question reliably; pure SQL can't
answer an open-ended guidance question. This module routes.

Shape: an LLM sees the question and two tools — `lookup_threshold` (Phase 2,
warehouse.lookup_threshold) and `search_guidance` (Phase 1, retrieve.retrieve)
— and decides which to call, via real tool-calling (Ollama's /api/chat
`tools` param; qwen2.5:7b-instruct supports it). Tools are executed locally,
deterministically, with no LLM in the loop. Their output — always carrying
a `[source: ...]` citation already attached — is then handed to a second,
final LLM turn that composes a grounded answer under the same
citation/refusal discipline as src/generate.py, and refuses if what the
tools returned doesn't support an answer.

This is the local-first demo scale-down of docs/architecture.md's Bedrock
Agent design (Claude tool-use via Bedrock Converse + Lambda action groups).
Only the Ollama path is implemented here — see `answer()`'s docstring.

Run: python -m src.agent "your question here"
"""
from __future__ import annotations

import json

from dotenv import load_dotenv

from src.generate import LLM_PROVIDER, OLLAMA_HOST, OLLAMA_MODEL
from src.retrieve import retrieve
from src.warehouse import lookup_threshold

load_dotenv()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_threshold",
            "description": (
                "Look up a numeric regulatory threshold (a capital ratio, liquidity ratio, "
                "buffer, or filing deadline) from the structured warehouse. Use this when "
                "the question asks for a specific number — 'what is the minimum X', 'how "
                "many days to Y'. Returns every matching provision with its scope, value, "
                "and source citation. Does not compose an answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "description": "The metric to search for, e.g. 'CET1', 'liquidity "
                        "coverage ratio', 'suspicious activity report'.",
                    }
                },
                "required": ["metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_guidance",
            "description": (
                "Search the regulatory text corpus for passages relevant to an open-ended "
                "or explanatory question — what a rule requires, why, or how it applies. "
                "Use this for anything that isn't a single-number lookup. Returns the "
                "top-matching passages with their source citation. Does not compose an answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question or topic to search guidance text for.",
                    }
                },
                "required": ["question"],
            },
        },
    },
]

ROUTER_SYSTEM = (
    "You are the routing layer for RegIntel, a banking/financial-regulation assistant. "
    "Given the user's question, decide which tool(s) to call to gather grounding evidence "
    "before answering. Call lookup_threshold for a specific numeric requirement (a ratio, "
    "buffer, or deadline). Call search_guidance for anything else — explanatory, procedural, "
    "or 'why' questions, or when a flat number isn't clearly what's being asked for. Call "
    "both if the question mixes a number with an explanation. Always call at least one tool "
    "before answering — never answer from your own knowledge."
)

FINAL_SYSTEM = """You are RegIntel, an assistant that answers questions about banking and financial regulation using ONLY the evidence gathered below by the routing layer.

Rules:
1. Ground every factual claim in the evidence. Every evidence line already carries its own [source: ...] tag — carry that exact tag forward in your answer.
2. If the evidence does not contain enough information to answer, say so explicitly: "The provided sources don't contain enough information to answer that." Do NOT guess or fall back to your training data — in a regulated domain, a confident but unsupported answer is the worst outcome.
3. Be precise. Quote thresholds, ratios, and dates exactly as the evidence states them; do not round or paraphrase numbers.
4. You provide regulatory research and retrieval, not legal or compliance advice.
"""


def _execute_tool(name: str, args: dict) -> tuple[str, set[str]]:
    """Run one tool call locally — deterministic Python, no LLM. Returns
    (evidence text with citations already embedded, set of source strings used)."""
    if name == "lookup_threshold":
        metric = str(args.get("metric") or "").strip()
        if not metric:
            return "lookup_threshold called with no metric argument.", set()
        rows = lookup_threshold(metric)
        if not rows:
            return f"No warehouse threshold found matching {metric!r}.", set()
        lines, sources = [], set()
        for r in rows:
            citation = f"{r['chunk_source']}::chunk-{r['chunk_index']:04d}"
            if r["is_formula"]:
                value_str = f"FORMULA (not a flat number) — {r['formula_expr']}"
            else:
                unit_str = "%" if r["unit"] == "percent" else f" {r['unit']}"
                value_str = f"{r['value']}{unit_str}"
            lines.append(
                f"- {r['metric']}: {value_str}, scope: {r['institution_tier']}"
                + (f" ({r['condition_text']})" if r.get("condition_text") else "")
                + f" [source: {citation}]"
            )
            sources.add(citation)
        return "\n".join(lines), sources

    if name == "search_guidance":
        question = str(args.get("question") or "").strip()
        if not question:
            return "search_guidance called with no question argument.", set()
        hits = retrieve(question, k=5, mode="hybrid")
        if not hits:
            return "No matching guidance text found.", set()
        lines, sources = [], set()
        for h in hits:
            lines.append(f"--- source: {h['source']} (chunk {h['chunk_index']}) ---\n{h['text']}")
            sources.add(h["source"])
        return "\n\n".join(lines), sources

    return f"Unknown tool {name!r}.", set()


def answer(question: str) -> dict:
    """Route the question through tool-calling, execute the chosen tool(s)
    locally, then compose a grounded final answer.

    Ollama-only for now: LLM_PROVIDER=ollama is the only path implemented.
    Bedrock Converse tool-use (Claude tool_use blocks via bedrock-runtime's
    Converse API, not the raw invoke_model src/generate.py's Bedrock branch
    uses) is the documented production target — see docs/architecture.md,
    "What's missing for production" — but isn't wired up here.
    """
    if LLM_PROVIDER != "ollama":
        raise NotImplementedError(
            "src.agent currently only supports LLM_PROVIDER=ollama. Bedrock Converse "
            "tool-use is the documented production target (docs/architecture.md, Phase 3) "
            "but is not yet implemented here."
        )

    import httpx

    router_resp = httpx.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "stream": False,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": ROUTER_SYSTEM},
                {"role": "user", "content": question},
            ],
            "tools": TOOLS,
        },
        timeout=900.0,
    )
    router_resp.raise_for_status()
    tool_calls = router_resp.json()["message"].get("tool_calls") or []

    if not tool_calls:
        # The router answered directly without calling a tool. Force a refusal
        # rather than trust an ungrounded answer — same discipline as generate.py.
        return {
            "answer": "The provided sources don't contain enough information to answer that.",
            "tools_called": [],
            "sources": [],
            "evidence": "",
        }

    evidence_blocks = []
    tools_called = []
    all_sources: set[str] = set()
    for call in tool_calls:
        fn_name = call["function"]["name"]
        fn_args = call["function"].get("arguments") or {}
        text, sources = _execute_tool(fn_name, fn_args)
        evidence_blocks.append(f"### Evidence from {fn_name}({json.dumps(fn_args)})\n{text}")
        tools_called.append({"name": fn_name, "arguments": fn_args})
        all_sources |= sources

    evidence = "\n\n".join(evidence_blocks)
    final_resp = httpx.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 1024},
            "messages": [
                {"role": "system", "content": FINAL_SYSTEM},
                {
                    "role": "user",
                    "content": f"Question: {question}\n\nEvidence:\n{evidence}\n\n"
                    "Answer the question following the rules above.",
                },
            ],
        },
        timeout=900.0,
    )
    final_resp.raise_for_status()
    final_text = final_resp.json()["message"]["content"]

    return {
        "answer": final_text,
        "tools_called": tools_called,
        "sources": sorted(all_sources),
        "evidence": evidence,
    }


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "What minimum liquidity coverage ratio must a covered institution maintain?"
    result = answer(q)
    print("\n=== QUESTION ===")
    print(q)
    print("\n=== TOOLS CALLED ===")
    for t in result["tools_called"]:
        print(f"  - {t['name']}({t['arguments']})")
    print("\n=== ANSWER ===")
    print(result["answer"])
    print("\n=== SOURCES ===")
    for s in result["sources"]:
        print(f"  - {s}")
