"""S7-P1-65: current-to-future delta coverage over every amendment target in a Phase 1 index (offline, read-only, stdlib only).

Targets are distinct (title, part, section) triples taken from two producer-parsed sources:
  (a) `amends` relationships (review_status parsed_unreviewed) whose dst_citation names a section, e.g. "12 CFR 5.20";
  (b) numeric memberships of `amendment_range` rows, e.g. 12 CFR 8.1 through 8.8.
Part-level `amends` rows ("12 CFR Part 5") and `amends_candidate` rows (unresolved candidates) are listed separately and are
never counted as targets. For each target the producer's Index.delta() is run unchanged and its fields are recorded: nothing is
merged across instruments, no proposal is relabelled, and every row keeps the producer's legal_validity and applicability_status.

    python3 -I scripts/p1_delta_coverage.py --index AFTER.sqlite [--before-index BEFORE.sqlite] [--inventory-54 INV.json]
        [--added-parts 12:6,12:30,...] [--as-of 2026-10-29] --out OUT.json
"""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regintel_p1.search import Index  # noqa: E402

SCHEMA = "p1-delta-coverage-refresh-0.1"
SECTION = re.compile(r"^(\d+) CFR (\d+)\.([0-9A-Za-z]+)$")
PART = re.compile(r"^(\d+) CFR Part (\d+)$")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources(conn):
    """Targets with their sources, plus the part-level and candidate rows kept apart."""
    targets, part_level, candidates = defaultdict(set), set(), set()
    for (cite,) in conn.execute("SELECT dst_citation FROM relationship WHERE rel_type='amends' AND review_status='parsed_unreviewed'"):
        m = SECTION.match(cite or "")
        if m:
            targets[(m[1], m[2], f"{m[2]}.{m[3]}")].add("amends")
        elif PART.match(cite or ""):
            part_level.add(cite)
        else:
            raise ValueError(f"unrecognised amends target {cite!r}")
    for title, part, lo, hi in conn.execute("SELECT title_no, part_no, lo_minor, hi_minor FROM amendment_range"):
        for n in range(int(lo), int(hi) + 1):
            targets[(str(title), str(part), f"{part}.{n}")].add("amendment_range")
    for (cite,) in conn.execute("SELECT dst_citation FROM relationship WHERE rel_type='amends_candidate'"):
        candidates.add(cite)
    return targets, sorted(part_level), sorted(candidates)


def sql_distinct_count(targets):
    """COUNT(DISTINCT title, part, section) computed by SQLite over the same source rows."""
    mem = sqlite3.connect(":memory:")
    try:
        mem.execute("CREATE TABLE t (title TEXT, part TEXT, section TEXT)")
        mem.executemany("INSERT INTO t VALUES (?, ?, ?)", [k for k, srcs in targets.items() for _ in srcs])
        return mem.execute("SELECT COUNT(*) FROM (SELECT DISTINCT title, part, section FROM t)").fetchone()[0]
    finally:
        mem.close()


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
        targets, part_level, candidates = sources(ix.conn)
        rows = [row(ix, k, targets[k]) for k in sorted(targets, key=lambda k: (int(k[0]), int(k[1]), k[2]))]
        status = dict(ix.conn.execute("SELECT status, COUNT(*) FROM source_status GROUP BY status").fetchall())
        dep = ix.dependency_verification["status"]
    finally:
        ix.conn.close()
    by_status = Counter(r["evidence_status"] for r in rows)
    summary = {"distinct_targets": len(rows), "sql_distinct_targets": sql_distinct_count(targets),
               "targets_by_evidence_status": dict(sorted(by_status.items())),
               "targets_by_baseline_kind": dict(sorted(Counter(r["baseline_kind"] for r in rows).items())),
               "targets_by_source": dict(sorted(Counter(s for r in rows for s in r["target_sources"]).items())),
               "part_level_amends_rows_not_counted": len(part_level), "amends_candidates_not_counted": len(candidates),
               "candidates_that_are_also_targets": sum(1 for c in candidates if SECTION.match(c) and
                                                       (SECTION.match(c)[1], SECTION.match(c)[2], f"{SECTION.match(c)[2]}.{SECTION.match(c)[3]}") in targets),
               "invariant_problems": invariants(rows)}
    summary["reconciles"] = (sum(by_status.values()) == summary["distinct_targets"] == summary["sql_distinct_targets"])
    return {"rows": rows, "part_level_targets": part_level, "unresolved_candidates": candidates, "summary": summary,
            "source_status_counts": status, "dependency_verification": dep}


