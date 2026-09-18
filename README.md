# RegIntel — Regulatory Intelligence Platform

**A grounded, citation-enforced Q&A assistant over banking & financial regulation — answers with sources, or refuses when the regulations don't cover it.**

Risk and compliance teams at banks and fintechs work across overlapping regulatory layers — federal *(FFIEC, OCC, FDIC, Federal Reserve SR/CA guidance, Dodd-Frank, SOX)*, prudential *(Basel III capital + LCR/NSFR liquidity)*, and financial-crime *(BSA/AML, OFAC)*. The authoritative text lives across thousands of public PDFs and rulebooks that update on independent cadences. Analysts burn hours answering *"what does the rule require for **this** institution, **this** exposure, **this** scenario — and where exactly does it say so?"*

**RegIntel** is the co-pilot for that work: ask a question, get a citation-grounded answer over a governed regulatory corpus, or an explicit refusal when the source text doesn't support an answer. Built to the standards that matter in regulated environments — **traceable sources, no hallucinated guidance, an auditable eval harness.**

> **Domain note:** this is the financial/banking-regulatory sibling of [ComplianceIQ](https://github.com/arbabc-ai/ComplianceIQ) (the same retrieval architecture applied to childcare compliance). RegIntel retargets the proven pattern at the author's career domain — regulated banking data modernization (Basel III / LCR) — and runs it on an **AWS Bedrock-native** stack.

---

## Value chain — DE → Data → RAG → Agent

```
┌────────────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────────┐
│   DE LAYER     │    │  DATA LAYER  │    │  RAG LAYER  │    │  AGENT LAYER │
│  (Phase 2)     │    │  (Phase 2)   │    │  (Phase 1)  │    │  (Phase 3)   │
├────────────────┤    ├──────────────┤    ├─────────────┤    ├──────────────┤
│ Ingest FFIEC / │ →  │ Warehouse of │ →  │ Vector +    │ ←─ │ LLM router:  │
│ OCC / Basel /  │    │ normalized   │    │ lexical     │ ←─ │ structured?  │
│ Fed SR-letters │    │ thresholds:  │    │ retrieval   │ ←─ │ → warehouse  │
│ + Dodd-Frank   │    │ capital      │    │ over raw    │ ←─ │ lookup       │
│ PDFs. S3 land  │    │ ratios, LCR  │    │ regulatory  │ ←─ │ unstructured?│
│ → chunk →      │    │ buffers,     │    │ text +      │ ←─ │ → RAG        │
│ metadata →     │    │ reporting    │    │ citations   │ ←─ │ tool-calling │
│ embeddings     │    │ deadlines    │    │ + refusals  │ ←─ │ + compose    │
└────────────────┘    └──────────────┘    └─────────────┘    └──────────────┘
        ↑                    ↑                  ↑                    ↑
  S3 + chunking         Postgres /         Bedrock KB /        Bedrock Agent
  + metadata            OpenSearch         OpenSearch          + Lambda action
  pipeline              Serverless         Serverless          groups + Guardrails
```

**Phases 1 and 2 (this repo) are built and runnable**: the RAG retrieval + citation/refusal + eval layer, and the DE/warehouse layer of LLM-extracted regulatory thresholds. **Phase 3 is designed** (see [`docs/architecture.md`](docs/architecture.md)) — the structured-vs-unstructured routing agent that would call both.

---

## AWS Bedrock-native stack

| Concern | This repo (Phase 1, runnable locally) | Production target (AWS-native) |
|---|---|---|
| Embeddings | **Ollama `nomic-embed-text`** (local, free) · or Amazon **Titan Text Embeddings** via `bedrock-runtime` · or sentence-transformers MiniLM | Titan v2 on Bedrock |
| Generation | **Ollama `qwen2.5:7b-instruct`** (local, free) · or **Claude on Bedrock** · or the Anthropic API — one env var switches | Claude on Bedrock + Knowledge Base |
| Vector store | Local index + BM25 hybrid retrieval (Reciprocal Rank Fusion) | **Bedrock Knowledge Base over OpenSearch Serverless** |
| Guardrails | System-prompt citation enforcement + explicit refusal | **Bedrock Guardrails** (denied topics, contextual grounding, PII redaction) |
| Eval | Claude-as-judge faithfulness harness (`eval/`) | Pinned-judge eval gate in CI |

This deliberately reuses the AIF-C01 hands-on work (Bedrock Converse, inference profiles, model-access grants) — RegIntel *is* the portfolio proof that the cert concepts are real.

---

## Quickstart — free and fully local (Ollama)

No API keys, no cloud spend, and no document or question ever leaves the machine — the deployment shape a bank's data-governance team will actually approve for a pilot.

```bash
ollama pull qwen2.5:7b-instruct-q4_K_M && ollama pull nomic-embed-text

python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env          # uncomment the LLM_PROVIDER=ollama / EMBED_PROVIDER=ollama block

python -m scripts.download_corpus     # fetch the public regulatory corpus (eCFR)
python -m src.ingest                  # chunk → embed → Chroma + BM25 index
python -m src.cli "What minimum liquidity coverage ratio must a covered institution maintain?"
python -m eval.eval > eval/results.md # run the eval harness → report
```

### Real output (local Ollama run, CPU-only laptop)

```
$ python -m src.cli "What minimum liquidity coverage ratio must a covered institution maintain?"

=== ANSWER ===
The minimum liquidity coverage ratio that a covered institution must maintain is 1.0. This requirement
applies on each business day (or, in the case of a Category IV Board-regulated institution, on the last
business day of the applicable month) [source: cfr_title12_part249_Regulation-WW-Liquidity-Coverage-Ratio.txt].

=== SOURCES ===
  - cfr_title12_part249_Regulation-WW-Liquidity-Coverage-Ratio.txt

[retrieval mode: hybrid, top-k: 5, chunks used: 5]
```

Index: 4 regulations → 3,050 chunks (800 chars, 100 overlap) in Chroma + BM25. On a CPU-only machine a
7B local model answers in ~1.5 min; on a GPU, or on Claude via Bedrock, it's a few seconds.

**Switching to AWS Bedrock** is configuration only: set `BEDROCK_INFERENCE_PROFILE` (Claude on Bedrock) and `USE_BEDROCK_EMBEDDINGS=1` (Titan) instead of the Ollama variables, then re-run `python -m src.ingest`. Same retrieval, prompts, citations, and eval.

---

## Corpus (Phase 1)

Public-domain U.S. federal regulation, pulled live from the **eCFR API**:

| Document | What it covers |
|---|---|
| 12 CFR Part 217 — **Regulation Q** | Capital adequacy (Basel III capital rules as implemented in the U.S.): CET1 / Tier 1 / total capital ratios, buffers, risk weights |
| 12 CFR Part 249 — **Regulation WW** | Liquidity Coverage Ratio (U.S. Basel III LCR): HQLA, outflow rates, the 100% minimum |
| 12 CFR Part 252 — **Regulation YY** | Enhanced prudential standards: stress testing, risk management, liquidity for large bank holding companies |
| 31 CFR Part 1020 — **BSA/AML** (FinCEN) | Bank Secrecy Act / anti-money-laundering: SAR filing, customer identification program |

~1.8 MB of regulatory text. The BIS Basel framework PDFs are also listed in [`scripts/download_corpus.py`](scripts/download_corpus.py), but bis.org answers scripted downloads with a bot-challenge page — the downloader detects that and skips them rather than indexing HTML as a "PDF". To include them (or FFIEC handbooks, Fed SR letters), drop the files into `data/raw/` and re-run `python -m src.ingest`.

---

## Phase 2 — regulatory-thresholds warehouse

Some regulatory questions ("what's the minimum CET1 ratio?") are a **lookup**, not a search — the answer is a single number with a scope and a citation, and RAG's job (retrieve similar passages, let a model compose an answer) is the wrong tool for it. Phase 2 builds the warehouse side of the architecture in `docs/architecture.md`: an LLM reads the already-ingested chunks, pulls out every discrete numeric threshold it finds (a capital ratio, a liquidity ratio, a buffer, a filing deadline), and writes it into a small SQLite star schema — `dim_regulations`, `dim_metrics`, `fct_regulatory_thresholds` — with the exact source passage attached to every row as `citation_excerpt`. Nothing in the warehouse is composed by the model; it only extracts, and every value is traceable back to raw text.

```bash
python -m scripts.extract_thresholds     # LLM-assisted extraction → data/warehouse.sqlite3
python -m src.lookup "CET1"               # deterministic lookup, no LLM call, full citation
python -m eval.eval_thresholds > eval/thresholds_results.md   # check extracted values against the raw text
```

### Real output

```
$ python -m src.lookup "CET1"

=== THRESHOLDS MATCHING 'CET1' ===

  Minimum common equity tier 1 (CET1) capital ratio: 4.0%
    Scope: Board-regulated institution (From January 1, 2014 to December 31, 2014)
    Source: 12 CFR Part 217 — Regulation Q — Capital Adequacy
    Citation: cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt::chunk-0018
    As of: 2026-09-18

  Minimum common equity tier 1 (CET1) capital ratio: 6.0%
    Scope: Advanced approaches Board-regulated institution (From January 1, 2015 to December 31, 2017)
    Source: 12 CFR Part 217 — Regulation Q — Capital Adequacy
    Citation: cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt::chunk-0018
    As of: 2026-09-18

  Minimum common equity tier 1 (CET1) capital ratio: 4.5%
    Scope: Board-regulated institution
    Source: 12 CFR Part 217 — Regulation Q — Capital Adequacy
    Citation: cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt::chunk-0226
    As of: 2026-09-18
```

Three rows, not one — and that's correct, not noisy. Reg Q's capital rules phased in over 2014–2017 before landing at today's steady-state ratios, and the extractor pulled all three provisions it found, each with its own scope and citation, rather than collapsing them into a single answer. A real compliance analyst needs exactly this: which ratio applied *when*, not just what it is today.

`eval/thresholds_results.md` checks the steady-state values (4.5% CET1, 6% Tier 1, 8% total capital, 2.5% conservation buffer, 1.0 LCR) directly against the raw eCFR text — **5/5 matched**, each with an exact chunk citation.

`as_of_date` is the extraction date, not a rule-publication date — the eCFR corpus this repo pulls is "current as of fetch", not versioned by amendment. A production warehouse (S3 raw zone + checksum-tracked incremental ingest, per `docs/architecture.md`) would carry a true `effective_date` per amendment and let a threshold answer be asked *as of* a historical date, which is what a model-risk audit trail actually needs.

**A real defect this caught, left in on purpose:** the naive version of this pipeline sent one 800-character chunk at a time to the extractor. Regulation text is dense enough that a value routinely lands right at a chunk boundary — one candidate chunk ended `"...maintain a minimum common equity ti"`, cutting the number away from its own label. The extractor filled the gap with the next number it saw and mislabeled it. Fix: `scripts/extract_thresholds.py` checks whether a candidate chunk ends mid-sentence and, if so, pulls in enough of the next chunk to complete it before extraction — the same class of fix RAG chunk-overlap exists for, applied to structured extraction instead of retrieval.

**A second one, not fixed — left in the warehouse rather than quietly cleaned up:** run `python -m src.lookup "buffer"` and one row reads *"Leverage buffer requirement: 50.0%, global systemically important BHC"*. The actual rule (`Regulation-Q-Capital-Adequacy.txt::chunk-0271`) is *"the lesser of 1.0 percent or **50 percent of** the most recent [GSIB] surcharge"* — a formula referencing another value, not a flat 50% requirement. The extractor has no notion of conditional/formulaic thresholds and read "50 percent" as one. This is a real, current limitation of the extraction prompt, not a display bug, and it's exactly the kind of failure a warehouse consumer needs surfaced rather than silently corrected: see "What's missing for production" in `docs/architecture.md`.

---

## What this demonstrates (interview-ready)

- **Data engineering over messy real-world documents:** ingestion, recursive chunking with overlap, metadata tagging, incremental indexing — the same skills as regulated-data ETL, retargeted at unstructured regulatory text.
- **Production-pattern RAG on Bedrock:** Titan embeddings + Claude Converse, hybrid (dense + BM25) retrieval with RRF, citation enforcement, explicit refusal on insufficient context.
- **Responsible AI in a regulated domain:** grounding checks, refusal-aware evaluation, no hallucinated compliance advice — the exact discipline a bank's model-risk function expects.
- **An auditable eval harness:** faithfulness scored per answer, results tracked over prompt changes.
- **Structured extraction from unstructured text (Phase 2):** LLM-assisted extraction of numeric thresholds into a queryable star schema, every value traceable to a source excerpt — the DE half of the value chain, not just the RAG half.

Author: Arbab Chowdhury — regulated financial-data modernization (Basel III / LCR) + GenAI. [github.com/arbabc-ai](https://github.com/arbabc-ai)
