"""S7-P1-52: federal current-to-future provision-level delta crosswalk (schema p1-federal-delta-crosswalk-0.1).

Offline and read-only. Candidate targets come only from index relationships the producer parser already recorded
(explicit `amends` targets, attributed paragraph paths on those amendment units, numeric amendment ranges, and
`amends_candidate` rows); every pairing is decided by the producer's own `Index.delta()` (which uses
`current_baseline()` and `resolve_citations()`). Nothing is hand-inferred.

    python3 -I scripts/build_p1_federal_delta_crosswalk.py SUPPLEMENT_51.json INDEX.sqlite OUT.json [--manifest PATH]
        [--expect-manifest-sha256 H] [--expect-supplement-sha256 H] [--expect-index-sha256 H]

S7-P1-53: inputs are checked against expected provenance before anything is built (verify_inputs), and outputs are
written atomically (atomic_write_json: a temporary file in the destination directory, then os.replace), so a failed
build never leaves a partial or replaced deliverable. The input provenance is also recorded in OUT.provenance.json.
"""
import argparse, hashlib, json, os, re, sqlite3, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SCHEMA = "p1-federal-delta-crosswalk-0.1"
AS_OF = "2026-10-29"
BASELINE_STATUSES = ("matched", "unavailable", "ambiguous_title", "section_only", "paragraph_precision_unresolved", "unsupported")
EXPECTED = {"schema": SCHEMA,
            "supplement_schema": "p1-federal-coverage-supplement-0.1",
            "supplement_sha256": "15001f1390fadde31316838438db65cec92dfeeac79eac9e6b181b03713c9aa8",
            "manifest_sha256": "94e6e22216c7ebf48d3d1d4ea5684af5b9d01eb16c8f10452b1da70f02f141a3",
            "index_sha256": "f74ef7c1b49baa48f96d8eb0bbf84220c81b5b88ceead787edd0d38c916239bb"}
DEFAULT_MANIFEST = Path("/home/arbab/projects/regintel-corpus/phase1/manifest.json")
PROVENANCE_SCHEMA = "p1-federal-delta-crosswalk-provenance-0.1"


class ProvenanceError(ValueError):
    """An input does not match its expected provenance; nothing is built or written."""


PATH = re.compile(r"^(\d+) CFR (\d+)\.([0-9A-Za-z-]+)((?:\([A-Za-z0-9]+\))*)$")


def classify(result, doc_key, precision, part_acquired=None):
    """Baseline status for one instrument and one requested citation, from a producer delta() result.

    `precision` is the requested precision ("paragraph", "section" or "part"). Returns (status, reason, match).
    A match is reported only when the producer result shows both the instrument and a baseline at the requested
    precision; anything less is a named gap. Pure function: no I/O.
    """
    tr = result.get("title_resolution") or {}
    if tr.get("status") == "ambiguous":
        return "ambiguous_title", "the citation's title could not be resolved to one CFR title", None
    inst = next((g for g in result.get("instruments") or [] if g["doc_key"] == doc_key), None)
    if inst is None:
        return "unsupported", "the producer delta does not list this instrument as an explicit amendment of the citation", None
    match = sorted({u.get("match") for u in inst.get("units", [])})
    base, ctx = result.get("baseline"), result.get("section_context")
    if base is None and ctx is None:
        if part_acquired is False:
            return "unavailable", "the baseline part is not acquired", match
        if part_acquired is True:
            return "unavailable", "the part is acquired but the cited section is not in the acquired baseline (it may be new; not inferred)", match
        return "unavailable", ((result.get("unavailable") or {}).get("reason") or "no current baseline text for the citation in the acquired eCFR"), match
    if precision == "part":
        return "unsupported", "part-level target only; the producer delta pairs it with the part heading, not with a provision", match
    if precision == "section":
        if base and base.get("match") == "range_section":
            return "section_only", "the baseline is an acquired range section (typically [Reserved]), not the requested section itself", match
        return "matched", None, match
    pp = inst.get("paragraph_precision") or {}
    if not pp.get("resolved"):
        return "paragraph_precision_unresolved", "this instrument's units do not explicitly name the complete requested paragraph path", match
    if base is None:
        return "section_only", "the instrument names the paragraph, but the current baseline has only the section (the paragraph is not in the acquired baseline)", match
    if base.get("precision") != "paragraph":
        return "section_only", "the current baseline is not resolved at paragraph precision", match
    return "matched", None, match


