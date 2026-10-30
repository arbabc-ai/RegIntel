"""Regression tests for S7-P1-CITATION-COMPLETENESS-04: every requested citation is resolved independently."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from test_p1 import ECFR_HTML, asset
from test_p1_completeness import FR_EXPLICIT, FR_RANGES

ECFR_217 = """<div class="part" id="part-217"><h1>PART 217—CAPITAL</h1>
<div class="section" id="217.1"><h4>§ 217.1 Purpose.</h4>
<div id="p-217.1(a)"><p class="indent-1" data-title="217.1(a)">(a) Purpose of part 217 minimum capital.</p></div></div></div>"""

MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")
FUTURE_KINDS = ("amendment", "fr_preamble", "fr_table")


class CitationCompletenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        ecfr = {"source_class": "regulation", "publisher": "eCFR", "publisher_issue_date": "2026-10-23",
                "publisher_up_to_date_as_of": "2026-10-28"}
        docs = {
            "ecfr:12:3": {**ecfr, "citation": "12 CFR Part 3", "title": "Capital", "asset": asset(root, "e3.html", ECFR_HTML.encode())},
            "ecfr:12:217": {**ecfr, "citation": "12 CFR Part 217", "title": "Capital 217", "asset": asset(root, "e217.html", ECFR_217.encode())},
            "fr:2026-70012": {**meta, "document_number": "2026-70012", "title": "Explicit", "document_type": "Rule",
                              "effective_on": "2027-02-01", "action": "Final rule.", "asset": asset(root, "x.xml", FR_EXPLICIT)},
            "fr:2026-70010": {**meta, "document_number": "2026-70010", "title": "Ranges", "document_type": "Rule",
                              "effective_on": "2027-01-18", "action": "Final rule.", "asset": asset(root, "r.xml", FR_RANGES)},
        }
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "k.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cit(self, query, scope="both", parts=None):
        return {c["citation"]: c for c in self.index.resolve_citations(query, scope, parts)}

    def gap_topics(self, out):
        return {g["topic"]: g for g in out["coverage_gaps"]}

    def assert_scope_invariants(self, out, scope, parts=None):
        self.assertEqual(out["applicability_status"], "not_assessed")
        for r in out["results"]:
            if scope == "future":
                self.assertTrue(r["kind"] in FUTURE_KINDS and not r["status"]["state"].startswith("current"), r["citation"])
            if scope == "current":
                self.assertTrue(r["status"]["state"].startswith("current_baseline") and r["doc_key"].startswith("ecfr:"), r["citation"])
            if parts:
                row = self.index.conn.execute("SELECT title_no, part_no FROM provision WHERE id=?", (r["provision_id"],)).fetchone()
                self.assertIn(f"{row['title_no']}:{row['part_no']}", parts, r["citation"])

    # ---- valid controls
    def test_valid_section_and_paragraph_resolve_independently(self):
        c = self.cit("12 CFR 3.1 and 12 CFR 3.1(b)(1) and § 217.1", "current")
        self.assertTrue(all(x["resolved"] for x in c.values()), c)
        self.assertEqual(c["12 CFR 3.1(b)(1)"]["requested_precision"], "paragraph")
        self.assertEqual(c["12 CFR 3.1(b)(1)"]["achieved"], "paragraph")
        self.assertEqual(c["12 CFR 3.1"]["by_scope"]["current"], c["12 CFR 3.1"]["by_scope"]["current"])
        self.assertTrue(all(x["legal_validity"] == "not_determined" for x in c.values()))

    def test_fully_resolved_multi_citation_query_is_evidence_found_in_every_mode_and_scope(self):
        for mode in MODES:
            for scope in SCOPES:
                out = self.index.answer("12 CFR 3.1 and 12 CFR 3.2", mode=mode, scope=scope, k=10)
                if scope == "future":
                    self.assertTrue(out["citations"][0]["resolved"] and not out["citations"][1]["resolved"], (mode, scope))
                    self.assertEqual(out["status"], "partial_evidence", (mode, scope))  # 3.2 has no amendment in the fixture
                else:
                    self.assertEqual(out["status"], "evidence_found", (mode, scope))
                    self.assertEqual([g for g in out["coverage_gaps"] if g["topic"].startswith("citation:")], [], (mode, scope))
                self.assert_scope_invariants(out, scope)
        for mode in MODES:
            both = self.index.answer("12 CFR 3.1 and § 217.1", mode=mode, scope="current")
            self.assertEqual(both["status"], "evidence_found", mode)

    def test_valid_future_paragraph_resolves_when_an_amendment_opens_with_it(self):
        c = self.cit("12 CFR 3.1(a)", "future")["12 CFR 3.1(a)"]
        self.assertTrue(c["resolved"])
        self.assertEqual(c["achieved"], "paragraph")

    # ---- missing paragraph never resolves to its parent section
    def test_missing_paragraph_does_not_silently_resolve_to_parent_section(self):
        q = "12 CFR 3.1(z)(999)"
        self.assertEqual(self.index.exact(q, 50, "current"), [])  # no parent-section substitution in current scope
        cur = self.cit(q, "current")[q]
        self.assertFalse(cur["resolved"])
        self.assertEqual(cur["reason"], "paragraph_not_found_in_acquired_section")
        fut = self.cit(q, "future")[q]
        self.assertFalse(fut["resolved"])
        self.assertEqual((fut["reason"], fut["achieved"]), ("paragraph_precision_unresolved", "section_only"))
        self.assertTrue(fut["by_scope"]["future"] > 0)
        for mode in MODES:
            for scope in SCOPES:
                out = self.index.answer(q, mode=mode, scope=scope, k=10)
                self.assertNotEqual(out["status"], "evidence_found", (mode, scope))
                self.assertIn(f"citation:{q}", self.gap_topics(out), (mode, scope))
                self.assert_scope_invariants(out, scope)
                if scope == "current" and mode == "exact":
                    self.assertEqual(out["status"], "missing_evidence")
                if scope != "current" and mode == "exact":  # section-level amendment evidence stays discoverable, flagged
                    self.assertEqual(out["status"], "partial_evidence")
                    self.assertTrue(out["results"] and all(r["kind"] == "amendment" for r in out["results"]))

    def test_paragraph_precision_gap_names_section_only(self):
        out = self.index.answer("12 CFR 3.1(z)(999)", mode="exact", scope="future")
        gap = self.gap_topics(out)["citation:12 CFR 3.1(z)(999)"]
        self.assertEqual(gap["reason"], "paragraph_precision_unresolved")
        self.assertIn("section-level", gap["note"])

    # ---- one resolved citation must not conceal another
    def test_resolved_citation_does_not_hide_an_unresolved_one(self):
        cases = {"12 CFR 50.1": "part_not_acquired", "12 CFR 999.999": "part_not_acquired",
                 "12 CFR 3.999": "not_found_in_acquired_part"}
        for bad, reason in cases.items():
            q = f"12 CFR 3.1 and {bad}"
            for mode in MODES:
                for scope in SCOPES:
                    out = self.index.answer(q, mode=mode, scope=scope, k=10)
                    c = {x["citation"]: x for x in out["citations"]}
                    self.assertFalse(c[bad]["resolved"], (q, mode, scope))
                    self.assertEqual(c[bad]["reason"], reason, (q, mode, scope))
                    self.assertEqual(c[bad]["legal_validity"], "not_determined")
                    self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope))
                    self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence", (q, mode, scope))
                    gap = self.gap_topics(out)[f"citation:{bad}"]
                    self.assertEqual(gap["coverage"], "unresolved_citation")
                    self.assertIn("not a determination that the citation is invalid", gap["note"])
                    self.assert_scope_invariants(out, scope)

    def test_part_filter_excluding_one_citation_is_reported_for_that_citation_only(self):
        q = "12 CFR 3.1 and 12 CFR 217.1"
        for mode in MODES:
            for scope in ("current", "both"):
                out = self.index.answer(q, mode=mode, scope=scope, parts=["12:3"], k=10)
                c = {x["citation"]: x for x in out["citations"]}
                self.assertTrue(c["12 CFR 3.1"]["resolved"], (mode, scope))
                self.assertEqual(c["12 CFR 217.1"]["reason"], "excluded_by_part_filter", (mode, scope))
                self.assertEqual(out["status"], "partial_evidence", (mode, scope))
                self.assertNotIn("citation:12 CFR 3.1", self.gap_topics(out))
                self.assert_scope_invariants(out, scope, ["12:3"])
        for scope in SCOPES:  # excluding every citation: nothing resolves
            out = self.index.answer(q, mode="exact", scope=scope, parts=["12:999"])
            self.assertEqual(out["results"], [], scope)
            self.assertEqual(out["status"], "missing_evidence", scope)
            reasons = {g["topic"]: g["reason"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:")}
            self.assertEqual(reasons["citation:12 CFR 3.1"], "excluded_by_part_filter", scope)
            self.assertEqual(reasons["citation:12 CFR 217.1"],  # 217.1 has no amendment, so in future scope the scope also excludes it
                             "excluded_by_scope_and_part_filter" if scope == "future" else "excluded_by_part_filter", scope)

    def test_scope_exclusion_is_distinguished_from_part_filter(self):
        c = self.cit("12 CFR 3.2", "future")["12 CFR 3.2"]  # baseline-only section asked for in future scope
        self.assertEqual(c["reason"], "excluded_by_requested_scope")

    def test_acquired_topic_does_not_hide_an_unresolved_citation(self):
        q = "CET1 capital 12 CFR 999.999"
        for scope in SCOPES:
            out = self.index.answer(q, mode="hybrid", scope=scope, k=10)
            self.assertIn("cet1_capital", out["topics_matched"], scope)
            self.assertIn("citation:12 CFR 999.999", self.gap_topics(out), scope)
            self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence", scope)
        spec = "CET1 12 CFR 999.999"  # the literal request
        for mode in MODES:
            for scope in SCOPES:
                out = self.index.answer(spec, mode=mode, scope=scope)
                self.assertNotEqual(out["status"], "evidence_found", (mode, scope))
                self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence", (mode, scope))
                self.assertIn("citation:12 CFR 999.999", self.gap_topics(out))

    def test_unavailable_evidence_is_not_a_validity_determination(self):
        for c in self.cit("12 CFR 999.999 and 12 CFR 3.1", "both").values():
            self.assertEqual(c["legal_validity"], "not_determined")
        gap = self.gap_topics(self.index.answer("12 CFR 999.999", mode="exact"))["citation:12 CFR 999.999"]
        self.assertIn("unavailable evidence only", gap["note"])
        self.assertNotIn("invalid citation", gap["note"].lower().replace("not a determination that the citation is invalid", ""))

    # ---- small k and truncation
    def test_small_k_keeps_every_resolved_citation_visible(self):
        ids = [p for p, _ in self.index.exact("12 CFR 3.1 and § 217.1", 2, "current")]
        parts = {self.index.conn.execute("SELECT part_no FROM provision WHERE id=?", (p,)).fetchone()["part_no"] for p in ids}
        self.assertEqual(parts, {"3", "217"})
        both = self.index.exact("12 CFR 3.1 and § 217.1", 4, "both")
        kinds = {self.index.evidence(p, False)["kind"] == "amendment" for p, _ in both}
        self.assertEqual(kinds, {True, False})
        self.assertEqual(len(self.index.exact("12 CFR 3.1", 1, "both")), 1)

    def test_unresolved_citation_does_not_consume_the_truncation_budget(self):
        res = self.index.exact("12 CFR 999.999 and 12 CFR 3.1", 1, "current")
        self.assertEqual(len(res), 1)
        self.assertEqual(self.index.evidence(res[0][0], False)["doc_key"], "ecfr:12:3")

    # ---- existing range and scope protections still hold
    def test_range_membership_and_scope_protections(self):
        self.assertTrue(self.cit("12 CFR 3.33", "future")["12 CFR 3.33"]["resolved"])
        self.assertFalse(self.cit("12 CFR 3.37", "future")["12 CFR 3.37"]["resolved"])
        for mode in MODES:
            out = self.index.answer("12 CFR 3.33", mode=mode, scope="future")
            self.assertEqual(out["status"], "evidence_found", mode)
            self.assert_scope_invariants(out, "future")
        for q in ("12 CFR 3.1", "12 CFR 3.1 and 12 CFR 999.999", "12 CFR 3.1(z)(999)"):
            for mode in MODES:
                for scope in SCOPES:
                    for parts in (None, ["12:3"], ["12:217"]):
                        self.assert_scope_invariants(self.index.answer(q, mode=mode, scope=scope, parts=parts, k=10), scope, parts)

    def test_evidence_found_requires_every_citation_resolved(self):
        for q in ("12 CFR 3.1 and 12 CFR 999.999", "12 CFR 3.1(z)(999)", "12 CFR 3.1 and 12 CFR 217.1"):
            for mode in MODES:
                for scope in SCOPES:
                    for parts in (None, ["12:3"]):
                        out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=10)
                        if out["status"] == "evidence_found":
                            self.assertTrue(all(c["resolved"] for c in out["citations"]), (q, mode, scope, parts))

    def test_mixed_topic_finding_f1_is_fixed(self):
        # F1 (S7-P1-MIXED-SOURCE-COVERAGE-06): an acquired topic plus a named jurisdiction no longer reads evidence_found.
        out = self.index.answer("Texas CET1 capital ratio", mode="hybrid", scope="current")
        self.assertEqual(out["status"], "partial_evidence")
        self.assertTrue(out["results"])  # useful federal results are preserved
        gap = {g["topic"]: g for g in out["coverage_gaps"]}["state_law"]
        self.assertEqual(gap["coverage"], "not_acquired")
        self.assertIn("does not assert state-law applicability", gap["note"])
        self.assertEqual(out["applicability_status"], "not_assessed")


if __name__ == "__main__":
    unittest.main()
