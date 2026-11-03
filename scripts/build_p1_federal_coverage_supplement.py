"""S7-P1-51: offline federal regulatory coverage reconciliation supplement (schema p1-federal-coverage-supplement-0.1).

Read-only: corpus manifest and held assets (hash-checked), index 49 (sqlite mode=ro), C3 census and inventory, S7-P1-50
inventory. Writes only OUT.json. No network.

    python3 -I scripts/build_p1_federal_coverage_supplement.py OUT.json
"""
import collections, csv, hashlib, json, sqlite3, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from regintel_p1.search import lifecycle  # producer label function at HEAD, unchanged

HEAD = "99f3274c4f5f828eefe348bd7f33add771ae2ec8"
AS_OF = "2026-10-29"
CORPUS = Path("/home/arbab/projects/regintel-corpus/phase1")
IDX = Path("/home/arbab/projects/regintel-corpus/phase1-index/regintel_p1_state_source_evidence_49.sqlite")
C3 = Path("/mnt/c/Users/arbab/OneDrive/Documents/AI-OS/projects/velodyn/Velodyn_BankOS/ChatGPT/"
          "2026-10-24_204204_EDT_Codex_Three_Sessions/C3-RegIntel-RAG/2026-10-26_PHASE1_US_RULES")
OB = Path("/mnt/c/Users/arbab/OneDrive/Documents/AI-OS/projects/velodyn/Velodyn_BankOS/claude/s7-regintel-rag")
INV50 = OB / "2026-11-02_S7_P1_COVERAGE_RECONCILIATION_50_INVENTORY.json"
SCHEMA = "p1-federal-coverage-supplement-0.1"
NR = "not_recorded"
# C3 inventory rows whose candidate_locator names a CFR chapter (taken from PHASE1_SOURCE_INVENTORY.csv locators)
C3_CHAPTER_ROW = {("12", "I"): "P1-095", ("12", "II"): "P1-096", ("12", "III"): "P1-097", ("12", "X"): "P1-098",
                  ("31", "X"): "P1-111", ("31", "V"): "P1-112", ("24", "VIII"): "P1-115", ("32", "I"): "P1-116",
                  ("28", "I"): "P1-117", ("17", "II"): "P1-118", ("17", "I"): "P1-119", ("16", "I"): "P1-120"}
# C3 inventory rows whose candidate_locator names specific parts (exact locator text in the C3 CSV)
C3_PART_ROW = {("12", "3"): "P1-099", ("12", "217"): "P1-100", ("12", "324"): "P1-101", ("12", "252"): "P1-102",
               ("12", "327"): "P1-105", ("12", "328"): "P1-105", ("12", "330"): "P1-105",
               ("31", "1010"): "P1-111", ("31", "1020"): "P1-111", ("32", "232"): "P1-116",
               ("28", "35"): "P1-117", ("28", "36"): "P1-117"}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def val(v):
    return NR if v in (None, "", []) else v


