# RegIntel — Architecture deep dive

This doc complements the README with the math + reasoning behind the choices, the AWS Bedrock-native target, and the Phase 2 + Phase 3 design intent.

## The full value chain (DE → Data → RAG → Agent)

RegIntel is built as four phases; only **Phase 1 (the RAG layer)** is in this repo.

### Phase 2 — DE + Data layer

**The DE problem:** Banking regulation comes from many sources on independent cadences — the Federal Reserve (Regulations Q/WW/YY), OCC and FDIC bulletins, FFIEC examination handbooks, the Basel Committee (BIS) framework, and BSA/AML rules under FinCEN. Thresholds (capital ratios, LCR/NSFR buffers, reporting deadlines) are scattered across thousands of pages of rule text and guidance. Manual tracking doesn't scale, and stale thresholds are a compliance risk.

**The ingestion shape (S3-native):**
- A scheduled job fetches per-part rule text (eCFR API), Fed SR/CA letters, and public BIS/FFIEC PDFs, lands them in **S3** (raw zone), extracts text, and tracks a per-document checksum so only changed parts re-ingest.
- Sensors guard against partial pulls; failures alert rather than silently dropping a source.

**The warehouse shape (medallion pattern):**
```
sources: raw_fed_regs_text, raw_basel_text, raw_ffiec_text, raw_sr_letters
   ↓ (staging)
stg_reg_chunks                          (1:1 with source, light cleanup + metadata)
   ↓ (intermediate)
int_extracted_thresholds                (LLM-assisted structured extraction:
                                         pulls capital ratios, LCR/NSFR buffers,
                                         reporting deadlines out of rule text)
   ↓ (marts)
fct_regulatory_thresholds               (regulation × institution_tier × metric ×
                                         effective_date, business-facing fact table)
dim_regulations, dim_institution_tiers, dim_metrics   (conformed dimensions)
```

**Why a warehouse beside the vector store:** "What's the minimum CET1 ratio for an advanced-approaches bank?" is a **lookup**, not a semantic search. Structured thresholds belong in a fact table with an `effective_date` so you can answer *as-of* questions and keep an audit trail of rule changes — exactly what a bank's model-risk and compliance functions expect.

### Phase 3 — Agent layer

**The agent problem:** Real regulatory queries mix structured + unstructured. *"What LCR must my institution hold, and what's the supervisory rationale?"* — the first half is a structured threshold lookup; the second is RAG over guidance text.

**The agent shape (Bedrock Agent + action groups):**
- An LLM router (Claude tool-use via Bedrock) inspects the query and decides which tools to call.
- Tool definitions (Lambda action groups):
  - `lookup_threshold(regulation, institution_tier, metric) → dict` — warehouse SQL query
  - `lookup_deadline(filing, institution_tier) → dict` — warehouse SQL query
  - `search_guidance(query, k=5) → list[chunk]` — the RAG pipeline in this repo
  - `check_applicability(institution_profile) → dict` — deterministic Python over the warehouse
- Final compose step: the agent receives all tool outputs, composes a grounded answer with citations from both sources, and **refuses when support is incomplete**.

**Why this shape:** Pure RAG can't answer a threshold question reliably ("what's the ratio?" is a lookup). Pure SQL can't answer an unstructured guidance question. The agent routes — and in a regulated domain, the routing + grounding + refusal discipline *is* the product value.

## AWS Bedrock-native mapping

| Layer | Phase 1 (this repo, runs locally) | Production target |
|---|---|---|
| Storage | local `data/raw/` | **S3** raw + curated zones |
| Embeddings | Titan Text Embeddings via `bedrock-runtime` (`USE_BEDROCK_EMBEDDINGS=1`), local MiniLM fallback | **Titan v2** on Bedrock |
| Vector store | local Chroma + BM25 hybrid (RRF) | **Bedrock Knowledge Base over OpenSearch Serverless** |
| Generation | **Claude on Bedrock** (`bedrock-runtime`), Anthropic SDK fallback | Claude on Bedrock + Knowledge Base retrieval |
| Guardrails | system-prompt citation enforcement + explicit refusal | **Bedrock Guardrails** (denied topics, contextual grounding, PII redaction) |
| Eval | Claude-as-judge faithfulness harness (`eval/`) | pinned-judge eval gate in CI |

This reuses the AIF-C01 hands-on work (Bedrock Converse/InvokeModel, inference profiles, model-access grants) — RegIntel is the portfolio proof those cert concepts are real.

