"""Evaluation harness: retrieval hit rates per retriever, deterministic delta checks and status/refusal checks.

A retrieved provision is a hit when it matches any gold matcher:
  {"doc_key", "locator"}     the provision with that locator, or any provision beneath it
  {"doc_key", "section_no"}  any provision in that section
  {"doc_key"[, "kind", "section_no"]}  any provision in that document
Gold matchers are validated against the index first; a matcher that matches nothing is reported as invalid
rather than silently counted as a miss.
"""
import hashlib
import json
import re
from pathlib import Path
from collections import defaultdict

from .search import Index, parse_query_citations

RETRIEVERS = ("bm25", "topic", "hybrid")
CUTOFFS = (1, 3, 5, 10)


def matches(row, gold):
    if row["doc_key"] != gold["doc_key"]:
        return False
    if "locator" in gold:
        return row["locator"] == gold["locator"] or row["locator"].startswith(gold["locator"] + "(")
    if "kind" in gold and row["kind"] != gold["kind"]:
        return False
    if "section_no" in gold and row["section_no"] != gold["section_no"]:
        return False
    return True


def provision_rows(index, ids):
    if not ids:
        return {}
    q = f"""SELECT p.id, p.locator, p.kind, p.section_no, p.part_no, p.title_no, d.doc_key
            FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE p.id IN ({','.join('?' * len(ids))})"""
    return {r["id"]: r for r in index.conn.execute(q, list(ids))}


def gold_valid(index, golds):
    bad = []
    for g in golds:
        rows = index.conn.execute(
            "SELECT p.locator, p.kind, p.section_no, d.doc_key FROM provision p JOIN source_document d ON d.id=p.doc_id "
            "WHERE d.doc_key=?", (g["doc_key"],)).fetchall()
        if not any(matches(r, g) for r in rows):
            bad.append(g)
    return bad


def run_retrieval(index, items, k=10):
    per_item, agg = [], {r: defaultdict(lambda: defaultdict(list)) for r in RETRIEVERS}
    for item in items:
        invalid = gold_valid(index, item["gold"])
        record = {"id": item["id"], "category": item["category"], "query": item["query"], "invalid_gold": invalid,
                  "retrievers": {}}
        for name in RETRIEVERS:
            ranked = index.retrieve(item["query"], name, k, item.get("scope", "both"), item.get("parts"))
            rows = provision_rows(index, [pid for pid, _ in ranked])
            first = None
            for rank, (pid, _) in enumerate(ranked, 1):
                if any(matches(rows[pid], g) for g in item["gold"]):
                    first = rank
                    break
            top = [{"rank": i, "doc_key": rows[pid]["doc_key"], "locator": rows[pid]["locator"]}
                   for i, (pid, _) in enumerate(ranked[:5], 1)]
            extra = {}
            if item.get("all_results_in_parts"):
                allowed = {tuple(p.split(":")) for p in item["all_results_in_parts"]}
                extra["filter_respected"] = all((rows[pid]["title_no"], rows[pid]["part_no"]) in allowed for pid, _ in ranked)
            record["retrievers"][name] = {"first_hit_rank": first, "returned": len(ranked), "top5": top, **extra}
            for cat in (item["category"], "ALL"):
                a = agg[name][cat]
                a["rr"].append(1.0 / first if first else 0.0)
                for c in CUTOFFS:
                    a[f"hit@{c}"].append(1.0 if first and first <= c else 0.0)
        per_item.append(record)
    summary = {}
    for name in RETRIEVERS:
        summary[name] = {}
        for cat, a in agg[name].items():
            n = len(a["rr"])
            summary[name][cat] = {"n": n, "mrr": round(sum(a["rr"]) / n, 3),
                                  **{f"hit@{c}": round(sum(a[f"hit@{c}"]) / n, 3) for c in CUTOFFS}}
    return per_item, summary


def delta_classification_failures(out):
    """Contract p1-delta-provision-classification-1, checked on every evaluated delta (not a frozen expectation).

    Only matched_provision may be provision-level support; section and part context, unresolved candidates and
    references never are. Every listed instrument is classified exactly once, no proposal is labelled final or current,
    and every record keeps applicability not_assessed and legal validity not_determined.
    """
    from .search import CLASSIFICATIONS, DELTA_CONTRACT
    failures = []
    if out.get("delta_contract") != DELTA_CONTRACT:
        return [f"delta_contract {out.get('delta_contract')!r} != {DELTA_CONTRACT!r}"]
    recs = out.get("instrument_classification") or []
    if out.get("classification_status") != "classified":
        return [] if not recs and not out.get("provision_level_supported_instruments") else ["unclassified delta lists instruments"]
    keys = [r["doc_key"] for r in recs]
    if len(keys) != len(set(keys)):
        failures.append("an instrument is classified more than once")
    for r in recs:
        if r["classification"] not in CLASSIFICATIONS:
            failures.append(f"unknown classification {r['classification']}")
        if r["provision_level_support"] != (r["classification"] == "matched_provision"):
            failures.append(f"{r['doc_key']}: provision-level support from {r['classification']}")
        if r["provision_level_delta_match"] and not r["provision_level_support"]:
            failures.append(f"{r['doc_key']}: provision-level delta match without provision-level support")
        if r["document_type"] == "Proposed Rule" and (r["lifecycle_state"] or "").startswith(("final", "current")):
            failures.append(f"proposal {r['doc_key']} labelled {r['lifecycle_state']}")
        if r["legal_validity"] != "not_determined":
            failures.append(f"{r['doc_key']}: legal validity asserted")
        failures += [f"{r['doc_key']}: {x}" for x in _applicability_record_failures(r, "bank_profile" in out)]
        if r["future"]["scope"] != "future_instrument" or r["current"]["scope"] != "current_baseline":
            failures.append(f"{r['doc_key']}: current and future scopes merged")
    if not set(g["doc_key"] for g in out["instruments"]) <= set(keys):
        failures.append("a listed instrument has no classification")
    if out["provision_level_supported_instruments"] != [r["doc_key"] for r in recs if r["provision_level_support"]]:
        failures.append("provision_level_supported_instruments disagrees with the classification")
    if not recs:
        failures.append("no classification record (no_acquired_instrument must be explicit)")
    return failures


def _applicability_record_failures(rec, profiled):
    """Without a profile every record stays not_assessed; with one, an assessed status needs a verified, cited scope condition."""
    from .applicability import STATUSES
    st = rec["applicability_status"]
    if not profiled:
        return [] if st == "not_assessed" else [f"applicability {st} without a bank profile"]
    if st not in STATUSES:
        return [f"unknown applicability status {st}"]
    a = rec.get("applicability") or {}
    out = []
    if a.get("applicability_status") != st:
        out.append("applicability detail disagrees with the record status")
    if a.get("legal_validity") != "not_determined":
        out.append("applicability detail asserts legal validity")
    if st != "not_assessed" and not (a.get("condition_id") and a.get("basis") and all(b["verified"] for b in a["basis"])):
        out.append(f"{st} without a verified, cited scope condition")
    return out


# S7-P1-56: literal routing expectations for the synthetic profiles in eval/phase1/bank_profiles (not frozen expectations)
APPLICABILITY_EXPECT = {
    "syn-national-bank-01": {"12:3": "assessed_conditional", "12:217": "assessed_not_applicable", "12:324": "assessed_not_applicable"},
    "syn-state-member-bank-01": {"12:3": "assessed_not_applicable", "12:217": "assessed_conditional", "12:324": "assessed_not_applicable"},
    "syn-state-nonmember-bank-01": {"12:3": "assessed_not_applicable", "12:217": "assessed_not_applicable", "12:324": "assessed_conditional"},
}
APPLICABILITY_QUERY = "minimum common equity tier 1 capital ratio"


def check_applicability(index, profiles_dir):
    """Conditional applicability routing for each synthetic profile: CET1 answer results and delta() per capital part.

    Expectations are literal per profile and part. Also checks that without a profile nothing is assessed, that every assessed
    status cites verified scope sources, that legal validity stays not_determined, that each instrument record carries its own
    applicability, and that proposals carry the not-operative qualifier.
    """
    from .bank_profile import load_profile
    results = []
    base = index.answer(APPLICABILITY_QUERY, k=8)
    nd = index.delta("12 CFR 3.10")
    f0 = [] if base["applicability_status"] == "not_assessed" and "bank_profile" not in base else ["answer without profile is assessed"]
    f0 += _delta_applicability_failures(nd)
    results.append({"id": "AP00-no-profile", "passed": not f0, "failures": f0})
    for path in sorted(Path(profiles_dir).glob("*.json")):
        prof = load_profile(path)
        exp = APPLICABILITY_EXPECT.get(prof.profile_id)
        failures = [] if exp else [f"no literal expectation for profile {prof.profile_id}"]
        a = index.answer(APPLICABILITY_QUERY, k=8, bank_profile=prof)
        if a["applicability_status"] == "not_assessed":
            failures.append("CET1 answer stayed not_assessed with a profile")
        for ev in a["results"]:
            ap = ev["applicability"]
            key = f"{ap['title']}:{ap['part']}"
            if exp and key in exp and ap["applicability_status"] != exp[key]:
                failures.append(f"answer {ev['citation']}: {ap['applicability_status']} != {exp[key]}")
            failures += [f"answer {ev['citation']}: {x}" for x in
                         _applicability_record_failures({"applicability_status": ap["applicability_status"], "applicability": ap}, True)]
        for key, want in (exp or {}).items():
            t, p = key.split(":")
            d = index.delta(f"{t} CFR {p}.10", bank_profile=prof)
            if d["applicability_status"] != want:
                failures.append(f"delta {t} CFR {p}.10 current: {d['applicability_status']} != {want}")
            failures += [f"delta {t} CFR {p}.10: {x}" for x in _delta_applicability_failures(d)]
        results.append({"id": f"AP-{prof.profile_id}", "profile": prof.summary(), "passed": not failures, "failures": failures})
    return results


def _delta_applicability_failures(d):
    out = list(delta_classification_failures(d))
    profiled = "bank_profile" in d
    for r in d.get("instrument_classification", []):
        if profiled and r["doc_key"] and "applicability" not in r:
            out.append(f"{r['doc_key']}: no per-instrument applicability")
        if profiled and r["document_type"] == "Proposed Rule" and r["applicability_status"] != "not_assessed" \
                and not any("proposal" in q for q in r["applicability"]["unevaluated_qualifiers"]):
            out.append(f"{r['doc_key']}: proposal assessed without the not-operative qualifier")
    if d.get("legal_validity") != "not_determined":
        out.append("delta asserts legal validity")
    return out


def check_delta(index, item):
    ix = index if "as_of" not in item else Index(index.db_path, item["as_of"])
    out = ix.delta(item["citation"])
    exp, failures = item["expect"], []
    failures += delta_classification_failures(out)
    got = {g["doc_key"]: g for g in out["instruments"]}
    if "baseline_doc" in exp and (not out["baseline"] or out["baseline"]["doc_key"] != exp["baseline_doc"]):
        failures.append(f"baseline doc {out['baseline'] and out['baseline']['doc_key']} != {exp['baseline_doc']}")
    for key, want in exp.get("instruments", {}).items():
        g = got.get(key)
        if not g:
            failures.append(f"missing instrument {key}")
            continue
        if "state" in want and g["status"]["state"] != want["state"]:
            failures.append(f"{key} state {g['status']['state']} != {want['state']}")
        if "baseline_relation" in want and g["baseline_relation"] != want["baseline_relation"]:
            failures.append(f"{key} baseline_relation {g['baseline_relation']} != {want['baseline_relation']}")
    if "min_instruments" in exp and len(got) < exp["min_instruments"]:
        failures.append(f"only {len(got)} instruments < {exp['min_instruments']}")
    if "max_instruments" in exp and len(got) > exp["max_instruments"]:
        failures.append(f"{len(got)} instruments > {exp['max_instruments']}: {sorted(got)}")
    if exp.get("no_proposal_labelled_final_or_effective"):
        for g in got.values():
            if g["document_type"] == "Proposed Rule" and g["status"]["state"].startswith(("final", "current")):
                failures.append(f"proposal {g['doc_key']} labelled {g['status']['state']}")
    if "baseline_contains" in exp:
        b = exp["baseline_contains"]
        row = index.conn.execute("SELECT p.text FROM provision p JOIN source_document d ON d.id=p.doc_id "
                                 "WHERE d.doc_key=? AND p.locator=?", (b["doc_key"], b["locator"])).fetchone()
        if not row or b["text"] not in row[0]:
            failures.append(f"baseline {b['locator']} lacks '{b['text']}'")
    if "instruction_contains" in exp:
        b = exp["instruction_contains"]
        g = got.get(b["doc_key"])
        if not g or not any(b["text"] in (u["instruction"] or "") for u in g["units"]):
            failures.append(f"instruction of {b['doc_key']} lacks '{b['text']}'")
    return {"id": item["id"], "citation": item["citation"], "as_of": ix.as_of, "passed": not failures,
            "failures": failures, "instruments_found": sorted(got),
            "provision_classification": {r["doc_key"] or "none": r["classification"] for r in out.get("instrument_classification", [])}}


def check_status(index, item):
    exp = item["expect"]
    out = index.answer(item["query"], mode="hybrid", scope=item.get("scope", "both"), k=10)
    failures = []
    if out["status"] != exp["status"]:
        failures.append(f"status {out['status']} != {exp['status']}")
    if "gap_topic" in exp and exp["gap_topic"] not in [g["topic"] for g in out["coverage_gaps"]]:
        failures.append(f"coverage gap {exp['gap_topic']} not reported")
    if "applicability_status" in exp and out["applicability_status"] != exp["applicability_status"]:
        failures.append(f"applicability_status {out['applicability_status']}")
    failures += invariant_failures(out, item.get("scope", "both"))
    if exp["status"] == "missing_evidence" and out["results"]:
        failures.append("returned results despite missing_evidence")
    if "no_proposal_state_prefix_in" in exp:
        for r in out["results"]:
            if r["status"].get("document_type") == "Proposed Rule" and r["status"]["state"].startswith(
                    tuple(exp["no_proposal_state_prefix_in"])):
                failures.append(f"proposal labelled {r['status']['state']}")
    return {"id": item["id"], "query": item["query"], "passed": not failures, "failures": failures,
            "status": out["status"], "results": len(out["results"])}


def invariant_failures(out, scope):
    """Universal safety invariants applied to every answer bundle."""
    failures = []
    if out["applicability_status"] != "not_assessed":
        failures.append("answer claimed an applicability status")
    for r in out["results"]:
        state = r["status"]["state"]
        if scope == "current" and not state.startswith("current_baseline"):
            failures.append(f"{r['citation']} has state {state} in current scope")
        if scope == "future" and state.startswith("current"):
            failures.append(f"{r['citation']} has current state in future scope")
        if r["status"].get("document_type") == "Proposed Rule" and state.startswith(("final", "current")):
            failures.append(f"proposal {r['citation']} labelled {state}")
    return failures


def _in_numeric_range(rng, part, minor):
    lo, hi = rng["lo"].split("."), rng["hi"].split(".")
    return (lo[0] == hi[0] == part and minor.isdigit() and lo[1].isdigit() and hi[1].isdigit()
            and int(lo[1]) <= int(minor) <= int(hi[1]))


def _all_units_target(out, citation):
    """Every unit of every instrument must explicitly target the queried section (or part)."""
    c = parse_query_citations(citation)[0]
    title, part, minor = c["title"], c["part"], c["minor"]
    bad = []
    for g in out["instruments"]:
        for u in g["units"]:
            if minor:
                ok = f"{part}.{minor}" in (u["explicit_targets"] or []) or (
                    u.get("match") == "range_membership" and u.get("range") and _in_numeric_range(u["range"], part, minor))
            else:
                ok = part in (u["explicit_parts"] or []) or any(
                    t.startswith(part + ".") for t in (u["explicit_targets"] or []))
            if not ok:
                bad.append(f"{g['doc_key']} {u['locator']} targets {u['explicit_targets']}/{u['explicit_parts']}")
    return bad


def check_boundary_delta(index, item):
    out, exp, failures = index.delta(item["citation"]), item["expect"], []
    got = {g["doc_key"]: g for g in out["instruments"]}
    for key, want in exp.get("instruments", {}).items():
        if key not in got:
            failures.append(f"missing instrument {key}")
        elif "state" in want and got[key]["status"]["state"] != want["state"]:
            failures.append(f"{key} state {got[key]['status']['state']}")
    for key, needle in exp.get("carried_text_contains", {}).items():
        if key in got and not any(needle in u["carried_text"] for u in got[key]["units"]):
            failures.append(f"{key} lost its genuine amendment text '{needle}'")
    for key, needle in exp.get("no_unit_locator_contains", {}).items():
        if key in got and any(needle in u["locator"] for u in got[key]["units"]):
            failures.append(f"{key} carries a unit from another section ({needle})")
    for key in exp.get("absent_instruments", []):
        if key in got:
            failures.append(f"{key} must not appear for {item['citation']}")
    for key, match in exp.get("unit_match", {}).items():
        if key in got and not all(u["match"] == match for u in got[key]["units"]):
            failures.append(f"{key} units are not all {match}: {[u['match'] for u in got[key]['units']]}")
    for key, needle in exp.get("instruction_contains", {}).items():
        if key in got and not all(needle in (u["instruction"] or "") for u in got[key]["units"]):
            failures.append(f"{key} unit lacks the complete instruction ({needle})")
    for key in exp.get("range_provenance", []):
        if key in got and not all(u["range"] and u["range"].get("lo") and u["range"].get("instruction_ordinal")
                                  and u["review_status"] == "parsed_unreviewed" for u in got[key]["units"]):
            failures.append(f"{key} range unit lacks range provenance or unreviewed status")
    if "unresolved_ranges" in exp and len([c for c in out["unresolved_candidates"] if c["pairing_status"] == "ambiguous_range"]) != exp["unresolved_ranges"]:
        failures.append("ambiguous range candidates differ from expectation")
    if exp.get("all_units_target_queried"):
        failures += _all_units_target(out, item["citation"])
    return {"id": item["id"], "citation": item["citation"], "passed": not failures, "failures": failures,
            "instruments_found": sorted(got), "unresolved_candidates": len(out["unresolved_candidates"])}


def check_relationship_integrity(index, item):
    failures, c = [], index.conn
    rows = c.execute("SELECT id, title_no, explicit_targets, explicit_parts FROM provision WHERE kind='amendment'").fetchall()
    explicit = {r["id"]: {f"{r['title_no']} CFR {t}" for t in json.loads(r["explicit_targets"] or "[]")}
                | {f"{r['title_no']} CFR Part {p}" for p in json.loads(r["explicit_parts"] or "[]")} for r in rows}
    for r in c.execute("SELECT src_provision_id, rel_type, dst_citation, review_status FROM relationship "
                       "WHERE src_provision_id IN (SELECT id FROM provision WHERE kind='amendment')"):
        cites = explicit[r["src_provision_id"]]
        if r["rel_type"] == "amends" and r["dst_citation"] not in cites:
            failures.append(f"amends {r['dst_citation']} is not an explicit target of provision {r['src_provision_id']}")
        if r["rel_type"] == "references" and r["dst_citation"] in cites:
            failures.append(f"references duplicates explicit target {r['dst_citation']}")
        if r["review_status"] == "reviewed":
            failures.append("a relationship is marked reviewed")
    return {"id": item["id"], "passed": not failures, "failures": failures[:10], "violations": len(failures)}