def candidates_for(conn, doc_id):
    """Distinct candidate citations for one instrument, from parsed index rows only. {citation: {precision, sources}}."""
    out = {}
    def add(cit, precision, source):
        rec = out.setdefault(cit, {"precision": precision, "sources": set()})
        rec["sources"].add(source)
    for r in conn.execute("""SELECT r.rel_type, r.dst_citation, p.explicit_paragraphs, p.pairing_status FROM relationship r
                             JOIN provision p ON p.id=r.src_provision_id WHERE p.doc_id=? AND r.rel_type IN ('amends','amends_candidate')""",
                          (doc_id,)):
        cit = r["dst_citation"]
        if r["rel_type"] == "amends_candidate":
            add(cit, "part" if " Part " in cit else "section", f"unresolved_candidate[{r['pairing_status']}]")
            continue
        if " Part " in cit:
            add(cit, "part", f"part_level_instruction[{r['pairing_status']}]")
            continue
        add(cit, "section", f"explicit_amendment_target[{r['pairing_status']}]")
        ep = json.loads(r["explicit_paragraphs"] or "{}")
        if ep.get("status") == "attributed":
            for path in ep.get("paths", []):
                add(f"{cit}{path}", "paragraph", "attributed_paragraph_path")
    for r in conn.execute("""SELECT a.title_no, a.part_no, a.lo_section, a.hi_section, a.lo_minor, a.hi_minor, a.status FROM amendment_range a
                             JOIN provision p ON p.id=a.src_provision_id WHERE p.doc_id=?""", (doc_id,)):
        if r["status"] != "numeric_range":
            add(f"{r['title_no']} CFR {r['lo_section']}", "section", "ambiguous_range_endpoint")
            continue
        # range members are the acquired baseline's own numeric sections inside the parsed range (none if the part is not acquired)
        for s in conn.execute("""SELECT DISTINCT p.section_no FROM provision p JOIN source_document d ON d.id=p.doc_id
                                 WHERE d.publisher='eCFR' AND p.title_no=? AND p.part_no=? AND p.kind='section'""",
                              (r["title_no"], r["part_no"])):
            minor = (s[0] or "").split(".")[-1]
            if minor.isdigit() and r["lo_minor"] <= int(minor) <= r["hi_minor"]:
                add(f"{r['title_no']} CFR {s[0]}", "section", "numeric_range_membership")
        add(f"{r['title_no']} CFR Part {r['part_no']}", "part", "numeric_range_part")
    return out


