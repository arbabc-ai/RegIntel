"""Tests for S7-P1-PASSIVE-LOCATION-PARAGRAPHS-40: a paragraph-qualified passive section location ("§ 265.7(d) is amended by revising paragraph (7)",
"Section 265.7(d)(7) is amended by removing paragraph (ii)") governs the relative actions that follow it, across colons, semicolons and
enumerators. Those actions establish neither top-level operands nor inferred joined paths. References and quoted text do not change the
context; a new bare passive location restarts it and keeps fully explicit paths. Expected values are literals."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import evaluate, fedreg, search
from test_p1_lowercase_paragraph_clause import fr_xml
from test_p1_paragraph_evidence_integrity import CITE, DOCS, SUPPORT, build_index

LITERAL = [
    # a paragraph-qualified passive location: relative actions name nothing
    ("§ 265.7(d) is amended by revising paragraph (7) to read as follows:", []),
    ("Section 265.7(d)(7) is amended by removing paragraph (ii).", []),
    ("§ 265.7(d) is amended by adding paragraph (7) to read as follows:", []),
    ("§ 265.7(d) is amended by removing and reserving paragraph (7).", []),
    ("§ 265.7(d)(7) is amended by revising paragraph (ii)(B).", []),
    ("§ 265.7(d) is amended by: a. Revising paragraph (7); b. Removing paragraph (8).", []),
    ("Section 265.7(d) is amended by: 1. In paragraph (7), removing “x”. 2. In paragraph (8), removing “y”.", []),
    ("§ 265.7(d) is amended by: i. Revising paragraph (7); ii. Revising paragraph (8).", []),
    ("§ 265.7 ( d ) is amended by revising paragraph ( 7 ).", []),
    ("§ 265.7(d)\n is amended by:\n a. Revising paragraph (7);\n b. Removing paragraph (8).", []),
    ("§§ 265.7(d) and 265.8 are amended by revising paragraph (7).", []),
    # references and quoted text do not change the context
    ("§ 265.7(d) is amended by revising paragraph (7) as described in § 3.22; revising paragraph (8).", []),
    ("§ 265.7(d) is amended by revising paragraph (7) for purposes of § 3.10(a)(5); revising paragraph (8).", []),
    ("§ 265.7(d) is amended by revising the text to read “§ 265.8 is amended by revising paragraph (a).”", []),
    ("§ 265.7 is amended by revising the text to read “§ 265.7(d) is amended by revising paragraph (7)” and revising paragraph (e)(1).", ["(e)(1)"]),
    # a bare passive location: fully explicit paths are named, with or without enumerators
    ("§ 265.7 is amended by revising paragraph (d)(7).", ["(d)(7)"]),
    ("Section 265.7 is amended by: a. Revising paragraph (a)(1); b. Removing paragraph (e)(2).", ["(a)(1)", "(e)(2)"]),
    ("§§ 265.7 and 265.8 are amended by revising paragraph (c)(2).", ["(c)(2)"]),
    # a genuinely new bare passive location restarts the context
    ("§ 265.7(d) is amended by revising paragraph (7). § 265.8 is amended by revising paragraph (a)(2).", ["(a)(2)"]),
    ("§ 265.7(d) is amended by revising paragraph (7); Section 265.9 is amended by removing paragraph (b)(3).", ["(b)(3)"]),
    ("§ 265.7(d) is amended by revising the heading. In § 265.8, revise paragraph (a).", ["(a)"]),
    # the active forms keep working
    ("In § 265.7(d), revise paragraph (7).", []),
    ("In § 265.7, revise paragraph (d)(7).", ["(d)(7)"]),
]
PASSIVE_DOCS = {
    "fr:2026-98001": "1. § 265.7(d) is amended by revising paragraph (7) to read as follows:",
    "fr:2026-98002": "1. Section 265.7(d)(7) is amended by removing paragraph (ii).",
    "fr:2026-98003": "1. § 265.7(d) is amended by: a. Revising paragraph (7); b. Removing paragraph (8).",
    "fr:2026-98004": "1. Section 265.7(d) is amended by: 1. In paragraph (7), removing “x”. 2. In paragraph (8), removing “y”.",
}


class LiteralParserTests(unittest.TestCase):
    def test_literal_expected_paths(self):
        for text, expected in LITERAL:
            self.assertEqual(fedreg.named_paragraph_paths(text), expected, text)

    def test_parse_instruction_still_accepts_the_passive_targets(self):
        self.assertEqual(fedreg.parse_instruction("§ 265.7(d) is amended by revising paragraph (7).")["targets"], ["265.7"])
        self.assertEqual(fedreg.parse_instruction("Section 265.7(d)(7) is amended by removing paragraph (ii).")["targets"], ["265.7"])


class SyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name, {**DOCS, **{k: fr_xml(v) for k, v in PASSIVE_DOCS.items()}})

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def test_stored_attribution_is_none_named(self):
        for key in PASSIVE_DOCS:
            rows = self.ix.conn.execute("SELECT p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment'", (key,)).fetchall()
            self.assertTrue(rows, key)
            for r in rows:
                self.assertEqual(json.loads(r[0]), {"paths": [], "status": "none_named"}, key)

    def test_delta_keeps_every_instrument_as_section_context_with_no_support(self):
        for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(8)", "12 CFR 265.7(ii)", "12 CFR 265.7(d)(7)", "12 CFR 265.7(d)(8)"):
            inst = {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(cite)["instruments"]}
            for key in PASSIVE_DOCS:
                self.assertIn(key, inst, (cite, key))
                self.assertEqual((inst[key]["resolved"], inst[key]["achieved"], inst[key]["reason"], inst[key]["supported_by"]),
                                 (False, "section_only", "paragraph_precision_unresolved", []), (cite, key))

    def test_answers_across_modes_scopes_budgets_and_filters(self):
        for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(8)", "12 CFR 265.7(ii)"):
            for mode in ("exact", "hybrid"):
                for scope in ("future", "both"):
                    for k in (1, 2, 5):
                        out = self.ix.answer(cite, mode=mode, scope=scope, k=k)
                        self.assertNotEqual(out["status"], "evidence_found", (cite, mode, scope, k))
                    self.assertEqual(self.ix.resolve_citations(cite, scope)[0]["future_paragraph_support"], [], (cite, scope))
                    self.assertEqual(self.ix.answer(cite, mode=mode, scope=scope, k=1, parts=["12:3"])["results"], [], (cite, mode, scope))
                    self.assertTrue(self.ix.answer(cite, mode=mode, scope=scope, k=10, parts=["12:265"])["results"], (cite, mode, scope))

    def test_independent_support_and_a_legitimate_baseline_evidence_are_not_suppressed(self):
        inst = {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(CITE)["instruments"]}
        self.assertTrue(inst[SUPPORT]["resolved"])  # the fully explicit instrument keeps its support
        for mode in ("exact", "hybrid"):
            for k in (1, 2, 5):
                self.assertTrue(self.ix.answer(CITE, mode=mode, scope="future", k=k)["results"][0]["locator"].startswith("2026-96001:"), (mode, k))
            # (d)(7) exists in the acquired baseline of this fixture: both scope is legitimately evidence_found from it
            out = self.ix.answer("12 CFR 265.7(d)(7)", mode=mode, scope="both", k=1)
            self.assertEqual(out["status"], "evidence_found", mode)
            self.assertTrue(out["results"][0]["doc_key"].startswith("ecfr:"), mode)
        cur = self.ix.answer("12 CFR 265.7(d)(7)", mode="exact", scope="current", k=1)
        self.assertTrue(cur["results"] and all(r["doc_key"].startswith("ecfr:") for r in cur["results"]))
        self.assertEqual((cur["applicability_status"], self.ix.delta(CITE)["legal_validity"]), ("not_assessed", "not_determined"))
        self.assertTrue({r[0] for r in self.ix.conn.execute("SELECT DISTINCT review_status FROM relationship")}
                        <= {"parsed_unreviewed", "regex_unreviewed", "unresolved_candidate"})


class ContextResetIndexTests(unittest.TestCase):
    def test_a_bare_passive_location_keeps_explicit_paths_while_a_qualified_one_names_nothing(self):
        # One instruction per section: an instruction that names two sections is deliberately unattributed (unattributed_multiple_targets).
        docs = {"fr:2026-98010": ("1. § 265.7(d) is amended by revising paragraph (7).", "2. § 265.5 is amended by revising paragraph (a)(2)(i).")}
        with tempfile.TemporaryDirectory() as tmp:
            ix = build_index(tmp, {k: fr_xml(*v) for k, v in docs.items()})
            stored = {r[0]: json.loads(r[1]) for r in ix.conn.execute(
                "SELECT p.instruction, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key='fr:2026-98010' AND p.kind='amendment'")}
            by_section = {("265.7" if "265.7" in k else "265.5"): v for k, v in stored.items()}
            self.assertEqual(by_section["265.7"], {"paths": [], "status": "none_named"})
            self.assertEqual(by_section["265.5"], {"paths": ["(a)(2)(i)"], "status": "attributed"})
            ix.conn.close()


class EvaluatorTests(unittest.TestCase):
    def test_group_is_registered_and_fails_against_the_starting_behavior(self):
        import re
        from unittest import mock
        self.assertIn("passive_location_paragraphs", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        items = reg["passive_location_paragraphs"]
        self.assertEqual([x["id"] for x in items], ["PA01", "PA02"])
        with tempfile.TemporaryDirectory() as tmp:
            ix = build_index(tmp, {**DOCS, **{k: fr_xml(v) for k, v in PASSIVE_DOCS.items()}})
            for x in items:
                self.assertTrue(evaluate.check_passive_location_paragraphs(ix, x)["passed"], x["id"])
            with mock.patch.object(fedreg, "PASSIVE_LOCATION_CITE", re.compile(r"(?!x)x")):  # the starting behavior: passive locations ignored
                self.assertFalse(evaluate.check_passive_location_paragraphs(ix, items[0])["passed"])
            ix.conn.close()


if __name__ == "__main__":
    unittest.main()
