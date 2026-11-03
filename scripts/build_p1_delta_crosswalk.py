"""S7-P1-51: offline current-to-future delta-readiness crosswalk (schema p1-delta-readiness-crosswalk-0.1).

Read-only over the corpus manifest, index 49 (sqlite mode=ro), the S7-P1-50 inventory, the S7-P1-50 federal
authorisation packet and the C3 part census. Writes only the two output paths given. No network.

    python3 -I scripts/build_p1_delta_crosswalk.py OUT.json OUT.csv
"""
import csv, hashlib, json, sqlite3, subprocess, sys
from pathlib import Path

HEAD = "99f3274c4f5f828eefe348bd7f33add771ae2ec8"
REPO = Path(__file__).resolve().parents[1]
CORPUS = Path("/home/arbab/projects/regintel-corpus/phase1")
IDX = Path("/home/arbab/projects/regintel-corpus/phase1-index/regintel_p1_state_source_evidence_49.sqlite")
OB = Path("/mnt/c/Users/arbab/OneDrive/Documents/AI-OS/projects/velodyn/Velodyn_BankOS/claude/s7-regintel-rag")
INV = OB / "2026-11-02_S7_P1_COVERAGE_RECONCILIATION_50_INVENTORY.json"
DELIV = OB / "2026-11-02_S7_P1_COVERAGE_RECONCILIATION_50_DELIVERY.md"
PACKET = OB / "2026-11-02_S7_P1_FEDERAL_ACQUISITION_AUTHORIZATION_PACKET_50.json"
CENSUS = Path("/mnt/c/Users/arbab/OneDrive/Documents/AI-OS/projects/velodyn/Velodyn_BankOS/ChatGPT/"
              "2026-10-24_204204_EDT_Codex_Three_Sessions/C3-RegIntel-RAG/2026-10-26_PHASE1_US_RULES/FEDERAL_CFR_PART_CENSUS.csv")
SCHEMA = "p1-delta-readiness-crosswalk-0.1"
REQUIRED = ["topic_id", "source_class", "jurisdiction", "authority", "legal_entity_scope", "locator", "source_url",
            "acquisition_status", "parsing_indexing_status", "rights_status", "provenance_reference", "publication_date",
            "effective_date", "current_or_future", "coverage_or_gap_reason", "evidence_pointer", "sha256", "last_checked",
            "lifecycle_state", "applicability_status", "legal_validity"]
EXTRA = ["row_id", "row_kind", "instrument_id", "instrument_label", "document_type", "baseline_doc", "comparison_status",
         "candidate_baseline_section_locators", "candidate_baseline_paragraph_locators", "decision_note_item_id", "blocker"]
FIELDS = EXTRA + REQUIRED
# provision_level_ready: >=1 instruction-named paragraph present in the baseline.
# section_context_only: target sections (explicit amends or numeric range) or cited sections resolve, but no instruction-named
#   paragraph is present in the baseline. unresolved_ambiguous_mapping: amendment evidence for the part exists but no target
#   section resolves. baseline_not_acquired: the part is named but no baseline for it is acquired.
STATUSES = ("provision_level_ready", "section_context_only", "baseline_not_acquired", "unresolved_ambiguous_mapping")
IDX_NAME = IDX.name


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def j(items):
    return "; ".join(str(x) for x in items)


def row(**kw):
    r = {f: "" for f in FIELDS}
    r.update({k: ("" if v is None else str(v)) for k, v in kw.items()})
    r["applicability_status"], r["legal_validity"] = "not_assessed", "not_determined"
    return r


