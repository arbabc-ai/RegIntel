"""Regression tests for S7-P1-INSTRUCTION-BOUNDARY-08 (R1): an amendatory instruction is kept apart from the loose
carried paragraphs that follow it; paragraph evidence comes only from instruction text."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.fedreg import is_continuation, named_paragraph_paths, parse_fr
from test_p1 import asset

AMD_E = "1. Amend § 3.8 by revising paragraphs (e), (f), and (g) to read as follows:"
# Loose carried paragraphs (P, FP, LI) follow the instruction with no SECTION wrapper, as in 2026-13192 instruction 3.
FR_LOOSE = ("""<RULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF>
<AMDPAR>1. Amend &#xA7; 3.8 by revising paragraphs (e), (f), and (g) to read as follows:</AMDPAR><STARS/>
<P>(e) Small institution.</P>
<P>(1) In general. Institutions under $30 billion, excluding assets described in &#xA7; 3.17(e), are small.</P>
<P>(2) Transition. Except as provided in paragraph (e)(3) of this section, an institution is reclassified under paragraph (e)(2) of this section.</P>
<P>(3) CBLR exception. An institution that elects 12 CFR 3.12(a)(3) shall be classified as small.</P>
<P>(f) Large institution. Text of f.</P>
<FP>(g) Highly complex institution. Text of g.</FP>
<LI>(h) A list item that is carried text and is never named.</LI>
<AMDPAR>2. Amend &#xA7; 3.9 by removing &#x201C;paragraph (e)(3)&#x201D; and by revising paragraph (a), conforming the reference as provided in paragraph (b) of this section.</AMDPAR>
</SUPLINF></RULE>""").encode()
# Legitimate continuations: a formulaic closer, and lettered sub-steps plus a closer before loose carried text.
FR_CONT = """<RULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF>
<AMDPAR>1. Amend &#xA7; 3.10 by revising paragraph (a).</AMDPAR><P>The revision reads as follows:</P>
<SECTION><SECTNO>&#xA7; 3.10</SECTNO><SUBJECT>Ten.</SUBJECT><P>(a) Ten text, see paragraph (z) of this section.</P></SECTION>
<AMDPAR>2. Amend &#xA7; 3.11 as follows:</AMDPAR><AMDPAR>a. Revise paragraph (b).</AMDPAR><AMDPAR>b. Add paragraph (c).</AMDPAR>
<P>The revision and addition read as follows:</P><P>(b) Eleven b text.</P><P>(c) Eleven c text.</P>
</SUPLINF></RULE>""".encode()
# Uncertain association: a two-section instruction, and an instruction with no explicit section target.
FR_UNCERTAIN = """<PRORULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF>
<AMDPAR>1. Amend &#xA7;&#xA7; 3.20 and 3.21 by revising paragraph (a).</AMDPAR><P>(a) Twenty text.</P>
<AMDPAR>2. Revise subpart B.</AMDPAR><P>&#xA7; 3.30 Thirty text.</P>
</SUPLINF></PRORULE>""".encode()
# Ordinary SECTION / REGTEXT handling, with an unquoted cross-reference inside the carried text.
FR_ORDINARY = """<RULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF>
<REGTEXT TITLE="12" PART="3"><AMDPAR>1. Amend &#xA7; 3.50 by revising paragraph (a) to read as follows:</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.50</SECTNO><SUBJECT>Fifty.</SUBJECT><P>(a) Fifty text. Revising paragraph (z) is described in paragraph (b) of this section.</P><P>(z) Z text.</P></SECTION></REGTEXT>
</SUPLINF></RULE>""".encode()

ECFR = """<div class="part" id="part-3"><h1>PART 3—CAPITAL</h1>
<div class="section" id="3.8"><h4>§ 3.8 Assessment classification.</h4>
<div id="p-3.8(a)"><p class="indent-1" data-title="3.8(a)">(a) General classification.</p></div></div></div>"""


class InstructionBoundaryParsingTests(unittest.TestCase):
    def units(self, xml, doc="2026-80000"):
        return parse_fr(xml, doc)[1]

    def test_loose_pfpli_carried_text_is_separate_from_the_instruction(self):
        u = [x for x in self.units(FR_LOOSE) if x["section_no"] == "3.8"]
        self.assertTrue(u)
        for unit in u:
            self.assertEqual(unit["instruction"], AMD_E)  # exactly the AMDPAR text, nothing swallowed
            self.assertEqual(unit["pairing_status"], "explicit_loose_text")
            self.assertEqual(unit["carried_text_origin"], "loose_paragraphs")
            self.assertEqual(unit["explicit_targets"], ["3.8"])
            self.assertEqual(unit["explicit_paragraphs"], {"paths": ["(e)", "(f)", "(g)"], "status": "attributed"})
        carried = "\n".join(x["text"] for x in u)
        for needle in ("(e) Small institution.", "(1) In general.", "paragraph (e)(3) of this section", "(f) Large institution",
                       "(g) Highly complex", "(h) A list item"):
            self.assertIn(needle, carried)  # P, FP and LI paragraphs are all preserved as carried text
        self.assertNotIn("Small institution", u[0]["instruction"])

    def test_carried_text_cross_references_never_establish_paragraph_evidence(self):
        paths = [p for x in self.units(FR_LOOSE) if x["section_no"] == "3.8" for p in x["explicit_paragraphs"]["paths"]]
        self.assertNotIn("(e)(3)", paths)
        self.assertNotIn("(e)(2)", paths)
        self.assertEqual(sorted(set(paths)), ["(e)", "(f)", "(g)"])  # ancestors named; descendants not established

    def test_quoted_and_unquoted_cross_references_in_the_instruction_are_not_paths(self):
        row = next(x for x in self.units(FR_LOOSE) if x["instruction"].startswith("2. Amend § 3.9"))
        self.assertEqual(row["pairing_status"], "instruction_only")
        self.assertEqual(row["explicit_paragraphs"], {"paths": ["(a)"], "status": "attributed"})
        self.assertEqual(named_paragraph_paths(row["instruction"]), ["(a)"])  # not (e)(3) (quoted) and not (b) ("as provided in")
        self.assertEqual(named_paragraph_paths("Revise § 3.1. Except as provided in paragraph (e)(3), keep (a)."), [])
        self.assertEqual(named_paragraph_paths("2. Amend § 3.1: d. In paragraph (c)(2), removing the word."), ["(c)(2)"])
        self.assertEqual(named_paragraph_paths("Remove the text as provided in paragraph (e)(3) of this section."), [])

    def test_legitimate_instruction_continuations_stay_with_the_instruction(self):
        u = {x["section_no"]: x for x in self.units(FR_CONT) if x["text"]}
        self.assertTrue(u["3.10"]["instruction"].endswith("The revision reads as follows:"))
        self.assertEqual(u["3.10"]["pairing_status"], "explicit")
        self.assertEqual(u["3.10"]["carried_text_origin"], "section_element")
        i11 = u["3.11"]["instruction"]
        for needle in ("2. Amend § 3.11 as follows:", "a. Revise paragraph (b).", "b. Add paragraph (c).",
                       "The revision and addition read as follows:"):
            self.assertIn(needle, i11)
        self.assertNotIn("Eleven b text", i11)
        self.assertIn("(b) Eleven b text.", u["3.11"]["text"])
        self.assertEqual(u["3.11"]["explicit_paragraphs"], {"paths": ["(b)", "(c)"], "status": "attributed"})
        self.assertEqual(u["3.11"]["carried_text_origin"], "loose_paragraphs")

    def test_continuation_test_is_narrow(self):
        for text in ("The revision reads as follows:", "The revisions and additions read as follows:", "To read as follows:"):
            self.assertTrue(is_continuation(text), text)
        for text in ("(e) Small institution.", "(a) The revision reads as follows:", "Small institution means an institution.",
                     "The agency adopts the following definitions " + "x" * 400 + " read as follows:"):
            self.assertFalse(is_continuation(text), text[:40])

    def test_uncertain_association_keeps_the_text_and_the_candidate_without_a_target(self):
        loose = [x for x in self.units(FR_UNCERTAIN) if x["carried_text_origin"] == "loose_paragraphs"]
        self.assertEqual(len(loose), 2)
        for x in loose:
            self.assertEqual(x["pairing_status"], "carried_text_unpaired")
            self.assertEqual(x["explicit_targets"], [])
            self.assertEqual(x["instruction"], "")
            self.assertEqual(x["explicit_paragraphs"]["paths"], [])
            self.assertEqual(len(x["unresolved_candidates"]), 1)
        self.assertIn("Twenty text", loose[0]["text"])
        self.assertTrue(loose[0]["unresolved_candidates"][0]["instruction"].startswith("1. Amend §§ 3.20 and 3.21"))
        self.assertIn("Thirty text", loose[1]["text"])
        self.assertTrue(loose[1]["unresolved_candidates"][0]["instruction"].startswith("2. Revise subpart B"))

    def test_ordinary_section_and_regtext_handling_is_unchanged(self):
        u = [x for x in self.units(FR_ORDINARY) if x["section_no"] == "3.50"]
        self.assertEqual(len(u), 1)
        self.assertEqual(u[0]["pairing_status"], "explicit")
        self.assertEqual(u[0]["carried_text_origin"], "section_element")
        self.assertEqual(u[0]["explicit_paragraphs"], {"paths": ["(a)"], "status": "attributed"})  # (z) and (b) are carried-text mentions
        self.assertIn("(z) Z text.", u[0]["text"])


class InstructionBoundaryIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        docs = {
            "ecfr:12:3": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 3", "title": "Capital",
                          "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                          "asset": asset(root, "e.html", ECFR.encode())},
            "fr:2026-80041": {**meta, "document_number": "2026-80041", "title": "Loose", "document_type": "Rule",
                              "effective_on": "2027-02-01", "action": "Final rule.", "asset": asset(root, "l.xml", FR_LOOSE)},
        }
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "b.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cit(self, q, scope="future", parts=None):
        return self.index.resolve_citations(q, scope, parts)[0]

    def test_supported_controls_resolve_and_the_cross_referenced_descendant_does_not(self):
        for q in ("12 CFR 3.8(e)", "12 CFR 3.8(f)", "12 CFR 3.8(g)"):
            c = self.cit(q)
            self.assertTrue(c["resolved"], q)
            self.assertEqual(c["achieved"], "paragraph")
            self.assertEqual(len(c["future_paragraph_support"]), len(c["future_paragraph_support"]))
        for q in ("12 CFR 3.8(e)(3)", "12 CFR 3.8(e)(2)", "12 CFR 3.8(e)(1)", "12 CFR 3.8(h)"):
            c = self.cit(q)
            self.assertFalse(c["resolved"], q)  # an instruction naming (e) never establishes (e)(3)
            self.assertEqual((c["achieved"], c["reason"]), ("section_only", "paragraph_precision_unresolved"), q)
            self.assertEqual(c["future_paragraph_support"], [])
            self.assertEqual(c["legal_validity"], "not_determined")

    def test_answer_across_modes_scopes_and_part_filters(self):
        for mode in ("exact", "hybrid"):
            for scope in ("current", "future", "both"):
                for parts in (None, ["12:3"], ["12:217"]):
                    bad = self.index.answer("12 CFR 3.8(e)(3)", mode=mode, scope=scope, parts=parts, k=10)
                    tag = (mode, scope, parts)
                    self.assertNotEqual(bad["status"], "evidence_found", tag)
                    self.assertIn("citation:12 CFR 3.8(e)(3)", {g["topic"] for g in bad["coverage_gaps"]}, tag)
                    self.assertEqual(bad["applicability_status"], "not_assessed")
                    for r in bad["results"]:
                        if scope == "future":
                            self.assertTrue(r["kind"] in ("amendment", "fr_preamble", "fr_table"), tag)
                    if parts == ["12:217"]:
                        self.assertEqual(bad["results"], [], tag)
                        self.assertEqual(bad["status"], "missing_evidence", tag)
                    good = self.index.answer("12 CFR 3.8(e)", mode=mode, scope=scope, parts=parts, k=10)
                    if scope == "future" and parts != ["12:217"]:
                        self.assertEqual(good["status"], "evidence_found", tag)
                        self.assertTrue(good["results"])
                    if parts == ["12:217"]:
                        self.assertEqual(good["results"], [], tag)
        out = self.index.answer("12 CFR 3.8(e)(3)", mode="exact", scope="future", k=1)  # small k keeps section context, flagged
        self.assertEqual(out["status"], "partial_evidence")
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(out["citations"][0]["achieved"], "section_only")

    def test_delta_separates_precision_and_keeps_section_context(self):
        d = self.index.delta("12 CFR 3.8(e)(3)")
        self.assertEqual(d["legal_validity"], "not_determined")
        self.assertEqual(d["applicability_status"], "not_assessed")
        self.assertIsNone(d["baseline"])
        self.assertIsNotNone(d["section_context"])
        self.assertEqual(d["future_precision"]["achieved"], "section_only")
        self.assertFalse(d["future_precision"]["resolved"])
        self.assertEqual([g["role"] for g in d["instruments"]], ["section_context"])
        self.assertTrue(d["instruments"][0]["status"]["caveat"] and d["instruments"][0]["source_sha256"])
        ok = self.index.delta("12 CFR 3.8(g)")
        self.assertTrue(ok["future_precision"]["resolved"])
        self.assertEqual(ok["future_precision"]["supported_instruments"], ["fr:2026-80041"])

    def test_indexed_units_keep_the_instruction_separate_and_record_provenance(self):
        rows = self.index.conn.execute(
            "SELECT instruction, text, pairing_status, carried_text_origin, explicit_targets FROM provision "
            "WHERE kind='amendment' AND section_no='3.8'").fetchall()
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["instruction"], AMD_E)
            self.assertEqual((r["pairing_status"], r["carried_text_origin"]), ("explicit_loose_text", "loose_paragraphs"))
            self.assertEqual(json.loads(r["explicit_targets"]), ["3.8"])
        self.assertIn("(e) Small institution.", "\n".join(r["text"] for r in rows))


if __name__ == "__main__":
    unittest.main()
