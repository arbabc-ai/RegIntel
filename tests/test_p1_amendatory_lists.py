"""Tests for S7-P1-AMENDATORY-LIST-PRECISION-11 (F1, F2): abbreviated paragraph designators inherit a parent only where
the CFR hierarchy supports it, and section lists, including Oxford-comma lists, are consumed completely."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.fedreg import named_paragraph_paths as paths, parse_fr, parse_instruction
from test_p1 import asset
from test_p1_delta_precision import fr

MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")

ECFR_3 = """<div class="part" id="part-3"><h1>PART 3—CAPITAL</h1>
<div class="section" id="3.1"><h4>§ 3.1 Purpose.</h4>
<div id="p-3.1(a)"><p class="indent-1" data-title="3.1(a)">(a) Purpose text.</p></div>
<div id="p-3.1(c)"><p class="indent-1" data-title="3.1(c)">(c) Applicability.</p>
<div id="p-3.1(c)(4)"><p class="indent-2" data-title="3.1(c)(4)">(4) Transition.</p>
<div id="p-3.1(c)(4)(i)"><p class="indent-3" data-title="3.1(c)(4)(i)">(i) First transition item.</p></div></div></div>
<div id="p-3.1(e)"><p class="indent-1" data-title="3.1(e)">(e) Existing e text.</p></div></div></div>"""

# X: the 2026-05960 shape. Literal expected paths: (c)(4)(i), (e), (f), (g).
FR_X = fr("RULE", "2. In &#xA7; 3.1: a. Revise paragraphs (c)(4)(i), (e) and (f); and b. Add paragraph (g). The addition and revision read as follows:",
          [("3.1", ["(c) * * *", "(4) * * *", "(i) X c4i text.", "(e) X e text.", "(f) X f text.", "(g) X g text."])])
# Y: names only (c)(4)(i) for the same section. It must not gain (e), (f) or (g) from X.
FR_Y = fr("PRORULE", "1. Amend &#xA7; 3.1 by revising paragraph (c)(4)(i) to read as follows:", [("3.1", ["(c) * * *", "(4) * * *", "(i) Y c4i text."])])
# Z: genuinely ambiguous shorthand: roman (v) or letter (v)?
FR_Z = fr("RULE", "1. Amend &#xA7; 3.1 by revising paragraphs (a)(1)(i) and (v) to read as follows:",
          [("3.1", ["(a) * * *", "(1) * * *", "(i) Z a1i text.", "(v) Z v text."])])
# W: 2026-15774 shape for 239.40 (a section with no baseline in the fixture).
FR_W = fr("PRORULE", "29. Amend &#xA7; 239.40 by revising paragraphs (b)(1), (c), and (d) to read as follows:",
          [("239.40", ["(b)(1) W b1 text.", "(c) W c text.", "(d) W d text."])], part="239")
# V: 2026-06947 shape: an Oxford-comma list, a numeric range, and a quoted list that is only a reference. No carried text.
FR_V = ("""<PRORULE><PREAMB><CFR>12 CFR Part 302</CFR></PREAMB><SUPLINF>
<AMDPAR>11. Add a heading for subpart A, consisting of &#xA7;&#xA7; 302.1, 302.2, and 302.3, to read as follows:</AMDPAR>
<AMDPAR>12. Add subpart B, consisting of &#xA7;&#xA7; 302.100 through 302.102, and 302.110, to read as follows:</AMDPAR>
<AMDPAR>13. Amend &#xA7; 302.5 by removing &#x201C;&#xA7;&#xA7; 302.8, 302.9, and 302.10&#x201D;.</AMDPAR>
</SUPLINF></PRORULE>""").encode()
KEYS = {"X": "fr:2026-90051", "Y": "fr:2026-90052", "Z": "fr:2026-90053", "W": "fr:2026-90054", "V": "fr:2026-90055"}


class RegressionGroupCoverageTests(unittest.TestCase):
    def test_overall_covers_every_regression_group_in_the_regressions_file(self):
        from regintel_p1 import evaluate
        spec = json.loads((Path(__file__).resolve().parent.parent / "eval" / "phase1" / "regressions.json").read_text())
        groups = {k for k, v in spec.items() if isinstance(v, (list, dict)) and k != "changelog"}
        self.assertEqual(groups, set(evaluate.REGRESSION_GROUPS))


class ShorthandParsingTests(unittest.TestCase):
    def test_literal_expected_paths_for_the_two_corpus_shapes(self):
        self.assertEqual(paths("2. In § 3.1: a. Revise paragraphs (c)(4)(i), (e) and (f); and b. Add paragraph (g)."),
                         ["(c)(4)(i)", "(e)", "(f)", "(g)"])
        self.assertEqual(paths("29. Amend § 239.40 by revising paragraphs (b)(1), (c), and (d) to read as follows:"),
                         ["(b)(1)", "(c)", "(d)"])

    def test_fabricated_paths_are_not_produced(self):
        out = paths("2. In § 3.1: a. Revise paragraphs (c)(4)(i), (e) and (f); and b. Add paragraph (g).")
        for fake in ("(c)(4)(e)", "(c)(4)(f)"):
            self.assertNotIn(fake, out)
        out = paths("Amend § 239.40 by revising paragraphs (b)(1), (c), and (d).")
        for fake in ("(b)(c)", "(b)(d)"):
            self.assertNotIn(fake, out)
        self.assertEqual(paths("Revise paragraphs (a)(1)(v), (b), (c), (d); and b. Add paragraph (b)(6)."),
                         ["(a)(1)(v)", "(b)", "(c)", "(d)", "(b)(6)"])
        self.assertEqual(paths("Revising paragraphs (h)(2), and (i)."), ["(h)(2)", "(i)"])
        self.assertEqual(paths("Revising paragraphs (e)(1)(i) and (l)."), ["(e)(1)(i)", "(l)"])

    def test_supported_shorthand_inherits_the_parent(self):
        self.assertEqual(paths("Revise paragraphs (c)(5) through (7)."), ["(c)(5)", "(c)(7)"])  # endpoints only, no interior
        self.assertNotIn("(c)(6)", paths("Revise paragraphs (c)(5) through (7)."))
        self.assertEqual(paths("Revise paragraphs (a)(1)(i) and (ii)."), ["(a)(1)(i)", "(a)(1)(ii)"])
        self.assertEqual(paths("Revise paragraphs (a)(1) and (3)."), ["(a)(1)", "(a)(3)"])
        self.assertEqual(paths("Revise paragraph (a)(1)(i)(A) and (B)."), ["(a)(1)(i)(A)", "(a)(1)(i)(B)"])
        self.assertEqual(paths("Revise paragraphs (a)(1), (b)(2) and (3)."), ["(a)(1)", "(b)(2)", "(b)(3)"])
        self.assertEqual(paths("Revise paragraphs (e), (f), and (g)."), ["(e)", "(f)", "(g)"])
        self.assertEqual(paths("Revise paragraphs (c)(4)(i) and (iii), and (f), and add paragraph (g)."),
                         ["(c)(4)(i)", "(c)(4)(iii)", "(f)", "(g)"])

    def test_genuinely_ambiguous_shorthand_is_left_unresolved(self):
        self.assertEqual(paths("Revise paragraphs (a)(1)(i) and (v)."), ["(a)(1)(i)"])  # roman v or letter v
        self.assertEqual(paths("Revise paragraphs (a)(1)(i) and (v), (vi)."), ["(a)(1)(i)"])  # nothing after it is trusted
        self.assertEqual(paths("Revise paragraphs (a)(1)(i) and (x), (b)(2)."), ["(a)(1)(i)", "(b)(2)"])  # a full path resumes
        self.assertEqual(paths("Revise paragraphs (a) and (1)."), ["(a)"])  # a digit cannot be a letter's sibling or top-level

    def test_quoted_and_cross_reference_shorthand_is_never_evidence(self):
        self.assertEqual(paths("Amend § 3.1 by removing “paragraphs (c)(4)(i), (e) and (f)” and revising paragraph (a)."), ["(a)"])
        self.assertEqual(paths("Revise § 3.1; see paragraphs (c)(4)(i), (e) and (f) of this section."), [])


class SectionListParsingTests(unittest.TestCase):
    def test_oxford_comma_list_keeps_all_three_targets(self):
        p = parse_instruction("11. Add a heading for subpart A, consisting of §§ 302.1, 302.2, and 302.3, to read as follows:")
        self.assertEqual(p["targets"], ["302.1", "302.2", "302.3"])
        self.assertEqual(p["ranges"], [])  # a list is not a range
        self.assertEqual(p["references"], [])

    def test_list_variants(self):
        self.assertEqual(parse_instruction("Amend §§ 3.1, 3.2, or 3.3 by revising text.")["targets"], ["3.1", "3.2", "3.3"])
        self.assertEqual(parse_instruction("Amend §§ 3.1, § 3.2, and § 3.3 by revising text.")["targets"], ["3.1", "3.2", "3.3"])
        self.assertEqual(parse_instruction("Amend §§ 3.1 and 3.2 by revising text.")["targets"], ["3.1", "3.2"])
        self.assertEqual(parse_instruction("Amend § 3.1 by revising text.")["targets"], ["3.1"])

    def test_targets_ranges_and_quoted_references_stay_distinct(self):
        p = parse_instruction("12. Add subpart B, consisting of §§ 302.100 through 302.102, and 302.110, to read as follows:")
        self.assertEqual(p["targets"], ["302.100", "302.102", "302.110"])
        self.assertEqual(p["ranges"], [["302.100", "302.102"]])  # 302.101 is interior, not a target
        q = parse_instruction("13. Amend § 302.5 by removing “§§ 302.8, 302.9, and 302.10”.")
        self.assertEqual(q["targets"], ["302.5"])
        self.assertEqual({(r["section"], r["quoted"]) for r in q["references"]}, {("302.8", True), ("302.9", True), ("302.10", True)})

    def test_instruction_with_no_explicit_target_stays_an_unresolved_candidate(self):
        units = parse_fr(("""<PRORULE><PREAMB><CFR>12 CFR Part 302</CFR></PREAMB><SUPLINF>
