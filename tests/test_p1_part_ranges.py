"""Tests for S7-P1-PART-RANGE-INTEGRITY-21 (F1): a part-level range in a request (`12 CFR Part 3-6`, `Part 3 through 6`,
`Part 3 to 12 CFR Part 6`, thru, en/em dash) is one unresolved unsupported_range expression, like section and paragraph
ranges. It is never reduced to the first part and never split into two independent part citations; its interior and any
legal validity are not inferred."""
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
PART_RANGES = ("12 CFR Part 3-6", "12 CFR Part 3 through 6", "12 CFR Part 3 to 12 CFR Part 6", "12 CFR Part 3 thru Part 6", "12 CFR Part 3–6",
               "12 CFR Part 3 — Part 6", "12 CFR Part 3 through 12 CFR Part 6", "12 CFR Part 3 to Part 6", "12 CFR Part 6-3", "12 CFR Part 3 through 31 CFR Part 6")
MISSING = ("12 CFR Part 3 through", "12 CFR Part 3 thru", "12 CFR Part 3 through.", "CET1 12 CFR Part 3 thru")


class ParserTests(unittest.TestCase):
    def only(self, q):
        cs = parse(q)
        self.assertEqual(len(cs), 1, q)
        return cs[0]

    def test_every_required_form_is_one_unsupported_expression_never_the_first_part(self):
        for q in PART_RANGES + MISSING:
            c = self.only(q)
            self.assertEqual(c["malformed"], UNSUPPORTED, q)
            self.assertIsNone(c["canonical"], q)
            self.assertFalse(c["diagnostics"]["accepted_valid_prefix"], q)
            self.assertFalse(c["diagnostics"]["interior_inferred"], q)
            self.assertEqual(c["diagnostics"]["valid_prefix"], "12 CFR Part 6" if q.startswith("12 CFR Part 6") else "12 CFR Part 3", q)
        self.assertEqual(self.only("12 CFR Part 3-6")["requested"], "12 CFR Part 3-6")  # the complete expression, as written
        self.assertEqual(self.only("CET1 12 CFR Part 3 through 6")["requested"], "12 CFR Part 3 through 6")

    def test_endpoint_diagnostics(self):
        r = self.only("12 CFR Part 3-6")["diagnostics"]["range"]
        self.assertEqual((r["operator"], r["start"], r["end"], r["end_requested"], r["end_form"], r["reversed"], r["cross_part_or_title"]),
                         ("dash", "12 CFR Part 3", "12 CFR Part 6", "6", "bare_part", False, True))
        r = self.only("12 CFR Part 3 to 12 CFR Part 6")["diagnostics"]["range"]
        self.assertEqual((r["operator"], r["end"], r["end_form"]), ("to", "12 CFR Part 6", "fully_qualified"))
        self.assertEqual(self.only("12 CFR Part 3 thru Part 6")["diagnostics"]["range"]["end_requested"], "Part 6")
        self.assertTrue(self.only("12 CFR Part 6-3")["diagnostics"]["range"]["reversed"])
        r = self.only("12 CFR Part 3 through 31 CFR Part 6")["diagnostics"]["range"]
        self.assertEqual((r["cross_part_or_title"], r["reversed"]), (True, None))
        m = self.only("12 CFR Part 3 through")["diagnostics"]["range"]
        self.assertEqual((m["end_form"], m["end"], m["end_requested"]), ("missing_endpoint", None, None))
        mixed = self.only("12 CFR Part 3 through 6.1")["diagnostics"]["range"]  # a part and a section endpoint: still one unsupported range
        self.assertEqual((mixed["end"], mixed["end_form"]), ("12 CFR 6.1", "bare_section"))

    def test_nothing_between_the_endpoints_is_named(self):
        d = json.dumps(self.only("12 CFR Part 3-9")["diagnostics"])
        for interior in ("Part 4", "Part 5", "Part 6", "Part 7", "Part 8"):
            self.assertNotIn(interior, d)

    def test_independent_citations_around_a_part_range_are_preserved(self):
        cs = parse("12 CFR Part 3-6 and 12 CFR 3.22(a)")
        self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR Part 3-6", UNSUPPORTED), ("12 CFR 3.22(a)", None)])
        cs = parse("12 CFR 3.22(a), 12 CFR Part 3 through 6, 12 CFR Part 217")
        self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR 3.22(a)", None), ("12 CFR Part 3 through 6", UNSUPPORTED), ("12 CFR Part 217", None)])
        cs = parse("12 CFR Part 3 to 12 CFR Part 6 and 12 CFR 3.1")  # the second endpoint is not a second, independent citation
        self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR Part 3 to 12 CFR Part 6", UNSUPPORTED), ("12 CFR 3.1", None)])

    def test_single_part_requests_lists_and_prose_are_unchanged(self):
        for q in ("12 CFR Part 3", "12 CFR Part 217", "12 CFR Part 3 and 12 CFR 3.22(a)", "12 CFR Part 3 to the extent applicable", "12 CFR Part 3 - a rule",
                  "12 CFR Part 3 to 2026", "12 CFR Part 3 to 6 percent", "12 CFR Part 3 through the end", "applies to 12 CFR Part 3"):
            cs = parse(q)
            self.assertEqual([c["malformed"] for c in cs], [None] * len(cs), q)
            self.assertEqual(cs[0]["label"], "12 CFR Part 3" if "Part 3" in q else "12 CFR Part 217", q)
        self.assertEqual([c["label"] for c in parse("12 CFR Part 3, 6.1")], ["12 CFR Part 3", "12 CFR 6.1"])
        self.assertEqual([c["label"] for c in parse("12 CFR Part 3 and 12 CFR 3.22(a)")], ["12 CFR Part 3", "12 CFR 3.22(a)"])

    def test_existing_section_and_paragraph_ranges_are_unchanged(self):
        for q, start, end in (("12 CFR 3.1-6.1", "12 CFR 3.1", "12 CFR 6.1"), ("12 CFR 3.22(a) through (z)", "12 CFR 3.22(a)", "12 CFR 3.22(z)")):
            r = self.only(q)["diagnostics"]["range"]
            self.assertEqual((r["start"], r["end"]), (start, end))
        self.assertEqual([c["label"] for c in parse("12 CFR 3.1, 6.1")], ["12 CFR 3.1", "12 CFR 6.1"])
        self.assertEqual(self.only("12 CFR 3.22(a) through (z")["malformed"], UNSUPPORTED)
        self.assertEqual(self.only("12 CFR Part 3 through (z)")["diagnostics"]["range"]["end_form"], "malformed_endpoint")  # no paragraph can end a part range

    def test_citation_only_accounts_for_the_whole_expression(self):
        for q in PART_RANGES:
            self.assertTrue(citation_only(q), q)
        self.assertFalse(citation_only("capital 12 CFR Part 3-6"))


