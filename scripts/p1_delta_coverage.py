"""S7-P1-65: current-to-future delta coverage over every amendment target in a Phase 1 index (offline, read-only, stdlib only).

Targets are distinct (title, part, section) triples taken from two producer-parsed sources:
  (a) `amends` relationships (review_status parsed_unreviewed) whose dst_citation names a section, e.g. "12 CFR 5.20";
  (b) numeric memberships of `amendment_range` rows, e.g. 12 CFR 8.1 through 8.8.
Part-level `amends` rows ("12 CFR Part 5") and `amends_candidate` rows (unresolved candidates) are listed separately and are
never counted as targets; an `amends` citation of any other shape is collected in amends_rows_unclassified (exit 1). For each
target the producer's Index.delta() is run unchanged and its fields are recorded: nothing is merged across instruments, no proposal
is relabelled, and every row keeps the producer's legal_validity and applicability_status.

The reconciliation is independent: SQL_TARGETS counts distinct targets with SQLite directly over the index's relationship and
amendment_range tables (shape tests in SQL, a recursive CTE for range members), and `reconciles` requires the Python distinct count,
that SQL count and the evidence-status tally to agree. Added eCFR parts are derived from the two indexes' document sets.

Counting policy (both counters follow it; pinned by tests):
  - `amends` rows count only with review_status 'parsed_unreviewed'; other review statuses are tallied in
    amends_rows_other_review_status and are not targets.
  - every `amendment_range` row counts, whatever its review_status or status (all rows the producer records are numeric ranges);
    its members are lo_minor..hi_minor inclusive. A row with lo_minor > hi_minor has no members and is tallied in
    amendment_range_rows_inverted. (Before S7-P1-67 the SQL path seeded lo_minor even for such a row while the Python path did not;
    both now give no members.)

With --before-index the status also reports the comparison (baseline_lost, targets_removed, added_parts_mismatch, unattributed
new baselines, the S7-P1-54 match) and `comparison_clean`; the CLI exits 1 unless it is clean. --inventory-54 and --added-parts
need --before-index (argparse exit 2 otherwise).

    python3 -I scripts/p1_delta_coverage.py --index AFTER.sqlite [--before-index BEFORE.sqlite] [--inventory-54 INV.json]
        [--added-parts 12:6,12:30,...] [--as-of 2026-10-29] --out OUT.json
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regintel_p1.search import Index  # noqa: E402

SCHEMA = "p1-delta-coverage-refresh-0.3"
SECTION = re.compile(r"^(\d+) CFR (\d+)\.([0-9A-Za-z]+)$")
PART = re.compile(r"^(\d+) CFR Part (\d+)$")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources(conn):
    """Targets with their sources, the part-level, candidate and unclassified rows kept apart, and raw row counts."""
    targets, part_level, candidates, unclassified = defaultdict(set), set(), set(), []
    raw = {"amends_rows_total": 0, "amends_rows_by_review_status": {}, "amends_rows_section": 0, "amends_rows_part_level": 0,
           "amends_rows_unclassified": 0, "amends_rows_other_review_status": 0}
    by_review = Counter()
    for cite, review in conn.execute("SELECT dst_citation, review_status FROM relationship WHERE rel_type='amends'"):
        raw["amends_rows_total"] += 1
        by_review[review] += 1
        if review != "parsed_unreviewed":
            raw["amends_rows_other_review_status"] += 1
            continue
        m = SECTION.match(cite or "")
        if m:
            raw["amends_rows_section"] += 1
            targets[(m[1], m[2], f"{m[2]}.{m[3]}")].add("amends")
        elif PART.match(cite or ""):
            raw["amends_rows_part_level"] += 1
            part_level.add(cite)
        else:
            raw["amends_rows_unclassified"] += 1
            unclassified.append(cite)
    raw["amends_rows_by_review_status"] = dict(sorted(by_review.items(), key=lambda kv: str(kv[0])))
    ranges = members = inverted = 0
    for title, part, lo, hi in conn.execute("SELECT title_no, part_no, lo_minor, hi_minor FROM amendment_range"):
        ranges += 1
        inverted += int(lo) > int(hi)
        for n in range(int(lo), int(hi) + 1):
            members += 1
            targets[(str(title), str(part), f"{part}.{n}")].add("amendment_range")
    raw["amendment_range_rows"], raw["amendment_range_members"], raw["amendment_range_rows_inverted"] = ranges, members, inverted
    for (cite,) in conn.execute("SELECT dst_citation FROM relationship WHERE rel_type='amends_candidate'"):
        candidates.add(cite)
    raw["amends_candidate_rows"] = conn.execute("SELECT COUNT(*) FROM relationship WHERE rel_type='amends_candidate'").fetchone()[0]
    return targets, sorted(part_level), sorted(candidates), sorted(set(str(c) for c in unclassified)), raw


# Distinct (title, part, section) targets computed by SQLite from the index's own tables, independently of sources().
SQL_TARGETS = """
WITH RECURSIVE
amends(title, rest) AS (
  SELECT substr(dst_citation, 1, instr(dst_citation, ' CFR ') - 1), substr(dst_citation, instr(dst_citation, ' CFR ') + 5)
  FROM relationship WHERE rel_type = 'amends' AND review_status = 'parsed_unreviewed' AND instr(dst_citation, ' CFR ') > 1),
