"""Tests for S7-P1-QUERY-RANGE-INTEGRITY-19 (F1): a section or paragraph range in a request (`3.1-6.1`, `3.22(a) through (z)`,
`to`, en dash) is an explicit unsupported expression. Its interior is never inferred and neither endpoint is accepted as
the whole request: the complete expression is preserved, resolver and exact retrieval return nothing for it, answers carry
a citation gap, and delta() reports it with no baseline or instruments. Independent citations and ordinary prose are untouched."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.search import citation_only, parse_query_citations as parse
from test_p1 import asset
from test_p1_query_lists import ECFR_3, ECFR_6, ECFR_31, F1, F2, KEYS

MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")
PARTS = (None, ["12:3"], ["12:6"], ["12:217"])
UNSUPPORTED = "unsupported_range"
SECTION_RANGES = ("12 CFR 3.1-6.1", "12 CFR 3.1 through 6.1", "12 CFR 3.1 to 6.1", "12 CFR 3.1–6.1", "12 CFR 3.1—6.1", "12 CFR 3.1 thru 12 CFR 6.1",
                  "§ 3.1 to § 6.1", "12 CFR 3.1 – 6.1")
PARAGRAPH_RANGES = ("CET1 12 CFR 3.22(a) through (z)", "CET1 12 CFR 3.22(a) to (z)", "CET1 12 CFR 3.22(a)–(z)", "CET1 12 CFR 3.22(a)-(z)",
                    "CET1 12 CFR 3.22(a) through 12 CFR 3.22(z)")


class ParserTests(unittest.TestCase):
    def only(self, q):
        cs = parse(q)
        self.assertEqual(len(cs), 1, q)
        return cs[0]

    def test_required_reproductions_are_one_unsupported_expression(self):
        c = self.only("12 CFR 3.1-6.1")
        self.assertEqual((c["malformed"], c["requested"], c["label"], c["canonical"]), (UNSUPPORTED, "12 CFR 3.1-6.1", "12 CFR 3.1-6.1", None))
        c = self.only("CET1 12 CFR 3.22(a) through (z)")
        self.assertEqual((c["malformed"], c["requested"]), (UNSUPPORTED, "12 CFR 3.22(a) through (z)"))
        for q in SECTION_RANGES + PARAGRAPH_RANGES:
            self.assertEqual(self.only(q)["malformed"], UNSUPPORTED, q)

    def test_the_complete_expression_and_both_endpoints_are_recorded_and_nothing_is_inferred(self):
        d = self.only("12 CFR 3.1-6.1")["diagnostics"]
        r = d["range"]
        self.assertEqual((d["accepted_valid_prefix"], d["interior_inferred"], d["valid_prefix"]), (False, False, "12 CFR 3.1"))
        self.assertEqual((r["operator"], r["start"], r["end"], r["end_requested"], r["end_form"], r["cross_part_or_title"]),
                         ("dash", "12 CFR 3.1", "12 CFR 6.1", "6.1", "bare_section", True))
        p = self.only("12 CFR 3.22(a) through (z)")["diagnostics"]["range"]
        self.assertEqual((p["operator"], p["start"], p["end"], p["end_form"]), ("through", "12 CFR 3.22(a)", "12 CFR 3.22(z)", "bare_paragraph"))
        self.assertNotIn("12 CFR 3.2", json.dumps(self.only("12 CFR 3.1-3.5")["diagnostics"]).replace("12 CFR 3.22", ""))  # no interior section is named

    def test_shorthand_and_fully_qualified_endpoints_read_the_same_range(self):
        a = self.only("12 CFR 3.22(a) through (z)")["diagnostics"]["range"]
        b = self.only("12 CFR 3.22(a) through 12 CFR 3.22(z)")["diagnostics"]["range"]
        self.assertEqual((a["start"], a["end"]), (b["start"], b["end"]))
        a = self.only("12 CFR 3.1 to 6.1")["diagnostics"]["range"]
        b = self.only("12 CFR 3.1 to 12 CFR 6.1")["diagnostics"]["range"]
        self.assertEqual((a["start"], a["end"]), (b["start"], b["end"]))
        self.assertEqual((a["end_form"], b["end_form"]), ("bare_section", "fully_qualified"))

    def test_missing_endpoint_reversed_and_cross_part_expressions(self):
        m = self.only("12 CFR 3.1 through")
        self.assertEqual((m["malformed"], m["diagnostics"]["range"]["end_form"], m["diagnostics"]["range"]["end"]), (UNSUPPORTED, "missing_endpoint", None))
        self.assertEqual(self.only("12 CFR 3.1 through.")["malformed"], UNSUPPORTED)
        rev = self.only("12 CFR 3.5-3.1")["diagnostics"]["range"]
        self.assertEqual((rev["reversed"], rev["cross_part_or_title"]), (True, False))
        cross = self.only("12 CFR 3.1 through 12 CFR 217.2")["diagnostics"]["range"]
        self.assertTrue(cross["cross_part_or_title"])
        titles = self.only("12 CFR 3.1 to 31 CFR 3.5")["diagnostics"]["range"]
        self.assertTrue(titles["cross_part_or_title"])
        self.assertEqual(self.only("12 CFR 6.1-3.1")["diagnostics"]["range"]["reversed"], True)

    def test_independent_citations_in_the_same_request_are_preserved(self):
        cs = parse("12 CFR 3.1-6.1 and 12 CFR 3.22(a)")
        self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR 3.1-6.1", UNSUPPORTED), ("12 CFR 3.22(a)", None)])
        cs = parse("12 CFR 3.22(a), 12 CFR 3.1 through 12 CFR 6.1, 12 CFR 3.2")
        self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR 3.22(a)", None), ("12 CFR 3.1 through 12 CFR 6.1", UNSUPPORTED), ("12 CFR 3.2", None)])
        cs = parse("12 CFR 3.1, 6.1 through 7.1 and 3.22")
        self.assertEqual([(c["requested"], c["malformed"]) for c in cs], [("12 CFR 3.1", None), ("6.1 through 7.1", UNSUPPORTED), ("3.22", None)])
        self.assertEqual(cs[1]["diagnostics"]["range"]["start"], "12 CFR 6.1")  # the member's inherited title
        self.assertEqual(cs[2]["canonical"], "12 CFR 3.22")

    def test_ordinary_prose_is_not_a_range(self):
        for q in ("12 CFR 3.1 to the extent applicable", "12 CFR 3.22 - a rule", "applies to 12 CFR 3.22", "12 CFR 3.1 to 6.1 percent",
                  "12 CFR 3.1-3.5 percent", "12 CFR 3.1 through the end of the part", "12 CFR 3.22(a) to explain", "12 CFR 3.1 through its guidance",
                  "12 CFR 3.1 to 2026", "12 CFR 3.22(a) - (introductory text)"):
            cs = parse(q)
            self.assertEqual([c["malformed"] for c in cs], [None] * len(cs), q)
            self.assertEqual(len(cs), 1, q)

    def test_single_citation_and_list_behaviour_is_unchanged(self):
        self.assertEqual([c["label"] for c in parse("12 CFR 3.1, 6.1")], ["12 CFR 3.1", "12 CFR 6.1"])
        self.assertEqual([c["label"] for c in parse("12 CFR 3.22(a) and (z)")], ["12 CFR 3.22(a)", "12 CFR 3.22(z)"])
        for q in ("12 CFR 3.1", "12 CFR 3.1(c)(4)(i)", "12 CFR Part 3", "§ 3.22(a)", "12 CFR 3.22(a)(9999999"):
            self.assertEqual(len(parse(q)), 1, q)
        # PART-RANGE-INTEGRITY-21: this line used to assert that `12 CFR Part 3-6` parses as Part 3 alone ("out of scope").
        # That silently accepted the first endpoint as the whole request; part-level ranges are now unsupported_range
        # expressions too (tests/test_p1_part_ranges.py). A plain part citation is still a plain part citation.
        self.assertEqual(self.only("12 CFR Part 3-6")["malformed"], UNSUPPORTED)
        self.assertIsNone(self.only("12 CFR Part 3")["malformed"])

    def test_citation_only_covers_the_whole_expression(self):
        for q in SECTION_RANGES + ("12 CFR 3.22(a) through (z)",):
            self.assertTrue(citation_only(q), q)
        self.assertFalse(citation_only("capital 12 CFR 3.1-6.1"))


class RangeIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        ecfr = lambda t, p, name, html: {"source_class": "regulation", "publisher": "eCFR", "citation": f"{t} CFR Part {p}", "title": "T",
                                         "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                                         "asset": asset(root, name, html.encode())}
        docs = {"ecfr:12:3": ecfr(12, 3, "e3.html", ECFR_3), "ecfr:12:6": ecfr(12, 6, "e6.html", ECFR_6), "ecfr:31:3": ecfr(31, 3, "e31.html", ECFR_31),
                KEYS[0]: {**meta, "document_type": "Rule", "effective_on": "2027-02-01", "action": "Final rule.", "document_number": KEYS[0][3:], "title": "F1",
                          "asset": asset(root, "f1.xml", F1)},
                KEYS[1]: {**meta, "document_type": "Proposed Rule", "comments_close_on": "2026-12-01", "document_number": KEYS[1][3:], "title": "F2",
                          "asset": asset(root, "f2.xml", F2)}}
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "r.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def gaps(self, out):
        return sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))

    def test_resolver_returns_no_records_for_the_expression_and_no_endpoint_citation(self):
        for q in SECTION_RANGES + PARAGRAPH_RANGES:
            expr = parse(q)[0]["label"]
            for scope in SCOPES:
                for parts in PARTS:
                    recs = self.index.resolve_citations(q, scope, parts)
                    self.assertEqual(len(recs), 1, (q, scope, parts))  # the endpoint is not a second, resolved citation
                    r = recs[0]
                    self.assertFalse(r["resolved"])
                    self.assertEqual((r["reason"], r["citation"], r["canonical"], r["_current_ids"], r["_future_ids"]), (UNSUPPORTED, expr, None, [], []))
                    self.assertEqual(r["legal_validity"], "not_determined")
                    self.assertFalse(r["diagnostics"]["accepted_valid_prefix"])
                    self.assertIn("not a determination", r["note"])

    def test_exact_returns_nothing_and_hybrid_never_claims_the_range(self):
        for q in SECTION_RANGES + PARAGRAPH_RANGES:
            expr = parse(q)[0]["label"]
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (q, mode, scope, parts, k)
                            self.assertNotEqual(out["status"], "evidence_found", tag)
                            self.assertIn(f"citation:{expr}", self.gaps(out), tag)
                            self.assertEqual(out["applicability_status"], "not_assessed")
                            if mode == "exact":
                                self.assertEqual((out["results"], out["status"]), ([], "missing_evidence"), tag)

    def test_delta_reports_the_unsupported_range_without_baseline_or_instruments(self):
        for q in SECTION_RANGES + PARAGRAPH_RANGES + ("12 CFR 3.1 through",):
            d = self.index.delta(q)
            tag = q
            self.assertEqual(d["evidence_status"], "unavailable", tag)
            self.assertEqual((d["baseline"], d["section_context"], d["instruments"]), (None, None, []), tag)
            self.assertEqual(d["unavailable"]["reason"], UNSUPPORTED, tag)
            self.assertIn("range", d["unavailable"]["diagnostics"], tag)
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))
            self.assertFalse(d["current_precision"]["resolved"] or d["future_precision"]["resolved"])
            self.assertEqual(d["requested_citation"], parse(q)[0]["requested"])

    def test_independent_citations_survive_and_the_request_is_partial(self):
        q = "12 CFR 3.1-6.1 and 12 CFR 3.22(a)"
        for mode in MODES:
            for scope in ("current", "both"):
                for k in (10, 1):
                    out = self.index.answer(q, mode=mode, scope=scope, k=k)
                    cites = {c["citation"]: c for c in out["citations"]}
                    self.assertTrue(cites["12 CFR 3.22(a)"]["resolved"], (mode, scope, k))
                    self.assertFalse(cites["12 CFR 3.1-6.1"]["resolved"])
                    self.assertEqual(out["status"], "partial_evidence")
                    self.assertEqual(self.gaps(out), ["citation:12 CFR 3.1-6.1"])
        exact = self.index.answer(q, mode="exact", scope="current", k=10)
        self.assertTrue(exact["results"] and all(r["locator"].startswith("3.22") for r in exact["results"]))  # only the independent citation's records
        wrong = self.index.answer(q, mode="exact", scope="current", parts=["12:6"])
        self.assertEqual(wrong["results"], [])
        d = self.index.delta(q)
        self.assertEqual(d["evidence_status"], "unsupported_multi_citation")
        self.assertEqual([c["unresolved_reason"] for c in d["unavailable"]["citations"]], [UNSUPPORTED, None])

    def test_endpoints_asked_for_separately_still_resolve_as_before(self):
        for q in ("12 CFR 3.1", "12 CFR 6.1", "12 CFR 3.22(a)"):
            self.assertEqual(self.index.answer(q, mode="exact", scope="current")["status"], "evidence_found", q)
        both = self.index.answer("12 CFR 3.1 and 12 CFR 6.1", mode="exact", scope="current")
        self.assertEqual(both["status"], "evidence_found")
        self.assertEqual({r["doc_key"] for r in both["results"]}, {"ecfr:12:3", "ecfr:12:6"})
        self.assertEqual(self.index.answer("12 CFR 3.1, 6.1", mode="exact", scope="current")["status"], "evidence_found")
        self.assertNotEqual(self.index.delta("12 CFR 3.22(a)")["evidence_status"], "unsupported_multi_citation")

    def test_prose_controls_are_answered_like_the_single_citation(self):
        for q, base in (("12 CFR 3.1 to the extent applicable", "12 CFR 3.1"), ("applies to 12 CFR 3.22(a)", "12 CFR 3.22(a)")):
            a, b = (self.index.answer(x, mode="exact", scope="current") for x in (q, base))
            self.assertEqual([r["provision_id"] for r in a["results"]], [r["provision_id"] for r in b["results"]], q)
            self.assertEqual(self.gaps(a), [], q)

    def test_evaluator_group_is_registered_and_fails_when_the_range_is_collapsed(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("query_ranges", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["query_ranges"]], [f"QR0{n}" for n in range(1, 6)])
        item = {"id": "T1", "kind": "unsupported", "requests": ["12 CFR 3.1-6.1", "12 CFR 3.22(a) through (z)"]}
        self.assertTrue(evaluate.check_query_ranges(self.index, item)["passed"])
        real = search.parse_query_citations
        collapse = lambda q: [dict(c, malformed=None, label=c["canonical"] or c["label"]) for c in real(q)[:1]] if " through " in q or "-" in q else real(q)
        with mock.patch.object(evaluate, "parse_query_citations", collapse):
            self.assertFalse(evaluate.check_query_ranges(self.index, item)["passed"])

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, status)


if __name__ == "__main__":
    unittest.main()
