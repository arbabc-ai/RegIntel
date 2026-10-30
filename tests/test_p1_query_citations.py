"""Tests for S7-P1-QUERY-CITATION-INTEGRITY-13 (F1): a requested citation is read once, the same way, by citation_only,
resolve_citations (and so exact/hybrid retrieval and answer) and delta. Whitespace inside a complete designator is
normalized for lookup while the request is preserved; an ancestor prefix is never accepted when an unclosed, malformed
or detached suffix remains unread."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.search import citation_only, parse_query_citations as parse
from test_p1 import asset
from test_p1_amendatory_lists import FR_X, FR_Y, FR_Z
from test_p1_delta_precision import fr

MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")
PARTS = (None, ["12:3"], ["12:217"])

ECFR = """<div class="part" id="part-3"><h1>PART 3—CAPITAL</h1>
<div class="section" id="3.1"><h4>§ 3.1 Purpose.</h4>
<div id="p-3.1(a)"><p class="indent-1" data-title="3.1(a)">(a) Purpose text.</p></div>
<div id="p-3.1(c)"><p class="indent-1" data-title="3.1(c)">(c) Applicability.</p>
<div id="p-3.1(c)(4)"><p class="indent-2" data-title="3.1(c)(4)">(4) Transition.</p>
<div id="p-3.1(c)(4)(i)"><p class="indent-3" data-title="3.1(c)(4)(i)">(i) First transition item.</p></div></div></div>
<div id="p-3.1(e)"><p class="indent-1" data-title="3.1(e)">(e) Existing e text.</p></div></div>
<div class="section" id="3.22"><h4>§ 3.22 Adjustments and deductions.</h4>
<div id="p-3.22(a)"><p class="indent-1" data-title="3.22(a)">(a) General adjustments and deductions.</p></div></div></div>"""
FR_22 = fr("RULE", "1. Amend &#xA7; 3.22 by revising paragraph (a) to read as follows:", [("3.22", ["(a) New 3.22(a) text."])])
X, Y, W = "fr:2026-90051", "fr:2026-90052", "fr:2026-90071"

CANON = "12 CFR 3.1(c)(4)(i)"
SPACED = ["12 CFR 3.1(c)( 4 )(i)", "12 CFR 3.1(c)(\t4\t)(i)", "12 CFR 3.1( c )(4)( i )", "12 CFR 3.1(c)(\n4\n)(i)", "12 CFR 3.1(  c  )(  4  )(  i  )"]


class ParserTests(unittest.TestCase):
    def test_whitespace_inside_complete_designators_is_normalized_and_the_request_preserved(self):
        for q in SPACED:
            (c,) = parse(q)
            self.assertIsNone(c["malformed"], q)
            self.assertEqual((c["paras"], c["canonical"], c["label"]), ("(c)(4)(i)", CANON, CANON), q)
            self.assertEqual(c["requested"], q)  # exactly as asked, whitespace and all

    def test_literal_complete_but_missing_path_keeps_every_designator(self):
        (c,) = parse("12 CFR 3.22(a)( 999 )")
        self.assertEqual((c["paras"], c["canonical"], c["malformed"]), ("(a)(999)", "12 CFR 3.22(a)(999)", None))
        self.assertEqual(c["requested"], "12 CFR 3.22(a)( 999 )")

    def test_unclosed_malformed_and_detached_suffixes_are_malformed_never_a_valid_prefix(self):
        cases = {"CET1 12 CFR 3.22(a)(999": ("unclosed_or_malformed_designator_suffix", "(a)", "(999"),
                 "12 CFR 3.22(a)(x y)": ("unclosed_or_malformed_designator_suffix", "(a)", "(x"),
                 "12 CFR 3.1(c)(4) (i)": ("detached_designator_suffix", "(c)(4)", "(i)"),
                 "12 CFR 3.1(c)(4)\t\n(i)": ("detached_designator_suffix", "(c)(4)", "(i)"),
                 "12 CFR 3.22 (a)": ("detached_designator_suffix", "", "(a)"),
                 "12 CFR 3.1(c)(4)(i)(": ("unclosed_or_malformed_designator_suffix", "(c)(4)(i)", "(")}
        for q, (reason, prefix, unread) in cases.items():
            (c,) = parse(q)
            self.assertEqual(c["malformed"], reason, q)
            self.assertFalse(c["diagnostics"]["accepted_valid_prefix"], q)
            self.assertEqual(c["diagnostics"]["valid_prefix"].endswith(prefix), True, q)
            self.assertEqual(c["diagnostics"]["unread_suffix"], unread, q)
            self.assertEqual(c["label"], " ".join(c["requested"].split()), q)  # labelled as asked, not as its ancestor
            self.assertNotEqual(c["label"], c["canonical"], q)

    def test_ordinary_trailing_parentheticals_do_not_make_a_request_malformed(self):
        for q in ("12 CFR 3.12(c)(1) (introductory text)", "12 CFR 3.12(c)(1) (see below)", "12 CFR 3.22 (the capital rule)"):
            (c,) = parse(q)
            self.assertIsNone(c["malformed"], q)

    def test_canonical_citations_parse_exactly_as_before(self):
        for q, want in (("12 CFR 3.1", "12 CFR 3.1"), ("§ 3.1(e)", "§ 3.1(e)"), ("12 CFR Part 3", "12 CFR Part 3"),
                        ("12 CFR 3.12(c)(1)", "12 CFR 3.12(c)(1)"), ("31 CFR 1020.220(a)(1)", "31 CFR 1020.220(a)(1)")):
            (c,) = parse(q)
            self.assertEqual((c["label"], c["malformed"]), (want, None), q)
        self.assertEqual([c["label"] for c in parse("12 CFR 3.1 and § 217.2(a)")], ["12 CFR 3.1", "§ 217.2(a)"])
        self.assertEqual(parse("see § 12 and Part 7"), [])

    def test_citation_only_uses_the_shared_parser(self):
        for q in SPACED + ["12 CFR 3.22(a)(999", "12 CFR 3.1(c)(4) (i)", "12 CFR 3.1 and 12 CFR 3.22(a)( 999 )"]:
            self.assertTrue(citation_only(q), q)
        for q in ("CET1 12 CFR 3.22(a)(999", "capital 12 CFR 3.1(c)( 4 )(i)", "what does 12 CFR 3.1(c)(4)(i) require"):
            self.assertFalse(citation_only(q), q)


class QueryCitationIndexTests(unittest.TestCase):
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
                          "asset": asset(root, "e.html", ECFR.encode())},
            X: {**final, "document_number": "2026-90051", "title": "X", "asset": asset(root, "x.xml", FR_X)},
            Y: {**prop, "document_number": "2026-90052", "title": "Y", "asset": asset(root, "y.xml", FR_Y)},
            "fr:2026-90053": {**final, "document_number": "2026-90053", "title": "Z", "asset": asset(root, "z.xml", FR_Z)},
            W: {**final, "document_number": "2026-90071", "title": "W", "asset": asset(root, "w.xml", FR_22)},
        }
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "q.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rec(self, q, scope, parts=None):
        return self.index.resolve_citations(q, scope, parts)[0]

    # ---- '12 CFR 3.1(c)( 4 )(i)' is the canonical citation
    def test_spaced_citation_resolves_identically_to_the_canonical_one(self):
        for scope in SCOPES:
            for parts in PARTS:
                want = self.rec(CANON, scope, parts)
                for q in SPACED:
                    got = self.rec(q, scope, parts)
                    for key in ("citation", "resolved", "achieved", "reason", "by_scope", "future_paragraph_support",
                                "requested_precision", "legal_validity", "baseline_match", "_current_ids", "_future_ids"):
                        self.assertEqual(got[key], want[key], (q, scope, parts, key))
                    self.assertEqual(got["requested"], q)  # the original request is preserved
                    self.assertEqual(got["canonical"], CANON)

    def test_both_supporting_future_instruments_are_found_through_the_spaced_form(self):
        fut = self.rec(SPACED[0], "future")
        self.assertTrue(fut["resolved"])
        self.assertEqual(fut["achieved"], "paragraph")
        self.assertEqual(sorted({s["doc_key"] for s in fut["future_paragraph_support"]}), sorted([X, Y]))
        d, dc = self.index.delta(SPACED[0]), self.index.delta(CANON)
        self.assertEqual(sorted(d["future_precision"]["supported_instruments"]), sorted([X, Y]))
        for key in ("baseline", "section_context", "current_precision", "future_precision", "evidence_status", "requested_path",
                    "requested_precision", "legal_validity", "applicability_status"):
            self.assertEqual(d[key], dc[key], key)
        self.assertEqual([g["doc_key"] for g in d["instruments"]], [g["doc_key"] for g in dc["instruments"]])
        self.assertEqual([g["role"] for g in d["instruments"]], [g["role"] for g in dc["instruments"]])
        self.assertEqual((d["requested_citation"], d["canonical_citation"]), (SPACED[0], CANON))
        self.assertEqual(dc["requested_citation"], CANON)

    def test_answers_agree_between_spaced_and_canonical_across_every_combination(self):
        for mode in MODES:
            for scope in SCOPES:
                for parts in PARTS:
                    for k in (10, 1):
                        a, b = (self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k) for q in (SPACED[0], CANON))
                        tag = (mode, scope, parts, k)
                        self.assertEqual(a["status"], b["status"], tag)
                        self.assertEqual([r["provision_id"] for r in a["results"]], [r["provision_id"] for r in b["results"]], tag)
                        self.assertEqual([g["topic"] for g in a["coverage_gaps"]], [g["topic"] for g in b["coverage_gaps"]], tag)
        self.assertEqual(self.index.answer(SPACED[0], mode="exact", scope="future")["status"], "evidence_found")

    def test_resolver_and_delta_agree(self):
        for q in SPACED + [CANON, "12 CFR 3.22(a)( 999 )", "12 CFR 3.1(e)", "12 CFR 3.1(c)( 4 )(z)"]:
            d = self.index.delta(q)
            self.assertEqual(d["current_precision"]["resolved"], self.rec(q, "current")["resolved"], q)
            self.assertEqual(d["future_precision"]["resolved"], self.rec(q, "future")["resolved"], q)

    # ---- '12 CFR 3.22(a)( 999 )' keeps the complete missing path
    def test_complete_missing_path_is_retained_and_never_collapses_to_its_ancestor(self):
        q = "12 CFR 3.22(a)( 999 )"
        cur, fut = self.rec(q, "current"), self.rec(q, "future")
        self.assertEqual(cur["canonical"], "12 CFR 3.22(a)(999)")
        self.assertEqual(cur["requested"], q)
        self.assertFalse(cur["resolved"])
        self.assertEqual(cur["reason"], "paragraph_not_found_in_acquired_section")
        self.assertEqual((fut["resolved"], fut["achieved"], fut["reason"]), (False, "section_only", "paragraph_precision_unresolved"))
        self.assertEqual(self.index.exact(q, 20, "current"), [])
        d = self.index.delta(q)
        self.assertEqual(d["requested_path"], "(a)(999)")
        self.assertIsNone(d["baseline"])
        self.assertIsNotNone(d["section_context"])
        self.assertTrue(d["instruments"] and all(g["role"] == "section_context" for g in d["instruments"]))
        self.assertTrue(self.rec("12 CFR 3.22(a)", "current")["resolved"])  # the ancestor itself still resolves when asked for
        self.assertTrue(self.rec("12 CFR 3.22(a)", "future")["resolved"])

    # ---- 'CET1 12 CFR 3.22(a)(999' (unclosed) never resolves to (a) and is never evidence_found
    def test_unclosed_request_is_an_explicit_unresolved_citation_never_its_ancestor(self):
        q = "CET1 12 CFR 3.22(a)(999"
        label = "12 CFR 3.22(a)(999"
        for scope in SCOPES:
            r = self.rec(q, scope)
            self.assertFalse(r["resolved"], scope)
            self.assertEqual(r["reason"], "unclosed_or_malformed_designator_suffix")
            self.assertEqual((r["citation"], r["requested"], r["canonical"]), (label, label, None))
            self.assertEqual(r["legal_validity"], "not_determined")
            self.assertFalse(r["diagnostics"]["accepted_valid_prefix"])
            self.assertEqual((r["diagnostics"]["valid_prefix"], r["diagnostics"]["unread_suffix"]), ("3.22(a)", "(999"))
            self.assertEqual((r["_current_ids"], r["_future_ids"]), ([], []))
            self.assertEqual(self.index.exact(q, 20, scope), [])
            self.assertIn("not a determination that the citation is invalid", r["note"])
        ctx = self.rec(q, "both")["context_available"]  # broader evidence, labelled as context and only counted
        self.assertEqual(ctx["role"], "section_context_not_the_requested_citation")
        self.assertGreater(ctx["current_records"] + ctx["future_records"], 0)
        for mode in MODES:
            for scope in SCOPES:
                for parts in PARTS:
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                        tag = (mode, scope, parts, k)
                        self.assertNotEqual(out["status"], "evidence_found", tag)
                        self.assertIn(f"citation:{label}", {g["topic"] for g in out["coverage_gaps"]}, tag)
                        self.assertEqual(out["applicability_status"], "not_assessed")
                        self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence", tag)
                        self.assertIn("cet1_capital", out["topics_matched"], tag)  # the acquired topic matches; it does not hide the gap
                        for r in out["results"]:
                            self.assertFalse(r["locator"] == "3.22(a)" and mode == "exact", tag)

    def test_delta_of_a_malformed_request_is_explicit_and_asserts_nothing(self):
        d = self.index.delta("12 CFR 3.22(a)(999")
        self.assertEqual(d["evidence_status"], "unavailable")
        self.assertIsNone(d["baseline"])
        self.assertEqual(d["instruments"], [])
        self.assertEqual(d["unavailable"]["reason"], "unclosed_or_malformed_designator_suffix")
        self.assertEqual(d["unavailable"]["diagnostics"]["valid_prefix"], "3.22(a)")
        self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))
        self.assertFalse(d["current_precision"]["resolved"] or d["future_precision"]["resolved"])
        self.assertEqual(d["requested_citation"], "12 CFR 3.22(a)(999")
        det = self.index.delta("12 CFR 3.1(c)(4) (i)")
        self.assertEqual(det["unavailable"]["reason"], "detached_designator_suffix")
        self.assertEqual(det["instruments"], [])

    def test_detached_suffix_is_ambiguous_not_a_resolved_ancestor(self):
        for q, label in (("12 CFR 3.1(c)(4) (i)", "12 CFR 3.1(c)(4) (i)"), ("12 CFR 3.22 (a)", "12 CFR 3.22 (a)")):
            for scope in SCOPES:
                r = self.rec(q, scope)
                self.assertFalse(r["resolved"], (q, scope))
                self.assertEqual((r["reason"], r["citation"]), ("detached_designator_suffix", label))
            out = self.index.answer(q, mode="exact", scope="both")
            self.assertEqual((out["status"], out["results"]), ("missing_evidence", []))
            self.assertIn(f"citation:{label}", {g["topic"] for g in out["coverage_gaps"]})

    # ---- mixed citations, parentheticals, controls
    def test_mixed_valid_and_unresolved_citations(self):
        for mode in MODES:
            for scope in ("future", "both"):
                mixed = self.index.answer("12 CFR 3.1(e) and 12 CFR 3.22(a)(999", mode=mode, scope=scope, k=10)
                cites = {c["citation"]: c for c in mixed["citations"]}
                self.assertTrue(cites["12 CFR 3.1(e)"]["resolved"])
                self.assertFalse(cites["12 CFR 3.22(a)(999"]["resolved"])
                self.assertEqual(mixed["status"], "partial_evidence", (mode, scope))
                self.assertEqual({g["topic"] for g in mixed["coverage_gaps"] if g["topic"].startswith("citation:")},
                                 {"citation:12 CFR 3.22(a)(999"})
                small = self.index.answer("12 CFR 3.1(e) and 12 CFR 3.22(a)(999", mode=mode, scope=scope, k=1)
                self.assertEqual(small["status"], "partial_evidence")
                good = self.index.answer("12 CFR 3.1(c)( 4 )(i) and 12 CFR 3.1(e)", mode=mode, scope="future", k=10)
                self.assertEqual(good["status"], "evidence_found", (mode, scope))
                wrong_part = self.index.answer("12 CFR 3.1(c)( 4 )(i) and 12 CFR 3.22(a)( 999 )", mode=mode, scope="future", parts=["12:217"])
                self.assertEqual(wrong_part["results"] if mode == "exact" else [], [])
                self.assertNotEqual(wrong_part["status"], "evidence_found")

    def test_ordinary_trailing_parenthetical_does_not_change_the_answer(self):
        for mode in MODES:
            plain = self.index.answer("12 CFR 3.1(e)", mode=mode, scope="future")
            with_note = self.index.answer("12 CFR 3.1(e) (introductory text)", mode=mode, scope="future")
            self.assertEqual(with_note["citations"][0]["resolved"], plain["citations"][0]["resolved"])
            self.assertEqual(with_note["citations"][0]["canonical"], "12 CFR 3.1(e)")
            self.assertEqual(self.index.exact("12 CFR 3.1(e) (introductory text)", 20, "both"), self.index.exact("12 CFR 3.1(e)", 20, "both"))

    def test_canonical_citation_controls_are_unchanged(self):
        for q in ("12 CFR 3.1", "§ 3.1", "12 CFR 3.1(e)", "12 CFR 3.1(c)(4)(i)", "12 CFR Part 3"):
            for mode in MODES:
                self.assertEqual(self.index.answer(q, mode=mode, scope="current")["status"], "evidence_found", (q, mode))
        for q in ("12 CFR 3.1(c)(4)(e)", "12 CFR 3.22(a)(999)"):
            self.assertFalse(self.rec(q, "future")["resolved"], q)
        self.assertEqual(self.rec("12 CFR 3.1(c)(4)(i)", "future")["future_paragraph_support"].__len__(), 2)

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertNotIn("reviewed", status)


if __name__ == "__main__":
    unittest.main()
