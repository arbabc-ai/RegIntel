# regintel_p1 — Phase 1 provision parser, evidence index, retrieval evaluation

Standard library only. No model, embedding, network or cloud call. Reads the corpus written by
`scripts/corpus_download.py` (`/home/arbab/projects/regintel-corpus/phase1/manifest.json`) and writes a SQLite
index outside Git and OneDrive (`/home/arbab/projects/regintel-corpus/phase1-index/regintel_p1.sqlite`).

```bash
python3 -I scripts/p1.py build                       # manifest -> SQLite (re-hashes every asset, skips mismatches)
python3 -I scripts/p1.py search "minimum CET1 ratio" --scope current -k 5
python3 -I scripts/p1.py delta "12 CFR 3.22"          # baseline vs each acquired future instrument
python3 -I scripts/p1.py eval --out results.json      # eval/phase1/questions.json
python3 -m unittest discover -s tests -p "test_*.py"
```

## Layers

| Module | Role |
|---|---|
| `ecfr.py` | eCFR HTML -> one provision per anchored paragraph, definition, section, appendix item, footnote and table (own text only; parent lead text kept as retrieval context; character offsets into the decoded source) |
| `fedreg.py` | Federal Register XML -> preamble passages (heading path, printed page) and amendment units (verbatim amendatory instruction paired to the section it names, plus the section text it carries) |
| `schema.py` | `source_document`, `rulemaking`, `provision` (+FTS5), `topic`, `provision_topic`, `relationship`, `rulemaking_link` |
| `topics.json` | shared topic catalog with synonyms, per-topic source coverage (`acquired`/`partial`/`not_acquired`) and provisional assignment rules |
| `build.py` | builds the index; derives `amends`, `references`, `uses_definition`, `defines` relationships and shared-RIN candidate links |
| `search.py` | BM25 (FTS5), topic-guided, exact-citation and RRF-hybrid retrievers; lifecycle labels; `answer()` and `delta()` |
| `evaluate.py` | retrieval hit@k/MRR per retriever, deterministic delta and refusal checks, universal safety invariants |

## Rules the code enforces

