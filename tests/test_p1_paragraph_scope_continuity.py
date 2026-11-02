"""Tests for S7-P1-PARAGRAPH-SCOPE-CONTINUITY-43: paragraph scope survives (F1) coordinated child actions that share one governing "by" or "to" and
(F2) an explicit capitalized "In paragraph PATH," location clause. Relative paths are never composed. Expected values are literals."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import evaluate, fedreg
from test_p1_lowercase_paragraph_clause import fr_xml
from test_p1_paragraph_evidence_integrity import CITE, DOCS, SUPPORT, build_index

LITERAL = [
    # the three review literals
    ("In § 265.7, revise paragraph (d) by adding paragraph (7) and revising paragraph (8) to read as follows:", ["(d)"]),
    ("In § 265.7, revise paragraph (d) to add paragraph (7) and remove paragraph (8).", ["(d)"]),
    ("In § 265.7: a. In paragraph (d), revise paragraph (7) to read as follows:", ["(d)"]),
    # coordinated chains and comma / or variants
    ("In § 265.7, revise paragraph (d) by adding paragraph (7), revising paragraph (8), and removing paragraph (9).", ["(d)"]),
    ("In § 265.7, revise paragraph (d) to add paragraph (7), remove paragraph (8), and revise paragraph (9).", ["(d)"]),
    ("In § 265.7, revise paragraph (d) by adding paragraph (7) or removing paragraph (8).", ["(d)"]),
    ("In § 265.7, revise paragraph (d) by adding paragraph (7) and by revising paragraph (8).", ["(d)"]),
    ("In § 265.7, revise paragraph (d) to add paragraph (7) and to remove paragraph (8).", ["(d)"]),
    ("In § 265.7, revise paragraph (d)\n by adding paragraph ( 7 )\n and revising paragraph ( 8 ).", ["(d)"]),
    ("§ 265.7 is amended by revising paragraph (d) by adding paragraph (7) and revising paragraph (8).", ["(d)"]),
    ("In § 265.7, revise paragraph (d)(7) to add paragraph (ii) and remove paragraph (iii).", ["(d)(7)"]),
    # actions after a capitalized paragraph location clause
    ("In § 265.7: a. In paragraph (d), revise paragraph (7) and remove paragraph (8).", ["(d)"]),
    ("In § 265.7: a. In paragraph (d), revise paragraph (7), remove paragraph (8), and add paragraph (9).", ["(d)"]),
    ("In § 265.7: a. In paragraph (d)(7), revise paragraph (ii) and remove paragraph (iii).", ["(d)(7)"]),
    ("In § 265.7: a. In paragraph ( d ),\n revise paragraph ( 7 ).", ["(d)"]),
    ("In § 265.7: a. In paragraph (d), revise paragraph (7) for purposes of § 3.10(a)(5) and remove paragraph (8).", ["(d)"]),
    ("In § 265.7, in paragraph (d), revise paragraph (7).", ["(d)"]),
    # fully written children are paths
    ("In § 265.7: a. In paragraph (d), revise paragraph (d)(7).", ["(d)", "(d)(7)"]),
    ("In § 265.7, revise paragraph (d) by adding paragraph (d)(7) and revising paragraph (8).", ["(d)", "(d)(7)"]),
    # independent siblings
    ("In § 265.7, revise paragraph (d) and add paragraph (e).", ["(d)", "(e)"]),
    ("In § 265.7, revise paragraph (d), and add paragraph (e).", ["(d)", "(e)"]),
    ("In § 265.7, revise paragraph (d) by adding paragraph (7); add paragraph (e).", ["(d)", "(e)"]),
    # enumerator, sentence and section-location resets
    ("In § 265.7: a. In paragraph (d), revise paragraph (7); b. Revise paragraph (e).", ["(d)", "(e)"]),
    ("In § 265.7: a. In paragraph (d), revise paragraph (7); b. In paragraph (e), revise paragraph (8).", ["(d)", "(e)"]),
    ("In § 265.7: a. In paragraph (d), revise paragraph (7). b. Revise paragraph (e)(1).", ["(d)", "(e)(1)"]),
    ("In § 265.7, revise paragraph (d) by adding paragraph (7) and revising paragraph (8). In § 265.8, revise paragraph (a)(2).", ["(d)", "(a)(2)"]),
    ("In § 265.7: a. In paragraph (d), revise paragraph (7). In § 265.8, revise paragraph (a)(2).", ["(d)", "(a)(2)"]),
    ("In § 265.7, revise paragraph (d) by adding paragraph (7) and revising paragraph (8); Section 265.9 is amended by removing paragraph (b)(3).", ["(d)", "(b)(3)"]),
    # references and quoted text never count
    ("In § 265.7, revise paragraph (d) by adding paragraph (7) and revising paragraph (8) as described in § 3.22.", ["(d)"]),
    ("In § 265.7, revise paragraph (d) by revising the text to read “by adding paragraph (7) and revising paragraph (8)”.", ["(d)"]),
    ("In § 265.7: a. In paragraph (d), revise the text to read “revise paragraph (7)”.", ["(d)"]),
    # a paragraph-qualified section location still governs, and the single-child forms keep working
    ("In § 265.7(d): a. In paragraph (7), revise paragraph (8).", []),
    ("In § 265.7, revise paragraph (d) by adding paragraph (7).", ["(d)"]),
    ("In § 265.7, revise paragraph (d) to add paragraph (7).", ["(d)"]),
]
CONT_DOCS = {
    "fr:2026-98301": "1. In § 265.7, revise paragraph (d) by adding paragraph (7) and revising paragraph (8) to read as follows:",
    "fr:2026-98302": "1. In § 265.7, revise paragraph (d) to add paragraph (7) and remove paragraph (8).",
    "fr:2026-98303": "1. In § 265.7: a. In paragraph (d), revise paragraph (7) to read as follows:",
    "fr:2026-98304": "1. In § 265.7, revise paragraph (d) by adding paragraph (7), revising paragraph (8), and removing paragraph (9).",
}


class LiteralParserTests(unittest.TestCase):
    def test_literal_expected_paths(self):
        for text, expected in LITERAL:
            self.assertEqual(fedreg.named_paragraph_paths(text), expected, text)


class SyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name, {**DOCS, **{k: fr_xml(v) for k, v in CONT_DOCS.items()}})

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def prec(self, cite):
        return {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(cite)["instruments"]}

    def test_stored_attribution_is_the_named_parent_only(self):
        for key in CONT_DOCS:
            rows = self.ix.conn.execute("SELECT p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment'", (key,)).fetchall()
            self.assertTrue(rows, key)
            for r in rows:
                self.assertEqual(json.loads(r[0]), {"paths": ["(d)"], "status": "attributed"}, key)

    def test_unsupported_standalone_children_have_no_support_and_section_context_precision(self):
        for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(8)", "12 CFR 265.7(9)", "12 CFR 265.7(d)(7)", "12 CFR 265.7(d)(8)"):
            inst = self.prec(cite)
            for key in CONT_DOCS:
                self.assertEqual((inst[key]["resolved"], inst[key]["achieved"], inst[key]["reason"], inst[key]["supported_by"]),
                                 (False, "section_only", "paragraph_precision_unresolved", []), (cite, key))
        parent = self.prec("12 CFR 265.7(d)")
        for key in CONT_DOCS:
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


class EvaluatorTests(unittest.TestCase):
    def test_group_is_registered_and_fails_against_the_starting_behavior(self):
        self.assertIn("paragraph_scope_continuity", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        items = reg["paragraph_scope_continuity"]
        self.assertEqual([x["id"] for x in items], ["PS01", "PS02"])
        with tempfile.TemporaryDirectory() as tmp:
            ix = build_index(tmp, {**DOCS, **{k: fr_xml(v) for k, v in CONT_DOCS.items()}})
            for x in items:
                self.assertTrue(evaluate.check_paragraph_scope_continuity(ix, x)["passed"], x["id"])
            with mock.patch.object(fedreg, "_coordinated_with_child", lambda *a: False), \
                    mock.patch.object(fedreg, "_within_paragraph_location", lambda *a: False):  # the starting behavior
                self.assertFalse(evaluate.check_paragraph_scope_continuity(ix, items[0])["passed"])
            ix.conn.close()


if __name__ == "__main__":
    unittest.main()
