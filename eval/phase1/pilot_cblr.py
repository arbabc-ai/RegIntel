"""P1.3 narrow evidence pilot: Community Bank Leverage Ratio (topic cbl_ratio), offline and read-only.

    python3 -I eval/phase1/pilot_cblr.py [INDEX.sqlite] [OUT.json]

Writes eval/phase1/pilot_cblr_evidence.json: answer() for 'community bank leverage ratio' in current and future scope with
no profile and each synthetic profile, delta() for the CBLR citations, per-profile applicability routing, a verification
checklist and an evaluation subset (CET1 retrieval items, the CBLR delta item, applicability). Each requested acceptance
check is recorded as specified, with the observed value; where the request's premise does not match the corpus, a
corrected check is evaluated and the mismatch is reported, never relabelled as a pass. Exit 0 only if the evaluation subset
and every corrected/verifiable check pass.
"""
import hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from regintel_p1 import evaluate                                   # noqa: E402
from regintel_p1.bank_profile import load_profile                  # noqa: E402
from regintel_p1.search import CONTENT_KINDS, Index                # noqa: E402

DEFAULT_INDEX = "/home/arbab/projects/regintel-corpus/phase1-index/regintel_p1_state_source_evidence_49.sqlite"
QUERY = "community bank leverage ratio"
INSTRUMENT = "fr:2026-08298"
DELTA_CITES = ["12 CFR 217.12", "12 CFR 217.12(a)(2)(i)", "12 CFR 217.10", "12 CFR 3.12", "12 CFR 324.12"]
EXPECT_ROUTING = {  # the request's literal routing expectations, by profile and part
    "syn-national-bank-01": {"217": "assessed_not_applicable", "3": "assessed_conditional"},
    "syn-state-member-bank-01": {"217": "assessed_conditional"},
    "syn-state-nonmember-bank-01": {"217": "assessed_not_applicable", "324": "assessed_conditional"},
}
CET1_ITEMS = ("R04", "R10", "R11", "R18", "R19")   # retrieval items about common equity tier 1 / tier 1 capital
CBLR_DELTA_ITEM = "D02"                            # 12 CFR 3.12 with fr:2026-08298


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def slim(ev):
    """An answer result without its full text (the text hash keeps it verifiable against the index)."""
    keep = ("provision_id", "doc_key", "citation", "locator", "kind", "heading", "status", "source_sha256", "source_url", "topics", "applicability")
    out = {k: ev[k] for k in keep if k in ev}
    out["text_sha256"] = hashlib.sha256(ev["text"].encode("utf-8")).hexdigest()
    return out


