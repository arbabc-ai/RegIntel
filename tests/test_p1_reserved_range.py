"""Regression tests for S7-P1-RESERVED-RANGE-CONSISTENCY-09 (F1): one current-baseline lookup serves resolve_citations,
exact, answer and delta, so a numeric section inside an acquired eCFR range resolves identically everywhere."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from test_p1 import asset
from test_p1_delta_precision import fr
from test_p1_instruction_boundary import FR_LOOSE

H = '<div class="section" id="{id}"><h4>§§ {id} [Reserved]</h4></div>'
ECFR_3 = ('<div class="part" id="part-3"><h1>PART 3—CAPITAL</h1>'
          + H.format(id="3.4-3.9")                                   # acquired reserved range
          + '<div class="section" id="3.10"><h4>§ 3.10 Minimum capital.</h4><div id="p-3.10(a)"><p class="indent-1" data-title="3.10(a)">(a) Ten text.</p></div></div>'
          + H.format(id="3.39-3.41")                                 # range that also contains an acquired exact section
          + '<div class="section" id="3.40"><h4>§ 3.40 Forty.</h4><div id="p-3.40(a)"><p class="indent-1" data-title="3.40(a)">(a) Forty text.</p></div></div>'
          + H.format(id="3.60-3.55")                                 # reversed endpoints
          + H.format(id="3.70-217.75")                               # endpoints in two parts
          + H.format(id="3.80a-3.89")                                # lettered endpoint
          + '</div>')
# 60 paragraphs that repeat the query terms: lexical ranking alone fills its top 50 with them and the reserved range
# section never enters the lexical list, so only the exact lookup can surface it.
ECFR_50 = ('<div class="part" id="part-50"><h1>PART 50—LIQUIDITY</h1><div class="section" id="50.1"><h4>§ 50.1 Cites.</h4>'
           + "".join(f'<div id="p-50.1(a{n})"><p class="indent-1" data-title="50.1(a{n})">(a{n}) 12 CFR 3.5 12 CFR 3.5 12 CFR 3.5 cfr 3 5 cfr 3 5 cfr 3 5 cfr 12.</p></div>' for n in range(60))
           + '</div></div>')
ECFR_217 = '<div class="part" id="part-217"><h1>PART 217—CAPITAL</h1>' + H.format(id="217.4-217.9") + '</div>'
FR_35 = fr("RULE", "1. Amend &#xA7; 3.5 by adding paragraph (a) to read as follows:", [("3.5", ["(a) New 3.5 text."])])

RANGE_ID = "section-3.4-3.9"
MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")


class ReservedRangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        ecfr = {"source_class": "regulation", "publisher": "eCFR", "publisher_issue_date": "2026-10-23",
                "publisher_up_to_date_as_of": "2026-10-28"}
        docs = {
            "ecfr:12:3": {**ecfr, "citation": "12 CFR Part 3", "title": "Capital", "asset": asset(root, "e3.html", ECFR_3.encode())},
            "ecfr:12:217": {**ecfr, "citation": "12 CFR Part 217", "title": "Capital 217", "asset": asset(root, "e217.html", ECFR_217.encode())},
            "ecfr:12:50": {**ecfr, "citation": "12 CFR Part 50", "title": "Distractor", "asset": asset(root, "e50.html", ECFR_50.encode())},
            "fr:2026-90001": {**meta, "document_number": "2026-90001", "title": "Adds 3.5(a)", "document_type": "Rule",
                              "effective_on": "2027-02-01", "action": "Final rule.", "asset": asset(root, "r.xml", FR_35)},
            "fr:2026-80041": {**meta, "document_number": "2026-80041", "title": "Loose", "document_type": "Rule",
                              "effective_on": "2027-02-01", "action": "Final rule.", "asset": asset(root, "l.xml", FR_LOOSE)},
        }
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "r.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cur(self, q, parts=None):
        return self.index.resolve_citations(q, "current", parts)[0]

    def prov(self, doc_key, locator):
        return self.index.conn.execute(
            "SELECT p.id, p.citation, d.sha256, d.publisher_issue_date, d.publisher_up_to_date_as_of FROM provision p "
            "JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.locator=?", (doc_key, locator)).fetchone()

    # ---- range membership, endpoints and interiors, identical through both interfaces
    def test_endpoints_and_interiors_resolve_and_identify_the_publishers_range(self):
        rng = self.prov("ecfr:12:3", RANGE_ID)
        for cite in ("12 CFR 3.4", "12 CFR 3.5", "12 CFR 3.9"):
            c = self.cur(cite)
            self.assertTrue(c["resolved"], cite)
            self.assertEqual(c["baseline_match"]["via"], "range_section")
            info = c["baseline_match"]["range"]
            self.assertEqual((info["range_section"], info["range_locator"], info["range_citation"]),
                             ("3.4-3.9", RANGE_ID, "12 CFR 3.4-3.9"))
            self.assertEqual((info["lo"], info["hi"], info["requested_section"]), ("3.4", "3.9", cite.split()[-1]))
            self.assertIn("not an operative obligation", info["note"])
            self.assertEqual(c["_current_ids"], [rng["id"]])
            d = self.index.delta(cite)
            self.assertEqual(d["baseline"]["doc_key"], "ecfr:12:3")
            self.assertEqual(d["baseline"]["locator"], RANGE_ID)  # the publisher's original range locator is kept
            self.assertEqual(d["baseline"]["citation"], "12 CFR 3.4-3.9")
            self.assertEqual(d["baseline"]["sha256"], rng["sha256"])
            self.assertEqual((d["baseline"]["issue_date"], d["baseline"]["up_to_date_as_of"]),
                             (rng["publisher_issue_date"], rng["publisher_up_to_date_as_of"]))
            self.assertEqual(d["baseline"]["match"], "range_section")
            self.assertEqual(d["baseline"]["range_membership"], info)
            self.assertEqual(d["legal_validity"], "not_determined")
            self.assertEqual(d["applicability_status"], "not_assessed")

    def test_part_217_range_resolves_the_same_way(self):
        c = self.cur("12 CFR 217.5")
        self.assertEqual(c["baseline_match"]["range"]["range_locator"], "section-217.4-217.9")
        d = self.index.delta("12 CFR 217.5")
        self.assertEqual((d["baseline"]["doc_key"], d["baseline"]["locator"]), ("ecfr:12:217", "section-217.4-217.9"))

    def test_delta_and_resolver_agree_on_every_section_citation(self):
        cites = ["3.3", "3.4", "3.5", "3.9", "3.10", "3.12", "3.39", "3.40", "3.41", "3.42", "3.4a", "3.5a", "3.57", "3.58",
                 "3.72", "3.85", "217.4", "217.5", "217.9", "217.10", "217.4a", "3.99"]
        for c in cites:
            q = f"12 CFR {c}"
            cur, d = self.cur(q), self.index.delta(q)
            self.assertEqual(d["baseline"] is not None, cur["resolved"], q)
            if cur["resolved"]:
                row = self.index.conn.execute("SELECT locator FROM provision WHERE id=?", (cur["_current_ids"][0],)).fetchone()
                self.assertEqual(d["baseline"]["locator"], row["locator"], q)

    # ---- precedence and guards
    def test_acquired_exact_section_beats_a_range_fallback(self):
        c = self.cur("12 CFR 3.40")
        self.assertEqual(c["baseline_match"]["via"], "exact_section")
        self.assertIsNone(c["baseline_match"]["range"])
        locs = [self.index.conn.execute("SELECT locator FROM provision WHERE id=?", (i,)).fetchone()["locator"] for i in c["_current_ids"]]
        self.assertNotIn("section-3.39-3.41", locs)
        self.assertIn("section-3.40", locs)
        self.assertEqual(self.index.delta("12 CFR 3.40")["baseline"]["match"], "exact_section")
        self.assertEqual(self.cur("12 CFR 3.39")["baseline_match"]["via"], "range_section")  # its neighbours still use the range
        self.assertEqual(self.cur("12 CFR 3.41")["baseline_match"]["range"]["range_section"], "3.39-3.41")

    def test_outside_reversed_cross_part_lettered_and_title_mismatch_never_resolve(self):
        for cite in ("12 CFR 3.3", "12 CFR 3.99", "12 CFR 3.57", "12 CFR 3.58", "12 CFR 3.72", "12 CFR 217.72", "12 CFR 3.85",
                     "12 CFR 3.4a", "12 CFR 3.5a", "12 CFR 217.4a", "31 CFR 3.5"):
            c = self.cur(cite)
            self.assertFalse(c["resolved"], cite)
            self.assertEqual(c["legal_validity"], "not_determined")
            self.assertIn("not a determination that the citation is invalid", c["note"])
            self.assertIsNone(self.index.delta(cite)["baseline"], cite)
        self.assertEqual(self.cur("12 CFR 3.57")["reason"], "not_found_in_acquired_part")  # reversed 3.60-3.55 is ignored
        self.assertEqual(self.cur("31 CFR 3.5")["reason"], "part_not_acquired")

    def test_missing_paragraph_never_resolves_through_its_section_or_range(self):
        for cite, reason in (("12 CFR 3.5(a)", "paragraph_not_found_in_acquired_range"),
                             ("12 CFR 3.4(a)(1)", "paragraph_not_found_in_acquired_range"),
                             ("12 CFR 3.10(z)", "paragraph_not_found_in_acquired_section"),
                             ("12 CFR 3.40(z)(1)", "paragraph_not_found_in_acquired_section")):
            c = self.cur(cite)
            self.assertFalse(c["resolved"], cite)
            self.assertEqual(c["reason"], reason, cite)
            self.assertEqual(self.index.exact(cite, 20, "current"), [], cite)
            d = self.index.delta(cite)
            self.assertIsNone(d["baseline"], cite)  # no paragraph baseline
            self.assertIsNotNone(d["section_context"], cite)  # the section or range appears only as context
            self.assertIn("not_the_requested_paragraph", d["section_context"]["role"])
        d = self.index.delta("12 CFR 3.5(a)")
        self.assertEqual(d["section_context"]["match"], "range_section")
        self.assertEqual(d["section_context"]["range_membership"]["range_locator"], RANGE_ID)
        self.assertTrue(self.cur("12 CFR 3.10(a)")["resolved"])  # a real paragraph in an exact section still resolves

    # ---- answers: exact/hybrid, scopes, filters, small k, mixed citations
    def test_answers_across_modes_scopes_filters_and_k(self):
        for mode in MODES:
            for scope in SCOPES:
                for parts, ok in ((None, True), (["12:3"], True), (["12:217"], False)):
                    for k in (10, 1):
                        out = self.index.answer("12 CFR 3.5", mode=mode, scope=scope, parts=parts, k=k)
                        tag = (mode, scope, parts, k)
                        self.assertEqual(out["applicability_status"], "not_assessed")
                        if ok:
                            self.assertEqual(out["status"], "evidence_found", tag)
                            self.assertTrue(out["results"], tag)
                            self.assertEqual(out["coverage_gaps"], [], tag)
                        else:
                            if mode == "exact":
                                self.assertEqual(out["results"], [], tag)
                            self.assertNotEqual(out["status"], "evidence_found", tag)
                            self.assertEqual(out["citations"][0]["reason"], "excluded_by_part_filter", tag)
                        for r in out["results"]:
                            if scope == "future":
                                self.assertTrue(r["kind"] in ("amendment", "fr_preamble", "fr_table"), tag)  # never the range text
                            if scope == "current":
                                self.assertTrue(r["doc_key"].startswith("ecfr:"), tag)  # lexical hits may come from part 217
                                if parts:  # hybrid lexical hits stay inside the part filter
                                    self.assertIn(r["doc_key"], {"ecfr:" + x for x in parts}, tag)
        out = self.index.answer("12 CFR 3.5", mode="exact", scope="current")
        self.assertEqual(out["results"][0]["locator"], RANGE_ID)
        self.assertEqual(out["results"][0]["range_membership"]["range_section"], "3.4-3.9")
        self.assertEqual(out["results"][0]["status"]["state"], "current_baseline_requires_operative_effect_review")
        both = {r["kind"] == "amendment" for r in self.index.answer("12 CFR 3.5", mode="exact", scope="both", k=2)["results"]}
        self.assertEqual(both, {True, False})  # k=2: the range baseline and the future instrument both stay visible

    def test_hybrid_small_k_keeps_the_requested_citations_own_record(self):
        # A part-50 paragraph repeats the query terms, so lexical ranking alone would put it first.
        lexical = self.index.bm25("12 CFR 3.5", 1, "current")
        self.assertEqual(self.index.evidence(lexical[0][0], False)["doc_key"], "ecfr:12:50")
        for k in (1, 2):
            out = self.index.answer("12 CFR 3.5", mode="hybrid", scope="current", k=k)
            self.assertEqual(out["results"][0]["locator"], RANGE_ID, k)
            self.assertEqual(out["results"][0]["range_membership"]["range_section"], "3.4-3.9")
        two = self.index.answer("12 CFR 3.5 and 12 CFR 217.5", mode="hybrid", scope="current", k=2)
        self.assertEqual(sorted(r["doc_key"] for r in two["results"]), ["ecfr:12:217", "ecfr:12:3"])

    def test_mixed_citations(self):
        q = "12 CFR 3.5 and 12 CFR 217.5"
        for mode in MODES:
            out = self.index.answer(q, mode=mode, scope="current", parts=["12:3", "12:217"], k=10)
            self.assertEqual(out["status"], "evidence_found", mode)
            self.assertEqual({c["baseline_match"]["via"] for c in out["citations"]}, {"range_section"})
            self.assertEqual({r["doc_key"] for r in out["results"]} & {"ecfr:12:3", "ecfr:12:217"}, {"ecfr:12:3", "ecfr:12:217"})
            part_filtered = self.index.answer(q, mode=mode, scope="current", parts=["12:3"])
            cites = {c["citation"]: c for c in part_filtered["citations"]}
            self.assertTrue(cites["12 CFR 3.5"]["resolved"])
            self.assertEqual(cites["12 CFR 217.5"]["reason"], "excluded_by_part_filter")
            self.assertEqual(part_filtered["status"], "partial_evidence")
            bad = self.index.answer("12 CFR 3.5 and 12 CFR 3.4a", mode=mode, scope="current")
            self.assertEqual(bad["status"], "partial_evidence")
            gaps = {g["topic"] for g in bad["coverage_gaps"]}
            self.assertIn("citation:12 CFR 3.4a", gaps)
            self.assertNotIn("citation:12 CFR 3.5", gaps)
        small = [r["doc_key"] for r in self.index.answer(q, mode="exact", scope="current", k=2)["results"]]
        self.assertEqual(sorted(small), ["ecfr:12:217", "ecfr:12:3"])

    # ---- earlier protections
    def test_future_instruments_stay_independent_beside_the_range_baseline(self):
        d = self.index.delta("12 CFR 3.5")
        self.assertEqual([g["doc_key"] for g in d["instruments"]], ["fr:2026-90001"])
        self.assertTrue(d["instruments"][0]["status"]["caveat"] and d["instruments"][0]["source_sha256"])
        p = self.index.delta("12 CFR 3.5(a)")
        self.assertTrue(p["future_precision"]["resolved"])  # the instruction names (a)
        self.assertFalse(p["current_precision"]["resolved"])  # the range does not contain it
        self.assertEqual(p["current_precision"]["reason"], "paragraph_not_found_in_acquired_range")

    def test_instruction_boundary_fix_is_preserved(self):
        c = self.index.resolve_citations("12 CFR 3.8(e)(3)", "future")[0]
        self.assertFalse(c["resolved"])
        self.assertEqual(c["achieved"], "section_only")
        self.assertTrue(self.index.resolve_citations("12 CFR 3.8(e)", "future")[0]["resolved"])

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertNotIn("reviewed", status)


if __name__ == "__main__":
    unittest.main()
