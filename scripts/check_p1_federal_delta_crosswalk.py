"""S7-P1-52: deterministic offline checker for a p1-federal-delta-crosswalk-0.1 crosswalk.

    python3 -I scripts/check_p1_federal_delta_crosswalk.py CROSSWALK.json SUPPLEMENT_51.json [--index INDEX.sqlite]
        [--manifest manifest.json] [--report REPORT.txt] [--expect-manifest-sha256 H] [--expect-supplement-sha256 H]
        [--expect-index-sha256 H]

`check()` is pure over the two JSON objects plus the hashes of the supplied input files; when an index is given, every
matched record is also re-verified against it (read-only). Exit 0 only if every check passes.
"""
import argparse, hashlib, json, re, sqlite3, sys
from pathlib import Path

EXPECTED = {"schema": "p1-federal-delta-crosswalk-0.1",
            "supplement_schema": "p1-federal-coverage-supplement-0.1",
            "supplement_sha256": "15001f1390fadde31316838438db65cec92dfeeac79eac9e6b181b03713c9aa8",
            "manifest_sha256": "94e6e22216c7ebf48d3d1d4ea5684af5b9d01eb16c8f10452b1da70f02f141a3",
            "index_sha256": "f74ef7c1b49baa48f96d8eb0bbf84220c81b5b88ceead787edd0d38c916239bb"}

STATUSES = {"matched", "unavailable", "ambiguous_title", "section_only", "paragraph_precision_unresolved", "unsupported"}
PATH = re.compile(r"^(\d+) CFR (\d+)\.([0-9A-Za-z-]+)((?:\([A-Za-z0-9]+\))*)$")


def provenance_checks(cw, sup, supplied, expected=None):
    """Input provenance: `supplied` holds the actual hashes/paths of the files given to the checker."""
    exp = {**EXPECTED, **(expected or {})}
    prov = cw.get("provenance") or {}
    sprov = sup.get("provenance") or {}
    res = []
    res.append(("expected crosswalk schema/version", cw.get("schema") == exp["schema"], f"{cw.get('schema')!r} != {exp['schema']!r}"))
    res.append(("expected supplement schema/version", sup.get("schema") == exp["supplement_schema"],
                f"{sup.get('schema')!r} != {exp['supplement_schema']!r}"))
    s_sha = supplied.get("supplement_sha256")
    res.append(("supplement hash: supplied file = recorded in crosswalk = expected",
                s_sha == (prov.get("supplement_51") or {}).get("sha256") == exp["supplement_sha256"],
                f"supplied {s_sha}, recorded {(prov.get('supplement_51') or {}).get('sha256')}, expected {exp['supplement_sha256']}"))
    m_sha = supplied.get("manifest_sha256")
    res.append(("manifest hash: supplied file = recorded by supplement = expected",
                (m_sha is None or m_sha == exp["manifest_sha256"]) and (sprov.get("manifest") or {}).get("sha256") == exp["manifest_sha256"],
                f"supplied {m_sha}, supplement-recorded {(sprov.get('manifest') or {}).get('sha256')}, expected {exp['manifest_sha256']}"))
    if supplied.get("index_path") is not None:
        rec = prov.get("index") or {}
        res.append(("index: supplied path and hash = recorded in crosswalk and supplement = expected",
                    supplied["index_path"] == rec.get("path") == (sprov.get("index") or {}).get("path")
                    and supplied["index_sha256"] == rec.get("sha256") == (sprov.get("index") or {}).get("sha256") == exp["index_sha256"],
                    f"supplied {supplied['index_path']} {supplied['index_sha256']}, recorded {rec.get('path')} {rec.get('sha256')}"))
    return [(n, bool(c), "" if c else d) for n, c, d in res]


