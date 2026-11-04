"""Tests for S7-P1-LOWERCASE-PARAGRAPH-CLAUSE-36: an explicit amendatory location clause "In § SECTION, in paragraph PATH, remove/revise/add ..."
(outside quoted text, one section) names PATH. Cross-references ("provided in paragraph ..."), quoted replacement text, several sections
and malformed suffixes name nothing. Paragraph attribution is textual evidence only."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import build, evaluate, fedreg, search
from test_p1 import asset

CORPUS = Path("/home/arbab/projects/regintel-corpus/phase1")
KEY, OTHER = "fr:2026-96001", "fr:2026-96002"
P7 = "(d)(7)(ii)(B)(3)"
POSITIVE = [
    ("In § 265.5, in paragraph (a)(2)(i), remove “Board's approval authority,” and add in its place “Board's approval, authorization,”", ["(a)(2)(i)"]),
    ("In § 265.7, in paragraph (d)(7)(ii)(B)( 3 ), remove “(252.146 and 252.158);” and add in its place “(12 CFR 252.146 and 252.158);”", [P7]),
    ("In § 265.7, in paragraph (d)(7)(ii)(B)(\n   3\n  ), remove “x”.", [P7]),
    ("In § 265.7, in paragraph (d)(2), revise the text.", ["(d)(2)"]),
    ("In § 265.4, remove and reserve paragraph (d)(2).", ["(d)(2)"]),  # the existing form is unchanged
]
NEGATIVE = [
    "In § 265.5, as provided in paragraph (e)(3), revise the text.",                                   # cross-reference
    "In § 265.7, revise the definition described in paragraph (d)(7)(ii)(B)(3).",                      # reference, no location clause
    "In § 265.7, revise the text to read “In § 265.5, in paragraph (d)(7)(ii)(B)(3), remove x.”",      # quoted replacement text
    "In §§ 265.5 and 265.7, in paragraph (d)(7)(ii)(B)(3), remove “y”.",                               # several sections: not attributable
    "In § 265.7, in paragraph (d)(7)(ii)(B)(3, remove “z”.",                                           # malformed suffix
    "In § 265.7, in paragraph (d)(7)(ii)(B)(3) of this section, revise the text.",                     # not followed by an action clause
    "In § 265.5, in paragraph (a)(2) described in § 265.6, revise it.",
    "The paragraph in § 265.7, in paragraph (d)(2), is referenced.",                                   # no clause-initial capital In
]


def ecfr_265():
    def para(sec, path, text):
        return f'<div id="p-{sec}{path}"><p class="indent-1" data-title="{sec}{path}">{path} {text}</p></div>'
    s5 = "".join(para("265.5", p, "Text.") for p in ("(a)", "(a)(2)", "(a)(2)(i)"))
    s7 = "".join(para("265.7", p, "Text.") for p in ("(d)", "(d)(7)", "(d)(7)(ii)", "(d)(7)(ii)(A)", "(d)(7)(ii)(B)", "(d)(7)(ii)(B)(3)"))
    return (f'<div class="part" id="part-265"><h1>PART 265—RULES</h1><div class="section" id="265.5"><h4>§ 265.5 Delegation.</h4>{s5}</div>'
            f'<div class="section" id="265.7"><h4>§ 265.7 Authority.</h4>{s7}</div></div>')


def fr_xml(*instructions, root="RULE"):
    body = "".join(f"<REGTEXT TITLE=\"12\" PART=\"265\"><AMDPAR>{i}</AMDPAR></REGTEXT>" for i in instructions)
    return f"<{root}><PREAMB><CFR>12 CFR Part 265</CFR></PREAMB><SUPLINF>{body}</SUPLINF></{root}>".encode()


EMPH = ('5. In § 265.7, in paragraph (d)(7)(ii)(B)(\n<E T="03">3</E>\n), remove “(252.146 and 252.158);” and add in its place “(12 CFR 252.146 and 252.158);”')
DOCS = {
    KEY: fr_xml("3. In § 265.5, in paragraph (a)(2)(i), remove “authority,” and add in its place “authorization,”", EMPH),
    OTHER: fr_xml("1. In § 265.7, in paragraph (d)(7)(ii)(A), remove “a” and add in its place “b”."),     # same section, a different paragraph
    "fr:2026-96003": fr_xml("1. In § 265.7, as provided in paragraph (d)(7)(ii)(B)(3), revise the text."),
    "fr:2026-96004": fr_xml("1. In § 265.7, revise the text to read “In § 265.5, in paragraph (d)(7)(ii)(B)(3), remove x.”"),
    "fr:2026-96005": fr_xml("1. In §§ 265.5 and 265.7, in paragraph (d)(7)(ii)(B)(3), remove “y”."),
    "fr:2026-96006": fr_xml("1. In § 265.7, in paragraph (d)(7)(ii)(B)(3, remove “z”."),
}


def build_index(tmp):
    root = Path(tmp) / "corpus"
    (root / "raw").mkdir(parents=True)
    docs = {"ecfr:12:265": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 265", "title": "Rules",
                            "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                            "asset": asset(root, "e265.html", ecfr_265().encode())}}
    for key, xml in DOCS.items():
        docs[key] = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01", "document_type": "Rule",
                     "effective_on": "2027-01-01", "action": "Final rule.", "document_number": key.split(":")[1], "title": "t",
                     "asset": asset(root, key.replace(":", "_") + ".xml", xml)}
    (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
    db = str(Path(tmp) / "i.sqlite")
    conn, _ = build.build(root, db, "2026-10-29", log=lambda *_: None)
    conn.close()
    return search.Index(db)


class GrammarTests(unittest.TestCase):
    def test_location_clause_names_its_path_including_inline_emphasis_spacing(self):
        for text, expected in POSITIVE:
            self.assertEqual(fedreg.named_paragraph_paths(text), expected, text)

    def test_cross_references_quoted_text_several_sections_and_malformed_suffixes_name_nothing(self):
        for text in NEGATIVE:
            self.assertEqual(fedreg.named_paragraph_paths(text), [], text)

    def test_a_bare_in_paragraph_is_not_a_target_elsewhere(self):
        for text in ("As described in paragraph (a)(2), revise § 265.5.", "Revise the text in paragraph (a)(2)(i) of § 265.5."):
            self.assertEqual(fedreg.named_paragraph_paths(text), [], text)


class SyntheticDeltaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def prec(self, citation):
        return {g["doc_key"]: g["paragraph_precision"] if "paragraph_precision" in g else g.get("future_precision") for g in self.ix.delta(citation)["instruments"]}

    def instruments(self, citation):
        d = self.ix.delta(citation)
        return {g["doc_key"]: g for g in d["instruments"]}

    def test_both_paths_are_attributed_from_the_named_instruction_only(self):
        rows = {r[0]: json.loads(r[1]) for r in self.ix.conn.execute(
            "SELECT p.locator, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment'", (KEY,))}
        self.assertEqual(sorted((v["status"], tuple(v["paths"])) for v in rows.values()),
                         [("attributed", ("(a)(2)(i)",)), ("attributed", (P7,))])

    def test_delta_support_comes_from_the_named_instrument_and_others_stay_section_context(self):
        for citation, path in (("12 CFR 265.7" + P7, P7), ("12 CFR 265.5(a)(2)(i)", "(a)(2)(i)")):
            inst = self.instruments(citation)
            p = inst[KEY]["paragraph_precision"]
            self.assertEqual((p["resolved"], p["achieved"], p["requested_path"]), (True, "paragraph", path), citation)
            self.assertEqual(len(p["supported_by"]), 1, citation)
            for other, g in inst.items():
                if other != KEY:
                    q = g["paragraph_precision"]
                    self.assertEqual((q["resolved"], q["achieved"], q["reason"]), (False, "section_only", "paragraph_precision_unresolved"), (citation, other))

    def test_negative_controls_never_support_the_path(self):
        inst = self.instruments("12 CFR 265.7" + P7)
        for key in ("fr:2026-96003", "fr:2026-96004", "fr:2026-96005", "fr:2026-96006", OTHER):
            if key in inst:
                self.assertFalse(inst[key]["paragraph_precision"]["resolved"], key)
                self.assertEqual(inst[key]["paragraph_precision"]["supported_by"], [], key)

    def test_exact_future_citation_resolves_from_the_named_instrument(self):
        r = self.ix.resolve_citations("12 CFR 265.7" + P7, "future")[0]
        self.assertTrue(r["resolved"])
        out = self.ix.answer("12 CFR 265.7" + P7, mode="exact", scope="future", k=10)
        self.assertEqual({x["doc_key"] for x in r["future_paragraph_support"]}, {KEY})  # support from the named instrument only
        self.assertEqual(out["status"], "evidence_found")
        self.assertIn(KEY, {x["doc_key"] for x in out["results"]})
        self.assertEqual((out["applicability_status"], self.ix.delta("12 CFR 265.7" + P7)["legal_validity"]), ("not_assessed", "not_determined"))

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.ix.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed", "unresolved_candidate"}, status)


@unittest.skipUnless((CORPUS / "manifest.json").exists(), "public corpus not available")
class AcquiredSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = json.loads((CORPUS / "manifest.json").read_text())["documents"]

    def amendments(self, key):
        import hashlib
        a = self.docs[key]["asset"]
        raw = (CORPUS / a["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), a["sha256"])  # the raw bytes are the pinned ones
        _provs, units, _stats = fedreg.parse_fr(raw, key.split(":")[1])
        return units

    def test_acquired_2026_04373_attributes_instructions_3_and_5(self):
        got = {u["unit_id"]: u["explicit_paragraphs"] for u in self.amendments("fr:2026-04373")}
        self.assertEqual(got["2026-04373:reg:6"], {"paths": ["(a)(2)(i)"], "status": "attributed"})
        self.assertEqual(got["2026-04373:reg:9"], {"paths": [P7], "status": "attributed"})
        self.assertEqual(got["2026-04373:reg:4"]["paths"], ["(d)(2)"])

    def test_across_all_acquired_federal_register_documents_only_the_listed_instructions_change(self):
        def named():
            out = {}
            for key, d in self.docs.items():
                if d.get("publisher") != "Federal Register":
                    continue
                for u in self.amendments(key):
                    if u.get("instruction"):
                        out[u["unit_id"]] = fedreg.named_paragraph_paths(u["instruction"])
            return out
        new = named()
        with mock.patch.object(fedreg, "PARA_LOCATION", re.compile(r"(?!x)x")):
            old = named()
        # S7-P1-74 added the Tranche C Treasury documents; in FR 2026-19973 (Cuban Assets Control Regulations, 31 CFR 515) three
        # "In § 515.x, in paragraph (p), add ..." instructions are attributed by the clause rule as intended: reg:4 -> (c),
        # reg:7 -> (a)(5), reg:17 -> (d). S7-P1-75 (the 16 completion-run documents) added no changed unit.
        self.assertEqual(sorted(k for k in new if new[k] != old[k]),
                         ["2026-04373:reg:6", "2026-04373:reg:9", "2026-19973:reg:17", "2026-19973:reg:4", "2026-19973:reg:7"])
        self.assertEqual((new["2026-19973:reg:4"], new["2026-19973:reg:7"], new["2026-19973:reg:17"]), (["(c)"], ["(a)(5)"], ["(d)"]))
        self.assertNotEqual(old["2026-04373:reg:6"], new["2026-04373:reg:6"])  # S7-P1-76: the PARA_LOCATION mock took effect


class EvaluatorTests(unittest.TestCase):
    def test_group_is_registered_in_the_aggregate(self):
        self.assertIn("lowercase_paragraph_clause", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["lowercase_paragraph_clause"]], ["LC01", "LC02", "LC03"])
        with tempfile.TemporaryDirectory() as tmp:
            ix = build_index(tmp)
            for x in reg["lowercase_paragraph_clause"]:
                self.assertTrue(evaluate.check_lowercase_paragraph_clause(ix, x)["passed"], x["id"])
            positive = next(x for x in reg["lowercase_paragraph_clause"] if x["kind"] == "positive")
            with mock.patch.object(fedreg, "PARA_LOCATION", re.compile(r"(?!x)x")):  # the starting grammar
                self.assertFalse(evaluate.check_lowercase_paragraph_clause(ix, positive)["passed"])
            ix.conn.close()


if __name__ == "__main__":
    unittest.main()
