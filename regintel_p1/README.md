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
