"""Regression tests for S7-P1-EVIDENCE-BOUNDARIES-01: amendment-target integrity and retrieval scope filtering."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.fedreg import parse_fr, parse_instruction
from test_p1 import ECFR_HTML, asset

# Rule 70001: the § 6.2 instruction cites § 3.1 only inside quotation marks and in "as defined in" prose.
FR_EMBEDDED = b"""<RULE><PREAMB><CFR>12 CFR Parts 3 and 6</CFR><SUBJECT>Embedded</SUBJECT></PREAMB><SUPLINF>
<REGTEXT TITLE="12" PART="3"><AMDPAR>1. Amend &#xA7; 3.1 by revising paragraph (a) to read as follows:</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.1</SECTNO><SUBJECT>Purpose.</SUBJECT><P>(a) Purpose. New section 3.1 text.</P></SECTION></REGTEXT>
<REGTEXT TITLE="12" PART="6"><AMDPAR>2. Amend &#xA7; 6.2 by removing &#x201C;a bank under &#xA7; 3.1&#x201D; in the definition of &#x201C;Total assets&#x201D; as defined in &#xA7; 3.1 of this chapter.</AMDPAR>
<SECTION><SECTNO>&#xA7; 6.2</SECTNO><SUBJECT>Definitions.</SUBJECT><P>Total assets means section 6.2 text.</P></SECTION></REGTEXT>
</SUPLINF></RULE>"""

# Proposal 70002: one instruction names two sections; a third section's text rides along without being named.
FR_MULTI = b"""<PRORULE><PREAMB><CFR>12 CFR Part 3</CFR><RIN>RIN 1557-AF98</RIN></PREAMB><SUPLINF>
<REGTEXT TITLE="12" PART="3"><AMDPAR>1. Amend &#xA7;&#xA7; 3.1 and 3.2 by revising paragraph (a).</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.1</SECTNO><SUBJECT>Purpose.</SUBJECT><P>(a) Alpha text for 3.1.</P></SECTION>
<SECTION><SECTNO>&#xA7; 3.2</SECTNO><SUBJECT>Definitions.</SUBJECT><P>(a) Beta text for 3.2.</P></SECTION>
<SECTION><SECTNO>&#xA7; 3.3</SECTNO><SUBJECT>Other.</SUBJECT><P>(a) Gamma text for 3.3 never named.</P></SECTION></REGTEXT>
</SUPLINF></PRORULE>"""

# Rule 70003: instruction-only edits, a multi-target instruction-only edit, and two instructions on one section.
FR_ONLY = b"""<RULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF>
<AMDPAR>1. Amend &#xA7; 3.1(a) by removing &#x201C;national bank&#x201D;.</AMDPAR>
<AMDPAR>2. Amend &#xA7;&#xA7; 3.2 and 3.4 by removing the words &#x201C;&#xA7; 9.9 of this chapter&#x201D;.</AMDPAR>
<AMDPAR>3. Amend &#xA7; 3.40 by revising paragraph (a).</AMDPAR>
<AMDPAR>4. Amend &#xA7; 3.40 by adding paragraph (b).</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.40</SECTNO><SUBJECT>Forty.</SUBJECT><P>(a) Forty text.</P></SECTION>
<AMDPAR>5. Amend part 3 by revising the section listed in &#xA7; 3.41.</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.41</SECTNO><SUBJECT>Forty-one.</SUBJECT><P>(a) Forty-one text.</P></SECTION>
</SUPLINF></RULE>"""


class InstructionParsingTests(unittest.TestCase):
    def test_embedded_references_are_not_targets(self):
        p = parse_instruction("2. Amend § 6.2 by removing “a bank under § 3.22” as defined in § 15.2 of this chapter.")
        self.assertEqual(p["targets"], ["6.2"])
        refs = {(r["section"], r["quoted"]) for r in p["references"]}
        self.assertEqual(refs, {("3.22", True), ("15.2", False)})

    def test_target_constructions(self):
        self.assertEqual(parse_instruction("2. In § 3.22, revise paragraph (c).")["targets"], ["3.22"])
        self.assertEqual(parse_instruction("Section 3.1 is amended to read as follows:")["targets"], ["3.1"])
        self.assertEqual(parse_instruction("3. Remove and reserve § 3.303.")["targets"], ["3.303"])
        self.assertEqual(parse_instruction("Amend paragraph (c) of § 3.22 by removing § 3.9.")["targets"], ["3.22", "3.9"])
        multi = parse_instruction("Amend §§ 3.10 and 3.11 by revising paragraph (a).")
        self.assertEqual(multi["targets"], ["3.10", "3.11"])
        self.assertEqual(multi["ranges"], [])
        rng = parse_instruction("Revise §§ 3.1 through 3.5.")
        self.assertEqual(rng["ranges"], [["3.1", "3.5"]])
        self.assertEqual(parse_instruction("The authority citation for part 3 is revised.")["parts"], ["3"])
        self.assertEqual(parse_instruction("Amend part 217 by adding subpart Z.")["parts"], ["217"])

    def test_lowercase_in_citation_is_a_reference(self):
        p = parse_instruction("Revise the definition as set out in § 3.22.")
        self.assertEqual(p["targets"], [])
        self.assertEqual([r["section"] for r in p["references"]], ["3.22"])


class PairingTests(unittest.TestCase):
    def units(self, xml, doc="2026-70000"):
        return parse_fr(xml, doc)[1]

    def test_carried_text_pairs_only_with_the_instruction_targeting_it(self):
        units = {u["section_no"]: u for u in self.units(FR_EMBEDDED) if u["text"]}
        self.assertEqual(units["3.1"]["explicit_targets"], ["3.1"])
        self.assertIn("Amend § 3.1", units["3.1"]["instruction"])
        self.assertEqual(units["6.2"]["explicit_targets"], ["6.2"])
        self.assertTrue(units["6.2"]["instruction"].startswith("2. Amend § 6.2"))
        self.assertIn("3.1", {r["section"] for r in units["6.2"]["referenced_citations"]})
        self.assertEqual(units["6.2"]["pairing_status"], "explicit")

    def test_multi_section_instruction_gives_each_section_its_own_text_only(self):
        units = [u for u in self.units(FR_MULTI) if u["text"]]
        by = {u["section_no"]: u for u in units}
        self.assertEqual(by["3.1"]["explicit_targets"], ["3.1"])
        self.assertEqual(by["3.2"]["explicit_targets"], ["3.2"])
        self.assertIn("Alpha", by["3.1"]["text"])
        self.assertNotIn("Beta", by["3.1"]["text"])
        self.assertNotIn("Alpha", by["3.2"]["text"])
        self.assertEqual(by["3.1"]["instruction"], by["3.2"]["instruction"])  # the one complete instruction, both rows
        self.assertEqual(by["3.1"]["instruction_targets_all"]["targets"], ["3.1", "3.2"])

    def test_unnamed_section_text_is_not_paired_with_a_multi_section_instruction(self):
        by = {u["section_no"]: u for u in self.units(FR_MULTI) if u["text"]}
        gamma = by["3.3"]
        self.assertEqual(gamma["pairing_status"], "carried_text_unpaired")
        self.assertEqual(gamma["explicit_targets"], [])
        self.assertEqual(gamma["instruction"], "")

    def test_instruction_only_amendments_keep_complete_text_and_all_targets(self):
        units = self.units(FR_ONLY)
        only = [u for u in units if u["pairing_status"] == "instruction_only"]
        by_targets = {tuple(u["explicit_targets"]): u for u in only}
        self.assertIn(("3.1",), by_targets)
        self.assertIn(("3.2", "3.4"), by_targets)
        multi = by_targets[("3.2", "3.4")]
        self.assertEqual(multi["text"], "")
        self.assertTrue(multi["instruction"].startswith("2. Amend §§ 3.2 and 3.4"))
        self.assertEqual([r["section"] for r in multi["referenced_citations"]], ["9.9"])
        self.assertTrue(multi["referenced_citations"][0]["quoted"])

    def test_two_instructions_on_one_section_are_both_retained(self):
        row = next(u for u in self.units(FR_ONLY) if u["section_no"] == "3.40")
        self.assertEqual(row["pairing_status"], "multiple_instructions_same_target")
        self.assertIn("3. Amend § 3.40 by revising", row["instruction"])
        self.assertIn("4. Amend § 3.40 by adding", row["instruction"])
        self.assertEqual(row["instruction_ordinals"], [3, 4])

    def test_reference_only_match_is_an_unresolved_candidate_not_a_target(self):
        row = next(u for u in self.units(FR_ONLY) if u["section_no"] == "3.41" and u["text"])
        self.assertEqual(row["pairing_status"], "carried_text_unpaired")
        self.assertEqual(row["explicit_targets"], [])
        cand = row["unresolved_candidates"]
        self.assertEqual(len(cand), 1)
        self.assertTrue(cand[0]["instruction"].startswith("5. Amend part 3 by revising the section listed in § 3.41"))
        self.assertEqual(cand[0]["reason"], "names the section only as a reference")

    def test_authority_text_pairs_by_part_number(self):
        xml = b"""<PRORULE><PREAMB><CFR>12 CFR Parts 3 and 217</CFR></PREAMB><SUPLINF>