def check_citation_scope(index, item):
    """Citation queries must honor scope and part filters in both the exact and the hybrid retriever.

    Every (mode, scope) pair is exercised; failures are tagged `mode/scope` so a hybrid-only violation is
    attributed to hybrid. `current_nonempty` requires acquired-baseline citations to return current records.
    """
    q, parts, failures = item["query"], item.get("parts"), []
    for mode in ("exact", "hybrid"):
        for scope in ("current", "future", "both"):
            res = index.answer(q, mode=mode, scope=scope, parts=parts, k=20)["results"]
            tag = f"{mode}/{scope}"
            for r in res:
                state = r["status"]["state"]
                if scope == "future" and (state.startswith("current") or r["kind"] not in ("amendment", "fr_preamble", "fr_table")):
                    failures.append(f"{tag}: current-baseline record {r['citation']}")
                if scope == "current" and (not state.startswith("current_baseline") or r["doc_key"].startswith("fr:")):
                    failures.append(f"{tag}: future or Federal Register record {r['citation']}")
                if parts:
                    row = index.conn.execute("SELECT title_no, part_no FROM provision WHERE id=?", (r["provision_id"],)).fetchone()
                    if f"{row['title_no']}:{row['part_no']}" not in parts:
                        failures.append(f"{tag}: part filter violated by {r['citation']}")
            if scope == "future" and item.get("future_nonempty") is True and not res:
                failures.append(f"{tag}: no future records")
            if scope == "future" and item.get("future_nonempty") is False and mode == "exact" and res:
                failures.append(f"{tag}: unexpected future records")
            if scope == "current" and item.get("current_nonempty") and not res:
                failures.append(f"{tag}: no current records for an acquired citation")
    small = index.exact(q, 2, "both", parts)
    kinds = {index.evidence(pid, with_context=False)["kind"] == "amendment" for pid, _ in small}
    if item.get("current_includes_kind"):
        cur = {index.evidence(pid, with_context=False)["kind"] for pid, _ in index.exact(q, 200, "current", parts)}
        if item["current_includes_kind"] not in cur:
            failures.append(f"exact/current lacks a {item['current_includes_kind']} record: {sorted(cur)}")
        if any(index.evidence(pid, with_context=False)["doc_key"].startswith("fr:") for pid, _ in index.exact(q, 200, "current", parts)):
            failures.append("exact/current widened to Federal Register records")
    if item.get("future_nonempty") and item.get("current_nonempty") and kinds != {True, False}:
        failures.append("both/k=2: one scope crowded out the other")
    return {"id": item["id"], "query": q, "passed": not failures, "failures": failures[:10]}


def check_status_regression(index, item):
    out = index.answer(item["query"], mode="hybrid", scope=item.get("scope", "both"), parts=item.get("parts"), k=10)
    failures = []
    if item.get("expect_status") and out["status"] != item["expect_status"]:
        failures.append(f"status {out['status']} != {item['expect_status']}")
    if item.get("expect_status_not") and out["status"] == item["expect_status_not"]:
        failures.append(f"status must not be {item['expect_status_not']}")
    if item.get("gap_topic") and item["gap_topic"] not in [g["topic"] for g in out["coverage_gaps"]]:
        failures.append(f"coverage gap {item['gap_topic']} not reported")
    if item.get("gap_coverage") and item["gap_coverage"] not in [g["coverage"] for g in out["coverage_gaps"]]:
        failures.append(f"no coverage gap with coverage '{item['gap_coverage']}'")
    failures += invariant_failures(out, item.get("scope", "both"))
    return {"id": item["id"], "query": item["query"], "scope": item.get("scope", "both"), "parts": item.get("parts"),
            "passed": not failures, "failures": failures, "status": out["status"], "results": len(out["results"]),
            "coverage_gaps": [(g["topic"], g["coverage"]) for g in out["coverage_gaps"]]}


def check_citation_completeness(index, item):
    """Every (mode, scope) run: each unresolved citation has its own gap, a resolved citation never hides it, and
    evidence_found appears only when every citation resolved. Unresolved requests are partial_evidence exactly when
    results exist, otherwise missing_evidence."""
    q, parts, failures = item["query"], item.get("parts"), []
    reasons = item.get("expect_reasons", {})
    seen_unresolved = set()
    for mode in ("exact", "hybrid"):
        for scope in ("current", "future", "both"):
            out = index.answer(q, mode=mode, scope=scope, parts=parts, k=10)
            tag = f"{mode}/{scope}"
            unresolved = [c for c in out["citations"] if not c["resolved"]]
            gaps = {g["topic"]: g for g in out["coverage_gaps"]}
            for c in unresolved:
                seen_unresolved.add(c["citation"])
                g = gaps.get(f"citation:{c['citation']}")
                if not g:
                    failures.append(f"{tag}: no gap for unresolved {c['citation']}")
                elif "not a determination that the citation is invalid" not in g["note"]:
                    failures.append(f"{tag}: gap for {c['citation']} does not separate unavailable from invalid")
                if c["legal_validity"] != "not_determined":
                    failures.append(f"{tag}: legal validity asserted for {c['citation']}")
                if c["citation"] in reasons and c["reason"] not in reasons[c["citation"]]:
                    failures.append(f"{tag}: {c['citation']} reason {c['reason']} not in {reasons[c['citation']]}")
            if unresolved:
                want = "partial_evidence" if out["results"] else "missing_evidence"
                if out["status"] != want:
                    failures.append(f"{tag}: status {out['status']} != {want} with unresolved citations")
            elif out["status"] == "evidence_found" and not all(c["resolved"] for c in out["citations"]):
                failures.append(f"{tag}: evidence_found with unresolved citation")
            if item.get("expect_all_resolved") is True and (unresolved or out["status"] != "evidence_found"):
                failures.append(f"{tag}: expected fully resolved evidence_found, got {out['status']}")
            failures += [f"{tag}: {f}" for f in invariant_failures(out, scope)]
            if parts:
                for r in out["results"]:
                    row = index.conn.execute("SELECT title_no, part_no FROM provision WHERE id=?", (r["provision_id"],)).fetchone()
                    if f"{row['title_no']}:{row['part_no']}" not in parts:
                        failures.append(f"{tag}: part filter violated by {r['citation']}")
    for label in item.get("expect_unresolved_somewhere", []):
        if label not in seen_unresolved:
            failures.append(f"{label} was never reported unresolved")
    small = index.exact(q, 2, "current", parts)
    if item.get("small_k_citations_in_parts"):
        got = {index.conn.execute("SELECT part_no FROM provision WHERE id=?", (pid,)).fetchone()["part_no"] for pid, _ in small}
        if got != set(item["small_k_citations_in_parts"]):
            failures.append(f"k=2 current exact covered parts {sorted(got)}, wanted {item['small_k_citations_in_parts']}")
    return {"id": item["id"], "query": q, "parts": parts, "passed": not failures, "failures": failures[:12]}


def check_mixed_source(index, item):
    """Named jurisdictions / accounting intent expose their not_acquired gap beside acquired CET1 evidence, in every
    mode and scope, at small k and under an excluding part filter. Results are kept, never suppressed."""
    q, failures = item["query"], []
    topic, runs = item["expect_gap_topic"], 0
    for mode in ("exact", "hybrid"):
        for scope in ("current", "future", "both"):
            for k, parts in ((10, None), (1, None), (10, ["12:999"])):
                out = index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                tag, runs = f"{mode}/{scope}/k{k}/{parts or 'nofilter'}", runs + 1
                gap = next((g for g in out["coverage_gaps"] if g["topic"] == topic), None)
                if not gap or gap["coverage"] != "not_acquired":
                    failures.append(f"{tag}: no {topic} not_acquired gap")
                if out["status"] == "evidence_found":
                    failures.append(f"{tag}: evidence_found with a named source gap")
                want = "partial_evidence" if out["results"] else "missing_evidence"
                if out["status"] != want:
                    failures.append(f"{tag}: status {out['status']} != {want}")
                if parts and out["results"]:
                    failures.append(f"{tag}: excluding filter returned results")
                if item.get("expect_acquired_topic") and item["expect_acquired_topic"] not in out["topics_matched"]:
                    failures.append(f"{tag}: acquired topic {item['expect_acquired_topic']} no longer matched")
                if item.get("expect_jurisdiction"):
                    ids = [x["id"] for x in out["source_requests"] if x["kind"] == "jurisdiction"]
                    if ids != [item["expect_jurisdiction"]]:
                        failures.append(f"{tag}: jurisdictions {ids} != {item['expect_jurisdiction']}")
                    for x in out["source_requests"]:
                        if "does not assert state-law applicability, preemption or compliance" not in x.get("note", ""):
                            failures.append(f"{tag}: jurisdiction record lacks the no-applicability note")
                failures += [f"{tag}: {f}" for f in invariant_failures(out, scope)]
    return {"id": item["id"], "query": q, "runs": runs, "passed": not failures, "failures": failures[:12]}


def check_delta_precision(index, item):
    """delta() on a paragraph or lettered citation: no crash, precision reported separately for current and for each
    instrument, no precision through the parent section, unavailable evidence structured and not a validity finding."""
    failures = []
    try:
        d = index.delta(item["citation"])
    except Exception as exc:  # a crash is a failure, never an acceptable answer
        return {"id": item["id"], "citation": item["citation"], "passed": False, "failures": [f"crashed: {exc!r}"]}
    exp = item["expect"]
    cur, fut = d["current_precision"], d["future_precision"]
    if d["applicability_status"] != "not_assessed" or d["legal_validity"] != "not_determined":
        failures.append("applicability or legal validity asserted")
    if "evidence_status" in exp and d["evidence_status"] != exp["evidence_status"]:
        failures.append(f"evidence_status {d['evidence_status']} != {exp['evidence_status']}")
    if "current_resolved" in exp and cur["resolved"] != exp["current_resolved"]:
        failures.append(f"current resolved {cur['resolved']} != {exp['current_resolved']}")
    if exp.get("current_reason") and cur.get("reason") != exp["current_reason"]:
        failures.append(f"current reason {cur.get('reason')} != {exp['current_reason']}")
    if exp.get("baseline_doc") and (not d["baseline"] or d["baseline"]["doc_key"] != exp["baseline_doc"]):
        failures.append("baseline missing or from the wrong document")
    if exp.get("baseline_none") and d["baseline"] is not None:
        failures.append("a baseline resolved for a paragraph the current text does not contain")
    if exp.get("section_context") is True and not d["section_context"]:
        failures.append("section context missing")
    inst = {g["doc_key"]: g for g in d["instruments"]}
    for key in exp.get("supported", []):
        if key not in inst or not (inst[key].get("paragraph_precision") or {}).get("resolved"):
            failures.append(f"{key} should be paragraph-supported")
    for key in exp.get("not_supported", []):
        if key in inst and (inst[key].get("paragraph_precision") or {}).get("resolved"):
            failures.append(f"{key} must not be paragraph-supported")
        if key not in inst:
            failures.append(f"{key} should be listed as section context")
    if exp.get("no_instrument_supported") and (fut["resolved"] or any(
            (g.get("paragraph_precision") or {}).get("resolved") for g in d["instruments"])):
        failures.append("an instrument established precision for an unsupported path")
    if exp.get("future_achieved") and fut["achieved"] != exp["future_achieved"]:
        failures.append(f"future achieved {fut['achieved']} != {exp['future_achieved']}")
    if exp.get("unavailable_reason"):
        if not d["unavailable"] or d["unavailable"]["reason"] != exp["unavailable_reason"]:
            failures.append(f"unavailable reason {d['unavailable'] and d['unavailable']['reason']}")
        elif "not a determination that the citation is invalid" not in d["unavailable"]["note"]:
            failures.append("unavailable note does not separate unavailable from invalid")
    keys = [g["doc_key"] for g in d["instruments"]]
    if len(keys) != len(set(keys)):
        failures.append("instruments are not independent")
    if d["requested_precision"] == "paragraph":  # section/part deltas also resolve numeric minors inside reserved ranges
        cres = index.resolve_citations(item["citation"], "current")[0]
        fres = index.resolve_citations(item["citation"], "future")[0]
        if cur["resolved"] != cres["resolved"] or fut["resolved"] != fres["resolved"]:
            failures.append("delta precision disagrees with resolve_citations")
    return {"id": item["id"], "citation": item["citation"], "passed": not failures, "failures": failures[:8],
            "evidence_status": d["evidence_status"],
            "supported": [k for k, g in inst.items() if (g.get("paragraph_precision") or {}).get("resolved")],
            "section_context": [k for k, g in inst.items() if g.get("role") == "section_context"]}


def check_instruction_boundary(index, item):
    """Real-corpus guard for the instruction/carried-text boundary: the instruction is kept separate from loose carried
    paragraphs, paragraph evidence comes only from instruction text, and an unsupported descendant never resolves."""
    from .fedreg import named_paragraph_paths
    failures, c = [], index.conn
    rows = c.execute(
        """SELECT p.instruction, p.text, p.pairing_status, p.carried_text_origin, p.explicit_paragraphs, p.locator
           FROM provision p JOIN source_document d ON d.id=p.doc_id
           WHERE d.doc_key=? AND p.kind='amendment' AND p.section_no=?""", (item["doc_key"], item["section"])).fetchall()
    if not rows:
        return {"id": item["id"], "passed": False, "failures": ["no amendment units found"]}
    for r in rows:
        if not r["instruction"].startswith(item["instruction_prefix"]) or len(r["instruction"]) > item["instruction_max_len"]:
            failures.append(f"{r['locator']}: instruction not separated ({len(r['instruction'])} chars)")
        if (r["carried_text_origin"], r["pairing_status"]) != ("loose_paragraphs", "explicit_loose_text"):
            failures.append(f"{r['locator']}: origin/pairing {r['carried_text_origin']}/{r['pairing_status']}")
        paths = json.loads(r["explicit_paragraphs"])["paths"]
        if sorted(paths) != sorted(item["supported"]):
            failures.append(f"{r['locator']}: paths {paths} != {item['supported']}")
    carried = "\n".join(r["text"] for r in rows)
    for needle in item["carried_contains"]:
        if needle not in carried:
            failures.append(f"carried text lost: {needle}")
    if any(item["carried_contains"][0] in r["instruction"] for r in rows):
        failures.append("carried text found inside the instruction")
    # corpus-wide: stored paragraph paths must be derivable from the stored instruction text alone
    bad = 0
    for ins, ep in c.execute("SELECT instruction, explicit_paragraphs FROM provision WHERE kind='amendment' AND explicit_paragraphs IS NOT NULL"):
        paths = json.loads(ep).get("paths", [])
        if paths and not set(paths) <= set(named_paragraph_paths(ins)):
            bad += 1
    if bad:
        failures.append(f"{bad} units carry paths not derivable from their instruction text")
    cite = lambda p: f"12 CFR {item['section']}{p}"
    for path in item["supported"]:
        rec = index.resolve_citations(cite(path), "future")[0]
        if not (rec["resolved"] and rec["achieved"] == "paragraph"):
            failures.append(f"{path} should resolve at paragraph precision")
    for path in item["unsupported"]:
        label = cite(path)
        rec = index.resolve_citations(label, "future")[0]
        if rec["resolved"] or rec["achieved"] != "section_only" or rec["future_paragraph_support"]:
            failures.append(f"{path} resolved through an ancestor or a cross-reference")
        d = index.delta(label)
        if d["future_precision"]["resolved"] or any(g.get("role") != "section_context" for g in d["instruments"] if g["doc_key"] == item["doc_key"]):
            failures.append(f"{path}: delta established precision")
        if d["legal_validity"] != "not_determined" or d["applicability_status"] != "not_assessed":
            failures.append(f"{path}: delta asserted validity or applicability")
        for mode in ("exact", "hybrid"):
            for scope in ("current", "future", "both"):
                for parts in (None, ["12:999"]):
                    out = index.answer(label, mode=mode, scope=scope, parts=parts, k=10)
                    tag = f"{path} {mode}/{scope}/{parts or 'nofilter'}"
                    if out["status"] == "evidence_found":
                        failures.append(f"{tag}: evidence_found")
                    if f"citation:{label}" not in {g["topic"] for g in out["coverage_gaps"]}:
                        failures.append(f"{tag}: no citation gap")
                    if parts and out["results"]:
                        failures.append(f"{tag}: excluding filter returned results")
                    failures += [f"{tag}: {f}" for f in invariant_failures(out, scope)]
    return {"id": item["id"], "doc_key": item["doc_key"], "section": item["section"], "units": len(rows),
            "passed": not failures, "failures": failures[:10]}


def check_current_agreement(index, item):
    """delta() and the current-citation resolver must expose the same baseline for a section citation, including a
    numeric section inside an acquired range. This is explicit and covers section citations; the paragraph-only
    agreement inside check_delta_precision does not cover it."""
    failures, q = [], item["citation"]
    cur = index.resolve_citations(q, "current")[0]
    d = index.delta(q)
    base = d["baseline"]
    if (base is not None) != cur["resolved"]:
        failures.append(f"delta baseline {'present' if base else 'absent'} but resolver resolved={cur['resolved']}")
    via = (cur.get("baseline_match") or {}).get("via")
    if via != item.get("expect_via"):
        failures.append(f"resolver via {via} != {item.get('expect_via')}")
    if base is not None and cur["resolved"]:
        first = index.evidence(cur["_current_ids"][0], with_context=False)
        for name, got, want in (("doc_key", base["doc_key"], first["doc_key"]), ("locator", base["locator"], first["locator"]),
                                ("sha256", base["sha256"], first["source_sha256"]),
                                ("issue_date", base["issue_date"], first["status"]["publisher_issue_date"]),
                                ("up_to_date_as_of", base["up_to_date_as_of"], first["status"]["publisher_up_to_date_as_of"])):
            if got != want:
                failures.append(f"{name} differs between delta and resolver: {got} vs {want}")
        if base.get("match") != via:
            failures.append(f"delta match {base.get('match')} != resolver via {via}")
        if via == "range_section":
            if base.get("range_membership") != cur["baseline_match"]["range"]:
                failures.append("range membership differs between delta and resolver")
            if base["locator"] != item.get("expect_range_locator") or base["doc_key"] != item.get("expect_doc"):
                failures.append(f"range provenance {base['doc_key']} {base['locator']} not the publisher's original")
        if first["status"]["state"] != "current_baseline_requires_operative_effect_review":
            failures.append("range or section evidence is labelled as anything but a baseline needing operative-effect review")
    if d["legal_validity"] != "not_determined" or d["applicability_status"] != "not_assessed":
        failures.append("delta asserted validity or applicability")
    for mode in ("exact", "hybrid"):
        for scope in ("current", "future", "both"):
            for tag, parts in (("nofilter", None), ("allow", item.get("allow")), ("exclude", item.get("exclude"))):
                if tag != "nofilter" and not parts:
                    continue
                for k in (10, 1):
                    out = index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                    label = f"{mode}/{scope}/{tag}/k{k}"
                    failures += [f"{label}: {f}" for f in invariant_failures(out, scope)]
                    if tag == "exclude":
                        if out["status"] == "evidence_found":
                            failures.append(f"{label}: evidence_found under an excluding filter")
                        if mode == "exact" and out["results"]:
                            failures.append(f"{label}: exact returned results under an excluding filter")
                        continue
                    if scope == "current" and item.get("expect_via"):
                        if out["status"] != "evidence_found":
                            failures.append(f"{label}: status {out['status']}")
                        if via == "range_section" and not any(r.get("range_membership") for r in out["results"] if r["doc_key"] == item["expect_doc"]):
                            failures.append(f"{label}: range membership not identified on the result")
                    if scope == "current" and not item.get("expect_via") and out["status"] == "evidence_found":
                        failures.append(f"{label}: evidence_found for an unresolved section")
    return {"id": item["id"], "citation": q, "via": via, "passed": not failures, "failures": failures[:10]}


def check_amendatory_lists(index, item):
    """Real-corpus guard for amendment-list parsing: literal expected paragraph paths (no fabricated shorthand) and
    literal explicit section targets (Oxford-comma lists kept whole)."""
    c, failures = index.conn, []
    rows = c.execute(
        """SELECT p.instruction, p.explicit_targets, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id
           WHERE d.doc_key=? AND p.kind='amendment' AND (? IS NULL OR p.section_no=?) AND p.instruction LIKE ?""",
        (item["doc_key"], item.get("section"), item.get("section"), item.get("instruction_prefix", "") + "%")).fetchall()
    if not rows:
        return {"id": item["id"], "doc_key": item["doc_key"], "passed": False, "failures": ["no amendment units found"]}
    if "expect_paths" in item:
        got = sorted({x for r in rows for x in json.loads(r["explicit_paragraphs"] or "{}").get("paths", [])})
        if got != sorted(item["expect_paths"]):
            failures.append(f"paths {got} != {sorted(item['expect_paths'])}")
        for path in item.get("forbid_paths", []):
            if path in got:
                failures.append(f"fabricated path {path} was produced")
            rec = index.resolve_citations(f"12 CFR {item['section']}{path}", "future")[0]
            if rec["resolved"]:
                failures.append(f"fabricated {path} resolves in future scope")
        for path in item["expect_paths"]:
            rec = index.resolve_citations(f"12 CFR {item['section']}{path}", "future")[0]
            if not (rec["resolved"] and rec["achieved"] == "paragraph"):
                failures.append(f"{path} should resolve at paragraph precision")
    if "expect_targets" in item:
        for r in rows:
            if sorted(json.loads(r["explicit_targets"])) != sorted(item["expect_targets"]):
                failures.append(f"targets {json.loads(r['explicit_targets'])} != {item['expect_targets']}")
    return {"id": item["id"], "doc_key": item["doc_key"], "units": len(rows), "passed": not failures, "failures": failures[:8]}


