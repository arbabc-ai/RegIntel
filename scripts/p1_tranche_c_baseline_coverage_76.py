"""S7-P1-76: Tranche C delta-baseline coverage inventory and TD06 amendatory evidence (offline, read-only, stdlib only).

Governing scope: founder record 2026-11-04_s7-regintel-p1-founder-decisions_accepted.md (DR-1 items, DR-5 FinCEN/OFAC via
treasury-department plus filter). Nothing is acquired, no parser, filter, probe or expectation is changed; the manifest and the
index are opened read-only (the index with SQLite mode=ro) and their sha256 values are recorded before and after.

Two outputs:
  --inventory-out  schema p1-tranche-c-baseline-coverage-0.1. One row per in-range fr: document of the index (a
                   cfr_references part in 31:500-599 or 31:1000-1099): document metadata, agency slugs from the stored
                   metadata asset, every targeted 31 CFR part with its eCFR baseline (manifest key, publisher_issue_date,
                   publisher_up_to_date_as_of) or "not_acquired". A per-part aggregate counts instruments, baseline
                   presence and the explicit_paragraphs status of the instruments' amendment units in that part.
                   delta_possibility per instrument: "possible" when every targeted 31 CFR part has a baseline,
                   "partial" when some do, "blocked_missing_baseline" when none do.
  --td06-out       schema p1-td06-evidence-0.1. Index.delta("31 CFR 1010.100") is run unchanged; for every instrument it
                   lists, every unit the delta uses (by locator) with the stored amendment row, the relationship rows that
                   name 31 CFR 1010.100, and the connection basis: "amendatory_location_clause" when the unit's verbatim
                   instruction names section 1010.100 (or a paragraph of it) and explicit_targets holds 1010.100,
                   "cross_reference_or_definition_mention" otherwise. The classification is (a) when every connecting unit
                   is an amendatory location clause, (b) when none is, (c) otherwise.

    python3 -I scripts/p1_tranche_c_baseline_coverage_76.py --index DB --inventory-out INV.json --td06-out TD06.json
"""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regintel_p1.search import Index  # noqa: E402

