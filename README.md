# RegIntel — Regulatory Intelligence Platform

[![CI (fast)](https://github.com/arbabc-ai/RegIntel/actions/workflows/ci.yml/badge.svg)](https://github.com/arbabc-ai/RegIntel/actions/workflows/ci.yml)
[![Full eval (Ollama, weekly)](https://github.com/arbabc-ai/RegIntel/actions/workflows/full-eval.yml/badge.svg)](https://github.com/arbabc-ai/RegIntel/actions/workflows/full-eval.yml)

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

**All three phases (this repo) are built and runnable**: the RAG retrieval + citation/refusal + eval layer, the DE/warehouse layer of LLM-extracted regulatory thresholds, and the routing agent that decides which one a question needs.

---

## AWS Bedrock-native stack

| Concern | This repo (Phase 1, runnable locally) | Production target (AWS-native) |
|---|---|---|
| Embeddings | **Ollama `nomic-embed-text`** (local, free) · or Amazon **Titan Text Embeddings** via `bedrock-runtime` · or sentence-transformers MiniLM | Titan v2 on Bedrock |
| Generation | **Ollama `qwen2.5:7b-instruct`** (local, free) · or **Claude on Bedrock** · or the Anthropic API — one env var switches | Claude on Bedrock + Knowledge Base |
| Vector store | Local index + BM25 hybrid retrieval (Reciprocal Rank Fusion) | **Bedrock Knowledge Base over OpenSearch Serverless** |
| Guardrails | System-prompt citation enforcement + explicit refusal | **Bedrock Guardrails** (denied topics, contextual grounding, PII redaction) |
| Eval | Claude-as-judge faithfulness harness (`eval/`), CI-gated (`.github/workflows/full-eval.yml`) | Same gate, Bedrock-hosted judge |

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
$ python -m src.lookup "buffer"

=== THRESHOLDS MATCHING 'buffer' ===

  Leverage buffer: FORMULA (not a flat number) — the lesser of 1.0 percent or 50 percent of the most
  recent method 1 surcharge (expressed as a percentage) that the global systemically important BHC
  that controls the state member bank was required to calculate pursuant to § 217.403(b)
    Scope: state member banks that are subsidiaries of global systemically important BHCs
    Source: 12 CFR Part 217 — Regulation Q — Capital Adequacy
    Citation: cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt::chunk-0271
    As of: 2026-09-18

  Leverage buffer: FORMULA (not a flat number) — Less than or equal to 75 percent of the global
  systemically important BHC's leverage buffer requirement under 12 CFR 217.11, and greater than 50
  percent of the global systemically important BHC's leverage buffer requirement under 12 CFR 217.11
    Scope: U.S. global systemically important banking organizations
    Source: 12 CFR Part 252 — Regulation YY — Enhanced Prudential Standards
    Citation: cfr_title12_part252_Regulation-YY-Enhanced-Prudential-Standards.txt::chunk-0239
    As of: 2026-09-18
```

That first row used to be the bug: an earlier version of this pipeline read *"the lesser of 1.0 percent or **50 percent of** the most recent [GSIB] surcharge"* and reported it as a flat *"Leverage buffer requirement: 50.0%"* — a real, wrong number, because the extraction prompt had no notion of a threshold defined relative to another value. **Fixed**: the prompt now distinguishes a flat threshold from a formulaic one, and a formulaic row carries the exact quoted expression (`formula_expr` in the warehouse) instead of a value — `value`/`unit` are `NULL` for these rows, not a guess. Re-running extraction with the fix found a *second* real formula this repo hadn't even manually caught before: Reg YY's graduated leverage-buffer schedule (75%/50%/25% tiers of the GSIB's own buffer requirement), correctly quoted rather than misreported as three unrelated flat percentages.

`eval/thresholds_results.md` checks 5 steady-state values (4.5% CET1, 6% Tier 1, 8% total capital, 2.5% conservation buffer, 1.0 LCR) directly against the raw eCFR text — **4/5 matched** this run. The miss is understood, not a mystery: extraction ranks candidate passages by a distinctiveness score (see below) and caps how many it sends to the LLM per regulation; the specific passage stating the 2.5% conservation-buffer default ranks 88th of 243 candidates in Reg Q, outside the current cap of 50. Raising the cap to catch it would mean re-running the ~50-minute extraction for one more passage — a real trade-off between coverage and cost, made explicitly rather than hidden. `python -m src.lookup "common equity"` still shows two correctly-extracted CET1 provisions (the 2014 transitional 4.0% and the steady-state 4.5%), each with its own scope and citation — Reg Q's capital rules phased in over 2014–2017 before landing at today's ratios, and the extractor keeps both provisions rather than collapsing them into one answer.

`as_of_date` is the extraction date, not a rule-publication date — the eCFR corpus this repo pulls is "current as of fetch", not versioned by amendment. A production warehouse (S3 raw zone + checksum-tracked incremental ingest, per `docs/architecture.md`) would carry a true `effective_date` per amendment and let a threshold answer be asked *as of* a historical date, which is what a model-risk audit trail actually needs.

**A real defect this caught, left in on purpose:** the naive version of this pipeline sent one 800-character chunk at a time to the extractor. Regulation text is dense enough that a value routinely lands right at a chunk boundary — one candidate chunk ended `"...maintain a minimum common equity ti"`, cutting the number away from its own label. The extractor filled the gap with the next number it saw and mislabeled it. Fix: `scripts/extract_thresholds.py` checks whether a candidate chunk ends mid-sentence and, if so, pulls in enough of the next chunk to complete it before extraction — the same class of fix RAG chunk-overlap exists for, applied to structured extraction instead of retrieval.

**How candidates are chosen, and what changed:** the original version just took the first N regex-matched chunks per regulation, in document order — cheap, and mostly wrong, since a long regulation's early chunks are disproportionately definitions and cross-references. Replaced with `_rank_by_distinctiveness()`: score each candidate by how many *rare numeric tokens* (IDF-weighted) it contains, since the operative rule usually states a number that appears in only one or two places, while boilerplate and cross-references restate the same numbers everywhere. A first version of this scorer used ordinary word tokens and completely ignored digits — verified empirically before shipping (not assumed), it ranked four passages this repo already knew mattered between position 137 and 172 of 243 candidates, nowhere near any realistic cap. Restricting the score to numeric tokens put the same four passages at ranks 5, 29, 38, and 46. Extraction went from 19 threshold records (10 distinct metrics) to **63 records (39 distinct metrics)** on the same corpus, at roughly double the candidate budget (85 vs. 49) and roughly double the run time (~50 min vs. ~14 min on this CPU-only laptop) — a real, bounded cost for real, substantially better coverage, not a free win.

---

## Phase 3 — routing agent

Phase 1 (RAG) and Phase 2 (the warehouse) each answer a different shape of question well and the other shape badly. "What's the minimum CET1 ratio" is a lookup — RAG will retrieve a paraphrase and an LLM will compose an approximate answer where an exact one exists. "What does Regulation YY require for stress testing, and why" is guidance — the warehouse has no row for it. Phase 3 is the router: an LLM sees the question and two tools, `lookup_threshold` (Phase 2) and `search_guidance` (Phase 1's `retrieve()`), decides which to call — via real tool-calling, not a keyword heuristic — and a second LLM turn composes the final answer strictly from what the tools returned, under the same citation/refusal discipline as Phase 1.

```bash
python -m src.agent "What minimum liquidity coverage ratio must a covered institution maintain?"
python -m eval.eval_agent > eval/agent_results.md   # routing + behavior check across 5 real questions
```

### Real output — three different questions, three different routes

```
$ python -m src.agent "What minimum liquidity coverage ratio must a covered institution maintain?"
=== TOOLS CALLED ===
  - lookup_threshold({'metric': 'liquidity coverage ratio'})
=== ANSWER ===
The minimum liquidity coverage ratio that a covered institution must maintain is 1.0 ratio, as
specified for Board-regulated institutions [source: cfr_title12_part249_Regulation-WW-Liquidity-
Coverage-Ratio.txt::chunk-0086].

$ python -m src.agent "What does Regulation YY require for company-run stress testing?"
=== TOOLS CALLED ===
  - search_guidance({'question': 'What does Regulation YY require for company-run stress testing?'})
=== ANSWER ===
[grounded answer citing cfr_title12_part252_Regulation-YY-Enhanced-Prudential-Standards.txt —
correctly routed to guidance search, not a threshold lookup, since there's no single number to return]

$ python -m src.agent "How do I calculate my personal income tax?"
=== TOOLS CALLED ===
  - search_guidance({'question': 'How do I calculate my personal income tax?'})
=== ANSWER ===
The provided sources don't contain enough information to answer that.
```

The third case is the one that matters most: `search_guidance` still ran (the router doesn't know in advance that nothing relevant exists) and returned its nearest chunks anyway — retrieval always returns *something*. The final-compose step correctly refused rather than stretching those unrelated chunks into an answer.

**A real bug this eval caught, three levels deep — the eval itself needed debugging as much as the agent did.** `eval/agent_results.md` (now **5/5 tool routing, 5/5 behavior**) runs 5 real questions: two lookups, one mixed, and two questions expected to be refused. One of those two was originally "What is the net stable funding ratio (NSFR) requirement?", copied from Phase 1's refusal set on the assumption *"NSFR not bundled → should refuse."* The agent answered it correctly, and a first version of this eval flagged that correct answer as a failure — chasing why took three fixes, not one:
1. **The ground-truth assumption was never checked.** 12 CFR Part 249 (Regulation WW) covers *both* the LCR and the NSFR — grep `data/raw/cfr_title12_part249_...` for `§ 249.100` and you'll find "A Board-regulated institution must maintain a net stable funding ratio that is equal to or greater than 1.0." Phase 1's `eval/questions.yaml` carried the same wrong assumption; both are now fixed (Phase 3's NSFR check flipped to expect a real answer; Phase 1's swapped to the Volcker Rule, verified genuinely absent from the bundled corpus) and `eval/results.md` regenerated — Phase 1's own refusal accuracy went from 2/3 to **3/3** as a direct result.
2. **A loose keyword check was too crude.** It flagged any answer containing `"don't contain enough information"` as a refusal — but `src/agent.py`'s final-compose step sometimes opens with that exact phrase as a rhetorical hedge, then gives a fully correct, grounded answer anyway (*"...don't contain enough information to answer that, as the minimum CET1 ratio varies depending on..."* — followed by the real, correct, cited numbers). A substring match can't tell a genuine refusal from a hedge-then-answer.
3. **The fix for #2 — an LLM judge — was tried and made things worse, not better.** Same technique as Phase 1's `eval/eval.py` faithfulness scoring: ask a judge model to classify each answer as CORRECT / REFUSED / WRONG against the real tool evidence. On this local 7B model it called a textbook-perfect, one-sentence refusal *"WRONG"*, and a fully correct, well-cited multi-part answer *"WRONG"* too. Tightening the generation prompt to forbid the hedge phrasing was tried next and overcorrected the other way — the model started refusing questions it could actually answer. **What actually worked:** check for the *exact* canonical refusal sentence (the one literally in `FINAL_SYSTEM`, with its period) as a substring, not a loose phrase or an LLM's judgment call. The hedge pattern always continues past "...that" with a comma or the next word, never that exact period; a genuine refusal has the period right there regardless of what it adds afterward. Simpler than an LLM judge, and it's the one that actually holds up against real model output — see `eval/eval_agent.py`'s docstring for the full trail.

**What's demo-scale here, not production:** this only wires up Ollama's native tool-calling. The documented production target — Bedrock Agent with Claude tool-use via the Converse API and Lambda action groups — is designed in `docs/architecture.md` but not implemented; `src/generate.py`'s existing Bedrock branch uses the raw `invoke_model` call, which doesn't support tool use, so swapping providers here isn't a one-line env var change the way Phases 1 and 2 are.

---

## Continuous integration

`docs/architecture.md` names two "what's missing for production" items directly: *"pinned-judge eval gate in CI"* and *"re-run the eval gate on every prompt change."* Both are built, split into two workflows for a real reason, not decoration:

| Workflow | Runs on | What it does |
|---|---|---|
| [`ci.yml`](.github/workflows/ci.yml) ("CI (fast)") | Every push / PR to `main` | Installs the package, compiles every module, imports every entry point. No Ollama, no corpus download, no LLM calls — a wiring check, not the eval gate. Runs in under a couple of minutes. |
| [`full-eval.yml`](.github/workflows/full-eval.yml) ("Full eval") | Manual (`workflow_dispatch`) + weekly (Monday 06:00 UTC) | The real gate: installs Ollama, pulls both models, downloads the corpus, ingests, runs `scripts.extract_thresholds`, then all three eval suites with `--gate`, which exits 1 on a genuine regression. Uploads `eval/*.md` as artifacts either way. |

**Why not run the full eval on every push:** the full pipeline — corpus fetch, chunk embedding, LLM threshold extraction, then 21 real LLM-generated answers across three eval suites — takes 45–70+ minutes on a CPU-only GitHub runner, the same shape of cost this README's own quickstarts show by hand. Blocking every PR on that is the wrong trade for a repo this size; a fast wiring check on every push plus a real, scheduled/on-demand gate is the standard split (this repo is public, so Actions minutes are free here — the constraint is wall-clock and reviewer attention, not cost).

**Why the gate thresholds aren't 100%**, even though the RAG and agent evals currently score it (the thresholds eval is at 4/5 and its gate sits exactly there, with no headroom, on purpose): local LLM generation isn't perfectly deterministic run to run, even at `temperature=0` (context caching, minor Ollama version drift), and Phase 2's extraction is itself a separate LLM pass whose candidate coverage can vary slightly. `eval.eval --gate`, `eval.eval_thresholds --gate`, and `eval.eval_agent --gate` each set their threshold below the perfect score (the thresholds gate at the current 4/5) — high enough to catch a genuine break (the corpus failing to download, a prompt change that actually breaks grounding), low enough to not flake the build on a single-question wobble. Every threshold and the reasoning behind it is a comment right next to the constant in each eval file.

`--gate` only changes the exit code; the printed report is identical either way, so the exact same `python -m eval.eval > eval/results.md` commands in this README's quickstarts still work unchanged — the CI workflow adds `--gate` on top of them, not a different code path.

---

## What this demonstrates (interview-ready)

- **Data engineering over messy real-world documents:** ingestion, recursive chunking with overlap, metadata tagging, incremental indexing — the same skills as regulated-data ETL, retargeted at unstructured regulatory text.
- **Production-pattern RAG on Bedrock:** Titan embeddings + Claude Converse, hybrid (dense + BM25) retrieval with RRF, citation enforcement, explicit refusal on insufficient context.
- **Responsible AI in a regulated domain:** grounding checks, refusal-aware evaluation, no hallucinated compliance advice — the exact discipline a bank's model-risk function expects.
- **An auditable eval harness:** faithfulness scored per answer, results tracked over prompt changes.
- **Structured extraction from unstructured text (Phase 2):** LLM-assisted extraction of numeric thresholds into a queryable star schema, every value traceable to a source excerpt — the DE half of the value chain, not just the RAG half.
- **Tool-calling agent routing (Phase 3):** real function-calling, not a keyword heuristic, deciding between a structured lookup and a semantic search per question, with a grounded, refusal-capable final compose step over whichever tool(s) fired.
- **CI that actually gates, split by real cost:** a fast wiring check on every push, a real scheduled/on-demand eval gate that fails the build on regression — with the trade-offs (why split, why the thresholds aren't 100%) written down next to the code, not just asserted.

Author: Arbab Chowdhury — regulated financial-data modernization (Basel III / LCR) + GenAI. [github.com/arbabc-ai](https://github.com/arbabc-ai)
