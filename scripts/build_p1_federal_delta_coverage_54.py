"""S7-P1-54: supplemental federal current-to-future delta coverage (offline, read-only).

Compared population: every acquired current eCFR provision in the task-49 index whose kind is one of the producer's
current-scope CONTENT_KINDS. A (provision, instrument) pair is ELIGIBLE when a future Federal Register instrument
(Rule or Proposed Rule) explicitly targets that provision at the provision's own precision, using only rows the
producer parser recorded (an `amends` relationship resolved to the section, an attributed paragraph path, or a numeric
amendment range), AND the producer's Index.delta() for the provision's citation classifies the pair as `matched`
(task-52 classify()). Subtraction population: every (instrument, baseline, locator) already represented by a task-52
record, whatever its status. Missing = eligible minus represented. Every other provision gets exactly one exclusion
reason, by fixed precedence.

    python3 -I scripts/build_p1_federal_delta_coverage_54.py OUT_DIR [--expect-<input>-sha256 H ...]
"""
import argparse, hashlib, importlib.util, json, sqlite3, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

HEAD = "99f3274c4f5f828eefe348bd7f33add771ae2ec8"
OB = Path("/mnt/c/Users/arbab/OneDrive/Documents/AI-OS/projects/velodyn/Velodyn_BankOS/claude/s7-regintel-rag")
INPUTS = {
    "manifest": (Path("/home/arbab/projects/regintel-corpus/phase1/manifest.json"),
                 "94e6e22216c7ebf48d3d1d4ea5684af5b9d01eb16c8f10452b1da70f02f141a3"),
    "index": (Path("/home/arbab/projects/regintel-corpus/phase1-index/regintel_p1_state_source_evidence_49.sqlite"),
              "f74ef7c1b49baa48f96d8eb0bbf84220c81b5b88ceead787edd0d38c916239bb"),
    "supplement_51": (OB / "2026-11-02_S7_P1_FEDERAL_COVERAGE_SUPPLEMENT_51/2026-11-02_S7_P1_FEDERAL_COVERAGE_SUPPLEMENT_51_INVENTORY.json",
                      "15001f1390fadde31316838438db65cec92dfeeac79eac9e6b181b03713c9aa8"),
    "crosswalk_52": (OB / "2026-11-02_S7_P1_FEDERAL_DELTA_CROSSWALK_52/2026-11-02_S7_P1_FEDERAL_DELTA_CROSSWALK_52.json",
                     "626aa0aacc5a31cd57c3f393ddb611618df516769e45045d95a128238fd75791"),
}
CODE = {  # code contracts this build depends on; a mismatch is a provenance failure
    "regintel_p1/search.py": "97153e335cf5acbc78fcc8a1a7c62703f76ef71191d4cbe77a662ff6ac145afb",
    "scripts/build_p1_federal_delta_crosswalk.py": "e11af0b859d428350d03a9fd936e279212ddb6289579b27253d1fced4bc35887",
}
SCHEMAS = {"supplement_51": "p1-federal-coverage-supplement-0.1", "crosswalk_52": "p1-federal-delta-crosswalk-0.1"}
INVENTORY_SCHEMA = "p1-federal-delta-coverage-inventory-0.1"
CROSSWALK_SCHEMA = "p1-federal-delta-supplemental-crosswalk-0.1"
PROVENANCE_SCHEMA = "p1-federal-delta-coverage-provenance-0.1"
NAMES = {"inventory": "2026-11-02_S7_P1_FEDERAL_DELTA_COVERAGE_54_INVENTORY.json",
         "crosswalk": "2026-11-02_S7_P1_FEDERAL_DELTA_COVERAGE_54_SUPPLEMENTAL_CROSSWALK.json",
         "provenance": "2026-11-02_S7_P1_FEDERAL_DELTA_COVERAGE_54_PROVENANCE.json"}
REASONS = ("source_class_not_eligible", "ambiguous_current_provision_scope", "instrument_scope_mismatch",
           "unsupported_mapping", "future_instrument_not_acquired", "no_future_instrument")
REASON_DEFS = {
    "source_class_not_eligible": "provision kind is not a producer current-scope content kind (part, subpart, authority, source or footnote heading)",
    "ambiguous_current_provision_scope": "the provision has no single section number (for example an acquired range section such as 3.4-3.9)",
    "instrument_scope_mismatch": "an instrument explicitly amends the enclosing section (checked first) or, failing that, only the enclosing part, "
                                 "but no instrument targets this provision at its own precision; reason_detail says which",
    "unsupported_mapping": "only unresolved candidates, references, or a pair the producer delta does not classify as matched relate to the provision's section",
    "future_instrument_not_acquired": "never assigned: the offline index cannot show instruments that were not acquired; kept so the vocabulary is complete",
    "no_future_instrument": "no acquired Federal Register Rule or Proposed Rule targets, cites or names the provision's section or part",
}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B52 = load("build_p1_federal_delta_crosswalk")  # classify() and atomic_write_json(), unchanged
ProvenanceError = B52.ProvenanceError


