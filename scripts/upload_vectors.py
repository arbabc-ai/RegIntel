"""Embed data/fed/chunks.jsonl with Workers AI and upsert into the Vectorize index.

Needs CF_ACCOUNT_ID and CF_API_TOKEN (a token with Workers AI + Vectorize edit
scope; the Pages-only deploy token is NOT enough). Both models must match
web/functions/_lib/rag.js, or query vectors will not line up with stored ones.

Resumable: --start N skips the first N chunks after a failed run.
Run: python -m scripts.upload_vectors [--dry-run] [--start N]
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx

CHUNKS = Path("data/fed/chunks.jsonl")
INDEX = os.environ.get("VECTORIZE_INDEX", "regintel-fed")
EMBED_MODEL = "@cf/baai/bge-small-en-v1.5"  # keep in sync with web/functions/_lib/rag.js
DIMS = 384
EMBED_BATCH, UPSERT_BATCH = 50, 500


def _api(acct: str) -> str:
    return f"https://api.cloudflare.com/client/v4/accounts/{acct}"


def embed(client: httpx.Client, acct: str, texts: list[str]) -> list[list[float]]:
    r = client.post(f"{_api(acct)}/ai/run/{EMBED_MODEL}", json={"text": texts})
    r.raise_for_status()
    vecs = r.json()["result"]["data"]
    if len(vecs) != len(texts) or any(len(v) != DIMS for v in vecs):
        raise RuntimeError(f"unexpected embedding shape (want {DIMS} dims x {len(texts)})")
    return vecs


def upsert(client: httpx.Client, acct: str, rows: list[dict]) -> None:
    body = "\n".join(json.dumps(r) for r in rows)
    r = client.post(f"{_api(acct)}/vectorize/v2/indexes/{INDEX}/upsert", content=body,
                    headers={"Content-Type": "application/x-ndjson"})
    r.raise_for_status()


def to_row(chunk: dict, vec: list[float]) -> dict:
    return {"id": chunk["id"], "values": vec,
            "metadata": {"source": chunk["source"], "title": chunk["title"], "text": chunk["text"]}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="validate chunks + row shape, no network")
    ap.add_argument("--start", type=int, default=0)
    args = ap.parse_args()

    chunks = [json.loads(line) for line in CHUNKS.read_text().splitlines() if line.strip()]
    ids = [c["id"] for c in chunks]
    assert len(ids) == len(set(ids)), "duplicate chunk ids"
    biggest = max(len(json.dumps(to_row(c, [0.0] * DIMS)["metadata"])) for c in chunks)
    print(f"{len(chunks)} chunks; largest metadata payload {biggest} bytes (Vectorize limit 10240)")
    assert biggest < 10240, "metadata too large for Vectorize"
    if args.dry_run:
        print("dry run OK")
        return

    acct, token = os.environ["CF_ACCOUNT_ID"], os.environ["CF_API_TOKEN"]
    with httpx.Client(timeout=120, headers={"Authorization": f"Bearer {token}"}) as client:
        pending: list[dict] = []
        for i in range(args.start, len(chunks), EMBED_BATCH):
            batch = chunks[i:i + EMBED_BATCH]
            for attempt in range(4):
                try:
                    vecs = embed(client, acct, [c["text"] for c in batch])
                    break
                except httpx.HTTPError as e:
                    if attempt == 3:
                        raise SystemExit(f"embed failed at chunk {i}: {e}. Resume with --start {i}")
                    time.sleep(2 ** attempt)
            pending += [to_row(c, v) for c, v in zip(batch, vecs)]
            if len(pending) >= UPSERT_BATCH:
                upsert(client, acct, pending)
                pending = []
            print(f"  {min(i + EMBED_BATCH, len(chunks))}/{len(chunks)}", end="\r")
        if pending:
            upsert(client, acct, pending)
    print(f"\nupserted {len(chunks) - args.start} chunks into {INDEX}")


if __name__ == "__main__":
    main()