<AMDPAR>1. Revise the provisions listed in §§ 302.1, 302.2, and 302.3.</AMDPAR></SUPLINF></PRORULE>""").encode(), "2026-90099")[1]
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["pairing_status"], "instruction_no_explicit_target")
        self.assertEqual(units[0]["explicit_targets"], [])
        self.assertEqual(units[0]["unresolved_candidates"][0]["candidate_sections"], ["302.1", "302.2", "302.3"])

    def test_the_acquired_06947_instruction_row_keeps_every_target(self):
        units = parse_fr(FR_V, "2026-90055")[1]
        row = next(u for u in units if u["instruction"].startswith("11. Add a heading"))
        self.assertEqual(row["explicit_targets"], ["302.1", "302.2", "302.3"])
        self.assertEqual(row["pairing_status"], "instruction_only")
        self.assertEqual(row["carried_text_origin"], "section_element")


class AmendatoryListIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        final = {**meta, "document_type": "Rule", "effective_on": "2027-02-01", "action": "Final rule."}
        prop = {**meta, "document_type": "Proposed Rule", "comments_close_on": "2026-12-01"}
        docs = {
            "ecfr:12:3": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 3", "title": "Capital",
                          "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                          "asset": asset(root, "e.html", ECFR_3.encode())},
            KEYS["X"]: {**final, "document_number": "2026-90051", "title": "X", "asset": asset(root, "x.xml", FR_X)},
            KEYS["Y"]: {**prop, "document_number": "2026-90052", "title": "Y", "asset": asset(root, "y.xml", FR_Y)},
            KEYS["Z"]: {**final, "document_number": "2026-90053", "title": "Z", "asset": asset(root, "z.xml", FR_Z)},
            KEYS["W"]: {**prop, "document_number": "2026-90054", "title": "W", "asset": asset(root, "w.xml", FR_W)},
            KEYS["V"]: {**prop, "document_number": "2026-90055", "title": "V", "asset": asset(root, "v.xml", FR_V)},
        }
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "l.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def fut(self, q):
        return self.index.resolve_citations(q, "future")[0]

    def supported(self, cite):
        return sorted(self.index.delta(cite)["future_precision"]["supported_instruments"])

    # ---- F1 through build, resolver and delta
    def test_indexed_paths_are_the_literal_expected_ones(self):
        for key, want in (("X", ["(c)(4)(i)", "(e)", "(f)", "(g)"]), ("Y", ["(c)(4)(i)"]), ("Z", ["(a)(1)(i)"]), ("W", ["(b)(1)", "(c)", "(d)"])):
            rows = self.index.conn.execute(
                "SELECT p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id "
                "WHERE d.doc_key=? AND p.kind='amendment' AND p.explicit_paragraphs IS NOT NULL", (KEYS[key],)).fetchall()
            got = sorted({x for r in rows for x in json.loads(r[0])["paths"]})
            self.assertEqual(got, sorted(want), key)

    def test_fabricated_paths_stay_unresolved_in_future_scope_with_section_context(self):
        for cite in ("12 CFR 3.1(c)(4)(e)", "12 CFR 3.1(c)(4)(f)", "12 CFR 3.1(a)(1)(v)", "12 CFR 239.40(b)(c)", "12 CFR 239.40(b)(d)"):
            c = self.fut(cite)
            self.assertFalse(c["resolved"], cite)
            self.assertEqual((c["achieved"], c["reason"]), ("section_only", "paragraph_precision_unresolved"), cite)
            self.assertEqual(c["future_paragraph_support"], [], cite)
            d = self.index.delta(cite)
            self.assertFalse(d["future_precision"]["resolved"], cite)
            self.assertEqual(d["evidence_status"], "section_context_only", cite)
            self.assertTrue(d["instruments"] and all(g["role"] == "section_context" for g in d["instruments"]), cite)
            self.assertEqual(d["legal_validity"], "not_determined")
            self.assertEqual(d["applicability_status"], "not_assessed")

    def test_each_instruments_support_is_independent(self):
        self.assertEqual(self.supported("12 CFR 3.1(e)"), [KEYS["X"]])
        self.assertEqual(self.supported("12 CFR 3.1(f)"), [KEYS["X"]])
        self.assertEqual(self.supported("12 CFR 3.1(g)"), [KEYS["X"]])
        self.assertEqual(self.supported("12 CFR 3.1(c)(4)(i)"), sorted([KEYS["X"], KEYS["Y"]]))  # both literally name it
        self.assertEqual(self.supported("12 CFR 3.1(a)(1)(i)"), [KEYS["Z"]])  # the unambiguous half of Z only
        d = self.index.delta("12 CFR 3.1(e)")
        roles = {g["doc_key"]: g["role"] for g in d["instruments"]}
        self.assertEqual(roles[KEYS["X"]], "paragraph_supported")
        self.assertEqual({roles[KEYS["Y"]], roles[KEYS["Z"]]}, {"section_context"})
        for cite, doc in (("12 CFR 239.40(b)(1)", "W"), ("12 CFR 239.40(c)", "W"), ("12 CFR 239.40(d)", "W")):
            self.assertEqual(self.supported(cite), [KEYS[doc]], cite)

    def test_baseline_existence_is_separate_from_instrument_support(self):
        d = self.index.delta("12 CFR 3.1(c)(4)(i)")  # in the baseline and named by X and Y
        self.assertEqual(d["current_precision"]["resolved"], True)
        d2 = self.index.delta("12 CFR 3.1(g)")  # not in the baseline, named only by X
        self.assertFalse(d2["current_precision"]["resolved"])
        self.assertEqual(d2["future_precision"]["supported_instruments"], [KEYS["X"]])
        d3 = self.index.delta("12 CFR 3.1(e)")  # in the baseline AND named by X
        self.assertTrue(d3["current_precision"]["resolved"] and d3["future_precision"]["resolved"])

    def test_answers_for_fabricated_paths_across_modes_scopes_filters_and_k(self):
        for cite in ("12 CFR 3.1(c)(4)(e)", "12 CFR 239.40(b)(c)"):
            for mode in MODES:
                for scope in SCOPES:
                    for parts in (None, ["12:3", "12:239"], ["12:217"]):
                        for k in (10, 1):
                            out = self.index.answer(cite, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (cite, mode, scope, parts, k)
                            self.assertNotEqual(out["status"], "evidence_found", tag)
                            self.assertIn(f"citation:{cite}", {g["topic"] for g in out["coverage_gaps"]}, tag)
                            self.assertEqual(out["applicability_status"], "not_assessed")
                            self.assertEqual(out["citations"][0]["legal_validity"], "not_determined")
                            for r in out["results"]:
                                if scope == "future":
                                    self.assertTrue(r["kind"] in ("amendment", "fr_preamble", "fr_table") and
                                                    not r["status"]["state"].startswith("current"), tag)
                            if parts == ["12:217"]:
                                self.assertEqual(out["results"], [], tag)
                                self.assertEqual(out["status"], "missing_evidence", tag)
        out = self.index.answer("12 CFR 3.1(c)(4)(e)", mode="exact", scope="future", k=1)
        self.assertEqual((out["status"], len(out["results"]), out["citations"][0]["achieved"]), ("partial_evidence", 1, "section_only"))

    def test_supported_paths_and_mixed_valid_unresolved_citations(self):
        for mode in MODES:
            ok = self.index.answer("12 CFR 3.1(e) and 12 CFR 3.1(f) and 12 CFR 3.1(g)", mode=mode, scope="future", k=10)
            self.assertEqual(ok["status"], "evidence_found", mode)
            self.assertEqual(ok["coverage_gaps"], [])
            mixed = self.index.answer("12 CFR 3.1(e) and 12 CFR 3.1(c)(4)(e)", mode=mode, scope="future", k=10)
            cites = {c["citation"]: c for c in mixed["citations"]}
            self.assertTrue(cites["12 CFR 3.1(e)"]["resolved"])
            self.assertFalse(cites["12 CFR 3.1(c)(4)(e)"]["resolved"])
            self.assertEqual(mixed["status"], "partial_evidence")
            self.assertEqual({g["topic"] for g in mixed["coverage_gaps"] if g["topic"].startswith("citation:")},
                             {"citation:12 CFR 3.1(c)(4)(e)"})
            wrong_part = self.index.answer("12 CFR 3.1(e) and 12 CFR 239.40(c)", mode=mode, scope="future", parts=["12:3"])
            self.assertEqual(wrong_part["status"], "partial_evidence")
            self.assertEqual({c["citation"]: c["reason"] for c in wrong_part["citations"] if not c["resolved"]},
                             {"12 CFR 239.40(c)": "excluded_by_part_filter"})
        for cite in ("12 CFR 239.40(b)(1)", "12 CFR 239.40(c)", "12 CFR 239.40(d)"):
            self.assertEqual(self.index.answer(cite, mode="exact", scope="future")["status"], "evidence_found", cite)

    # ---- F2 through build, resolver, answer and delta
    def test_oxford_list_targets_reach_delta_and_future_retrieval(self):
        for sec in ("302.1", "302.2", "302.3"):
            d = self.index.delta(f"12 CFR {sec}")
            self.assertEqual([g["doc_key"] for g in d["instruments"]], [KEYS["V"]], sec)
            self.assertEqual(d["instruments"][0]["units"][0]["match"], "explicit_target")
            self.assertEqual(self.index.answer(f"12 CFR {sec}", mode="exact", scope="future")["status"], "evidence_found", sec)
        self.assertEqual(self.index.delta("12 CFR 302.4")["instruments"], [])  # not named
        for mode in MODES:
            self.assertNotEqual(self.index.answer("12 CFR 302.4", mode=mode, scope="future")["status"], "evidence_found")

    def test_ranges_lists_and_quoted_references_are_not_confused_after_the_fix(self):
        self.assertEqual([g["doc_key"] for g in self.index.delta("12 CFR 302.101")["instruments"]], [KEYS["V"]])  # numeric range interior
        self.assertEqual(self.index.delta("12 CFR 302.101")["instruments"][0]["units"][0]["match"], "range_membership")
        self.assertEqual([g["doc_key"] for g in self.index.delta("12 CFR 302.110")["instruments"]], [KEYS["V"]])  # list member after the range
        self.assertEqual(self.index.delta("12 CFR 302.103")["instruments"], [])  # beyond the range
        for quoted in ("302.8", "302.9", "302.10"):
            self.assertEqual(self.index.delta(f"12 CFR {quoted}")["instruments"], [], quoted)  # only quoted references
        self.assertEqual([g["doc_key"] for g in self.index.delta("12 CFR 302.5")["instruments"]], [KEYS["V"]])

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertNotIn("reviewed", status)


if __name__ == "__main__":
    unittest.main()