def build(supplement, index_path):
    from regintel_p1.search import Index
    idx = Index(str(index_path), as_of=AS_OF)
    conn = idx.conn
    docid = {r["doc_key"]: r["id"] for r in conn.execute("SELECT id, doc_key FROM source_document")}
    acquired = {k.split(":", 1)[1] for k in docid if k.startswith("ecfr:")}
    records = supplement["records"]
    accounting, deltas = [], []
    for rec in records:
        key = rec["manifest_key"]
        if rec["record_kind"] == "current_baseline":
            accounting.append({"inventory_id": rec["record_id"], "manifest_key": key, "source_kind": "current_baseline",
                               "role": "current_baseline_source", "lifecycle_state": rec["lifecycle_state"],
                               "asset_sha256": rec["asset"]["sha256"], "source_url": rec["source_url"],
                               "existing_gap": rec["gap"], "delta_records": 0})
            continue
        if rec["record_kind"] == "notice":
            accounting.append({"inventory_id": rec["record_id"], "manifest_key": key, "source_kind": "notice",
                               "document_number": rec["document_number"], "role": "excluded_notice_not_in_future_scope",
                               "lifecycle_state": rec["lifecycle_state"], "asset_sha256": rec["asset"]["sha256"],
                               "existing_gap": rec["gap"], "delta_records": 0})
            continue
        cands = candidates_for(conn, docid[key])
        n0 = len(deltas)
        mapping_class = sorted({m["classification"] for m in rec["cfr_mappings"]})
        if not cands:  # no parsed target at all: one structured gap per publisher-metadata part
            parts = sorted({m["citation"] for m in rec["cfr_mappings"] if "publisher_metadata_cfr_references" in m["basis"]}) or ["none"]
            for cit in parts:
                deltas.append(_gap_record(rec, cit, "part", ["publisher_metadata_part_only"], "unsupported",
                                          "no explicit amendment target is parsed for this instrument; the publisher metadata names the part only"))
        for cit in sorted(cands):
            c = cands[cit]
            only_candidate = all(s.startswith(("unresolved_candidate", "ambiguous_range")) for s in c["sources"])
            if only_candidate:
                deltas.append(_gap_record(rec, cit, c["precision"], sorted(c["sources"]), "unsupported",
                                          "unresolved amendment candidate (could not be paired explicitly); the producer delta lists it under unresolved_candidates only"))
                continue
            query = cit
            m = PATH.match(cit)
            try:
                res = idx.delta(query)
            except ValueError as exc:
                deltas.append(_gap_record(rec, cit, c["precision"], sorted(c["sources"]), "unsupported", f"citation not parseable by delta(): {exc}"))
                continue
            tp = re.match(r"^(\d+) CFR (?:Part )?(\d+)", cit)
            status, reason, match = classify(res, key, c["precision"], (tp and f"{tp.group(1)}:{tp.group(2)}" in acquired) if tp else None)
            inst = next((g for g in res["instruments"] if g["doc_key"] == key), None)
            base = res.get("baseline") or res.get("section_context")
            deltas.append({
                "delta_id": f"{key}|{cit}", "inventory_id": rec["record_id"], "instrument_doc_key": key,
                "document_number": rec["document_number"], "document_type": rec["document_type"],
                "lifecycle_state": rec["lifecycle_state"], "current_or_future": "future_instrument",
                "mapping_classification": mapping_class, "existing_gap": rec["gap"],
                "citation": res["canonical_citation"], "requested_citation": cit,
                "target_path": (m.group(4) or "none") if m else "none", "requested_precision": res["requested_precision"],
                "candidate_sources": sorted(c["sources"]),
                "baseline_status": status, "baseline_reason": reason or "none",
                "pairing_match": match or [], "pairing_statuses": sorted({u.get("pairing_status") or "none" for u in (inst or {}).get("units", [])}),
                "instrument_units": sorted(u["locator"] for u in (inst or {}).get("units", [])),
                "instrument_sha256": rec["asset"]["sha256"], "instrument_source_url": rec["source_url"],
                "paragraph_precision": (inst or {}).get("paragraph_precision") or "not_requested",
                "baseline_relation": (inst or {}).get("baseline_relation") or "none",
                "current_baseline": ({"doc_key": base["doc_key"], "locator": base["locator"], "citation": base["citation"],
                                      "sha256": base["sha256"], "issue_date": base["issue_date"],
                                      "role": "baseline" if res.get("baseline") else "section_context",
                                      "precision": (res.get("baseline") or {}).get("precision", "section_or_part"),
                                      "match": base.get("match", "none")} if base else "none"),
                "current_precision": res["current_precision"], "future_precision": res["future_precision"],
                "title_resolution": (res.get("title_resolution") or {}).get("status", "none"),
                "also_unresolved_candidate": any(s.startswith("unresolved_candidate") for s in c["sources"]),
                "gap": ("none" if status == "matched" else {"class": status, "instrument": key, "requested_path": cit, "reason": reason}),
                "applicability_status": res["applicability_status"], "legal_validity": res["legal_validity"]})
        accounting.append({"inventory_id": rec["record_id"], "manifest_key": key, "source_kind": "future_instrument",
                           "document_number": rec["document_number"], "document_type": rec["document_type"],
                           "role": "future_instrument_source", "lifecycle_state": rec["lifecycle_state"],
                           "mapping_classification": mapping_class, "asset_sha256": rec["asset"]["sha256"],
                           "source_url": rec["source_url"], "existing_gap": rec["gap"], "delta_records": len(deltas) - n0})
    idx.conn.close()
    return accounting, deltas


