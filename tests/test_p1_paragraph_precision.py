"""Regression tests for S7-P1-FUTURE-PARAGRAPH-PRECISION-05.

Matching the first designator of a requested paragraph (or a lead of the carried text) must never establish a deeper
requested path. A future paragraph resolves only when an amendatory instruction names the complete path.
"""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from test_p1 import asset

ECFR_P = """<div class="part" id="part-3"><h1>PART 3—CAPITAL</h1>
<div class="section" id="3.12"><h4>§ 3.12 Community bank leverage ratio framework.</h4>
<div id="p-3.12(c)"><p class="indent-1" data-title="3.12(c)">(c) Treatment when ceasing to qualify.</p>
<div id="p-3.12(c)(1)"><p class="indent-2" data-title="3.12(c)(1)">(1) A grace period of four reporting periods.</p></div></div></div>
<div class="section" id="3.22"><h4>§ 3.22 Adjustments and deductions.</h4>
<div id="p-3.22(a)"><p class="indent-1" data-title="3.22(a)">(a) General adjustments and deductions.</p></div></div></div>"""

# 3.12: names (c)(1) and (c)(7); the carried text opens with lead (c) and carries (1) and (7).
FR_C = b"""<RULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF>
<REGTEXT TITLE="12" PART="3"><AMDPAR>1. Amend &#xA7; 3.12 by revising paragraph (c)(1) and adding paragraph (c)(7) to read as follows:</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.12</SECTNO><SUBJECT>Community bank leverage ratio framework.</SUBJECT>
<P>(c) * * *</P><P>(1) Revised grace period text.</P><P>(7) Added paragraph seven text.</P></SECTION></REGTEXT>
</SUPLINF></RULE>"""

# 3.22: names only (i); the carried text also carries children (1) and (2) that the instruction never names.
FR_I = b"""<RULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF>
<REGTEXT TITLE="12" PART="3"><AMDPAR>1. Amend &#xA7; 3.22 by adding paragraph (i) to read as follows:</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.22</SECTNO><SUBJECT>Adjustments.</SUBJECT>
<P>(i) New paragraph i text.</P><P>(1) Child one text.</P><P>(2) Child two text.</P></SECTION></REGTEXT>
</SUPLINF></RULE>"""

# One instruction, two sections: the named paragraph cannot be attributed to either section.
FR_MULTI = b"""<PRORULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF>
<REGTEXT TITLE="12" PART="3"><AMDPAR>1. Amend &#xA7;&#xA7; 3.12 and 3.22 by revising paragraph (d).</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.12</SECTNO><SUBJECT>CBLR.</SUBJECT><P>(d) Twelve d text.</P></SECTION>
<SECTION><SECTNO>&#xA7; 3.22</SECTNO><SUBJECT>Adjustments.</SUBJECT><P>(d) Twenty-two d text.</P></SECTION></REGTEXT>
</SUPLINF></PRORULE>"""

# Shorthand: (c)(5) through (7) names the two ends, not the interior (c)(6).
FR_RANGE = b"""<RULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF>
<AMDPAR>1. Amend &#xA7; 3.12 by revising paragraphs (c)(5) through (7), and by removing &#x201C;paragraph (z)&#x201D;.</AMDPAR>
</SUPLINF></RULE>"""

MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")
FUTURE_KINDS = ("amendment", "fr_preamble", "fr_table")
PART_FILTERS = (None, ["12:3"], ["12:217"])


class PathEvidenceParsingTests(unittest.TestCase):
    def test_complete_paths_only_from_actions_outside_quotes(self):
        from regintel_p1.fedreg import named_paragraph_paths as paths
        text = ("2. Amend § 3.12 by: a. In paragraphs (a)(1) and (a)(2)(i), removing the text “paragraph (z)”; "
                "b. Removing paragraph (a)(4); c. Revising paragraph (c)(1); f. Adding paragraph (c)(7).")
        self.assertEqual(paths(text), ["(a)(1)", "(a)(2)(i)", "(a)(4)", "(c)(1)", "(c)(7)"])
        self.assertEqual(paths("Amend § 3.22 by adding paragraph (i) to read as follows:"), ["(i)"])
        self.assertEqual(paths("Revise paragraphs (e), (f), and (g)."), ["(e)", "(f)", "(g)"])

    def test_shorthand_names_ends_not_interior_and_cross_references_are_not_evidence(self):
        from regintel_p1.fedreg import named_paragraph_paths as paths
        self.assertEqual(paths("Revise paragraphs (c)(5) through (7)."), ["(c)(5)", "(c)(7)"])
        self.assertEqual(paths("Amend § 6.2 by revising the definition to cross-reference paragraph (b) of § 3.22."), [])

    def test_paragraph_evidence_requires_a_single_section_target(self):
        from regintel_p1.fedreg import parse_fr
        single = [u for u in parse_fr(FR_I, "2026-70022")[1] if u["section_no"] == "3.22"][0]
        self.assertEqual(single["explicit_paragraphs"], {"paths": ["(i)"], "status": "attributed"})
        multi = [u for u in parse_fr(FR_MULTI, "2026-70023")[1] if u["text"]]
        self.assertTrue(multi)
        for u in multi:
            self.assertEqual(u["explicit_paragraphs"], {"paths": [], "status": "unattributed_multiple_targets"})


class FuturePathResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        final = {**meta, "document_type": "Rule", "effective_on": "2027-02-01", "action": "Final rule."}
        docs = {
            "ecfr:12:3": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 3", "title": "Capital",
                          "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                          "asset": asset(root, "e.html", ECFR_P.encode())},
            "fr:2026-70021": {**final, "document_number": "2026-70021", "title": "C rule", "asset": asset(root, "c.xml", FR_C)},
            "fr:2026-70022": {**final, "document_number": "2026-70022", "title": "I rule", "asset": asset(root, "i.xml", FR_I)},
            "fr:2026-70023": {**meta, "document_number": "2026-70023", "title": "Multi", "document_type": "Proposed Rule",
                              "comments_close_on": "2026-12-01", "asset": asset(root, "m.xml", FR_MULTI)},
            "fr:2026-70024": {**final, "document_number": "2026-70024", "title": "Range", "asset": asset(root, "r.xml", FR_RANGE)},
        }
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "p.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cit(self, query, scope="both", parts=None):
        return {c["citation"]: c for c in self.index.resolve_citations(query, scope, parts)}

    def assert_invariants(self, out, scope, parts):
        self.assertEqual(out["applicability_status"], "not_assessed")
        for c in out["citations"]:
            self.assertEqual(c["legal_validity"], "not_determined")
        for r in out["results"]:
            if scope == "future":
                self.assertTrue(r["kind"] in FUTURE_KINDS and not r["status"]["state"].startswith("current"), r["citation"])
            if scope == "current":
                self.assertTrue(r["doc_key"].startswith("ecfr:"), r["citation"])
            if parts:
                row = self.index.conn.execute("SELECT title_no, part_no FROM provision WHERE id=?", (r["provision_id"],)).fetchone()
                self.assertIn(f"{row['title_no']}:{row['part_no']}", parts)

    # ---- the starting-HEAD defect: a matching first designator established a deeper path
    def test_deeper_unsupported_paths_stay_section_only_in_future_scope(self):
        for q in ("12 CFR 3.12(c)(999)", "12 CFR 3.22(i)(999)"):
            c = self.cit(q, "future")[q]
            self.assertFalse(c["resolved"], q)
            self.assertEqual(c["achieved"], "section_only", q)
            self.assertEqual(c["reason"], "paragraph_precision_unresolved", q)
            self.assertEqual(c["future_paragraph_support"], [], q)
            self.assertGreater(c["by_scope"]["future"], 0, q)  # section-level discovery retained
            self.assertEqual(c["legal_validity"], "not_determined")

    def test_path_never_inferred_from_leads_or_order_of_carried_paragraphs(self):
        # (i)(1) and (c)(1)-under-(c) both appear as carried leads/order; only a full path in an instruction counts.
        c = self.cit("12 CFR 3.22(i)(1)", "future")["12 CFR 3.22(i)(1)"]
        self.assertFalse(c["resolved"])
        self.assertEqual((c["achieved"], c["reason"]), ("section_only", "paragraph_precision_unresolved"))
        c2 = self.cit("12 CFR 3.12(c)(6)", "future")["12 CFR 3.12(c)(6)"]  # interior of the "(c)(5) through (7)" shorthand
        self.assertFalse(c2["resolved"])

    def test_current_scope_unchanged_for_missing_deep_paragraphs(self):
        for q, reason in (("12 CFR 3.12(c)(999)", "paragraph_not_found_in_acquired_section"),
                          ("12 CFR 3.22(i)(999)", "paragraph_not_found_in_acquired_section")):
            c = self.cit(q, "current")[q]
            self.assertFalse(c["resolved"])
            self.assertEqual(c["reason"], reason)
            self.assertEqual(self.index.exact(q, 20, "current"), [])  # never replaced by the parent section

    # ---- supported controls
    def test_supported_future_paths_resolve_with_provenance(self):
        for q in ("12 CFR 3.12(c)(1)", "12 CFR 3.12(c)(7)", "12 CFR 3.22(i)"):
            c = self.cit(q, "future")[q]
            self.assertTrue(c["resolved"], q)
            self.assertEqual(c["achieved"], "paragraph")
            self.assertTrue(c["future_paragraph_support"], q)
            self.assertTrue(all(s["doc_key"].startswith("fr:") for s in c["future_paragraph_support"]))
        # instruments stay separate: exactly the two fixture rules that name (c)(7) support it, none of the others
        self.assertEqual({s["doc_key"] for s in self.cit("12 CFR 3.12(c)(7)", "future")["12 CFR 3.12(c)(7)"]["future_paragraph_support"]},
                         {"fr:2026-70021", "fr:2026-70024"})
        self.assertEqual({s["doc_key"] for s in self.cit("12 CFR 3.22(i)", "future")["12 CFR 3.22(i)"]["future_paragraph_support"]},
                         {"fr:2026-70022"})

    def test_current_3_12_c_1_control_and_citation_only_controls_stay_evidence_found(self):
        self.assertTrue(self.cit("12 CFR 3.12(c)(1)", "current")["12 CFR 3.12(c)(1)"]["resolved"])
        for q in ("12 CFR 3.12(c)(1)", "12 CFR 3.12(c)(7) and 12 CFR 3.12(c)(1)"):
            for mode in MODES:
                for scope in ("future", "both") if "(7)" in q else SCOPES:
                    out = self.index.answer(q, mode=mode, scope=scope, k=10)
                    self.assertEqual(out["status"], "evidence_found", (q, mode, scope))
                    self.assertEqual([g for g in out["coverage_gaps"] if g["topic"].startswith("citation:")], [])
                    self.assert_invariants(out, scope, None)
        for mode in MODES:
            out = self.index.answer("12 CFR 3.22(i)", mode=mode, scope="future")
            self.assertEqual(out["status"], "evidence_found", mode)

    def test_multi_section_instruction_does_not_attribute_a_paragraph_to_either_section(self):
        for q in ("12 CFR 3.12(d)", "12 CFR 3.22(d)"):
            c = self.cit(q, "future")[q]
            self.assertFalse(c["resolved"], q)
            self.assertEqual((c["achieved"], c["reason"]), ("section_only", "paragraph_precision_unresolved"))

    # ---- mixed valid/unresolved, modes, scopes, part filters
    def test_mixed_valid_and_unresolved_citations_report_each_separately(self):
        cases = (("12 CFR 3.12(c)(1) and 12 CFR 3.12(c)(999)", "12 CFR 3.12(c)(999)", "12 CFR 3.12(c)(1)"),
                 ("12 CFR 3.22(i) and 12 CFR 3.22(i)(999)", "12 CFR 3.22(i)(999)", "12 CFR 3.22(i)"))
        for q, bad, good in cases:
            for mode in MODES:
                for scope in SCOPES:
                    out = self.index.answer(q, mode=mode, scope=scope, k=10)
                    cites = {c["citation"]: c for c in out["citations"]}
                    self.assertFalse(cites[bad]["resolved"], (q, mode, scope))
                    self.assertIn(f"citation:{bad}", {g["topic"] for g in out["coverage_gaps"]}, (q, mode, scope))
                    self.assertNotIn(f"citation:{good}", {g["topic"] for g in out["coverage_gaps"]} if cites[good]["resolved"] else set())
                    self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope))
                    self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence", (q, mode, scope))
                    self.assert_invariants(out, scope, None)
            self.assertTrue(self.cit(q, "future")[good]["resolved"], q)  # the valid half resolves in future scope

    def test_unresolved_deep_paths_across_modes_scopes_and_part_filters(self):
        for q in ("12 CFR 3.12(c)(999)", "12 CFR 3.22(i)(999)", "12 CFR 3.12(c)(1) and 12 CFR 3.12(c)(999)"):
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PART_FILTERS:
                        out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=10)
                        self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope, parts))
                        self.assertTrue([g for g in out["coverage_gaps"] if g["topic"].startswith("citation:")], (q, mode, scope, parts))
                        self.assert_invariants(out, scope, parts)
                        if parts == ["12:217"]:  # excluding filter: nothing from part 3 may leak through
                            self.assertEqual(out["results"], [], (q, mode, scope))
                            self.assertEqual(out["status"], "missing_evidence")

    def test_excluding_part_filter_reason_for_the_valid_citation(self):
        out = self.index.answer("12 CFR 3.12(c)(1) and 12 CFR 3.12(c)(999)", mode="exact", scope="future", parts=["12:217"])
        reasons = {c["citation"]: c["reason"] for c in out["citations"]}
        self.assertEqual(reasons["12 CFR 3.12(c)(1)"], "excluded_by_part_filter")
        self.assertIn(reasons["12 CFR 3.12(c)(999)"], ("excluded_by_part_filter",))

    def test_small_k_and_truncation_keep_section_level_records_visible_and_flagged(self):
        res = self.index.exact("12 CFR 3.12(c)(999)", 1, "future")
        self.assertEqual(len(res), 1)
        out = self.index.answer("12 CFR 3.12(c)(999)", mode="exact", scope="future", k=1)
        self.assertEqual(out["status"], "partial_evidence")
        self.assertEqual(out["citations"][0]["achieved"], "section_only")


if __name__ == "__main__":
    unittest.main()
