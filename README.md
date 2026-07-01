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

**Phase 1 (this repo) is built and runnable**: the RAG retrieval + citation/refusal + eval layer.
**Phases 2–3 are designed** (see [`docs/architecture.md`](docs/architecture.md)) — the DE/warehouse layer and the structured-vs-unstructured routing agent.

---

## AWS Bedrock-native stack

| Concern | This repo (Phase 1, runnable locally) | Production target (AWS-native) |
|---|---|---|
| Embeddings | Amazon **Titan Text Embeddings** via `bedrock-runtime` (local sentence-transformers fallback for offline dev) | Titan v2 on Bedrock |
| Generation | **Claude on Bedrock** via the **Converse API** | Claude on Bedrock + Knowledge Base |
| Vector store | Local index + BM25 hybrid retrieval (Reciprocal Rank Fusion) | **Bedrock Knowledge Base over OpenSearch Serverless** |
| Guardrails | System-prompt citation enforcement + explicit refusal | **Bedrock Guardrails** (denied topics, contextual grounding, PII redaction) |
| Eval | Claude-as-judge faithfulness harness (`eval/`) | Pinned-judge eval gate in CI |

This deliberately reuses the AIF-C01 hands-on work (Bedrock Converse, inference profiles, model-access grants) — RegIntel *is* the portfolio proof that the cert concepts are real.

---

## Quickstart

```bash
cd ~/projects/regintel
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env          # fill in AWS creds / region (us-east-1)

python -m scripts.download_corpus     # fetch the public regulatory corpus
python -m src.cli ingest              # chunk → embed → index
python -m src.cli ask "What is the minimum LCR a covered institution must maintain?"
python -m eval.eval                   # run the faithfulness eval harness
```

---

## Corpus (Phase 1)

Public-domain regulatory sources only (no licensed/paywalled rulebooks):
- **Basel III** framework documents (BIS, public)
- **FFIEC** examination handbooks
- **Federal Reserve** SR / CA guidance letters
- **OCC / FDIC** public bulletins
- **SEC / SOX** public rule text

*(Exact source list + retrieval scripts in [`scripts/download_corpus.py`](scripts/download_corpus.py).)*

---

## What this demonstrates (interview-ready)

- **Data engineering over messy real-world documents:** ingestion, recursive chunking with overlap, metadata tagging, incremental indexing — the same skills as regulated-data ETL, retargeted at unstructured regulatory text.
- **Production-pattern RAG on Bedrock:** Titan embeddings + Claude Converse, hybrid (dense + BM25) retrieval with RRF, citation enforcement, explicit refusal on insufficient context.
- **Responsible AI in a regulated domain:** grounding checks, refusal-aware evaluation, no hallucinated compliance advice — the exact discipline a bank's model-risk function expects.
- **An auditable eval harness:** faithfulness scored per answer, results tracked over prompt changes.

Author: Arbab Chowdhury — regulated financial-data modernization (Basel III / LCR) + GenAI. [github.com/arbabc-ai](https://github.com/arbabc-ai)