def check_designator_integrity(index, item):
    """Real-corpus guard for designator tokens: complete paths read whole (whitespace around inline emphasis ignored),
    their truncated ancestors are not named, the stored instruction text is untouched, and one instrument's support
    never establishes another's."""
    from .fedreg import named_paragraph_paths
    c, failures = index.conn, []
    rows = c.execute(
        """SELECT p.instruction, p.explicit_paragraphs, p.instruction_ordinals FROM provision p JOIN source_document d ON d.id=p.doc_id
           WHERE d.doc_key=? AND p.kind='amendment' AND p.section_no=? AND p.instruction LIKE ?""",
        (item["doc_key"], item["section"], item["instruction_prefix"] + "%")).fetchall()
    if not rows:
        return {"id": item["id"], "doc_key": item["doc_key"], "passed": False, "failures": ["no amendment units found"]}
    got = sorted({x for r in rows for x in json.loads(r["explicit_paragraphs"] or "{}").get("paths", [])})
    if got != sorted(item["expect_paths"]):
        failures.append(f"paths {got} != {sorted(item['expect_paths'])}")
    for path in item["truncated_ancestors"]:
        if path in got:
            failures.append(f"truncated ancestor {path} was accepted as a named path")
    for text in item["instruction_contains"]:
        if not all(text in r["instruction"] for r in rows):
            failures.append(f"stored instruction text altered: missing {text!r}")
    cite = lambda p: f"12 CFR {item['section']}{p}"
    for path in item["complete_paths"]:
        rec = index.resolve_citations(cite(path), "future")[0]
        d = index.delta(cite(path))
        if not (rec["resolved"] and rec["achieved"] == "paragraph"):
            failures.append(f"{path} should resolve at paragraph precision")
        if item["doc_key"] not in d["future_precision"]["supported_instruments"]:
            failures.append(f"{path} not supported by {item['doc_key']}")
        for mode in ("exact", "hybrid"):
            out = index.answer(cite(path), mode=mode, scope="future", k=10)
            if out["status"] != "evidence_found":
                failures.append(f"{path} {mode}/future status {out['status']}")
            if index.answer(cite(path), mode=mode, scope="future", parts=["12:999"], k=10)["results"] and mode == "exact":
                failures.append(f"{path}: excluding filter returned exact results")
    for path in item["truncated_ancestors"]:
        rec = index.resolve_citations(cite(path), "future")[0]
        d = index.delta(cite(path))
        if rec["resolved"] or rec["achieved"] != "section_only" or item["doc_key"] in d["future_precision"]["supported_instruments"]:
            failures.append(f"truncated ancestor {path} resolved or was supported by {item['doc_key']}")
        if any(g["doc_key"] == item["doc_key"] and g.get("role") != "section_context" for g in d["instruments"]):
            failures.append(f"{path}: {item['doc_key']} is not section context")
        for mode in ("exact", "hybrid"):
            for scope in ("current", "future", "both"):
                for parts in (None, ["12:999"]):
                    for k in (10, 1):
                        out = index.answer(cite(path), mode=mode, scope=scope, parts=parts, k=k)
                        tag = f"{path} {mode}/{scope}/{parts or 'nofilter'}/k{k}"
                        if out["status"] == "evidence_found":
                            failures.append(f"{tag}: evidence_found")
                        if f"citation:{cite(path)}" not in {g["topic"] for g in out["coverage_gaps"]}:
                            failures.append(f"{tag}: no citation gap")
                        if parts and out["results"]:
                            failures.append(f"{tag}: excluding filter returned results")
                        failures += [f"{tag}: {f}" for f in invariant_failures(out, scope)]
    bad = 0  # corpus-wide: stored paths of single-instruction units equal what the normalized parse of their instruction yields
    for ins, ep, ords in c.execute("SELECT instruction, explicit_paragraphs, instruction_ordinals FROM provision WHERE kind='amendment' AND explicit_paragraphs IS NOT NULL"):
        data = json.loads(ep)
        if data.get("status") == "attributed" and len(json.loads(ords or "[]")) == 1 and set(data["paths"]) != set(named_paragraph_paths(ins)):
            bad += 1
    if bad:
        failures.append(f"{bad} units store paths that differ from the normalized parse of their instruction")
    return {"id": item["id"], "doc_key": item["doc_key"], "section": item["section"], "units": len(rows),
            "passed": not failures, "failures": failures[:10]}


