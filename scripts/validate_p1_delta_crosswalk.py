"""S7-P1-51: deterministic offline validator for a p1-delta-readiness-crosswalk-0.1 bundle.

    python3 -I scripts/validate_p1_delta_crosswalk.py CROSSWALK.json CROSSWALK.csv

Re-checks the JSON/CSV against the corpus manifest and index 49 (opened read-only). Exit 0 only if every check passes.
"""
import csv, hashlib, json, sqlite3, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from regintel_p1.search import lifecycle  # producer label function, unchanged

MANIFEST_SHA = "94e6e22216c7ebf48d3d1d4ea5684af5b9d01eb16c8f10452b1da70f02f141a3"
HEAD = "99f3274c4f5f828eefe348bd7f33add771ae2ec8"
CORPUS = Path("/home/arbab/projects/regintel-corpus/phase1")
IDX = Path("/home/arbab/projects/regintel-corpus/phase1-index/regintel_p1_state_source_evidence_49.sqlite")
AS_OF = "2026-10-29"
REQUIRED = ["topic_id", "source_class", "jurisdiction", "authority", "legal_entity_scope", "locator", "source_url",
            "acquisition_status", "parsing_indexing_status", "rights_status", "provenance_reference", "publication_date",
            "effective_date", "current_or_future", "coverage_or_gap_reason", "evidence_pointer", "sha256", "last_checked",
            "lifecycle_state", "applicability_status", "legal_validity"]
STATUSES = {"provision_level_ready", "section_context_only", "baseline_not_acquired", "unresolved_ambiguous_mapping"}