sec(title, part, section, minor) AS (
  SELECT title, substr(rest, 1, instr(rest, '.') - 1), rest, substr(rest, instr(rest, '.') + 1) FROM amends WHERE instr(rest, '.') > 1),
amend_targets(title, part, section) AS (
  SELECT title, part, section FROM sec
  WHERE title NOT GLOB '*[^0-9]*' AND part NOT GLOB '*[^0-9]*' AND minor <> '' AND minor NOT GLOB '*[^0-9A-Za-z]*'),
members(title, part, n, hi) AS (
  SELECT CAST(title_no AS TEXT), CAST(part_no AS TEXT), CAST(lo_minor AS INTEGER), CAST(hi_minor AS INTEGER) FROM amendment_range
  WHERE CAST(lo_minor AS INTEGER) <= CAST(hi_minor AS INTEGER)
  UNION ALL SELECT title, part, n + 1, hi FROM members WHERE n < hi)
SELECT COUNT(*) FROM (SELECT title, part, section FROM amend_targets
                      UNION SELECT title, part, part || '.' || n FROM members)
"""


def sql_distinct_count(conn):
    return conn.execute(SQL_TARGETS).fetchone()[0]


def ecfr_parts(conn):
    """title:part for every eCFR baseline document in the index."""
    return sorted({k.split(":", 1)[1] for (k,) in conn.execute("SELECT doc_key FROM source_document WHERE doc_key LIKE 'ecfr:%'")},
                  key=lambda tp: [int(x) for x in tp.split(":")])


def row(ix, key, srcs):
    title, part, section = key
    citation = f"{title} CFR {section}"
    d = ix.delta(citation)
    base = d.get("baseline") or None
    classes = {c["doc_key"]: c for c in d.get("instrument_classification") or []}
    instruments = [{"doc_key": g["doc_key"], "document_type": g.get("document_type"),
                    "lifecycle_state": (g.get("status") or {}).get("state"),
                    "role": g.get("role"), "paragraph_precision": g.get("paragraph_precision"),
                    "classification": (classes.get(g["doc_key"]) or {}).get("classification")}
                   for g in d.get("instruments") or []]
    return {"title": title, "part": part, "section": section, "citation": citation, "target_sources": sorted(srcs),
            "evidence_status": d.get("evidence_status"),
            "baseline_kind": (base or {}).get("match") or "none", "baseline_doc_key": (base or {}).get("doc_key"),
            "instrument_count": len(instruments), "instruments": instruments,
            "ambiguous_title": d.get("evidence_status") == "ambiguous_title" or (d.get("title_resolution") or {}).get("status") == "ambiguous",
            "title_resolution": (d.get("title_resolution") or {}).get("status"),
            "legal_validity": d.get("legal_validity"), "applicability_status": d.get("applicability_status")}


def invariants(rows):
    """Problems (empty when none): merged instruments, relabelled proposals, or changed validity/applicability labels."""
    problems = []
    for r in rows:
        keys = [g["doc_key"] for g in r["instruments"]]
        if len(keys) != len(set(keys)) or r["instrument_count"] != len(keys):
            problems.append(f"{r['citation']}: instruments merged or duplicated")
        for g in r["instruments"]:
            state = g["lifecycle_state"] or ""
            if g["document_type"] == "Proposed Rule" and (state.startswith("final") or state.startswith("current")):
                problems.append(f"{r['citation']}: proposal {g['doc_key']} labelled {state}")
        if r["legal_validity"] != "not_determined" or r["applicability_status"] != "not_assessed":
            problems.append(f"{r['citation']}: legal_validity/applicability_status changed")
    return problems


def coverage(index_path, as_of):
    ix = Index(str(index_path), as_of)
    try:
        targets, part_level, candidates, unclassified, raw = sources(ix.conn)
        rows = [row(ix, k, targets[k]) for k in sorted(targets, key=natural)]
        status = dict(ix.conn.execute("SELECT status, COUNT(*) FROM source_status GROUP BY status").fetchall())
        dep = ix.dependency_verification["status"]
        sql_count = sql_distinct_count(ix.conn)
        parts = ecfr_parts(ix.conn)
    finally:
        ix.conn.close()
    by_status = Counter(r["evidence_status"] for r in rows)
    summary = {"distinct_targets": len(rows), "sql_distinct_targets": sql_count, "sql_distinct_targets_basis": "index tables (SQL_TARGETS)",
               **raw, "amends_rows_unclassified_citations": unclassified,
               "targets_by_evidence_status": dict(sorted(by_status.items())),
               "targets_by_baseline_kind": dict(sorted(Counter(r["baseline_kind"] for r in rows).items())),
               "targets_by_source": dict(sorted(Counter(s for r in rows for s in r["target_sources"]).items())),
               "part_level_amends_rows_not_counted": len(part_level), "amends_candidates_not_counted": len(candidates),
               "candidates_that_are_also_targets": sum(1 for c in candidates if SECTION.match(c) and
                                                       (SECTION.match(c)[1], SECTION.match(c)[2], f"{SECTION.match(c)[2]}.{SECTION.match(c)[3]}") in targets),
               "invariant_problems": invariants(rows)}
    summary["reconciles"] = (sum(by_status.values()) == summary["distinct_targets"] == summary["sql_distinct_targets"])
    return {"rows": rows, "part_level_targets": part_level, "unresolved_candidates": candidates, "summary": summary,
            "source_status_counts": status, "dependency_verification": dep, "ecfr_parts": parts}


def inventory_54_sections(path):
    inv = json.loads(Path(path).read_text())
    out = set()
    for p in inv.get("eligible_pairs") or []:
        _, title, part = p["baseline_doc_key"].split(":")
        loc = p["locator"].removeprefix("section-")
        out.add((title, part, loc.split("(", 1)[0]))  # a paragraph locator such as 217.1(c)(4) belongs to section 217.1
    return out, inv.get("schema")


def natural(key):
    """Sort key for (title, part, section): numeric title and part, then section with digit runs compared as numbers (3.2 < 3.10)."""
    t, p, sec = key
    return (int(t), int(p), [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", sec)])


def citations(keys):
    return [f"{t} CFR {s}" for t, _, s in sorted(keys, key=natural)]


def compare(after, before, inv_sections=None, added_argument=None):
    """Before/after per target. inv_sections: S7-P1-54 eligible-pair sections, or None when no inventory is given.
    Attribution uses added parts derived from the two indexes; added_argument is kept only for comparison."""
    prev = {(r["title"], r["part"], r["section"]): r for r in before["rows"]}
    now = {(r["title"], r["part"], r["section"]): r for r in after["rows"]}
    added_parts = sorted(set(after["ecfr_parts"]) - set(before["ecfr_parts"]), key=lambda tp: [int(x) for x in tp.split(":")])
    argument = sorted(set(added_argument or ()), key=lambda tp: [int(x) for x in tp.split(":")])
    inv = set(inv_sections or ())
    rows, newly, still = [], [], defaultdict(list)
    derived = set(added_parts)
    for r in after["rows"]:
        k = (r["title"], r["part"], r["section"])
        b = prev.get(k)
        entry = {"citation": r["citation"], "before_evidence_status": b and b["evidence_status"], "before_baseline_kind": b and b["baseline_kind"],
                 "after_evidence_status": r["evidence_status"], "after_baseline_kind": r["baseline_kind"],
                 "in_54_eligible_pairs": k in inv}
        rows.append(entry)
        if (b is None or b["baseline_kind"] == "none") and r["baseline_kind"] != "none":
            newly.append({"citation": r["citation"], "baseline_doc_key": r["baseline_doc_key"],
                          "attributed_to_added_part": f"{r['title']}:{r['part']}" if f"{r['title']}:{r['part']}" in derived else None})
        if r["baseline_kind"] == "none":
            still[f"{r['title']} CFR Part {r['part']}"].append(r["section"])
    lost = [e["citation"] for e in rows if e["before_baseline_kind"] not in (None, "none") and e["after_baseline_kind"] == "none"]
    exact_before = {k for k, r in prev.items() if r["baseline_kind"] == "exact_section"}
    check54 = None
    if inv_sections is not None:
        check54 = {"inventory_54_sections_not_in_targets": citations(inv - set(now)),
                   "before_exact_section_not_in_54": citations(exact_before - inv),
                   "in_54_but_before_baseline_not_exact": citations({k for k in inv & set(prev) if k not in exact_before})}
        check54["inventory_54_matches_before_exact_section"] = not any(check54.values())
    return {"per_target": rows, "newly_baseline_resolvable": newly, "targets_removed": citations(set(prev) - set(now)),
            "added_parts_derived": added_parts, "added_parts_argument": argument,
            "added_parts_mismatch": sorted(set(added_parts) ^ set(argument), key=lambda tp: [int(x) for x in tp.split(":")]),
            "inventory_54_check": check54,
            "newly_baseline_resolvable_unattributed": [n["citation"] for n in newly if not n["attributed_to_added_part"]],
            "baseline_lost": lost,
            "still_without_baseline_by_part": {p: sorted(s, key=lambda x: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", x)])
                                               for p, s in sorted(still.items(), key=lambda kv: [int(x) for x in re.findall(r"\d+", kv[0])])},
            "before_targets": len(before["rows"]), "after_targets": len(after["rows"]),
            "before_by_baseline_kind": before["summary"]["targets_by_baseline_kind"],
            "after_by_baseline_kind": after["summary"]["targets_by_baseline_kind"],
            "before_by_evidence_status": before["summary"]["targets_by_evidence_status"],
            "after_by_evidence_status": after["summary"]["targets_by_evidence_status"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", required=True)
    ap.add_argument("--before-index")
    ap.add_argument("--inventory-54")
    ap.add_argument("--added-parts", default="")
    ap.add_argument("--manifest")
    ap.add_argument("--as-of", default="2026-10-29")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if not a.before_index:
        for flag, value in (("--inventory-54", a.inventory_54), ("--added-parts", a.added_parts)):
            if value:
                ap.error(f"{flag} is ignored without --before-index; supply --before-index or drop {flag}")
    after = coverage(a.index, a.as_of)
    s = after["summary"]
    out = {"schema": SCHEMA,
           "status": {"label": "producer-reported; offline; read-only; no gate approved", "reconciles": s["reconciles"],
                      "invariant_problem_count": len(s["invariant_problems"]),
                      "amends_rows_unclassified_count": len(s["amends_rows_unclassified_citations"]),
                      "legal_validity": "not_determined", "applicability_status": "not_assessed"},
           "as_of": a.as_of, "index": {"name": Path(a.index).name, "path": str(a.index), "sha256": sha256(a.index),
                                        "dependency_verification": after.pop("dependency_verification"),
                                        "source_status_counts": after.pop("source_status_counts"),
                                        "ecfr_parts": after["ecfr_parts"]},
           **after}
    if a.manifest:
        out["manifest"] = {"path": a.manifest, "sha256": sha256(a.manifest)}
    if a.before_index:
        before = coverage(a.before_index, a.as_of)
        inv, inv_schema = inventory_54_sections(a.inventory_54) if a.inventory_54 else (None, None)
        added = {p for p in a.added_parts.split(",") if p}
        out["before"] = {"index": {"name": Path(a.before_index).name, "sha256": sha256(a.before_index), "ecfr_parts": before["ecfr_parts"]},
                         "summary": before["summary"]}
        if a.inventory_54:
            out["inventory_54"] = {"path": a.inventory_54, "sha256": sha256(a.inventory_54), "schema": inv_schema,
                                   "eligible_pair_sections": len(inv)}
        ba = out["before_after"] = compare(after, before, inv, added)
        match = (ba["inventory_54_check"] or {}).get("inventory_54_matches_before_exact_section")
        out["status"].update({"baseline_lost_count": len(ba["baseline_lost"]), "targets_removed_count": len(ba["targets_removed"]),
                              "added_parts_mismatch_count": len(ba["added_parts_mismatch"]),
                              "newly_baseline_resolvable_unattributed_count": len(ba["newly_baseline_resolvable_unattributed"]),
                              "inventory_54_matches_before_exact_section": match})
        out["status"]["comparison_clean"] = (not any(out["status"][k] for k in ("baseline_lost_count", "targets_removed_count",
                                                                                 "added_parts_mismatch_count",
                                                                                 "newly_baseline_resolvable_unattributed_count"))
                                             and match in (None, True))
    out.pop("ecfr_parts", None)
    raw = (json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode()
    Path(a.out).write_bytes(raw)
    s = out["summary"]
    print(json.dumps({"out": a.out, "sha256": hashlib.sha256(raw).hexdigest(), "distinct_targets": s["distinct_targets"],
                      "sql_distinct_targets": s["sql_distinct_targets"], "by_status": s["targets_by_evidence_status"],
                      "reconciles": s["reconciles"], "invariant_problems": len(s["invariant_problems"]),
                      "amends_rows_unclassified": len(s["amends_rows_unclassified_citations"]),
                      "comparison_clean": out["status"].get("comparison_clean")}, indent=1))
    ok = s["reconciles"] and not s["invariant_problems"] and not s["amends_rows_unclassified_citations"]
    return 0 if ok and out["status"].get("comparison_clean", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
