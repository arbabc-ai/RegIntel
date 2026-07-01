"""Prompt construction + Claude call with citations.

Programmatic use:
    from src.generate import answer
    result = answer("What minimum LCR must a covered institution maintain?")
    # result = {"answer": "...", "sources": [...], "context_used": [...]}
"""
from __future__ import annotations

import json
import os

from dotenv import load_dotenv

from src.retrieve import retrieve

load_dotenv()

# Provider routing: if BEDROCK_INFERENCE_PROFILE is set, route via AWS Bedrock (boto3);
# otherwise fall back to the Anthropic SDK with ANTHROPIC_API_KEY.
BEDROCK_PROFILE = os.environ.get("BEDROCK_INFERENCE_PROFILE")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 1024


def llm_call(system_prompt: str, user_content: str, max_tokens: int = MAX_TOKENS) -> str:
    if BEDROCK_PROFILE:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        })
        resp = client.invoke_model(modelId=BEDROCK_PROFILE, body=body)
        payload = json.loads(resp["body"].read())
        return "".join(b["text"] for b in payload["content"] if b["type"] == "text")

    from anthropic import Anthropic

    client = Anthropic()
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    return "".join(block.text for block in response.content if block.type == "text")

SYSTEM_PROMPT = """You are RegIntel, an assistant that answers questions about banking and financial regulation using ONLY the provided context.

Rules:
1. Ground every factual claim in the context. After each claim, attach a citation in the form [source: <doc_name>].
2. If the context does not contain enough information to answer, say so explicitly: "The provided sources don't contain enough information to answer that." Do NOT guess or fall back to your training data — in a regulated domain, a confident but unsupported answer is the worst outcome.
3. Be precise. Quote thresholds, ratios, and effective dates exactly as the source states them; do not round or paraphrase numbers.
4. You provide regulatory research and retrieval, not legal or compliance advice. If the question is ambiguous, ask a clarifying question rather than guessing.
"""

USER_TEMPLATE = """Question: {question}

Context (retrieved chunks):
{context}

Answer the question following the rules above. Remember to cite every claim with [source: <doc_name>] and refuse if the context is insufficient."""


def _format_context(hits: list[dict]) -> str:
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(f"--- chunk {i} (source: {h['source']}) ---\n{h['text']}\n")
    return "\n".join(lines)


def answer(question: str, k: int = 5, mode: str = "hybrid") -> dict:
    hits = retrieve(question, k=k, mode=mode)
    if not hits:
        return {
            "answer": "The provided sources don't contain enough information to answer that.",
            "sources": [],
            "context_used": [],
        }

    user_content = USER_TEMPLATE.format(question=question, context=_format_context(hits))
    text = llm_call(SYSTEM_PROMPT, user_content)
    return {
        "answer": text,
        "sources": sorted({h["source"] for h in hits}),
        "context_used": [{"source": h["source"], "chunk_index": h["chunk_index"]} for h in hits],
    }


if __name__ == "__main__":
    import sys
    question = " ".join(sys.argv[1:]) or "What minimum liquidity coverage ratio must a covered institution maintain?"
    result = answer(question)
    print("\n=== ANSWER ===")
    print(result["answer"])
    print("\n=== SOURCES ===")
    for s in result["sources"]:
        print(f"  - {s}")
