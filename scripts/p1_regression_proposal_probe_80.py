"""S7-P1-80: run drafted regression literals through the existing check functions on an index (offline, read-only, stdlib only).

Proposal tooling only: nothing here changes eval/phase1/regressions.json, the evaluator or any index. Governing scope: founder record
2026-11-04_s7-regintel-p1-founder-decisions_accepted.md, which decides nothing about regression records.

Input (--drafts): {"drafts": [{"check": "K15", "option": "R", "group": "citation_completeness", "literal": {...},
"mutation": {"path": ["expect_reasons", "12 CFR 337.6(e)(4)"], "value": ["unused_reason"]}, "absent_parts": ["12:337"]}, ...]}.
For each draft the probe
  - runs the group's existing evaluator function (regintel_p1.evaluate) on the literal and records passed and failures;
  - records what the resolver and answer paths observe for it (statuses, citations, reasons, members, instruments, units);
  - applies the draft's `mutation` (the field that makes the literal non-vacuous) to a copy and reruns the check, which must fail;
  - for every `absent_parts` entry, reports whether ecfr:<title>:<part> is in the manifest and in the index's source_document.
The index is opened with SQLite mode=ro; its sha256 is recorded before and after, and the CLI exits 1 if it changed.

    python3 -I scripts/p1_regression_proposal_probe_80.py --index DB --drafts DRAFTS.json --out PROBE.json
"""
import argparse
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regintel_p1 import evaluate  # noqa: E402
from regintel_p1.search import Index, parse_query_citations  # noqa: E402

SCHEMA = "p1-regression-proposal-probe-0.1"
DECISION_RECORD = "2026-11-04_s7-regintel-p1-founder-decisions_accepted.md"
DEFAULT_MANIFEST = "/home/arbab/projects/regintel-corpus/phase1/manifest.json"
CHECKS = {"citation_completeness": evaluate.check_citation_completeness,
          "instruction_boundary": evaluate.check_instruction_boundary,
          "query_lists": evaluate.check_query_lists,
          "part_lists": evaluate.check_query_lists,  # the evaluator runs part_lists through the same contract
          "titleless_delta": evaluate.check_titleless_delta}
SCOPES = ("current", "future", "both")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolved_rows(index, query, scope):
    return [{"citation": r["citation"], "resolved": r["resolved"], "achieved": r.get("achieved"), "reason": r.get("reason")}
            for r in index.resolve_citations(query, scope)]


def answer_row(index, query, mode, scope):
    out = index.answer(query, mode=mode, scope=scope, k=10)
    return {"mode": mode, "scope": scope, "status": out["status"],
            "citations": [{"citation": c["citation"], "resolved": c["resolved"], "reason": c.get("reason")} for c in out["citations"]],
            "citation_gaps": sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))}


def observe(index, group, item):
    if group == "citation_completeness":
        return {"answers": [answer_row(index, item["query"], m, s) for m in ("exact", "hybrid") for s in SCOPES]}
    if group in ("query_lists", "part_lists"):
        return {"members_parsed": [c["canonical"] for c in parse_query_citations(item["shorthand"])],
                "resolver": {s: resolved_rows(index, item["shorthand"], s) for s in SCOPES},
                "current_exact": answer_row(index, item["shorthand"], "exact", "current")}
    if group == "instruction_boundary":
        rows = index.conn.execute(
            """SELECT p.locator, p.pairing_status, p.carried_text_origin, p.explicit_paragraphs, length(p.instruction) AS n
               FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment' AND p.section_no=?""",
            (item["doc_key"], item["section"])).fetchall()
        cite = lambda p: f"12 CFR {item['section']}{p}"
        paths = {}
        for p in item["supported"] + item["unsupported"]:
            fut = index.resolve_citations(cite(p), "future")[0]
            cur = index.answer(cite(p), mode="exact", scope="current", k=10)
            paths[p] = {"future_resolved": fut["resolved"], "future_achieved": fut.get("achieved"),
                        "future_paragraph_support": [x["doc_key"] for x in fut.get("future_paragraph_support") or []],
                        "current_exact_status": cur["status"]}
        return {"units": [{"locator": r["locator"], "pairing_status": r["pairing_status"], "carried_text_origin": r["carried_text_origin"],
                           "explicit_paragraphs": json.loads(r["explicit_paragraphs"] or "null"), "instruction_chars": r["n"]} for r in rows],
                "paths": paths}
    if group == "titleless_delta":
        d = index.delta(item["titleless"])
        return {"instruments": [g["doc_key"] for g in d["instruments"]], "baseline": bool(d["baseline"]),
                "baseline_doc_key": (d["baseline"] or {}).get("doc_key"), "title_resolution": d["title_resolution"],
                "legal_validity": d["legal_validity"], "applicability_status": d["applicability_status"]}
    raise ValueError(f"unsupported group {group}")