def run(index_path, out_path):
    ix = Index(index_path, as_of="2026-10-29")
    deps = json.loads((REPO / "regintel_p1/topic_dependencies.json").read_text())
    profiles = [load_profile(p) for p in sorted((HERE / "bank_profiles").glob("*.json"))]
    checks, mismatches = [], []

    def check(cid, requested, observed, ok, corrected=None, met_as_specified=None):
        """`passed` is the (corrected, where needed) check; `requested_met_as_specified` says whether the request as written holds."""
        met = bool(ok) if corrected is None else bool(met_as_specified)
        checks.append({"id": cid, "requested": requested, "observed": observed, "passed": bool(ok),
                       "requested_met_as_specified": met, **({"premise_mismatch": corrected} if corrected else {})})
        if corrected:
            mismatches.append({"id": cid, "requested_met_as_specified": met, **corrected})

    # (a)/(5) required sources indexed with usable provisions
    status = {r["doc_key"]: dict(r) for r in ix.conn.execute("SELECT * FROM source_status")}
    req_base = deps["required_baseline_sources"]["cbl_ratio"]["sources"]
    req_rule = deps["required_rulemaking_sources"]["cbl_ratio"]["sources"]
    req_secs = deps["required_baseline_sources"]["cbl_ratio"]["required_sections"]
    verification = []
    for k in req_base + req_rule:
        s = status.get(k, {})
        secs = req_secs.get(k, [])
        present = {sec: bool(ix.conn.execute("""SELECT 1 FROM provision p JOIN source_document d ON d.id=p.doc_id
                                                 WHERE d.doc_key=? AND p.section_no=? AND p.kind='section'""", (k, sec)).fetchone()) for sec in secs}
        doc = ix.conn.execute("SELECT sha256, publisher_issue_date, retrieved_at FROM source_document WHERE doc_key=?", (k,)).fetchone()
        verification.append({"doc_key": k, "declared_as": "required_baseline_source" if k in req_base else "required_rulemaking_source",
                             "source_status": s.get("status"), "provisions": s.get("provisions"), "usable_provisions": s.get("usable_provisions"),
                             "required_sections_present": present, "sha256": doc[0] if doc else None,
                             "publisher_issue_date": doc[1] if doc else None, "retrieved_at": doc[2] if doc else None,
                             "passed": s.get("status") == "indexed" and (s.get("usable_provisions") or 0) > 0 and all(present.values())})
    check("a", "required sources ecfr:12:217 and fr:2026-08298 show source_status=indexed and provisions>0",
          {v["doc_key"]: [v["source_status"], v["provisions"]] for v in verification}, all(v["passed"] for v in verification),
          {"requested": "required sources are ecfr:12:217 and fr:2026-08298",
           "actual": f"topic_dependencies.json ({deps['version']}) declares baselines {req_base} (sections {req_secs}) and rulemaking {req_rule}",
           "corrected_check": "every declared required source is indexed with usable provisions and its required sections present"},
          met_as_specified=all(status.get(k, {}).get("status") == "indexed" and (status.get(k, {}).get("provisions") or 0) > 0
                               for k in ("ecfr:12:217", INSTRUMENT)))

    # answers in both scopes, without and with each profile
    answers = {}
    for sc in ("current", "future"):
        answers[sc] = {"no_profile": ix.answer(QUERY, scope=sc, k=10)}
        for p in profiles:
            answers[sc][p.profile_id] = ix.answer(QUERY, scope=sc, k=10, bank_profile=p)
    cur = answers["current"]["no_profile"]["results"]
    cur_docs = sorted({r["doc_key"] for r in cur})
    leak = [r["citation"] for sc in answers["current"].values() for r in sc["results"]
            if not r["doc_key"].startswith("ecfr:") or r["kind"] not in CONTENT_KINDS]
    check("b", "current-scope answer returns only ecfr:12:217 provisions labelled current_baseline_*",
          {"docs": cur_docs, "labels": sorted({r["status"]["state"] for r in cur})},
          not leak and all(r["status"]["state"].startswith("current_baseline") for r in cur) and set(cur_docs) <= set(req_base),
          {"requested": "only ecfr:12:217", "actual": f"current results come from {cur_docs}; CBLR is in 12 CFR 3.12, 217.12 and 324.12",
           "corrected_check": "every current-scope result is an eCFR content provision of a declared cbl_ratio baseline, labelled current_baseline_*"}
          if cur_docs != ["ecfr:12:217"] else None, met_as_specified=False)
    check("6", "no Federal Register text in current scope (any profile)", {"leaks": leak}, not leak)
    fut = answers["future"]["no_profile"]["results"]
    rule_units = [r for r in fut if r["doc_key"] == INSTRUMENT and r["kind"] == "amendment"]
    labels = sorted({r["status"]["state"] for r in rule_units})
    check("c-future", "future-scope answer returns fr:2026-08298 amendment units labelled proposed_*",
          {"fr:2026-08298_amendment_units": len(rule_units), "labels": labels,
           "other_instruments": sorted({r["doc_key"] for r in fut} - {INSTRUMENT})},
          bool(rule_units) and all(r["doc_key"].startswith("fr:") for r in fut)
          and labels == ["final_effective_date_passed_operative_effect_unreviewed"],
          {"requested": "labels proposed_*", "actual": f"fr:2026-08298 is a final Rule (effective_on {ix._rulemaking[INSTRUMENT]['effective_on']}); "
                                                       f"its producer lifecycle label is {labels}",
           "corrected_check": "fr:2026-08298 amendment units are present in future scope and keep the producer label "
                              "final_effective_date_passed_operative_effect_unreviewed; every future result is a Federal Register record"},
          met_as_specified=bool(rule_units) and all(l.startswith("proposed") for l in labels))

    # deltas (no profile and per profile)
    deltas = {c: {"no_profile": ix.delta(c), **{p.profile_id: ix.delta(c, bank_profile=p) for p in profiles}} for c in DELTA_CITES}
    d10, d12, d12p = deltas["12 CFR 217.10"]["no_profile"], deltas["12 CFR 217.12"]["no_profile"], deltas["12 CFR 217.12(a)(2)(i)"]["no_profile"]
    i10 = sorted(g["doc_key"] for g in d10["instruments"])
    g12p = next((g for g in d12p["instruments"] if g["doc_key"] == INSTRUMENT), None)
    check("c-delta", "delta() for 12 CFR 217.10 shows baseline from 217 and instrument fr:2026-08298 with paragraph_precision per instrument",
          {"217.10": {"baseline": (d10["baseline"] or {}).get("doc_key"), "instruments": i10},
           "217.12": {"baseline": (d12["baseline"] or {}).get("doc_key"), "instruments": sorted(g["doc_key"] for g in d12["instruments"])},
           "217.12(a)(2)(i)": {"baseline_precision": (d12p["baseline"] or {}).get("precision"),
                               "fr:2026-08298_paragraph_precision": (g12p or {}).get("paragraph_precision"),
                               "classification": {r["doc_key"]: r["classification"] for r in d12p["instrument_classification"]}}},
          (d12["baseline"] or {}).get("doc_key") == "ecfr:12:217" and INSTRUMENT in [g["doc_key"] for g in d12["instruments"]]
          and g12p and g12p["paragraph_precision"]["resolved"] and (d12p["baseline"] or {}).get("precision") == "paragraph",
          {"requested": "12 CFR 217.10", "actual": f"fr:2026-08298 amends 12 CFR 217.12 (the CBLR section), not 217.10; delta(217.10) lists {i10}; "
                                                   "paragraph_precision is reported only for a paragraph request",
           "corrected_check": "delta(12 CFR 217.12) has the 217 baseline and fr:2026-08298; delta(12 CFR 217.12(a)(2)(i)) resolves the baseline "
                              "paragraph and fr:2026-08298's paragraph precision (matched_provision)"} if INSTRUMENT not in i10 else None,
          met_as_specified=INSTRUMENT in i10)
    check("3", "provision-level delta: fr:2026-08298 is matched_provision for 217.12(a)(2)(i) and stays a separate instrument",
          {"classification": {r["doc_key"]: r["classification"] for r in d12p["instrument_classification"]},
           "baseline_relation": (g12p or {}).get("baseline_relation")},
          any(r["doc_key"] == INSTRUMENT and r["classification"] == "matched_provision" and r["provision_level_delta_match"]
              for r in d12p["instrument_classification"]) and len({g["doc_key"] for g in d12p["instruments"]}) == len(d12p["instruments"]))

    # (d) routing per profile and part, from delta() on each CBLR section and the current answers
    routing = {}
    for p in profiles:
        routing[p.profile_id] = {"by_part_delta": {c.split()[2].split(".")[0]: deltas[c][p.profile_id]["applicability_status"]
                                                   for c in ("12 CFR 3.12", "12 CFR 217.12", "12 CFR 324.12")},
                                 "current_answer_aggregate": answers["current"][p.profile_id]["applicability_status"],
                                 "current_answer_by_part": answers["current"][p.profile_id]["applicability_summary"]["by_part"],
                                 "future_answer_aggregate": answers["future"][p.profile_id]["applicability_status"],
                                 "fr_2026_08298_on_217_12": next(r["applicability_status"] for r in deltas["12 CFR 217.12"][p.profile_id]["instrument_classification"]
                                                                 if r["doc_key"] == INSTRUMENT)}
    bad = [f"{pid} part {part}: {routing[pid]['by_part_delta'].get(part)} != {want}"
           for pid, parts in EXPECT_ROUTING.items() for part, want in parts.items() if routing.get(pid, {}).get("by_part_delta", {}).get(part) != want]
    check("d", "routing: national 217 NA / 3 conditional; state member 217 conditional; state nonmember 217 NA / 324 conditional",
          routing, not bad and set(routing) == set(EXPECT_ROUTING))
    check("4", "conservative aggregation: no current answer aggregate is conditional unless one of its results is",
          {pid: r["current_answer_aggregate"] for pid, r in routing.items()},
          all((r["current_answer_aggregate"] == "assessed_conditional") == ("assessed_conditional" in r["current_answer_by_part"].values())
              for r in routing.values()))
    boundary = [f"{sc}/{k}" for sc in answers for k, a in answers[sc].items() if a.get("legal_validity", "not_determined") != "not_determined"] + \
               [f"{c}/{k}" for c in deltas for k, d in deltas[c].items() if d["legal_validity"] != "not_determined"]
    check("boundaries", "legal_validity not_determined everywhere; no-profile outputs not_assessed",
          {"violations": boundary}, not boundary and all(a["applicability_status"] == "not_assessed" for a in
                                                         (answers["current"]["no_profile"], answers["future"]["no_profile"])))

    # (e) evaluation subset
    spec = json.loads((HERE / "questions_v0.3.json").read_text())
    per_item, _ = evaluate.run_retrieval(ix, [r for r in spec["retrieval"] if r["id"] in CET1_ITEMS])
    retrieval_ok = all(not i["invalid_gold"] for i in per_item)
    dchk = [evaluate.check_delta(ix, d) for d in spec["delta"] if d["id"] == CBLR_DELTA_ITEM]
    appl = evaluate.check_applicability(ix, HERE / "bank_profiles")
    subset = {"retrieval_items": [{"id": i["id"], "invalid_gold": i["invalid_gold"],
                                   "first_hit_rank": {n: i["retrievers"][n].get("first_hit_rank") for n in i["retrievers"]}} for i in per_item],
              "delta": dchk, "applicability": appl,
              "passed": retrieval_ok and all(d["passed"] for d in dchk) and all(a["passed"] for a in appl),
              "note": "retrieval hit rates are measurements (no threshold); a retrieval item fails only on an invalid gold matcher"}
    check("e", "evaluation subset (CET1 retrieval items + CBLR delta + applicability) passes", {"passed": subset["passed"]}, subset["passed"])

    ix.conn.close()
    bundle = {"schema": "p1-pilot-cblr-evidence-0.1", "topic": "cbl_ratio", "query": QUERY, "as_of": "2026-10-29",
              "status": "producer-reported pilot; no gate approved; applicability is conditional routing by stated scope, not a legal determination",
              "inputs": {"index": {"path": index_path, "sha256": sha(index_path)},
                         "topic_dependencies": {"version": deps["version"], "sha256": sha(REPO / "regintel_p1/topic_dependencies.json")},
                         "questions_v0.3": sha(HERE / "questions_v0.3.json"),
                         "profiles": {p.profile_id: sha(HERE / "bank_profiles" / f) for p, f in
                                      zip(profiles, sorted(x.name for x in (HERE / "bank_profiles").glob("*.json")))},
                         "code": {f: sha(REPO / f) for f in ("regintel_p1/search.py", "regintel_p1/evaluate.py", "regintel_p1/applicability.py",
                                                             "regintel_p1/applicability_conditions.json", "regintel_p1/bank_profile.py",
                                                             "eval/phase1/pilot_cblr.py")}},
              "verification_checklist": verification,
              "acceptance_checks": checks, "premise_mismatches": mismatches,
              "answers": {sc: {k: {**{x: v for x, v in a.items() if x != "results"}, "results": [slim(r) for r in a["results"]]}
                               for k, a in by.items()} for sc, by in answers.items()},
              "deltas": deltas, "routing": routing, "evaluation_subset": subset,
              "passed": all(c["passed"] for c in checks),
              "requested_checks_met_as_specified": all(c["requested_met_as_specified"] for c in checks),
              "result_note": "passed = every verifiable and corrected check holds; requested_checks_met_as_specified is false because the "
                             "request's premises listed in premise_mismatches do not match the corpus"}
    Path(out_path).write_text(json.dumps(bundle, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    for c in checks:
        tag = "" if "premise_mismatch" not in c else ("  [as specified: MET; corrected check also evaluated]" if c["requested_met_as_specified"]
                                                    else "  [as specified: NOT MET (premise mismatch); PASS is the corrected check]")
        print(("PASS " if c["passed"] else "FAIL ") + c["id"] + ": " + c["requested"] + tag)
    print(f"{sum(c['passed'] for c in checks)}/{len(checks)} checks passed; "
          f"{sum(not c['requested_met_as_specified'] for c in checks)} requested checks not met as specified; {len(mismatches)} premise mismatches reported")
    return 0 if bundle["passed"] else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(run(args[0] if args else DEFAULT_INDEX, args[1] if len(args) > 1 else str(HERE / "pilot_cblr_evidence.json")))
