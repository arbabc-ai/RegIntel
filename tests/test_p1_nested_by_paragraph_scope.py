"""Tests for S7-P1-NESTED-BY-PARAGRAPH-SCOPE-41: a child paragraph action introduced by "by" under an explicit parent paragraph
("In § 265.7, revise paragraph (d) by adding paragraph (7) to read as follows:") keeps the named parent (d), does not establish a top-level (7)
and does not infer (d)(7). Expected values are literals."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import evaluate, fedreg, search
from test_p1_lowercase_paragraph_clause import fr_xml
from test_p1_paragraph_evidence_integrity import CITE, DOCS, SUPPORT, build_index

LITERAL = [
    # the nested-by construction: the named parent stays, the single-designator child is not a path
    ("In § 265.7, revise paragraph (d) by adding paragraph (7) to read as follows:", ["(d)"]),
    ("In § 265.7, remove paragraph (d) by revising paragraph (7).", ["(d)"]),
    ("In § 265.7, revise paragraph (d) by removing paragraph (7).", ["(d)"]),
    ("§ 265.7 is amended by revising paragraph (d) by adding paragraph (7) to read as follows:", ["(d)"]),
    ("Section 265.7 is amended by revising paragraph (d)(7) by adding paragraph (ii).", ["(d)(7)"]),
    ("In § 265.7, revise paragraph (d)(7)(ii) by adding paragraph (B)(3).", ["(d)(7)(ii)"]),
    ("In § 265.7, revise paragraph (d) by adding paragraphs (7) and (8).", ["(d)"]),
    ("In § 265.7, revise paragraph (d) by adding paragraph (7) and by revising paragraph (8).", ["(d)"]),
    ("In § 265.7, revise paragraph ( d )\n by\n adding paragraph ( 7 ) to read as follows:", ["(d)"]),
    ("In § 265.7, revise paragraph (d)   by   adding paragraph (7).", ["(d)"]),
    ("In § 265.7, revise paragraph (d) as described in § 3.22 by adding paragraph (7).", ["(d)"]),
    # a child written out in full is a complete explicit path
    ("In § 265.7, revise paragraph (d) by adding paragraph (d)(7).", ["(d)", "(d)(7)"]),
    # independent sibling actions and enumerated clauses are not nested
    ("In § 265.7, revise paragraph (d) and add paragraph (e).", ["(d)", "(e)"]),
    ("In § 265.7, revise paragraph (d); add paragraph (e).", ["(d)", "(e)"]),
    ("In § 265.7: a. Revise paragraph (d) by adding paragraph (7); b. Revise paragraph (e).", ["(d)", "(e)"]),
    # new locations restart; fully explicit paths after bare locations are named
    ("In § 265.7, revise paragraph (d) by adding paragraph (7). In § 265.8, revise paragraph (a)(2).", ["(d)", "(a)(2)"]),
    ("In § 265.7, revise paragraph (d) by adding paragraph (7); Section 265.9 is amended by removing paragraph (b)(3).", ["(d)", "(b)(3)"]),
    ("In § 265.7, revise paragraph (d)(7).", ["(d)(7)"]),
    # quoted text never counts
    ("In § 265.7, revise paragraph (d) by revising the text to read “by adding paragraph (7)”.", ["(d)"]),
    # a paragraph-qualified location still governs its own relative actions
    ("In § 265.7(d), revise paragraph (e) by adding paragraph (7).", []),
]
NESTED_DOCS = {
    "fr:2026-98101": "1. In § 265.7, revise paragraph (d) by adding paragraph (7) to read as follows:",
    "fr:2026-98102": "1. § 265.7 is amended by revising paragraph (d) by adding paragraph (7) to read as follows:",
    "fr:2026-98103": "1. In § 265.7, revise paragraph ( d )\n by\n adding paragraph ( 7 ) to read as follows:",
    "fr:2026-98104": "1. In § 265.7, revise paragraph (d) by adding paragraphs (7) and (8).",
}


class LiteralParserTests(unittest.TestCase):
    def test_literal_expected_paths(self):
        for text, expected in LITERAL:
            self.assertEqual(fedreg.named_paragraph_paths(text), expected, text)


class SyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name, {**DOCS, **{k: fr_xml(v) for k, v in NESTED_DOCS.items()}})

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def prec(self, cite):
        return {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(cite)["instruments"]}

    def test_stored_attribution_is_the_named_parent_only(self):
        for key in NESTED_DOCS:
            rows = self.ix.conn.execute("SELECT p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment'", (key,)).fetchall()
            self.assertTrue(rows, key)
            for r in rows:
                self.assertEqual(json.loads(r[0]), {"paths": ["(d)"], "status": "attributed"}, key)

    def test_delta_precision_per_instrument(self):
        for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(8)", "12 CFR 265.7(d)(7)", "12 CFR 265.7(d)(8)"):
            inst = self.prec(cite)
            for key in NESTED_DOCS:
                self.assertEqual((inst[key]["resolved"], inst[key]["achieved"], inst[key]["reason"], inst[key]["supported_by"]),
                                 (False, "section_only", "paragraph_precision_unresolved", []), (cite, key))  # no top-level child, no inferred (d)(7)
        parent = self.prec("12 CFR 265.7(d)")
        for key in NESTED_DOCS:  # the explicitly named parent is supported
            self.assertEqual((parent[key]["resolved"], parent[key]["achieved"]), (True, "paragraph"), key)
            self.assertEqual(len(parent[key]["supported_by"]), 1, key)

    def test_answers_for_the_child_across_modes_scopes_and_budgets(self):
        for mode in ("exact", "hybrid"):
            for scope in ("future", "both"):
                for k in (1, 2, 5):
                    # top-level (7) does not exist in the baseline either: never evidence_found
                    self.assertNotEqual(self.ix.answer("12 CFR 265.7(7)", mode=mode, scope=scope, k=k)["status"], "evidence_found", (mode, scope, k))
                    fut = self.ix.answer("12 CFR 265.7(d)(7)", mode=mode, scope=scope, k=k)
                    if scope == "future":
                        self.assertNotEqual(fut["status"], "evidence_found", (mode, scope, k))
                    else:  # (d)(7) exists in the acquired baseline of this fixture: legitimate resolution, from the baseline, in both scope
                        self.assertEqual(fut["status"], "evidence_found", (mode, scope, k))
                        self.assertTrue(fut["results"][0]["doc_key"].startswith("ecfr:"), (mode, scope, k))
                    parent = self.ix.answer("12 CFR 265.7(d)", mode=mode, scope="future", k=k)
                    self.assertEqual(parent["status"], "evidence_found", (mode, k))
                    self.assertTrue(parent["results"][0]["doc_key"].startswith("fr:"), (mode, k))

    def test_part_filters_support_ranking_and_current_baseline_are_unchanged(self):
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


class EvaluatorTests(unittest.TestCase):
    def test_group_is_registered_and_fails_against_the_starting_behavior(self):
        self.assertIn("nested_by_paragraph_scope", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        items = reg["nested_by_paragraph_scope"]
        self.assertEqual([x["id"] for x in items], ["NB01", "NB02"])
        with tempfile.TemporaryDirectory() as tmp:
            ix = build_index(tmp, {**DOCS, **{k: fr_xml(v) for k, v in NESTED_DOCS.items()}})
            for x in items:
                self.assertTrue(evaluate.check_nested_by_paragraph_scope(ix, x)["passed"], x["id"])
            with mock.patch.object(fedreg, "_nested_by_child", lambda *a: False):  # the starting behavior
                self.assertFalse(evaluate.check_nested_by_paragraph_scope(ix, items[0])["passed"])
            ix.conn.close()


if __name__ == "__main__":
    unittest.main()