def check_query_citations(index, item):
    """Real-corpus guard for query-citation reading: a request with whitespace inside complete designators resolves
    exactly like the canonical citation (original request preserved); a complete but missing path keeps every designator;
    an unclosed, malformed or detached suffix is an explicit unresolved citation whose valid prefix is never accepted."""
    failures, q, kind = [], item["requested"], item["kind"]
    scopes = ("current", "future", "both")
    if kind == "equivalent":
        canon = item["canonical"]
        for scope in scopes:
            for parts in (None, ["12:3"], ["12:999"]):
                a, b = index.resolve_citations(q, scope, parts)[0], index.resolve_citations(canon, scope, parts)[0]
                for key in ("citation", "resolved", "achieved", "reason", "by_scope", "future_paragraph_support", "_current_ids", "_future_ids"):
                    if a[key] != b[key]:
                        failures.append(f"{scope}/{parts}: {key} differs from the canonical citation")
                if a["requested"] != q or a["canonical"] != canon:
                    failures.append("original request or canonical citation not preserved")
        fut = index.resolve_citations(q, "future")[0]
        got = sorted({x["doc_key"] for x in fut["future_paragraph_support"]})
        if got != sorted(item["expect_support"]):
            failures.append(f"supporting instruments {got} != {sorted(item['expect_support'])}")
        da, db = index.delta(q), index.delta(canon)
        for key in ("baseline", "section_context", "current_precision", "future_precision", "evidence_status", "requested_path"):
            if da[key] != db[key]:
                failures.append(f"delta {key} differs from the canonical citation")
        if [g["doc_key"] for g in da["instruments"]] != [g["doc_key"] for g in db["instruments"]]:
            failures.append("delta instruments differ from the canonical citation")
        for mode in ("exact", "hybrid"):
            for scope in scopes:
                for parts in (None, ["12:999"]):
                    for k in (10, 1):
                        x, y = (index.answer(t, mode=mode, scope=scope, parts=parts, k=k) for t in (q, canon))
                        if x["status"] != y["status"] or [r["provision_id"] for r in x["results"]] != [r["provision_id"] for r in y["results"]]:
                            failures.append(f"answer {mode}/{scope}/{parts}/k{k} differs from the canonical citation")
    elif kind == "missing_complete":
        canon = item["canonical"]
        for scope in scopes:
            r = index.resolve_citations(q, scope)[0]
            if r["canonical"] != canon or r["requested"] != q:
                failures.append(f"{scope}: path not retained ({r['canonical']})")
            if r["resolved"] and scope != "both":
                failures.append(f"{scope}: a missing path resolved")
        if index.exact(q, 50, "current"):
            failures.append("current exact returned records for a missing paragraph")
        d = index.delta(q)
        if d["baseline"] is not None or d["requested_path"] != item["requested_path"]:
            failures.append("delta lost the complete requested path or resolved it through an ancestor")
    elif kind == "malformed":
        for scope in scopes:
            r = index.resolve_citations(q, scope)
            bad = [x for x in r if x["citation"] == item["expect_label"]]
            if not bad or bad[0]["resolved"] or bad[0]["reason"] != item["expect_reason"]:
                failures.append(f"{scope}: not an explicit unresolved citation with reason {item['expect_reason']}")
            elif bad[0]["diagnostics"]["accepted_valid_prefix"] or bad[0]["legal_validity"] != "not_determined" or bad[0]["_current_ids"] or bad[0]["_future_ids"]:
                failures.append(f"{scope}: valid prefix accepted or validity asserted")
            if index.exact(q, 50, scope):
                failures.append(f"{scope}: exact returned records for a malformed request")
        for mode in ("exact", "hybrid"):
            for scope in scopes:
                for parts in (None, ["12:3"], ["12:999"]):
                    for k in (10, 1):
                        out = index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                        tag = f"{mode}/{scope}/{parts or 'nofilter'}/k{k}"
                        if out["status"] == "evidence_found":
                            failures.append(f"{tag}: evidence_found")
                        if f"citation:{item['expect_label']}" not in {g["topic"] for g in out["coverage_gaps"]}:
                            failures.append(f"{tag}: no citation gap")
                        if item.get("expect_topic") and item["expect_topic"] not in out["topics_matched"]:
                            failures.append(f"{tag}: topic {item['expect_topic']} no longer matched")
                        failures += [f"{tag}: {f}" for f in invariant_failures(out, scope)]
        d = index.delta(item["expect_label"])
        if d["evidence_status"] != "unavailable" or d["baseline"] or d["instruments"] or d["unavailable"]["reason"] != item["expect_reason"]:
            failures.append("delta of the malformed request is not explicit unavailable evidence")
    elif kind == "control":
        for scope in ("future",):
            for mode in ("exact", "hybrid"):
                if index.answer(q, mode=mode, scope=scope, k=10)["status"] != "evidence_found":
                    failures.append(f"canonical control {q} {mode}/{scope} is not evidence_found")
        if not index.resolve_citations(q, "future")[0]["resolved"] or index.resolve_citations(q, "future")[0]["canonical"] != q:
            failures.append("canonical control changed")
    return {"id": item["id"], "requested": q, "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_suffix_integrity(index, item):
    """Length-independent suffix guard on the acquired corpus. `unclosed`: an unclosed numeric suffix of every listed length
    (with and without whitespace) is an explicit unresolved citation, never its ancestor, never evidence_found, with a
    citation gap, in resolver, exact/hybrid answers and delta. `complete`: a closed designator of the same lengths keeps
    its full path. `instruction`: an instruction whose designator is unclosed names no path. `control`: the ancestor itself,
    asked for plainly, is still evidence."""
    from .fedreg import named_paragraph_paths
    failures, kind = [], item["kind"]
    scopes = ("current", "future", "both")
    if kind in ("unclosed", "forms"):
        if kind == "forms":  # punctuation-led and unclosed-glued forms, written out literally
            cases = [(0, q) for q in item["requests"]]
        else:
            cases = [(n, t.replace("{N}", "9" * n)) for n in item["lengths"] for t in item["templates"]]
        for text in item.get("instructions", []):
            if named_paragraph_paths(text):
                failures.append(f"instruction '{text}' attributed a path")
        for n, q in cases:
            label = parse_query_citations(q)[0]["label"]
            c = parse_query_citations(q)[0]
            if c["malformed"] != "unclosed_or_malformed_designator_suffix" or c["diagnostics"]["accepted_valid_prefix"]:
                failures.append(f"len{n} '{q}': not read as an unclosed suffix")
                continue
            for scope in scopes:
                r = [x for x in index.resolve_citations(q, scope) if x["citation"] == label]
                if not r or r[0]["resolved"] or r[0]["canonical"] == item["ancestor"] or r[0]["_current_ids"] or r[0]["_future_ids"]:
                    failures.append(f"len{n} {scope}: '{q}' resolved or established its ancestor")
                if index.exact(q, 50, scope):
                    failures.append(f"len{n} {scope}: exact returned records for '{q}'")
            for mode in ("exact", "hybrid"):
                for scope in scopes:
                    for parts in (None, ["12:3"], ["12:999"]):
                        for k in (10, 1):
                            out = index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = f"len{n} {mode}/{scope}/{parts or 'nofilter'}/k{k}"
                            if out["status"] == "evidence_found":
                                failures.append(f"{tag}: evidence_found")
                            if f"citation:{label}" not in {g["topic"] for g in out["coverage_gaps"]}:
                                failures.append(f"{tag}: no citation gap")
                            failures += [f"{tag}: {f}" for f in invariant_failures(out, scope)]
            d = index.delta(label)
            if d["evidence_status"] != "unavailable" or d["baseline"] or d["instruments"]:
                failures.append(f"len{n}: delta of '{q}' is not explicit unavailable evidence")
    elif kind == "complete":
        for n in item["lengths"]:
            for template in item["templates"]:
                q = template.replace("{N}", "9" * n)
                c = parse_query_citations(q)[0]
                want = item["canonical"].replace("{N}", "9" * n)
                if c["malformed"] or c["canonical"] != want or c["requested"] != q:
                    failures.append(f"len{n} '{q}': full path not retained ({c['canonical']})")
                for scope in scopes:
                    r = index.resolve_citations(q, scope)[0]
                    if r["canonical"] != want or (r["resolved"] and scope == "current"):
                        failures.append(f"len{n} {scope}: '{q}' collapsed or resolved through its ancestor")
                if index.exact(q, 50, "current"):
                    failures.append(f"len{n}: current exact returned records for a missing paragraph")
                dd = index.delta(q)
                if dd["baseline"] is not None or dd["requested_path"] != item["requested_path"].replace("{N}", "9" * n):
                    failures.append(f"len{n}: delta lost the complete path")
    elif kind == "instruction":
        for n in item["lengths"]:
            for template in item["templates"]:
                if named_paragraph_paths(template.replace("{N}", "9" * n)):
                    failures.append(f"len{n}: an unclosed instruction designator named a path")
        for text, want in item.get("closed", []):
            if named_paragraph_paths(text) != want:
                failures.append(f"closed instruction '{text}' != {want}")
    elif kind == "control":
        q = item["requested"]
        for mode in ("exact", "hybrid"):
            if index.answer(q, mode=mode, scope="future", k=10)["status"] != "evidence_found":
                failures.append(f"control {q} {mode} is not evidence_found")
        for text in [item["parenthetical"], *item.get("closed_explanations", [])]:
            if parse_query_citations(text)[0]["malformed"]:
                failures.append(f"an ordinary closed explanation '{text}' became a malformed designator suffix")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_appendix_evidence(index, item):
    """Real-corpus guard for appendix text carried as a direct REGTEXT/EXTRACT block. `recovered`: the appendix's passages
    exist in order with stable locators, the source hash, a complete line count and a token fingerprint equal to the source
    element's. `retrieval`: the text reaches future retrieval for the appendix's part and stays out of current scope and
    excluded part filters. `no_targets`: the appendix and its instructions assert no section, part-wide or range
    target, no relationship, and appendix-internal section numbers become no CFR section. `identity`: distinct
    publisher-titled appendices, no binding or applicability claim."""
    from .fedreg import source_tokens, token_fingerprint
    c, failures, kind = index.conn, [], item["kind"]
    rows = c.execute("SELECT p.*, d.sha256 AS doc_sha FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND "
                     "json_extract(p.appendix,'$.kind')='appendix_extract' AND json_extract(p.appendix,'$.letter')=? ORDER BY p.id",
                     (item["doc_key"], item.get("letter", ""))).fetchall()
    if kind == "recovered":
        aps = [json.loads(r["appendix"]) for r in rows]
        if len(rows) != item["chunks"]:
            failures.append(f"{len(rows)} passages != expected {item['chunks']}")
        if [a["chunk"] for a in aps] != list(range(1, len(rows) + 1)) or {a["chunks"] for a in aps} != {len(rows)}:
            failures.append("passages are not numbered 1..n in document order")
        if rows:
            if sum(a["lines"] for a in aps) != aps[0]["total_lines"] or aps[0]["total_lines"] != item["lines"]:
                failures.append("line count does not cover the appendix")
            got = token_fingerprint([t for r in rows for t in source_tokens(r["text"])])
            if got != aps[0]["source_fingerprint"] or got != item["fingerprint"]:
                failures.append(f"recovered text does not reproduce the source element ({got['tokens']} tokens)")
            for r, a in zip(rows, aps):
                if not (a["source_sha256"] == r["doc_sha"] == item["source_sha256"]) or not a.get("source_url") or not a.get("source_asset_url"):
                    failures.append(f"{r['locator']}: source hash or URL missing or different")
                if not re.fullmatch(rf"{re.escape(item['doc_key'][3:])}:reg:\d+:appx-{item['part']}-{item['letter']}:\d{{3}}of\d{{3}}", r["locator"]):
                    failures.append(f"{r['locator']}: unexpected locator")
                if (r["heading"], r["part_no"], r["section_no"], r["kind"]) != (item["heading"], item["part"], None, "amendment"):
                    failures.append(f"{r['locator']}: heading, part or section differs")
                if (r["char_start"], r["char_end"]) != (rows[0]["char_start"], rows[0]["char_end"]) or not (r["char_start"] or 0) < (r["char_end"] or 0):
                    failures.append(f"{r['locator']}: source span missing or inconsistent")
            if (rows[0]["locator"], rows[-1]["locator"]) != (item["first_locator"], item["last_locator"]):
                failures.append("first/last locator differs from the expected literal")
    elif kind == "retrieval":
        marker = f"appx-{item['part']}-{item['letter']}:"
        for mode in ("hybrid", "bm25"):
            for k in (10, 5):
                out = index.answer(item["query"], mode=mode, scope="future", parts=[f"12:{item['part']}"], k=k)
                if not [r for r in out["results"] if marker in r["locator"]]:
                    failures.append(f"{mode}/k{k}: future 12:{item['part']} did not reach the appendix")
                if out["applicability_status"] != "not_assessed":
                    failures.append("applicability asserted")
            for scope, parts in (("current", [f"12:{item['part']}"]), ("current", None), ("future", ["12:3"]), ("future", ["12:217"])):
                out = index.answer(item["query"], mode=mode, scope=scope, parts=parts, k=20)
                if [r for r in out["results"] if "appx-" in r["locator"]]:
                    failures.append(f"{mode}/{scope}/{parts}: appendix text returned outside its scope")
    elif kind == "no_targets":
        app = c.execute("SELECT p.* FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.appendix IS NOT NULL", (item["doc_key"],)).fetchall()
        ids = [r["id"] for r in app]
        if not ids:
            failures.append("no appendix records")
        for r in app:
            if any(json.loads(r[k] or "[]") for k in ("explicit_targets", "explicit_parts", "explicit_ranges", "referenced_citations")) or r["section_no"]:
                failures.append(f"{r['locator']}: asserts a section, part or range target")
        if ids and c.execute(f"SELECT COUNT(*) FROM relationship WHERE src_provision_id IN ({','.join('?' * len(ids))})", ids).fetchone()[0]:
            failures.append("appendix records source relationships")
        marks = ",".join("?" * len(item["internal_sections"]))
        if c.execute(f"SELECT COUNT(*) FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.section_no IN ({marks})",
                     (item["doc_key"], *item["internal_sections"])).fetchone()[0]:
            failures.append("an appendix-internal section number became a CFR section")
        for sec in item["internal_sections"]:
            d = index.delta(f"12 CFR {sec}")
            if [g for g in d["instruments"] if g["doc_key"] == item["doc_key"]]:
                failures.append(f"delta 12 CFR {sec} lists the appendix document as an instrument")
        for cite in item["delta_citations"]:
            d = index.delta(cite)
            if "appx-" in json.dumps(d["instruments"], default=str):
                failures.append(f"delta {cite} lists appendix text as an instrument")
    elif kind == "identity":
        heads = {r["heading"] for r in rows}
        if heads != {item["heading"]}:
            failures.append(f"heading {heads} != {item['heading']}")
        for r in rows:
            a = json.loads(r["appendix"])
            if a["binding_status"] != "not_determined" or r["pairing_status"] != "appendix_text":
                failures.append(f"{r['locator']}: status or binding claim")
            if item.get("policy_statement") and not a["policy_statement"]:
                failures.append(f"{r['locator']}: publisher's policy-statement identity lost")
            if json.loads(r["explicit_paragraphs"] or "{}").get("status") != "appendix_scope_not_attributed":
                failures.append(f"{r['locator']}: paragraph attribution asserted")
        rec = index.evidence(rows[0]["id"]) if rows else {}
        if rec.get("appendix", {}).get("letter") != item["letter"]:
            failures.append("evidence record lacks the appendix identity")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_titleless_delta(index, item):
    """Real-corpus guard for titleless section citations: delta() and the citation resolver take the same title decision,
    so `\u00a7 3.22` keeps exactly the instruments (and per-instrument paragraph support) of `12 CFR 3.22`, and a title that
    cannot be identified uniquely is reported as ambiguity, never merged. Literal expectations are pinned in the item."""
    failures = []
    plain, explicit = item["titleless"], item["explicit"]
    a, b = index.delta(plain), index.delta(explicit)
    strip = ("citation", "requested_citation", "canonical_citation", "title_resolution")
    if {k: v for k, v in a.items() if k not in strip} != {k: v for k, v in b.items() if k not in strip}:
        failures.append("titleless delta differs from the fully qualified delta")
    res = a["title_resolution"]
    if (res["status"], res["title"]) != ("unique_from_acquired_evidence", item["title"]):
        failures.append(f"title resolution {res['status']}/{res['title']} != unique/{item['title']}")
    if [g["doc_key"] for g in a["instruments"]] != item["instruments"]:
        failures.append(f"instruments {[g['doc_key'] for g in a['instruments']]} != {item['instruments']}")
    if item.get("supported") is not None and sorted(a["future_precision"].get("supported_instruments") or []) != item["supported"]:
        failures.append("paragraph support differs from the expected instruments")
    if bool(a["baseline"]) != item["baseline"]:
        failures.append("baseline presence differs")
    if item.get("supported") is not None:
        ctx = sorted(g["doc_key"] for g in a["instruments"] if g.get("role") == "section_context")
        if ctx != item["section_context"]:
            failures.append(f"section-context instruments {ctx} != {item['section_context']}")
    for scope in ("current", "future", "both"):
        ra, rb = index.resolve_citations(plain, scope)[0], index.resolve_citations(explicit, scope)[0]
        for key in ("resolved", "achieved", "reason", "by_scope", "future_paragraph_support", "_current_ids", "_future_ids"):
            if ra[key] != rb[key]:
                failures.append(f"{scope}: resolver {key} differs between titleless and explicit")
    if a["current_precision"]["resolved"] != index.resolve_citations(plain, "current")[0]["resolved"] or \
            a["future_precision"]["resolved"] != index.resolve_citations(plain, "future")[0]["resolved"]:
        failures.append("delta and resolver disagree for the titleless citation")
    if "None CFR" in json.dumps(a, default=str):
        failures.append("a missing title was interpolated as 'None'")
    if a["legal_validity"] != "not_determined" or a["applicability_status"] != "not_assessed":
        failures.append("validity or applicability asserted")
    return {"id": item["id"], "titleless": plain, "explicit": explicit, "passed": not failures, "failures": failures[:10]}


def check_query_lists(index, item):
    """Real-corpus guard for list and shorthand completeness in a request. `equivalent`: a comma/and/or list or paragraph
    shorthand yields one citation per member (the expected canonical labels) and resolves, in every scope, part filter,
    mode and k, exactly like its fully qualified control; with a missing member the answer is never evidence_found and
    carries that member's gap. `unresolved`: members the hierarchy does not settle, or malformed tails, are structured
    unresolved expressions with a gap, never merged into the citation before them. `delta`: a multi-citation request is
    reported as unsupported, not truncated to its first member."""
    failures, kind = [], item["kind"]
    scopes = ("current", "future", "both")
    gaps = lambda out: sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))
    if kind == "equivalent":
        short, control = item["shorthand"], item["control"]
        if [c["canonical"] for c in parse_query_citations(short)] != item["members"]:
            failures.append(f"members {[c['canonical'] for c in parse_query_citations(short)]} != {item['members']}")
        for scope in scopes:
            for parts in (None, ["12:3"], ["12:999"]):
                a, b = index.resolve_citations(short, scope, parts), index.resolve_citations(control, scope, parts)
                keys = ("citation", "resolved", "achieved", "reason", "by_scope", "future_paragraph_support", "_current_ids", "_future_ids")
                if [[r[k] for k in keys] for r in a] != [[r[k] for k in keys] for r in b]:
                    failures.append(f"{scope}/{parts}: resolver differs from the fully qualified control")
        for mode in ("exact", "hybrid"):
            for scope in scopes:
                for parts in (None, ["12:3"], ["12:999"]):
                    for k in (10, 1):
                        x, y = (index.answer(q, mode=mode, scope=scope, parts=parts, k=k) for q in (short, control))
                        tag = f"{mode}/{scope}/{parts or 'nofilter'}/k{k}"
                        if x["status"] != y["status"] or gaps(x) != gaps(y):
                            failures.append(f"{tag}: status or citation gaps differ from the control")
                        if mode == "exact" and [r["provision_id"] for r in x["results"]] != [r["provision_id"] for r in y["results"]]:
                            failures.append(f"{tag}: results differ from the control")
                        if not item["complete"] and scope == "current" and x["status"] == "evidence_found":
                            failures.append(f"{tag}: evidence_found although a member is missing")
        cur = index.answer(short, mode="exact", scope="current", k=10)
        if (cur["status"] == "evidence_found") != item["complete"]:
            failures.append(f"current exact status {cur['status']} does not match complete={item['complete']}")
        if item.get("gap") and item["gap"] not in gaps(cur):
            failures.append(f"missing gap {item['gap']}")
    elif kind == "unresolved":
        q = item["request"]
        cites = parse_query_citations(q)
        bad = [c for c in cites if c["malformed"]]
        if [(c["requested"], c["malformed"]) for c in bad] != [(x["requested"], x["reason"]) for x in item["expressions"]]:
            failures.append(f"unresolved expressions {[(c['requested'], c['malformed']) for c in bad]} != expected")
        if any(c["diagnostics"]["accepted_valid_prefix"] for c in bad):
            failures.append("a resolved prefix was accepted")
        for mode in ("exact", "hybrid"):
            for scope in scopes:
                for parts in (None, ["12:3"], ["12:999"]):
                    for k in (10, 1):
                        out = index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                        tag = f"{mode}/{scope}/{parts or 'nofilter'}/k{k}"
                        if out["status"] == "evidence_found":
                            failures.append(f"{tag}: evidence_found")
                        for c in bad:
                            if f"citation:{c['label']}" not in gaps(out):
                                failures.append(f"{tag}: no gap for {c['label']}")
        d = index.delta(q)
        if d["evidence_status"] != "unsupported_multi_citation":
            failures.append("delta did not report the multi-citation request")
    elif kind == "delta":
        d = index.delta(item["request"])
        if d["evidence_status"] != "unsupported_multi_citation" or d["baseline"] or d["instruments"]:
            failures.append("delta selected a member instead of reporting the unsupported request")
        if [c["label"] for c in d["unavailable"]["citations"]] != item["members"]:
            failures.append("delta did not list every member")
        single = index.delta(item["single"])
        if single["evidence_status"] == "unsupported_multi_citation" or not single["instruments"] == index.delta(item["single_control"])["instruments"]:
            failures.append("single-citation delta changed")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_query_ranges(index, item):
    """Real-corpus guard for ranges in a request. `unsupported`: each request parses to one unresolved `unsupported_range`
    expression (never an endpoint), the resolver and exact retrieval return nothing for it, answers carry its citation
    gap and are never evidence_found, and delta() reports it with no baseline or instruments. `independent`: a citation
    elsewhere in the request still resolves and is the only source of exact results. `prose`: ordinary text around a
    citation is not a range and the request still equals the plain citation."""
    failures, kind = [], item["kind"]
    scopes = ("current", "future", "both")
    gaps = lambda out: sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))
    if kind == "unsupported":
        for q in item["requests"]:
            cites = parse_query_citations(q)
            if len(cites) != 1 or cites[0]["malformed"] != "unsupported_range" or cites[0]["diagnostics"]["interior_inferred"]:
                failures.append(f"'{q}': not read as one unsupported range expression")
                continue
            label = cites[0]["label"]
            for scope in scopes:
                for parts in (None, ["12:3"], ["12:999"]):
                    recs = index.resolve_citations(q, scope, parts)
                    if len(recs) != 1 or recs[0]["resolved"] or recs[0]["_current_ids"] or recs[0]["_future_ids"] or recs[0]["reason"] != "unsupported_range":
                        failures.append(f"'{q}' {scope}/{parts}: resolver accepted an endpoint or interior")
            for mode in ("exact", "hybrid"):
                for scope in scopes:
                    for parts in (None, ["12:3"], ["12:999"]):
                        for k in (10, 1):
                            out = index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = f"'{q}' {mode}/{scope}/{parts or 'nofilter'}/k{k}"
                            if out["status"] == "evidence_found":
                                failures.append(f"{tag}: evidence_found")
                            if f"citation:{label}" not in gaps(out):
                                failures.append(f"{tag}: no citation gap")
                            if mode == "exact" and out["results"]:
                                failures.append(f"{tag}: exact returned records for the range")
            d = index.delta(q)
            if d["evidence_status"] != "unavailable" or d["baseline"] or d["instruments"] or d["unavailable"]["reason"] != "unsupported_range":
                failures.append(f"'{q}': delta did not report the unsupported range")
    elif kind == "independent":
        q, other = item["request"], item["independent"]
        cites = parse_query_citations(q)
        if [c["malformed"] for c in cites] != item["malformed"] or other not in [c["canonical"] for c in cites]:
            failures.append("independent citation was not preserved next to the range")
        for mode in ("exact", "hybrid"):
            for scope in ("current", "both"):
                out = index.answer(q, mode=mode, scope=scope, k=10)
                if out["status"] == "evidence_found" or len(gaps(out)) != 1:
                    failures.append(f"{mode}/{scope}: expected exactly one citation gap and no evidence_found")
        ex = index.answer(q, mode="exact", scope="current", k=10)
        alone = index.answer(other, mode="exact", scope="current", k=10)
        if [r["provision_id"] for r in ex["results"]] != [r["provision_id"] for r in alone["results"]]:
            failures.append("exact results are not exactly the independent citation's records")
        if index.delta(q)["evidence_status"] != "unsupported_multi_citation":
            failures.append("delta selected one member of a multi-citation request")
    elif kind == "prose":
        for q, plain in item["requests"]:
            cites = parse_query_citations(q)
            if len(cites) != 1 or cites[0]["malformed"]:
                failures.append(f"'{q}' was read as a range or list")
                continue
            a, b = (index.answer(x, mode="exact", scope="current", k=10) for x in (q, plain))
            if [r["provision_id"] for r in a["results"]] != [r["provision_id"] for r in b["results"]] or gaps(a) != gaps(b):
                failures.append(f"'{q}' differs from '{plain}'")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_connector_operands(index, item):
    """Real-corpus guard for malformed operands after list separators and range operators. `list`: the valid first member is
    kept and the malformed operand (`and (-1)`, `; (z`) is its own unresolved member with a citation gap; answers are never
    evidence_found, exact returns only the valid member's records, and delta reports unsupported_multi_citation. `range`: the
    whole expression is one unresolved unsupported range (no exact records, delta unavailable without baseline or
    instruments). `independent`: a citation after the malformed expression still resolves. `explanation`: ordinary
    parentheticals after a connector are not operands."""
    failures, kind = [], item["kind"]
    scopes = ("current", "future", "both")
    gaps = lambda out: sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))
    if kind == "list":
        alone = index.answer(item["valid"], mode="exact", scope="current", k=10)
        for q in item["requests"]:
            cites = parse_query_citations(q)
            if len(cites) != 2 or cites[0]["canonical"] != item["valid"] or cites[0]["malformed"] or not cites[1]["malformed"] or cites[1]["canonical"]:
                failures.append(f"'{q}': the valid member or the malformed operand was not kept as its own member")
                continue
            label = cites[1]["label"]
            for mode in ("exact", "hybrid"):
                for scope in scopes:
                    for parts in (None, ["12:3"], ["12:999"]):
                        for k in (10, 1):
                            out = index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = f"'{q}' {mode}/{scope}/{parts or 'nofilter'}/k{k}"
                            if out["status"] == "evidence_found" or f"citation:{label}" not in gaps(out):
                                failures.append(f"{tag}: evidence_found or no gap for the malformed operand")
            ex = index.answer(q, mode="exact", scope="current", k=10)
            if [r["provision_id"] for r in ex["results"]] != [r["provision_id"] for r in alone["results"]]:
                failures.append(f"'{q}': exact results are not just the valid member's records")
            d = index.delta(q)
            if d["evidence_status"] != "unsupported_multi_citation" or d["baseline"] or d["instruments"]:
                failures.append(f"'{q}': delta did not report unsupported_multi_citation")
    elif kind == "range":
        for q in item["requests"]:
            cites = parse_query_citations(q)
            if len(cites) != 1 or cites[0]["malformed"] != "unsupported_range" or cites[0]["diagnostics"]["range"]["end_form"] != "malformed_endpoint":
                failures.append(f"'{q}': not one unsupported range with a malformed endpoint")
                continue
            label = cites[0]["label"]
            for mode in ("exact", "hybrid"):
                for scope in scopes:
                    for parts in (None, ["12:3"], ["12:999"]):
                        for k in (10, 1):
                            out = index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = f"'{q}' {mode}/{scope}/{parts or 'nofilter'}/k{k}"
                            if out["status"] == "evidence_found" or f"citation:{label}" not in gaps(out) or (mode == "exact" and out["results"]):
                                failures.append(f"{tag}: evidence_found, no gap, or exact returned records")
            d = index.delta(q)
            if d["evidence_status"] != "unavailable" or d["baseline"] or d["instruments"] or d["unavailable"]["reason"] != "unsupported_range":
                failures.append(f"'{q}': delta did not report the unsupported range")
    elif kind == "independent":
        q = item["request"]
        cites = parse_query_citations(q)
        if [c["canonical"] for c in cites if not c["malformed"]] != item["valid"]:
            failures.append("citations after the malformed expression were not preserved")
        out = index.answer(q, mode="exact", scope="current", k=10)
        if out["status"] == "evidence_found" or len(gaps(out)) != 1:
            failures.append("expected exactly one citation gap and no evidence_found")
    elif kind == "explanation":
        for q in item["requests"]:
            cites = parse_query_citations(q)
            if len(cites) != 1 or cites[0]["malformed"]:
                failures.append(f"'{q}': an ordinary parenthetical was read as an operand")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_four_digit_parts(index, item):
    """Real-corpus guard for four-digit part operands: lists are checked like part lists (`equivalent`, `delta`), ranges like
    part ranges (`unsupported`, `independent`, `prose`), against the fully qualified control and the literal expectations."""
    if item["kind"] in ("equivalent", "unresolved", "delta"):
        return check_query_lists(index, item)
    return check_query_ranges(index, item)