def mutate(item, mutation):
    out = copy.deepcopy(item)
    target = out
    for key in mutation["path"][:-1]:
        target = target[key]
    target[mutation["path"][-1]] = mutation["value"]
    return out


def probe(index, manifest, drafts):
    docs = manifest["documents"]
    held = {r[0] for r in index.conn.execute("SELECT doc_key FROM source_document")}
    rows = []
    for d in drafts:
        check = CHECKS[d["group"]]
        result = check(index, d["literal"])
        row = {"check": d["check"], "option": d["option"], "record": d.get("record", "replacement"), "group": d["group"],
               "literal_id": d["literal"]["id"], "passed": result["passed"], "failures": result["failures"],
               "observed": observe(index, d["group"], d["literal"])}
        if d.get("mutation"):
            m = check(index, mutate(d["literal"], d["mutation"]))
            row["non_vacuity"] = {"field": d["mutation"]["path"], "mutated_value": d["mutation"]["value"],
                                  "mutated_passed": m["passed"], "mutated_failures": m["failures"][:4]}
        row["absent_parts"] = [{"part": p, "manifest_key": f"ecfr:{p}", "in_manifest": f"ecfr:{p}" in docs,
                                "in_source_document": f"ecfr:{p}" in held} for p in d.get("absent_parts", [])]
        rows.append(row)
    return rows


def head():
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", required=True)
    ap.add_argument("--as-of", default="2026-10-29")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--drafts", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    before = sha256(a.index)
    index = Index(a.index, a.as_of)
    rows = probe(index, json.loads(Path(a.manifest).read_text()), json.loads(Path(a.drafts).read_text())["drafts"])
    meta = dict(index.meta)
    index.conn.close()
    after = sha256(a.index)
    out = {"schema": SCHEMA, "task": "S7-P1-80", "decision_record": DECISION_RECORD, "head": head(), "as_of": a.as_of,
           "index": {"path": a.index, "sha256": before, "unchanged_after_run": before == after, "built_at": meta.get("built_at"),
                     "manifest_sha256": meta.get("manifest_sha256")},
           "manifest": {"path": a.manifest, "sha256": sha256(a.manifest)},
           "regressions": {"path": "eval/phase1/regressions.json", "sha256": sha256(REPO / "eval/phase1/regressions.json")},
           "rows": rows, "legal_validity": "not_determined", "applicability_status": "not_assessed",
           "status": "proposal; producer-reported; no gate approved; nothing authorised"}
    data = (json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode()
    Path(a.out).write_bytes(data)
    print(json.dumps({"out": a.out, "sha256": hashlib.sha256(data).hexdigest(), "rows": len(rows),
                      "passed": sum(r["passed"] for r in rows),
                      "mutations_failing": sum(1 for r in rows if r.get("non_vacuity") and not r["non_vacuity"]["mutated_passed"]),
                      "index_unchanged": before == after}))
    return 0 if before == after else 1


if __name__ == "__main__":
    raise SystemExit(main())