class PartRangeIndexTests(unittest.TestCase):
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
        cls.db = str(Path(cls.tmp.name) / "p.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def gaps(self, out):
        return sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))

    def test_resolver_returns_one_unresolved_record_and_no_endpoint_citation(self):
        for q in PART_RANGES + MISSING:
            expr = parse(q)[0]["label"]
            for scope in SCOPES:
                for parts in PARTS:
                    recs = self.index.resolve_citations(q, scope, parts)
                    self.assertEqual(len(recs), 1, (q, scope, parts))
                    r = recs[0]
                    self.assertFalse(r["resolved"])
                    self.assertEqual((r["reason"], r["citation"], r["canonical"], r["_current_ids"], r["_future_ids"]), (UNSUPPORTED, expr, None, [], []))
                    self.assertEqual(r["legal_validity"], "not_determined")

    def test_exact_returns_nothing_and_no_mode_reports_evidence_found(self):
        for q in PART_RANGES + MISSING:
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

    def test_delta_is_unavailable_without_baseline_or_instruments(self):
        for q in PART_RANGES + MISSING:
            d = self.index.delta(q)
            self.assertEqual((d["evidence_status"], d["baseline"], d["section_context"], d["instruments"]), ("unavailable", None, None, []), q)
            self.assertEqual(d["unavailable"]["reason"], UNSUPPORTED, q)
            self.assertIn("range", d["unavailable"]["diagnostics"], q)
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))

    def test_independent_citation_after_the_range_still_resolves(self):
        q = "12 CFR Part 3-6 and 12 CFR 3.22(a)"
        for mode in MODES:
            for scope in ("current", "both"):
                for k in (10, 1):
                    out = self.index.answer(q, mode=mode, scope=scope, k=k)
                    cites = {c["citation"]: c for c in out["citations"]}
                    self.assertTrue(cites["12 CFR 3.22(a)"]["resolved"], (mode, scope, k))
                    self.assertFalse(cites["12 CFR Part 3-6"]["resolved"])
                    self.assertEqual((out["status"], self.gaps(out)), ("partial_evidence", ["citation:12 CFR Part 3-6"]))
        ex = self.index.answer(q, mode="exact", scope="current", k=10)
        alone = self.index.answer("12 CFR 3.22(a)", mode="exact", scope="current", k=10)
        self.assertEqual([r["provision_id"] for r in ex["results"]], [r["provision_id"] for r in alone["results"]])
        self.assertEqual(self.index.answer(q, mode="exact", scope="current", parts=["12:6"])["results"], [])
        self.assertEqual(self.index.delta(q)["evidence_status"], "unsupported_multi_citation")

    def test_single_part_requests_and_filters_behave_as_before(self):
        for parts, expect in ((None, True), (["12:3"], True), (["12:6"], False), (["12:217"], False)):
            out = self.index.answer("12 CFR Part 3", mode="exact", scope="current", parts=parts)
            self.assertEqual(out["status"] == "evidence_found", expect, parts)
        self.assertEqual(self.index.delta("12 CFR Part 3")["evidence_status"], "available")
        six = self.index.answer("12 CFR Part 6", mode="exact", scope="current")
        self.assertEqual(six["status"], "evidence_found")
        self.assertEqual({r["doc_key"] for r in six["results"]}, {"ecfr:12:6"})

    def test_evaluator_group_is_registered_and_fails_when_the_range_narrows_to_the_first_part(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("part_ranges", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["part_ranges"]], [f"PR0{n}" for n in range(1, 6)])
        item = {"id": "T1", "kind": "unsupported", "requests": ["12 CFR Part 3-6", "12 CFR Part 3 to 12 CFR Part 6", "12 CFR Part 3 through"]}
        self.assertTrue(evaluate.check_query_ranges(self.index, item)["passed"])
        real = search.parse_query_citations

        def narrow(q):  # the previous parser: the first part is the whole request
            cs = real(q)
            return [dict(cs[0], malformed=None, canonical="12 CFR Part 3", label="12 CFR Part 3", diagnostics=None)] if cs and cs[0]["malformed"] == UNSUPPORTED and "Part" in q else cs
        with mock.patch.object(evaluate, "parse_query_citations", narrow):
            self.assertFalse(evaluate.check_query_ranges(self.index, item)["passed"])

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, status)


if __name__ == "__main__":
    unittest.main()