def main(out_json, out_csv):
    manifest = json.loads((CORPUS / "manifest.json").read_text())["documents"]
    inv = {r["row_id"]: r for r in json.loads(INV.read_text())["rows"]}
    packet = json.loads(PACKET.read_text())
    con = sqlite3.connect(f"file:{IDX}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = lambda s, *a: con.execute(s, a).fetchall()

    docid = {r["doc_key"]: r["id"] for r in q("SELECT id, doc_key FROM source_document")}
    topics = {}
    for r in q("SELECT d.doc_key k, pt.topic_id t FROM provision_topic pt JOIN provision p ON p.id=pt.provision_id "
               "JOIN source_document d ON d.id=p.doc_id GROUP BY 1,2"):
        topics.setdefault(r["k"], []).append(r["t"])
    baselines = sorted(k for k in manifest if k.startswith("ecfr:"))
    base_part = {tuple(k.split(":")[1:]): k for k in baselines}            # ("12","3") -> ecfr:12:3
    base_secs = {k: {r["section_no"]: r["locator"] for r in q("SELECT section_no, locator FROM provision WHERE doc_id=? AND kind='section'", docid[k])}
                 for k in baselines}
    base_locs = {k: {r["locator"] for r in q("SELECT locator FROM provision WHERE doc_id=?", docid[k])} for k in baselines}

    def common(key):
        s = inv[f"SRC-{key}"]
        return dict(source_class=s["source_class"], jurisdiction=s["jurisdiction"], authority=s["authority"],
                    legal_entity_scope=s["legal_entity_scope"], source_url=s["source_url"],
                    rights_status=s["rights_status"], sha256=s["sha256"], last_checked=s["last_checked"],
                    publication_date=s["publication_date"], effective_date=s["effective_date"], lifecycle_state=s["lifecycle_state"],
                    acquisition_status=s["acquisition_status"], parsing_indexing_status=s["parsing_indexing_status"],
                    provenance_reference=s["provenance_reference"])

    rows = []
    # --- current baselines (the only current_baseline rows) ---
    for k in baselines:
        rows.append(row(row_id=f"CB-{k}", row_kind="current_baseline", baseline_doc=k, locator=manifest[k]["citation"],
                        topic_id=j(f"s7:{t}" for t in sorted(topics.get(k, []))) or "none_assigned",
                        current_or_future="current_baseline", **common(k),
                        coverage_or_gap_reason="acquired eCFR part; publisher snapshot; later publications and operative effect not checked",
                        evidence_pointer=f"{IDX_NAME} source_document doc_key={k}; S7-P1-50 SRC-{k}"))

    # --- Federal Register instruments, one row each, plus per-part comparisons ---
    for k in sorted(x for x in manifest if x.startswith("fr:")):
        d = manifest[k]
        num = d["document_number"]
        dtype = d["document_type"]
        c = common(k)
        is_notice = dtype == "Notice"
        named = sorted({(str(x["title"]), x["part"]) for x in d.get("cfr_references", []) if x.get("part")},
                       key=lambda tp: (int(tp[0]), int(tp[1]) if tp[1].isdigit() else 0, tp[1]))
        label = f"FN-{num}" if is_notice else f"FI-{num}"
        rows.append(row(row_id=f"FR-{k}", row_kind="notice" if is_notice else "future_instrument", instrument_id=k,
                        instrument_label=label, document_type=dtype,
                        topic_id=j(f"s7:{t}" for t in sorted(topics.get(k, []))) or "none_assigned",
                        locator=f"FR Doc. {num}; CFR parts in publisher metadata: " + (j(f"{t}:{p}" for t, p in named) or "none"),
                        current_or_future="not_in_future_scope_notice" if is_notice else "future_instrument", **c,
                        coverage_or_gap_reason=("Notice: not a rulemaking state, outside future scope; no comparison made" if is_notice else
                                                "separate instrument record; never merged with a baseline or another instrument; disposition unreviewed"),
                        evidence_pointer=f"{IDX_NAME} rulemaking doc_key={k}; S7-P1-50 SRC-{k}"))
        if is_notice:
            continue
        rel = q("""SELECT r.id, r.rel_type, r.dst_citation, r.dst_provision_id, r.source_locator, p.explicit_paragraphs,
                          dp.locator dst_locator, dd.doc_key dst_doc
                   FROM relationship r JOIN provision p ON p.id=r.src_provision_id
                   LEFT JOIN provision dp ON dp.id=r.dst_provision_id LEFT JOIN source_document dd ON dd.id=dp.doc_id
                   WHERE p.doc_id=? AND r.rel_type IN ('amends','amends_candidate','references')""", docid[k])
        ranges = q("""SELECT a.id, a.title_no, a.part_no, a.lo_section, a.hi_section, a.lo_minor, a.hi_minor, a.status, p.locator
                      FROM amendment_range a JOIN provision p ON p.id=a.src_provision_id WHERE p.doc_id=?""", docid[k])

        def part_of(cit):
            parts = cit.split()
            if len(parts) < 3 or parts[1] != "CFR":
                return None
            t = parts[0]
            p = parts[3] if parts[2] == "Part" else parts[2].split(".")[0]
            return (t, p)

        touched = set(named) | {part_of(r["dst_citation"]) for r in rel if r["rel_type"] != "references" and part_of(r["dst_citation"])} \
                  | {(a["title_no"], a["part_no"]) for a in ranges}
        ref_parts = {part_of(r["dst_citation"]) for r in rel if r["rel_type"] == "references" and part_of(r["dst_citation"])}
        for tp in sorted(touched | (ref_parts & set(base_part)), key=lambda tp: (int(tp[0]), int(tp[1]) if tp[1].isdigit() else 0, tp[1])):
            b = base_part.get(tp)
            cid = f"CMP-{k}-{tp[0]}:{tp[1]}"
            base = dict(row_kind="comparison", instrument_id=k, instrument_label=label, document_type=dtype,
                        topic_id=j(f"s7:{t}" for t in sorted(topics.get(k, []))) or "none_assigned",
                        source_class="delta_comparison (one instrument vs one baseline part)", jurisdiction="US-federal",
                        authority=c["authority"], legal_entity_scope="not_assessed", source_url=c["source_url"],
                        rights_status=c["rights_status"], publication_date=c["publication_date"], effective_date=c["effective_date"],
                        lifecycle_state=c["lifecycle_state"], last_checked=c["last_checked"],
                        current_or_future="delta_comparison_future_instrument_vs_current_baseline",
                        provenance_reference=f"{IDX_NAME} relationship/amendment_range rows from {k}")
            if not b:
                rows.append(row(row_id=cid, baseline_doc=f"{tp[0]} CFR Part {tp[1]} (not acquired)", comparison_status="baseline_not_acquired",
                                locator=f"{tp[0]} CFR Part {tp[1]}", acquisition_status="baseline_not_acquired",
                                parsing_indexing_status="instrument indexed; baseline absent", sha256=f"instrument {c['sha256']}",
                                coverage_or_gap_reason=f"{tp[0]} CFR Part {tp[1]} is named by this instrument but no current baseline for it is acquired",
                                evidence_pointer=f"manifest {k} cfr_references" + ("" if tp in set(named) else f"; {IDX_NAME} amends targets"),
                                **base))
                continue
            in_part = lambda r: part_of(r["dst_citation"]) == tp
            amends = [r for r in rel if r["rel_type"] == "amends" and in_part(r)]
            cands = [r for r in rel if r["rel_type"] == "amends_candidate" and in_part(r)]
            refs = [r for r in rel if r["rel_type"] == "references" and in_part(r)]
            rgs = [a for a in ranges if (a["title_no"], a["part_no"]) == tp]
            sec_amends = [r for r in amends if "Part" not in r["dst_citation"]]
            resolved = [r for r in sec_amends if r["dst_doc"] == b]
            unresolved_secs = sorted({r["dst_citation"] for r in sec_amends if r["dst_doc"] != b})
            part_level = [r for r in amends if "Part" in r["dst_citation"]]
            sec_locs = sorted({r["dst_locator"] for r in resolved})
            range_secs = []
            for a in rgs:
                if a["status"] == "numeric_range" and a["lo_minor"] is not None:
                    inr = [s for s in base_secs[b] if s.split(".")[-1].isdigit() and a["lo_minor"] <= int(s.split(".")[-1]) <= a["hi_minor"]]
                    range_secs += [base_secs[b][s] for s in inr]
            sec_locs = sorted(set(sec_locs) | set(range_secs))
            paras, para_missing = set(), set()
            for r in resolved:
                ep = json.loads(r["explicit_paragraphs"]) if r["explicit_paragraphs"] else {}
                if ep.get("status") == "attributed":
                    for path in ep.get("paths", []):
                        sec = r["dst_citation"].split()[-1]          # "12 CFR 3.12" -> "3.12"; paragraph locators are "3.12(a)(1)"
                        loc = f"{sec}{path}"
                        (paras if loc in base_locs[b] else para_missing).add(loc)
            ev = j([f"relationship id {r['id']} ({r['rel_type']}, {r['source_locator']} -> {r['dst_citation']})" for r in (resolved or amends or cands)[:8]]
                   + [f"amendment_range id {a['id']} ({a['lo_section']}-{a['hi_section']})" for a in rgs])
            if sec_locs:
                # provision_level_ready needs a paragraph the instruction names explicitly AND that exists in the baseline;
                # a resolved section without such a paragraph is section context only (no paragraph support inferred).
                status = "provision_level_ready" if paras else "section_context_only"
                why = (f"{len(resolved)} explicit amends relationship(s) and {len(rgs)} numeric range(s) resolve to baseline sections in {b}"
                       + (f"; {len(paras)} paragraph locator(s) named explicitly by the instruction and present in the baseline" if paras else
                          "; no explicitly named paragraph is present in the baseline, so the comparison is section context only")
                       + (f"; explicitly named paragraph(s) absent from the baseline: {j(sorted(para_missing))}" if para_missing else "")
                       + (f"; additional target section(s) not in the baseline: {j(unresolved_secs)}" if unresolved_secs else "")
                       + (f"; {len(cands)} unresolved candidate(s) also present" if cands else ""))
            elif sec_amends or cands or part_level or rgs:
                status = "unresolved_ambiguous_mapping"
                why = j(x for x in [
                    f"target section(s) not present in the acquired baseline (new or absent; not inferred): {j(unresolved_secs)}" if unresolved_secs else "",
                    f"{len(cands)} amendment candidate(s) could not be paired explicitly" if cands else "",
                    f"{len(part_level)} part-level instruction(s) only (for example an authority citation), no section target" if part_level else "",
                    "numeric range does not intersect baseline sections" if rgs else ""] if x)
                ev = ev or f"{IDX_NAME} relationship rows for {k}"
            else:
                status = "section_context_only"
                ctx = sorted({r["dst_locator"] for r in refs if r["dst_doc"] == b})
                why = ("instrument names the part (publisher cfr_references) or cites its sections, but has no amendment instruction targeting it; "
                       + (f"{len(ctx)} cited baseline section(s), context only" if ctx else "no cited baseline section resolved"))
                sec_locs = ctx
                ev = (j([f"relationship id {r['id']} (references -> {r['dst_citation']})" for r in refs[:8]])
                      or f"manifest {k} cfr_references names {tp[0]}:{tp[1]}")
            rows.append(row(row_id=cid, baseline_doc=b, comparison_status=status, locator=f"{tp[0]} CFR Part {tp[1]}",
                            candidate_baseline_section_locators=j(sec_locs[:60]) + (f"; … {len(sec_locs) - 60} more" if len(sec_locs) > 60 else ""),
                            candidate_baseline_paragraph_locators=j(sorted(paras)),
                            acquisition_status="instrument and baseline acquired",
                            parsing_indexing_status="both indexed (index 49)",
                            sha256=f"instrument {c['sha256']}; baseline {manifest[b]['asset']['sha256']}",
                            coverage_or_gap_reason=why, evidence_pointer=f"{IDX_NAME}: {ev}", **base))

    # --- proposed Tranche A/B parts: non-acquired readiness rows only ---
    for it in packet["items"]:
        if it["tranche"] not in ("A", "B"):
            continue
        tp = f"12:{it['part']}"
        rows.append(row(row_id=f"PROP-{tp}", row_kind="proposed_not_acquired", decision_note_item_id=it["item_id"],
                        topic_id=j(f"c3:{x}" for x in it["crosswalk"]["coverage_inventory_rows"]),
                        source_class="regulation (proposed; not acquired)", jurisdiction="US-federal", authority=it["authority"],
                        legal_entity_scope="not_recorded_in_source_metadata", locator=it["locator"],
                        source_url=it["census_source_url"], acquisition_status="not_acquired",
                        parsing_indexing_status="not_parsed; not_indexed", rights_status="unresolved; operator rights decision required",
                        provenance_reference=f"S7-P1-50 authorisation packet item {it['item_id']}; C3 census {it['census_row']}",
                        current_or_future="not_acquired_proposed", lifecycle_state="",
                        blocker="retrieval requires an operator acquisition authorization and a rights decision (packet DR-1, DR-2); not fetched",
                        coverage_or_gap_reason="proposed baseline only; no current or future evidence exists; comparisons to it are baseline_not_acquired",
                        evidence_pointer=f"{PACKET.name} item {it['item_id']}"))

    ids = [r["row_id"] for r in rows]
    assert len(ids) == len(set(ids))
    prov = {"head": HEAD, "head_verified": subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() == HEAD,
            "manifest": {"path": str(CORPUS / "manifest.json"), "sha256": sha(CORPUS / "manifest.json"),
                         "expected": "94e6e22216c7ebf48d3d1d4ea5684af5b9d01eb16c8f10452b1da70f02f141a3"},
            "index": {"path": str(IDX), "sha256": sha(IDX), "opened": "read-only"},
            "s7_p1_50_delivery": {"path": str(DELIV), "sha256": sha(DELIV)},
            "s7_p1_50_inventory": {"path": str(INV), "sha256": sha(INV)},
            "s7_p1_50_authorization_packet": {"path": str(PACKET), "sha256": sha(PACKET)},
            "c3_federal_cfr_part_census": {"path": str(CENSUS), "sha256": sha(CENSUS)},
            "builder": {"path": str(Path(__file__).resolve()), "sha256": sha(__file__), "committed": False}}
    counts = {"rows": len(rows), "by_kind": {}, "comparison_status": {}}
    for r in rows:
        counts["by_kind"][r["row_kind"]] = counts["by_kind"].get(r["row_kind"], 0) + 1
        if r["comparison_status"]:
            counts["comparison_status"][r["comparison_status"]] = counts["comparison_status"].get(r["comparison_status"], 0) + 1
    out = {"schema": SCHEMA, "status": "producer-reported; offline; not independently verified; no gate approved; no authorization, rights, "
                                     "applicability, legal validity, operative effect or complete coverage is claimed",
           "fields": FIELDS, "required_fields": REQUIRED, "comparison_statuses": list(STATUSES),
           "normalization": "every value is a string; lists are joined with '; '; absent values are ''", "provenance": prov,
           "counts": counts, "rows": rows}
    Path(out_json).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(counts))


if __name__ == "__main__":
    main(*sys.argv[1:3])
