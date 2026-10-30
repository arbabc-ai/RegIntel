"""Tests for S7-P1-DESIGNATOR-TOKEN-INTEGRITY-12 (F1): a designator is read whole even when whitespace surrounds
inline emphasis markup, and a valid prefix of an incomplete or ambiguous designator sequence is never accepted as a
named parent path. The stored instruction text is never altered."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.fedreg import named_paragraph_paths as paths, parse_fr, parsing_view
from test_p1 import asset

MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")
C1 = "(a)(5)(iii)(C)(1)"
I1X = "(a)(5)(v)(I)(1)(xiii)"
ANC_C, ANC_I = "(a)(5)(iii)(C)", "(a)(5)(v)(I)"

# The 2026-11044 instruction 4 shape: whitespace inside the last designators, from newlines/tabs around <E> markup.
INSTR_P = ("4. Amend &#xA7; 337.6 by: e. Revising paragraph (a)(3)(i); g. Revising paragraph (a)(5)(iii)(C)(\n    <E T=\"03\">1</E>\n  ); "
           "and h. In paragraph (a)(5)(v)(I)(\n <E T=\"03\">1</E>\n )(\t<E T=\"03\">xiii</E>\n), removing the text &#x201C;and&#x201D;; "
           "and i. In paragraph (d), removing the text &#x201C;paragraph (b)(2)(ii) of the section&#x201D;.")
INSTR_Q = "1. Amend &#xA7; 337.6 by revising paragraph (a)(5)(iii)(C) to read as follows:"
INSTR_R = "1. Amend &#xA7; 337.6 by revising paragraph (a)(5)(iii)(C)(\n<E T=\"03\">1</E> to read as follows:"          # unclosed
INSTR_S = "1. Amend &#xA7; 337.6 by revising paragraph (a)(5)(v)(I) (<E T=\"03\">1</E>)(<E T=\"03\">xiii</E>) to read as follows:"  # set apart


def doc(root, instruction):
    return (f'<{root}><PREAMB><CFR>12 CFR Part 337</CFR></PREAMB><SUPLINF><REGTEXT TITLE="12" PART="337"><AMDPAR>{instruction}</AMDPAR>'
            f'<SECTION><SECTNO>&#xA7; 337.6</SECTNO><SUBJECT>Brokered deposits.</SUBJECT><P>(a) Carried text.</P></SECTION></REGTEXT>'
            f'</SUPLINF></{root}>').encode()


ECFR_337 = ('<div class="part" id="part-337"><h1>PART 337</h1><div class="section" id="337.6"><h4>§ 337.6 Brokered deposits.</h4>'
            '<div id="p-337.6(a)"><p class="indent-1" data-title="337.6(a)">(a) General.</p>'
            '<div id="p-337.6(a)(5)"><p class="indent-2" data-title="337.6(a)(5)">(5) Items.</p>'
            '<div id="p-337.6(a)(5)(iii)"><p class="indent-3" data-title="337.6(a)(5)(iii)">(iii) Third.</p>'
            '<div id="p-337.6(a)(5)(iii)(C)"><p class="indent-4" data-title="337.6(a)(5)(iii)(C)">(C) Letter C.</p>'
            '<div id="p-337.6(a)(5)(iii)(C)(1)"><p class="indent-5" data-title="337.6(a)(5)(iii)(C)(1)">(1) Existing C1 text.</p>'
            '</div></div></div></div></div></div></div>')
KEYS = {"P": "fr:2026-90061", "Q": "fr:2026-90062", "R": "fr:2026-90063", "S": "fr:2026-90064"}


class DesignatorViewTests(unittest.TestCase):
    def test_parsing_view_only_closes_whitespace_inside_designators(self):
        self.assertEqual(parsing_view("(C)( 1 ) and ( xiii ), (\t5\n) (introductory text)"), "(C)(1) and (xiii), (5) (introductory text)")
        self.assertEqual(parsing_view("paragraph (a)(2) of this section"), "paragraph (a)(2) of this section")

    def test_literal_paths_for_the_11044_shape(self):
        text = ("4. Amend § 337.6 by: e. Revising paragraph (a)(3)(i); g. Revising paragraph (a)(5)(iii)(C)( 1 ); and h. In paragraph "
                "(a)(5)(v)(I)( 1 )( xiii ), removing the text “and”; and i. In paragraph (d), removing the text “paragraph (b)(2)(ii) of the section”.")
        got = paths(text)
        self.assertEqual(got, ["(a)(3)(i)", C1, I1X, "(d)"])
        for truncated in (ANC_C, ANC_I):
            self.assertNotIn(truncated, got)  # the valid prefixes of the complete paths are not named parents
        self.assertNotIn("(b)(2)(ii)", got)  # quoted

    def test_whitespace_variants_read_identically(self):
        want = [C1]
        for variant in ("Revise paragraph (a)(5)(iii)(C)(1).", "Revise paragraph (a)(5)(iii)(C)( 1 ).",
                        "Revise paragraph (a)(5)(iii)(C)(\n\t1\t\n).", "Revise paragraph (a)(5)(iii)(C)(  1).", "Revise paragraph (a)(5)(iii)(C)(1  )."):
            self.assertEqual(paths(variant), want, repr(variant))
        self.assertEqual(paths("Revise paragraphs ( e ), ( f ), and ( g )."), ["(e)", "(f)", "(g)"])
        self.assertEqual(paths("Revise paragraphs (c)( 5 ) through ( 7 )."), ["(c)(5)", "(c)(7)"])  # shorthand survives the view

    def test_malformed_suffixes_never_leave_a_valid_prefix_behind(self):
        for bad in ("Revise paragraph (a)(5)(iii)(C)(1.",                 # unclosed
                    "Revise paragraph (a)(5)(iii)(C)(1 and (b).",          # unclosed, more text
                    "Revise paragraph (a)(5)(iii)(C)(x y).",               # two tokens inside one designator
                    "Revise paragraph (a)(5)(iii)(C) (1).",                # set apart by whitespace: ambiguous
                    "Revise paragraph (a)(5)(v)(I) (1)(xiii)."):
            self.assertEqual(paths(bad), [], bad)
        # a malformed item ends the list; earlier complete paths in it stay, nothing after is trusted
        self.assertEqual(paths("Revise paragraphs (a)(1)(i), (a)(5)(iii)(C)(1, and (b)."), ["(a)(1)(i)"])
        self.assertEqual(paths("Revise paragraphs (a)(1)(i), (a)(5)(iii)(C) (1), and (b)."), ["(a)(1)(i)"])

    def test_a_parenthetical_that_is_not_a_designator_does_not_block_the_path(self):
        self.assertEqual(paths("Revise paragraph (a)(2) (introductory text)."), ["(a)(2)"])
        self.assertEqual(paths("Revise paragraph (a)(5)(iii)(C) (see below)."), ["(a)(5)(iii)(C)"])

    def test_quoted_and_unquoted_references_are_not_evidence(self):
        self.assertEqual(paths("Remove “paragraph (a)(5)(iii)(C)( 1 )” and revise paragraph (a)(1)."), ["(a)(1)"])
        self.assertEqual(paths("Revise § 337.6 as provided in paragraph (a)(5)(iii)(C)( 1 )."), [])

    def test_existing_shorthand_and_list_behaviour_is_unchanged(self):
        self.assertEqual(paths("2. In § 3.1: a. Revise paragraphs (c)(4)(i), (e) and (f); and b. Add paragraph (g)."), ["(c)(4)(i)", "(e)", "(f)", "(g)"])
        self.assertEqual(paths("Revise paragraphs (b)(1), (c), and (d)."), ["(b)(1)", "(c)", "(d)"])
        self.assertEqual(paths("Revise paragraphs (a)(1)(i) and (v)."), ["(a)(1)(i)"])


class StoredTextTests(unittest.TestCase):
    def test_stored_instruction_keeps_its_spaces_while_paths_are_normalized(self):
        unit = next(u for u in parse_fr(doc("RULE", INSTR_P), "2026-90061")[1] if u["section_no"] == "337.6")
        self.assertIn("(C)( 1 )", unit["instruction"])         # stored text is exactly what the source gave
        self.assertIn("(I)( 1 )( xiii )", unit["instruction"])
        self.assertEqual(unit["explicit_paragraphs"], {"paths": ["(a)(3)(i)", C1, I1X, "(d)"], "status": "attributed"})
        self.assertEqual(unit["explicit_targets"], ["337.6"])
        self.assertEqual(unit["pairing_status"], "explicit")

    def test_malformed_and_ancestor_instruments_attribute_what_they_actually_name(self):
        def stored(instr):
            return next(u for u in parse_fr(doc("RULE", instr), "2026-90099")[1] if u["section_no"] == "337.6")["explicit_paragraphs"]
        self.assertEqual(stored(INSTR_Q), {"paths": [ANC_C], "status": "attributed"})
        self.assertEqual(stored(INSTR_R)["paths"], [])
        self.assertEqual(stored(INSTR_S)["paths"], [])


class DesignatorIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        final = {**meta, "document_type": "Rule", "effective_on": "2027-02-01", "action": "Final rule."}
        docs = {
            "ecfr:12:337": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 337", "title": "Brokered",
                            "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                            "asset": asset(root, "e.html", ECFR_337.encode())},
            KEYS["P"]: {**final, "document_number": "2026-90061", "title": "P", "asset": asset(root, "p.xml", doc("RULE", INSTR_P))},
            KEYS["Q"]: {**meta, "document_number": "2026-90062", "title": "Q", "document_type": "Proposed Rule",
                        "comments_close_on": "2026-12-01", "asset": asset(root, "q.xml", doc("PRORULE", INSTR_Q))},
            KEYS["R"]: {**final, "document_number": "2026-90063", "title": "R", "asset": asset(root, "r.xml", doc("RULE", INSTR_R))},
            KEYS["S"]: {**final, "document_number": "2026-90064", "title": "S", "asset": asset(root, "s.xml", doc("RULE", INSTR_S))},
        }
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "d.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def supported(self, path):
        return sorted(self.index.delta(f"12 CFR 337.6{path}")["future_precision"]["supported_instruments"])

    def fut(self, path):
        return self.index.resolve_citations(f"12 CFR 337.6{path}", "future")[0]

    # ---- each instrument supports exactly what it names
    def test_each_instrument_supports_only_its_own_complete_paths(self):
        self.assertEqual(self.supported(C1), [KEYS["P"]])             # P names it whole; Q names only the ancestor
        self.assertEqual(self.supported(I1X), [KEYS["P"]])
        self.assertEqual(self.supported(ANC_C), [KEYS["Q"]])          # the truncated ancestor only where separately named
        self.assertEqual(self.supported(ANC_I), [])                   # nobody names it
        self.assertEqual(self.supported("(a)(3)(i)"), [KEYS["P"]])
        self.assertEqual(self.supported("(d)"), [KEYS["P"]])
        for key in ("R", "S"):  # malformed and set-apart sequences support nothing, not even a prefix
            for p in (C1, I1X, ANC_C, ANC_I):
                self.assertNotIn(KEYS[key], self.supported(p), (key, p))

    def test_ancestors_and_descendants_do_not_establish_each_other(self):
        d = self.index.delta(f"12 CFR 337.6{ANC_I}")
        self.assertFalse(d["future_precision"]["resolved"])
        self.assertEqual(d["future_precision"]["achieved"], "section_only")
        self.assertTrue(all(g["role"] == "section_context" for g in d["instruments"]))
        self.assertEqual({g["doc_key"] for g in d["instruments"]}, set(KEYS.values()))  # all four stay listed as context
        c = self.fut(ANC_I)
        self.assertEqual((c["resolved"], c["achieved"], c["reason"]), (False, "section_only", "paragraph_precision_unresolved"))
        self.assertEqual(c["future_paragraph_support"], [])
        roles = {g["doc_key"]: g["role"] for g in self.index.delta(f"12 CFR 337.6{C1}")["instruments"]}
        self.assertEqual(roles[KEYS["P"]], "paragraph_supported")
        self.assertEqual({roles[KEYS["Q"]], roles[KEYS["R"]], roles[KEYS["S"]]}, {"section_context"})

    def test_baseline_existence_is_separate_from_instrument_support_and_resolver_agrees(self):
        for p, baseline in ((C1, True), (ANC_C, True), (I1X, False), (ANC_I, False)):
            d = self.index.delta(f"12 CFR 337.6{p}")
            cur = self.index.resolve_citations(f"12 CFR 337.6{p}", "current")[0]
            fut = self.fut(p)
            self.assertEqual(d["current_precision"]["resolved"], baseline, p)
            self.assertEqual(cur["resolved"], baseline, p)
            self.assertEqual(d["future_precision"]["resolved"], fut["resolved"], p)
            self.assertEqual(d["legal_validity"], "not_determined")
            self.assertEqual(d["applicability_status"], "not_assessed")

    # ---- answers
    def test_answers_across_modes_scopes_filters_and_k(self):
        for mode in MODES:
            for scope in SCOPES:
                for parts in (None, ["12:337"], ["12:217"]):
                    for k in (10, 1):
                        tag = (mode, scope, parts, k)
                        for p, supported in ((I1X, True), (ANC_I, False)):
                            out = self.index.answer(f"12 CFR 337.6{p}", mode=mode, scope=scope, parts=parts, k=k)
                            self.assertEqual(out["applicability_status"], "not_assessed", tag)
                            if parts == ["12:217"]:
                                self.assertEqual(out["results"], [], tag)
                                self.assertNotEqual(out["status"], "evidence_found", tag)
                            elif scope == "future":
                                self.assertEqual(out["status"], "evidence_found" if supported else "partial_evidence", (p,) + tag)
                                if not supported:
                                    self.assertIn(f"citation:12 CFR 337.6{p}", {g["topic"] for g in out["coverage_gaps"]}, tag)
                            for r in out["results"]:
                                if scope == "future":
                                    self.assertTrue(r["kind"] in ("amendment", "fr_preamble", "fr_table"), tag)

    def test_mixed_complete_and_truncated_citations(self):
        for mode in MODES:
            mixed = self.index.answer(f"12 CFR 337.6{I1X} and 12 CFR 337.6{ANC_I}", mode=mode, scope="future", k=10)
            cites = {c["citation"]: c for c in mixed["citations"]}
            self.assertTrue(cites[f"12 CFR 337.6{I1X}"]["resolved"])
            self.assertFalse(cites[f"12 CFR 337.6{ANC_I}"]["resolved"])
            self.assertEqual(mixed["status"], "partial_evidence")
            self.assertEqual({g["topic"] for g in mixed["coverage_gaps"] if g["topic"].startswith("citation:")},
                             {f"citation:12 CFR 337.6{ANC_I}"})
            both_ok = self.index.answer(f"12 CFR 337.6{C1} and 12 CFR 337.6{I1X}", mode=mode, scope="future", k=10)
            self.assertEqual(both_ok["status"], "evidence_found", mode)
        small = self.index.answer(f"12 CFR 337.6{I1X} and 12 CFR 337.6{C1}", mode="exact", scope="future", k=2)
        self.assertEqual(len(small["results"]), 2)

    def test_relationships_stay_unreviewed_and_instruments_independent(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertNotIn("reviewed", status)
        d = self.index.delta("12 CFR 337.6")
        self.assertEqual(sorted(g["doc_key"] for g in d["instruments"]), sorted(KEYS.values()))


if __name__ == "__main__":
    unittest.main()
