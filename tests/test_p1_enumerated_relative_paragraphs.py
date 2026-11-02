"""Tests for S7-P1-ENUMERATED-RELATIVE-PARAGRAPHS-39: the enclosing paragraph-qualified location of an amendatory instruction is not lost across
colons, semicolons, sentence ends and enumerators ("Amend § 35.4(a)(2) by: a. In paragraph (i), removing …; b. In paragraph (ii), …").
Its relative operands establish neither top-level paths nor inferred joined paths and the instruction stays section context. Only a new
location citation ("In § …", "Amend § …") ends the governing location; a reference citation never does. Expected values here are literals,
not outputs of named_paragraph_paths."""
import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, evaluate, fedreg, search
from test_p1_lowercase_paragraph_clause import fr_xml
from test_p1_paragraph_evidence_integrity import CITE, CORPUS, DOCS, P7, SUPPORT, build_index

KEY16454 = "fr:2026-16454"
SHA16454 = "521f93de594fec6be97e915afbb340ab28b9a190473a9dac18ac55380706180f"
OPERANDS = ["(i)", "(ii)", "(iii)", "(iv)", "(v)", "(vi)", "(vii)"]
LITERAL = [
    # relative operands under a paragraph-qualified location: nothing is named
    ("Amend § 265.7(d) by: a. In paragraph (7), removing “x”; b. In paragraph (8), removing “y”.", []),
    ("In § 265.7(d): a. Revise paragraph (7); b. Remove paragraph (8).", []),
    ("In § 265.7(d), revise paragraph (7); remove paragraph (8); add paragraph (9) to read as follows:", []),
    ("Amend § 265.7(d) by: 1. In paragraph (7), removing “x”. 2. In paragraph (8), removing “y”.", []),
    ("Amend § 265.7(d)(7) by: i. In paragraph (ii), removing “x”; ii. In paragraph (iii), removing “y”.", []),
    ("Amend § 265.7(d) by:\n a. In paragraph ( 7 ),\n removing “x”;\n b. In paragraph (8), removing “y”.", []),
    ("Amend § 265.7(d) by: a. In paragraph (7), removing “x” and adding in its place “for purposes of § 3.10(a)(5)”; b. In paragraph (8), removing “y”.", []),
    # a reference citation never ends the governing location
    ("In § 265.7(d), revise paragraph (7) as described in § 3.22; revise paragraph (8).", []),
    # a bare section location: explicit paths are still named, with or without enumerators
    ("Amend § 265.7 by: a. In paragraph (d)(7), removing “x”; b. In paragraph (e)(1), removing “y”.", ["(d)(7)", "(e)(1)"]),
    ("In § 265.7: a. Revise paragraphs (a)(1) and (a)(4); b. Remove paragraph (a)(6).", ["(a)(1)", "(a)(4)", "(a)(6)"]),
    ("In § 265.7, revise paragraph (a) for purposes of § 3.10(a)(5); revise paragraph (c)(2).", ["(a)", "(c)(2)"]),
    # a genuinely new explicit location restarts the context
    ("Amend § 265.7(d) by: a. In paragraph (7), removing “x”. b. In § 265.8, in paragraph (c), removing “y”.", ["(c)"]),
    ("Amend § 265.7(d) by: a. In paragraph (7), removing “x”. b. In § 265.8, revise paragraph (a)(2).", ["(a)(2)"]),
    ("In § 265.7(d), revise the heading; b. In § 265.9, revise paragraph (b)(3).", ["(b)(3)"]),
    # quoted text is excluded
    ("Amend § 265.7 by revising the text to read “a. In paragraph (7), x; b. In paragraph (8), y”.", []),
    ("Amend § 265.7(d) by: a. Revising the text to read “In § 265.8, in paragraph (c), remove x.”", []),
]
RELATIVE_DOCS = {
    "fr:2026-97101": "1. Amend § 265.7(d) by: a. In paragraph (7), removing “x” and adding in its place “y”; b. In paragraph (8), removing “z”.",
    "fr:2026-97102": "1. In § 265.7(d): a. Revise paragraph (7); b. Remove paragraph (8).",
    "fr:2026-97103": "1. Amend § 265.7(d) by: 1. In paragraph (7), removing “x”. 2. In paragraph (8), removing “y”.",
}


