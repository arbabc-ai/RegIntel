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
- A requested citation is read once, by one parser (`parse_query_citations`), for `citation_only`, `resolve_citations` (hence exact/hybrid and `answer`) and `delta`. Whitespace inside a complete designator (`3.1(c)( 4 )(i)`, tabs, newlines) is normalized for lookup; the record keeps `requested` (as asked) and `canonical`. An unclosed or malformed designator (`3.22(a)(999`) or one set apart by whitespace (`3.1(c)(4) (i)`, `3.22 (a)`) is never read as its own ancestor: the request is an explicit unresolved citation labelled as asked, with reason `unclosed_or_malformed_designator_suffix` or `detached_designator_suffix`, `diagnostics` (valid prefix, unread suffix, `accepted_valid_prefix: false`), a coverage gap, and no records; the section it sits in appears only as a count under `context_available`. `legal_validity` stays `not_determined`. An ordinary trailing parenthetical (`(introductory text)`) is not a suffix.
- Designators are read whole: whitespace inside a designator (`( 1 )`, `( xiii )`, left by newlines or tabs around inline XML emphasis) is closed in a parsing view only; the stored instruction text, source bytes and hashes are unchanged. A path followed by an unclosed or whitespace-separated designator fragment (`(C)(1`, `(C) (1)`) is a valid prefix of something unread and is never accepted as a named parent; the rest of that list stays unresolved. A parenthetical that is not a designator (`(introductory text)`) does not block the path.
- Abbreviated designators in an instruction's paragraph list inherit a parent only where the CFR hierarchy (letter, digit, roman, upper, digit, roman) supports it: `(c)(5) through (7)` names (c)(5) and (c)(7) (not the interior), `(c)(4)(i), (e) and (f)` names (c)(4)(i), (e), (f) (never (c)(4)(e)), `(b)(1), (c), (d)` names (b)(1), (c), (d). Shorthand that fits two readings (`(a)(1)(i) and (v)`: roman or letter) is left unresolved, and so is every bare designator after it until a fully written path resumes the list. Section lists are consumed completely, including Oxford-comma lists (`§§ 302.1, 302.2, and 302.3` names all three); a list is not a range, and quoted lists remain references.
- A future-scope paragraph resolves only when an attributed amendatory instruction names the **complete** requested path as the object of an action (`explicit_paragraphs`, e.g. `(c)(1)`, `(a)(2)(i)`, `(i)`; quoted text and bare cross-references do not count; `(c)(5) through (7)` names its two ends, not the interior). A matching first designator, a lead of the carried text, or an ancestor never establishes a deeper path, hierarchy is not inferred from the order of carried paragraphs, and an instruction that targets several sections names no paragraph for any one of them. Otherwise the citation stays `resolved: false`, `achieved: section_only`, reason `paragraph_precision_unresolved`, with section-level amendment units still discoverable. Supporting units are listed in `future_paragraph_support`.
- A request that names a jurisdiction (names from the C3 `STATE_JURISDICTION_REGISTER.csv`, copied read-only into `jurisdictions.json` with the register's sha256 by `scripts/build_jurisdictions.py`) or states accounting intent (recognition/measurement of losses and similar, patterns in `source_requests.json`) always carries a `state_law` or `gaap_asc` `not_acquired` gap, **even when an acquired topic such as CET1 also matches**. `partial_evidence` if results exist (federal results are kept), else `missing_evidence`. The bundle's `source_requests` records what was matched and the register provenance; naming a jurisdiction asserts no state-law applicability, preemption or compliance. Federal bank names ("Federal Reserve Bank of New York") are not jurisdiction requests; abbreviations are not matched.
- `delta()` keeps the requested paragraph path. For a paragraph citation, `current_precision` and `future_precision` are reported separately (reusing `resolve_citations`). The baseline resolves only if the paragraph (or something beneath it) is in the current text; otherwise `baseline` is null and the section's baseline appears only as `section_context` (role `section_context_not_the_requested_paragraph`). Each instrument carries its own `paragraph_precision` and `role` (`paragraph_supported` or `section_context`), decided from that instrument's own units via the `explicit_paragraphs` contract; one instrument's support never establishes another's. A lettered section such as `3.4a` that is not acquired returns `evidence_status: unavailable` with a structured reason, `legal_validity: not_determined`, and no exception.
- One current-baseline lookup (`Index.current_baseline`) serves `resolve_citations`, `exact`, `answer` and `delta`. An acquired exact section is always preferred. Otherwise a numeric section inside an acquired eCFR range section (e.g. `3.5` in `3.4-3.9`) resolves to that range record, which keeps the publisher's own locator, citation, hash and dates and is labelled `match: range_section` with `range_membership` (requested section, range locator, endpoints, and a note that reserved text is acquired evidence, not an operative obligation). A range needs the same title and part, numeric ordered endpoints within one part, and the requested part filter; lettered sections never match a range, and a missing paragraph is never resolved through its section or range ancestor (reason `paragraph_not_found_in_acquired_range`). Hybrid retrieval pins the first record of every resolved citation ahead of the fused ranking so a lexical hit from elsewhere cannot crowd it out of a small k.
- Citation queries honor `scope` and `parts` in SQL before ranking and truncation; `future` returns only amendment units that explicitly amend the cited section/part, never a baseline record.
- Rulemaking state is a label computed from publisher metadata and an as-of date at query time. A proposal is never
  labelled final or current; closed comments are not finalization; a passed effective date is "operative effect
  unreviewed". `effective_on` on a proposal is not legal effect.
- `delta()` lists each future instrument separately and never merges them into one future rulebook.
- Every answer carries `applicability_status: not_assessed`. A topic match or a similarity score is evidence discovery,
  not applicability.
- Topics whose sources are not acquired (FASB ASC, state law, Call Report instructions) return `missing_evidence`, or
  `partial_evidence` with the gap reported when asked together with an acquired topic.

## Evaluation exit status

`python3 -I scripts/p1.py [--db …] [--as-of …] eval [--out report.json]` writes `--out` first, prints the report with an
explicit `Overall evaluation: PASS|FAIL` section, and exits **0 only when the overall result passes**, otherwise 1. The overall
result fails on any failed frozen delta or status check, any invalid gold matcher, any violated part filter, or any failed
check in a regression group (`delta`, `relationship_integrity`, `citation_scope`, `status`, `citation_completeness`,
`mixed_source`, `delta_precision`, `instruction_boundary`, `current_agreement`). Retrieval hit rates and MRR are
measurements; no ranking threshold is applied. Failing frozen expectations (S05, S06) are reported as failures, not waived.
The report's `overall` object lists the failing ids per category.

## Not done

No dense/neural retriever (no embedding model was available without installing packages or downloading weights), so
the "semantic" half of hybrid retrieval is the topic-catalog expansion, not vectors. The `Index.retrieve` interface and
`evaluate.RETRIEVERS` are where a vector retriever would be compared. No reranker, no relationship review, no
applicability engine, no parsing of tables into cells beyond `|`-joined rows.
