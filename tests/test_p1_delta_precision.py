"""Regression tests for S7-P1-DELTA-PRECISION-07 (R1, R2): delta() keeps the requested paragraph path, reports current
and future precision separately, never lets one instrument's support establish another's, and handles lettered
section inputs without crashing or asserting legal invalidity."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from test_p1 import asset

ECFR_3 = """<div class="part" id="part-3"><h1>PART 3—CAPITAL</h1>
<div class="section" id="3.4-3.9"><h4>§§ 3.4-3.9 [Reserved]</h4></div>
<div class="section" id="3.12"><h4>§ 3.12 Community bank leverage ratio framework.</h4>
<div id="p-3.12(c)"><p class="indent-1" data-title="3.12(c)">(c) Treatment when ceasing to qualify.</p>
<div id="p-3.12(c)(1)"><p class="indent-2" data-title="3.12(c)(1)">(1) A grace period of four reporting periods.</p></div></div></div>
<div class="section" id="3.22"><h4>§ 3.22 Adjustments and deductions.</h4>
<div id="p-3.22(a)"><p class="indent-1" data-title="3.22(a)">(a) General adjustments and deductions.</p></div></div></div>"""
ECFR_217 = """<div class="part" id="part-217"><h1>PART 217—CAPITAL</h1>
<div class="section" id="217.1"><h4>§ 217.1 Purpose.</h4><div id="p-217.1(a)"><p class="indent-1" data-title="217.1(a)">(a) Purpose.</p></div></div>
<div class="section" id="217.4-217.9"><h4>§§ 217.4-217.9 [Reserved]</h4></div></div>"""


def fr(root_tag, instruction, sections, title="12", part="3"):
    body = "".join(f"<SECTION><SECTNO>&#xA7; {n}</SECTNO><SUBJECT>S.</SUBJECT>{''.join(f'<P>{p}</P>' for p in ps)}</SECTION>"
                   for n, ps in sections)
    return (f'<{root_tag}><PREAMB><CFR>{title} CFR Part {part}</CFR></PREAMB><SUPLINF><REGTEXT TITLE="{title}" PART="{part}">'
            f"<AMDPAR>{instruction}</AMDPAR>{body}</REGTEXT></SUPLINF></{root_tag}>").encode()


# A names (c)(1) and (c)(7) for 3.12.
FR_A = fr("RULE", "1. Amend &#xA7; 3.12 by revising paragraph (c)(1) and adding paragraph (c)(7) to read as follows:",
          [("3.12", ["(c) * * *", "(1) A text for c1.", "(7) A text for c7."])])
# B names only (d), but its carried text opens with lead (c) and carries (1): lead matching would wrongly support (c)(1).
FR_B = fr("PRORULE", "1. Amend &#xA7; 3.12 by revising paragraph (d).",
          [("3.12", ["(c) * * *", "(1) B text for c1.", "(d) B text for d."])])
# C names (c)(1) but targets two sections: nothing can be attributed to either.
FR_C = fr("RULE", "1. Amend &#xA7;&#xA7; 3.12 and 3.22 by revising paragraph (c)(1).",
          [("3.12", ["(c) * * *", "(1) C text 12."]), ("3.22", ["(c) * * *", "(1) C text 22."])])
# D names only (i) for 3.22 and carries children (1) and (2).
FR_D = fr("RULE", "1. Amend &#xA7; 3.22 by adding paragraph (i) to read as follows:",
          [("3.22", ["(i) D new paragraph i.", "(1) D child one.", "(2) D child two."])])
# B2 names (d) and (c)(7)'s sibling only: used to show (d) support is B's alone.

KEYS = {"A": "fr:2026-70031", "B": "fr:2026-70032", "C": "fr:2026-70033", "D": "fr:2026-70034"}


class DeltaPrecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        final = {**meta, "document_type": "Rule", "effective_on": "2027-02-01", "action": "Final rule."}
        ecfr = {"source_class": "regulation", "publisher": "eCFR", "publisher_issue_date": "2026-10-23",
                "publisher_up_to_date_as_of": "2026-10-28"}
        docs = {
            "ecfr:12:3": {**ecfr, "citation": "12 CFR Part 3", "title": "Capital", "asset": asset(root, "e3.html", ECFR_3.encode())},
            "ecfr:12:217": {**ecfr, "citation": "12 CFR Part 217", "title": "Capital 217", "asset": asset(root, "e217.html", ECFR_217.encode())},
            KEYS["A"]: {**final, "document_number": "2026-70031", "title": "A", "asset": asset(root, "a.xml", FR_A)},
            KEYS["B"]: {**meta, "document_number": "2026-70032", "title": "B", "document_type": "Proposed Rule",
                        "comments_close_on": "2026-12-01", "asset": asset(root, "b.xml", FR_B)},
            KEYS["C"]: {**final, "document_number": "2026-70033", "title": "C", "asset": asset(root, "c.xml", FR_C)},
            KEYS["D"]: {**final, "document_number": "2026-70034", "title": "D", "asset": asset(root, "d.xml", FR_D)},
        }
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "d.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def inst(self, d):
        return {g["doc_key"]: g for g in d["instruments"]}

    def invariants(self, d):
        self.assertEqual(d["applicability_status"], "not_assessed")
        self.assertEqual(d["legal_validity"], "not_determined")
        keys = [g["doc_key"] for g in d["instruments"]]
        self.assertEqual(len(keys), len(set(keys)))  # instruments stay independent
        for g in d["instruments"]:
            self.assertTrue(g["status"]["caveat"] and g["source_sha256"])  # lifecycle caveat and hash preserved

    # ---- baseline existence and instrument support are separate facts
    def test_baseline_exists_and_only_the_instrument_naming_the_path_is_precise(self):
        d = self.index.delta("12 CFR 3.12(c)(1)")
        self.invariants(d)
        self.assertEqual(d["requested_precision"], "paragraph")
        self.assertEqual(d["requested_path"], "(c)(1)")
        self.assertTrue(d["current_precision"]["resolved"])
        self.assertEqual(d["baseline"]["precision"], "paragraph")
        self.assertIsNone(d["section_context"])
        i = self.inst(d)
        self.assertEqual(set(i), {KEYS["A"], KEYS["B"], KEYS["C"]})
        self.assertEqual(i[KEYS["A"]]["paragraph_precision"]["achieved"], "paragraph")
        self.assertEqual(i[KEYS["A"]]["role"], "paragraph_supported")
        # B opens with lead (c) and carries (1) but never names (c)(1); C names it only under a two-section instruction.
        for k in ("B", "C"):
            self.assertEqual(i[KEYS[k]]["paragraph_precision"]["achieved"], "section_only", k)
            self.assertFalse(i[KEYS[k]]["paragraph_precision"]["resolved"])
            self.assertEqual(i[KEYS[k]]["paragraph_precision"]["reason"], "paragraph_precision_unresolved")
            self.assertEqual(i[KEYS[k]]["role"], "section_context")
            self.assertTrue(all(not u["paragraph_supported"] for u in i[KEYS[k]]["units"]))
        self.assertEqual(d["future_precision"]["supported_instruments"], [KEYS["A"]])
        self.assertEqual(set(d["future_precision"]["section_context_instruments"]), {KEYS["B"], KEYS["C"]})

    def test_instrument_supports_a_path_the_baseline_lacks(self):
        d = self.index.delta("12 CFR 3.12(c)(7)")  # (c)(7) is not in the baseline; A adds it
        self.invariants(d)
        self.assertIsNone(d["baseline"])
        self.assertFalse(d["current_precision"]["resolved"])
        self.assertEqual(d["current_precision"]["reason"], "paragraph_not_found_in_acquired_section")
        self.assertEqual(d["section_context"]["role"], "section_context_not_the_requested_paragraph")
        self.assertTrue(d["future_precision"]["resolved"])
        self.assertEqual(d["future_precision"]["supported_instruments"], [KEYS["A"]])
        self.assertEqual(d["evidence_status"], "available")

    def test_support_belongs_to_the_instrument_that_names_the_path(self):
        d = self.index.delta("12 CFR 3.12(d)")
        i = self.inst(d)
        self.assertEqual(i[KEYS["B"]]["paragraph_precision"]["achieved"], "paragraph")
        self.assertEqual(i[KEYS["A"]]["paragraph_precision"]["achieved"], "section_only")
        self.assertEqual(i[KEYS["C"]]["paragraph_precision"]["achieved"], "section_only")
        self.assertEqual(d["future_precision"]["supported_instruments"], [KEYS["B"]])
        self.assertIsNone(d["baseline"])  # no (d) in the current baseline

    # ---- the unsupported deep paths stay section context
    def test_missing_deep_paths_never_resolve_through_the_parent_section(self):
        for cite in ("12 CFR 3.22(z)(999)", "12 CFR 3.12(c)(999)", "12 CFR 3.22(i)(999)"):
            d = self.index.delta(cite)
            self.invariants(d)
            self.assertIsNone(d["baseline"], cite)
            self.assertFalse(d["current_precision"]["resolved"], cite)
            self.assertEqual(d["current_precision"]["reason"], "paragraph_not_found_in_acquired_section", cite)
            self.assertIsNotNone(d["section_context"], cite)
            self.assertIn("not_the_requested_paragraph", d["section_context"]["role"])
            self.assertFalse(d["future_precision"]["resolved"], cite)
            self.assertEqual(d["future_precision"]["achieved"], "section_only", cite)
            self.assertEqual(d["future_precision"]["reason"], "paragraph_precision_unresolved", cite)
            self.assertEqual(d["evidence_status"], "section_context_only", cite)
            self.assertTrue(d["instruments"], cite)  # broader section evidence stays discoverable
            self.assertTrue(all(g["role"] == "section_context" and not g["paragraph_precision"]["resolved"]
                                for g in d["instruments"]), cite)
            self.assertTrue(all(not u["paragraph_supported"] for g in d["instruments"] for u in g["units"]), cite)

    def test_supported_control_3_22_i_is_precise_only_for_the_instrument_that_names_it(self):
        d = self.index.delta("12 CFR 3.22(i)")
        i = self.inst(d)
        self.assertEqual(i[KEYS["D"]]["paragraph_precision"]["achieved"], "paragraph")
        self.assertEqual(i[KEYS["C"]]["paragraph_precision"]["achieved"], "section_only")
        self.assertFalse(d["current_precision"]["resolved"])  # (i) is only being added
        self.assertEqual(d["future_precision"]["supported_instruments"], [KEYS["D"]])
        child = self.index.delta("12 CFR 3.22(i)(1)")  # D carries (1) but names only (i): hierarchy is not inferred
        self.assertFalse(child["future_precision"]["resolved"])
        self.assertEqual(child["future_precision"]["achieved"], "section_only")

    def test_delta_precision_agrees_with_resolve_citations_per_scope(self):
        for cite in ("12 CFR 3.12(c)(1)", "12 CFR 3.12(c)(7)", "12 CFR 3.12(d)", "12 CFR 3.12(c)(999)",
                     "12 CFR 3.22(i)", "12 CFR 3.22(i)(999)", "12 CFR 3.22(z)(999)"):
            d = self.index.delta(cite)
            cur = self.index.resolve_citations(cite, "current")[0]
            fut = self.index.resolve_citations(cite, "future")[0]
            self.assertEqual(d["current_precision"]["resolved"], cur["resolved"], cite)
            self.assertEqual(d["future_precision"]["resolved"], fut["resolved"], cite)
            self.assertEqual(d["future_precision"]["achieved"] == "section_only", fut["achieved"] == "section_only", cite)

    # ---- lettered sections (R2)
    def test_lettered_sections_return_structured_unavailable_evidence_without_crashing(self):
        for cite in ("12 CFR 3.4a", "12 CFR 217.4a"):
            d = self.index.delta(cite)
            self.invariants(d)
            self.assertEqual(d["evidence_status"], "unavailable", cite)
            self.assertIsNone(d["baseline"])
            self.assertEqual(d["instruments"], [])
            self.assertEqual(d["unavailable"]["reason"], "not_found_in_acquired_part", cite)
            self.assertEqual(d["unavailable"]["legal_validity"], "not_determined")
            self.assertIn("not a determination that the citation is invalid", d["unavailable"]["note"])
            self.assertFalse(d["current_precision"]["resolved"])
            self.assertFalse(d["future_precision"]["resolved"])

    def test_unacquired_part_and_numeric_reserved_range_lookup_still_work(self):
        d = self.index.delta("12 CFR 3.5")  # numeric minor inside the 3.4-3.9 reserved range still resolves
        self.assertEqual(d["baseline"]["doc_key"], "ecfr:12:3")
        self.assertEqual(self.index.delta("12 CFR 217.5")["baseline"]["doc_key"], "ecfr:12:217")
        gone = self.index.delta("12 CFR 999.1a")
        self.assertEqual(gone["evidence_status"], "unavailable")
        self.assertEqual(gone["unavailable"]["reason"], "part_not_acquired")

    # ---- section and part behaviour is preserved
    def test_section_and_part_deltas_are_unchanged_in_shape_and_content(self):
        d = self.index.delta("12 CFR 3.12")
        self.invariants(d)
        self.assertEqual(d["requested_precision"], "section")
        self.assertEqual(set(self.inst(d)), {KEYS["A"], KEYS["B"], KEYS["C"]})
        self.assertTrue(all("paragraph_precision" not in g for g in d["instruments"]))
        self.assertEqual(d["baseline"]["doc_key"], "ecfr:12:3")
        self.assertIsNone(d["section_context"])
        part = self.index.delta("12 CFR Part 3")
        self.assertEqual(part["requested_precision"], "part")
        self.assertEqual(part["baseline"]["doc_key"], "ecfr:12:3")
        self.assertEqual(set(self.inst(part)), set(KEYS.values()))


if __name__ == "__main__":
    unittest.main()
