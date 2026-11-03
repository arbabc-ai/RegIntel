"""S7-P1-54: deterministic offline checker for the supplemental delta-coverage bundle.

    python3 -I scripts/check_p1_federal_delta_coverage_54.py OUT_DIR [--report REPORT.txt]

check() is pure over the three output objects, the task-52 crosswalk and a dict of actual file hashes.
Exit 0 only if every check passes.
"""
import argparse, hashlib, importlib.util, json, sqlite3, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("b54", REPO / "scripts" / "build_p1_federal_delta_coverage_54.py")


def _b54():
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def check(inv, cw, prov, task52, actual, pins, population=None):
    """`actual`: {"inputs": {name: sha}, "code": {rel: sha}, "outputs": {name: sha}}; `pins`: the builder's pinned constants."""
    b = _b54()
    res = []
    def ok(name, cond, detail=""):
        res.append((name, bool(cond), "" if cond else detail))

    ok("schemas and HEAD", inv.get("schema") == pins["inventory_schema"] and cw.get("schema") == pins["crosswalk_schema"]
       and prov.get("schema") == pins["provenance_schema"] and inv.get("head") == cw.get("head") == prov.get("head") == pins["head"])
    bad_in = [k for k, v in pins["inputs"].items()
              if not (prov["inputs"].get(k, {}).get("sha256") == v == actual["inputs"].get(k) == inv["inputs"].get(k, {}).get("sha256"))]
    ok("pinned input hashes: expected = recorded (provenance and inventory) = actual files", not bad_in, ", ".join(bad_in))
    bad_code = [k for k, v in prov["code"].items() if actual["code"].get(k) != v] + \
               [k for k, v in pins["code"].items() if prov["code"].get(k) != v]
    ok("code provenance: recorded code hashes = actual files = pinned contracts", not bad_code, ", ".join(bad_code))
    bad_out = [k for k, v in prov["outputs"].items() if v["sha256"] != actual["outputs"].get(k)]
    ok("output hashes recorded in the provenance sidecar = actual output files", not bad_out, ", ".join(bad_out))

    elig, miss, exc = inv["eligible_pairs"], inv["missing_pairs"], inv["excluded_provisions"]
    ekeys = [e["pair_key"] for e in elig]
    ok("eligible pair keys unique and deterministic (instrument|baseline|locator)",
       len(ekeys) == len(set(ekeys)) and all(e["pair_key"] == b.pair_key(e["instrument_doc_key"], e["baseline_doc_key"], e["locator"]) for e in elig))
    eprov = {(e["baseline_doc_key"], e["locator"]) for e in elig}
    xprov = [(x["baseline_doc_key"], x["locator"]) for x in exc]
    c = inv["counts"]
    ok("every compared provision is eligible or excluded exactly once",
       len(xprov) == len(set(xprov)) and not (eprov & set(xprov)) and len(eprov) + len(xprov) == c["compared_population_provisions"]
       and (population is None or population == c["compared_population_provisions"]),
       f"{len(eprov)} + {len(xprov)} vs {c['compared_population_provisions']} (index {population})")
    ok("every excluded provision has exactly one known reason; counts by reason match",
       all(x["reason"] in b.REASONS for x in exc)
       and c["excluded_by_reason"] == {r: sum(1 for x in exc if x["reason"] == r) for r in b.REASONS})
    rep = b.represented_pairs(task52)
    ok("complete subtraction: missing = eligible minus task-52-represented",
       sorted(m["pair_key"] for m in miss) == sorted(k for k in ekeys if k not in rep)
       and all((e["represented_by_task_52"] == "no") == (e["pair_key"] not in rep) for e in elig))
    ok("no task-52 pair duplicated in the supplement", not any(m["pair_key"] in rep for m in miss) and not any(r["pair_key"] in rep for r in cw["records"]))
    rkeys = [r["pair_key"] for r in cw["records"]]
    ok("one supplemental record per missing pair, exactly once", sorted(rkeys) == sorted(m["pair_key"] for m in miss)
       and len(rkeys) == len(set(rkeys)) and cw["record_count"] == len(rkeys))
    ok("current and future kept separate; one instrument per record",
       all(r["current"]["scope"] == "current_baseline" and r["current"]["doc_key"].startswith("ecfr:")
           and r["future"]["scope"] == "future_instrument" and r["future"]["doc_key"].startswith("fr:") and ";" not in r["future"]["doc_key"]
           and r["future"]["document_type"] in ("Rule", "Proposed Rule") and r["pair_key"].split("|")[0] == r["future"]["doc_key"]
           for r in cw["records"]))
    ok("proposals never labelled final, current or operative",
       all(not r["future"]["lifecycle_state"].startswith(("final", "current")) for r in cw["records"] if r["future"]["document_type"] == "Proposed Rule"))
    ok("applicability not_assessed and legal validity not_determined",
       inv["applicability_status"] == "not_assessed" and inv["legal_validity"] == "not_determined"
       and all(r["applicability_status"] == "not_assessed" and r["legal_validity"] == "not_determined" for r in cw["records"]))
    ok("counts block consistent", c["eligible_pairs"] == len(elig) and c["missing_pairs"] == len(miss)
       and c["excluded_provisions"] == len(exc) and c["represented_eligible_pairs"] == len(elig) - len(miss))
    ok("no gate or verification claim", "no gate approved" in inv["status"] and "no independent rerun" in inv["status"])
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir"); ap.add_argument("--report")
    a = ap.parse_args(argv)
    b = _b54()
    d = Path(a.out_dir)
    paths = {k: d / v for k, v in b.NAMES.items()}
    inv, cw, prov = (json.loads(paths[k].read_text()) for k in ("inventory", "crosswalk", "provenance"))
    task52 = json.loads(Path(b.INPUTS["crosswalk_52"][0]).read_text())
    actual = {"inputs": {k: sha(p) for k, (p, _) in b.INPUTS.items()},
              "code": {k: sha(REPO / k) for k in prov["code"]},
              "outputs": {k: sha(paths[k]) for k in ("inventory", "crosswalk")}}
    pins = {"head": b.HEAD, "inputs": {k: v for k, (_, v) in b.INPUTS.items()}, "code": b.CODE,
            "inventory_schema": b.INVENTORY_SCHEMA, "crosswalk_schema": b.CROSSWALK_SCHEMA, "provenance_schema": b.PROVENANCE_SCHEMA}
    conn = sqlite3.connect(f"file:{b.INPUTS['index'][0]}?mode=ro", uri=True)
    population = conn.execute("SELECT count(*) FROM provision p JOIN source_document s ON s.id=p.doc_id WHERE s.publisher='eCFR'").fetchone()[0]
    conn.close()
    res = check(inv, cw, prov, task52, actual, pins, population)
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