def verify(inputs=None, code=None, expected=None, check_head=True):
    """Assert every pinned input, schema, code hash and HEAD. Returns the recorded provenance; raises ProvenanceError."""
    inputs = {**INPUTS, **(inputs or {})}
    code = {**CODE, **(code or {})}
    exp = {k: v for k, (_, v) in inputs.items()}
    exp.update(expected or {})
    problems, rec = [], {"inputs": {}, "code": {}}
    for k, (path, _) in inputs.items():
        actual = sha(path)
        rec["inputs"][k] = {"path": str(path), "sha256": actual}
        if actual != exp[k]:
            problems.append(f"{k} sha256 {actual} != expected {exp[k]}")
        if k in SCHEMAS:
            schema = json.loads(Path(path).read_text()).get("schema")
            rec["inputs"][k]["schema"] = schema
            if schema != SCHEMAS[k]:
                problems.append(f"{k} schema {schema!r} != expected {SCHEMAS[k]!r}")
    for rel, want in code.items():
        actual = sha(REPO / rel)
        rec["code"][rel] = actual
        if actual != want:
            problems.append(f"code {rel} sha256 {actual} != expected {want}")
    if check_head:
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        rec["head"] = head
        if head != HEAD:
            problems.append(f"HEAD {head} != expected {HEAD}")
    if problems:
        raise ProvenanceError("; ".join(problems))
    return rec


def pair_key(instrument, baseline_doc, locator):
    """Deterministic pair key: instrument | baseline document | baseline locator."""
    return f"{instrument}|{baseline_doc}|{locator}"


def citation_of(title, locator):
    return f"{title} CFR {locator[len('section-'):]}" if locator.startswith("section-") else f"{title} CFR {locator}"


def represented_pairs(crosswalk52):
    """Every (instrument, baseline, locator) a task-52 record represents, whatever its status."""
    out = {}
    for d in crosswalk52["deltas"]:
        cb = d["current_baseline"]
        m = B52.PATH.match(d["requested_citation"])
        if cb == "none" or not m:
            continue
        loc = f"{m.group(2)}.{m.group(3)}{m.group(4)}" if m.group(4) else f"section-{m.group(2)}.{m.group(3)}"
        out[pair_key(d["instrument_doc_key"], cb["doc_key"], loc)] = {"delta_id": d["delta_id"], "baseline_status": d["baseline_status"]}
    return out


def assign_reason(prov, explicit, section_amended, part_amended, weak):
    """(reason, detail) by fixed precedence, or (None, None) when eligible. Exactly one reason per provision. Pure."""
    from regintel_p1.search import CONTENT_KINDS
    if prov["kind"] not in CONTENT_KINDS:
        return "source_class_not_eligible", f"kind {prov['kind']}"
    if not prov["section_no"] or "-" in prov["section_no"]:
        return "ambiguous_current_provision_scope", f"section_no {prov['section_no']!r}"
    if explicit:
        return None, None
    if section_amended:
        return "instrument_scope_mismatch", "enclosing_section_explicitly_amended"
    if weak:
        return "unsupported_mapping", "section_has_only_unresolved_candidates_references_or_unmatched_pairs"
    if part_amended:
        return "instrument_scope_mismatch", "enclosing_part_amended_by_part_level_instruction_only"
    return "no_future_instrument", "none"