def main(pj, pc):
    results = []
    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    o = json.loads(Path(pj).read_text())
    rows = o["rows"]
    with open(pc, newline="") as f:
        reader = csv.DictReader(f)
        csv_fields, csv_rows = reader.fieldnames, list(reader)
    manifest_bytes = (CORPUS / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)["documents"]
    con = sqlite3.connect(f"file:{IDX}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    check("schema id", o.get("schema") == "p1-delta-readiness-crosswalk-0.1")
    check("provenance: head", o["provenance"]["head"] == HEAD)
    check("provenance: manifest hash", o["provenance"]["manifest"]["sha256"] == MANIFEST_SHA == hashlib.sha256(manifest_bytes).hexdigest())
    check("provenance: census and S7-P1-50 delivery recorded",
          all(o["provenance"].get(k, {}).get("sha256") for k in ("c3_federal_cfr_part_census", "s7_p1_50_delivery")))
    check("required fields present (JSON field list and CSV header)",
          all(f in o["fields"] for f in REQUIRED) and csv_fields == o["fields"])
    norm = lambda rs: sorted(json.dumps({k: r[k] for k in o["fields"]}, sort_keys=True) for r in rs)
    check("JSON and CSV identical normalized row sets", norm(rows) == norm(csv_rows) and all(isinstance(v, str) for r in rows for v in r.values()))
    ids = [r["row_id"] for r in rows]
    check("row_id unique", len(ids) == len(set(ids)))
    check("applicability not_assessed / legal_validity not_determined on every row",
          all(r["applicability_status"] == "not_assessed" and r["legal_validity"] == "not_determined" for r in rows))

    cb = [r for r in rows if r["row_kind"] == "current_baseline"]
    fr = [r for r in rows if r["row_kind"] in ("future_instrument", "notice")]
    cmp_ = [r for r in rows if r["row_kind"] == "comparison"]
    prop = [r for r in rows if r["row_kind"] == "proposed_not_acquired"]
    check("every acquired manifest document has exactly one row (7 eCFR + FR)",
          sorted(r["baseline_doc"] for r in cb) + sorted(r["instrument_id"] for r in fr) == sorted(k for k in manifest if k.startswith("ecfr:")) + sorted(k for k in manifest if k.startswith("fr:"))
          and len(cb) + len(fr) == len(manifest))
    check("current_baseline only on acquired eCFR rows",
          all((r["current_or_future"] == "current_baseline") == (r["row_kind"] == "current_baseline") for r in rows)
          and all(r["baseline_doc"].startswith("ecfr:") and r["baseline_doc"] in manifest for r in cb))
    check("FR rows: unique document id and distinct instrument label",
          len({r["instrument_id"] for r in fr}) == len(fr) and len({r["instrument_label"] for r in fr}) == len(fr)
          and all(r["instrument_label"] == ("FN-" if r["row_kind"] == "notice" else "FI-") + manifest[r["instrument_id"]]["document_number"] for r in fr))
    check("source-class separation",
          all(r["source_class"] == "regulation" for r in cb) and all(r["source_class"] == "rulemaking_candidate" for r in fr)
          and all(r["source_class"].startswith("delta_comparison") for r in cmp_)
          and all(r["source_class"] == "regulation (proposed; not acquired)" for r in prop))
    rk = {r["doc_key"]: dict(r) for r in con.execute("SELECT * FROM rulemaking")}
    bad_life = [r["row_id"] for r in fr if lifecycle(rk[r["instrument_id"]], AS_OF)["state"] != r["lifecycle_state"]]
    check("lifecycle labels equal producer lifecycle() at as-of 2026-10-29", not bad_life, ", ".join(bad_life))
    check("no proposal labelled current or final; notices outside future scope",
          all(not r["lifecycle_state"].startswith(("final", "current")) for r in fr + cmp_ if r["document_type"] == "Proposed Rule")
          and all(r["current_or_future"] == "not_in_future_scope_notice" for r in fr if r["row_kind"] == "notice")
          and all(r["current_or_future"] == "future_instrument" for r in fr if r["row_kind"] == "future_instrument"))
    fut = {r["instrument_id"] for r in fr if r["row_kind"] == "future_instrument"}
    check("each comparison names exactly one future instrument (no merged instruments, no notices)",
          all(r["instrument_id"] in fut and ";" not in r["instrument_id"] and r["row_id"].count("fr:") == 1 for r in cmp_))
    check("comparison status vocabulary and non-empty gap reason / evidence pointer",
          all(r["comparison_status"] in STATUSES and r["coverage_or_gap_reason"] and r["evidence_pointer"] for r in cmp_))

    # citation pointers and paragraph assertions re-derived from the index
    docid = {r["doc_key"]: r["id"] for r in con.execute("SELECT id, doc_key FROM source_document")}
    locs = {}
    bad = []
    for r in cmp_:
        b = r["baseline_doc"]
        if r["comparison_status"] == "baseline_not_acquired":
            if b in manifest or r["candidate_baseline_paragraph_locators"] or r["candidate_baseline_section_locators"]:
                bad.append(r["row_id"])
            continue
        if b not in manifest:
            bad.append(r["row_id"]); continue
        if b not in locs:
            locs[b] = {x[0] for x in con.execute("SELECT locator FROM provision WHERE doc_id=?", (docid[b],))}
        secs = [s for s in r["candidate_baseline_section_locators"].split("; ") if s and not s.startswith("…")]
        paras = [p for p in r["candidate_baseline_paragraph_locators"].split("; ") if p]
        if any(s not in locs[b] for s in secs + paras):
            bad.append(r["row_id"]); continue
        if paras and r["comparison_status"] != "provision_level_ready":
            bad.append(r["row_id"]); continue
        if r["comparison_status"] == "provision_level_ready" and not paras:
            bad.append(r["row_id"]); continue
        attributed = set()
        for x in con.execute("""SELECT r.dst_citation, p.explicit_paragraphs FROM relationship r JOIN provision p ON p.id=r.src_provision_id
                                JOIN provision dp ON dp.id=r.dst_provision_id WHERE p.doc_id=? AND r.rel_type='amends' AND dp.doc_id=?""",
                             (docid[r["instrument_id"]], docid[b])):
            ep = json.loads(x[1]) if x[1] else {}
            if ep.get("status") == "attributed":
                attributed |= {x[0].split()[-1] + path for path in ep.get("paths", [])}
        if any(p not in attributed for p in paras):
            bad.append(r["row_id"])
        ptr_ids = [int(t.split()[2]) for t in r["evidence_pointer"].split("relationship id ")[1:] for t in ["x y " + t] if t.split()[2].isdigit()]
        for rid in ptr_ids:
            src = con.execute("SELECT p.doc_id FROM relationship r JOIN provision p ON p.id=r.src_provision_id WHERE r.id=?", (rid,)).fetchone()
            if not src or src[0] != docid[r["instrument_id"]]:
                bad.append(r["row_id"])
    check("citation pointers resolve; paragraph locators only where the instruction names them and the baseline has them",
          not bad, ", ".join(sorted(set(bad))[:10]))

    check("proposed Tranche A/B rows: 15, not acquired, blocker names authorization and rights, no hash, not in manifest",
          len(prop) == 15 and all(r["acquisition_status"] == "not_acquired" and "authorization" in r["blocker"] and "rights" in r["blocker"]
                                  and not r["sha256"] and r["decision_note_item_id"] and f"ecfr:{r['row_id'][5:]}" not in manifest for r in prop))
    restricted = ("fasb", "accounting", "asc ", "reporting form", "call report", "guidance", "state_", "state law")
    check("no accounting/FASB/reporting/guidance/state row marked acquired",
          all(not any(t in (r["source_class"] + " " + r["jurisdiction"]).lower() for t in restricted) or r["acquisition_status"] == "not_acquired"
              for r in rows) and all(r["jurisdiction"] == "US-federal" for r in rows))
    check("no row claims authorization, permission or gate approval",
          all(not r["rights_status"].lower().startswith(("permitted", "authorized")) for r in rows)
          and "no gate approved" in o["status"]
          and not any(v.lower() in ("authorized", "permitted", "approved") for r in rows for v in r.values()))
    con.close()

    for name, ok, detail in results:
        print(("PASS " if ok else "FAIL ") + name + (f" [{detail}]" if detail and not ok else ""))
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
