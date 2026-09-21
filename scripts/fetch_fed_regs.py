"""Fetch every non-reserved Federal Reserve regulation (12 CFR Chapter II) from
the eCFR and chunk it into data/fed/chunks.jsonl for the hosted app.

eCFR text is a U.S. Government work (public domain), so redistribution is fine.
The part list comes from eCFR's own structure endpoint, so new/removed parts are
picked up automatically instead of a hand-maintained list going stale.

Run: python -m scripts.fetch_fed_regs [--limit N]
Output: one JSON object per line: {id, part, title, source, text}
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import httpx
from langchain_text_splitters import RecursiveCharacterTextSplitter

from scripts.download_corpus import _strip_html

OUT = Path("data/fed/chunks.jsonl")
STRUCTURE = "https://www.ecfr.gov/api/versioner/v1/structure/current/title-12.json"
CONTENT = "https://www.ecfr.gov/api/renderer/v1/content/enhanced/current/title-12"
CHUNK_SIZE, CHUNK_OVERLAP = 800, 100  # same as src/ingest.py so retrieval behaves the same


def list_fed_parts(client: httpx.Client) -> list[tuple[str, str]]:
    """Return [(part_number, label)] for every non-reserved part in Chapter II."""
    data = client.get(STRUCTURE).json()
    parts: list[tuple[str, str]] = []

    def walk(node: dict, in_ch2: bool) -> None:
        in_ch2 = in_ch2 or (node.get("type") == "chapter" and node.get("identifier") == "II")
        if in_ch2 and node.get("type") == "part" and not node.get("reserved"):
            parts.append((node["identifier"], node.get("label_description") or node["identifier"]))
        for child in node.get("children") or []:
            walk(child, in_ch2)

    walk(data, False)
    return parts


def fetch_part(client: httpx.Client, part: str) -> str:
    # Chapter II is only the Fed; the part number alone is unambiguous within it.
    r = client.get(CONTENT, params={"chapter": "II", "part": part})
    r.raise_for_status()
    return _strip_html(r.text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only the first N parts (smoke test)")
    args = ap.parse_args()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    failed: list[tuple[str, str]] = []
    n_chunks = 0

    with httpx.Client(timeout=120, follow_redirects=True) as client, OUT.open("w") as f:
        parts = list_fed_parts(client)
        if args.limit:
            parts = parts[: args.limit]
        print(f"{len(parts)} Federal Reserve parts")
        for part, label in parts:
            try:
                text = fetch_part(client, part)
            except httpx.HTTPError as e:
                failed.append((part, str(e)[:80]))
                continue
            if len(text) < 200:  # an empty/withdrawn part; nothing worth indexing
                failed.append((part, "no text"))
                continue
            source = f"12 CFR Part {part}"
            for i, chunk in enumerate(splitter.split_text(text)):
                f.write(json.dumps({"id": f"cfr12-{part}-{i}", "part": part, "title": label,
                                    "source": source, "text": chunk}) + "\n")
                n_chunks += 1
            print(f"  part {part:>4}  {len(text):>8} chars  {label[:60]}")
            time.sleep(0.3)  # be polite to eCFR

    print(f"\n{n_chunks} chunks -> {OUT}")
    if failed:
        print("skipped/failed:", failed)


if __name__ == "__main__":
    main()
