"""Phase 2 — LLM-assisted structured extraction of regulatory thresholds.

Reads the chunks already produced by `python -m src.ingest` (from
data/bm25_index.pkl, so this never re-chunks or re-downloads), filters to
chunks that plausibly state a numeric threshold, and asks the active LLM
(same provider routing as src/generate.py — Ollama by default, Bedrock/
Anthropic if configured) to pull out structured {metric, value, unit,
institution_tier} records. Every record keeps the exact source chunk as its
citation_excerpt, so nothing in the warehouse is ungrounded — the model
extracts, it doesn't compose.

This is the `int_extracted_thresholds` step from docs/architecture.md's
medallion pipeline, run against local files instead of S3.

Run: python -m scripts.extract_thresholds
"""
from __future__ import annotations

import json
import pickle
import re
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

from src.generate import llm_call
from src.warehouse import (
    connect,
    insert_threshold,
    reset,
    stats,
    upsert_metric,
    upsert_regulation,
)

load_dotenv()

BM25_PATH = Path("data/bm25_index.pkl")

# Precision-leaning filter: a chunk is a candidate only if it states an actual
# number against a ratio/percentage/day-count concept — not just the word
# "minimum" or "ratio" in isolation (those hit on ~15% of all chunks in a
# cross-referential regulation and would waste LLM calls on prose with no
# extractable value).
CANDIDATE_PATTERN = re.compile(
    r"\d+(\.\d+)?\s*percent"
    r"|not less than\s+\d"
    r"|equal to or greater than\s+\d"
    r"|of at least\s+\d"
    r"|within\s+\d+\s+(calendar\s+|business\s+)?days",
    re.I,
)

MAX_CANDIDATES_PER_SOURCE = 7  # cap so a repetitive regulation (Reg Q) doesn't dominate,
                                # and so a full run stays inside a reasonable wall-clock
                                # budget on CPU-only local inference (roughly 90s/batch of
                                # 6 on a laptop CPU with qwen2.5:7b; raise this on a GPU
                                # host or with Bedrock). Candidates are taken in document
                                # order, which is naive — the first N hits in a long
                                # regulation are disproportionately definitions and
                                # transitional schedules, not the steady-state provision
                                # a lookup usually wants. MAX_CANDIDATES_OVERRIDE raises
                                # the cap for a specific source when that matters; a
                                # production version would prioritize by passage
                                # distinctiveness (e.g. TF-IDF) instead of position.
MAX_CANDIDATES_OVERRIDE = {
    "cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt": 30,
}
BATCH_SIZE = 6

EXTRACT_SYSTEM = """You are a regulatory-data extraction engine. You are given numbered passages of \
U.S. banking regulation text. For each passage, extract every DISCRETE NUMERIC THRESHOLD it states \
(a capital ratio, liquidity ratio, buffer, or a deadline in days). A threshold has: a concrete number, \
a unit (percent, or days), and what it's a minimum/requirement for.

Output ONLY a JSON array (no prose, no markdown fences). Each element:
{"passage": <int, the passage number>, "metric_key": "<snake_case short id, e.g. cet1_minimum_ratio>", \
"metric_label": "<human label, e.g. Minimum common equity tier 1 (CET1) capital ratio>", \
"value": <number, e.g. 4.5>, "unit": "percent", "days", or "ratio" (a bare decimal requirement \
like a liquidity coverage ratio stated as "1.0", not a percentage), \
"institution_tier": "<who it applies to, or 'all covered institutions' if unstated>", \
"condition": "<short qualifier if any, else empty string>"}

Rules:
- Skip passages with no concrete numeric threshold (definitions, cross-references, procedural text) — \
emit nothing for them.
- A passage may yield zero, one, or several threshold records.
- Do not invent institution scope or conditions not stated in the text.
- value must be a bare number (4.5 not "4.5%", 30 not "30 days").
"""


WINDOW_EXTRA_CHARS = 350  # how much of the next chunk to pull in when a candidate
                           # ends mid-sentence, splitting a value from its label
                           # across the 800-char/100-overlap chunk boundary (see
                           # the extraction PR description for a caught example)
