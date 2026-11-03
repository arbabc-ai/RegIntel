"""S7-P1-55: deterministic offline checker for the delta-classification bundle.

    python3 -I scripts/check_p1_delta_classification_55.py OUT_DIR [--report REPORT.txt]

check() is pure over the inventory, the provenance sidecar, the task-54 inventory and the actual file hashes.
"""
import argparse, collections, hashlib, importlib.util, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLASSES = ("matched_provision", "matched_section_context", "matched_part_context", "unresolved_candidate", "reference_only",
           "no_acquired_instrument")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _b55():
    spec = importlib.util.spec_from_file_location("b55", REPO / "scripts" / "build_p1_delta_classification_55.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check(inv, prov, inv54, actual, pins):
    res = []
    def ok(name, cond, detail=""):
        res.append((name, bool(cond), "" if cond else detail))

    ok("schemas, HEAD and delta contract", inv.get("schema") == pins["schema"] and prov.get("schema") == pins["provenance_schema"]
       and inv.get("head") == prov.get("head") == pins["head"] and inv.get("delta_contract") == "p1-delta-provision-classification-1")
    bad = [k for k, v in pins["inputs"].items() if not (v == actual["inputs"].get(k) == prov["inputs"].get(k, {}).get("sha256")
                                                         == inv["inputs"].get(k, {}).get("sha256"))]
    ok("input hashes: pinned = recorded = actual", not bad, ", ".join(bad))
    bad = [k for k, v in prov["code"].items() if actual["code"].get(k) != v]
    ok("code hashes recorded in provenance = actual files", not bad, ", ".join(bad))
    ok("output hash recorded in provenance = actual inventory", prov["outputs"]["inventory"]["sha256"] == actual["outputs"]["inventory"])

    provs = inv["provisions"]
    keys = [(p["baseline_doc_key"], p["locator"]) for p in provs]
    want = {(e["baseline_doc_key"], e["locator"]) for e in inv54["eligible_pairs"]} | {(x["baseline_doc_key"], x["locator"]) for x in inv54["excluded_provisions"]}
    ok("every task-54 provision classified exactly once", len(keys) == len(set(keys)) and set(keys) == want, f"{len(keys)} vs {len(want)}")
    ok("best classification vocabulary", all(p["best_classification"] in CLASSES + ("not_queried",) for p in provs))
    bad = [f"{p['locator']}:{i['doc_key']}" for p in provs for i in p["instruments"]
           if i["provision_level_support"] != (i["classification"] == "matched_provision") or (i["provision_level_delta_match"] and not i["provision_level_support"])]
    ok("only matched_provision is provision-level support (context never promoted)", not bad, ", ".join(bad[:3]))
    bad = [p["locator"] for p in provs if p["provision_level_pairs"] != sorted(i["doc_key"] for i in p["instruments"] if i["provision_level_delta_match"])]
    ok("provision-level pairs derive only from provision-level delta matches", not bad, ", ".join(bad[:3]))
    b = _b55()
    bad = [p["locator"] for p in provs if p["best_classification"] != "not_queried"
           and (p["addressable_by_own_citation"] != b.addressable({"locator": p["locator"], "kind": p["kind"], "section_no": None if p["section_no"] == "none" else p["section_no"]})
                or (not p["addressable_by_own_citation"] and p["provision_level_pairs"]))]
    ok("non-addressable provisions (definitions, tables, definition-nested paragraphs) never carry provision-level pairs", not bad, ", ".join(bad[:3]))
    bad = [p["locator"] for p in provs if p["best_classification"] != "not_queried"
           and p["best_classification"] != (min((i["classification"] for i in p["instruments"]), key=CLASSES.index) if p["instruments"] else "no_acquired_instrument")]
    ok("best classification is the strongest per-instrument class", not bad, ", ".join(bad[:3]))
    ok("no_acquired_instrument is an explicit record, never an empty list",
       all(p["instruments"] for p in provs if p["best_classification"] != "not_queried"))
    ok("one instrument per record; current and future scopes separate",
       all(i["current_scope"] == "current_baseline" and i["future_scope"] == "future_instrument" and ";" not in str(i["doc_key"])
           for p in provs for i in p["instruments"])
       and all(len({i["doc_key"] for i in p["instruments"]}) == len(p["instruments"]) for p in provs))
    ok("proposals never labelled final or current",
       all(not (i["lifecycle_state"] or "").startswith(("final", "current")) for p in provs for i in p["instruments"] if i["document_type"] == "Proposed Rule"))
    ok("applicability not_assessed and legal validity not_determined",
       inv["applicability_status"] == "not_assessed" and inv["legal_validity"] == "not_determined"
       and all(i["applicability_status"] == "not_assessed" and i["legal_validity"] == "not_determined" for p in provs for i in p["instruments"]))
    table = collections.defaultdict(collections.Counter)
    for p in provs:
        table[p["task_54_category"]][p["best_classification"]] += 1
    ok("reconciliation table recomputes from the provisions",
       inv["reconciliation_task_54_category_by_best_classification"] == {c: dict(sorted(v.items())) for c, v in sorted(table.items())})
    ok("evidence-presence table recomputes from the provisions", inv["reconciliation_task_54_category_by_evidence_presence"] == b.presence(provs))
    pairs = sorted(f"{d}|{p['baseline_doc_key']}|{p['locator']}" for p in provs for d in p["provision_level_pairs"])
    c = inv["counts"]
    ok("counts consistent", c["provisions"] == len(provs) and c["provision_level_pairs"] == len(pairs) == len(inv["provision_level_pair_keys"])
       and pairs == inv["provision_level_pair_keys"]
       and c["provision_level_pairs_equal_task_54_eligible"] == (pairs == sorted(e["pair_key"] for e in inv54["eligible_pairs"])))
    ok("no gate or verification claim; no claim that excluded provisions lack future instruments",
       "no gate approved" in inv["status"] and "no independent rerun" in inv["status"] and "not claimed to have no future instruments" in inv["status"])
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir"); ap.add_argument("--report")
    a = ap.parse_args(argv)
    b = _b55()
    paths = {k: Path(a.out_dir) / v for k, v in b.NAMES.items()}
    inv, prov = json.loads(paths["inventory"].read_text()), json.loads(paths["provenance"].read_text())
    inv54 = json.loads(Path(b.INPUTS["inventory_54"][0]).read_text())
    actual = {"inputs": {k: sha(p) for k, (p, _) in b.INPUTS.items()}, "code": {k: sha(REPO / k) for k in prov["code"]},
              "outputs": {"inventory": sha(paths["inventory"])}}
    pins = {"head": b.HEAD, "schema": b.SCHEMA, "provenance_schema": b.PROVENANCE_SCHEMA, "inputs": {k: v for k, (_, v) in b.INPUTS.items()}}
    res = check(inv, prov, inv54, actual, pins)
    lines = [("PASS " if g else "FAIL ") + n + (f" — {x}" if x else "") for n, g, x in res]
    failed = sum(1 for _, g, _ in res if not g)
    lines.append(f"{len(res) - failed}/{len(res)} checks passed")
    text = "\n".join(lines) + "\n"
    if a.report:
        Path(a.report).write_text(text)
    print(text, end="")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