class LiteralParserTests(unittest.TestCase):
    def test_literal_expected_paths(self):
        for text, expected in LITERAL:
            self.assertEqual(fedreg.named_paragraph_paths(text), expected, text)


class SyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name, {**DOCS, **{k: fr_xml(v) for k, v in RELATIVE_DOCS.items()}})

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def test_stored_attribution_is_none_named(self):
        for key in RELATIVE_DOCS:
            rows = self.ix.conn.execute("SELECT p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment'", (key,)).fetchall()
            self.assertTrue(rows, key)
            for r in rows:
                self.assertEqual(json.loads(r[0]), {"paths": [], "status": "none_named"}, key)

    def test_per_instrument_delta_is_section_context_for_every_operand(self):
        for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(8)", "12 CFR 265.7(9)", "12 CFR 265.7(d)(7)", "12 CFR 265.7(d)(8)"):
            inst = {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(cite)["instruments"]}
            for key in RELATIVE_DOCS:
                self.assertIn(key, inst, (cite, key))
                self.assertEqual((inst[key]["resolved"], inst[key]["achieved"], inst[key]["reason"], inst[key]["supported_by"]),
                                 (False, "section_only", "paragraph_precision_unresolved", []), (cite, key))

    def test_answers_across_modes_scopes_budgets_and_filters(self):
        for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(8)"):
            for mode in ("exact", "hybrid"):
                for scope in ("future", "both"):
                    for k in (1, 2, 5, 10):
                        out = self.ix.answer(cite, mode=mode, scope=scope, k=k)
                        self.assertNotEqual(out["status"], "evidence_found", (cite, mode, scope, k))
                        self.assertEqual(self.ix.resolve_citations(cite, scope)[0]["future_paragraph_support"], [], (cite, scope))
                    self.assertEqual(self.ix.answer(cite, mode=mode, scope=scope, k=1, parts=["12:3"])["results"], [], (cite, mode, scope))
                    out = self.ix.answer(cite, mode=mode, scope=scope, k=10, parts=["12:265"])
                    self.assertTrue(out["results"], (cite, mode, scope))  # the included part still returns section context

    def test_independent_instrument_ranking_and_current_baseline_are_unchanged(self):
        inst = {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(CITE)["instruments"]}
        self.assertTrue(inst[SUPPORT]["resolved"])
        for mode in ("exact", "hybrid"):
            for k in (1, 2, 5):
                self.assertTrue(self.ix.answer(CITE, mode=mode, scope="future", k=k)["results"][0]["locator"].startswith("2026-96001:"), (mode, k))
        cur = self.ix.answer("12 CFR 265.7(d)(7)", mode="exact", scope="current", k=1)
        self.assertTrue(cur["results"] and all(r["doc_key"].startswith("ecfr:") for r in cur["results"]))
        self.assertEqual((cur["applicability_status"], self.ix.delta(CITE)["legal_validity"]), ("not_assessed", "not_determined"))
        self.assertTrue({r[0] for r in self.ix.conn.execute("SELECT DISTINCT review_status FROM relationship")}
                        <= {"parsed_unreviewed", "regex_unreviewed", "unresolved_candidate"})