def inventory_54_sections(path):
    inv = json.loads(Path(path).read_text())
    out = set()
    for p in inv.get("eligible_pairs") or []:
        _, title, part = p["baseline_doc_key"].split(":")
        loc = p["locator"].removeprefix("section-")
        out.add((title, part, loc.split("(", 1)[0]))  # a paragraph locator such as 217.1(c)(4) belongs to section 217.1
    return out, inv.get("schema")


def compare(after, before, inv_sections, added_parts):
    prev = {(r["title"], r["part"], r["section"]): r for r in before["rows"]}
    rows, newly, still = [], [], defaultdict(list)
    for r in after["rows"]:
        k = (r["title"], r["part"], r["section"])
        b = prev.get(k)
        entry = {"citation": r["citation"], "before_evidence_status": b and b["evidence_status"], "before_baseline_kind": b and b["baseline_kind"],
                 "after_evidence_status": r["evidence_status"], "after_baseline_kind": r["baseline_kind"],
                 "in_54_eligible_pairs": k in inv_sections}
        rows.append(entry)
        if (b is None or b["baseline_kind"] == "none") and r["baseline_kind"] != "none":
            newly.append({"citation": r["citation"], "baseline_doc_key": r["baseline_doc_key"],
                          "attributed_to_part_added_in_58": f"{r['title']}:{r['part']}" if f"{r['title']}:{r['part']}" in added_parts else None})
        if r["baseline_kind"] == "none":
            still[f"{r['title']} CFR Part {r['part']}"].append(r["section"])
    lost = [e["citation"] for e in rows if e["before_baseline_kind"] not in (None, "none") and e["after_baseline_kind"] == "none"]
    return {"per_target": rows, "newly_baseline_resolvable": newly,
            "newly_baseline_resolvable_unattributed": [n["citation"] for n in newly if not n["attributed_to_part_added_in_58"]],
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
    after = coverage(a.index, a.as_of)
    out = {"schema": SCHEMA, "status": "producer-reported; offline; read-only; no gate approved; legal_validity not_determined; "
                                       "applicability_status not_assessed",
           "as_of": a.as_of, "index": {"name": Path(a.index).name, "path": str(a.index), "sha256": sha256(a.index),
                                        "dependency_verification": after.pop("dependency_verification"),
                                        "source_status_counts": after.pop("source_status_counts")},
           **after}
    if a.manifest:
        out["manifest"] = {"path": a.manifest, "sha256": sha256(a.manifest)}
    if a.before_index:
        before = coverage(a.before_index, a.as_of)
        inv, inv_schema = inventory_54_sections(a.inventory_54) if a.inventory_54 else (set(), None)
        added = {p for p in a.added_parts.split(",") if p}
        out["before"] = {"index": {"name": Path(a.before_index).name, "sha256": sha256(a.before_index)}, "summary": before["summary"]}
        if a.inventory_54:
            out["inventory_54"] = {"path": a.inventory_54, "sha256": sha256(a.inventory_54), "schema": inv_schema,
                                   "eligible_pair_sections": len(inv)}
        out["before_after"] = compare(after, before, inv, added)
    raw = (json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode()
    Path(a.out).write_bytes(raw)
    s = out["summary"]
    print(json.dumps({"out": a.out, "sha256": hashlib.sha256(raw).hexdigest(), "distinct_targets": s["distinct_targets"],
                      "sql_distinct_targets": s["sql_distinct_targets"], "by_status": s["targets_by_evidence_status"],
                      "reconciles": s["reconciles"], "invariant_problems": len(s["invariant_problems"])}, indent=1))
    return 0 if s["reconciles"] and not s["invariant_problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