<AMDPAR>1. The authority citation for part 217 continues to read as follows:</AMDPAR>
<PART><HD SOURCE="HED">PART 217--CAPITAL</HD><AUTH><HD>Authority:</HD><P>217 authority.</P></AUTH></PART>
<AMDPAR>2. The authority citation for part 3 is revised.</AMDPAR>
<PART><HD SOURCE="HED">PART 3--CAPITAL</HD><AUTH><HD>Authority:</HD><P>3 authority.</P></AUTH></PART></SUPLINF></PRORULE>"""
        auth = {u["part_no"]: u for u in self.units(xml)}
        self.assertTrue(auth["217"]["instruction"].startswith("1. The authority citation for part 217"))
        self.assertIn("217 authority", auth["217"]["text"])
        self.assertNotIn("3 authority", auth["217"]["text"].replace("217 authority", ""))
        self.assertTrue(auth["3"]["instruction"].startswith("2. The authority citation for part 3"))
        self.assertEqual(auth["3"]["explicit_parts"], ["3"])


class IndexBoundaryTests(unittest.TestCase):
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
            "fr:2026-70001": {**meta, "document_number": "2026-70001", "title": "Embedded refs", "document_type": "Rule",
                              "effective_on": "2027-01-18", "action": "Final rule.", "asset": asset(root, "a.xml", FR_EMBEDDED)},
            "fr:2026-70002": {**meta, "document_number": "2026-70002", "title": "Multi", "document_type": "Proposed Rule",
                              "comments_close_on": "2026-12-01", "regulation_id_numbers": ["1557-AF98"],
                              "asset": asset(root, "b.xml", FR_MULTI)},
            "fr:2026-70003": {**meta, "document_number": "2026-70003", "title": "Instruction only", "document_type": "Rule",
                              "effective_on": "2027-02-01", "action": "Final rule.", "asset": asset(root, "c.xml", FR_ONLY)},
        }
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        conn, cls.counts = build.build(root, Path(cls.tmp.name) / "b.sqlite", "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(str(Path(cls.tmp.name) / "b.sqlite"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def units_of(self, delta, key):
        return next(g for g in delta["instruments"] if g["doc_key"] == key)["units"]

    # ---- amendment targets -> delta
    def test_delta_excludes_amendment_that_only_cites_the_section(self):
        d = self.index.delta("12 CFR 3.1")
        embedded = self.units_of(d, "fr:2026-70001")
        self.assertEqual([u["explicit_targets"] for u in embedded], [["3.1"]])
        self.assertIn("New section 3.1 text", embedded[0]["carried_text"])
        self.assertNotIn("section 6.2 text", json.dumps(d))
        d62 = self.index.delta("12 CFR 6.2")
        self.assertEqual([g["doc_key"] for g in d62["instruments"]], ["fr:2026-70001"])
        self.assertEqual(d62["baseline"], None)

    def test_references_are_separate_relationships_and_never_amends(self):
        rows = self.index.conn.execute(
            """SELECT rel.rel_type, rel.dst_citation, rel.review_status FROM relationship rel
               JOIN provision p ON p.id=rel.src_provision_id JOIN source_document d ON d.id=p.doc_id
               WHERE d.doc_key='fr:2026-70001' AND p.section_no='6.2'""").fetchall()
        kinds = {(r["rel_type"], r["dst_citation"]) for r in rows}
        self.assertIn(("amends", "12 CFR 6.2"), kinds)
        self.assertIn(("references", "12 CFR 3.1"), kinds)
        self.assertNotIn(("amends", "12 CFR 3.1"), kinds)
        self.assertTrue(all(r["review_status"] in ("parsed_unreviewed", "regex_unreviewed") for r in rows))

    def test_multi_section_instruction_never_leaks_one_sections_text_into_another(self):
        d1, d2 = self.index.delta("12 CFR 3.1"), self.index.delta("12 CFR 3.2")
        t1 = json.dumps(self.units_of(d1, "fr:2026-70002"))
        t2 = json.dumps(self.units_of(d2, "fr:2026-70002"))
        self.assertIn("Alpha text for 3.1", t1)
        self.assertNotIn("Beta", t1)
        self.assertIn("Beta text for 3.2", t2)
        self.assertNotIn("Alpha", t2)

    def test_unpaired_section_is_a_candidate_outside_instruments(self):
        d = self.index.delta("12 CFR 3.3")
        self.assertEqual(d["instruments"], [])
        self.assertEqual([c["doc_key"] for c in d["unresolved_candidates"]], ["fr:2026-70002"])
        self.assertEqual(d["unresolved_candidates"][0]["pairing_status"], "carried_text_unpaired")
        d41 = self.index.delta("12 CFR 3.41")
        self.assertEqual(d41["instruments"], [])
        self.assertEqual(len(d41["unresolved_candidates"]), 1)
        self.assertTrue(d41["unresolved_candidates"][0]["candidate_instructions"][0]["instruction"].startswith("5. Amend part 3"))

    def test_instruction_only_amendments_reach_every_explicit_target_and_not_quoted_citations(self):
        for cite in ("12 CFR 3.2", "12 CFR 3.4"):
            keys = [g["doc_key"] for g in self.index.delta(cite)["instruments"]]
            self.assertIn("fr:2026-70003", keys)
        self.assertNotIn("fr:2026-70003", [g["doc_key"] for g in self.index.delta("12 CFR 9.9")["instruments"]])

    def test_ambiguous_double_instruction_keeps_both_texts(self):
        units = self.units_of(self.index.delta("12 CFR 3.40"), "fr:2026-70003")
        self.assertEqual(units[0]["pairing_status"], "multiple_instructions_same_target")
        self.assertIn("revising paragraph (a)", units[0]["instruction"])
        self.assertIn("adding paragraph (b)", units[0]["instruction"])

    def test_instruments_stay_separate_and_unreviewed(self):
        d = self.index.delta("12 CFR 3.1")
        self.assertEqual(sorted(g["doc_key"] for g in d["instruments"]), ["fr:2026-70001", "fr:2026-70002", "fr:2026-70003"])
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship WHERE rel_type IN ('amends','references','amends_candidate')")}
        self.assertNotIn("reviewed", status)
        self.assertEqual(d["applicability_status"], "not_assessed")

    # ---- citation retrieval honors scope and part filters before ranking and truncation
    def kinds_and_docs(self, ids):
        return [(self.index.evidence(pid, with_context=False)["kind"], self.index.evidence(pid, with_context=False)["doc_key"])
                for pid, _ in ids]

    def test_section_citation_scopes(self):
        cur = self.kinds_and_docs(self.index.exact("what does 12 CFR 3.1 require", 50, "current"))
        self.assertTrue(cur and all(d == "ecfr:12:3" and k != "amendment" for k, d in cur))
        fut = self.kinds_and_docs(self.index.exact("what does 12 CFR 3.1 require", 50, "future"))
        self.assertTrue(fut and all(k == "amendment" and d.startswith("fr:") for k, d in fut))
        both = self.kinds_and_docs(self.index.exact("what does 12 CFR 3.1 require", 50, "both"))
        self.assertTrue({d for _, d in both} >= {"ecfr:12:3", "fr:2026-70001"})

    def test_future_scope_citation_never_returns_current_baseline(self):
        for q in ("12 CFR 3.1", "§ 3.2", "12 CFR Part 3", "12 CFR 3.1(a)"):
            for mode in ("hybrid", "exact"):
                res = self.index.answer(q, mode=mode, scope="future", k=20)
                self.assertTrue(res["results"], (q, mode))
                self.assertFalse([r for r in res["results"] if r["status"]["state"].startswith("current")], (q, mode))
                self.assertTrue(all(r["kind"] in ("amendment", "fr_preamble", "fr_table") for r in res["results"]), (q, mode))

    def test_current_scope_citation_never_returns_future_records(self):
        for mode in ("hybrid", "exact"):
            res = self.index.answer("12 CFR 3.1", mode=mode, scope="current", k=20)
            self.assertTrue(res["results"])
            self.assertTrue(all(r["status"]["state"].startswith("current_baseline") for r in res["results"]))

    def test_truncation_after_filtering_keeps_both_scopes_visible(self):
        both = self.kinds_and_docs(self.index.exact("12 CFR 3.1", 2, "both"))
        self.assertEqual({k == "amendment" for k, _ in both}, {True, False})  # k=2 holds one of each

    def test_part_filter_applies_before_truncation_in_every_scope(self):
        for scope in ("current", "future", "both"):
            self.assertEqual(self.index.exact("12 CFR 3.1", 20, scope, ["12:217"]), [], scope)
            res = self.index.exact("12 CFR 6.2 and then 12 CFR 3.1", 1, scope, ["12:3"])
            self.assertEqual(len(res), 1, scope)  # a part-6 hit may not occupy the single slot and then be filtered away
            row = self.index.conn.execute("SELECT part_no FROM provision WHERE id=?", (res[0][0],)).fetchone()
            self.assertEqual(row["part_no"], "3")
        hybrid = self.index.hybrid("12 CFR 6.2 amendment to total assets", 20, "future", ["12:3"])
        parts = {self.index.conn.execute("SELECT part_no FROM provision WHERE id=?", (pid,)).fetchone()["part_no"] for pid, _ in hybrid}
        self.assertEqual(parts - {"3"}, set())

    def test_part_citation_future_scope_returns_explicit_part_and_section_amendments_only(self):
        ids = self.index.exact("12 CFR Part 3", 100, "future")
        docs = {self.index.evidence(pid, with_context=False)["doc_key"] for pid, _ in ids}
        self.assertTrue(docs >= {"fr:2026-70001", "fr:2026-70002", "fr:2026-70003"})
        for pid, _ in ids:
            self.assertEqual(self.index.evidence(pid, with_context=False)["kind"], "amendment")


if __name__ == "__main__":
    unittest.main()
