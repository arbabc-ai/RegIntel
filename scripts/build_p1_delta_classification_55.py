"""S7-P1-55: per-provision delta classification bundle (contract p1-delta-provision-classification-1), offline and read-only.

Every provision of the task-54 compared population is classified through the producer's Index.delta(): paragraph and
section provisions by their own citation; definitions and tables (not addressable by a paragraph path) by their section
citation, where a section-level match is reported as matched_section_context, never as provision support. Heading rows
and range sections keep their task-54 exclusion and are not queried. The result is reconciled against task 54.

    python3 -I scripts/build_p1_delta_classification_55.py OUT_DIR [--expect-<input>-sha256 H ...]
"""
import argparse, collections, hashlib, importlib.util, json, re, subprocess, sys
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
    "inventory_54": (OB / "2026-11-02_S7_P1_FEDERAL_DELTA_COVERAGE_54/2026-11-02_S7_P1_FEDERAL_DELTA_COVERAGE_54_INVENTORY.json",
                     "2e0b80ae98ec3592bcb235765a8d895b14847eedb0f78d00551d150f6a3ef906"),
}
SCHEMA = "p1-delta-provision-classification-inventory-0.1"
PROVENANCE_SCHEMA = "p1-delta-provision-classification-provenance-0.1"
CODE_FILES = ("regintel_p1/search.py", "regintel_p1/evaluate.py", "scripts/build_p1_federal_delta_crosswalk.py",
              "scripts/build_p1_delta_classification_55.py", "scripts/check_p1_delta_classification_55.py")
NAMES = {"inventory": "2026-11-02_S7_P1_DELTA_CLASSIFICATION_55_INVENTORY.json",
         "provenance": "2026-11-02_S7_P1_DELTA_CLASSIFICATION_55_PROVENANCE.json"}
DESIGNATORS = re.compile(r"^(?:\([A-Za-z0-9]{1,5}\))+$")  # an enumerated paragraph path such as (a)(1)(i)
RANK = ("matched_provision", "matched_section_context", "matched_part_context", "unresolved_candidate", "reference_only",
        "no_acquired_instrument")
NOT_QUERIED = ("source_class_not_eligible", "ambiguous_current_provision_scope")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B52 = _load("build_p1_federal_delta_crosswalk")  # atomic_write_json, ProvenanceError
ProvenanceError = B52.ProvenanceError


def verify(inputs=None, expected=None, check_head=True):
    inputs = {**INPUTS, **(inputs or {})}
    exp = {k: v for k, (_, v) in inputs.items()}
    exp.update(expected or {})
    problems, rec = [], {}
    for k, (path, _) in inputs.items():
        rec[k] = {"path": str(path), "sha256": sha(path)}
        if rec[k]["sha256"] != exp[k]:
            problems.append(f"{k} sha256 {rec[k]['sha256']} != expected {exp[k]}")
    if "inventory_54" in inputs and not problems:
        schema = json.loads(Path(inputs["inventory_54"][0]).read_text()).get("schema")
        if schema != "p1-federal-delta-coverage-inventory-0.1":
            problems.append(f"inventory_54 schema {schema!r} unexpected")
    if check_head:
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        if head != HEAD:
            problems.append(f"HEAD {head} != expected {HEAD}")
    if problems:
        raise ProvenanceError("; ".join(problems))
    return rec


def best(classes):
    return min(classes, key=RANK.index) if classes else "no_acquired_instrument"


def addressable(p):
    """True when the provision has its own citation path: a section, or a paragraph whose path is enumerated designators only.

    Definitions, tables and paragraphs nested under a defined term (for example `217.2(Advanced approaches ...)(1)`) are not:
    turning their locator into a citation would parse as a different paragraph or as malformed.
    """
    if p["kind"] == "section":
        return p["locator"] == f"section-{p['section_no']}"
    return p["kind"] == "paragraph" and bool(DESIGNATORS.match(p["locator"][len(p["section_no"] or ""):]))


def downgrade(records):
    """Non-addressable provisions are queried through their section: section evidence is context, never provision support."""
    out = []
    for r in records:
        r = dict(r)
        if r["classification"] == "matched_provision":
            r["classification"] = "matched_section_context"
            r["reason"] = "the provision (definition, table or definition-nested paragraph) has no own citation path; its section's evidence is context only"
            r["provision_level_support"] = r["provision_level_delta_match"] = False
        out.append(r)
    return out