def _synthetic_title_index(as_of):
    """A small self-contained corpus: identical reversed ranges 3.9 through 3.4 under Titles 12 and 31, so candidate leakage across
    titles can be checked although the acquired corpus holds no unresolved range."""
    import hashlib
    import tempfile
    from . import build
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name) / "corpus"
    (root / "raw").mkdir(parents=True)

    def asset(name, data):
        (root / "raw" / name).write_bytes(data)
        return {"path": f"raw/{name}", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "retrieved_at": "2026-10-29T00:00:00+00:00"}
    html = lambda part: (f'<div class="part" id="part-3"><h1>PART 3</h1><div class="section" id="3.22"><h4>\u00a7 3.22 Adjustments.</h4>'
                         f'<div id="p-3.22(a)"><p class="indent-1" data-title="3.22(a)">(a) Title {part} text.</p></div></div></div>').encode()
    docs = {}
    for t in ("12", "31"):
        docs[f"ecfr:{t}:3"] = {"source_class": "regulation", "publisher": "eCFR", "citation": f"{t} CFR Part 3", "title": "T",
                               "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28", "asset": asset(f"e{t}.html", html(t))}
        xml = (f'<RULE><PREAMB><CFR>{t} CFR Part 3</CFR></PREAMB><SUPLINF><REGTEXT TITLE="{t}" PART="3"><AMDPAR>1. Remove &#xA7;&#xA7; 3.9 through 3.4.'
               f"</AMDPAR></REGTEXT></SUPLINF></RULE>").encode()
        docs[f"fr:2026-9{t}"] = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01", "document_type": "Rule",
                                 "effective_on": "2027-02-01", "action": "Final rule.", "document_number": f"2026-9{t}", "title": t, "asset": asset(f"r{t}.xml", xml)}
    (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
    conn, _ = build.build(root, str(Path(tmp.name) / "t.sqlite"), as_of, log=lambda *_: None)
    conn.close()
    return tmp, Index(str(Path(tmp.name) / "t.sqlite"), as_of)


def check_candidate_titles(index, item):
    """Guard for the unresolved ambiguous-range candidates in delta(), which belong to the resolved CFR title only. `synthetic`:
    on a self-contained corpus with identical reversed ranges under Titles 12 and 31, each case [request, expected candidate
    doc_keys] must hold (so cross-title leakage fails the check even though the acquired corpus has no such range). `corpus`:
    on the acquired corpus, titleless requests must equal their fully qualified forms and expose the same candidates."""
    failures, kind = [], item["kind"]
    if kind == "synthetic":
        tmp, idx = _synthetic_title_index(index.as_of)
        try:
            for request, expected in item["cases"]:
                d = idx.delta(request)
                got = sorted(c["doc_key"] for c in d["unresolved_candidates"])
                if got != sorted(expected):
                    failures.append(f"'{request}': candidates {got} != {sorted(expected)}")
                if d["legal_validity"] != "not_determined" or d["applicability_status"] != "not_assessed":
                    failures.append(f"'{request}': validity or applicability asserted")
            for request in item.get("ambiguous", []):
                d = idx.delta(request)
                if d["evidence_status"] != "ambiguous_title" or d["unresolved_candidates"]:
                    failures.append(f"'{request}': an ambiguous title exposed candidates")
            idx.conn.close()
        finally:
            tmp.cleanup()
    else:
        for plain, explicit, expected in item["pairs"]:
            a, b = index.delta(plain), index.delta(explicit)
            if a["unresolved_candidates"] != b["unresolved_candidates"] or sorted(c["doc_key"] for c in a["unresolved_candidates"]) != sorted(expected):
                failures.append(f"'{plain}': candidates differ from '{explicit}' or from the expected {expected}")
            if a["legal_validity"] != "not_determined" or a["applicability_status"] != "not_assessed":
                failures.append(f"'{plain}': validity or applicability asserted")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_explicit_title_sign(index, item):
    """Real-corpus guard for an explicit title in front of a section sign. `signed_equivalents`: `31 CFR \u00a7 1010.100` parses,
    resolves, answers (exact/hybrid, every scope and part filter, k=10 and 1) and deltas exactly like `31 CFR 1010.100`, with the
    original signed span preserved. `wrong_title`: a title that does not have the section keeps an unresolved citation gap, is
    never evidence_found, never returns another title's records, and delta has no baseline or instruments. `unsupported`: signed
    ranges stay one unsupported range. `titleless`: a genuinely titleless citation still matches its titled form."""
    kind, failures = item["kind"], []
    scopes = ("current", "future", "both")
    gaps = lambda out: sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))
    strip = lambda d: {k: v for k, v in d.items() if k not in ("citation", "requested_citation", "canonical_citation")}
    if kind == "signed_equivalents":
        for signed, plain in item["pairs"]:
            a, b = parse_query_citations(signed), parse_query_citations(plain)
            if [(c["title"], c["canonical"]) for c in a] != [(c["title"], c["canonical"]) for c in b] or not a or a[0]["requested"] != signed.split(",")[0].split(" and ")[0]:
                failures.append(f"'{signed}': title, canonical citation or requested span differs from '{plain}'")
                continue
            for scope in scopes:
                for parts in (None, ["12:3"], ["31:1010"]):
                    keys = ("citation", "resolved", "achieved", "reason", "by_scope", "_current_ids", "_future_ids")
                    ra, rb = index.resolve_citations(signed, scope, parts), index.resolve_citations(plain, scope, parts)
                    if [[r[k] for k in keys] for r in ra] != [[r[k] for k in keys] for r in rb]:
                        failures.append(f"'{signed}' {scope}/{parts}: resolver differs from '{plain}'")
            for mode in ("exact", "hybrid"):
                for scope in scopes:
                    for k in (10, 1):
                        x, y = (index.answer(q, mode=mode, scope=scope, k=k) for q in (signed, plain))
                        # hybrid also fuses lexical hits on the word CFR, which `C.F.R.` does not tokenize to: compare gaps and the evidence_found line
                        if gaps(x) != gaps(y) or (x["status"] == "evidence_found") != (y["status"] == "evidence_found") or (
                                mode == "exact" and (x["status"], [r["provision_id"] for r in x["results"]]) != (y["status"], [r["provision_id"] for r in y["results"]])):
                            failures.append(f"'{signed}' {mode}/{scope}/k{k}: answer differs from '{plain}'")
            if len(a) == 1 and strip(index.delta(signed)) != strip(index.delta(plain)):
                failures.append(f"'{signed}': delta differs from '{plain}'")
    elif kind == "wrong_title":
        for q in item["requests"]:
            label = parse_query_citations(q)[-1]["label"]
            for mode in ("exact", "hybrid"):
                for scope in scopes:
                    for k in (10, 1):
                        out = index.answer(q, mode=mode, scope=scope, k=k)
                        if out["status"] == "evidence_found" or f"citation:{label}" not in gaps(out):
                            failures.append(f"'{q}' {mode}/{scope}/k{k}: evidence_found or no gap for {label}")
                        if mode == "exact" and out["results"]:
                            failures.append(f"'{q}' {scope}: exact returned records through another title")
            d = index.delta(q)
            if d["evidence_status"] != "unavailable" or d["baseline"] or d["instruments"]:
                failures.append(f"'{q}': delta substituted another title's evidence")
    elif kind == "unsupported":
        return {**check_query_ranges(index, item), "id": item["id"]}
    elif kind == "titleless":
        return {**check_titleless_delta(index, item), "id": item["id"], "kind": kind}
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_citation_tokens(index, item):
    """Real-corpus guard for identifiers that run on with no space (`3.22.1`, `3.22_a`, `Part 3_a`). `unresolved`: each request is
    one unresolved `unread_identifier_continuation` expression (complete text kept, continuation identified), the resolver and
    exact retrieval return nothing, every exact/hybrid answer (scopes, part filters, k=10 and 1) carries its own citation gap and
    is never evidence_found, and delta is unavailable with no baseline or instruments. `mixed`: valid members are kept next to the
    unresolved one and delta reports unsupported_multi_citation. `controls`: valid forms (`3.22.`, lettered sections) parse as before."""
    failures, kind = [], item["kind"]
    reason = item.get("reason", "unread_identifier_continuation")
    scopes = ("current", "future", "both")
    gaps = lambda out: sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))
    if kind == "unresolved":
        for q in item["requests"]:
            cites = parse_query_citations(q)
            bad = [c for c in cites if c["malformed"] == reason]
            if len(cites) != 1 or len(bad) != 1 or not bad[0]["diagnostics"].get("unread_continuation") or bad[0]["diagnostics"]["accepted_valid_prefix"]:
                failures.append(f"'{q}': not read as one unresolved expression with its unread continuation")
                continue
            label = bad[0]["label"]
            for scope in scopes:
                for parts in (None, ["12:3"], ["12:999"]):
                    recs = index.resolve_citations(q, scope, parts)
                    if len(recs) != 1 or recs[0]["resolved"] or recs[0]["_current_ids"] or recs[0]["_future_ids"]:
                        failures.append(f"'{q}' {scope}/{parts}: the resolver accepted the prefix")
            for mode in ("exact", "hybrid"):
                for scope in scopes:
                    for parts in (None, ["12:3"], ["12:999"]):
                        for k in (10, 1):
                            out = index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = f"'{q}' {mode}/{scope}/{parts or 'nofilter'}/k{k}"
                            if out["status"] == "evidence_found" or f"citation:{label}" not in gaps(out) or (mode == "exact" and out["results"]):
                                failures.append(f"{tag}: evidence_found, no gap, or exact records")
            d = index.delta(q)
            if d["evidence_status"] != "unavailable" or d["baseline"] or d["instruments"] or d["unavailable"]["reason"] != reason:
                failures.append(f"'{q}': delta did not report the unresolved request")
    elif kind == "mixed":
        q = item["request"]
        cites = parse_query_citations(q)
        if [c["canonical"] for c in cites if not c["malformed"]] != item["valid"] or sum(1 for c in cites if c["malformed"] == reason) != 1:
            failures.append("valid members were not kept next to the unresolved one")
        for mode in ("exact", "hybrid"):
            out = index.answer(q, mode=mode, scope="current", k=10)
            if out["status"] == "evidence_found" or len(gaps(out)) != 1:
                failures.append(f"{mode}: expected exactly one citation gap and no evidence_found")
        if index.delta(q)["evidence_status"] != "unsupported_multi_citation":
            failures.append("delta did not report unsupported_multi_citation")
    elif kind == "controls":
        for q, want in item["requests"]:
            cites = parse_query_citations(q)
            if [c["canonical"] for c in cites] != want or any(c["malformed"] for c in cites):
                failures.append(f"'{q}': a valid form changed (got {[c['canonical'] for c in cites]})")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_list_recovery(index, item):
    """Real-corpus guard for list recovery after an unresolved identifier, hyphen tail or designator suffix. The request must parse to
    exactly the expected members [label, reason-or-null] (every requested member present, unresolved ones with their own reason),
    the resolver and every exact/hybrid answer (scopes, part filters, k=10 and 1) must keep a citation gap per unresolved member and
    never report evidence_found, the exact results must be only the valid members' records, and delta must report
    unsupported_multi_citation with every member."""
    failures = []
    q, want = item["request"], [tuple(m) for m in item["members"]]
    got = [(c["label"], c["malformed"]) for c in parse_query_citations(q)]
    if got != want:
        return {"id": item["id"], "kind": "list_recovery", "passed": False, "failures": [f"members {got} != {want}"]}
    gaps = lambda out: sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))
    unresolved = sorted(f"citation:{label}" for label, reason in want if reason)
    for scope in ("current", "future", "both"):
        recs = index.resolve_citations(q, scope)
        if [r["citation"] for r in recs] != [w[0] for w in want] or any(r["resolved"] or r["_current_ids"] or r["_future_ids"] for r, w in zip(recs, want) if w[1]):
            failures.append(f"{scope}: resolver records differ from the members or resolved an unresolved member")
    for mode in ("exact", "hybrid"):
        for scope in ("current", "future", "both"):
            for parts in (None, ["12:3"], ["12:999"]):
                for k in (10, 1):
                    out = index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                    tag = f"{mode}/{scope}/{parts or 'nofilter'}/k{k}"
                    if out["status"] == "evidence_found" or not set(unresolved) <= set(gaps(out)):
                        failures.append(f"{tag}: evidence_found or a missing gap")
    valid = " and ".join(c["canonical"] for c in parse_query_citations(q) if not c["malformed"])
    ex = index.answer(q, mode="exact", scope="current", k=10)
    ctl = index.answer(valid, mode="exact", scope="current", k=10) if valid else {"results": []}
    if [r["provision_id"] for r in ex["results"]] != [r["provision_id"] for r in ctl["results"]]:
        failures.append("exact results are not just the valid members' records")
    d = index.delta(q)
    if d["evidence_status"] != "unsupported_multi_citation" or [c["label"] for c in d["unavailable"]["citations"]] != [w[0] for w in want] or d["baseline"] or d["instruments"]:
        failures.append("delta did not report unsupported_multi_citation with every member")
    return {"id": item["id"], "kind": "list_recovery", "passed": not failures, "failures": failures[:10]}