@unittest.skipUnless((CORPUS / "manifest.json").exists(), "public corpus not available")
class AcquiredInstructionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest = json.loads((CORPUS / "manifest.json").read_text())["documents"]
        cls.asset = manifest[KEY16454]["asset"]
        cls.raw = (CORPUS / cls.asset["path"]).read_bytes()
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        dest = root / cls.asset["path"]
        dest.parent.mkdir(parents=True)
        shutil.copyfile(CORPUS / cls.asset["path"], dest)
        (root / "manifest.json").write_text(json.dumps({"documents": {KEY16454: json.loads(json.dumps(manifest[KEY16454]))}, "failures": {}, "updated_at": "x"}))
        db = str(Path(cls.tmp.name) / "a.sqlite")
        conn, _ = build.build(root, db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.ix = search.Index(db)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def instruction_row(self):
        rows = self.ix.conn.execute("SELECT p.locator, p.instruction, p.explicit_paragraphs, p.explicit_targets FROM provision p JOIN source_document d ON d.id=p.doc_id "
                                    "WHERE d.doc_key=? AND p.locator=?", (KEY16454, "2026-16454:reg:1:35.4:61")).fetchall()
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_source_bytes_are_the_hash_pinned_ones(self):
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), SHA16454)
        self.assertEqual(self.asset["sha256"], SHA16454)

    def test_instruction_9_has_the_expected_literal_operands_and_no_stored_paths(self):
        loc, ins, ev, targets = self.instruction_row()
        self.assertTrue(ins.startswith("9. Amend § 35.4(a)(2) by: a. In paragraph (i),"))
        operands = ["(" + m + ")" for m in re.findall(r"In paragraph \(([ivx]+)\)", ins)]  # independent of named_paragraph_paths
        self.assertEqual(operands, OPERANDS)
        self.assertEqual(json.loads(ev), {"paths": [], "status": "none_named"})
        self.assertEqual(json.loads(targets), ["35.4"])

    def test_every_operand_stays_section_context_in_delta_and_answers(self):
        for operand in OPERANDS:
            for cite in (f"12 CFR 35.4(a)(2){operand}", f"12 CFR 35.4{operand}"):
                inst = {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(cite)["instruments"]}
                self.assertEqual((inst[KEY16454]["resolved"], inst[KEY16454]["achieved"], inst[KEY16454]["supported_by"]), (False, "section_only", []), cite)
                for mode in ("exact", "hybrid"):
                    for scope in ("future", "both"):
                        for k in (1, 2, 5):
                            out = self.ix.answer(cite, mode=mode, scope=scope, k=k)
                            self.assertNotEqual(out["status"], "evidence_found", (cite, mode, scope, k))
                self.assertEqual(self.ix.answer(cite, mode="exact", scope="future", k=1, parts=["12:3"])["results"], [], cite)


class EvaluatorTests(unittest.TestCase):
    def test_group_is_registered_and_fails_against_the_starting_behavior(self):
        from unittest import mock
        self.assertIn("enumerated_relative_paragraphs", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        items = reg["enumerated_relative_paragraphs"]
        self.assertEqual([x["id"] for x in items], ["ER01", "ER02"])

        class Stub:  # no acquired documents: ER02 has nothing to test and passes vacuously
            class conn:
                @staticmethod
                def execute(*_):
                    class Empty:
                        fetchone = staticmethod(lambda: None)
                    return Empty()
        for x in items:
            self.assertTrue(evaluate.check_enumerated_relative_paragraphs(Stub(), x)["passed"], x["id"])
        sentence_bounded = re.compile(r"[.;:]\s")  # the starting behavior: the governing location ended at a colon, semicolon or sentence end

        def starting(stripped, match, locations):
            if match in locations:
                return False
            breaks = [b.end() for b in sentence_bounded.finditer(stripped, 0, match.start())]
            start = breaks[-1] if breaks else 0
            cites = list(re.finditer(r"§§?\s*\d+\.\d+[A-Za-z]?((?:\s*\([A-Za-z0-9]+\))*)", stripped[start:match.start()]))
            return bool(cites and cites[-1].group(1).strip())
        with mock.patch.object(fedreg, "_relative_to_enclosing_location", starting):
            self.assertFalse(evaluate.check_enumerated_relative_paragraphs(Stub(), items[0])["passed"])


if __name__ == "__main__":
    unittest.main()