## Why hybrid retrieval (BM25 + dense)

Dense (embedding) retrieval is strong on paraphrase; BM25 (lexical/tf-idf) is strong on exact terms and acronyms — which regulation is full of:

- Query: *"how much high-quality liquid assets must a bank hold?"*
  Document: *"...the liquidity coverage ratio requires HQLA sufficient to cover net cash outflows..."* — **dense** wins (semantic), **BM25** may miss (no shared keywords).
- Query: *"LCR HQLA Regulation WW"*
  Document: *"Regulation WW (12 CFR Part 249) establishes the LCR..."* — **BM25** wins (rare high-IDF terms `LCR`, `WW`); **dense** may rank a paraphrase equally.

Combining the two via **Reciprocal Rank Fusion** captures both regimes without weight tuning.

### RRF formula
```
score(d) = sum over rankers r:  1 / (k_rrf + rank_r(d))
```
`k_rrf` (60) flattens the contribution of top-ranked items so each ranker contributes meaningfully. Why 60? Cormack/Clarke/Buettcher (2009) tested 1–100 empirically; 60 is a robust default. Don't tune it.

## Chunking math

`RecursiveCharacterTextSplitter`, `chunk_size=800`, `chunk_overlap=100`, separators `["\n\n", "\n", ". ", " ", ""]`.

- **800 chars (~200 tokens):** regulation is dense and cross-referential; 800 keeps a rule and its immediate qualifier together while staying small enough for precise retrieval. Smaller (200–400) fragments multi-clause rules; larger (1500+) drags noise into the prompt.
- **100-char overlap:** prevents losing a threshold or condition that straddles a chunk boundary (common in rule text: *"...shall maintain a ratio of not less than..."* split mid-sentence).
- **Recursive (not fixed-size):** breaks at paragraph/sentence boundaries, so chunks rarely split mid-rule.

## Embeddings: Titan vs MiniLM

- **Local default (MiniLM-L6-v2):** free, CPU, 384-dim — a strong baseline for offline dev and CI.
- **Bedrock default (Titan Text Embeddings):** in-stack on AWS, no model to host, and the production answer when the vector store is Bedrock Knowledge Base / OpenSearch. Toggle with `USE_BEDROCK_EMBEDDINGS=1`.
- Either way, embeddings are computed once at ingest and cached in the index; the same embedder must serve queries (enforced by the shared module).

## Why Claude-as-judge for faithfulness eval

For this build, the canonical RAG eval framework ([RAGAS](https://docs.ragas.io)) is heavier than needed. Claude-as-judge gives a reasonable faithfulness signal in ~10 lines: show the judge the question, the answer, and the retrieved context; ask for a 0–3 score; average across the eval set. It's the same technique RAGAS uses internally for faithfulness. Trade-offs: cost (one call per question) and judge drift across model versions. For production: RAGAS or a pinned/fine-tuned judge, gated in CI.

## Citation enforcement + refusal pattern

The system prompt enforces grounding:
```
After each claim, attach a citation in the form [source: <doc_name>].
If the context does not contain enough information, refuse explicitly.
```
This is a soft pattern (the model can violate it), but Claude Sonnet follows it reliably when the rule is in the system prompt. **Production-grade enforcement:** post-process with regex, reject any sentence lacking a citation tag, and use **Bedrock Guardrails contextual-grounding** to block answers not supported by retrieved context. In a regulated domain, a confident-but-ungrounded answer is the worst failure mode — refusal is the correct default.

## What's missing for production

| Concern | Mitigation |
|---|---|
| Threshold staleness | Phase 2 warehouse with `effective_date` + checksum-tracked incremental ingest |
| Hallucinated guidance | Citation post-processing + Bedrock Guardrails contextual grounding; refuse + retry without context if unsupported |
| Auditability | S3 versioning + warehouse time-travel = an audit log of which rule version answered which query (model-risk requirement) |
| Cold-start latency | Pre-warm the vector client; cache BM25 in process |
| Access control | IAM least-privilege on `bedrock-runtime` (data plane) vs `bedrock` (control plane); CloudTrail on every inference |
| Eval drift | Pin the judge model version; re-run the eval gate on every prompt change |
| Scope creep into legal advice | Position as a *research/retrieval* aid, not regulatory counsel; refusal-by-default on un-sourced questions |