def compute(index_path, supplement, crosswalk52):
    from regintel_p1.search import Index
    idx = Index(str(index_path), as_of="2026-10-29")
    c = idx.conn
    future = {r["manifest_key"]: r for r in supplement["records"] if r["record_kind"] == "future_instrument"}
    provs = [dict(r) for r in c.execute("""SELECT p.id, p.locator, p.kind, p.title_no, p.part_no, p.section_no, p.citation, p.text,
                                              d.doc_key, d.sha256 FROM provision p JOIN source_document d ON d.id=p.doc_id
                                       WHERE d.publisher='eCFR' ORDER BY d.doc_key, p.ordinal, p.id""")]
    by_loc = {(p["doc_key"], p["locator"]): p for p in provs}
    base_of = {(p["title_no"], p["part_no"]): p["doc_key"] for p in provs if p["kind"] == "part"}
    explicit, sec_amended, part_amended, weak = {}, {}, {}, {}
    units = {}
    for r in c.execute("""SELECT r.rel_type, r.dst_citation, dp.locator dst_loc, dd.doc_key dst_doc, p.locator unit, p.explicit_paragraphs,
                                 sd.doc_key src FROM relationship r JOIN provision p ON p.id=r.src_provision_id
                                 JOIN source_document sd ON sd.id=p.doc_id
                                 LEFT JOIN provision dp ON dp.id=r.dst_provision_id LEFT JOIN source_document dd ON dd.id=dp.doc_id
                          WHERE sd.publisher='Federal Register' AND r.rel_type IN ('amends','amends_candidate','references')"""):
        if r["src"] not in future:
            continue
        t = r["dst_citation"].split()
        if len(t) < 3 or t[1] != "CFR":
            continue
        title = t[0]
        if r["rel_type"] == "amends" and t[2] == "Part" and len(t) > 3:
            part_amended.setdefault((title, t[3]), set()).add(r["src"])
            continue
        sec = t[2]
        part = sec.split(".")[0]
        if r["rel_type"] != "amends":
            weak.setdefault((title, sec), set()).add(r["src"])
            continue
        sec_amended.setdefault((title, sec), set()).add(r["src"])
        doc = base_of.get((title, part))
        if r["dst_doc"] and r["dst_doc"] == doc:
            explicit.setdefault((doc, r["dst_loc"]), set()).add(r["src"])
            units.setdefault((r["src"], doc, r["dst_loc"]), set()).add(r["unit"])
            ep = json.loads(r["explicit_paragraphs"] or "{}")
            if ep.get("status") == "attributed":
                for path in ep.get("paths", []):
                    if (doc, sec + path) in by_loc:
                        explicit.setdefault((doc, sec + path), set()).add(r["src"])
                        units.setdefault((r["src"], doc, sec + path), set()).add(r["unit"])
    for r in c.execute("""SELECT a.title_no, a.part_no, a.lo_minor, a.hi_minor, a.status, p.locator unit, sd.doc_key src FROM amendment_range a
                          JOIN provision p ON p.id=a.src_provision_id JOIN source_document sd ON sd.id=p.doc_id"""):
        doc = base_of.get((r["title_no"], r["part_no"]))
        if r["src"] not in future or not doc or r["status"] != "numeric_range":
            continue
        for p in provs:
            if p["doc_key"] == doc and p["kind"] == "section" and p["section_no"].split(".")[-1].isdigit() \
                    and r["lo_minor"] <= int(p["section_no"].split(".")[-1]) <= r["hi_minor"]:
                explicit.setdefault((doc, p["locator"]), set()).add(r["src"])
                units.setdefault((r["src"], doc, p["locator"]), set()).add(r["unit"])

    represented = represented_pairs(crosswalk52)
    eligible, unconfirmed, exclusions = [], [], []
    precision_of = lambda loc: "section" if loc.startswith("section-") else "paragraph"
    for p in provs:
        key = (p["doc_key"], p["locator"])
        title, sec = p["title_no"], p["section_no"] or ""
        cand = sorted(explicit.get(key, set())) if p["kind"] in ("section", "paragraph") else []
        matched = []
        if cand and sec and "-" not in sec:
            res = idx.delta(citation_of(title, p["locator"]))
            for inst in cand:
                st, why, _ = B52.classify(res, inst, precision_of(p["locator"]), True)
                rec = {"pair_key": pair_key(inst, p["doc_key"], p["locator"]), "instrument_doc_key": inst,
                       "baseline_doc_key": p["doc_key"], "locator": p["locator"], "delta_status": st}
                (matched if st == "matched" else unconfirmed).append({**rec, "reason": why or "none"})
        reason, detail = assign_reason(p, matched, sec_amended.get((title, sec)), part_amended.get((title, p["part_no"])),
                                       weak.get((title, sec)) or (cand and not matched))
        if reason is None:
            eligible.extend(matched)
        else:
            exclusions.append({"baseline_doc_key": p["doc_key"], "locator": p["locator"], "kind": p["kind"], "reason": reason,
                               "reason_detail": detail})
    idx.conn.close()
    for e in eligible:
        e["represented_by_task_52"] = represented.get(e["pair_key"], "no")
    missing = [e for e in eligible if e["represented_by_task_52"] == "no"]
    return provs, by_loc, eligible, unconfirmed, exclusions, missing, represented, units, future