def _gap_record(rec, cit, precision, sources, status, reason):
    return {"delta_id": f"{rec['manifest_key']}|{cit}", "inventory_id": rec["record_id"], "instrument_doc_key": rec["manifest_key"],
            "document_number": rec["document_number"], "document_type": rec["document_type"], "lifecycle_state": rec["lifecycle_state"],
            "current_or_future": "future_instrument", "mapping_classification": sorted({m["classification"] for m in rec["cfr_mappings"]}),
            "existing_gap": rec["gap"], "citation": cit, "requested_citation": cit,
            "target_path": (PATH.match(cit).group(4) or "none") if PATH.match(cit) else "none",
            "requested_precision": precision, "candidate_sources": sources, "baseline_status": status, "baseline_reason": reason,
            "pairing_match": [], "pairing_statuses": [], "instrument_units": [], "instrument_sha256": rec["asset"]["sha256"],
            "instrument_source_url": rec["source_url"], "paragraph_precision": "not_requested", "baseline_relation": "none",
            "current_baseline": "none", "current_precision": "none", "future_precision": "none", "title_resolution": "not_queried",
            "also_unresolved_candidate": any(s.startswith("unresolved_candidate") for s in sources),
            "gap": {"class": status, "instrument": rec["manifest_key"], "requested_path": cit, "reason": reason},
            "applicability_status": "not_assessed", "legal_validity": "not_determined"}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def verify_inputs(sup_path, index_path, manifest_path, expected=None):
    """Assert the supplied inputs match the expected provenance. Returns the recorded provenance; raises ProvenanceError.

    Checked: supplement bytes hash and schema; the manifest hash (actual file, expected value, and the value the
    supplement recorded); the index hash (actual file and expected value) and that the supplement was built from the
    same index path and hash.
    """
    exp = {**EXPECTED, **(expected or {})}
    problems = []
    sup_sha, idx_sha, man_sha = sha(sup_path), sha(index_path), sha(manifest_path)
    sup = json.loads(Path(sup_path).read_text())
    prov = sup.get("provenance") or {}
    if sup_sha != exp["supplement_sha256"]:
        problems.append(f"supplement sha256 {sup_sha} != expected {exp['supplement_sha256']}")
    if sup.get("schema") != exp["supplement_schema"]:
        problems.append(f"supplement schema {sup.get('schema')!r} != expected {exp['supplement_schema']!r}")
    if man_sha != exp["manifest_sha256"]:
        problems.append(f"manifest sha256 {man_sha} != expected {exp['manifest_sha256']}")
    if (prov.get("manifest") or {}).get("sha256") != exp["manifest_sha256"]:
        problems.append(f"supplement-recorded manifest sha256 {(prov.get('manifest') or {}).get('sha256')} != expected {exp['manifest_sha256']}")
    if idx_sha != exp["index_sha256"]:
        problems.append(f"index sha256 {idx_sha} != expected {exp['index_sha256']}")
    if (prov.get("index") or {}).get("sha256") != idx_sha or (prov.get("index") or {}).get("path") != str(index_path):
        problems.append("the supplement was not built from the supplied index (path or sha256 differs)")
    if problems:
        raise ProvenanceError("; ".join(problems))
    return {"schema": PROVENANCE_SCHEMA, "crosswalk_schema": exp["schema"], "head": "99f3274c4f5f828eefe348bd7f33add771ae2ec8",
            "supplement": {"path": str(sup_path), "sha256": sup_sha, "schema": sup["schema"]},
            "manifest": {"path": str(manifest_path), "sha256": man_sha, "recorded_by_supplement": prov["manifest"]["sha256"]},
            "index": {"path": str(index_path), "sha256": idx_sha, "recorded_by_supplement": prov["index"]["sha256"]},
            "assertions": "all expected values matched before the build started"}


def atomic_write_json(path, obj):
    """Serialize first, then write a temporary file in the destination directory and os.replace it into place.

    If serialization or writing fails, the destination keeps its previous bytes and no temporary file is left behind.
    """
    path = Path(path)
    data = json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def provenance_path(out):
    out = Path(out)
    return out.with_name(out.stem + ".provenance.json")


def main(sup_path, index_path, out, manifest_path=DEFAULT_MANIFEST, expected=None):
    recorded = verify_inputs(sup_path, index_path, manifest_path, expected)  # raises before anything is built or written
    sup = json.loads(Path(sup_path).read_text())
    accounting, deltas = build(sup, index_path)
    counts = {"inventory_records": len(accounting),
              "by_role": {}, "delta_records": len(deltas), "baseline_status": {}, "instruments_with_matched": 0}
    for a in accounting:
        counts["by_role"][a["role"]] = counts["by_role"].get(a["role"], 0) + 1
    for d in deltas:
        counts["baseline_status"][d["baseline_status"]] = counts["baseline_status"].get(d["baseline_status"], 0) + 1
    counts["instruments_with_matched"] = len({d["instrument_doc_key"] for d in deltas if d["baseline_status"] == "matched"})
    o = {"schema": SCHEMA, "as_of": AS_OF,
         "status": "producer-reported; offline; read-only; no gate approved; no independent rerun of producer tests, index builds, evaluation or checks; "
                   "no instrument is labelled current, final or operative beyond the producer lifecycle label",
         "baseline_statuses": list(BASELINE_STATUSES),
         "provenance": {"head": "99f3274c4f5f828eefe348bd7f33add771ae2ec8", "supplement_51": {"path": str(sup_path), "sha256": sha(sup_path)},
                        "index": {"path": str(index_path), "sha256": sha(index_path), "opened": "read-only via regintel_p1.search.Index"},
                        "code": {"regintel_p1/search.py": sha(REPO / "regintel_p1/search.py"), "builder": sha(__file__), "committed": False}},
         "counts": counts, "input_accounting": accounting, "deltas": deltas}
    atomic_write_json(out, o)
    atomic_write_json(provenance_path(out), {**recorded, "crosswalk": {"path": str(out), "sha256": sha(out)}})
    print(json.dumps(counts))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("supplement"); ap.add_argument("index"); ap.add_argument("out")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--expect-manifest-sha256"); ap.add_argument("--expect-supplement-sha256"); ap.add_argument("--expect-index-sha256")
    a = ap.parse_args()
    exp = {k: v for k, v in {"manifest_sha256": a.expect_manifest_sha256, "supplement_sha256": a.expect_supplement_sha256,
                             "index_sha256": a.expect_index_sha256}.items() if v}
    try:
        main(a.supplement, a.index, a.out, a.manifest, exp)
    except ProvenanceError as exc:
        print(f"PROVENANCE ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