def main(out):
    man_bytes = (CORPUS / "manifest.json").read_bytes()
    manifest = json.loads(man_bytes)["documents"]
    census = {(r["title"], r["part"]): r for r in csv.DictReader(open(C3 / "FEDERAL_CFR_PART_CENSUS.csv", newline=""))}
    c3_ids = {r["id"]: r for r in csv.DictReader(open(C3 / "PHASE1_SOURCE_INVENTORY.csv", newline=""))}
    inv50 = {r["row_id"]: r for r in json.loads(INV50.read_text())["rows"]}
    con = sqlite3.connect(f"file:{IDX}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = lambda s, *a: con.execute(s, a).fetchall()
    docid = {r["doc_key"]: r["id"] for r in q("SELECT id, doc_key FROM source_document")}
    rk = {r["doc_key"]: dict(r) for r in q("SELECT * FROM rulemaking")}

    def c3_for(title, part):
        c = census.get((title, part))
        ids = []
        if c and c["reserved"] == "false" and (title, c["chapter"]) in C3_CHAPTER_ROW:
            ids.append(C3_CHAPTER_ROW[(title, c["chapter"])])
        if (title, part) in C3_PART_ROW:
            ids.append(C3_PART_ROW[(title, part)])
        if not c:
            gap = "part not in C3 census (2026-10-23)"
        elif c["reserved"] == "true":
            gap = "part reserved in C3 census"
        elif not ids:
            gap = f"no C3 inventory row names {title} CFR chapter {c['chapter']}"
        else:
            gap = "none"
        return {"census_row": c["id"] if c else NR, "census_chapter": c["chapter"] if c else NR,
                "census_reserved": (c["reserved"] == "true") if c else NR, "c3_inventory_ids": sorted(set(ids)), "gap": gap}

    def part_of(cit):
        t = cit.split()
        if len(t) < 3 or t[1] != "CFR":
            return None
        return (t[0], t[3]) if t[2] == "Part" and len(t) > 3 else (t[0], t[2].split(".")[0])

    records = []
    # ---------- eCFR current baselines ----------
    for k in sorted(x for x in manifest if x.startswith("ecfr:")):
        d = manifest[k]
        _, t, p = k.split(":")
        a, ca = d["asset"], d["catalog_asset"]
        secs = [r["section_no"] for r in q("SELECT section_no FROM provision WHERE doc_id=? AND kind='section' ORDER BY ordinal", docid[k])]
        kinds = dict(q("SELECT kind, count(*) FROM provision WHERE doc_id=? GROUP BY 1", docid[k]))
        cw = c3_for(t, p)
        census_secs = census[(t, p)]["sections"] if (t, p) in census else NR
        records.append({
            "record_id": f"CB-{k}", "record_kind": "current_baseline", "manifest_key": k,
            "current_or_future": "current_baseline", "source_class": d["source_class"], "publisher": d["publisher"],
            "cfr_title": t, "cfr_part": p, "citation": d["citation"], "title": d["title"],
            "publisher_issue_date": val(d.get("publisher_issue_date")), "publisher_up_to_date_as_of": val(d.get("publisher_up_to_date_as_of")),
            "source_url": d["source_url"],
            "asset": {"path": a["path"], "sha256": a["sha256"], "bytes": a["bytes"], "retrieved_at": a["retrieved_at"],
                      "sha256_verified_offline": sha(CORPUS / a["path"]) == a["sha256"], "content_type": val(a.get("content_type")),
                      "etag": val(a.get("etag")), "last_modified": val(a.get("last_modified"))},
            "catalog_asset": {"path": ca["path"], "sha256": ca["sha256"], "sha256_verified_offline": sha(CORPUS / ca["path"]) == ca["sha256"]},
            "rule_status": d.get("rule_status"),
            "section_coverage": {"indexed_section_rows": len(secs), "first_section": secs[0] if secs else NR,
                                 "last_section": secs[-1] if secs else NR, "provision_kinds": kinds,
                                 "c3_census_section_count": census_secs,
                                 "basis": "index 49 provision rows of kind 'section' for this document; census count is C3 publisher metadata. "
                                          "The two counts are recorded side by side, not reconciled into a completeness claim"},
            "c3_crosswalk": cw, "s7_p1_50_c3_ids": inv50[f"SRC-{k}"]["c3_inventory_ids"],
            "gap": cw["gap"],
            "lifecycle_state": "current_baseline_publisher_snapshot (publisher snapshot; operative effect not determined)",
            "applicability_status": "not_assessed", "legal_validity": "not_determined"})

    # ---------- Federal Register documents ----------
    for k in sorted(x for x in manifest if x.startswith("fr:")):
        d = manifest[k]
        a, ma = d["asset"], d["metadata_asset"]
        meta_ok = sha(CORPUS / ma["path"]) == ma["sha256"]
        meta = json.loads((CORPUS / ma["path"]).read_text()) if meta_ok else {}
        agencies = [x.get("name") or x.get("raw_name") for x in meta.get("agencies", [])] or NR
        compare = {}
        for mf, pf in (("document_number", "document_number"), ("type", "document_type"), ("title", "title"),
                       ("publication_date", "publication_date"), ("effective_on", "effective_on"),
                       ("comments_close_on", "comments_close_on"), ("html_url", "source_url")):
            compare[pf] = "match" if meta.get(mf) == d.get(pf) else {"metadata_asset": meta.get(mf), "manifest": d.get(pf)}
        life = lifecycle(rk[k], AS_OF)
        dtype = d["document_type"]
        cof = ("not_in_future_scope_notice" if dtype == "Notice" else "future_instrument")
        # mappings
        maps = collections.OrderedDict()
        def add(cit, cls, basis, ptr):
            m = maps.setdefault((cit, cls), {"citation": cit, "classification": cls, "basis": set(), "evidence": []})
            m["basis"].add(basis)
            if len(m["evidence"]) < 5:
                m["evidence"].append(ptr)
        for c in d.get("cfr_references", []):
            if c.get("part"):
                add(f"{c['title']} CFR Part {c['part']}", "explicit", "publisher_metadata_cfr_references", f"manifest {k} cfr_references")
        for r in q("""SELECT r.id, r.rel_type, r.dst_citation, r.basis, r.dst_provision_id, p.pairing_status FROM relationship r
                      JOIN provision p ON p.id=r.src_provision_id WHERE p.doc_id=?""", docid[k]):
            ptr = f"index relationship id {r['id']}"
            if r["rel_type"] == "amends":
                add(r["dst_citation"], "explicit", f"parsed_amendment_target[{r['pairing_status']}]", ptr)
            elif r["rel_type"] == "amends_candidate":
                add(r["dst_citation"], "unresolved", f"unresolved_candidate[{r['pairing_status']}]", ptr)
            elif r["rel_type"] == "references":
                add(r["dst_citation"], "reference", "parsed_reference_not_an_amendment_target", ptr)
        for r in q("""SELECT a.id, a.title_no, a.part_no, a.lo_section, a.hi_section, a.status FROM amendment_range a
                      JOIN provision p ON p.id=a.src_provision_id WHERE p.doc_id=?""", docid[k]):
            add(f"{r['title_no']} CFR {r['lo_section']} through {r['hi_section']}",
                "explicit" if r["status"] == "numeric_range" else "unresolved", f"parsed_amendment_range[{r['status']}]",
                f"index amendment_range id {r['id']}")
        if not maps:
            maps[("none", "not_applicable")] = {"citation": "none", "classification": "not_applicable",
                                               "basis": {"no CFR part in publisher metadata and no parsed target or reference"},
                                               "evidence": [f"manifest {k} cfr_references empty; index has no relationship rows for {k}"]}
        mappings = []
        for m in maps.values():
            pt = part_of(m["citation"]) if m["citation"] != "none" else None
            cw = c3_for(*pt) if pt else None
            mappings.append({"citation": m["citation"], "classification": m["classification"], "basis": sorted(m["basis"]),
                             "evidence": m["evidence"], "cfr_part": f"{pt[0]}:{pt[1]}" if pt else "not_applicable",
                             "baseline_acquired": (f"ecfr:{pt[0]}:{pt[1]}" in manifest) if pt else "not_applicable",
                             "c3_crosswalk": cw if cw else "not_applicable"})
        kinds = dict(q("SELECT kind, count(*) FROM provision WHERE doc_id=? GROUP BY 1", docid[k]))
        pairing = dict(q("SELECT pairing_status, count(*) FROM provision WHERE doc_id=? AND kind='amendment' GROUP BY 1", docid[k]))
        material = {"amendment_rows": kinds.get("amendment", 0),
                    "amendment_pairing_status": pairing,
                    "carried_text_rows": sum(v for s, v in pairing.items() if s and s.startswith(("carried_text", "explicit_loose", "appendix_text"))),
                    "non_amendment_rows": {kk: v for kk, v in kinds.items() if kk != "amendment"}}
        # only explicit mappings drive the C3 crosswalk; references and unresolved candidates are listed but are not coverage
        parts_map = [m for m in mappings if m["c3_crosswalk"] != "not_applicable" and m["classification"] == "explicit"]
        mapped = [m for m in parts_map if m["c3_crosswalk"]["c3_inventory_ids"]]
        cls_parts = {m["cfr_part"] for m in parts_map if m["classification"] == "explicit"}
        if dtype == "Notice":
            gap = "Notice: no explicit CFR part in publisher metadata or parsed targets; no C3 category" if not parts_map else "none"
        elif not parts_map:
            gap = "no explicit CFR part mapped"
        elif len(mapped) < len(parts_map):
            unm = sorted({f"{m['cfr_part']} ({m['c3_crosswalk']['gap']})" for m in parts_map if not m["c3_crosswalk"]["c3_inventory_ids"]})
            gap = "partially mapped: " + "; ".join(unm)
        else:
            gap = "none"
        if dtype == "Notice" and not parts_map:
            c3_ids = []
        else:
            c3_ids = sorted({i for m in parts_map for i in m["c3_crosswalk"]["c3_inventory_ids"]})
        records.append({
            "record_id": f"FR-{k}", "record_kind": "notice" if dtype == "Notice" else "future_instrument", "manifest_key": k,
            "document_number": d["document_number"], "document_type": dtype, "title": d["title"],
            "agencies": agencies, "agencies_source": f"metadata_asset {ma['path']}" if meta_ok else "metadata asset hash mismatch",
            "publisher": d["publisher"], "publication_date": val(d.get("publication_date")),
            "effective_on": val(d.get("effective_on")), "effective_on_basis": "publisher metadata field; not proof of operative effect",
            "comments_close_on": val(d.get("comments_close_on")), "action": val(d.get("action")),
            "docket_ids": val(d.get("docket_ids")), "regulation_id_numbers": val(d.get("regulation_id_numbers")),
            "source_url": val(d.get("source_url")), "official_pdf_url": val(d.get("official_pdf_url")),
            "asset": {"path": a["path"], "url": a["url"], "sha256": a["sha256"], "bytes": a["bytes"], "retrieved_at": a["retrieved_at"],
                      "sha256_verified_offline": sha(CORPUS / a["path"]) == a["sha256"], "content_type": val(a.get("content_type")),
                      "last_modified": val(a.get("last_modified"))},
            "metadata_asset": {"path": ma["path"], "url": ma["url"], "sha256": ma["sha256"], "sha256_verified_offline": meta_ok,
                               "retrieved_at": ma["retrieved_at"]},
            "checked_at": val(d.get("checked_at")), "rule_status": d.get("rule_status"),
            "metadata_vs_manifest": compare,
            "current_or_future": cof, "lifecycle_state": life["state"], "lifecycle_caveat": life.get("caveat", ""),
            "cfr_mappings": mappings, "material": material,
            "c3_crosswalk_ids": c3_ids, "s7_p1_50_c3_ids": inv50[f"SRC-{k}"]["c3_inventory_ids"],
            "explicit_parts": sorted(cls_parts), "gap": gap,
            "applicability_status": "not_assessed", "legal_validity": "not_determined"})
    con.close()

    counts = {"records": len(records),
              "by_kind": dict(collections.Counter(r["record_kind"] for r in records)),
              "fr_by_document_type": dict(collections.Counter(r["document_type"] for r in records if r["manifest_key"].startswith("fr:"))),
              "fr_by_lifecycle": dict(sorted(collections.Counter(r["lifecycle_state"] for r in records if r["manifest_key"].startswith("fr:")).items())),
              "mappings_by_classification": dict(collections.Counter(m["classification"] for r in records for m in r.get("cfr_mappings", []))),
              "records_with_gap": sum(1 for r in records if r["gap"] != "none"),
              "records_fully_mapped_to_c3": sum(1 for r in records if r["gap"] == "none"),
              "metadata_vs_manifest_differences": sum(1 for r in records for v in r.get("metadata_vs_manifest", {}).values() if v != "match"),
              "c3_mapping_differs_from_s7_p1_50": sum(1 for r in records
                                                      if sorted(r.get("c3_crosswalk_ids", r["c3_crosswalk"]["c3_inventory_ids"] if "c3_crosswalk" in r else [])) != sorted(r["s7_p1_50_c3_ids"]))}
    prov = {"head": HEAD,
            "head_verified": subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() == HEAD,
            "manifest": {"path": str(CORPUS / "manifest.json"), "sha256": hashlib.sha256(man_bytes).hexdigest()},
            "index": {"path": str(IDX), "sha256": sha(IDX), "opened": "read-only"},
            "c3_census": {"path": str(C3 / "FEDERAL_CFR_PART_CENSUS.csv"), "sha256": sha(C3 / "FEDERAL_CFR_PART_CENSUS.csv")},
            "c3_inventory": {"path": str(C3 / "PHASE1_SOURCE_INVENTORY.csv"), "sha256": sha(C3 / "PHASE1_SOURCE_INVENTORY.csv")},
            "s7_p1_50_inventory": {"path": str(INV50), "sha256": sha(INV50)},
            "lifecycle_function": "regintel_p1/search.py lifecycle() at HEAD, as_of 2026-10-29",
            "builder": {"path": str(Path(__file__).resolve()), "sha256": sha(__file__), "committed": False}}
    o = {"schema": SCHEMA,
         "status": "producer-reported; offline; read-only; no gate approved; no independent verification of producer tests or evaluation; "
                   "no instrument is claimed current, final, operative, applicable or legally effective beyond producer lifecycle labels",
         "classification_vocabulary": {"explicit": "named in publisher cfr_references, or a parsed amendment target or numeric range",
                                       "reference": "a citation parsed as a reference, not an amendment target",
                                       "unresolved": "a parsed amendment candidate that could not be paired, or an ambiguous range",
                                       "not_applicable": "no CFR part named or parsed (for example a notice)"},
         "provenance": prov, "counts": counts, "records": records}
    Path(out).write_text(json.dumps(o, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(counts))


if __name__ == "__main__":
    main(sys.argv[1])