def check(cw, sup, conn=None, supplied=None, expected=None):
    out = list(provenance_checks(cw, sup, supplied, expected)) if supplied is not None else []
    def ok(name, cond, detail=""):
        out.append((name, bool(cond), detail if not cond else ""))

    recs = {r["record_id"]: r for r in sup["records"]}
    acc = cw["input_accounting"]
    deltas = cw["deltas"]
    ids = [a["inventory_id"] for a in acc]
    ok("schema", cw.get("schema") == "p1-federal-delta-crosswalk-0.1")
    ok("every inventory record accounted for exactly once", sorted(ids) == sorted(recs) and len(ids) == len(set(ids)),
       f"{len(ids)} accounted vs {len(recs)} inventory")
    pres = []
    for a in acc:
        r = recs.get(a["inventory_id"])
        if not r or a["manifest_key"] != r["manifest_key"] or a["lifecycle_state"] != r["lifecycle_state"] or a["existing_gap"] != r["gap"] \
                or a["source_kind"] != r["record_kind"] or (r.get("document_number") and a.get("document_number") != r["document_number"]):
            pres.append(a["inventory_id"])
    ok("inventory fields preserved (id, key, kind, document number, lifecycle, gap)", not pres, ", ".join(pres[:5]))
    roles = {a["inventory_id"]: a["role"] for a in acc}
    ok("current records are current-baseline sources only; FR records never current",
       all((roles[i] == "current_baseline_source") == recs[i]["manifest_key"].startswith("ecfr:") for i in ids if i in recs)
       and all(roles[i] in ("future_instrument_source", "excluded_notice_not_in_future_scope") for i in ids if i in recs and recs[i]["manifest_key"].startswith("fr:")))

    pairs = [(d["instrument_doc_key"], d["requested_citation"]) for d in deltas]
    dup = sorted({p for p in pairs if pairs.count(p) > 1}) if len(pairs) != len(set(pairs)) else []
    ok("no duplicate instrument/provision pair; delta_id = instrument|citation",
       not dup and all(d["delta_id"] == f"{d['instrument_doc_key']}|{d['requested_citation']}" for d in deltas), str(dup[:3]))
    future_src = {a["manifest_key"]: a for a in acc if a["role"] == "future_instrument_source"}
    mism = [d["delta_id"] for d in deltas if d["instrument_doc_key"] in future_src and (
        d["document_number"] != future_src[d["instrument_doc_key"]].get("document_number")
        or d["document_type"] != future_src[d["instrument_doc_key"]].get("document_type")
        or d["instrument_doc_key"] != f"fr:{d['document_number']}")]
    ok("pair records consistent with their instrument (document number, type, key)", not mism, ", ".join(mism[:3]))
    ok("every delta belongs to one future-instrument source (no notices, no baselines, no merged instruments)",
       all(d["instrument_doc_key"] in future_src and d["instrument_doc_key"].startswith("fr:") and ";" not in d["instrument_doc_key"]
           and d["current_or_future"] == "future_instrument" and d["inventory_id"] == future_src[d["instrument_doc_key"]]["inventory_id"]
           for d in deltas))
    leak = [d["delta_id"] for d in deltas if d["current_baseline"] != "none" and not str(d["current_baseline"].get("doc_key", "")).startswith("ecfr:")]
    ok("current side of every delta is an eCFR current-baseline record (no cross-scope leakage)", not leak, ", ".join(leak[:3]))
    cnt = {}
    for d in deltas:
        cnt[d["instrument_doc_key"]] = cnt.get(d["instrument_doc_key"], 0) + 1
    ok("per-source delta counts match", all(a["delta_records"] == cnt.get(a["manifest_key"], 0) for a in acc))

    bad_status = [d["delta_id"] for d in deltas if d["baseline_status"] not in STATUSES]
    ok("baseline status vocabulary", not bad_status, ", ".join(bad_status[:3]))
    gaps = [d["delta_id"] for d in deltas if d["baseline_status"] != "matched" and not (
        isinstance(d["gap"], dict) and d["gap"].get("class") == d["baseline_status"] and d["gap"].get("instrument") == d["instrument_doc_key"]
        and d["gap"].get("requested_path") == d["requested_citation"] and d["gap"].get("reason") and d["baseline_reason"] not in ("", "none"))]
    ok("every non-matched record carries a structured gap with instrument, path and reason", not gaps, ", ".join(gaps[:3]))
    false = []
    for d in deltas:
        if d["baseline_status"] != "matched":
            continue
        cb = d["current_baseline"]
        m = PATH.match(d["requested_citation"])
        if d["gap"] != "none" or cb == "none" or not m or cb.get("role") != "baseline":
            false.append(d["delta_id"]); continue
        sec, path = f"{m.group(2)}.{m.group(3)}", m.group(4)
        if d["requested_precision"] == "paragraph":
            pp = d["paragraph_precision"]
            if not (path and cb["precision"] == "paragraph" and cb["locator"] == sec + path and isinstance(pp, dict)
                    and pp.get("resolved") and pp.get("supported_by")):
                false.append(d["delta_id"])
        elif d["requested_precision"] == "section":
            if path or cb["locator"] != f"section-{sec}" or cb.get("match") != "exact_section":
                false.append(d["delta_id"])
        else:
            false.append(d["delta_id"])
    ok("no false exact match (matched needs the instrument and the baseline at the requested precision)", not false, ", ".join(false[:3]))
    prop = [d["delta_id"] for d in deltas if d["document_type"] == "Proposed Rule" and (
        d["lifecycle_state"].startswith(("final", "current")) or "operative" in json.dumps(d["lifecycle_state"])
        or d["baseline_relation"] not in ("proposal_not_in_baseline", "none"))]
    ok("proposals never labelled final, current or operative", not prop, ", ".join(prop[:3]))
    life = [d["delta_id"] for d in deltas if d["lifecycle_state"] != recs[d["inventory_id"]]["lifecycle_state"]]
    ok("delta lifecycle equals the inventory's producer lifecycle label", not life, ", ".join(life[:3]))
    ok("applicability not_assessed and legal validity not_determined on every delta",
       all(d["applicability_status"] == "not_assessed" and d["legal_validity"] == "not_determined" for d in deltas))
    c = cw["counts"]
    ok("counts block consistent", c["inventory_records"] == len(acc) and c["delta_records"] == len(deltas)
       and sum(c["baseline_status"].values()) == len(deltas))
    ok("no verification or gate claim", "no gate approved" in cw["status"] and "no independent rerun" in cw["status"])

    if conn is not None:
        bad = []
        for d in deltas:
            if d["baseline_status"] != "matched":
                continue
            cb = d["current_baseline"]
            if not conn.execute("""SELECT 1 FROM provision p JOIN source_document s ON s.id=p.doc_id
                                   WHERE s.doc_key=? AND p.locator=? AND s.sha256=?""", (cb["doc_key"], cb["locator"], cb["sha256"])).fetchone():
                bad.append(d["delta_id"]); continue
            if d["requested_precision"] == "paragraph":
                path = PATH.match(d["requested_citation"]).group(4)
                sec_cit = d["requested_citation"][: -len(path)]
                rows = conn.execute("""SELECT p.explicit_paragraphs FROM relationship r JOIN provision p ON p.id=r.src_provision_id
                                       JOIN source_document s ON s.id=p.doc_id WHERE s.doc_key=? AND r.rel_type='amends' AND r.dst_citation=?""",
                                    (d["instrument_doc_key"], sec_cit)).fetchall()
                if not any((json.loads(x[0] or "{}").get("status") == "attributed" and path in json.loads(x[0] or "{}").get("paths", [])) for x in rows):
                    bad.append(d["delta_id"])
        ok("index re-check: matched baselines exist with their hash; matched paragraphs are attributed by the instrument", not bad, ", ".join(bad[:3]))
    return out


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("crosswalk"); ap.add_argument("supplement")
    ap.add_argument("--index"); ap.add_argument("--manifest"); ap.add_argument("--report")
    ap.add_argument("--expect-manifest-sha256"); ap.add_argument("--expect-supplement-sha256"); ap.add_argument("--expect-index-sha256")
    a = ap.parse_args(argv)
    exp = {k: v for k, v in {"manifest_sha256": a.expect_manifest_sha256, "supplement_sha256": a.expect_supplement_sha256,
                             "index_sha256": a.expect_index_sha256}.items() if v}
    cw = json.loads(Path(a.crosswalk).read_text())
    sup = json.loads(Path(a.supplement).read_text())
    supplied = {"supplement_sha256": sha(a.supplement), "manifest_sha256": sha(a.manifest) if a.manifest else None,
                "index_path": a.index, "index_sha256": sha(a.index) if a.index else None}
    conn = sqlite3.connect(f"file:{a.index}?mode=ro", uri=True) if a.index else None
    res = check(cw, sup, conn, supplied, exp)
    if conn:
        conn.close()
    lines = [("PASS " if good else "FAIL ") + name + (f" — {detail}" if detail else "") for name, good, detail in res]
    failed = sum(1 for _, good, _ in res if not good)
    lines.append(f"{len(res) - failed}/{len(res)} checks passed")
    text = "\n".join(lines) + "\n"
    if a.report:
        Path(a.report).write_text(text)
    print(text, end="")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