def classify_population(index_path, inv54):
    from regintel_p1.search import Index
    idx = Index(str(index_path), as_of="2026-10-29")
    rows = {(r["doc_key"], r["locator"]): dict(r) for r in idx.conn.execute(
        "SELECT d.doc_key, p.locator, p.kind, p.title_no, p.section_no FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.publisher='eCFR'")}
    t54 = {(e["baseline_doc_key"], e["locator"]): "eligible" for e in inv54["eligible_pairs"]}
    detail54 = {}
    for x in inv54["excluded_provisions"]:
        t54[(x["baseline_doc_key"], x["locator"])] = x["reason"]
        detail54[(x["baseline_doc_key"], x["locator"])] = x.get("reason_detail", "none")
    cache, out = {}, []
    for key in sorted(t54):
        doc, loc = key
        p = rows[key]
        cat54 = t54[key] if t54[key] != "instrument_scope_mismatch" else f"instrument_scope_mismatch:{detail54[key]}"
        if t54[key] in NOT_QUERIED:
            out.append({"baseline_doc_key": doc, "locator": loc, "kind": p["kind"], "section_no": p["section_no"] or "none", "task_54_category": cat54,
                        "requested_citation": "not_queried", "classification_status": f"not_queried:{t54[key]}",
                        "best_classification": "not_queried", "instruments": [], "provision_level_pairs": []})
            continue
        sec_cit = f"{p['title_no']} CFR {p['section_no']}"
        own = addressable(p)
        cite = f"{p['title_no']} CFR {loc}" if own and p["kind"] == "paragraph" else sec_cit
        if cite not in cache:
            cache[cite] = idx.delta(cite)
        d = cache[cite]
        recs = d["instrument_classification"]
        if not own:
            recs = downgrade(recs)
        out.append({"baseline_doc_key": doc, "locator": loc, "kind": p["kind"], "section_no": p["section_no"] or "none", "task_54_category": cat54,
                    "addressable_by_own_citation": own, "requested_citation": cite, "classification_status": d["classification_status"],
                    "best_classification": best([r["classification"] for r in recs]),
                    "instruments": [{"doc_key": r["doc_key"], "document_type": r["document_type"], "lifecycle_state": r["lifecycle_state"],
                                     "classification": r["classification"], "other_evidence": sorted(set(r["other_evidence"])),
                                     "provision_level_support": r["provision_level_support"],
                                     "provision_level_delta_match": r["provision_level_delta_match"],
                                     "current_scope": r["current"]["scope"], "future_scope": r["future"]["scope"],
                                     "applicability_status": r["applicability_status"], "legal_validity": r["legal_validity"]} for r in recs],
                    "provision_level_pairs": sorted(r["doc_key"] for r in recs if r["provision_level_delta_match"])})
    idx.conn.close()
    return out


def reconcile(provisions):
    table = collections.defaultdict(collections.Counter)
    for p in provisions:
        table[p["task_54_category"]][p["best_classification"]] += 1
    return {cat: dict(sorted(c.items())) for cat, c in sorted(table.items())}


def presence(provisions):
    """Per task-54 category: provisions with ANY evidence of each class (primary or other_evidence), so weaker evidence stays visible."""
    table = collections.defaultdict(collections.Counter)
    for p in provisions:
        seen = {c for i in p["instruments"] for c in [i["classification"], *i.get("other_evidence", [])]}
        for c in seen:
            table[p["task_54_category"]][c] += 1
    return {cat: dict(sorted(c.items())) for cat, c in sorted(table.items())}


def build(out_dir, inputs=None, expected=None, check_head=True):
    rec_inputs = verify(inputs, expected, check_head)
    ins = {**INPUTS, **(inputs or {})}
    inv54 = json.loads(Path(ins["inventory_54"][0]).read_text())
    provisions = classify_population(ins["index"][0], inv54)
    pairs = sorted(f"{d}|{p['baseline_doc_key']}|{p['locator']}" for p in provisions for d in p["provision_level_pairs"])
    t54_pairs = sorted(e["pair_key"] for e in inv54["eligible_pairs"])
    counts = {"provisions": len(provisions),
              "by_best_classification": dict(sorted(collections.Counter(p["best_classification"] for p in provisions).items())),
              "provision_level_pairs": len(pairs), "provision_level_provisions": sum(1 for p in provisions if p["provision_level_pairs"]),
              "task_54_eligible_pairs": len(t54_pairs), "provision_level_pairs_equal_task_54_eligible": pairs == t54_pairs}
    inventory = {"schema": SCHEMA, "head": HEAD, "delta_contract": "p1-delta-provision-classification-1",
                 "status": "producer-reported; offline; read-only; no gate approved; no independent rerun; section and part context are never "
                           "provision-level support; excluded provisions are not claimed to have no future instruments",
                 "inputs": rec_inputs, "counts": counts, "reconciliation_task_54_category_by_best_classification": reconcile(provisions),
                 "reconciliation_task_54_category_by_evidence_presence": presence(provisions),
                 "provision_level_pair_keys": pairs, "provisions": provisions,
                 "applicability_status": "not_assessed", "legal_validity": "not_determined"}
    out_dir = Path(out_dir)
    paths = {k: out_dir / v for k, v in NAMES.items()}
    B52.atomic_write_json(paths["inventory"], inventory)
    prov = {"schema": PROVENANCE_SCHEMA, "head": HEAD, "inputs": rec_inputs,
            "code": {f: sha(REPO / f) for f in CODE_FILES},
            "outputs": {"inventory": {"path": str(paths["inventory"]), "sha256": sha(paths["inventory"]), "schema": SCHEMA}}}
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
