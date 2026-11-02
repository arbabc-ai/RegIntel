"""Tests for S7-P1-NESTED-TO-PARAGRAPH-SCOPE-42: an immediate infinitive child action ("In § 265.7, revise paragraph (d) to add paragraph (7) to read as
follows:") is nested under the preceding named paragraph: (d) is kept, a standalone (7) is not a path and (d)(7) is not inferred. Expected values
are literals; the rule is not paragraph-path composition."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import evaluate, fedreg
from test_p1_lowercase_paragraph_clause import fr_xml
from test_p1_paragraph_evidence_integrity import CITE, DOCS, SUPPORT, build_index

LITERAL = [
    # the infinitive construction
    ("In § 265.7, revise paragraph (d) to add paragraph (7) to read as follows:", ["(d)"]),
    ("In § 265.7, revise paragraph (d) to remove paragraph (7).", ["(d)"]),
    ("In § 265.7, revise paragraph (d) to revise paragraph (7).", ["(d)"]),
    ("In § 265.7, remove paragraph (d) to add paragraph (7).", ["(d)"]),
    ("§ 265.7 is amended by revising paragraph (d) to add paragraph (7).", ["(d)"]),
    ("Section 265.7 is amended by revising paragraph (d)(7) to add paragraph (ii).", ["(d)(7)"]),
    ("In § 265.7, revise paragraph (d)(7)(ii) to add paragraph (B)(3).", ["(d)(7)(ii)"]),
    ("In § 265.7, revise paragraph (d) to add paragraphs (7) and (8).", ["(d)"]),
    ("In § 265.7, revise paragraph (d) to add paragraph (7) and to remove paragraph (8).", ["(d)"]),
    ("In § 265.7, revise paragraph ( d )\n to\n add paragraph ( 7 ) to read as follows:", ["(d)"]),
    ("In § 265.7, revise paragraph (d)   to   add paragraph (7).", ["(d)"]),
    ("In § 265.7, revise paragraph (d) as described in § 3.22 to add paragraph (7).", ["(d)"]),
    # the existing "by" construction is unchanged
    ("In § 265.7, revise paragraph (d) by adding paragraph (7) to read as follows:", ["(d)"]),
    ("In § 265.7, revise paragraph (d) by adding paragraph (7) and to remove paragraph (8).", ["(d)"]),
    # a child written out as a complete path is a path
    ("In § 265.7, revise paragraph (d) to add paragraph (d)(7).", ["(d)", "(d)(7)"]),
    # independent siblings, enumerators and resets
    ("In § 265.7, revise paragraph (d) and add paragraph (e).", ["(d)", "(e)"]),
    ("In § 265.7, revise paragraph (d); to add paragraph (7).", ["(d)", "(7)"]),
    ("In § 265.7: a. Revise paragraph (d) to add paragraph (7); b. Revise paragraph (e).", ["(d)", "(e)"]),
    ("In § 265.7, revise paragraph (d) to add paragraph (7). In § 265.8, revise paragraph (a)(2).", ["(d)", "(a)(2)"]),
    ("In § 265.7, revise paragraph (d) to add paragraph (7); Section 265.9 is amended by removing paragraph (b)(3).", ["(d)", "(b)(3)"]),
    # range "to" is not an infinitive, quoted text never counts, a qualified location still governs
    ("In § 265.7, revise paragraphs (a) to (c).", ["(a)", "(c)"]),
    ("In § 265.7, revise paragraph (d) by revising the text to read “to add paragraph (7)”.", ["(d)"]),
    ("In § 265.7(d), revise paragraph (e) to add paragraph (7).", []),
]
TO_DOCS = {
    "fr:2026-98201": "1. In § 265.7, revise paragraph (d) to add paragraph (7) to read as follows:",
    "fr:2026-98202": "1. § 265.7 is amended by revising paragraph (d) to add paragraph (7) to read as follows:",
    "fr:2026-98203": "1. In § 265.7, revise paragraph ( d )\n to\n add paragraph ( 7 ) to read as follows:",
    "fr:2026-98204": "1. In § 265.7, revise paragraph (d) to add paragraphs (7) and (8).",
}


class LiteralParserTests(unittest.TestCase):
    def test_literal_expected_paths(self):
        for text, expected in LITERAL:
            self.assertEqual(fedreg.named_paragraph_paths(text), expected, text)


class SyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name, {**DOCS, **{k: fr_xml(v) for k, v in TO_DOCS.items()}})

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def prec(self, cite):
        return {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(cite)["instruments"]}

    def test_stored_attribution_is_the_named_parent_only(self):
        for key in TO_DOCS:
            rows = self.ix.conn.execute("SELECT p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment'", (key,)).fetchall()
            self.assertTrue(rows, key)
            for r in rows:
                self.assertEqual(json.loads(r[0]), {"paths": ["(d)"], "status": "attributed"}, key)

    def test_delta_precision_per_instrument(self):
        for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(8)", "12 CFR 265.7(d)(7)", "12 CFR 265.7(d)(8)"):
            inst = self.prec(cite)
            for key in TO_DOCS:
                self.assertEqual((inst[key]["resolved"], inst[key]["achieved"], inst[key]["reason"], inst[key]["supported_by"]),
                                 (False, "section_only", "paragraph_precision_unresolved", []), (cite, key))
        parent = self.prec("12 CFR 265.7(d)")
        for key in TO_DOCS:
            self.assertEqual((parent[key]["resolved"], parent[key]["achieved"], len(parent[key]["supported_by"])), (True, "paragraph", 1), key)

    def test_answers_across_modes_scopes_and_budgets(self):
        for mode in ("exact", "hybrid"):
            for scope in ("future", "both"):
                for k in (1, 2, 5):
                    self.assertNotEqual(self.ix.answer("12 CFR 265.7(7)", mode=mode, scope=scope, k=k)["status"], "evidence_found", (mode, scope, k))
                    child = self.ix.answer("12 CFR 265.7(d)(7)", mode=mode, scope=scope, k=k)
                    if scope == "future":
                        self.assertNotEqual(child["status"], "evidence_found", (mode, scope, k))
                    else:  # (d)(7) exists in the acquired baseline of this fixture: legitimate resolution, from the baseline
                        self.assertEqual(child["status"], "evidence_found", (mode, scope, k))
                        self.assertTrue(child["results"][0]["doc_key"].startswith("ecfr:"), (mode, scope, k))
                    parent = self.ix.answer("12 CFR 265.7(d)", mode=mode, scope="future", k=k)
                    self.assertEqual(parent["status"], "evidence_found", (mode, k))

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
        import re
        self.assertIn("nested_to_paragraph_scope", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        items = reg["nested_to_paragraph_scope"]
        self.assertEqual([x["id"] for x in items], ["NT01", "NT02"])
        with tempfile.TemporaryDirectory() as tmp:
            ix = build_index(tmp, {**DOCS, **{k: fr_xml(v) for k, v in TO_DOCS.items()}})
            for x in items:
                self.assertTrue(evaluate.check_nested_to_paragraph_scope(ix, x)["passed"], x["id"])
            by_only = re.compile(r"(?:[^.;:]|(?<=\d)\.(?=\d))*\bby\s*", re.I)  # the starting behavior: only "by" connects a nested child
            with mock.patch.object(fedreg, "NESTED_BY_GAP", by_only):
                self.assertFalse(evaluate.check_nested_to_paragraph_scope(ix, items[0])["passed"])
            ix.conn.close()


if __name__ == "__main__":
    unittest.main()
