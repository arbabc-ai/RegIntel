"""Tests for S7-P1-PARAGRAPH-EVIDENCE-INTEGRITY-37: (1) PARA_LOCATION reads a bare section number only, so "In § 265.7(d), in paragraph (7),
remove" establishes neither top-level (7) nor an inferred (d)(7); (2) units that support the requested future paragraph path lead the
future evidence list, so exact/hybrid ranking and truncation keep them (k=1 included) and a section-context unit cannot displace them."""
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import build, evaluate, fedreg, search
from test_p1 import asset
from test_p1_lowercase_paragraph_clause import EMPH, ecfr_265, fr_xml

CORPUS = Path("/home/arbab/projects/regintel-corpus/phase1")
P7 = "(d)(7)(ii)(B)(3)"
CITE = "12 CFR 265.7" + P7
SUPPORT, SECTION_CTX, RELATIVE, QUOTED = "fr:2026-96001", "fr:2026-95001", "fr:2026-95002", "fr:2026-95003"
DOCS = {
    SECTION_CTX: fr_xml("1. In § 265.7, in paragraph (d)(7)(ii)(A), remove “a” and add in its place “b”."),  # sorts first: a displacement risk
    RELATIVE: fr_xml("1. In § 265.7(d), in paragraph (7), remove “x”."),
    QUOTED: fr_xml("1. In § 265.7, revise the text to read “In § 265.7, in paragraph (d)(7)(ii)(B)(3), remove x.”"),
    SUPPORT: fr_xml("3. In § 265.5, in paragraph (a)(2)(i), remove “authority,” and add in its place “authorization,”", EMPH),
}


def build_index(tmp, docs=DOCS):
    root = Path(tmp) / "corpus"
    (root / "raw").mkdir(parents=True)
    out = {"ecfr:12:265": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 265", "title": "Rules",
                           "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                           "asset": asset(root, "e265.html", ecfr_265().encode())}}
    for key, xml in docs.items():
        out[key] = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01", "document_type": "Rule",
                    "effective_on": "2027-01-01", "action": "Final rule.", "document_number": key.split(":")[1], "title": "t",
                    "asset": asset(root, key.replace(":", "_") + ".xml", xml)}
    (root / "manifest.json").write_text(json.dumps({"documents": out, "failures": {}, "updated_at": "x"}))
    db = str(Path(tmp) / "i.sqlite")
    conn, _ = build.build(root, db, "2026-10-29", log=lambda *_: None)
    conn.close()
    return search.Index(db)


class BareSectionGrammarTests(unittest.TestCase):
    def test_a_paragraph_in_the_section_clause_makes_the_lowercase_path_relative_and_unattributed(self):
        for text in ("In § 265.7(d), in paragraph (7), remove “x”.", "In § 265.7(d)(7), in paragraph (ii), remove “x”.",
                     "In § 265.7 (d), in paragraph (7), remove “x”.", "In § 265.7(d) , in paragraph (7), revise the text."):
            self.assertEqual(fedreg.named_paragraph_paths(text), [], text)

    def test_bare_section_clauses_remain_supported_including_inline_emphasis_spacing(self):
        self.assertEqual(fedreg.named_paragraph_paths("In § 265.7, in paragraph (d)(7)(ii)(B)( 3 ), remove “x”."), [P7])
        self.assertEqual(fedreg.named_paragraph_paths("In § 265.7, in paragraph (d)(7)(ii)(B)(\n3\n), remove “x”."), [P7])
        self.assertEqual(fedreg.named_paragraph_paths("In § 265.5, in paragraph (a)(2)(i), remove “x”."), ["(a)(2)(i)"])
        self.assertEqual(fedreg.named_paragraph_paths("In § 265.5a, in paragraph (b), add “x”."), ["(b)"])

    def test_quoted_and_cross_reference_clauses_remain_excluded(self):
        for text in ("In § 265.7, revise the text to read “In § 265.7, in paragraph (d)(7)(ii)(B)(3), remove x.”",
                     "In § 265.7, as provided in paragraph (d)(7)(ii)(B)(3), revise the text."):
            self.assertEqual(fedreg.named_paragraph_paths(text), [], text)


class SyntheticRankingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def test_relative_clause_establishes_neither_top_level_7_nor_d_7(self):
        for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(d)(7)"):
            inst = {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(cite)["instruments"]}
            self.assertFalse(inst[RELATIVE]["resolved"], cite)
            self.assertEqual((inst[RELATIVE]["achieved"], inst[RELATIVE]["reason"], inst[RELATIVE]["supported_by"]),
                             ("section_only", "paragraph_precision_unresolved", []), cite)
            self.assertIn(RELATIVE, {g["doc_key"] for g in self.ix.delta(cite)["instruments"]})  # still listed, as section context

    def test_supporting_unit_leads_exact_and_hybrid_at_k1_and_larger_budgets_in_future_and_both(self):
        for mode in ("exact", "hybrid"):
            for scope in ("future", "both"):
                for k in (1, 2, 3, 10):
                    out = self.ix.answer(CITE, mode=mode, scope=scope, k=k)
                    tag = (mode, scope, k)
                    fut = [r for r in out["results"] if r["doc_key"].startswith("fr:")]
                    if scope == "both" and k == 1:  # unchanged interleaving: the current baseline record comes first in both scope
                        self.assertTrue(out["results"][0]["doc_key"].startswith("ecfr:"), tag)
                        continue
                    if mode == "hybrid" and scope == "both":
                        # hybrid pins only the first record of a citation (the baseline here); the rest is fused lexical ranking,
                        # which is unchanged. The supporting unit must still be returned once the budget allows.
                        if k == 10:
                            self.assertIn(SUPPORT, {r["doc_key"] for r in out["results"]}, tag)
                        continue
                    self.assertEqual(fut[0]["doc_key"], SUPPORT, tag)  # the first future unit is the supporting one
                    self.assertTrue(fut[0]["locator"].startswith("2026-96001:"), tag)
                    self.assertEqual(out["status"], "evidence_found", tag)
                    if scope == "future":
                        self.assertEqual(out["results"][0]["doc_key"], SUPPORT, tag)
                    if k >= 4:  # larger budgets still list the independent section-context instruments, labelled as such
                        self.assertIn(SECTION_CTX, {r["doc_key"] for r in out["results"]}, tag)

    def test_independent_instruments_stay_labelled_and_unsupported_paths_stay_unresolved(self):
        inst = {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(CITE)["instruments"]}
        self.assertTrue(inst[SUPPORT]["resolved"])
        for other in (SECTION_CTX, RELATIVE, QUOTED):
            self.assertEqual((inst[other]["resolved"], inst[other]["achieved"]), (False, "section_only"), other)
        r = self.ix.resolve_citations("12 CFR 265.7(d)(7)(ii)(B)(4)", "future")[0]
        self.assertEqual((r["resolved"], r["achieved"], r["future_paragraph_support"]), (False, "section_only", []))
        out = self.ix.answer("12 CFR 265.7(d)(7)(ii)(B)(4)", mode="exact", scope="future", k=1)
        self.assertNotIn(SUPPORT, {x["doc_key"] for x in out["results"]})  # no support: no unit is promoted

    def test_part_filter_current_scope_and_citation_interleaving_are_unchanged(self):
        for mode in ("exact", "hybrid"):
            self.assertEqual(self.ix.answer(CITE, mode=mode, scope="future", k=1, parts=["12:3"])["results"], [], mode)
            cur = self.ix.answer(CITE, mode=mode, scope="current", k=1)
            self.assertTrue(cur["results"] and all(r["doc_key"].startswith("ecfr:") for r in cur["results"]), mode)
        two = self.ix.answer(f"{CITE} and 12 CFR 265.5(a)(2)(i)", mode="exact", scope="future", k=2)
        self.assertEqual(len(two["results"]), 2)  # round-robin: each citation keeps its own first record
        self.assertEqual(len({r["doc_key"] for r in two["results"]}), 1)  # both supporting units are from the same document here
        self.assertEqual(len({r["locator"] for r in two["results"]}), 2)

    def test_applicability_validity_and_relationships_are_unchanged(self):
        self.assertEqual((self.ix.answer(CITE, mode="exact", scope="future", k=1)["applicability_status"], self.ix.delta(CITE)["legal_validity"]),
                         ("not_assessed", "not_determined"))
        self.assertTrue({r[0] for r in self.ix.conn.execute("SELECT DISTINCT review_status FROM relationship")}
                        <= {"parsed_unreviewed", "regex_unreviewed", "unresolved_candidate"})


@unittest.skipUnless((CORPUS / "manifest.json").exists(), "public corpus not available")
class AcquiredRankingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        manifest = json.loads((CORPUS / "manifest.json").read_text())["documents"]
        root = Path(cls.tmp.name) / "corpus"
        docs = {}
        for key in ("fr:2026-04373", "fr:2026-20247"):
            d = json.loads(json.dumps(manifest[key]))
            dest = root / d["asset"]["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CORPUS / d["asset"]["path"], dest)
            docs[key] = d
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        db = str(Path(cls.tmp.name) / "a.sqlite")
        conn, _ = build.build(root, db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.ix = search.Index(db)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def test_acquired_k1_returns_the_supporting_unit_not_the_newer_section_context_unit(self):
        for mode in ("exact", "hybrid"):
            for scope in ("future", "both"):
                for k in (1, 2, 5):
                    out = self.ix.answer(CITE, mode=mode, scope=scope, k=k)
                    self.assertEqual((out["results"][0]["doc_key"], out["results"][0]["locator"]), ("fr:2026-04373", "2026-04373:reg:9:265.7:5"), (mode, scope, k))
        inst = {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(CITE)["instruments"]}
        self.assertEqual((inst["fr:2026-20247"]["resolved"], inst["fr:2026-20247"]["achieved"]), (False, "section_only"))
        self.assertEqual(self.ix.resolve_citations("12 CFR 265.7(d)(7)(ii)(B)(4)", "future")[0]["resolved"], False)


class EvaluatorTests(unittest.TestCase):
    def test_group_is_registered_in_the_aggregate_and_the_grammar_check_fails_on_the_starting_grammar(self):
        self.assertIn("paragraph_evidence", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["paragraph_evidence"]], ["PE01", "PE02"])

        class Stub:  # no acquired documents: the ranking check has nothing to test and passes vacuously
            class conn:
                @staticmethod
                def execute(*_):
                    return []
        for x in reg["paragraph_evidence"]:
            self.assertTrue(evaluate.check_paragraph_evidence_integrity(Stub(), x)["passed"], x["id"])
        starting = re.compile(r"(?<![A-Za-z])(?-i:In)\s+§\s*" + fedreg.SEC_ITEM + r"\s*,\s+(?-i:in)\s+paragraphs?\s+" + fedreg.PARA_LIST +
                              r"(?=\s*,\s*" + fedreg.VERB + r"\b)", re.I)
        with mock.patch.object(fedreg, "PARA_LOCATION", starting):
            self.assertFalse(evaluate.check_paragraph_evidence_integrity(Stub(), reg["paragraph_evidence"][0])["passed"])


if __name__ == "__main__":
    unittest.main()
