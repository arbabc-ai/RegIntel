"""Download public-domain banking/financial-regulatory text into data/raw/.

Two corpora are fetched (both public domain / freely redistributable):

1. Key U.S. banking regulations via the **eCFR API** (federal regs, public domain):
   - 12 CFR Part 217  — Regulation Q  (Capital Adequacy)
   - 12 CFR Part 249  — Regulation WW (Liquidity Coverage Ratio, LCR)
   - 12 CFR Part 252  — Regulation YY (Enhanced Prudential Standards)
   - 31 CFR Part 1020 — BSA/AML rules for banks (FinCEN)

2. **BIS Basel framework** PDFs (public, freely available from bis.org).

FFIEC examination handbooks and Fed SR/CA letters are HTML/PDF that move often —
to add them, drop your own .pdf/.txt/.md into data/raw/ and re-run
`python -m src.ingest`. Failures below are non-fatal for exactly this reason.

Run: python -m scripts.download_corpus
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx
from tqdm import tqdm

DATA_RAW = Path("data/raw")

# --- 1. eCFR regulations -------------------------------------------------
# (title, chapter, subchapter | None, part, slug)
ECFR_PARTS = [
    ("12", "II", "A", "217", "Regulation-Q-Capital-Adequacy"),
    ("12", "II", "A", "249", "Regulation-WW-Liquidity-Coverage-Ratio"),
    ("12", "II", "A", "252", "Regulation-YY-Enhanced-Prudential-Standards"),
    ("31", "X", None, "1020", "BSA-AML-Rules-for-Banks"),
]
ECFR_BASE = "https://www.ecfr.gov/api/renderer/v1/content/enhanced/current/title-{title}"

# --- 2. BIS Basel framework PDFs ----------------------------------------
BIS_CORPUS = [
    ("bis_basel_iii_finalising_reforms_d424.pdf", "https://www.bis.org/bcbs/publ/d424.pdf"),
    ("bis_basel_iii_lcr_bcbs238.pdf",             "https://www.bis.org/publ/bcbs238.pdf"),
]


def _strip_html(text: str) -> str:
    """Crude HTML → text. Good enough for eCFR's clean markup."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _ecfr_url(title: str, chapter: str, subchapter: str | None, part: str) -> str:
    params = [f"chapter={chapter}", f"part={part}"]
    if subchapter:
        params.insert(1, f"subchapter={subchapter}")
    return ECFR_BASE.format(title=title) + "?" + "&".join(params)


def fetch_ecfr(client: httpx.Client) -> tuple[int, list[tuple[str, str]]]:
    failed: list[tuple[str, str]] = []
    ok = 0
    for title, chapter, subchapter, part, slug in tqdm(ECFR_PARTS, desc="eCFR regulations"):
        fname = f"cfr_title{title}_part{part}_{slug}.txt"
        dst = DATA_RAW / fname
        if dst.exists() and dst.stat().st_size > 0:
            ok += 1
            continue
        try:
            r = client.get(_ecfr_url(title, chapter, subchapter, part))
            if r.status_code != 200:
                failed.append((fname, f"HTTP {r.status_code}"))
                continue
            body = _strip_html(r.text)
            if len(body) < 500:
                failed.append((fname, f"body too short ({len(body)} chars)"))
                continue
            header = f"{title} CFR Part {part} — {slug.replace('-', ' ')}\n\n"
            dst.write_text(header + body, encoding="utf-8")
            ok += 1
        except Exception as e:  # noqa: BLE001
            failed.append((fname, str(e)))
    return ok, failed


def fetch_bis(client: httpx.Client) -> tuple[int, list[tuple[str, str]]]:
    failed: list[tuple[str, str]] = []
    ok = 0
    for fname, url in tqdm(BIS_CORPUS, desc="BIS Basel PDFs"):
        dst = DATA_RAW / fname
        if dst.exists() and dst.stat().st_size > 0:
            ok += 1
            continue
        try:
            r = client.get(url)
            if r.status_code != 200:
                failed.append((fname, f"HTTP {r.status_code}"))
                continue
            dst.write_bytes(r.content)
            ok += 1
        except Exception as e:  # noqa: BLE001
            failed.append((fname, str(e)))
    return ok, failed


def main() -> None:
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    print(f"Downloading public-domain financial-regulatory corpora into {DATA_RAW}/ ...\n")

    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": "RegIntel/0.1"}) as client:
        ecfr_ok, ecfr_failed = fetch_ecfr(client)
        bis_ok, bis_failed = fetch_bis(client)

    print()
    print(f"  eCFR regulations:  {ecfr_ok}/{len(ECFR_PARTS)} downloaded")
    print(f"  BIS Basel PDFs:    {bis_ok}/{len(BIS_CORPUS)} downloaded")

    failed = ecfr_failed + bis_failed
    if failed:
        print(f"\nFailed downloads ({len(failed)}):")
        for fname, reason in failed:
            print(f"  - {fname}: {reason}")
        print(
            "\nFailures are non-fatal. Continue with what was fetched; or drop any "
            ".txt/.md/.pdf (e.g. FFIEC handbooks, Fed SR letters) into data/raw/ "
            "yourself and re-run `python -m src.ingest`."
        )

    total = ecfr_ok + bis_ok
    if total == 0:
        raise SystemExit(
            "No documents downloaded. Check network connectivity or supply your own "
            "corpus by dropping files into data/raw/ manually."
        )
    print(f"\n✓ {total} documents available in {DATA_RAW}/")


if __name__ == "__main__":
    main()