- Relationships carry `review_status` (`parsed_unreviewed`, `regex_unreviewed`, `candidate_unreviewed`, `unresolved_candidate`); none is reviewed.
- `amends` comes only from an instruction's explicit targets (verb-object, "In § N,", "§ N is amended", `consisting of`, part-level forms), read outside quotation marks. Any other citation in an instruction is a `references` row, never a target. Carried section text is paired only with an instruction that targets that section (`pairing_status`: `explicit`, `multiple_instructions_same_target`, `instruction_only`, `carried_text_unpaired`, `instruction_no_explicit_target`); unpaired text and its candidate instructions stay in `unresolved_candidates` / `amends_candidate` and are listed separately from `delta()` instruments.
- An instruction-only `A.x through A.y` range records numeric membership in `amendment_range` (unreviewed), so interior sections are discoverable through `delta()` (`match: range_membership`, with `range` and the complete instruction) and exact future lookup. Only plain integer minors of one part count; any other range is `unresolved_ambiguous` and asserts nothing between its endpoints.
- `answer()` returns `partial_evidence` whenever results coexist with a matched topic whose coverage is `partial` or `not_acquired`, or when **no** catalog topic matched (gap `none_matched`, coverage `unassessed`; a lexical hit does not establish coverage). With no results it returns `missing_evidence`, still carrying the gap. `evidence_found` means every matched topic is fully acquired, or the query is citation-only and its exact lookup resolves in the requested scope and part filter. `liquidity_lcr` and `liquidity_nsfr` are `partial`: acquired Fed Part 249 does not establish coverage of the missing OCC/FDIC sources.
- Each citation in a query is resolved independently (`resolve_citations`) in the requested scope and part filter, before truncation; records are interleaved across citations so none crowds out another. An unresolved citation gets its own gap `citation:<label>` (coverage `unresolved_citation`) with a reason (`part_not_acquired`, `not_found_in_acquired_part`, `excluded_by_part_filter`, `excluded_by_requested_scope`, `excluded_by_scope_and_part_filter`, `paragraph_not_found_in_acquired_section`, `paragraph_precision_unresolved`), even when another citation resolved or an acquired topic matched. A missing paragraph is never replaced by its parent section in current scope; section-level amendment units stay discoverable but are flagged `paragraph_precision_unresolved` (paragraph precision there is an unreviewed text-pattern check of an amendment's leading designators). Every citation record states `legal_validity: not_determined`: unavailable evidence is not a finding that a citation is invalid. `evidence_found` for a citation-only query requires every citation resolved at the requested precision.
- A future-scope paragraph resolves only when an attributed amendatory instruction names the **complete** requested path as the object of an action (`explicit_paragraphs`, e.g. `(c)(1)`, `(a)(2)(i)`, `(i)`; quoted text and bare cross-references do not count; `(c)(5) through (7)` names its two ends, not the interior). A matching first designator, a lead of the carried text, or an ancestor never establishes a deeper path, hierarchy is not inferred from the order of carried paragraphs, and an instruction that targets several sections names no paragraph for any one of them. Otherwise the citation stays `resolved: false`, `achieved: section_only`, reason `paragraph_precision_unresolved`, with section-level amendment units still discoverable. Supporting units are listed in `future_paragraph_support`.
- A request that names a jurisdiction (names from the C3 `STATE_JURISDICTION_REGISTER.csv`, copied read-only into `jurisdictions.json` with the register's sha256 by `scripts/build_jurisdictions.py`) or states accounting intent (recognition/measurement of losses and similar, patterns in `source_requests.json`) always carries a `state_law` or `gaap_asc` `not_acquired` gap, **even when an acquired topic such as CET1 also matches**. `partial_evidence` if results exist (federal results are kept), else `missing_evidence`. The bundle's `source_requests` records what was matched and the register provenance; naming a jurisdiction asserts no state-law applicability, preemption or compliance. Federal bank names ("Federal Reserve Bank of New York") are not jurisdiction requests; abbreviations are not matched.
- `delta()` keeps the requested paragraph path. For a paragraph citation, `current_precision` and `future_precision` are reported separately (reusing `resolve_citations`). The baseline resolves only if the paragraph (or something beneath it) is in the current text; otherwise `baseline` is null and the section's baseline appears only as `section_context` (role `section_context_not_the_requested_paragraph`). Each instrument carries its own `paragraph_precision` and `role` (`paragraph_supported` or `section_context`), decided from that instrument's own units via the `explicit_paragraphs` contract; one instrument's support never establishes another's. A lettered section such as `3.4a` that is not acquired returns `evidence_status: unavailable` with a structured reason, `legal_validity: not_determined`, and no exception.
- Citation queries honor `scope` and `parts` in SQL before ranking and truncation; `future` returns only amendment units that explicitly amend the cited section/part, never a baseline record.
- Rulemaking state is a label computed from publisher metadata and an as-of date at query time. A proposal is never
  labelled final or current; closed comments are not finalization; a passed effective date is "operative effect
  unreviewed". `effective_on` on a proposal is not legal effect.
- `delta()` lists each future instrument separately and never merges them into one future rulebook.
- Every answer carries `applicability_status: not_assessed`. A topic match or a similarity score is evidence discovery,
  not applicability.
- Topics whose sources are not acquired (FASB ASC, state law, Call Report instructions) return `missing_evidence`, or
  `partial_evidence` with the gap reported when asked together with an acquired topic.

## Not done

No dense/neural retriever (no embedding model was available without installing packages or downloading weights), so
the "semantic" half of hybrid retrieval is the topic-catalog expansion, not vectors. The `Index.retrieve` interface and
`evaluate.RETRIEVERS` are where a vector retriever would be compared. No reranker, no relationship review, no
applicability engine, no parsing of tables into cells beyond `|`-joined rows.