def _synthetic_source_index(variant, as_of, drop=(), rule="complete"):
    """A small self-contained eCFR baseline (12 CFR 3, 217, 324, 252, each with a CET1 minimum) with 12 CFR 3 omitted from the
    manifest (`omitted`), rejected by hash (`rejected`) or parsing to nothing (`empty`), or complete (`complete`)."""
    import hashlib
    import tempfile
    from . import build
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name) / "corpus"
    (root / "raw").mkdir(parents=True)
    docs = {}
    for part in ("3", "217", "324", "252"):
        if part == "3" and variant == "omitted":
            continue
        html = "<div></div>" if (part == "3" and variant == "empty") else (
            f'<div class="part" id="part-{part}"><h1>PART {part}</h1><div class="section" id="{part}.10"><h4>\u00a7 {part}.10 Minimum capital.</h4>'
            f'<div id="p-{part}.10(a)"><p class="indent-1" data-title="{part}.10(a)">(a) A bank must maintain a common equity tier 1 capital ratio '
            f'(CET1) of at least 4.5 percent, and a capital conservation buffer.</p></div></div>'
            f'<div class="section" id="{part}.12"><h4>\u00a7 {part}.12 Framework.</h4><div id="p-{part}.12(a)"><p class="indent-1" data-title="{part}.12(a)">'
            f'(a) Filler paragraph of part {part}.</p></div></div></div>')
        for sec in drop:  # remove a whole section (e.g. "3.12") from its part
            if sec.split(".")[0] == part:
                a = html.index(f'<div class="section" id="{sec}">')
                html = html[:a] + html[html.index("</div></div>", a) + len("</div></div>"):]
        data = html.encode()
        (root / "raw" / f"e{part}.html").write_bytes(data)
        sha = hashlib.sha256(b"other bytes").hexdigest() if (part == "3" and variant == "rejected") else hashlib.sha256(data).hexdigest()
        docs[f"ecfr:12:{part}"] = {"source_class": "regulation", "publisher": "eCFR", "citation": f"12 CFR Part {part}", "title": "Capital",
                                   "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                                   "asset": {"path": f"raw/e{part}.html", "sha256": sha, "bytes": len(data), "retrieved_at": "2026-10-29T00:00:00+00:00"}}
    # The CBLR final rule the cbl_ratio coverage note names (`rule`: complete | omitted | rejected | empty | unsupported).
    if rule != "omitted":
        xml = ("<RULE></RULE>" if rule == "empty" else
               '<RULE><PREAMB><CFR>12 CFR Part 217</CFR></PREAMB><SUPLINF><REGTEXT TITLE="12" PART="217"><AMDPAR>1. Amend &#xA7; 217.12 by '
               'revising paragraph (a).</AMDPAR><SECTION><SECTNO>&#xA7; 217.12</SECTNO><SUBJECT>Community bank leverage ratio framework.</SUBJECT>'
               '<P>(a) Revised community bank leverage ratio text.</P></SECTION></REGTEXT></SUPLINF></RULE>').encode()
        name = "r.pdf" if rule == "unsupported" else "r.xml"
        (root / "raw" / name).write_bytes(xml)
        sha = hashlib.sha256(b"other bytes" if rule == "rejected" else xml).hexdigest()
        docs["fr:2026-08298"] = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "document_number": "2026-08298",
                                 "title": "Community bank leverage ratio", "document_type": "Rule", "publication_date": "2026-05-01",
                                 "effective_on": "2026-07-01", "action": "Final rule.",
                                 "asset": {"path": f"raw/{name}", "sha256": sha, "bytes": len(xml), "retrieved_at": "2026-10-29T00:00:00+00:00"}}
    (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
    conn, _ = build.build(root, str(Path(tmp.name) / "t.sqlite"), as_of, log=lambda *_: None)
    conn.close()
    return tmp, Index(str(Path(tmp.name) / "t.sqlite"), as_of)


def check_source_coverage(index, item):
    """Guard for index-source availability. `corpus`: the acquired corpus index has every declared eCFR baseline dependency indexed
    with usable provisions, no persisted dependency gap, and its topic answers carry no source gap. `degraded`: on small
    self-contained builds with 12 CFR 3 omitted, hash-rejected or empty, every matched acquired topic reports the unavailable source
    in its coverage gaps and is partial_evidence (missing_evidence when nothing remains), across modes, scopes, part filters and
    k=1, while a complete build stays evidence_found and exact citations keep their precision. Availability only."""
    failures, kind = [], item["kind"]
    gaps = lambda out: [g for g in out["coverage_gaps"] if g.get("coverage") == "source_unavailable"]
    if kind == "corpus":
        declared = json.loads((Path(__file__).with_name("topic_dependencies.json")).read_text())["required_baseline_sources"]
        status = {r[0]: r[1] for r in index.conn.execute("SELECT doc_key, status FROM source_status")}
        for topic, dep in declared.items():
            for key in dep["sources"]:
                if status.get(key) != "indexed":
                    failures.append(f"{topic}: dependency {key} is {status.get(key)}")
        if index.source_gaps:
            failures.append(f"persisted dependency gaps: {sorted(index.source_gaps)}")
        for q in item["queries"]:
            for mode in ("hybrid", "bm25"):
                out = index.answer(q, mode=mode, scope="current", k=10)
                if gaps(out) or out["status"] != "evidence_found":
                    failures.append(f"'{q}' {mode}: {out['status']} with source gaps {len(gaps(out))}")
    elif kind == "degraded":
        for variant in item["variants"]:
            tmp, idx = _synthetic_source_index(variant, index.as_of)
            try:
                complete = variant == "complete"
                for q in item["queries"]:
                    for mode in ("hybrid", "bm25", "topic"):
                        for scope in ("current", "future", "both"):
                            for parts in (None, ["12:3"], ["12:217"]):
                                out = idx.answer(q, mode=mode, scope=scope, parts=parts, k=1)
                                tag = f"{variant} '{q}' {mode}/{scope}/{parts or 'nofilter'}/k1"
                                if complete:
                                    if gaps(out) or (out["results"] and out["status"] != "evidence_found" and scope != "future"):
                                        failures.append(f"{tag}: a complete baseline lost evidence_found or gained a gap")
                                    continue
                                srcs = {m["doc_key"] for g in gaps(out) for m in g["unavailable_sources"]}
                                if srcs != {"ecfr:12:3"} or out["status"] != ("partial_evidence" if out["results"] else "missing_evidence"):
                                    failures.append(f"{tag}: {out['status']} with unavailable sources {sorted(srcs)}")
                if not complete:
                    only3 = idx.answer(item["queries"][0], mode="hybrid", scope="current", parts=["12:3"], k=10)
                    if only3["results"] or only3["status"] != "missing_evidence":
                        failures.append(f"{variant}: nothing remains but the status is {only3['status']}")
                    st = idx.conn.execute("SELECT status FROM source_status WHERE doc_key='ecfr:12:3'").fetchone()
                    if not st or st[0] != item["variants"][variant]:
                        failures.append(f"{variant}: persisted status {st and st[0]} != {item['variants'][variant]}")
                cit = idx.answer("12 CFR 217.10(a)", mode="exact", scope="current", k=1)
                if cit["status"] != "evidence_found" or not cit["citations"][0]["resolved"]:
                    failures.append(f"{variant}: exact citation precision changed")
                idx.conn.close()
            finally:
                tmp.cleanup()
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_required_sections(index, item):
    """Guard for section-level source availability (topic_dependencies.json `required_sections`). `corpus`: every declared section
    is an indexed section of an indexed document, no persisted `missing_required_section` gap, and the topic's answers carry no
    source gap. `degraded`: on small self-contained builds missing the named sections, the matched topic reports each missing
    `<document>#<section>` in its coverage gaps and is never evidence_found, across modes, scopes, part filters and k=1, while the
    complete build stays evidence_found. `isolation`: other topics and exact citations are unchanged by a missing section.
    Section presence only; not completeness, applicability or validity."""
    failures, kind = [], item["kind"]
    gaps = lambda out: [g for g in out["coverage_gaps"] if g.get("coverage") == "source_unavailable"]
    if kind == "corpus":
        declared = json.loads((Path(__file__).with_name("topic_dependencies.json")).read_text())["required_baseline_sources"]
        for topic, dep in declared.items():
            for key, sections in (dep.get("required_sections") or {}).items():
                for sec in sections:
                    if not index.conn.execute("""SELECT 1 FROM provision p JOIN source_document d ON d.id = p.doc_id
                                                 WHERE d.doc_key = ? AND p.kind = 'section' AND p.section_no = ?""", (key, sec)).fetchone():
                        failures.append(f"{topic}: required section {sec} of {key} is not indexed")
        if any(m["status"] == "missing_required_section" for ms in index.source_gaps.values() for m in ms):
            failures.append("persisted missing_required_section gaps")
        for q in item["queries"]:
            for mode in ("hybrid", "bm25"):
                out = index.answer(q, mode=mode, scope="current", k=10)
                if gaps(out) or out["status"] != "evidence_found":
                    failures.append(f"'{q}' {mode}: {out['status']} with source gaps {len(gaps(out))}")
    elif kind in ("degraded", "isolation"):
        for variant, drop in item["variants"].items():
            tmp, idx = _synthetic_source_index("complete", index.as_of, drop)
            try:
                expect = {f"ecfr:12:{s.split('.')[0]}#{s}" for s in drop}
                for q in item["queries"]:
                    for mode in ("hybrid", "bm25", "topic"):
                        for scope in ("current", "future", "both"):
                            for parts in (None, ["12:3"], ["12:217"]):
                                out = idx.answer(q, mode=mode, scope=scope, parts=parts, k=1)
                                tag = f"{variant} '{q}' {mode}/{scope}/{parts or 'nofilter'}/k1"
                                srcs = {m["doc_key"] for g in gaps(out) for m in g["unavailable_sources"]}
                                if kind == "isolation" or not drop:
                                    if srcs or (out["results"] and out["status"] != "evidence_found" and scope != "future"):
                                        failures.append(f"{tag}: unrelated or complete topic gained a gap or lost evidence_found")
                                elif srcs != expect or out["status"] == "evidence_found":
                                    failures.append(f"{tag}: {out['status']} with unavailable sources {sorted(srcs)}")
                cit = idx.answer("12 CFR 217.10(a)", mode="exact", scope="current", k=1)
                if cit["status"] != "evidence_found" or not cit["citations"][0]["resolved"]:
                    failures.append(f"{variant}: exact citation precision changed")
                idx.conn.close()
            finally:
                tmp.cleanup()
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_rulemaking_sources(index, item):
    """Guard for the Federal Register rulemaking sources a topic's coverage note names (topic_dependencies.json
    `required_rulemaking_sources`, e.g. cbl_ratio: fr:2026-08298), declared separately from the eCFR baseline. `corpus`: each declared
    document is an indexed source with usable provisions and no persisted rulemaking gap. `degraded`: on small self-contained builds where
    the rule is omitted, hash-rejected, unsupported-format or parses to nothing, a matched CBLR query reports the unavailable rulemaking
    source (kind `rulemaking`, with its persisted status) and is never evidence_found (partial_evidence when results remain, otherwise
    missing_evidence), across modes, scopes, representative part filters and k=1; a complete build stays evidence_found. `isolation`: other
    topics and exact citations to surviving provisions are unchanged. Availability only: not complete rulemaking coverage or operative effect."""
    failures, kind = [], item["kind"]
    gaps = lambda out: [g for g in out["coverage_gaps"] if g.get("coverage") == "source_unavailable"]
    if kind == "corpus":
        declared = json.loads((Path(__file__).with_name("topic_dependencies.json")).read_text()).get("required_rulemaking_sources") or {}
        status = {r[0]: (r[1], r[2]) for r in index.conn.execute("SELECT doc_key, status, usable_provisions FROM source_status")}
        for topic, dep in declared.items():
            for key in dep["sources"]:
                st = status.get(key)
                if not st or st[0] != "indexed" or not st[1]:
                    failures.append(f"{topic}: rulemaking source {key} is {st}")
        if any(m["source_kind"] == "rulemaking" for ms in index.source_gaps.values() for m in ms):
            failures.append("persisted rulemaking-source gaps")
        for q in item["queries"]:
            for mode in ("hybrid", "bm25"):
                out = index.answer(q, mode=mode, scope="both", k=10)
                if gaps(out) or out["status"] != "evidence_found":
                    failures.append(f"'{q}' {mode}: {out['status']} with source gaps {len(gaps(out))}")
    elif kind in ("degraded", "isolation"):
        for variant, expect_status in item["variants"].items():
            tmp, idx = _synthetic_source_index("complete", index.as_of, (), variant)
            try:
                for q in item["queries"]:
                    for mode in ("hybrid", "bm25", "topic"):
                        for scope in ("current", "future", "both"):
                            for parts in (None, ["12:3"], ["12:217"]):
                                out = idx.answer(q, mode=mode, scope=scope, parts=parts, k=1)
                                tag = f"{variant} '{q}' {mode}/{scope}/{parts or 'nofilter'}/k1"
                                found = [m for g in gaps(out) for m in g["unavailable_sources"]]
                                if kind == "isolation" or variant == "complete":
                                    if found or (out["results"] and out["status"] != "evidence_found" and scope != "future"):
                                        failures.append(f"{tag}: gained a gap or lost evidence_found")
                                    continue
                                srcs = {(m["doc_key"], m["source_kind"], m["status"]) for m in found}
                                if srcs != {("fr:2026-08298", "rulemaking", expect_status)} or out["status"] != ("partial_evidence" if out["results"] else "missing_evidence"):
                                    failures.append(f"{tag}: {out['status']} with unavailable sources {sorted(srcs)}")
                if kind == "degraded" and variant != "complete":
                    st = idx.conn.execute("SELECT status FROM source_status WHERE doc_key='fr:2026-08298'").fetchone()
                    if not st or st[0] != expect_status:
                        failures.append(f"{variant}: persisted status {st and st[0]} != {expect_status}")
                cit = idx.answer("12 CFR 217.10(a)", mode="exact", scope="current", k=1)
                if cit["status"] != "evidence_found" or not cit["citations"][0]["resolved"]:
                    failures.append(f"{variant}: exact citation precision changed")
                idx.conn.close()
            finally:
                tmp.cleanup()
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def _tamper_index(tmp, as_of, variant):
    """Rewrite a copy of a fresh synthetic index into a legacy/parent-era/mismatched one (the read-only original is a temporary file)."""
    import sqlite3
    path = str(Path(tmp.name) / "t.sqlite")
    conn = sqlite3.connect(path)
    keys = ("dependency_check_contract", "topic_dependencies_sha256", "topics_sha256")
    if variant == "legacy_absent_tables":
        conn.executescript("DROP TABLE source_status; DROP TABLE topic_source_gap;")
        conn.execute("DELETE FROM index_meta WHERE key IN (?,?,?)", keys)
        conn.execute("UPDATE index_meta SET value='p1-0.6' WHERE key='schema_version'")
    elif variant == "parent_era_p1_0_7":
        conn.execute("DELETE FROM index_meta WHERE key IN (?,?,?)", keys)
        conn.execute("UPDATE index_meta SET value='p1-0.7' WHERE key='schema_version'")
    elif variant == "absent_source_status":  # the persisted gap rows remain; the status table is gone
        conn.executescript("DROP TABLE source_status;")
    elif variant == "missing_metadata":
        conn.execute("DELETE FROM index_meta WHERE key IN (?,?,?)", keys)
    elif variant == "incomplete_metadata":
        conn.execute("DELETE FROM index_meta WHERE key='topics_sha256'")
    elif variant == "obsolete_contract":
        conn.execute("UPDATE index_meta SET value='p1-dependency-check-0' WHERE key='dependency_check_contract'")
    elif variant == "mismatched_topics":
        conn.execute("UPDATE index_meta SET value=? WHERE key='topics_sha256'", ("0" * 64,))
    elif variant == "mismatched_dependencies":
        conn.execute("UPDATE index_meta SET value=? WHERE key='topic_dependencies_sha256'", ("f" * 64,))
    else:
        raise ValueError(variant)
    conn.commit()
    conn.close()
    return Index(path, as_of)


def check_dependency_verification(index, item):
    """Guard for dependency-check provenance. `corpus`: the evaluated index carries the current dependency-check contract and exact
    input fingerprints (verified) and its topic answers carry no unverified-dependencies gap. `verified`: fresh synthetic builds, complete
    (evidence_found, no gap) and degraded (a confirmed `source_unavailable` gap, never `dependencies_unverified`). `unverified`: legacy
    indexes with no dependency tables, parent-era p1-0.7 indexes, and indexes with incomplete, obsolete or mismatched verification
    metadata report `dependencies_unverified` with the reason for every matched acquired topic across modes, scopes, part filters and k=1,
    never evidence_found (partial_evidence when results remain, otherwise missing_evidence), never as a confirmed missing source, while
    citation-only resolution and non-acquired topics keep their behavior. Verification status only; no source is asserted present or absent."""
    failures, kind = [], item["kind"]
    unver = lambda out: [g for g in out["coverage_gaps"] if g.get("coverage") == "dependencies_unverified"]
    unavail = lambda out: [g for g in out["coverage_gaps"] if g.get("coverage") == "source_unavailable"]
    if kind == "corpus":
        v = index.dependency_verification
        if v["status"] != "verified":
            failures.append(f"the evaluated index is {v['status']}: {v['reason']}")
        for q in item["queries"]:
            for mode in ("hybrid", "bm25"):
                out = index.answer(q, mode=mode, scope="both", k=10)
                if unver(out) or out["status"] != "evidence_found":
                    failures.append(f"'{q}' {mode}: {out['status']} with {len(unver(out))} unverified gaps")
    elif kind == "verified":
        for variant, builder in item["variants"].items():
            tmp, idx = _synthetic_source_index("complete", index.as_of, (), "complete" if variant == "fresh_complete" else "omitted")
            try:
                if idx.dependency_verification["status"] != "verified":
                    failures.append(f"{variant}: a fresh build is {idx.dependency_verification['status']}")
                for q in item["queries"]:
                    for mode in ("hybrid", "bm25", "topic"):
                        for scope in ("current", "future", "both"):
                            for parts in (None, ["12:3"], ["12:217"]):
                                out = idx.answer(q, mode=mode, scope=scope, parts=parts, k=1)
                                tag = f"{variant} '{q}' {mode}/{scope}/{parts or 'nofilter'}/k1"
                                if unver(out):
                                    failures.append(f"{tag}: a verified index reported unverified dependencies")
                                if variant == "fresh_degraded" and not unavail(out):
                                    failures.append(f"{tag}: the confirmed missing rule was not reported")
                                if variant == "fresh_complete" and (unavail(out) or (out["results"] and out["status"] != "evidence_found" and scope != "future")):
                                    failures.append(f"{tag}: a complete verified build lost evidence_found")
                idx.conn.close()
            finally:
                tmp.cleanup()
    elif kind == "unverified":
        for variant, reason in item["variants"].items():
            tmp, base = _synthetic_source_index("complete", index.as_of)
            base.conn.close()
            idx = _tamper_index(tmp, index.as_of, variant)
            try:
                v = idx.dependency_verification
                if v["status"] != "unverified" or v["reason"] != reason:
                    failures.append(f"{variant}: verification {v['status']}/{v['reason']}, expected unverified/{reason}")
                for q in item["queries"]:
                    for mode in ("hybrid", "bm25", "topic"):
                        for scope in ("current", "future", "both"):
                            for parts in (None, ["12:3"], ["12:217"]):
                                out = idx.answer(q, mode=mode, scope=scope, parts=parts, k=1)
                                tag = f"{variant} '{q}' {mode}/{scope}/{parts or 'nofilter'}/k1"
                                got = unver(out)
                                if not got or any(g["verification"]["reason"] != reason for g in got):
                                    failures.append(f"{tag}: missing or wrong unverified-dependencies diagnostic")
                                if unavail(out):
                                    failures.append(f"{tag}: unknown verification was reported as a confirmed missing source")
                                if out["status"] != ("partial_evidence" if out["results"] else "missing_evidence"):
                                    failures.append(f"{tag}: status {out['status']}")
                cit = idx.answer("12 CFR 217.10(a)", mode="exact", scope="current", k=1)
                if cit["status"] != "evidence_found" or not cit["citations"][0]["resolved"] or unver(cit):
                    failures.append(f"{variant}: citation-only resolution changed")
                other = idx.answer("CECL under ASC 326", mode="hybrid", scope="current", k=5)
                if unver(other):
                    failures.append(f"{variant}: a non-acquired topic gained an unverified-dependencies gap")
                if (cit["applicability_status"], idx.delta("12 CFR 217.10")["legal_validity"]) != ("not_assessed", "not_determined"):
                    failures.append(f"{variant}: applicability or legal validity changed")
            finally:
                idx.conn.close()
                tmp.cleanup()
    elif kind == "unverified_persisted":
        import hashlib
        for src_name, (ecfr_variant, drop, rule) in item["sources"].items():
            for tamper, reason in item["tampers"].items():
                tmp, base = _synthetic_source_index(ecfr_variant, index.as_of, tuple(drop), rule)
                confirmed = bool(base.source_gaps) and base.dependency_verification["status"] == "verified"
                base.conn.close()
                if not confirmed:
                    failures.append(f"{src_name}: the verified degraded build has no persisted gap rows to test")
                idx = _tamper_index(tmp, index.as_of, tamper)
                dbfile = Path(tmp.name) / "t.sqlite"
                before = hashlib.sha256(dbfile.read_bytes()).hexdigest()
                try:
                    v = idx.dependency_verification
                    if v["status"] != "unverified" or v["reason"] != reason:
                        failures.append(f"{src_name}/{tamper}: verification {v['status']}/{v['reason']}, expected unverified/{reason}")
                    if not idx.unverified_source_observations:
                        failures.append(f"{src_name}/{tamper}: persisted gap rows were not kept as unverified observations")
                    for q in item["queries"]:
                        for mode in ("hybrid", "bm25", "topic"):
                            for scope in ("current", "future", "both"):
                                for parts in (None, ["12:3"], ["12:217"]):
                                    out = idx.answer(q, mode=mode, scope=scope, parts=parts, k=1)
                                    tag = f"{src_name}/{tamper} '{q}' {mode}/{scope}/{parts or 'nofilter'}/k1"
                                    if unavail(out) or out["index"]["unavailable_topic_sources"]:
                                        failures.append(f"{tag}: a persisted row was promoted to a confirmed source gap")
                                    got = unver(out)
                                    if not got:
                                        failures.append(f"{tag}: no unverified-dependencies diagnostic")
                                    obs = [o for g in got for o in g["unverified_source_observations"]]
                                    if not obs or any(o["confirmed"] or o["unverified_reason"] != reason for o in obs):
                                        failures.append(f"{tag}: unverified observations missing, confirmed, or without verification provenance")
                                    if out["status"] != ("partial_evidence" if out["results"] else "missing_evidence"):
                                        failures.append(f"{tag}: status {out['status']}")
                    cit = idx.answer("12 CFR 217.10(a)", mode="exact", scope="current", k=1)
                    if cit["status"] != "evidence_found" or not cit["citations"][0]["resolved"] or unver(cit) or unavail(cit):
                        failures.append(f"{src_name}/{tamper}: citation-only resolution changed")
                    if (cit["applicability_status"], idx.delta("12 CFR 217.10")["legal_validity"]) != ("not_assessed", "not_determined"):
                        failures.append(f"{src_name}/{tamper}: applicability or legal validity changed")
                finally:
                    idx.conn.close()
                    if hashlib.sha256(dbfile.read_bytes()).hexdigest() != before:
                        failures.append(f"{src_name}/{tamper}: reading the index changed its bytes")
                    tmp.cleanup()
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_lowercase_paragraph_clause(index, item):
    """Guard for explicit amendatory location clauses "In § SECTION, in paragraph PATH, remove/revise/add ...". `positive`: the grammar names
    PATH for each listed instruction text (including inline-emphasis spacing), and, where the acquired index holds the document, delta
    support for the requested path comes from the named instrument only (other instruments of the section stay section context).
    `negative`: cross-references, quoted replacement text, several sections and malformed suffixes name nothing, and the other acquired
    instrument stays section context. `exact_citation`: the exact future citation resolves and lists the named instrument. Textual evidence only."""
    from . import fedreg
    failures, kind = [], item["kind"]
    have = {r[0] for r in index.conn.execute("SELECT doc_key FROM source_document")}
    if kind == "positive":
        for t in item["texts"]:
            got = fedreg.named_paragraph_paths(t["text"])
            if got != t["paths"]:
                failures.append(f"'{t['text'][:60]}': named {got}, expected {t['paths']}")
    elif kind == "negative":
        for text in item["texts"]:
            got = fedreg.named_paragraph_paths(text)
            if got:
                failures.append(f"'{text[:60]}': named {got}, expected nothing")
    checked = skipped = 0  # S7-P1-78: delta expectations whose document is absent from the index are skipped, and now counted
    if kind in ("positive", "negative") and item.get("delta"):
        for d in item["delta"]:
            if d["doc_key"] not in have:
                skipped += 1
                continue
            checked += 1
            inst = {g["doc_key"]: g["paragraph_precision"] for g in index.delta(d["citation"])["instruments"]}
            for doc, want in d["expect"].items():
                got = inst.get(doc)
                if got is None:
                    if want != "absent":
                        failures.append(f"{d['citation']}: {doc} is not an instrument")
                    continue
                if want == "paragraph" and not (got["resolved"] and got["achieved"] == "paragraph" and got["supported_by"]):
                    failures.append(f"{d['citation']}: {doc} not supported at paragraph level ({got['achieved']})")
                if want == "section_context" and (got["resolved"] or got["achieved"] != "section_only" or got["reason"] != "paragraph_precision_unresolved"):
                    failures.append(f"{d['citation']}: {doc} should stay section context ({got['achieved']}, {got['reason']})")
    elif kind == "exact_citation":
        for d in item["delta"]:
            if d["doc_key"] not in have:
                skipped += 1
                continue
            checked += 1
            r = index.resolve_citations(d["citation"], "future")[0]
            out = index.answer(d["citation"], mode="exact", scope="future", k=10)
            if not r["resolved"] or d["doc_key"] not in {x["doc_key"] for x in out["results"]} or \
                    {x["doc_key"] for x in r.get("future_paragraph_support", [])} != {d["doc_key"]}:
                failures.append(f"{d['citation']}: future citation did not resolve to {d['doc_key']}")
            if out["applicability_status"] != "not_assessed":
                failures.append("applicability_status changed")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10],
            "deltas_checked": checked, "deltas_skipped": skipped}


