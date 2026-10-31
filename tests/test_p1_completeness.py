"""Regression tests for S7-P1-EVIDENCE-COMPLETENESS-02: range membership, current-part lookup, scope-check coverage
and partial-coverage status."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, evaluate, search
from regintel_p1.fedreg import classify_ranges, parse_fr
from test_p1 import ECFR_HTML, asset, complete_baseline

# Instruction-only ranges. Instruction 2 quotes § 3.60 and § 3.51 inside the text being removed.
FR_RANGES = b"""<RULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF>
<AMDPAR>1. Remove and reserve subpart B, consisting of &#xA7;&#xA7; 3.30 through 3.36.</AMDPAR>
<AMDPAR>2. Amend &#xA7;&#xA7; 3.50 through 3.52 by removing &#x201C;&#xA7; 3.60 of this chapter&#x201D; and &#x201C;&#xA7; 3.51&#x201D;.</AMDPAR>
<AMDPAR>3. Amend &#xA7;&#xA7; 3.90 through 3.92 by revising paragraph (a).</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.91</SECTNO><SUBJECT>Ninety-one.</SUBJECT><P>(a) Ninety-one text.</P></SECTION>
</SUPLINF></RULE>"""

# Ranges whose interior cannot be asserted: endpoints in different parts, and lettered minors.
FR_AMBIGUOUS = b"""<PRORULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF>
<AMDPAR>1. Amend &#xA7;&#xA7; 3.70 through 217.5 by removing the word &#x201C;x&#x201D;.</AMDPAR>
<AMDPAR>2. Remove &#xA7;&#xA7; 3.80a through 3.80c.</AMDPAR>
</SUPLINF></PRORULE>"""

FR_EXPLICIT = b"""<RULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF>
<REGTEXT TITLE="12" PART="3"><AMDPAR>1. Amend &#xA7; 3.1 by revising paragraph (a) to read as follows:</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.1</SECTNO><SUBJECT>Purpose.</SUBJECT><P>(a) Purpose. Revised minimum capital text.</P></SECTION></REGTEXT>
</SUPLINF></RULE>"""


class RangeParsingTests(unittest.TestCase):
    def test_classification_is_numeric_only_when_unambiguous(self):
        got = {tuple((r["lo"], r["hi"])): r["status"] for r in classify_ranges(
            [["327.30", "327.36"], ["3.70", "217.5"], ["3.80a", "3.80c"], ["3.9", "3.2"]])}
        self.assertEqual(got[("327.30", "327.36")], "numeric_range")
        self.assertEqual(got[("3.70", "217.5")], "unresolved_ambiguous")
        self.assertEqual(got[("3.80a", "3.80c")], "unresolved_ambiguous")
        self.assertEqual(got[("3.9", "3.2")], "unresolved_ambiguous")

    def test_instruction_only_range_row_keeps_complete_instruction_endpoints_and_quoted_refs_apart(self):
        units = parse_fr(FR_RANGES, "2026-70010")[1]
        row = next(u for u in units if u["instruction"].startswith("2. Amend §§ 3.50 through 3.52"))
        self.assertEqual(row["pairing_status"], "instruction_only")
        self.assertEqual(row["explicit_targets"], ["3.50", "3.52"])
        self.assertEqual([(r["lo"], r["hi"], r["status"], r["instruction_ordinal"]) for r in row["explicit_ranges"]],
                         [("3.50", "3.52", "numeric_range", 2)])
        self.assertEqual({(r["section"], r["quoted"]) for r in row["referenced_citations"]}, {("3.60", True), ("3.51", True)})
        self.assertIn("3.60", row["instruction"])  # complete text, nothing trimmed

    def test_ambiguous_range_keeps_endpoints_and_an_unresolved_candidate(self):
        units = parse_fr(FR_AMBIGUOUS, "2026-70011")[1]
        row = next(u for u in units if u["instruction"].startswith("1. Amend §§ 3.70"))
        self.assertEqual(row["explicit_targets"], ["3.70", "217.5"])
        self.assertEqual(row["explicit_ranges"][0]["status"], "unresolved_ambiguous")
        self.assertEqual(row["unresolved_candidates"][0]["range"], ["3.70", "217.5"])
        self.assertIn("unresolved", row["unresolved_candidates"][0]["reason"])


class CompletenessIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        docs = {
            "ecfr:12:3": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 3", "title": "Capital",
                          "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                          "asset": asset(root, "e.html", ECFR_HTML.encode())},
            "fr:2026-70010": {**meta, "document_number": "2026-70010", "title": "Ranges", "document_type": "Rule",
                              "effective_on": "2027-01-18", "action": "Final rule.", "asset": asset(root, "r.xml", FR_RANGES)},
            "fr:2026-70011": {**meta, "document_number": "2026-70011", "title": "Ambiguous", "document_type": "Proposed Rule",
                              "comments_close_on": "2026-12-01", "asset": asset(root, "a.xml", FR_AMBIGUOUS)},
            "fr:2026-70012": {**meta, "document_number": "2026-70012", "title": "Explicit", "document_type": "Rule",
                              "effective_on": "2027-02-01", "action": "Final rule.", "asset": asset(root, "x.xml", FR_EXPLICIT)},
        }
        complete_baseline(root, docs)  # the declared eCFR baseline dependencies of the acquired topics
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "c.sqlite")
        conn, cls.counts = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def keys(self, delta):
        return [g["doc_key"] for g in delta["instruments"]]

    # ---- (1) range membership
    def test_interior_section_is_found_with_complete_instruction_and_range_provenance(self):
        d = self.index.delta("12 CFR 3.33")
        self.assertEqual(self.keys(d), ["fr:2026-70010"])
        unit = d["instruments"][0]["units"][0]
        self.assertEqual(unit["match"], "range_membership")
        self.assertEqual(unit["range"], {"lo": "3.30", "hi": "3.36", "instruction_ordinal": 1})
        self.assertEqual(unit["instruction"], "1. Remove and reserve subpart B, consisting of §§ 3.30 through 3.36.")
        self.assertEqual(unit["review_status"], "parsed_unreviewed")
        self.assertEqual(d["applicability_status"], "not_assessed")
        row = self.index.conn.execute("SELECT status, review_status FROM amendment_range WHERE lo_section='3.30'").fetchone()
        self.assertEqual((row["status"], row["review_status"]), ("numeric_range", "parsed_unreviewed"))

    def test_endpoints_stay_explicit_and_outside_range_sections_are_excluded(self):
        for cite in ("12 CFR 3.30", "12 CFR 3.36"):
            u = self.index.delta(cite)["instruments"][0]["units"][0]
            self.assertEqual(u["match"], "explicit_target", cite)
        for cite in ("12 CFR 3.29", "12 CFR 3.37", "12 CFR 3.100"):
            self.assertNotIn("fr:2026-70010", self.keys(self.index.delta(cite)), cite)

    def test_quoted_cross_references_in_range_instructions_never_become_targets(self):
        self.assertNotIn("fr:2026-70010", self.keys(self.index.delta("12 CFR 3.60")))
        d51 = self.index.delta("12 CFR 3.51")  # interior of the 3.50-3.52 range, and also quoted in that instruction
        units = d51["instruments"][0]["units"]
        self.assertEqual([u["match"] for u in units], ["range_membership"])
        self.assertTrue(units[0]["instruction"].startswith("2. Amend §§ 3.50 through 3.52"))
        self.assertIn("§ 3.60 of this chapter", units[0]["instruction"])

    def test_carried_interior_text_and_range_unit_are_both_kept_separate(self):
        d = self.index.delta("12 CFR 3.91")
        matches = sorted(u["match"] for g in d["instruments"] for u in g["units"])
        self.assertEqual(matches, ["explicit_target", "range_membership"])
        carried = next(u for g in d["instruments"] for u in g["units"] if u["carried_text"].startswith("(a) Ninety-one"))
        self.assertEqual(carried["explicit_targets"], ["3.91"])

    def test_exact_future_retrieval_reaches_interior_sections_and_honors_filters(self):
        res = self.index.exact("12 CFR 3.33", 20, "future")
        self.assertEqual([self.index.evidence(p, False)["doc_key"] for p, _ in res], ["fr:2026-70010"])
        self.assertEqual(self.index.exact("12 CFR 3.37", 20, "future"), [])
        self.assertEqual(self.index.exact("12 CFR 3.33", 20, "current"), [])  # no baseline section 3.33 in the fixture
        self.assertEqual(self.index.exact("12 CFR 3.33", 20, "future", ["12:217"]), [])
        both = self.index.answer("12 CFR 3.33", mode="hybrid", scope="future")["results"]
        self.assertTrue(both and all(r["kind"] in ("amendment", "fr_preamble", "fr_table") for r in both))

    def test_ambiguous_ranges_are_unresolved_not_membership(self):
        d75 = self.index.delta("12 CFR 3.75")
        self.assertEqual(d75["instruments"], [])
        self.assertEqual(self.index.exact("12 CFR 3.75", 20, "future"), [])
        d70 = self.index.delta("12 CFR 3.70")  # an endpoint is still an explicit target
        self.assertEqual(self.keys(d70), ["fr:2026-70011"])
        cands = [c for c in d70["unresolved_candidates"] if c["pairing_status"] == "ambiguous_range"]
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["candidate_instructions"][0]["range"], ["3.70", "217.5"])
        rows = self.index.conn.execute("SELECT status, review_status, lo_minor FROM amendment_range WHERE status!='numeric_range'").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["review_status"] == "unresolved_candidate" and r["lo_minor"] is None for r in rows))
        self.assertEqual(self.index.delta("12 CFR 3.80b")["instruments"], [])

    def test_explicit_single_section_amendment_is_not_a_range_match(self):
        u = self.index.delta("12 CFR 3.1")["instruments"]
        self.assertTrue(all(x["match"] == "explicit_target" for g in u for x in g["units"]))

    # ---- (2) current part lookup
    def test_current_part_citation_returns_part_and_sections_without_federal_register(self):
        res = self.index.exact("12 CFR Part 3", 50, "current")
        ev = [self.index.evidence(p, False) for p, _ in res]
        self.assertIn("part", {e["kind"] for e in ev})
        self.assertIn("section", {e["kind"] for e in ev})
        self.assertTrue(all(e["doc_key"] == "ecfr:12:3" for e in ev))
        self.assertTrue(all(e["status"]["state"].startswith("current_baseline") for e in ev))
        self.assertEqual(self.index.exact("12 CFR Part 3", 50, "current", ["12:217"]), [])
        hy = self.index.answer("12 CFR Part 3", mode="hybrid", scope="current", k=20)["results"]
        self.assertTrue(hy and not any(r["doc_key"].startswith("fr:") for r in hy))

    def test_both_scopes_at_k2_for_part_and_section_citations(self):
        for q in ("12 CFR Part 3", "12 CFR 3.1"):
            kinds = {self.index.evidence(p, False)["kind"] == "amendment" for p, _ in self.index.exact(q, 2, "both")}
            self.assertEqual(kinds, {True, False}, q)

    def test_part_filter_before_truncation_for_part_citations(self):
        for scope in ("current", "future", "both"):
            res = self.index.exact("12 CFR Part 3", 1, scope, ["12:3"])
            self.assertEqual(len(res), 1, scope)
            self.assertEqual(self.index.exact("12 CFR Part 3", 5, scope, ["12:217"]), [], scope)

    # ---- (3) check_citation_scope exercises exact and hybrid, and detects a hybrid-only leak
    ITEM = {"id": "T1", "query": "12 CFR 3.1", "future_nonempty": True, "current_nonempty": True}

    def test_check_passes_on_a_correct_index_and_requires_nonempty_current(self):
        ok = evaluate.check_citation_scope(self.index, self.ITEM)
        self.assertTrue(ok["passed"], ok["failures"])
        none = evaluate.check_citation_scope(self.index, {"id": "T2", "query": "12 CFR 3.33", "current_nonempty": True})
        self.assertFalse(none["passed"])
        self.assertTrue(any("no current records" in f for f in none["failures"]))

    def test_hybrid_only_scope_violation_is_detected(self):
        class LeakyHybrid(search.Index):
            def hybrid(self, query, k=50, scope="both", parts=None):
                base = super().hybrid(query, k, scope, parts)
                if scope == "future":  # leak baseline records only through the hybrid path
                    base = [(pid, 1.0) for pid in self._exact_current(None, "3", "3.1", "", None)[:2]] + base
                return base

        bad = evaluate.check_citation_scope(LeakyHybrid(self.db), self.ITEM)
        self.assertFalse(bad["passed"])
        self.assertTrue(any(f.startswith("hybrid/future") for f in bad["failures"]), bad["failures"])
        self.assertFalse(any(f.startswith("exact/") for f in bad["failures"]), bad["failures"])

    def test_exact_current_regression_is_detected_by_the_nonempty_requirement(self):
        class BrokenCurrentPart(search.Index):
            def _exact_current(self, title, part, section, paras, parts):
                return [] if not section else super()._exact_current(title, part, section, paras, parts)

        item = {"id": "T3", "query": "12 CFR Part 3", "current_nonempty": True, "current_includes_kind": "part"}
        self.assertTrue(evaluate.check_citation_scope(self.index, item)["passed"])
        bad = evaluate.check_citation_scope(BrokenCurrentPart(self.db), item)
        self.assertFalse(bad["passed"])
        self.assertTrue(any("exact/current" in f for f in bad["failures"]), bad["failures"])

    # ---- (4) partial coverage
    def test_partial_topic_coverage_with_results_is_partial_evidence(self):
        q = "customer identification program for minimum capital"
        out = self.index.answer(q, scope="current")
        self.assertTrue(out["results"])
        self.assertEqual(out["status"], "partial_evidence")
        self.assertIn("bsa_aml_program", [g["topic"] for g in out["coverage_gaps"]])
        self.assertEqual(out["applicability_status"], "not_assessed")

    def test_fully_acquired_topics_stay_evidence_found_and_unacquired_only_stays_missing(self):
        self.assertEqual(self.index.answer("minimum common equity tier 1 capital ratio", scope="current")["status"],
                         "evidence_found")
        self.assertEqual(self.index.answer("CECL under ASC 326")["status"], "missing_evidence")
        self.assertEqual(self.index.answer("ASC 326 CECL and the community bank leverage ratio minimum capital")["status"],
                         "partial_evidence")

    def test_partial_coverage_without_results_is_missing_evidence(self):
        out = self.index.answer("customer identification program zzzzqqq", scope="current", parts=["12:217"])
        self.assertEqual(out["results"], [])
        self.assertEqual(out["status"], "missing_evidence")


if __name__ == "__main__":
    unittest.main()
