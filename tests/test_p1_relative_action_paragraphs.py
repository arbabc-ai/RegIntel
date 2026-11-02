"""Tests for S7-P1-RELATIVE-ACTION-PARAGRAPHS-38: a paragraph named by an action inside a paragraph-qualified section location
("In § 265.7(d), revise paragraph (7) to read as follows:") is relative to that location. It establishes neither a top-level (7) nor an
inferred (d)(7); the instruction stays section context with unresolved paragraph precision. The guard sits on the action-matching route.
Fully explicit paths after bare section locations, quoted-text exclusion and the acquired fr:2026-04373 support are preserved."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import evaluate, fedreg, search
from test_p1_paragraph_evidence_integrity import CITE, CORPUS, P7, SUPPORT, build_index, DOCS
from test_p1_lowercase_paragraph_clause import fr_xml

REL = {"fr:2026-97001": "1. In § 265.7(d), revise paragraph (7) to read as follows:",
       "fr:2026-97002": "1. In § 265.7(d), remove paragraph (7).",
       "fr:2026-97003": "1. In § 265.7(d), add paragraph (7) to read as follows:",
       "fr:2026-97004": "1. In § 265.7(d)(7), revise paragraph (ii) to read as follows:",
       "fr:2026-97005": "1. In § 265.7 ( d ), revise paragraph ( 7 ) to read as follows:",
       "fr:2026-97006": "1. In § 265.7(d),\n   revise paragraph (7) to read as follows:"}
CASES = [
    ("In § 265.7(d), revise paragraph (7) to read as follows:", []), ("In § 265.7(d), remove paragraph (7).", []),
    ("In § 265.7(d), add paragraph (7) to read as follows:", []), ("In § 265.7(d), remove and reserve paragraph (7).", []),
    ("In § 265.7(d)(7), revise paragraph (ii).", []), ("In § 265.7(d)(7)(ii), remove paragraph (B)(3).", []),
    ("In § 265.7 ( d ), revise paragraph ( 7 ).", []), ("In § 265.7(d),\n revise paragraph (7).", []),
    ("In § 265.7(d), In paragraph (7), removing “x”.", []),
    ("Amend § 330.11(a) by adding paragraph (3) to read as follows:", []),
    ("In § 265.7(d), revise paragraph (d)(7).", []),                                        # even written in full after a qualified location
    ("In § 265.7, in paragraph (d), revise paragraph (7).", ["(d)"]),                        # relative to the established lowercase path
    ("In § 265.7, revise paragraph (d)(7).", ["(d)(7)"]),
    ("In § 265.7, remove and reserve paragraph (d)(2).", ["(d)(2)"]),
    ("In § 265.7, revise paragraph (d)(7)(ii)(B)( 3 ).", [P7]),
    ("In § 265.5, revise paragraph (a). In § 265.7, revise paragraph (d)(7).", ["(a)", "(d)(7)"]),
    ("In § 265.7(d), revise the heading. In § 265.8, revise paragraph (a).", ["(a)"]),     # a new sentence has its own location
    ("In § 265.7, revise paragraph (a) as described in § 3.22(c).", ["(a)"]),               # a later citation does not govern an earlier action
    ("In § 265.7, revise the text to read “In § 265.7(d), revise paragraph (7).” and revise paragraph (e).", ["(e)"]),   # quoted text excluded
]


class ParserTests(unittest.TestCase):
    def test_parser_output_for_relative_and_explicit_actions(self):
        for text, expected in CASES:
            self.assertEqual(fedreg.named_paragraph_paths(text), expected, text)


class SyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        docs = {**DOCS, **{k: fr_xml(v) for k, v in REL.items()}}
        cls.ix = build_index(cls.tmp.name, docs)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def test_stored_attribution_is_none_named_for_relative_actions(self):
        for key in REL:
            rows = self.ix.conn.execute("SELECT p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment'", (key,)).fetchall()
            self.assertTrue(rows, key)
            for r in rows:
                self.assertEqual(json.loads(r[0]), {"paths": [], "status": "none_named"}, key)

    def test_per_instrument_delta_keeps_relative_actions_as_section_context(self):
        for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(d)(7)", "12 CFR 265.7(7)(ii)", "12 CFR 265.7(d)(7)(ii)"):
            inst = {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(cite)["instruments"]}
            for key in REL:
                self.assertIn(key, inst, (cite, key))  # the instruction is still listed for the section
                self.assertEqual((inst[key]["resolved"], inst[key]["achieved"], inst[key]["reason"], inst[key]["supported_by"]),
                                 (False, "section_only", "paragraph_precision_unresolved", []), (cite, key))

    def test_answers_are_not_evidence_found_for_unsupported_relative_paths_across_modes_scopes_and_budgets(self):
        for cite in ("12 CFR 265.7(7)", "12 CFR 265.7(d)(7)"):
            for mode in ("exact", "hybrid"):
                for scope in ("future", "both"):
                    for k in (1, 2, 5):
                        out = self.ix.answer(cite, mode=mode, scope=scope, k=k)
                        if scope == "future" or cite == "12 CFR 265.7(7)":  # (d)(7) exists in the current baseline, so both scope may resolve there
                            self.assertNotEqual(out["status"], "evidence_found", (cite, mode, scope, k))
                        r = self.ix.resolve_citations(cite, scope)[0]
                        self.assertFalse(r["resolved"] and r["achieved"] == "paragraph" and r["future_paragraph_support"], (cite, scope))
                        self.assertEqual(r["future_paragraph_support"], [], (cite, scope))

    def test_fully_explicit_path_support_and_its_ranking_are_preserved(self):
        inst = {g["doc_key"]: g["paragraph_precision"] for g in self.ix.delta(CITE)["instruments"]}
        self.assertTrue(inst[SUPPORT]["resolved"])
        for mode in ("exact", "hybrid"):
            for k in (1, 2, 5):
                self.assertTrue(self.ix.answer(CITE, mode=mode, scope="future", k=k)["results"][0]["locator"].startswith("2026-96001:"), (mode, k))
        self.assertEqual(self.ix.answer(CITE, mode="exact", scope="future", k=1, parts=["12:3"])["results"], [])
        cur = self.ix.answer("12 CFR 265.7(d)(7)", mode="exact", scope="current", k=1)
        self.assertTrue(cur["results"] and all(r["doc_key"].startswith("ecfr:") for r in cur["results"]))
        self.assertEqual((cur["applicability_status"], self.ix.delta(CITE)["legal_validity"]), ("not_assessed", "not_determined"))


@unittest.skipUnless((CORPUS / "manifest.json").exists(), "public corpus not available")
class AcquiredCorpusTests(unittest.TestCase):
    def units(self, key):
        import hashlib
        d = json.loads((CORPUS / "manifest.json").read_text())["documents"][key]["asset"]
        raw = (CORPUS / d["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), d["sha256"])
        return fedreg.parse_fr(raw, key.split(":")[1])[1]

    def test_acquired_2026_04373_attribution_is_preserved(self):
        got = {u["unit_id"]: u["explicit_paragraphs"]["paths"] for u in self.units("fr:2026-04373")}
        self.assertEqual(got["2026-04373:reg:6"], ["(a)(2)(i)"])
        self.assertEqual(got["2026-04373:reg:9"], [P7])

    def test_the_only_acquired_change_is_the_relative_330_11_a_action(self):
        manifest = json.loads((CORPUS / "manifest.json").read_text())["documents"]

        def named():
            out = {}
            for key, d in manifest.items():
                if d.get("publisher") == "Federal Register":
                    for i, u in enumerate(self.units(key)):  # unit ids repeat within a document, so key by position
                        if u.get("instruction"):
                            out[(key, i)] = fedreg.named_paragraph_paths(u["instruction"])
            return out
        new = named()
        with mock.patch.object(fedreg, "_relative_to_enclosing_location", lambda *a: False):
            old = named()
        changed = sorted(k for k in new if new[k] != old[k])
        self.assertEqual(len(changed), 1, changed)
        key, pos = changed[0]
        self.assertEqual((key, old[changed[0]], new[changed[0]]), ("fr:2026-06974", ["(3)"], []))
        ins = self.units(key)[pos]["instruction"]
        self.assertIn("Amend § 330.11(a) by adding paragraph (3)", ins)  # the path is (a)(3): the old top-level (3) was false evidence


class EvaluatorTests(unittest.TestCase):
    def test_group_is_registered_and_fails_against_the_starting_behavior(self):
        self.assertIn("relative_action_paragraphs", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        items = reg["relative_action_paragraphs"]
        self.assertEqual([x["id"] for x in items], ["RA01", "RA02"])
        with tempfile.TemporaryDirectory() as tmp:
            ix = build_index(tmp, {**DOCS, **{k: fr_xml(v) for k, v in REL.items()}})
            for x in items:
                self.assertTrue(evaluate.check_relative_action_paragraphs(ix, x)["passed"], x["id"])
            with mock.patch.object(fedreg, "_relative_to_enclosing_location", lambda *a: False):  # the starting behavior
                self.assertFalse(evaluate.check_relative_action_paragraphs(ix, items[0])["passed"])
            ix.conn.close()


if __name__ == "__main__":
    unittest.main()
