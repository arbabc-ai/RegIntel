"""Tests for scripts/p1_definition_relative_paths_83.py (S7-P1-83, C3 F-82-1): stored paragraph paths are classified by their first
designator, non-letter-led paths are listed with their composed citation observed through the existing resolver, answer and delta paths, and
the index is left byte-identical. Expected values are literals. Offline; task-77 synthetic fixture plus one in-test document. Governing
scope: founder record 2026-11-04_s7-regintel-p1-founder-decisions_accepted.md."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_p1_triple_letter_paragraph_list as tl
from scripts import p1_definition_relative_paths_83 as paths

DEF_KEY = "fr:2026-97101"
TWO_KEY = "fr:2026-97102"
TWO_XML = ("<RULE><PREAMB><CFR>12 CFR Part 9</CFR></PREAMB><SUPLINF><REGTEXT TITLE=\"12\" PART=\"9\"><AMDPAR>1. In § 9.3: a. Revise "
           "paragraph (2) of the definition of “Alpha”; b. Revise paragraph (2) of the definition of “Beta”.</AMDPAR></REGTEXT></SUPLINF></RULE>").encode()
def def_xml(section, body):
    return (f"<RULE><PREAMB><CFR>12 CFR Part 9</CFR></PREAMB><SUPLINF><REGTEXT TITLE=\"12\" PART=\"9\"><AMDPAR>1. In § {section}: {body}"
            "</AMDPAR></REGTEXT></SUPLINF></RULE>").encode()


# S7-P1-85 in-test documents (C3 F-84-1..F-84-3)
TOKEN_KEY, PARENT_KEY = "fr:2026-97105", "fr:2026-97106"
TOKEN_XML = def_xml("9.6", "t. Revise paragraph (4)(i)(A) in the definition of “Alpha”; u. Revise paragraphs (3) and (4) of the definition "
                           "for “Beta”.")
PARENT_XML = def_xml("9.7", "ee. In the definition of “Gamma”: i. Remove paragraph (2); ii. Redesignate paragraphs (1)(i) and (1)(ii) as "
                            "paragraphs (1) and (2).")
MERGED = ("1. In § 9.4: i. Revise footnote 3 to paragraph (2) of the definition for “Cleared transaction.” j. Revise the definition for "
          "“Commitment”.")
SPLIT = ("1. In § 9.5: i. Revise footnote 3 to paragraph (2) of the definition for “Cleared transaction.”; j. Revise the definition for "
         "“Commitment”.")
DEF_XML = ("<RULE><PREAMB><CFR>12 CFR Part 9</CFR></PREAMB><SUPLINF><REGTEXT TITLE=\"12\" PART=\"9\"><AMDPAR>1. In § 9.2: a. Revise the "
           "definition of “Term”; b. Revise paragraph (3) of the definition of “Term”.</AMDPAR></REGTEXT></SUPLINF></RULE>").encode()


class FixtureOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = tl.build_index(cls.tmp.name)
        cls.root = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def test_letter_led_paths_classify_as_letter_and_nothing_is_listed(self):
        totals, listed, _ = paths.classify(paths.units(self.ix.conn))
        self.assertEqual(listed, [])
        self.assertEqual(totals["paths"], {"letter": 5, "digit": 0, "roman": 0, "other_or_ambiguous": 0})  # (e) (r) (nnn) (ooo) (xxx)
        self.assertEqual(paths.path_class("(nnn)"), ("letter", ["letter"]))
        self.assertEqual(paths.path_class("(3)"), ("digit", ["digit"]))
        self.assertEqual(paths.path_class("(ii)"), ("roman", ["roman"]))
        self.assertEqual(paths.path_class("(i)(2)"), ("other_or_ambiguous", ["letter", "roman"]))

    def test_the_cli_leaves_the_index_byte_identical_and_writes_schema_and_labels(self):
        db, out = self.root / "i.sqlite", self.root / "paths.json"
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        with mock.patch("builtins.print"):
            self.assertEqual(paths.main(["--index", str(db), "--out", str(out)]), 0)
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)
        report = json.loads(out.read_text())
        self.assertEqual(report["schema"], "p1-definition-relative-paths-0.4")  # S7-P1-86 schema bump
        self.assertEqual(report["decision_record"], "2026-11-04_s7-regintel-p1-founder-decisions_accepted.md")
        self.assertEqual((report["legal_validity"], report["applicability_status"]), ("not_determined", "not_assessed"))
        self.assertEqual(report["status"], "proposal; producer-reported; no gate approved; nothing authorised")
        self.assertEqual((report["index"]["sha256"], report["index"]["unchanged_after_run"]), (before, True))


class DefinitionRelativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with mock.patch.dict(tl.DOCS, {DEF_KEY: DEF_XML, TWO_KEY: TWO_XML, TOKEN_KEY: TOKEN_XML, PARENT_KEY: PARENT_XML}):
            cls.ix = tl.build_index(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def test_a_definition_relative_digit_path_is_listed_and_its_citation_observed(self):
        _, listed, _ = paths.classify(paths.units(self.ix.conn))
        listed = [u for u in listed if u["doc_key"] == DEF_KEY]
        self.assertEqual([(u["doc_key"], u["section_no"], u["paths"]) for u in listed], [(DEF_KEY, "9.2", ["(3)"])])
        self.assertEqual(listed[0]["path_classes"], [{"path": "(3)", "class": "digit", "first_designator_classes": ["digit"]}])
        [obs] = paths.observe(self.ix, listed)
        # S7-P1-84 (F-83-1): the term the instruction names for the path, and its term-anchored baseline locator
        self.assertEqual(obs["definition_anchor"]["terms"], ["Term"])
        self.assertIs(obs["definition_anchor"]["ambiguous"], False)
        keys = ("term", "term_anchored_locator", "term_anchored_citation", "baseline_status",  # the 0.2 literal
                "term_raw", "inherited_from_parent",  # 0.3 keys (S7-P1-85)
                "term_phrase_form", "role")  # 0.4 keys (S7-P1-86)
        self.assertEqual([{k: a[k] for k in keys} for a in obs["definition_anchor"]["anchors"]],
                         [{"term": "Term", "term_anchored_locator": "9.2(Term)(3)", "term_anchored_citation": "12 CFR 9.2 \u201cTerm\u201d (3)",
                           "baseline_status": "baseline part not in index", "term_raw": "Term", "inherited_from_parent": False,
                           "term_phrase_form": "singular", "role": "operand"}])
        self.assertEqual({k: obs["definition_anchor"][k] for k in ("path_role", "term_phrase_form", "sibling_blocked")},
                         {"path_role": "operand", "term_phrase_form": "singular", "sibling_blocked": []})
        self.assertEqual(obs["citation"], "12 CFR 9.2(3)")
        self.assertEqual(obs["baseline"], {"baseline_doc_key": "ecfr:12:9", "status": "baseline part not in index"})
        self.assertEqual((obs["resolve_future"]["resolved"], obs["resolve_future"]["achieved"]), (True, "paragraph"))
        self.assertEqual(obs["resolve_future"]["future_paragraph_support"], [DEF_KEY])
        self.assertEqual(obs["resolve_current"]["resolved"], False)
        self.assertEqual(obs["answer_exact_current"]["status"], "missing_evidence")
        self.assertEqual([(g["doc_key"], g["role"]) for g in obs["delta_instruments"]], [(DEF_KEY, "paragraph_supported")])


    def test_two_terms_for_one_path_are_both_recorded_and_ambiguous(self):
        _, listed, _ = paths.classify(paths.units(self.ix.conn))
        listed = [u for u in listed if u["doc_key"] == TWO_KEY]
        self.assertEqual([(u["section_no"], u["paths"]) for u in listed], [("9.3", ["(2)"])])
        [obs] = paths.observe(self.ix, listed)
        self.assertEqual(obs["citation"], "12 CFR 9.3(2)")
        self.assertEqual(obs["definition_anchor"]["terms"], ["Alpha", "Beta"])
        self.assertIs(obs["definition_anchor"]["ambiguous"], True)
        self.assertEqual([a["term_anchored_locator"] for a in obs["definition_anchor"]["anchors"]], ["9.3(Alpha)(2)", "9.3(Beta)(2)"])

    def observed(self, key):
        _, listed, _ = paths.classify(paths.units(self.ix.conn))
        return {o["path"]: o["definition_anchor"] for o in paths.observe(self.ix, [u for u in listed if u["doc_key"] == key])}

    def test_a_trailing_period_is_stripped_and_the_raw_capture_kept(self):
        # S7-P1-85 (F-84-1). The parser stores no path for the footnote instruction, so definition_anchor is called directly.
        merged = paths.definition_anchor(self.ix.conn, "12", "9", "9.4", "(2)", MERGED)
        # fedreg.ENUM_BOUNDARY does not split after `.”`, so items i. and j. stay one item and “Commitment” stays attached (literal)
        self.assertEqual(paths.items(MERGED), ["1. In § 9.4:", " i. Revise footnote 3 to paragraph (2) of the definition for "
                                               "“Cleared transaction.” j. Revise the definition for “Commitment”."])
        self.assertEqual(merged["terms"], ["Cleared transaction", "Commitment"])
        self.assertEqual([(a["term"], a["term_raw"]) for a in merged["anchors"]],
                         [("Cleared transaction", "Cleared transaction."), ("Commitment", "Commitment")])
        self.assertEqual(merged["anchors"][0]["term_anchored_locator"], "9.4(Cleared transaction)(2)")
        self.assertEqual(merged["anchors"][0]["term_anchored_citation"], "12 CFR 9.4 \u201cCleared transaction\u201d (2)")
        self.assertIs(merged["ambiguous"], True)
        # when the items are split, “Commitment” (whose item has no (2)) is not attached
        split = paths.definition_anchor(self.ix.conn, "12", "9", "9.5", "(2)", SPLIT)
        self.assertEqual(split["terms"], ["Cleared transaction"])
        self.assertEqual(split["anchors"][0]["term_raw"], "Cleared transaction.")
        self.assertIs(split["ambiguous"], False)
        self.assertEqual(split["containment"], "token")

    def test_a_path_is_contained_only_as_a_whole_token(self):
        # S7-P1-85 (F-84-2)
        obs = self.observed(TOKEN_KEY)
        self.assertEqual(sorted(obs), ["(3)", "(4)(i)(A)"])  # the parser stores no (4) for this instruction
        self.assertEqual(obs["(4)(i)(A)"]["terms"], ["Alpha"])
        self.assertEqual(obs["(3)"]["terms"], ["Beta"])
        instruction = self.ix.conn.execute("SELECT instruction FROM provision WHERE instruction LIKE '1. In § 9.6:%'").fetchone()[0]
        four = paths.definition_anchor(self.ix.conn, "12", "9", "9.6", "(4)", instruction)
        self.assertEqual(four["terms"], ["Beta"])
        self.assertIs(four["ambiguous"], False)
        self.assertEqual(four["containment"], "token")
        self.assertIs(paths.contains("revise paragraph (4)(i)(A)", "(4)"), False)
        self.assertIs(paths.contains("revise paragraph (4)(i)(A)", "(4)(i)(A)"), True)
        self.assertIs(paths.contains("revise paragraph (1)(i)", "(i)"), False)

    def test_a_nested_item_inherits_the_single_term_of_its_parent(self):
        # S7-P1-85 (F-84-3)
        obs = self.observed(PARENT_KEY)
        self.assertEqual(sorted(obs), ["(1)(i)", "(1)(ii)", "(2)"])
        two = obs["(2)"]
        self.assertEqual(two["terms"], ["Gamma"])
        self.assertIs(two["ambiguous"], False)
        self.assertEqual([(a["term_anchored_locator"], a["inherited_from_parent"], a["parent_item_head"]) for a in two["anchors"]],
                         [("9.7(Gamma)(2)", True, "ee. In the definition of “Gamma”:")])
        self.assertNotIn("parent_status", two)
        direct = self.observed(DEF_KEY)["(3)"]
        self.assertEqual([a["inherited_from_parent"] for a in direct["anchors"]], [False])

    def test_no_single_parent_term_and_no_containing_item(self):
        two_terms = "1. In § 9.8: a. In the definition of “Delta” and the definition of “Epsilon”: i. Remove paragraph (2)."
        self.assertEqual(paths.definition_anchor(self.ix.conn, "12", "9", "9.8", "(2)", two_terms)["parent_status"], "no_single_parent_term")
        self.assertEqual(paths.definition_anchor(self.ix.conn, "12", "9", "9.8", "(2)", two_terms)["terms"], [])
        none = paths.definition_anchor(self.ix.conn, "12", "9", "9.8", "(5)", two_terms)
        self.assertEqual((none["terms"], none["parent_status"]), ([], "no_item_contains_path"))

    def test_plural_definitions_capture_every_quoted_term(self):
        # S7-P1-86 (F-85-2)
        a = paths.definition_anchor(self.ix.conn, "12", "9", "9.9", "(2)",
                                    "1. In § 9.9: a. Revise paragraph (2) of the definitions of “A” and “B”.")
        self.assertEqual(a["terms"], ["A", "B"])
        self.assertEqual([(x["term"], x["term_phrase_form"]) for x in a["anchors"]], [("A", "plural"), ("B", "plural")])
        self.assertEqual((a["term_phrase_form"], a["ambiguous"]), ("plural", True))
        self.assertEqual(paths.capture_terms("the definitions of “A”, “B”, and “C”; the definition for “D” or “E”"),
                         [("A", "plural"), ("B", "plural"), ("C", "plural"), ("D", "singular"), ("E", "singular")])
        mixed = paths.definition_anchor(self.ix.conn, "12", "9", "9.9", "(2)",
                                        "1. In § 9.9: a. Revise paragraph (2) of the definitions of “A”, “B” and the definition of “C”.")
        self.assertEqual((mixed["terms"], mixed["term_phrase_form"]), (["A", "B", "C"], "mixed"))

    def test_the_sibling_guard_stops_inheritance_at_an_item_of_the_same_class(self):
        # S7-P1-86 (F-85-1)
        text = "1. In § 9.7: ee. In the definition of “Gamma”: i. Remove paragraph (2); ii. Revise paragraph (4); ff. Revise paragraph (3)."
        anchor = lambda p: paths.definition_anchor(self.ix.conn, "12", "9", "9.7", p, text)
        three = anchor("(3)")
        self.assertEqual((three["terms"], three["parent_status"]), ([], "sibling_boundary"))
        self.assertEqual(three["sibling_blocked"],
                         [{"item_index": 4, "item_enumerator": "ff", "stopped_at_index": 1, "stopped_at_enumerator": "ee"}])
        for path, enumerator in (("(2)", "i"), ("(4)", "ii")):
            a = anchor(path)
            self.assertEqual(a["terms"], ["Gamma"])
            self.assertEqual([(x["inherited_from_parent"], x["item_enumerator"], x["parent_enumerator"], x["parent_item_index"])
                              for x in a["anchors"]], [(True, enumerator, "ee", 1)])
            self.assertNotIn("parent_status", a)
        # measured limit: designator_classes gives "ii" and "iii" the same class {roman}, so "iii." stops at "ii."
        limit = paths.definition_anchor(self.ix.conn, "12", "9", "9.7", "(5)", "1. In § 9.7: ee. In the definition of “Gamma”: "
                                        "i. Remove paragraph (2); ii. Revise paragraph (4); iii. Revise paragraph (5).")
        self.assertEqual((limit["terms"], limit["parent_status"], limit["sibling_blocked"]),
                         ([], "sibling_boundary", [{"item_index": 4, "item_enumerator": "iii", "stopped_at_index": 3,
                                                    "stopped_at_enumerator": "ii"}]))

    def test_redesignation_source_and_target_roles(self):
        # S7-P1-86 (F-85-3)
        text = "1. In § 9.8: a. In the definition of “Delta”: i. Redesignate paragraph (4) as paragraph (3)."
        four = paths.definition_anchor(self.ix.conn, "12", "9", "9.8", "(4)", text)
        three = paths.definition_anchor(self.ix.conn, "12", "9", "9.8", "(3)", text)
        self.assertEqual([(x["term"], x["role"]) for x in four["anchors"]], [("Delta", "redesignation_source")])
        self.assertNotIn("present_refers_to", four["anchors"][0])
        self.assertEqual([(x["term"], x["role"], x["present_refers_to"]) for x in three["anchors"]],
                         [("Delta", "redesignation_target", "current_baseline_text")])
        self.assertEqual((four["path_role"], three["path_role"]), ("redesignation_source", "redesignation_target"))
        self.assertEqual(paths.item_role("ii. Redesignate paragraphs (1)(i) and (1)(ii) as paragraphs (1) and (2);", "(2)"),
                         "redesignation_target")
        self.assertEqual(paths.item_role("ii. Redesignate paragraphs (1)(i) and (1)(ii) as paragraphs (1) and (2); and iii. In paragraph (2)",
                                         "(2)"), "multiple")  # one item (no boundary before "and iii."): target and operand
        self.assertEqual(paths.item_role("i. Remove paragraph (2); ii. Redesignate paragraph (1) as paragraph (2).", "(2)"), "multiple")

    def test_terms_and_containment_read_the_same_parsing_view(self):
        # S7-P1-86 (F-85-4): whitespace inside a designator is removed by fedreg.parsing_view for both the path and the term
        a = paths.definition_anchor(self.ix.conn, "12", "9", "9.9", "(3)", "1. In § 9.9: a. Revise paragraph ( 3 ) of the definition of “Rate ( a )”.")
        self.assertEqual([(x["term"], x["term_raw"]) for x in a["anchors"]], [("Rate (a)", "Rate (a)")])

    def test_enum_boundary_punctuation_is_one_character(self):
        # S7-P1-86 (F-85-5): items() cuts at b.start(1) + 1, which equals b.end(1) only while group 1 is one character
        spans = []
        for text in ("x; a. y", "x: i. y", "x. 2. y", "x: (ii) y", "x; (A) y"):
            b = paths.fedreg.ENUM_BOUNDARY.search(text)
            spans.append((text, b.group(1), b.end(1) - b.start(1), b.start(1) + 1 == b.end(1), paths.fedreg._boundary_token(b)))
        self.assertEqual(spans, [("x; a. y", ";", 1, True, "a"), ("x: i. y", ":", 1, True, "i"), ("x. 2. y", ".", 1, True, "2"),
                                 ("x: (ii) y", ":", 1, True, "ii"), ("x; (A) y", ";", 1, True, "A")])


class HeldBaselineTests(unittest.TestCase):
    """S7-P1-85 (F-84-5). The task-77 builder cannot carry an eCFR document other than ecfr:31:1010
    (tests/test_p1_triple_letter_paragraph_list.py:86 hard-codes it; every tl.DOCS entry is built as a Federal Register document), so the
    held-baseline, LIKE-fallback and census paths are pinned on an in-memory sqlite3 fixture with the columns the script queries."""

    LOCATORS = [("9.2", "12 CFR 9.2"), ("9.2(a)", "12 CFR 9.2(a)"), ("9.2(Term)", "12 CFR 9.2 \u201cTerm\u201d"),
                ("9.2(Term)(3)", "12 CFR 9.2 \u201cTerm\u201d (3)"), ("9.2(Other term (OT))", "12 CFR 9.2 \u201cOther term (OT)\u201d"),
                ("9.2(Other term (OT))(1)", "12 CFR 9.2 \u201cOther term (OT)\u201d (1)"), ("9.2(Rate 5x)(1)", "12 CFR 9.2 \u201cRate 5x\u201d (1)"),
                ("9.2(4)", "12 CFR 9.2(4)")]

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""CREATE TABLE source_document (id INTEGER PRIMARY KEY, doc_key TEXT);
                                   CREATE TABLE provision (id INTEGER PRIMARY KEY, doc_id INTEGER, locator TEXT, citation TEXT,
                                                           section_no TEXT, ordinal INTEGER);
                                   INSERT INTO source_document VALUES (1, 'ecfr:12:9');""")
        self.conn.executemany("INSERT INTO provision (doc_id, locator, citation, section_no, ordinal) VALUES (1, ?, ?, '9.2', ?)",
                              [(l, c, i) for i, (l, c) in enumerate(self.LOCATORS)])

    def tearDown(self):
        self.conn.close()

    def test_a_held_term_anchored_locator_is_present(self):
        a = paths.definition_anchor(self.conn, "12", "9", "9.2", "(3)", "1. In § 9.2: a. Revise paragraph (3) of the definition of “Term”.")
        self.assertIs(a["baseline_in_index"], True)
        self.assertEqual([(x["term_anchored_present"], x["baseline_locator"], x["baseline_citation"], x["baseline_status"]) for x in a["anchors"]],
                         [(True, "9.2(Term)(3)", "12 CFR 9.2 \u201cTerm\u201d (3)", "queried")])
        self.assertNotIn("like_matches", a["anchors"][0])

    def test_an_absent_locator_falls_back_to_like_and_escapes_percent(self):
        a = paths.definition_anchor(self.conn, "12", "9", "9.2", "(4)", "1. In § 9.2: a. Revise paragraph (4) of the definition of “Term”.")
        self.assertEqual([(x["term_anchored_present"], x["like_match_count"]) for x in a["anchors"]], [(False, 2)])
        self.assertEqual([m["locator"] for m in a["anchors"][0]["like_matches"]], ["9.2(Term)", "9.2(Term)(3)"])
        pct = paths.definition_anchor(self.conn, "12", "9", "9.2", "(1)", "1. In § 9.2: a. Revise paragraph (1) of the definition of “Rate 5%”.")
        self.assertEqual(pct["anchors"][0]["like_match_count"], 0)  # unescaped, `5%` would match 9.2(Rate 5x)(1)

    def test_a_held_redesignation_target_says_what_present_refers_to(self):
        a = paths.definition_anchor(self.conn, "12", "9", "9.2", "(3)",
                                    "1. In § 9.2: a. In the definition of “Term”: i. Redesignate paragraph (4) as paragraph (3).")
        self.assertEqual([(x["role"], x["term_anchored_present"], x["present_refers_to"], x["baseline_locator"]) for x in a["anchors"]],
                         [("redesignation_target", True, "current_baseline_text", "9.2(Term)(3)")])

    def test_census_keeps_the_old_counts_and_adds_the_balanced_count(self):
        self.assertEqual(paths.term_anchor_census(self.conn, {"ecfr:12:9", "ecfr:12:99"}), {
            "ecfr:12:9": {"in_index": True, "provisions": 8, "regex_matches": 4, "term_segment_matches": 3,
                          "term_segment_examples": ["9.2(Term)", "9.2(Term)(3)", "9.2(Rate 5x)(1)"],
                          "balanced_term_segment_matches": 5,
                          "balanced_examples": ["9.2(Term)", "9.2(Term)(3)", "9.2(Other term (OT))", "9.2(Other term (OT))(1)",
                                                "9.2(Rate 5x)(1)"]},
            "ecfr:12:99": {"in_index": False}})
        self.assertEqual(paths.first_balanced_segment("3.2(Adjusted allowances for credit losses (AACL))(1)"),
                         "Adjusted allowances for credit losses (AACL)")
        self.assertIsNone(paths.first_balanced_segment("3.2(Unclosed (x)"))


if __name__ == "__main__":
    unittest.main()