INVENTORY_SCHEMA = "p1-tranche-c-baseline-coverage-0.1"
TD06_SCHEMA = "p1-td06-evidence-0.1"
DECISION_RECORD = "2026-11-04_s7-regintel-p1-founder-decisions_accepted.md"
DEFAULT_MANIFEST = "/home/arbab/projects/regintel-corpus/phase1/manifest.json"
REGRESSIONS = REPO / "eval" / "phase1" / "regressions.json"
RANGES = {"31:500-599": (500, 599), "31:1000-1099": (1000, 1099)}
TD06_CITATION = "31 CFR 1010.100"
# the section sign followed by 1010.100 not continued by another digit or dot-digit (so 1010.1001 does not match)
NAMES_SECTION = re.compile(r"§+\s*1010\.100(?![0-9]|\.[0-9])", re.ASCII)
CITES_SECTION = re.compile(r"(?:31\s+CFR\s+)?1010\.100(?![0-9]|\.[0-9])", re.ASCII)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dumps(obj):
    return (json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode()


def title31_parts(entry):
    out = []
    for ref in entry.get("cfr_references") or []:
        part = str(ref.get("part") or "")
        if ref.get("title") == 31 and part.isascii() and part.isdigit() and int(part) not in out:
            out.append(int(part))
    return out


def ranges_of(parts):
    return sorted(name for name, (lo, hi) in RANGES.items() if any(lo <= p <= hi for p in parts))


def agency_slugs(corpus, entry):
    asset = entry.get("metadata_asset") or {}
    path = corpus / asset["path"] if asset.get("path") else None
    if not path or not path.exists():
        return None
    return sorted({a["slug"] for a in json.loads(path.read_text()).get("agencies") or [] if a.get("slug")})


def inventory(manifest, corpus, conn):
    docs = manifest["documents"]
    indexed = {r[0] for r in conn.execute("SELECT doc_key FROM source_document")}
    rows, per_part = [], {}
    for key in sorted(k for k in docs if k.startswith("fr:") and k in indexed):
        entry = docs[key]
        parts = title31_parts(entry)
        in_range = ranges_of(parts)
        if not in_range:
            continue
        units = conn.execute(
            "SELECT p.part_no, p.explicit_paragraphs FROM provision p JOIN source_document s ON s.id = p.doc_id "
            "WHERE s.doc_key = ? AND p.kind = 'amendment'", (key,)).fetchall()
        targets = []
        for part in sorted(parts):
            base_key = f"ecfr:31:{part}"
            base = docs.get(base_key)
            statuses = Counter((json.loads(ep).get("status") if ep else None) or "none"
                               for part_no, ep in units if part_no == str(part))
            targets.append({"title": 31, "part": part, "baseline_acquired": base is not None,
                            "baseline_key": base_key if base is not None else "not_acquired",
                            "baseline_in_index": base_key in indexed,
                            "publisher_issue_date": (base or {}).get("publisher_issue_date"),
                            "publisher_up_to_date_as_of": (base or {}).get("publisher_up_to_date_as_of"),
                            "amendment_units": sum(statuses.values()),
                            "explicit_paragraphs_status": dict(sorted(statuses.items()))})
            agg = per_part.setdefault(f"31:{part}", {"title": 31, "part": part, "instruments": 0,
                                                     "baseline_acquired": base is not None,
                                                     "baseline_key": base_key if base is not None else "not_acquired",
                                                     "amendment_units": 0, "explicit_paragraphs_status": Counter()})
            agg["instruments"] += 1
            agg["amendment_units"] += sum(statuses.values())
            agg["explicit_paragraphs_status"].update(statuses)
        with_base = sum(t["baseline_acquired"] for t in targets)
        possibility = ("possible" if with_base == len(targets) else "blocked_missing_baseline" if with_base == 0
                       else "partial")
        rows.append({"doc_key": key, "document_number": entry.get("document_number"),
                     "publication_date": entry.get("publication_date"), "document_type": entry.get("document_type"),
                     "agency_slugs": agency_slugs(corpus, entry), "in_ranges": in_range, "targets": targets,
                     "unparsed_title31_references": [r for r in entry.get("cfr_references") or []
                                                     if r.get("title") == 31 and not str(r.get("part") or "").isdigit()],
                     "delta_possibility": possibility,
                     "legal_validity": "not_determined", "applicability_status": "not_assessed"})
    parts = []
    for name in sorted(per_part, key=lambda k: per_part[k]["part"]):
        agg = per_part[name]
        st = agg["explicit_paragraphs_status"]
        parts.append({**agg, "explicit_paragraphs_status": dict(sorted(st.items())),
                      "units_attributed": st.get("attributed", 0), "units_unresolved": st.get("unresolved", 0)})
    count = Counter(r["delta_possibility"] for r in rows)
    slug_count = Counter(s for r in rows for s in (r["agency_slugs"] or []))
    summary = {"instruments": len(rows),
               "instruments_by_range": {n: sum(n in r["in_ranges"] for r in rows) for n in RANGES},
               "instruments_in_both_ranges": sum(len(r["in_ranges"]) == 2 for r in rows),
               "instruments_by_slug": {s: slug_count.get(s, 0) for s in
                                       ("financial-crimes-enforcement-network", "foreign-assets-control-office")},
               "delta_possibility": {k: count.get(k, 0) for k in ("possible", "partial", "blocked_missing_baseline")},
               "parts_targeted": len(parts),
               "parts_with_baseline": [p["baseline_key"] for p in parts if p["baseline_acquired"]],
               "parts_without_baseline": [f"31:{p['part']}" for p in parts if not p["baseline_acquired"]],
               "chapter_v_parts_without_baseline": [f"31:{p['part']}" for p in parts
                                                    if not p["baseline_acquired"] and 500 <= p["part"] <= 599]}
    return rows, parts, summary


def basis(instruction, targets):
    named = bool(NAMES_SECTION.search(instruction or ""))
    if named and "1010.100" in targets:
        return "amendatory_location_clause"
    return "cross_reference_or_definition_mention"


def td06(index, conn):
    d = index.delta(TD06_CITATION)
    instruments = []
    for g in d["instruments"]:
        key = g["doc_key"]
        units = []
        for u in g.get("units") or []:
            row = conn.execute(
                "SELECT p.id, p.locator, p.kind, p.title_no, p.part_no, p.section_no, p.pairing_status, p.instruction, "
                "p.explicit_targets, p.explicit_parts, p.explicit_paragraphs, p.referenced_citations "
                "FROM provision p JOIN source_document s ON s.id = p.doc_id WHERE s.doc_key = ? AND p.locator = ?",
                (key, u["locator"])).fetchone()
            pid, loc, kind, title_no, part_no, section_no, pairing, instruction, targets, eparts, eparas, refs = row
            targets = json.loads(targets or "[]")
            rels = [{"rel_type": r[0], "dst_citation": r[1], "basis": r[2], "review_status": r[3]}
                    for r in conn.execute("SELECT rel_type, dst_citation, basis, review_status FROM relationship "
                                          "WHERE src_provision_id = ? ORDER BY id", (pid,)) if CITES_SECTION.search(r[1] or "")]
            units.append({"unit_id": loc, "provision_id": pid, "kind": kind, "title_no": title_no, "part_no": part_no,
                          "section_no": section_no, "pairing_status": pairing, "instruction": instruction,
                          "instruction_names_section_1010_100": bool(NAMES_SECTION.search(instruction or "")),
                          "explicit_targets": targets, "explicit_parts": json.loads(eparts or "[]"),
                          "explicit_paragraphs": json.loads(eparas) if eparas else None,
                          "referenced_citations": json.loads(refs or "[]"),
                          "relationships_naming_1010_100": rels, "connection_basis": basis(instruction, targets)})
        other = [{"locator": r[0], "rel_type": r[1], "dst_citation": r[2], "kind": r[3]} for r in conn.execute(
            "SELECT p.locator, r.rel_type, r.dst_citation, p.kind FROM relationship r JOIN provision p ON p.id = r.src_provision_id "
            "JOIN source_document s ON s.id = p.doc_id WHERE s.doc_key = ? ORDER BY r.id", (key,))
            if CITES_SECTION.search(r[2] or "") and r[0] not in {u["unit_id"] for u in units}]
        instruments.append({"doc_key": key, "document_type": g.get("document_type"), "role": g.get("role"),
                            "status": g.get("status"), "units": units,
                            "other_relationships_naming_1010_100_not_used_by_delta": other})
    bases = [u["connection_basis"] for g in instruments for u in g["units"]]
    if bases and all(b == "amendatory_location_clause" for b in bases):
        cls = "a"
        text = ("genuine amendatory target, probe is corpus-dependent: every unit the delta uses is an amendatory "
                "instruction naming section 1010.100 with explicit_targets 1010.100")
    elif bases and not any(b == "amendatory_location_clause" for b in bases):
        cls, text = "b", "attribution error, engine regression: no connecting unit is an amendatory location clause"
    else:
        cls, text = "c", "undetermined: connecting units are mixed or absent"
    return d, instruments, cls, text


def regression_record():
    lines = REGRESSIONS.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == '"id": "TD06",') - 1
    end = next(i for i in range(start, len(lines)) if lines[i].strip() in ("}", "},"))
    return {"path": "eval/phase1/regressions.json", "sha256": sha256(REGRESSIONS),
            "lines": {"from": start + 1, "to": end + 1},
            "text": [{"line": n + 1, "text": lines[n]} for n in range(start, end + 1)]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--index", required=True)
    ap.add_argument("--as-of", default="2026-10-29")
    ap.add_argument("--inventory-out", required=True)
    ap.add_argument("--td06-out", required=True)
    a = ap.parse_args(argv)
    before = {"manifest": sha256(a.manifest), "index": sha256(a.index)}
    manifest = json.loads(Path(a.manifest).read_text())
    conn = sqlite3.connect(f"file:{a.index}?mode=ro", uri=True)
    meta = dict(conn.execute("SELECT key, value FROM index_meta"))
    rows, parts, summary = inventory(manifest, Path(a.manifest).parent, conn)
    d, instruments, cls, text = td06(Index(a.index, a.as_of), conn)
    conn.close()
    after = {"manifest": sha256(a.manifest), "index": sha256(a.index)}
    inputs = {"manifest": {"path": a.manifest, "sha256": before["manifest"]},
              "index": {"path": a.index, "sha256": before["index"], "index_meta_manifest_sha256": meta.get("manifest_sha256")},
              "unchanged_after_run": before == after}
    common = {"task": "S7-P1-76", "decision_record": DECISION_RECORD, "as_of": a.as_of, "inputs": inputs,
              "legal_validity": "not_determined", "applicability_status": "not_assessed",
              "status": "producer-reported; offline; read-only; no gate approved"}
    inv = {"schema": INVENTORY_SCHEMA, **common, "summary": summary, "instruments": rows, "parts": parts}
    ev = {"schema": TD06_SCHEMA, **common, "citation": TD06_CITATION, "regression_record": regression_record(),
          "delta": {k: d.get(k) for k in ("baseline", "classification_status", "evidence_status", "legal_validity",
                                          "applicability_status", "title_resolution")},
          "delta_instruments": [g["doc_key"] for g in d["instruments"]], "instruments": instruments,
          "classification": cls, "classification_text": text,
          "method": "connection_basis = amendatory_location_clause iff the unit's verbatim instruction matches "
                    f"{NAMES_SECTION.pattern!r} and explicit_targets contains '1010.100'; else cross_reference_or_definition_mention"}
    out = {}
    for path, obj in ((a.inventory_out, inv), (a.td06_out, ev)):
        data = dumps(obj)
        Path(path).write_bytes(data)
        out[path] = hashlib.sha256(data).hexdigest()
    print(json.dumps({"outputs": out, "instruments": summary["instruments"], "classification": cls,
                      "inputs_unchanged": before == after}))
    return 0 if before == after and meta.get("manifest_sha256") == before["manifest"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
