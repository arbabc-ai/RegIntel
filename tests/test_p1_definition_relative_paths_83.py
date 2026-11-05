"""Tests for scripts/p1_definition_relative_paths_83.py (S7-P1-83, C3 F-82-1): stored paragraph paths are classified by their first
designator, non-letter-led paths are listed with their composed citation observed through the existing resolver, answer and delta paths, and
the index is left byte-identical. Expected values are literals. Offline; task-77 synthetic fixture plus one in-test document. Governing
scope: founder record 2026-11-04_s7-regintel-p1-founder-decisions_accepted.md."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_p1_triple_letter_paragraph_list as tl
from scripts import p1_definition_relative_paths_83 as paths

DEF_KEY = "fr:2026-97101"
TWO_KEY = "fr:2026-97102"
TWO_XML = ("<RULE><PREAMB><CFR>12 CFR Part 9</CFR></PREAMB><SUPLINF><REGTEXT TITLE=\"12\" PART=\"9\"><AMDPAR>1. In § 9.3: a. Revise "
           "paragraph (2) of the definition of “Alpha”; b. Revise paragraph (2) of the definition of “Beta”.</AMDPAR></REGTEXT></SUPLINF></RULE>").encode()
DEF_XML = ("<RULE><PREAMB><CFR>12 CFR Part 9</CFR></PREAMB><SUPLINF><REGTEXT TITLE=\"12\" PART=\"9\"><AMDPAR>1. In § 9.2: a. Revise the "
           "definition of “Term”; b. Revise paragraph (3) of the definition of “Term”.</AMDPAR></REGTEXT></SUPLINF></RULE>").encode()


class FixtureOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = tl.build_index(cls.tmp.name)
        cls.root = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def test_letter_led_paths_classify_as_letter_and_nothing_is_listed(self):
        totals, listed, _ = paths.classify(paths.units(self.ix.conn))
        self.assertEqual(listed, [])
        self.assertEqual(totals["paths"], {"letter": 5, "digit": 0, "roman": 0, "other_or_ambiguous": 0})  # (e) (r) (nnn) (ooo) (xxx)
        self.assertEqual(paths.path_class("(nnn)"), ("letter", ["letter"]))
        self.assertEqual(paths.path_class("(3)"), ("digit", ["digit"]))
        self.assertEqual(paths.path_class("(ii)"), ("roman", ["roman"]))
        self.assertEqual(paths.path_class("(i)(2)"), ("other_or_ambiguous", ["letter", "roman"]))

    def test_the_cli_leaves_the_index_byte_identical_and_writes_schema_and_labels(self):
        db, out = self.root / "i.sqlite", self.root / "paths.json"
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        with mock.patch("builtins.print"):
            self.assertEqual(paths.main(["--index", str(db), "--out", str(out)]), 0)
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)
        report = json.loads(out.read_text())
        self.assertEqual(report["schema"], "p1-definition-relative-paths-0.2")  # S7-P1-84 schema bump
        self.assertEqual(report["decision_record"], "2026-11-04_s7-regintel-p1-founder-decisions_accepted.md")
        self.assertEqual((report["legal_validity"], report["applicability_status"]), ("not_determined", "not_assessed"))
        self.assertEqual(report["status"], "proposal; producer-reported; no gate approved; nothing authorised")
        self.assertEqual((report["index"]["sha256"], report["index"]["unchanged_after_run"]), (before, True))


class DefinitionRelativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with mock.patch.dict(tl.DOCS, {DEF_KEY: DEF_XML, TWO_KEY: TWO_XML}):
            cls.ix = tl.build_index(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def test_a_definition_relative_digit_path_is_listed_and_its_citation_observed(self):
        _, listed, _ = paths.classify(paths.units(self.ix.conn))
        listed = [u for u in listed if u["doc_key"] == DEF_KEY]
        self.assertEqual([(u["doc_key"], u["section_no"], u["paths"]) for u in listed], [(DEF_KEY, "9.2", ["(3)"])])
        self.assertEqual(listed[0]["path_classes"], [{"path": "(3)", "class": "digit", "first_designator_classes": ["digit"]}])
        [obs] = paths.observe(self.ix, listed)
        # S7-P1-84 (F-83-1): the term the instruction names for the path, and its term-anchored baseline locator
        self.assertEqual(obs["definition_anchor"]["terms"], ["Term"])
        self.assertIs(obs["definition_anchor"]["ambiguous"], False)
        self.assertEqual(obs["definition_anchor"]["anchors"],
                         [{"term": "Term", "term_anchored_locator": "9.2(Term)(3)", "term_anchored_citation": "12 CFR 9.2 \u201cTerm\u201d (3)",
                           "baseline_status": "baseline part not in index"}])
        self.assertEqual(obs["citation"], "12 CFR 9.2(3)")
        self.assertEqual(obs["baseline"], {"baseline_doc_key": "ecfr:12:9", "status": "baseline part not in index"})
        self.assertEqual((obs["resolve_future"]["resolved"], obs["resolve_future"]["achieved"]), (True, "paragraph"))
        self.assertEqual(obs["resolve_future"]["future_paragraph_support"], [DEF_KEY])
        self.assertEqual(obs["resolve_current"]["resolved"], False)
        self.assertEqual(obs["answer_exact_current"]["status"], "missing_evidence")
        self.assertEqual([(g["doc_key"], g["role"]) for g in obs["delta_instruments"]], [(DEF_KEY, "paragraph_supported")])


    def test_two_terms_for_one_path_are_both_recorded_and_ambiguous(self):
        _, listed, _ = paths.classify(paths.units(self.ix.conn))
        listed = [u for u in listed if u["doc_key"] == TWO_KEY]
        self.assertEqual([(u["section_no"], u["paths"]) for u in listed], [("9.3", ["(2)"])])
        [obs] = paths.observe(self.ix, listed)
        self.assertEqual(obs["citation"], "12 CFR 9.3(2)")
        self.assertEqual(obs["definition_anchor"]["terms"], ["Alpha", "Beta"])
        self.assertIs(obs["definition_anchor"]["ambiguous"], True)
        self.assertEqual([a["term_anchored_locator"] for a in obs["definition_anchor"]["anchors"]], ["9.3(Alpha)(2)", "9.3(Beta)(2)"])


if __name__ == "__main__":
    unittest.main()