def check_paragraph_evidence_integrity(index, item):
    """Guard for paragraph evidence. `grammar`: an explicit location clause names a path only for a bare section number ("In § 265.7, in
    paragraph (d)(7)(ii)(B)(3), remove"); "In § 265.7(d), in paragraph (7), remove" establishes neither (7) nor (d)(7). `ranking`: where the
    acquired index holds the supporting instrument, exact and hybrid answers in future and both scopes keep its supporting unit first at
    k=1 and at larger budgets, a newer section-context unit does not displace it, an excluded-part filter returns nothing, an unsupported
    path stays unresolved, and current scope returns no Federal Register unit. Textual evidence only; no legal effect is determined."""
    from . import fedreg
    failures, kind = [], item["kind"]
    if kind == "grammar":
        for t in item["texts"]:
            got = fedreg.named_paragraph_paths(t["text"])
            if got != t["paths"]:
                failures.append(f"'{t['text'][:60]}': named {got}, expected {t['paths']}")
    elif kind == "ranking":
        have = {r[0] for r in index.conn.execute("SELECT doc_key FROM source_document")}
        for d in item["citations"]:
            if d["doc_key"] not in have:
                continue
            for mode in ("exact", "hybrid"):
                for scope in ("future", "both"):
                    for k in (1, 2, 5):
                        out = index.answer(d["citation"], mode=mode, scope=scope, k=k)
                        tag = f"{d['citation']} {mode}/{scope}/k{k}"
                        if not out["results"] or out["results"][0]["locator"] != d["locator"]:
                            failures.append(f"{tag}: first result is {out['results'][0]['locator'] if out['results'] else None}, expected {d['locator']}")
            blocked = index.answer(d["citation"], mode="exact", scope="future", k=1, parts=[d["excluded_part"]])
            if blocked["results"]:
                failures.append(f"{d['citation']}: an excluded-part filter still returned {len(blocked['results'])} results")
            cur = index.answer(d["citation"], mode="exact", scope="current", k=5)
            if any(r["doc_key"].startswith("fr:") for r in cur["results"]):
                failures.append(f"{d['citation']}: current scope returned a Federal Register unit")
            inst = {g["doc_key"]: g["paragraph_precision"] for g in index.delta(d["citation"])["instruments"]}
            for other in d.get("section_context", []):
                q = inst.get(other)
                if q is not None and (q["resolved"] or q["achieved"] != "section_only"):
                    failures.append(f"{d['citation']}: {other} should stay section context")
            un = index.resolve_citations(d["unsupported"], "future")[0]
            if un["resolved"]:
                failures.append(f"{d['unsupported']}: an unsupported path resolved")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_relative_action_paragraphs(index, item):
    """Guard for paragraphs named inside a paragraph-qualified section location ("In § 265.7(d), revise paragraph (7)"). `grammar`: such a
    relative action establishes neither a top-level path nor an inferred joined one (revise/remove/add, nested enclosing designators,
    whitespace variants), while fully explicit paths after a bare section location are still named. `stored`: every attributed path stored in
    the evaluated index is still named by its own instruction under the current grammar (a stale or over-eager attribution fails), and no
    stored path comes from an instruction whose governing location is paragraph-qualified. Textual evidence only; no legal effect is determined."""
    from . import fedreg
    failures, kind = [], item["kind"]
    if kind == "grammar":
        for t in item["texts"]:
            got = fedreg.named_paragraph_paths(t["text"])
            if got != t["paths"]:
                failures.append(f"'{t['text'][:70]}': named {got}, expected {t['paths']}")
    elif kind == "stored":
        checked = 0
        for doc, loc, instruction, ev in index.conn.execute(
                """SELECT d.doc_key, p.locator, p.instruction, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id
                   WHERE p.kind='amendment' AND p.instruction IS NOT NULL"""):
            paths = (json.loads(ev or "{}")).get("paths") or []
            if not paths:
                continue
            checked += 1
            named = fedreg.named_paragraph_paths(instruction)
            stale = [p for p in paths if p not in named]
            if stale:
                failures.append(f"{doc} {loc}: stored {stale} is not named by its instruction under the current grammar")
        if not checked:
            failures.append("no attributed paragraph paths were found to check")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_enumerated_relative_paragraphs(index, item):
    """Guard for enumerated operands under a paragraph-qualified location ("Amend § 35.4(a)(2) by: a. In paragraph (i), removing …; b. …").
    `grammar`: literal expected paths for colon, semicolon, sentence and enumerator variants (relative operands name nothing; a new location
    citation or a bare-section location still names explicit paths; reference citations and quoted text do not change the context).
    `acquired`: the hash-verified acquired instruction keeps its operands as section context: the source sha256 matches the pinned value, the
    operands read independently of the parser equal the literal list, the stored attribution is the literal expected paths, per-instrument
    delta is section-only for each operand, exact and hybrid answers in future and both scope at small budgets are not evidence_found, and an
    excluded-part filter returns nothing. Expected values are literals, never the parser's own output. Textual evidence only."""
    from . import fedreg
    failures, kind = [], item["kind"]
    if kind == "grammar":
        for t in item["texts"]:
            got = fedreg.named_paragraph_paths(t["text"])
            if got != t["paths"]:
                failures.append(f"'{t['text'][:70]}': named {got}, expected {t['paths']}")
    elif kind == "acquired":
        row = index.conn.execute("SELECT sha256 FROM source_document WHERE doc_key=?", (item["doc_key"],)).fetchone()
        if row is not None:
            if row[0] != item["sha256"]:
                failures.append(f"{item['doc_key']}: source sha256 {row[0]} is not the pinned {item['sha256']}")
            rec = index.conn.execute("""SELECT p.instruction, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id
                                        WHERE d.doc_key=? AND p.locator=?""", (item["doc_key"], item["locator"])).fetchone()
            if rec is None or not rec[0].startswith(item["instruction_prefix"]):
                failures.append(f"{item['locator']}: instruction not found or different")
            else:
                operands = ["(" + m + ")" for m in re.findall(item["operand_regex"], rec[0])]
                if operands != item["operands"]:
                    failures.append(f"operands {operands} != {item['operands']}")
                stored = json.loads(rec[1] or "{}").get("paths")
                if stored != item["expected_paths"]:
                    failures.append(f"stored paths {stored} != {item['expected_paths']}")
                for op in item["operands"]:
                    for template in item["citations"]:
                        cite = template.replace("{op}", op)
                        inst = {g["doc_key"]: g["paragraph_precision"] for g in index.delta(cite)["instruments"]}
                        q = inst.get(item["doc_key"])
                        if q is None or q["resolved"] or q["achieved"] != "section_only" or q["supported_by"]:
                            failures.append(f"{cite}: {item['doc_key']} is not section context only")
                        for mode in ("exact", "hybrid"):
                            for scope in ("future", "both"):
                                for k in (1, 2):
                                    if index.answer(cite, mode=mode, scope=scope, k=k)["status"] == "evidence_found":
                                        failures.append(f"{cite} {mode}/{scope}/k{k}: evidence_found without paragraph support")
                        if index.answer(cite, mode="exact", scope="future", k=1, parts=[item["excluded_part"]])["results"]:
                            failures.append(f"{cite}: an excluded-part filter still returned results")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_passive_location_paragraphs(index, item):
    """Guard for paragraph-qualified passive section locations ("§ 265.7(d) is amended by revising paragraph (7)", "Section 265.7(d)(7) is
    amended by removing paragraph (ii)"). `grammar`: literal expected paths for nested, enumerated, whitespace, quoted, reference, bare-section and
    context-reset variants (a relative action names nothing; a new bare passive location restarts the context and keeps explicit paths).
    `stored`: in the evaluated index, no instruction with a paragraph-qualified passive location stores a path its own text does not name
    under the current grammar. Textual evidence only; no legal effect is determined."""
    from . import fedreg
    failures, kind = [], item["kind"]
    if kind == "grammar":
        for t in item["texts"]:
            got = fedreg.named_paragraph_paths(t["text"])
            if got != t["paths"]:
                failures.append(f"'{t['text'][:70]}': named {got}, expected {t['paths']}")
    elif kind == "stored":
        for doc, loc, instruction, ev in index.conn.execute(
                """SELECT d.doc_key, p.locator, p.instruction, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id
                   WHERE p.kind='amendment' AND p.instruction IS NOT NULL"""):
            if not any(m.group(1).strip() for m in fedreg.PASSIVE_LOCATION_CITE.finditer(fedreg.parsing_view(fedreg.QUOTED.sub(" \u2039q\u203a ", instruction)))):
                continue
            paths = (json.loads(ev or "{}")).get("paths") or []
            named = fedreg.named_paragraph_paths(instruction)
            stale = [p for p in paths if p not in named]
            if stale:
                failures.append(f"{doc} {loc}: stored {stale} is not named under the current grammar")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_nested_by_paragraph_scope(index, item):
    """Guard for a child paragraph action introduced by "by" under an explicit parent paragraph ("In § 265.7, revise paragraph (d) by adding
    paragraph (7)"). `grammar`: literal expected paths: the named parent stays, a single-designator child is not a top-level path and
    (d)(7) is not inferred, in active and passive forms and with whitespace variants, while sibling actions, fully explicit paths, new
    locations, quoted text and references keep their behavior. `stored`: in the evaluated index, no instruction with a "paragraph (x) by
    <verb>ing paragraph (y)" construction stores a path its own text does not name under the current grammar. Textual evidence only."""
    from . import fedreg
    failures, kind = [], item["kind"]
    if kind == "grammar":
        for t in item["texts"]:
            got = fedreg.named_paragraph_paths(t["text"])
            if got != t["paths"]:
                failures.append(f"'{t['text'][:70]}': named {got}, expected {t['paths']}")
    elif kind == "stored":
        construction = re.compile(r"paragraphs?\s+\([^)]*\)(?:\([^)]*\))*\s+by\s+\w+\s+(?:new\s+)?paragraphs?\b", re.I)
        for doc, loc, instruction, ev in index.conn.execute(
                """SELECT d.doc_key, p.locator, p.instruction, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id
                   WHERE p.kind='amendment' AND p.instruction IS NOT NULL"""):
            if not construction.search(fedreg.parsing_view(instruction)):
                continue
            paths = (json.loads(ev or "{}")).get("paths") or []
            stale = [p for p in paths if p not in fedreg.named_paragraph_paths(instruction)]
            if stale:
                failures.append(f"{doc} {loc}: stored {stale} is not named under the current grammar")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_nested_to_paragraph_scope(index, item):
    """Guard for an immediate infinitive child action under an explicit parent paragraph ("In § 265.7, revise paragraph (d) to add paragraph (7)").
    `grammar`: literal expected paths: the named parent stays; a single-designator or non-letter-led child is not a path and (d)(7) is not
    inferred (to add/remove/revise, nested parents, child lists, whitespace variants), while a complete-path child, sibling actions, sentence
    and section-location resets, quoted text, references and the "by" construction keep their behavior. `stored`: in the evaluated index no
    "paragraph (x) to <verb> paragraph (y)" instruction stores a path its own text does not name under the current grammar. Textual evidence only."""
    from . import fedreg
    failures, kind = [], item["kind"]
    if kind == "grammar":
        for t in item["texts"]:
            got = fedreg.named_paragraph_paths(t["text"])
            if got != t["paths"]:
                failures.append(f"'{t['text'][:70]}': named {got}, expected {t['paths']}")
    elif kind == "stored":
        construction = re.compile(r"paragraphs?\s+\([^)]*\)(?:\([^)]*\))*\s+to\s+(?:add|remove|revise|reserve|delete|redesignate)\s+(?:new\s+)?paragraphs?\b", re.I)
        for doc, loc, instruction, ev in index.conn.execute(
                """SELECT d.doc_key, p.locator, p.instruction, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id
                   WHERE p.kind='amendment' AND p.instruction IS NOT NULL"""):
            if not construction.search(fedreg.parsing_view(instruction)):
                continue
            paths = (json.loads(ev or "{}")).get("paths") or []
            stale = [p for p in paths if p not in fedreg.named_paragraph_paths(instruction)]
            if stale:
                failures.append(f"{doc} {loc}: stored {stale} is not named under the current grammar")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_paragraph_scope_continuity(index, item):
    """Guard for paragraph scope that must survive coordination and an explicit paragraph location clause. `grammar`: literal expected paths:
    coordinated child actions sharing one governing "by"/"to" ("revise paragraph (d) by adding paragraph (7) and revising paragraph (8)",
    comma and "or" variants, repeated connectors) and actions after a capitalized "In paragraph (d)," clause name only the parent, while a child
    written as a complete path, independent siblings, enumerator/sentence/section resets, references and quoted text keep their behavior.
    `stored`: in the evaluated index no instruction with such a construction stores a path its own text does not name under the current
    grammar. Relative paths are never composed. Textual evidence only."""
    from . import fedreg
    failures, kind = [], item["kind"]
    if kind == "grammar":
        for t in item["texts"]:
            got = fedreg.named_paragraph_paths(t["text"])
            if got != t["paths"]:
                failures.append(f"'{t['text'][:70]}': named {got}, expected {t['paths']}")
    elif kind == "stored":
        construction = re.compile(r"\bIn\s+paragraphs?\s+\([^)]*\)[^.;:]*?,\s*(?:and\s+)?\w+\s+(?:new\s+)?paragraphs?\b|"
                                  r"\b(?:by|to)\s+\w+\s+(?:new\s+)?paragraphs?\s+\([^)]*\)(?:\([^)]*\))*\s*(?:,|and|or)\s*(?:(?:and|or|by|to)\s+)?\w+\s+(?:new\s+)?paragraphs?\b", re.I)
        for doc, loc, instruction, ev in index.conn.execute(
                """SELECT d.doc_key, p.locator, p.instruction, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id
                   WHERE p.kind='amendment' AND p.instruction IS NOT NULL"""):
            if not construction.search(fedreg.parsing_view(instruction)):
                continue
            paths = (json.loads(ev or "{}")).get("paths") or []
            stale = [p for p in paths if p not in fedreg.named_paragraph_paths(instruction)]
            if stale:
                failures.append(f"{doc} {loc}: stored {stale} is not named under the current grammar")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_enumerated_paragraph_location(index, item):
    """Guard for the scope of an explicit paragraph location clause over the subordinate enumerated list it introduces ("in paragraph (d), amend
    as follows: 1. Revise paragraph (7); 2. Remove paragraph (8)", "a. In paragraph (d): i. Revise paragraph (7); ii. Remove paragraph (8)").
    `grammar`: literal expected paths: such a list names only the parent (lowercase Roman, digit and letter enumerators, whitespace variants),
    while an enumerator of another class (`b.` after `i., ii.`), a sentence boundary, a new section location, complete-path operands,
    references and quoted text keep their behavior and nothing is composed. `stored`: in the evaluated index no instruction with a
    colon-introduced subordinate list stores a path its own text does not name under the current grammar. Textual evidence only."""
    from . import fedreg
    failures, kind = [], item["kind"]
    if kind == "grammar":
        for t in item["texts"]:
            got = fedreg.named_paragraph_paths(t["text"])
            if got != t["paths"]:
                failures.append(f"'{t['text'][:70]}': named {got}, expected {t['paths']}")
    elif kind == "stored":
        construction = re.compile(r"\bparagraphs?\s+\([^)]*\)(?:\([^)]*\))*\s*(?:,\s*(?:amend|revis\w*)[^.;:]*)?:\s+(?:\d{1,2}|[A-Za-z]{1,4})\.\s+", re.I)
        for doc, loc, instruction, ev in index.conn.execute(
                """SELECT d.doc_key, p.locator, p.instruction, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id
                   WHERE p.kind='amendment' AND p.instruction IS NOT NULL"""):
            if not construction.search(fedreg.parsing_view(instruction)):
                continue
            paths = (json.loads(ev or "{}")).get("paths") or []
            stale = [p for p in paths if p not in fedreg.named_paragraph_paths(instruction)]
            if stale:
                failures.append(f"{doc} {loc}: stored {stale} is not named under the current grammar")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_parenthesized_paragraph_list(index, item):
    """Guard for subordinate lists whose enumerators are parenthesized ("in paragraph (d), amend as follows: (1) Revise paragraph (7); (2) Remove
    paragraph (8)", "a. In paragraph (d): (i) Revise paragraph (7); (ii) Remove paragraph (8)"). `grammar`: literal expected paths: such a list
    names only the parent (digit, Roman and letter enumerators, whitespace variants), while a list of mixed enumerator forms or classes, a
    sentence start, a new section location, complete-path operands, references and quoted text keep their behavior and nothing is composed.
    `stored`: in the evaluated index no instruction with a parenthesized-enumerator list stores a path its own text does not name under the
    current grammar. Textual evidence only."""
    from . import fedreg
    failures, kind = [], item["kind"]
    if kind == "grammar":
        for t in item["texts"]:
            got = fedreg.named_paragraph_paths(t["text"])
            if got != t["paths"]:
                failures.append(f"'{t['text'][:70]}': named {got}, expected {t['paths']}")
    elif kind == "stored":
        construction = re.compile(r":\s+\(\s*(?:\d{1,2}|[A-Za-z]{1,4})\s*\)\s+[A-Za-z]", re.I)
        for doc, loc, instruction, ev in index.conn.execute(
                """SELECT d.doc_key, p.locator, p.instruction, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id
                   WHERE p.kind='amendment' AND p.instruction IS NOT NULL"""):
            if not construction.search(fedreg.parsing_view(instruction)):
                continue
            paths = (json.loads(ev or "{}")).get("paths") or []
            stale = [p for p in paths if p not in fedreg.named_paragraph_paths(instruction)]
            if stale:
                failures.append(f"{doc} {loc}: stored {stale} is not named under the current grammar")
    return {"id": item["id"], "kind": kind, "passed": not failures, "failures": failures[:10]}


def check_triple_letter_paragraph_list(index, item):
    """Guard for added-paragraph lists of tripled letters ("Adding paragraphs (nnn), (ooo), ... and (xxx)", S7-P1-77). `positive`: the grammar
    names every listed path, single- and triple-letter members alike, and, where the acquired index holds the document, delta support for a
    later member comes from the instruments that name it (others stay section context). `negative`: quoted replacement text, a
    cross-reference and a malformed designator name nothing. `stored`: for each unit whose document the index holds, the locator row must
    exist ("document indexed but locator not found" otherwise) and its stored explicit_paragraphs equal the expected paths; units of
    documents absent from the index are counted as skipped (S7-P1-78), never silently passed. Textual evidence only."""
    if item["kind"] in ("positive", "negative"):
        return {**check_lowercase_paragraph_clause(index, item), "id": item["id"], "kind": item["kind"]}
    failures, checked, skipped = [], 0, 0
    for u in item["units"]:
        if index.conn.execute("SELECT 1 FROM source_document WHERE doc_key=?", (u["doc_key"],)).fetchone() is None:
            skipped += 1  # the document is not in this index
            continue
        checked += 1
        row = index.conn.execute(
            """SELECT p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id
               WHERE d.doc_key=? AND p.locator=? AND p.kind='amendment'""", (u["doc_key"], u["locator"])).fetchone()
        if row is None:
            failures.append(f"{u['locator']}: document indexed but locator not found")
            continue
        got = json.loads(row[0] or "{}")
        if (got.get("status"), got.get("paths")) != ("attributed", u["paths"]):
            failures.append(f"{u['locator']}: stored {got.get('status')} {got.get('paths')} != attributed {u['paths']}")
    return {"id": item["id"], "kind": item["kind"], "passed": not failures, "failures": failures[:10],
            "units_checked": checked, "units_skipped": skipped}

SKIP_COUNTED_GROUPS = ("lowercase_paragraph_clause", "triple_letter_paragraph_list")


def skipped_absent_document(reg):
    """Roll-up of expectations skipped because their document is absent from the evaluated index (S7-P1-79): deltas_skipped plus
    units_skipped per counting group. Reporting only; no pass/fail outcome depends on it."""
    groups = {}
    for group in SKIP_COUNTED_GROUPS:
        items = reg.get(group) or []
        groups[group] = {"deltas_skipped": sum(x.get("deltas_skipped", 0) for x in items),
                         "units_skipped": sum(x.get("units_skipped", 0) for x in items)}
    return {"total": sum(g["deltas_skipped"] + g["units_skipped"] for g in groups.values()), "groups": groups}


def run_regressions(index, path):
    spec = json.loads(Path(path).read_text())
    out = {"version": spec["version"], "task": spec["task"],
           "delta": [check_boundary_delta(index, d) for d in spec["delta"]],
           "relationship_integrity": check_relationship_integrity(index, spec["relationship_integrity"]),
           "citation_scope": [check_citation_scope(index, c) for c in spec["citation_scope"]],
           "status": [check_status_regression(index, x) for x in spec.get("status", [])],
           "citation_completeness": [check_citation_completeness(index, x) for x in spec.get("citation_completeness", [])],
           "mixed_source": [check_mixed_source(index, x) for x in spec.get("mixed_source", [])],
           "delta_precision": [check_delta_precision(index, x) for x in spec.get("delta_precision", [])],
           "instruction_boundary": [check_instruction_boundary(index, x) for x in spec.get("instruction_boundary", [])],
           "current_agreement": [check_current_agreement(index, x) for x in spec.get("current_agreement", [])],
           "amendatory_lists": [check_amendatory_lists(index, x) for x in spec.get("amendatory_lists", [])],
           "designator_integrity": [check_designator_integrity(index, x) for x in spec.get("designator_integrity", [])],
           "query_citations": [check_query_citations(index, x) for x in spec.get("query_citations", [])],
           "suffix_integrity": [check_suffix_integrity(index, x) for x in spec.get("suffix_integrity", [])],
           "appendix_evidence": [check_appendix_evidence(index, x) for x in spec.get("appendix_evidence", [])],
           "titleless_delta": [check_titleless_delta(index, x) for x in spec.get("titleless_delta", [])],
           "query_lists": [check_query_lists(index, x) for x in spec.get("query_lists", [])],
           "query_ranges": [check_query_ranges(index, x) for x in spec.get("query_ranges", [])],
           "connector_operands": [check_connector_operands(index, x) for x in spec.get("connector_operands", [])],
           "part_ranges": [check_query_ranges(index, x) for x in spec.get("part_ranges", [])],  # same contract as section/paragraph ranges
           "part_to_ranges": [check_query_ranges(index, x) for x in spec.get("part_to_ranges", [])],
           "part_lists": [check_query_lists(index, x) for x in spec.get("part_lists", [])],  # same list-completeness contract, for part lists
           "four_digit_parts": [check_four_digit_parts(index, x) for x in spec.get("four_digit_parts", [])],
           "candidate_titles": [check_candidate_titles(index, x) for x in spec.get("candidate_titles", [])],
           "explicit_title_sign": [check_explicit_title_sign(index, x) for x in spec.get("explicit_title_sign", [])],
           "punctuated_cfr_title": [check_explicit_title_sign(index, x) for x in spec.get("punctuated_cfr_title", [])],  # same contract, punctuated spelling
           "citation_tokens": [check_citation_tokens(index, x) for x in spec.get("citation_tokens", [])],
           "hyphen_tails": [check_citation_tokens(index, x) for x in spec.get("hyphen_tails", [])],  # same contract, reason unread_hyphen_tail
           "list_recovery": [check_list_recovery(index, x) for x in spec.get("list_recovery", [])],
           "source_coverage": [check_source_coverage(index, x) for x in spec.get("source_coverage", [])],
           "required_sections": [check_required_sections(index, x) for x in spec.get("required_sections", [])],
           "rulemaking_sources": [check_rulemaking_sources(index, x) for x in spec.get("rulemaking_sources", [])],
           "dependency_verification": [check_dependency_verification(index, x) for x in spec.get("dependency_verification", [])],
           "lowercase_paragraph_clause": [check_lowercase_paragraph_clause(index, x) for x in spec.get("lowercase_paragraph_clause", [])],
           "paragraph_evidence": [check_paragraph_evidence_integrity(index, x) for x in spec.get("paragraph_evidence", [])],
           "relative_action_paragraphs": [check_relative_action_paragraphs(index, x) for x in spec.get("relative_action_paragraphs", [])],
           "enumerated_relative_paragraphs": [check_enumerated_relative_paragraphs(index, x) for x in spec.get("enumerated_relative_paragraphs", [])],
           "passive_location_paragraphs": [check_passive_location_paragraphs(index, x) for x in spec.get("passive_location_paragraphs", [])],
           "nested_by_paragraph_scope": [check_nested_by_paragraph_scope(index, x) for x in spec.get("nested_by_paragraph_scope", [])],
           "nested_to_paragraph_scope": [check_nested_to_paragraph_scope(index, x) for x in spec.get("nested_to_paragraph_scope", [])],
           "paragraph_scope_continuity": [check_paragraph_scope_continuity(index, x) for x in spec.get("paragraph_scope_continuity", [])],
           "enumerated_paragraph_location": [check_enumerated_paragraph_location(index, x) for x in spec.get("enumerated_paragraph_location", [])],
           "parenthesized_paragraph_list": [check_parenthesized_paragraph_list(index, x) for x in spec.get("parenthesized_paragraph_list", [])],
           "triple_letter_paragraph_list": [check_triple_letter_paragraph_list(index, x) for x in spec.get("triple_letter_paragraph_list", [])]}
    out["expectations_skipped_absent_document"] = skipped_absent_document(out)
    out["passed"] = (all(d["passed"] for d in out["delta"]) and out["relationship_integrity"]["passed"]
                     and all(c["passed"] for c in out["citation_scope"]) and all(x["passed"] for x in out["status"])
                     and all(x["passed"] for x in out["citation_completeness"])
                     and all(x["passed"] for x in out["mixed_source"])
                     and all(x["passed"] for x in out["delta_precision"])
                     and all(x["passed"] for x in out["instruction_boundary"])
                     and all(x["passed"] for x in out["current_agreement"])
                     and all(x["passed"] for x in out["amendatory_lists"])
                     and all(x["passed"] for x in out["designator_integrity"])
                     and all(x["passed"] for x in out["query_citations"])
                     and all(x["passed"] for x in out["suffix_integrity"])
                     and all(x["passed"] for x in out["appendix_evidence"])
                     and all(x["passed"] for x in out["titleless_delta"])
                     and all(x["passed"] for x in out["query_lists"])
                     and all(x["passed"] for x in out["query_ranges"])
                     and all(x["passed"] for x in out["connector_operands"])
                     and all(x["passed"] for x in out["part_ranges"])
                     and all(x["passed"] for x in out["part_to_ranges"])
                     and all(x["passed"] for x in out["part_lists"])
                     and all(x["passed"] for x in out["four_digit_parts"])
                     and all(x["passed"] for x in out["candidate_titles"])
                     and all(x["passed"] for x in out["explicit_title_sign"])
                     and all(x["passed"] for x in out["punctuated_cfr_title"])
                     and all(x["passed"] for x in out["citation_tokens"])
                     and all(x["passed"] for x in out["hyphen_tails"])
                     and all(x["passed"] for x in out["list_recovery"])
                     and all(x["passed"] for x in out["source_coverage"])
                     and all(x["passed"] for x in out["required_sections"])
                     and all(x["passed"] for x in out["rulemaking_sources"])
                     and all(x["passed"] for x in out["dependency_verification"])
                     and all(x["passed"] for x in out["lowercase_paragraph_clause"])
                     and all(x["passed"] for x in out["paragraph_evidence"])
                     and all(x["passed"] for x in out["relative_action_paragraphs"])
                     and all(x["passed"] for x in out["enumerated_relative_paragraphs"])
                     and all(x["passed"] for x in out["passive_location_paragraphs"])
                     and all(x["passed"] for x in out["nested_by_paragraph_scope"])
                     and all(x["passed"] for x in out["nested_to_paragraph_scope"])
                     and all(x["passed"] for x in out["paragraph_scope_continuity"])
                     and all(x["passed"] for x in out["enumerated_paragraph_location"])
                     and all(x["passed"] for x in out["parenthesized_paragraph_list"])
                     and all(x["passed"] for x in out["triple_letter_paragraph_list"]))
    return out


