# regintel — Claude Code notes
_Draft by Claude, 2026-11-01 — edit to your own wording._

RegIntel: citation-grounded RAG Q&A over banking/financial regulation (Basel III/LCR, FFIEC, OCC, BSA/AML) on AWS Bedrock; answers with sources or refuses. Phase 1 (RAG + eval) is built. PUBLIC repo.

## Commands (README Quickstart; no CI, no Makefile, no test suite defined)
- Setup: `python -m venv .venv && source .venv/bin/activate && pip install -e .`, then `cp .env.example .env` (never read or print `.env`)
- `python -m scripts.download_corpus`, `python -m src.cli ingest`
- `python -m src.cli ask "<question>"`
- `python -m eval.eval` (faithfulness eval harness)

## Key paths
- `src/` — cli, ingest, retrieve, embeddings, generate
- `eval/` — eval.py, questions.yaml, results.md; `scripts/download_corpus.py`
- `docs/architecture.md` — Phase 2-3 design

## Rules
- Public repo: never commit `.env`, keys, `data/raw/`, `data/chroma/`, `*.pkl`, `*.sqlite*` (see `.gitignore`). A local pre-commit hook (`.git/hooks/pre-commit` → `~/.claude/bin/secret-scan-staged.sh`) blocks staged secrets, `.env` files and data; `.claude/settings.json` denies reading/editing `.env`. Bypass only deliberately with `git commit --no-verify`.
- Local commits only; no push unless asked.
