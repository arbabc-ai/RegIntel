"""S7-P1-82: amendatory-instruction construction census over a Phase 1 index (offline, read-only, stdlib only).

Governing scope: founder record 2026-11-04_s7-regintel-p1-founder-decisions_accepted.md. Proposal tooling: nothing is acquired, no
expectation record or evaluator contract changes, and no decision is implied.

For every provision with kind='amendment' and a non-NULL instruction (the rows the evaluator's stored-kind scans read), the census
  (a) applies the seven stored-scan predicates hoisted into regintel_p1.evaluate (STORED_SCAN_PREDICATES): per family it records
      units_matched (non-exclusive, the evaluator's own count), units_with_paths (non-empty explicit_paragraphs.paths) and up to 5
      examples (doc_key, locator);
  (b) tags three grammar forms that other regression groups guard, using existing regintel_p1.fedreg helpers only:
      lowercase_location_clause (fedreg.PARA_LOCATION in the quoted-text-stripped parsing view, as named_paragraph_paths reads it),
      triple_letter_designator (a designator fedreg.designator_classes classes as a letter and that is a tripled letter), and
      relative_under_paragraph_qualified_location (a fedreg.PARA_ACTION match that fedreg._relative_to_enclosing_location places under a
      paragraph-qualified location; `enumerated` counts those whose instruction also has a fedreg.ENUM_BOUNDARY enumerator);
  (c) reports `unclassified` units: units matched by none of the six construction families (every family except
      relative_action_paragraphs, whose predicate is path presence rather than a construction, so it would absorb every unit with paths)
      and by none of the three grammar tags, split into with-paths and without-paths, with the first 25 with-paths examples.
      `partition` assigns every unit to the first matching construction family or tag in a fixed order (or to unclassified), so its
      counts sum to the amendment-unit count.
Consistency: for each of the seven families the census units_matched must equal the units_matched the evaluator's stored check returns
on the same index (each group's `stored` item from eval/phase1/regressions.json, run through its regintel_p1.evaluate check). A mismatch
is listed under `consistency.mismatches` and makes the CLI exit 1. The index is opened through regintel_p1.search.Index
(sqlite3 mode=ro, regintel_p1/search.py:505); the CLI also hashes it before and after and exits 1 if it changed.

    python3 -I scripts/p1_instruction_construction_census_82.py --index DB [--as-of 2026-10-29] --out CENSUS.json
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regintel_p1 import evaluate, fedreg  # noqa: E402
from regintel_p1.search import Index  # noqa: E402

SCHEMA = "p1-instruction-construction-census-0.1"
DECISION_RECORD = "2026-11-04_s7-regintel-p1-founder-decisions_accepted.md"
REGRESSIONS = REPO / "eval" / "phase1" / "regressions.json"
PATH_FAMILY = "relative_action_paragraphs"  # path presence, not a construction
CONSTRUCTION_FAMILIES = tuple(g for g in evaluate.STORED_SCAN_GROUPS if g != PATH_FAMILY)
TAGS = ("lowercase_location_clause", "triple_letter_designator", "relative_under_paragraph_qualified_location")
CHECKS = {"relative_action_paragraphs": evaluate.check_relative_action_paragraphs,
          "passive_location_paragraphs": evaluate.check_passive_location_paragraphs,
          "nested_by_paragraph_scope": evaluate.check_nested_by_paragraph_scope,
          "nested_to_paragraph_scope": evaluate.check_nested_to_paragraph_scope,
          "paragraph_scope_continuity": evaluate.check_paragraph_scope_continuity,
          "enumerated_paragraph_location": evaluate.check_enumerated_paragraph_location,
          "parenthesized_paragraph_list": evaluate.check_parenthesized_paragraph_list}
EXAMPLES, UNCLASSIFIED_EXAMPLES = 5, 25


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stripped_view(instruction):
    return fedreg.parsing_view(fedreg.QUOTED.sub(" ‹q› ", instruction))


def tags_of(instruction):
    """The guarded grammar forms present in one instruction (existing fedreg helpers only)."""
    view = stripped_view(instruction)
    lowercase = list(fedreg.PARA_LOCATION.finditer(view))
    out = {}
    if lowercase:
        out["lowercase_location_clause"] = True
    for token in re.finditer(fedreg.PARA_TOKEN, view):
        if any("letter" in fedreg.designator_classes(p) and len(p.strip("()")) == 3 and len(set(p.strip("()"))) == 1
               for p in fedreg.PARA_PART.findall(token.group(0))):  # tripled: three identical letters (S7-P1-83, F-82-2)
            out["triple_letter_designator"] = True
            break
    if any(fedreg._relative_to_enclosing_location(view, m, lowercase) for m in fedreg.PARA_ACTION.finditer(view)):
        out["relative_under_paragraph_qualified_location"] = True
        out["enumerated"] = any(fedreg._boundary_token(b) for b in fedreg.ENUM_BOUNDARY.finditer(view))
    return out


def census(conn):
    rows = conn.execute("""SELECT d.doc_key, p.locator, p.instruction, p.explicit_paragraphs FROM provision p
                           JOIN source_document d ON d.id=p.doc_id WHERE p.kind='amendment' AND p.instruction IS NOT NULL
                           ORDER BY p.id""").fetchall()
    fam = {g: {"units_matched": 0, "units_with_paths": 0, "examples": []} for g in evaluate.STORED_SCAN_GROUPS}
    tag = {t: {"units_matched": 0, "units_with_paths": 0, "examples": []} for t in TAGS}
    tag["relative_under_paragraph_qualified_location"]["enumerated"] = 0
    order = CONSTRUCTION_FAMILIES + TAGS
    partition = {k: 0 for k in order + ("unclassified",)}
    unclassified = {"with_paths": 0, "without_paths": 0, "with_paths_examples": []}
    for doc, loc, instruction, ev in rows:
        paths = (json.loads(ev or "{}")).get("paths") or []
        hit = []
        for g, predicate in evaluate.STORED_SCAN_PREDICATES.items():
            if predicate(instruction, ev):
                f = fam[g]
                f["units_matched"] += 1
                f["units_with_paths"] += bool(paths)
                if len(f["examples"]) < EXAMPLES:
                    f["examples"].append([doc, loc])
                if g != PATH_FAMILY:
                    hit.append(g)
        found = tags_of(instruction)
        for t in TAGS:
            if found.get(t):
                x = tag[t]
                x["units_matched"] += 1
                x["units_with_paths"] += bool(paths)
                if len(x["examples"]) < EXAMPLES:
                    x["examples"].append([doc, loc])
                hit.append(t)
        if found.get("enumerated"):
            tag["relative_under_paragraph_qualified_location"]["enumerated"] += 1
        first = next((k for k in order if k in hit), "unclassified")
        partition[first] += 1
        if first == "unclassified":
            unclassified["with_paths" if paths else "without_paths"] += 1
            if paths and len(unclassified["with_paths_examples"]) < UNCLASSIFIED_EXAMPLES:
                unclassified["with_paths_examples"].append({"doc_key": doc, "locator": loc, "instruction": instruction[:120],
                                                            "paths": paths})
    return {"amendment_units": len(rows), "families": fam, "tags": tag, "partition_order": list(order), "partition": partition,
            "unclassified": unclassified}


def evaluator_units_matched(index):
    """units_matched that each stored check reports on this index for its `stored` record in regressions.json."""
    spec = json.loads(REGRESSIONS.read_text())
    out = {}
    for group, check in CHECKS.items():
        items = [x for x in spec.get(group, []) if x.get("kind") == "stored"]
        out[group] = {x["id"]: check(index, x).get("units_matched") for x in items}
    return out


def consistency(result, evaluator):
    mismatches = []
    for group, by_id in evaluator.items():
        for item_id, value in by_id.items():
            if value != result["families"][group]["units_matched"]:
                mismatches.append({"group": group, "id": item_id, "census": result["families"][group]["units_matched"], "evaluator": value})
    return {"evaluator_units_matched": evaluator, "mismatches": mismatches, "consistent": not mismatches}


def head():
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", required=True)
    ap.add_argument("--as-of", default="2026-10-29")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    before = sha256(a.index)
    index = Index(a.index, a.as_of)
    result = census(index.conn)
    check = consistency(result, evaluator_units_matched(index))
    meta = dict(index.meta)
    index.conn.close()
    after = sha256(a.index)
    out = {"schema": SCHEMA, "task": "S7-P1-82", "decision_record": DECISION_RECORD, "head": head(), "as_of": a.as_of,
           "index": {"path": a.index, "sha256": before, "unchanged_after_run": before == after, "built_at": meta.get("built_at"),
                     "manifest_sha256": meta.get("manifest_sha256")},
           **result, "consistency": check, "legal_validity": "not_determined", "applicability_status": "not_assessed",
           "status": "proposal; producer-reported; no gate approved; nothing authorised"}
    data = (json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode()
    Path(a.out).write_bytes(data)
    print(json.dumps({"out": a.out, "sha256": hashlib.sha256(data).hexdigest(), "amendment_units": result["amendment_units"],
                      "unclassified": result["partition"]["unclassified"], "consistent": check["consistent"],
                      "index_unchanged": before == after}))
    return 0 if before == after and check["consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
