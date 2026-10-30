"""Evaluation harness: retrieval hit rates per retriever, deterministic delta checks and status/refusal checks.

A retrieved provision is a hit when it matches any gold matcher:
  {"doc_key", "locator"}     the provision with that locator, or any provision beneath it
  {"doc_key", "section_no"}  any provision in that section
  {"doc_key"[, "kind", "section_no"]}  any provision in that document
Gold matchers are validated against the index first; a matcher that matches nothing is reported as invalid
rather than silently counted as a miss.
"""
import json
from collections import defaultdict

from .search import CITE, Index

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


def check_delta(index, item):
    ix = index if "as_of" not in item else Index(index.db_path, item["as_of"])
    out = ix.delta(item["citation"])
    exp, failures = item["expect"], []
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
            "failures": failures, "instruments_found": sorted(got)}


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
    m = CITE.search(citation)
    title, part, minor, _ = m.groups()
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


def run_regressions(index, path):
    spec = json.loads(open(path).read())
    out = {"version": spec["version"], "task": spec["task"],
           "delta": [check_boundary_delta(index, d) for d in spec["delta"]],
           "relationship_integrity": check_relationship_integrity(index, spec["relationship_integrity"]),
           "citation_scope": [check_citation_scope(index, c) for c in spec["citation_scope"]],
           "status": [check_status_regression(index, x) for x in spec.get("status", [])],
           "citation_completeness": [check_citation_completeness(index, x) for x in spec.get("citation_completeness", [])]}
    out["passed"] = (all(d["passed"] for d in out["delta"]) and out["relationship_integrity"]["passed"]
                     and all(c["passed"] for c in out["citation_scope"]) and all(x["passed"] for x in out["status"])
                     and all(x["passed"] for x in out["citation_completeness"]))
    return out


def run(index, questions_path, regressions_path=None):
    spec = json.loads(open(questions_path).read())
    per_item, summary = run_retrieval(index, spec["retrieval"])
    deltas = [check_delta(index, d) for d in spec["delta"]]
    statuses = [check_status(index, s) for s in spec["status"]]
    filters = [(r["id"], r["retrievers"][n]["filter_respected"], n) for r in per_item for n in RETRIEVERS
               if "filter_respected" in r["retrievers"][n]]
    regressions = run_regressions(index, regressions_path) if regressions_path else None
    return {"questions_version": spec["version"], "as_of": index.as_of, "index": index.meta, "regressions": regressions,
            "retrieval_summary": summary, "retrieval_items": per_item, "delta": deltas, "status": statuses,
            "filter_checks": [{"id": i, "retriever": n, "respected": ok} for i, ok, n in filters]}


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
    reg = report.get("regressions")
    if reg:
        lines.append(f"Boundary regressions ({reg['task']}, {reg['version']}): {'PASS' if reg['passed'] else 'FAIL'}")
        for d in reg["delta"]:
            lines.append(f"  {d['id']} {'PASS' if d['passed'] else 'FAIL'} delta {d['citation']} instruments={len(d['instruments_found'])} candidates={d['unresolved_candidates']} {'; '.join(d['failures'])}")
        ri = reg["relationship_integrity"]
        lines.append(f"  {ri['id']} {'PASS' if ri['passed'] else 'FAIL'} relationship integrity violations={ri['violations']} {'; '.join(ri['failures'])}")
        for c in reg["citation_scope"]:
            lines.append(f"  {c['id']} {'PASS' if c['passed'] else 'FAIL'} citation scope '{c['query']}' {'; '.join(c['failures'])}")
        for x in reg.get("citation_completeness", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} citation completeness '{x['query']}' {x.get('parts') or ''} {'; '.join(x['failures'])}")
        for x in reg.get("status", []):
            lines.append(f"  {x['id']} {'PASS' if x['passed'] else 'FAIL'} status={x['status']} results={x['results']} '{x['query']}' {'; '.join(x['failures'])}")
    lines.append("Filter checks: " + ", ".join(f"{c['id']}/{c['retriever']}={'ok' if c['respected'] else 'VIOLATED'}" for c in report["filter_checks"]))
    return "\n".join(lines)