SENTENCE_END = re.compile(r"[.;:)]\s*$")


def load_candidates() -> list[dict]:
    if not BM25_PATH.exists():
        raise SystemExit(f"No index at {BM25_PATH}. Run `python -m src.ingest` first.")
    chunks = pickle.load(BM25_PATH.open("rb"))["chunks"]

    by_key = {(c["source"], c["chunk_index"]): c for c in chunks}

    by_source: dict[str, list[dict]] = {}
    for c in chunks:
        if CANDIDATE_PATTERN.search(c["text"]):
            by_source.setdefault(c["source"], []).append(c)

    candidates: list[dict] = []
    for source, chunks_for_source in by_source.items():
        cap = MAX_CANDIDATES_OVERRIDE.get(source, MAX_CANDIDATES_PER_SOURCE)
        for c in chunks_for_source[:cap]:
            window = c["text"]
            if not SENTENCE_END.search(window):
                nxt = by_key.get((c["source"], c["chunk_index"] + 1))
                if nxt:
                    window = window + " " + nxt["text"][:WINDOW_EXTRA_CHARS]
            candidates.append({**c, "window_text": window})
    return candidates


def _extract_json_array(raw: str) -> list[dict]:
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def extract_batch(batch: list[dict]) -> list[dict]:
    """Call the LLM once for a batch of candidate chunks. Returns validated
    records annotated with which chunk (by index into `batch`) they came from."""
    user_content = "\n\n".join(
        f"Passage {i}:\n{c['window_text']}" for i, c in enumerate(batch)
    )
    raw = llm_call(EXTRACT_SYSTEM, user_content, max_tokens=1500)
    items = _extract_json_array(raw)

    records = []
    for item in items:
        try:
            passage_i = int(item["passage"])
            value = float(item["value"])
            metric_key = str(item["metric_key"]).strip().lower().replace(" ", "_")
            metric_label = str(item["metric_label"]).strip()
            unit = str(item["unit"]).strip().lower()
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= passage_i < len(batch)) or not metric_key or not metric_label:
            continue
        records.append(
            {
                "chunk": batch[passage_i],
                "metric_key": metric_key,
                "metric_label": metric_label,
                "value": value,
                "unit": unit,
                "institution_tier": str(item.get("institution_tier") or "all covered institutions").strip(),
                "condition_text": str(item.get("condition") or "").strip(),
            }
        )
    return records


def main() -> None:
    candidates = load_candidates()
    print(f"Candidate chunks (regex-filtered, capped at {MAX_CANDIDATES_PER_SOURCE}/source, "
          f"overrides {MAX_CANDIDATES_OVERRIDE}): {len(candidates)}")

    reset()
    conn = connect()
    regulation_ids = {
        source: upsert_regulation(conn, source)
        for source in {c["source"] for c in candidates}
    }
    conn.commit()

    total_extracted = 0
    total_rejected_parse = 0
    batches = [candidates[i : i + BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]
    for batch in tqdm(batches, desc="extracting thresholds"):
        try:
            records = extract_batch(batch)
        except Exception as e:  # noqa: BLE001 — a bad LLM response shouldn't kill the run
            print(f"\n  ! batch failed: {e}")
            total_rejected_parse += len(batch)
            continue

        for rec in records:
            chunk = rec["chunk"]
            metric_id = upsert_metric(conn, rec["metric_key"], rec["metric_label"])
            insert_threshold(
                conn,
                regulation_id=regulation_ids[chunk["source"]],
                metric_id=metric_id,
                institution_tier=rec["institution_tier"],
                value=rec["value"],
                unit=rec["unit"],
                condition_text=rec["condition_text"],
                chunk_source=chunk["source"],
                chunk_index=chunk["chunk_index"],
                citation_excerpt=chunk["window_text"],
            )
            total_extracted += 1
        conn.commit()

    conn.close()
    print(f"\nExtracted {total_extracted} threshold records "
          f"({total_rejected_parse} candidate chunks skipped on batch/parse failure).")
    print(f"Warehouse stats: {stats()}")


if __name__ == "__main__":
    main()
