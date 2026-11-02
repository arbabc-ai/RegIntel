"""Tests for S7-P1-ENUMERATED-PARAGRAPH-LOCATION-44: an explicit paragraph location clause keeps its scope over the subordinate enumerated list it
introduces with a colon or "amend as follows:" ("in paragraph (d), amend as follows: 1. Revise paragraph (7); 2. Remove paragraph (8)." and
"a. In paragraph (d): i. Revise paragraph (7); ii. Remove paragraph (8)."), so only (d) is named. Genuine enumerator, sentence and section-location
resets still apply. Expected values are literals; relative paths are never composed."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import evaluate, fedreg
from test_p1_lowercase_paragraph_clause import fr_xml
from test_p1_paragraph_evidence_integrity import CITE, DOCS, SUPPORT, build_index

LITERAL = [
    # the two review literals
    ("In § 265.7, in paragraph (d), amend as follows: 1. Revise paragraph (7); 2. Remove paragraph (8).", ["(d)"]),
    ("In § 265.7: a. In paragraph (d): i. Revise paragraph (7); ii. Remove paragraph (8).", ["(d)"]),
    # lowercase Roman enumerators, longer lists, digit and letter lists
    ("In § 265.7: a. In paragraph (d): i. Revise paragraph (7); ii. Remove paragraph (8); iii. Add paragraph (9); iv. Add paragraph (10).", ["(d)"]),
    ("In § 265.7: a. In paragraph (d), amend as follows: i. Revise paragraph (7); ii. Remove paragraph (8).", ["(d)"]),
    ("In § 265.7: a. In paragraph (d): 1. Revise paragraph (7); 2. Remove paragraph (8).", ["(d)"]),
    ("In § 265.7, in paragraph (d), amend as follows: a. Revise paragraph (7); b. Remove paragraph (8).", ["(d)"]),
    ("In § 265.7: a. In paragraph (d): i. Revise paragraph (7) and add paragraph (8); ii. Remove paragraph (9).", ["(d)"]),
    ("In § 265.7: a. In paragraph (d)(7): i. Revise paragraph (ii); ii. Remove paragraph (iii).", ["(d)(7)"]),
    # whitespace variants
    ("In § 265.7, in paragraph (d), amend as follows:\n 1. Revise paragraph ( 7 );\n 2. Remove paragraph ( 8 ).", ["(d)"]),
    ("In § 265.7: a. In paragraph (d) :  i.  Revise paragraph (7);  ii.  Remove paragraph (8).", ["(d)"]),
    ("In § 265.7: a. In paragraph ( d ):\n i. Revise paragraph (7);\n ii. Remove paragraph (8).", ["(d)"]),
    # genuine resets: an enumerator of another class, a sentence start, a new section location
    ("In § 265.7: a. In paragraph (d): i. Revise paragraph (7); ii. Remove paragraph (8); b. Revise paragraph (e).", ["(d)", "(e)"]),
    ("In § 265.7: a. In paragraph (d): i. Revise paragraph (7); ii. Remove paragraph (8). b. Revise paragraph (e)(1).", ["(d)", "(e)(1)"]),
    ("In § 265.7: a. In paragraph (d): i. Revise paragraph (7); b. In paragraph (e): i. Revise paragraph (8).", ["(d)", "(e)"]),
    ("In § 265.7, in paragraph (d), amend as follows: 1. Revise paragraph (7); 2. Remove paragraph (8). In § 265.8, revise paragraph (a)(2).", ["(d)", "(a)(2)"]),
    ("In § 265.7: a. In paragraph (d): i. Revise paragraph (7); ii. Remove paragraph (8); b. In § 265.8, revise paragraph (a)(2).", ["(d)", "(a)(2)"]),
    # complete-path operands, references and quoted text
    ("In § 265.7: a. In paragraph (d): i. Revise paragraph (d)(7); ii. Remove paragraph (8).", ["(d)", "(d)(7)"]),
    ("In § 265.7: a. In paragraph (d): i. Revise paragraph (7) for purposes of § 3.10(a)(5); ii. Remove paragraph (8).", ["(d)"]),
    ("In § 265.7: a. In paragraph (d): i. Revise the text to read “ii. Remove paragraph (8)”.", ["(d)"]),
    # a colon after an action does not open a subordinate list; unenumerated colon clauses end at the first boundary after the action
    ("In § 265.7: a. In paragraph (d), revise paragraph (7) to read as follows: b. Revise paragraph (e).", ["(d)", "(e)"]),
    ("In § 265.7: a. In paragraph (d): Revise paragraph (7).", ["(d)"]),
    ("In § 265.7: a. In paragraph (d): Revise paragraph (7); remove paragraph (e).", ["(d)", "(e)"]),
    # existing behavior is unchanged
    ("In § 265.7: a. In paragraph (d), revise paragraph (7); b. Revise paragraph (e).", ["(d)", "(e)"]),
    ("In § 265.7, in paragraph (d), revise the heading; revise paragraph (e).", ["(d)", "(e)"]),
    ("In § 265.7, revise paragraph (d) by adding paragraph (7) and revising paragraph (8).", ["(d)"]),
    ("In § 265.7(d), in paragraph (7), amend as follows: 1. Revise paragraph (8).", []),
]
ENUM_DOCS = {
    "fr:2026-98401": "1. In § 265.7, in paragraph (d), amend as follows: 1. Revise paragraph (7); 2. Remove paragraph (8).",
    "fr:2026-98402": "1. In § 265.7: a. In paragraph (d): i. Revise paragraph (7); ii. Remove paragraph (8).",
    "fr:2026-98403": "1. In § 265.7, in paragraph (d), amend as follows:\n 1. Revise paragraph ( 7 );\n 2. Remove paragraph ( 8 ).",
    "fr:2026-98404": "1. In § 265.7: a. In paragraph ( d ) :  i.  Revise paragraph (7);  ii.  Remove paragraph (8); iii. Add paragraph (9).",
}


class LiteralParserTests(unittest.TestCase):
    def test_literal_expected_paths(self):
        for text, expected in LITERAL:
            self.assertEqual(fedreg.named_paragraph_paths(text), expected, text)


class SyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name, {**DOCS, **{k: fr_xml(v) for k, v in ENUM_DOCS.items()}})

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def prec(self, cite):
        return {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(cite)["instruments"]}

    def test_stored_attribution_is_the_named_parent_only(self):
        for key in ENUM_DOCS:
            rows = self.ix.conn.execute("SELECT p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment'", (key,)).fetchall()
            self.assertTrue(rows, key)
            for r in rows:
                self.assertEqual(json.loads(r[0]), {"paths": ["(d)"], "status": "attributed"}, key)

    def test_unsupported_standalone_children_have_no_support_and_section_context_precision(self):
        for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(8)", "12 CFR 265.7(9)", "12 CFR 265.7(d)(7)", "12 CFR 265.7(d)(8)"):
            inst = self.prec(cite)
            for key in ENUM_DOCS:
                self.assertEqual((inst[key]["resolved"], inst[key]["achieved"], inst[key]["reason"], inst[key]["supported_by"]),
                                 (False, "section_only", "paragraph_precision_unresolved", []), (cite, key))
        parent = self.prec("12 CFR 265.7(d)")
        for key in ENUM_DOCS:
            self.assertEqual((parent[key]["resolved"], parent[key]["achieved"], len(parent[key]["supported_by"])), (True, "paragraph", 1), key)

    def test_answers_across_modes_scopes_and_budgets(self):
        for mode in ("exact", "hybrid"):
            for scope in ("future", "both"):
                for k in (1, 2, 5):
                    for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(8)", "12 CFR 265.7(9)"):
                        self.assertNotEqual(self.ix.answer(cite, mode=mode, scope=scope, k=k)["status"], "evidence_found", (cite, mode, scope, k))
                    child = self.ix.answer("12 CFR 265.7(d)(7)", mode=mode, scope=scope, k=k)
                    if scope == "future":
                        self.assertNotEqual(child["status"], "evidence_found", (mode, scope, k))
                    else:  # (d)(7) exists in the acquired baseline of this fixture: legitimate resolution, from the baseline
                        self.assertEqual(child["status"], "evidence_found", (mode, scope, k))
                        self.assertTrue(child["results"][0]["doc_key"].startswith("ecfr:"), (mode, scope, k))
                    self.assertEqual(self.ix.answer("12 CFR 265.7(d)", mode=mode, scope="future", k=k)["status"], "evidence_found", (mode, k))

    def test_part_filters_ranking_current_baseline_and_independent_instruments(self):
        for mode in ("exact", "hybrid"):
            for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(d)"):
                self.assertEqual(self.ix.answer(cite, mode=mode, scope="future", k=1, parts=["12:3"])["results"], [], (mode, cite))
                self.assertTrue(self.ix.answer(cite, mode=mode, scope="future", k=10, parts=["12:265"])["results"], (mode, cite))
            for k in (1, 2, 5):
                self.assertTrue(self.ix.answer(CITE, mode=mode, scope="future", k=k)["results"][0]["locator"].startswith("2026-96001:"), (mode, k))
        self.assertTrue(self.prec(CITE)[SUPPORT]["resolved"])
        cur = self.ix.answer("12 CFR 265.7(d)(7)", mode="exact", scope="current", k=1)
        self.assertTrue(cur["results"] and all(r["doc_key"].startswith("ecfr:") for r in cur["results"]))
        self.assertEqual((cur["applicability_status"], self.ix.delta(CITE)["legal_validity"]), ("not_assessed", "not_determined"))
        self.assertTrue({r[0] for r in self.ix.conn.execute("SELECT DISTINCT review_status FROM relationship")}
                        <= {"parsed_unreviewed", "regex_unreviewed", "unresolved_candidate"})


class ResetIndexTests(unittest.TestCase):
    def test_a_genuine_sibling_reset_names_the_next_top_level_paragraph_in_the_index(self):
        docs = {"fr:2026-98410": "1. In § 265.7: a. In paragraph (d): i. Revise paragraph (7); ii. Remove paragraph (8); b. Revise paragraph (e)(1)."}
        with tempfile.TemporaryDirectory() as tmp:
            ix = build_index(tmp, {k: fr_xml(v) for k, v in docs.items()})
            stored = [json.loads(r[0]) for r in ix.conn.execute("SELECT p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key='fr:2026-98410' AND p.kind='amendment'")]
            self.assertTrue(all(e["paths"] == ["(d)", "(e)(1)"] for e in stored), stored)
            ix.conn.close()


class EvaluatorTests(unittest.TestCase):
    def test_group_is_registered_and_fails_against_the_starting_behavior(self):
        self.assertIn("enumerated_paragraph_location", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        items = reg["enumerated_paragraph_location"]
        self.assertEqual([x["id"] for x in items], ["EP01", "EP02"])
        with tempfile.TemporaryDirectory() as tmp:
            ix = build_index(tmp, {**DOCS, **{k: fr_xml(v) for k, v in ENUM_DOCS.items()}})
            for x in items:
                self.assertTrue(evaluate.check_enumerated_paragraph_location(ix, x)["passed"], x["id"])
            with mock.patch.object(fedreg, "_scope_continues", lambda stripped, loc, match: not fedreg.ENUM_BOUNDARY.search(stripped, loc.end(), match.start())):
                self.assertFalse(evaluate.check_enumerated_paragraph_location(ix, items[0])["passed"])  # the starting behavior: any boundary ends the scope
            ix.conn.close()


if __name__ == "__main__":
    unittest.main()
