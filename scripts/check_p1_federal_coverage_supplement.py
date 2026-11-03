"""S7-P1-51: deterministic offline checker for a p1-federal-coverage-supplement-0.1 inventory.

    python3 -I scripts/check_p1_federal_coverage_supplement.py INVENTORY.json [REPORT.txt]

Re-reads the manifest, held assets and index 49 (read-only). Exit 0 only if every check passes.
"""
import hashlib, json, re, sqlite3, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from regintel_p1.search import lifecycle

CORPUS = Path("/home/arbab/projects/regintel-corpus/phase1")
IDX = Path("/home/arbab/projects/regintel-corpus/phase1-index/regintel_p1_state_source_evidence_49.sqlite")
MANIFEST_SHA = "94e6e22216c7ebf48d3d1d4ea5684af5b9d01eb16c8f10452b1da70f02f141a3"
HEAD = "99f3274c4f5f828eefe348bd7f33add771ae2ec8"
HEX = re.compile(r"[0-9a-f]{64}")
CLASSES = {"explicit", "reference", "unresolved", "not_applicable"}


def walk_none(x, path=""):
    if x is None:
        yield path
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from walk_none(v, f"{path}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from walk_none(v, f"{path}[{i}]")


def main(inv_path, report_path=None):
    lines, failed = [], 0
    def check(name, ok, detail=""):
        nonlocal failed
        failed += 0 if ok else 1
        lines.append(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))

    o = json.loads(Path(inv_path).read_text())
    recs = o["records"]
    man_bytes = (CORPUS / "manifest.json").read_bytes()
    manifest = json.loads(man_bytes)["documents"]
    con = sqlite3.connect(f"file:{IDX}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rk = {r["doc_key"]: dict(r) for r in con.execute("SELECT * FROM rulemaking")}
    con.close()

    check("schema", o["schema"] == "p1-federal-coverage-supplement-0.1")
    check("provenance HEAD and manifest hash", o["provenance"]["head"] == HEAD
          and o["provenance"]["manifest"]["sha256"] == MANIFEST_SHA == hashlib.sha256(man_bytes).hexdigest())
    keys = [r["manifest_key"] for r in recs]
    check("counts: 74 records = 7 eCFR + 67 Federal Register",
          len(recs) == len(manifest) == 74 and sum(k.startswith("ecfr:") for k in keys) == 7 and sum(k.startswith("fr:") for k in keys) == 67,
          f"{len(recs)} records")
    check("every manifest source represented exactly once", sorted(keys) == sorted(manifest) and len(set(keys)) == len(keys))
    check("no duplicate record ids or FR document numbers",
          len({r["record_id"] for r in recs}) == len(recs)
          and len({r["document_number"] for r in recs if "document_number" in r}) == 67)
    check("counts block matches records", o["counts"]["records"] == len(recs)
          and o["counts"]["by_kind"] == {k: sum(r["record_kind"] == k for r in recs) for k in o["counts"]["by_kind"]})
    nones = [f"{r['record_id']}{p}" for r in recs for p in walk_none(r)]
    check("every field populated or explicitly 'not_recorded' (no nulls)", not nones, ", ".join(nones[:5]))

    bad_hash = []
    for r in recs:
        d = manifest[r["manifest_key"]]
        assets = [("asset", r["asset"], d["asset"])]
        assets += [("metadata_asset", r["metadata_asset"], d["metadata_asset"])] if "metadata_asset" in r else []
        assets += [("catalog_asset", r["catalog_asset"], d["catalog_asset"])] if "catalog_asset" in r else []
        for name, a, m in assets:
            if not (HEX.fullmatch(a["sha256"]) and a["sha256"] == m["sha256"] and a["sha256_verified_offline"] is True
                    and hashlib.sha256((CORPUS / a["path"]).read_bytes()).hexdigest() == a["sha256"]):
                bad_hash.append(f"{r['record_id']}:{name}")
    check("hash fields present, equal to the manifest, and re-verified against held bytes", not bad_hash, ", ".join(bad_hash[:5]))

    cb = [r for r in recs if r["record_kind"] == "current_baseline"]
    fr = [r for r in recs if r["manifest_key"].startswith("fr:")]
    check("current/future separation",
          all(r["manifest_key"].startswith("ecfr:") and r["current_or_future"] == "current_baseline" for r in cb)
          and all(r["current_or_future"] != "current_baseline" for r in fr)
          and all(r["current_or_future"] == ("not_in_future_scope_notice" if r["document_type"] == "Notice" else "future_instrument") for r in fr)
          and all((r["record_kind"] == "notice") == (r["document_type"] == "Notice") for r in fr))
    bad_life = [r["record_id"] for r in fr if lifecycle(rk[r["manifest_key"]], "2026-10-29")["state"] != r["lifecycle_state"]]
    check("FR lifecycle labels equal producer lifecycle() at 2026-10-29", not bad_life, ", ".join(bad_life))
    check("no proposed instrument labelled final/current/operative",
          all(not r["lifecycle_state"].startswith(("final", "current")) and "operative" not in r["current_or_future"]
              for r in fr if r["document_type"] == "Proposed Rule"))
    undated = [r["record_id"] for r in fr if r["document_type"] == "Rule" and r["effective_on"] == "not_recorded"]
    check("undated Rule documents carry only the producer label final_no_effective_date_in_metadata and are not current",
          all(r["lifecycle_state"] == "final_no_effective_date_in_metadata" and r["current_or_future"] == "future_instrument"
              for r in fr if r["record_id"] in undated), "undated: " + (", ".join(undated) or "none"))
    check("no record claims operative effect or applicability",
          all(r["applicability_status"] == "not_assessed" and r["legal_validity"] == "not_determined" for r in recs)
          and not any(re.search(r'"(operative|applicable|legally_effective|current_in_force)"', json.dumps(r)) for r in recs))
    maps = [m for r in fr for m in r["cfr_mappings"]]
    check("every CFR mapping classified explicit/reference/unresolved/not_applicable with evidence",
          all(m["classification"] in CLASSES and m["evidence"] and m["basis"] for m in maps), f"{len(maps)} mappings")
    check("C3 crosswalk uses explicit mappings only",
          all(set(r["c3_crosswalk_ids"]) == {i for m in r["cfr_mappings"] if m["classification"] == "explicit" and m["c3_crosswalk"] != "not_applicable"
                                             for i in m["c3_crosswalk"]["c3_inventory_ids"]} for r in fr))
    check("every unmapped or partially mapped source carries a gap",
          all((r["gap"] != "none") == (not r["c3_crosswalk_ids"] or any(m["classification"] == "explicit" and m["c3_crosswalk"] != "not_applicable"
                                                                   and not m["c3_crosswalk"]["c3_inventory_ids"] for m in r["cfr_mappings"]))
              for r in fr)
          and all((r["gap"] != "none") == (not r["c3_crosswalk"]["c3_inventory_ids"]) for r in cb)
          and all(r["gap"] for r in recs))
    check("no verification claim", "no independent verification" in o["status"] and "no gate approved" in o["status"])

    lines.append(f"{len(lines) - failed}/{len(lines)} checks passed")
    text = "\n".join(lines) + "\n"
    if report_path:
        Path(report_path).write_text(text)
    print(text, end="")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