REGRESSION_GROUPS = ("delta", "relationship_integrity", "citation_scope", "status", "citation_completeness",
                     "mixed_source", "delta_precision", "instruction_boundary", "current_agreement", "amendatory_lists", "designator_integrity", "query_citations", "suffix_integrity", "appendix_evidence", "titleless_delta", "query_lists", "query_ranges", "connector_operands", "part_ranges", "part_to_ranges", "part_lists", "four_digit_parts", "candidate_titles", "explicit_title_sign", "punctuated_cfr_title", "citation_tokens", "hyphen_tails", "list_recovery", "source_coverage", "required_sections", "rulemaking_sources", "dependency_verification", "lowercase_paragraph_clause", "paragraph_evidence", "relative_action_paragraphs", "enumerated_relative_paragraphs", "passive_location_paragraphs", "nested_by_paragraph_scope", "nested_to_paragraph_scope", "paragraph_scope_continuity", "enumerated_paragraph_location", "parenthesized_paragraph_list",
                     "triple_letter_paragraph_list")


def overall(report):
    """One explicit pass/fail for the whole evaluation.

    It fails when any frozen delta or status check fails, a gold matcher is invalid, a part filter is violated, the versioned questions
    record does not match the frozen record it supersedes, or any regression group has a failing check. A status item whose expectation a
    recorded decision superseded is reported under `superseded` (with its old and new result) and is judged by its current expectation. Retrieval hit rates and MRR are measurements only: no ranking threshold is
    invented here, so a low rate never fails the evaluation by itself. Nothing is waived: a failing check stays a
    failure whatever its history.
    """
    failures = {
        "delta_checks": [d["id"] for d in report["delta"] if not d["passed"]],
        "status_checks": [s["id"] for s in report["status"] if not s["passed"]],
        "invalid_gold": [i["id"] for i in report["retrieval_items"] if i["invalid_gold"]],
        "part_filter_violations": [f"{c['id']}/{c['retriever']}" for c in report["filter_checks"] if not c["respected"]],
        "record_integrity": list(report.get("record_integrity", [])),
        "applicability_checks": [a["id"] for a in report.get("applicability", []) if not a["passed"]],
        "regression_groups": {},
    }
    reg = report.get("regressions")
    if reg:
        for group in REGRESSION_GROUPS:
            value = reg.get(group)
            items = [value] if isinstance(value, dict) else (value or [])
            bad = [x.get("id", "?") for x in items if not x.get("passed")]
            if bad:
                failures["regression_groups"][group] = bad
        if not reg.get("passed") and not failures["regression_groups"]:
            failures["regression_groups"]["regression_summary"] = ["regressions.passed is false"]
    counts = {k: (sum(len(v) for v in val.values()) if isinstance(val, dict) else len(val)) for k, val in failures.items()}
    return {"passed": not any(counts.values()), "failure_counts": counts, "failures": failures,
            "superseded": [x["id"] for x in report.get("superseded", [])],
            "regressions_run": bool(reg),
            "retrieval": "reported as measurements only; no ranking threshold is applied"}


def _expect_core(expect):
    """An expectation without the fields a superseding decision may change."""
    return {k: v for k, v in expect.items() if k not in ("status", "gap_topic")}


def record_integrity(questions_path, spec):
    """Check a versioned questions record against the frozen record it supersedes, read-only.

    A record may declare `supersedes`: the frozen file (relative to the record), its pinned sha256 and the status items whose expectation a
    recorded decision changed. The frozen file is never edited. This returns (baseline spec or None, problems): the pinned hash must match
    the bytes on disk, and the new record may differ from the frozen one only in its version, the `supersedes` block, the changelog and the
    `status`/`gap_topic` of the listed items (ids, queries, scopes, guards and notes must be unchanged; every other item must be identical).
    """
    sup = spec.get("supersedes")
    if not sup:
        return None, []
    problems = []
    base_path = Path(questions_path).parent / sup["file"]
    if not base_path.exists():
        return None, [f"superseded record {sup['file']} not found"]
    raw = base_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != sup["sha256"]:
        problems.append(f"{sup['file']} sha256 {digest} is not the pinned {sup['sha256']}")
    base = json.loads(raw)
    if base.get("version") != sup["version"]:
        problems.append(f"{sup['file']} version {base.get('version')} is not the declared {sup['version']}")
    for key in ("retrieval", "delta", "frozen_before_first_run", "note"):
        if spec.get(key) != base.get(key):
            problems.append(f"{key} differs from the superseded record")
    new_status = {x["id"]: x for x in spec["status"]}
    old_status = {x["id"]: x for x in base["status"]}
    if [x["id"] for x in spec["status"]] != [x["id"] for x in base["status"]]:
        problems.append("status item ids or order differ from the superseded record")
    items = sup.get("items", {})
    for sid in old_status:
        if sid not in new_status:
            continue
        old, cur = old_status[sid], new_status[sid]
        if sid not in items:
            if old != cur:
                problems.append(f"{sid} differs from the superseded record but is not declared as superseded")
            continue
        entry = items[sid]
        if (old["query"], old.get("scope")) != (cur["query"], cur.get("scope")):
            problems.append(f"{sid} query or scope changed")
        if _expect_core(old["expect"]) != _expect_core(cur["expect"]):
            problems.append(f"{sid} guards or notes changed")
        if entry.get("v0_2_expect") != old["expect"]:
            problems.append(f"{sid} recorded v0.2 expectation does not match the superseded record")
        if not entry.get("decision", {}).get("file") or not entry.get("decision", {}).get("sha256"):
            problems.append(f"{sid} does not cite its decision file and hash")
    for sid in items:
        if sid not in old_status:
            problems.append(f"{sid} is declared superseded but does not exist in the superseded record")
    return base, problems


def superseded_results(index, spec, base):
    """Run the superseded record's expectation of each declared item next to the new one and report both, never as a silent waiver."""
    out = []
    if not base:
        return out
    old_status = {x["id"]: x for x in base["status"]}
    new_status = {x["id"]: x for x in spec["status"]}
    for sid, entry in spec["supersedes"]["items"].items():
        if sid not in old_status or sid not in new_status:
            continue
        old = check_status(index, old_status[sid])
        cur = check_status(index, new_status[sid])
        out.append({"id": sid, "query": new_status[sid]["query"], "scope": new_status[sid].get("scope"),
                    "superseded_by": entry["decision"], "change": entry.get("change"),
                    "superseded_record": {"file": spec["supersedes"]["file"], "version": spec["supersedes"]["version"],
                                          "sha256": spec["supersedes"]["sha256"], "expect": old_status[sid]["expect"],
                                          "result": {"passed": old["passed"], "status": old["status"], "failures": old["failures"]}},
                    "current_record": {"version": spec["version"], "expect": new_status[sid]["expect"],
                                       "result": {"passed": cur["passed"], "status": cur["status"], "failures": cur["failures"]}}})
    return out


def run(index, questions_path, regressions_path=None):
    raw = Path(questions_path).read_bytes()
    spec = json.loads(raw)
    base, problems = record_integrity(questions_path, spec)
    per_item, summary = run_retrieval(index, spec["retrieval"])
    deltas = [check_delta(index, d) for d in spec["delta"]]
    statuses = [check_status(index, s) for s in spec["status"]]
    filters = [(r["id"], r["retrievers"][n]["filter_respected"], n) for r in per_item for n in RETRIEVERS
               if "filter_respected" in r["retrievers"][n]]
    regressions = run_regressions(index, regressions_path) if regressions_path else None
    profiles_dir = Path(questions_path).parent / "bank_profiles"
    applicability = check_applicability(index, profiles_dir) if profiles_dir.is_dir() else []
    report = {"questions_version": spec["version"], "as_of": index.as_of, "index": index.meta, "regressions": regressions,
              "questions_record": {"file": Path(questions_path).name, "version": spec["version"], "sha256": hashlib.sha256(raw).hexdigest(),
                                   "supersedes": ({k: spec["supersedes"][k] for k in ("file", "version", "sha256")} if spec.get("supersedes") else None)},
              "record_integrity": problems, "superseded": superseded_results(index, spec, base),
              "retrieval_summary": summary, "retrieval_items": per_item, "delta": deltas, "status": statuses,
              "applicability": applicability,
              "filter_checks": [{"id": i, "retriever": n, "respected": ok} for i, ok, n in filters]}
    report["overall"] = overall(report)
    return report


def render(report):
    lines = [f"As of {report['as_of']}; index built {report['index'].get('built_at')}", ""]
    lines.append("Retrieval (hit@k = first gold hit within top k; MRR over first-hit rank)")
    lines.append(f"{'retriever':8} {'category':14} {'n':>3} {'hit@1':>6} {'hit@3':>6} {'hit@5':>6} {'hit@10':>7} {'MRR':>6}")
    for name, cats in report["retrieval_summary"].items():
        for cat, m in sorted(cats.items(), key=lambda kv: (kv[0] != "ALL", kv[0])):
            lines.append(f"{name:8} {cat:14} {m['n']:>3} {m['hit@1']:>6} {m['hit@3']:>6} {m['hit@5']:>6} {m['hit@10']:>7} {m['mrr']:>6}")
        lines.append("")
    bad = [i for i in report["retrieval_items"] if i["invalid_gold"]]
    lines.append(f"Invalid gold matchers: {len(bad)}")
    for i in bad:
        lines.append(f"  {i['id']}: {i['invalid_gold']}")
    lines.append("")
    lines.append("Misses by retriever (id: first-hit rank, '-' = no hit in top 10)")
    for name in RETRIEVERS:
        miss = [f"{i['id']}:{i['retrievers'][name]['first_hit_rank'] or '-'}" for i in report["retrieval_items"]
                if not i["retrievers"][name]["first_hit_rank"] or i["retrievers"][name]["first_hit_rank"] > 3]
        lines.append(f"  {name}: {', '.join(miss) or 'none'}")
    lines.append("")
    lines.append("Delta checks")
    for d in report["delta"]:
        lines.append(f"  {d['id']} {'PASS' if d['passed'] else 'FAIL'} {d['citation']} as_of={d['as_of']} {'; '.join(d['failures'])}")
    lines.append("Status / refusal checks")
    for s in report["status"]:
        lines.append(f"  {s['id']} {'PASS' if s['passed'] else 'FAIL'} status={s['status']} results={s['results']} {'; '.join(s['failures'])}")
    rec = report.get("questions_record")
    if rec and rec.get("supersedes"):
        sup = rec["supersedes"]
        lines.append(f"Questions record {rec['file']} ({rec['version']}, sha256 {rec['sha256'][:16]}…) supersedes {sup['file']} ({sup['version']}, pinned sha256 {sup['sha256'][:16]}…)")
        for p in report.get("record_integrity", []):
            lines.append(f"  RECORD INTEGRITY FAIL: {p}")
        for x in report.get("superseded", []):
            old, cur = x["superseded_record"], x["current_record"]
            lines.append(f"  {x['id']} SUPERSEDED by {x['superseded_by']['file']} (sha256 {x['superseded_by']['sha256'][:16]}…): "
                         f"{old['version']} expected {old['expect']['status']} -> result {'PASS' if old['result']['passed'] else 'FAIL'} "
                         f"({'; '.join(old['result']['failures']) or 'no failure'}); {cur['version']} expects {cur['expect']['status']}"
                         f"{' / gap ' + cur['expect']['gap_topic'] if cur['expect'].get('gap_topic') else ''} -> result {'PASS' if cur['result']['passed'] else 'FAIL'}"
                         f" [old outcome recorded, not waived]")
    reg = report.get("regressions")
    if reg:
        lines.append(f"Boundary regressions ({reg['task']}, {reg['version']}): {'PASS' if reg['passed'] else 'FAIL'}")
        skip = reg.get("expectations_skipped_absent_document") or skipped_absent_document(reg)
        lines.append(f"  expectations skipped because the document is absent from the index: {skip['total']} (groups: "
                     + "; ".join(f"{g} deltas={c['deltas_skipped']} units={c['units_skipped']}" for g, c in skip["groups"].items()) + ")")
        for d in reg["delta"]:
            lines.append(f"  {d['id']} {'PASS' if d['passed'] else 'FAIL'} delta {d['citation']} instruments={len(d['instruments_found'])} candidates={d['unresolved_candidates']} {'; '.join(d['failures'])}")
        ri = reg["relationship_integrity"]
        lines.append(f"  {ri['id']} {'PASS' if ri['passed'] else 'FAIL'} relationship integrity violations={ri['violations']} {'; '.join(ri['failures'])}")
        for c in reg["citation_scope"]:
            lines.append(f"  {c['id']} {'PASS' if c['passed'] else 'FAIL'} citation scope '{c['query']}' {'; '.join(c['failures'])}")
        for x in reg.get("triple_letter_paragraph_list", []):
            counts = (f"units checked={x['units_checked']} skipped={x['units_skipped']}" if "units_checked" in x else
                      f"deltas checked={x.get('deltas_checked', 0)} skipped={x.get('deltas_skipped', 0)}")
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} triple-letter paragraph list [{x['kind']}] {counts} {'; '.join(x['failures'])}")
        for x in reg.get("parenthesized_paragraph_list", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} parenthesized paragraph list [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("enumerated_paragraph_location", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} enumerated paragraph location [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("paragraph_scope_continuity", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} paragraph scope continuity [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("nested_to_paragraph_scope", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} nested-to paragraph scope [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("nested_by_paragraph_scope", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} nested-by paragraph scope [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("passive_location_paragraphs", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} passive location paragraphs [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("enumerated_relative_paragraphs", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} enumerated relative paragraphs [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("relative_action_paragraphs", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} relative action paragraphs [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("paragraph_evidence", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} paragraph evidence [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("lowercase_paragraph_clause", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} lowercase paragraph clause [{x['kind']}] "
                         f"deltas checked={x.get('deltas_checked', 0)} skipped={x.get('deltas_skipped', 0)} {'; '.join(x['failures'])}")
        for x in reg.get("dependency_verification", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} dependency verification [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("rulemaking_sources", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} rulemaking sources [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("required_sections", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} required sections [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("source_coverage", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} source coverage [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("list_recovery", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} list recovery '{x.get('request', '')}' {'; '.join(x['failures'])}" if 'request' in x else f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} list recovery {'; '.join(x['failures'])}")
        for x in reg.get("hyphen_tails", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} hyphen tails [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("citation_tokens", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} citation tokens [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("punctuated_cfr_title", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} punctuated CFR title [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("explicit_title_sign", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} explicit title sign [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("candidate_titles", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} candidate titles [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("four_digit_parts", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} four-digit parts [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("part_lists", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} part lists [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("part_to_ranges", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} part to-ranges [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("part_ranges", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} part ranges [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("connector_operands", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} connector operands [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("query_ranges", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} query ranges [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("query_lists", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} query lists [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("titleless_delta", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} titleless delta '{x['titleless']}' = '{x['explicit']}' {'; '.join(x['failures'])}")
        for x in reg.get("appendix_evidence", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} appendix evidence [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("suffix_integrity", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} suffix integrity [{x['kind']}] {'; '.join(x['failures'])}")
        for x in reg.get("query_citations", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} query citation [{x['kind']}] '{x['requested']}' {'; '.join(x['failures'])}")
        for x in reg.get("designator_integrity", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} designator integrity {x.get('doc_key')} § {x.get('section')} units={x.get('units')} {'; '.join(x['failures'])}")
        for x in reg.get("amendatory_lists", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} amendatory lists {x.get('doc_key')} units={x.get('units')} {'; '.join(x['failures'])}")
        for x in reg.get("current_agreement", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} current agreement '{x['citation']}' via={x.get('via')} {'; '.join(x['failures'])}")
        for x in reg.get("instruction_boundary", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} instruction boundary {x.get('doc_key')} § {x.get('section')} units={x.get('units')} {'; '.join(x['failures'])}")
        for x in reg.get("delta_precision", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} delta precision '{x['citation']}' status={x.get('evidence_status')} supported={[k[-5:] for k in x.get('supported', [])]} context={[k[-5:] for k in x.get('section_context', [])]} {'; '.join(x['failures'])}")
        for x in reg.get("mixed_source", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} mixed source '{x['query']}' runs={x['runs']} {'; '.join(x['failures'])}")
        for x in reg.get("citation_completeness", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} citation completeness '{x['query']}' {x.get('parts') or ''} {'; '.join(x['failures'])}")
        for x in reg.get("status", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} status={x['status']} results={x['results']} '{x['query']}' {'; '.join(x['failures'])}")
    lines.append("Filter checks: " + ", ".join(f"{c['id']}/{c['retriever']}={'ok' if c['respected'] else 'VIOLATED'}" for c in report["filter_checks"]))
    for a in report.get("applicability", []):
        lines.append(f"Applicability {a['id']}: {'PASS' if a['passed'] else 'FAIL'} {'; '.join(a['failures'])}")
    ov = report.get("overall") or overall(report)
    lines.append("")
    lines.append(f"Overall evaluation: {'PASS' if ov['passed'] else 'FAIL'}")
    c = ov["failure_counts"]
    lines.append(f"  failed delta checks: {c['delta_checks']}; failed status checks: {c['status_checks']}; "
                 f"invalid gold matchers: {c['invalid_gold']}; part-filter violations: {c['part_filter_violations']}; "
                 f"failed regression checks: {c['regression_groups']}")
    if c.get("applicability_checks"):
        lines.append(f"  failed applicability checks: {c['applicability_checks']}")
    for key in ("delta_checks", "status_checks", "invalid_gold", "part_filter_violations", "applicability_checks"):
        if ov["failures"][key]:
            lines.append(f"  {key}: {', '.join(ov['failures'][key])}")
    for group, ids in ov["failures"]["regression_groups"].items():
        lines.append(f"  regression group {group}: {', '.join(ids)}")
    if not ov["regressions_run"]:
        lines.append("  (regression groups were not run)")
    lines.append("  retrieval hit rates and MRR above are measurements; no ranking threshold is applied")
    return "\n".join(lines)