def build(out_dir, inputs=None, code=None, expected=None, check_head=True):
    recorded = verify(inputs, code, expected, check_head)  # raises before any computation or write
    ins = {**INPUTS, **(inputs or {})}
    supplement = json.loads(Path(ins["supplement_51"][0]).read_text())
    cw52 = json.loads(Path(ins["crosswalk_52"][0]).read_text())
    provs, by_loc, eligible, unconfirmed, exclusions, missing, represented, units, future = compute(ins["index"][0], supplement, cw52)
    keys = [e["pair_key"] for e in eligible]
    assert len(keys) == len(set(keys)), "duplicate eligible pair"
    counts = {"compared_population_provisions": len(provs),
              "eligible_provisions": len({(e["baseline_doc_key"], e["locator"]) for e in eligible}),
              "eligible_pairs": len(eligible), "represented_eligible_pairs": len(eligible) - len(missing),
              "missing_pairs": len(missing), "unconfirmed_candidate_pairs": len(unconfirmed),
              "task_52_represented_pairs": len(represented),
              "excluded_provisions": len(exclusions),
              "excluded_by_reason": {r: sum(1 for x in exclusions if x["reason"] == r) for r in REASONS},
              "excluded_by_reason_detail": {k: v for k, v in sorted(__import__("collections").Counter(
                  f"{x['reason']}:{x['reason_detail'] if x['reason'] in ('instrument_scope_mismatch', 'unsupported_mapping', 'no_future_instrument') else 'see kind/section_no'}"
                  for x in exclusions).items())}}
    inventory = {"schema": INVENTORY_SCHEMA, "head": HEAD,
                 "status": "producer-reported; offline; read-only; no gate approved; no independent rerun; no applicability, legal validity, "
                           "operative effect or amendment identity inferred",
                 "definitions": {"compared_population": "every acquired current eCFR provision in the task-49 index (7 baselines)",
                                 "eligible_pair": "future instrument explicitly targets the provision at its own precision (parsed amends to the section, "
                                                  "attributed paragraph path, or numeric range) and Index.delta() + task-52 classify() return matched",
                                 "subtraction_population": "every (instrument, baseline, locator) represented by a task-52 record, whatever its status",
                                 "missing_pair": "eligible pair not in the subtraction population",
                                 "exclusion_reasons": REASON_DEFS,
                                 "precedence": ["source_class_not_eligible", "ambiguous_current_provision_scope", "eligible",
                                                "instrument_scope_mismatch (enclosing section)", "unsupported_mapping",
                                                "instrument_scope_mismatch (enclosing part only)", "no_future_instrument"]},
                 "inputs": recorded["inputs"], "counts": counts,
                 "eligible_pairs": eligible, "missing_pairs": missing, "unconfirmed_candidate_pairs": unconfirmed,
                 "excluded_provisions": exclusions,
                 "applicability_status": "not_assessed", "legal_validity": "not_determined"}
    records = []
    for e in missing:
        p = by_loc[(e["baseline_doc_key"], e["locator"])]
        f = future[e["instrument_doc_key"]]
        records.append({"pair_key": e["pair_key"],
                        "current": {"scope": "current_baseline", "doc_key": p["doc_key"], "locator": p["locator"], "citation": p["citation"],
                                    "kind": p["kind"], "document_sha256": p["sha256"],
                                    "provision_text_sha256": hashlib.sha256(p["text"].encode("utf-8")).hexdigest()},
                        "future": {"scope": "future_instrument", "doc_key": f["manifest_key"], "document_number": f["document_number"],
                                   "document_type": f["document_type"], "lifecycle_state": f["lifecycle_state"],
                                   "unit_locators": sorted(units.get((e["instrument_doc_key"], e["baseline_doc_key"], e["locator"]), [])),
                                   "document_sha256": f["asset"]["sha256"]},
                        "applicability_status": "not_assessed", "legal_validity": "not_determined"})
    crosswalk = {"schema": CROSSWALK_SCHEMA, "head": HEAD, "status": inventory["status"], "record_count": len(records),
                 "note": "one record per eligible missing pair; current and future are separate objects; no instrument is merged",
                 "records": records}
    out_dir = Path(out_dir)
    paths = {k: out_dir / v for k, v in NAMES.items()}
    B52.atomic_write_json(paths["inventory"], inventory)
    B52.atomic_write_json(paths["crosswalk"], crosswalk)
    prov = {"schema": PROVENANCE_SCHEMA, "head": recorded.get("head", HEAD), "inputs": recorded["inputs"],
            "code": {**recorded["code"], "scripts/build_p1_federal_delta_coverage_54.py": sha(__file__),
                     "scripts/check_p1_federal_delta_coverage_54.py": sha(REPO / "scripts/check_p1_federal_delta_coverage_54.py")},
            "outputs": {k: {"path": str(paths[k]), "sha256": sha(paths[k]),
                            "schema": INVENTORY_SCHEMA if k == "inventory" else CROSSWALK_SCHEMA} for k in ("inventory", "crosswalk")}}
    B52.atomic_write_json(paths["provenance"], prov)
    print(json.dumps(counts))
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    for k in INPUTS:
        ap.add_argument(f"--expect-{k.replace('_', '-')}-sha256", dest=k)
    a = ap.parse_args()
    try:
        build(a.out_dir, expected={k: getattr(a, k) for k in INPUTS if getattr(a, k)})
    except ProvenanceError as exc:
        print(f"PROVENANCE ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
