"""Tests for S7-P1-77 (F-76-1): an added-paragraph list of tripled letters ("Adding paragraphs (nnn), (ooo), ... and (xxx)") names every
member. Before the fix `designator_classes` gave a tripled letter no hierarchy class, so `expand_shorthand` could not settle the second
member and the rest of the list was dropped. Quoted replacement text, cross-references and malformed designators still name nothing, and a
nested `(aaa)(1)` stays one path. Paragraph attribution is textual evidence only. Governing scope: founder record
2026-11-04_s7-regintel-p1-founder-decisions_accepted.md."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import build, evaluate, fedreg, search
from test_p1 import asset

CORPUS = Path("/home/arbab/projects/regintel-corpus/phase1")
REGRESSIONS = Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json"
KEY, QUOTED, XREF = "fr:2026-97001", "fr:2026-97002", "fr:2026-97003"
TRIPLE = ["(nnn)", "(ooo)", "(ppp)", "(qqq)", "(rrr)", "(sss)", "(ttt)", "(uuu)", "(vvv)", "(www)", "(xxx)"]
I_06963 = ("3. Revising § 1010.100 by: a. Revising and republishing paragraph (t)(9) and (t)(10); b. Adding paragraph (t)(11); c. Revising "
           "and republishing paragraph (ff)(8)(ii) and (ff)(8)(iii); d. Adding paragraph (ff)(8)(iv); e. Revising and republishing paragraph "
           "(bbb)(1); f. Revising and republishing paragraph (eee); and g. Adding paragraphs (nnn), (ooo), (ppp), (qqq), (rrr), (sss), (ttt), "
           "(uuu), (vvv), (www), and (xxx). The revisions, republications, and additions read as follows:")
I_07033 = ("2. Amend § 1010.100 by: a. Revising paragraphs (e) and (r); and b. Adding paragraphs (nnn) and (ooo). The revisions and additions "
           "read as follows:")
PREVIOUS_06963 = ["(t)(9)", "(t)(10)", "(t)(11)", "(ff)(8)(ii)", "(ff)(8)(iii)", "(ff)(8)(iv)", "(bbb)(1)", "(eee)", "(nnn)"]
PATHS_06963 = PREVIOUS_06963 + TRIPLE[1:]
PATHS_07033 = ["(e)", "(r)", "(nnn)", "(ooo)"]
POSITIVE = [
    (I_06963, PATHS_06963),
    (I_07033, PATHS_07033),
    ("Amend § 1010.100 by adding paragraphs (aaa) and (bbb).", ["(aaa)", "(bbb)"]),
    ("Amend § 1010.100 by revising paragraphs (e) and (r) and adding paragraphs (nnn) and (ooo).", PATHS_07033),  # mixed list
    ("Amend § 1010.100 by adding paragraph (aaa)(1).", ["(aaa)(1)"]),                                           # nested: one path
]
NEGATIVE = [
    "In § 1010.100, revise the text to read “Adding paragraphs (nnn) and (ooo).”",            # quoted replacement text
    "Amend § 1010.100 by revising the definition as provided in paragraphs (nnn) and (ooo).",  # cross-reference
    "Amend § 1010.100 by adding paragraph (nnn",                                               # malformed designator
    "Amend § 1010.100 by adding paragraphs (nnn)(ooo",                                         # malformed nested suffix
]


def pre_fix_designator_classes(designator):
    """`designator_classes` as it stood at 3e3fe6f: a lowercase letter only single or doubled."""
    d = designator.strip("()")
    classes = set()
    if d.isdigit():
        classes.add("digit")
    if d in fedreg.ROMAN_LOWER:
        classes.add("roman")
    if d.islower() and (len(d) == 1 or (len(set(d)) == 1 and len(d) == 2 and d not in fedreg.ROMAN_LOWER)) and \
            d not in fedreg.ROMAN_LOWER - {"i", "v", "x"}:
        classes.add("letter")
    if d.isupper() and (len(d) == 1 or len(set(d)) == 1 and len(d) == 2):
        classes.add("upper")
    return classes


def pre_fix():
    return mock.patch.object(fedreg, "designator_classes", pre_fix_designator_classes)


def ecfr_1010():
    paths = ("(e)", "(r)", "(nnn)", "(ooo)", "(xxx)")
    paras = "".join(f'<div id="p-1010.100{p}"><p class="indent-1" data-title="1010.100{p}">{p} Text.</p></div>' for p in paths)
    return (f'<div class="part" id="part-1010"><h1>PART 1010—GENERAL PROVISIONS</h1><div class="section" id="1010.100">'
            f'<h4>§ 1010.100 General definitions.</h4>{paras}</div></div>')


def fr_xml(*instructions):
    body = "".join(f"<REGTEXT TITLE=\"31\" PART=\"1010\"><AMDPAR>{i}</AMDPAR></REGTEXT>" for i in instructions)
    return f"<PRORULE><PREAMB><CFR>31 CFR Part 1010</CFR></PREAMB><SUPLINF>{body}</SUPLINF></PRORULE>".encode()


DOCS = {
    KEY: fr_xml("1. Amend § 1010.100 by: a. Revising paragraphs (e) and (r); and b. Adding paragraphs (nnn), (ooo), and (xxx)."),
    QUOTED: fr_xml("1. In § 1010.100, revise the text to read “Adding paragraphs (ooo) and (xxx).”"),
    XREF: fr_xml("1. Amend § 1010.100 by revising the definition as provided in paragraphs (ooo) and (xxx)."),
}


def build_index(tmp):
    root = Path(tmp) / "corpus"
    (root / "raw").mkdir(parents=True)
    docs = {"ecfr:31:1010": {"source_class": "regulation", "publisher": "eCFR", "citation": "31 CFR Part 1010", "title": "General",
                             "publisher_issue_date": "2026-10-15", "publisher_up_to_date_as_of": "2026-10-28",
                             "asset": asset(root, "e1010.html", ecfr_1010().encode())}}
    for key, xml in DOCS.items():
        docs[key] = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-04-10",
                     "document_type": "Proposed Rule", "comments_close_on": "2026-06-09", "action": "Proposed rule.",
                     "document_number": key.split(":")[1], "title": "t", "asset": asset(root, key.replace(":", "_") + ".xml", xml)}
    (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
    db = str(Path(tmp) / "i.sqlite")
    conn, _ = build.build(root, db, "2026-10-29", log=lambda *_: None)
    conn.close()
    return search.Index(db)


class GrammarTests(unittest.TestCase):
    def test_every_member_of_a_triple_letter_list_is_named(self):
        for text, expected in POSITIVE:
            self.assertEqual(fedreg.named_paragraph_paths(text), expected, text)

    def test_quoted_text_cross_references_and_malformed_designators_name_nothing(self):
        for text in NEGATIVE:
            self.assertEqual(fedreg.named_paragraph_paths(text), [], text)

    def test_a_malformed_second_member_is_never_named(self):
        # The complete first member stays named, exactly as a single-letter list `(e), (r` names (e) (existing rule, unchanged).
        self.assertEqual(fedreg.named_paragraph_paths("Amend § 1010.100 by adding paragraphs (nnn), (ooo"), ["(nnn)"])
        self.assertEqual(fedreg.named_paragraph_paths("Amend § 1010.100 by adding paragraphs (e), (r"), ["(e)"])

    def test_tripled_letters_are_letters_and_roman_numerals_keep_their_class(self):
        self.assertEqual(fedreg.designator_classes("(ooo)"), {"letter"})
        self.assertEqual(fedreg.designator_classes("(xxx)"), {"letter"})
        self.assertEqual(fedreg.designator_classes("(iii)"), {"roman"})  # listed roman numeral: unchanged
        self.assertEqual(fedreg.designator_classes("(abc)"), set())      # not a repeated letter
        self.assertEqual(fedreg.designator_classes("(nnnn)"), set())     # beyond tripled letters: unchanged

    def test_the_pre_fix_grammar_dropped_the_list_after_its_first_triple_letter_member(self):
        with pre_fix():
            self.assertEqual(fedreg.named_paragraph_paths(I_06963), PREVIOUS_06963)
            self.assertEqual(fedreg.named_paragraph_paths(I_07033), ["(e)", "(r)", "(nnn)"])
        self.assertEqual(fedreg.named_paragraph_paths(I_06963), PATHS_06963)


class SyntheticDeltaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def instruments(self, citation):
        return {g["doc_key"]: g for g in self.ix.delta(citation)["instruments"]}

    def test_later_members_resolve_from_the_named_instrument_only(self):
        for path in ("(ooo)", "(xxx)"):
            inst = self.instruments("31 CFR 1010.100" + path)
            p = inst[KEY]["paragraph_precision"]
            self.assertEqual((p["resolved"], p["achieved"], p["requested_path"]), (True, "paragraph", path), path)
            for other in (QUOTED, XREF):
                q = inst[other]["paragraph_precision"]
                self.assertEqual((q["resolved"], q["achieved"], q["reason"]), (False, "section_only", "paragraph_precision_unresolved"),
                                 (path, other))
            d = self.ix.delta("31 CFR 1010.100" + path)
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))

    def test_stored_paths_list_every_member(self):
        rows = [json.loads(r[0]) for r in self.ix.conn.execute(
            "SELECT p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment'",
            (KEY,))]
        self.assertEqual({(r["status"], tuple(r["paths"])) for r in rows}, {("attributed", ("(e)", "(r)", "(nnn)", "(ooo)", "(xxx)"))})


@unittest.skipUnless((CORPUS / "manifest.json").exists(), "public corpus not available")
class AcquiredSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = json.loads((CORPUS / "manifest.json").read_text())["documents"]

    def units(self, key):
        a = self.docs[key]["asset"]
        raw = (CORPUS / a["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), a["sha256"])  # the raw bytes are the pinned ones
        _provs, units, _stats = fedreg.parse_fr(raw, key.split(":")[1])
        return [u for u in units if u["section_no"] == "1010.100" and u.get("instruction")]

    def test_the_acquired_instructions_list_every_added_path(self):
        for key, previous, expected in (("fr:2026-06963", PREVIOUS_06963, PATHS_06963),
                                        ("fr:2026-07033", ["(e)", "(r)", "(nnn)"], PATHS_07033)):
            units = self.units(key)
            self.assertTrue(units, key)
            for u in units:
                self.assertEqual(u["explicit_paragraphs"], {"paths": expected, "status": "attributed"}, key)
            with pre_fix():
                for u in self.units(key):
                    self.assertEqual(u["explicit_paragraphs"]["paths"], previous, key)
        self.assertEqual(len(PATHS_06963), 19)  # the nine earlier paths (ending (nnn)) plus (ooo) ... (xxx)
        self.assertTrue(set(TRIPLE) <= set(PATHS_06963))


class EvaluatorTests(unittest.TestCase):
    def test_group_is_registered_and_passes_here_but_not_under_the_pre_fix_grammar(self):
        self.assertIn("triple_letter_paragraph_list", evaluate.REGRESSION_GROUPS)
        reg = json.loads(REGRESSIONS.read_text())
        self.assertEqual([x["id"] for x in reg["triple_letter_paragraph_list"]], ["TL01", "TL02", "TL03"])
        with tempfile.TemporaryDirectory() as tmp:
            ix = build_index(tmp)
            for x in reg["triple_letter_paragraph_list"]:
                self.assertTrue(evaluate.check_triple_letter_paragraph_list(ix, x)["passed"], x["id"])
            positive = next(x for x in reg["triple_letter_paragraph_list"] if x["kind"] == "positive")
            with pre_fix():
                self.assertFalse(evaluate.check_triple_letter_paragraph_list(ix, positive)["passed"])
            ix.conn.close()


if __name__ == "__main__":
    unittest.main()
