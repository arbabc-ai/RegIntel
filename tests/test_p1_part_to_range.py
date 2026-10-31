"""Tests for S7-P1-PART-TO-RANGE-22 (F1): a terminal bare-number endpoint after `to` (`12 CFR Part 3 to 6`, reversed
`12 CFR Part 6 to 3`) is one unsupported_range expression holding the complete request, like every other part range.
Years and quantities after `to` (`to 2026`, `to 6 percent`, `to 6 banks`) and prose stay a plain Part 3 citation."""
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
TO_RANGES = ("12 CFR Part 3 to 6", "12 CFR Part 6 to 3", "CET1 12 CFR Part 3 to 6", "12 CFR Part 3 to 6.", "12 CFR Part 217 to 3", "12 CFR Part 3 to 12")
PROSE = ("12 CFR Part 3 to the extent applicable", "12 CFR Part 3 to 2026", "12 CFR Part 3 to 6 percent", "12 CFR Part 3 to 6 banks", "12 CFR Part 3 to 6 %",
         "12 CFR Part 3 to 6 android", "12 CFR Part 3 to 6 apply", "applies to 12 CFR Part 3")


class ParserTests(unittest.TestCase):
    def only(self, q):
        cs = parse(q)
        self.assertEqual(len(cs), 1, q)
        return cs[0]

    def test_bare_number_after_to_is_one_unsupported_range_with_the_complete_request(self):
        for q in TO_RANGES:
            c = self.only(q)
            self.assertEqual(c["malformed"], UNSUPPORTED, q)
            self.assertIsNone(c["canonical"], q)
            d = c["diagnostics"]
            self.assertEqual((d["accepted_valid_prefix"], d["interior_inferred"]), (False, False), q)
        self.assertEqual(self.only("12 CFR Part 3 to 6")["requested"], "12 CFR Part 3 to 6")
        self.assertEqual(self.only("CET1 12 CFR Part 3 to 6")["requested"], "12 CFR Part 3 to 6")
        self.assertEqual(self.only("12 CFR Part 3 to 6.")["requested"], "12 CFR Part 3 to 6")  # the sentence period is not part of it

    def test_endpoint_diagnostics_and_reversal(self):
        r = self.only("12 CFR Part 3 to 6")["diagnostics"]["range"]
        self.assertEqual((r["operator"], r["start"], r["end"], r["end_requested"], r["end_form"], r["reversed"], r["cross_part_or_title"]),
                         ("to", "12 CFR Part 3", "12 CFR Part 6", "6", "bare_part", False, True))
        r = self.only("12 CFR Part 6 to 3")["diagnostics"]["range"]
        self.assertEqual((r["start"], r["end"], r["reversed"]), ("12 CFR Part 6", "12 CFR Part 3", True))
        self.assertNotIn("Part 4", json.dumps(self.only("12 CFR Part 3 to 6")["diagnostics"]))  # the interior is never named

    def test_independent_citations_following_the_range_are_preserved(self):
        for q, rest in (("12 CFR Part 3 to 6 and 12 CFR 3.22(a)", "12 CFR 3.22(a)"), ("12 CFR Part 3 to 6, 12 CFR 3.22(a)", "12 CFR 3.22(a)"),
                        ("12 CFR Part 3 to 6 and 12 CFR Part 217", "12 CFR Part 217"), ("12 CFR Part 3 to 6; 12 CFR 3.1", "12 CFR 3.1")):
            cs = parse(q)
            self.assertEqual([(c["malformed"], c["label"]) for c in cs], [(UNSUPPORTED, "12 CFR Part 3 to 6"), (None, rest)], q)

    def test_prose_year_and_quantity_controls_stay_a_plain_part(self):
        for q in PROSE:
            cs = parse(q)
            self.assertEqual([c["malformed"] for c in cs], [None] * len(cs), q)
            self.assertEqual(cs[0]["label"], "12 CFR Part 3", q)

    def test_existing_ranges_lists_and_citations_are_unchanged(self):
        for q, want in (("12 CFR Part 3-6", UNSUPPORTED), ("12 CFR Part 3 through 6", UNSUPPORTED), ("12 CFR Part 3 to Part 6", UNSUPPORTED),
                        ("12 CFR Part 3 to 12 CFR Part 6", UNSUPPORTED), ("12 CFR 3.1 to 6.1", UNSUPPORTED), ("12 CFR 3.22(a) to (z)", UNSUPPORTED),
                        ("12 CFR 3.1 to 6.1 percent", None), ("12 CFR Part 3", None), ("12 CFR Part 3 and 12 CFR 3.22(a)", None)):
            self.assertEqual(parse(q)[0]["malformed"], want, q)
        self.assertEqual([c["label"] for c in parse("12 CFR 3.1, 6.1")], ["12 CFR 3.1", "12 CFR 6.1"])

    def test_citation_only_accounts_for_the_whole_expression(self):
        for q in ("12 CFR Part 3 to 6", "12 CFR Part 6 to 3"):
            self.assertTrue(citation_only(q), q)
        self.assertFalse(citation_only("capital 12 CFR Part 3 to 6"))


class PartToRangeIndexTests(unittest.TestCase):
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
        cls.db = str(Path(cls.tmp.name) / "t.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def gaps(self, out):
        return sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))

    def test_resolver_records_are_unresolved_with_no_evidence_ids(self):
        for q in TO_RANGES:
            expr = parse(q)[0]["label"]
            for scope in SCOPES:
                for parts in PARTS:
                    recs = self.index.resolve_citations(q, scope, parts)
                    self.assertEqual(len(recs), 1, (q, scope, parts))
                    r = recs[0]
                    self.assertFalse(r["resolved"])
                    self.assertEqual((r["reason"], r["citation"], r["canonical"], r["_current_ids"], r["_future_ids"]), (UNSUPPORTED, expr, None, [], []))
                    self.assertEqual(r["legal_validity"], "not_determined")

    def test_exact_returns_nothing_and_no_answer_reports_evidence_found(self):
        for q in TO_RANGES:
            expr = parse(q)[0]["label"]
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:  # matching (12:3, 12:6) and non-matching (12:217) part filters
                        for k in (10, 1):
                            out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (q, mode, scope, parts, k)
                            self.assertNotEqual(out["status"], "evidence_found", tag)
                            self.assertIn(f"citation:{expr}", self.gaps(out), tag)
                            self.assertEqual(out["applicability_status"], "not_assessed")
                            if mode == "exact":
                                self.assertEqual((out["results"], out["status"]), ([], "missing_evidence"), tag)

    def test_delta_is_unavailable_without_baseline_section_context_or_instruments(self):
        for q in TO_RANGES:
            d = self.index.delta(q)
            self.assertEqual((d["evidence_status"], d["baseline"], d["section_context"], d["instruments"]), ("unavailable", None, None, []), q)
            self.assertEqual(d["unavailable"]["reason"], UNSUPPORTED, q)
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))
            self.assertFalse(d["current_precision"]["resolved"] or d["future_precision"]["resolved"])

    def test_independent_citation_after_the_range_still_resolves(self):
        q = "12 CFR Part 3 to 6 and 12 CFR 3.22(a)"
        for mode in MODES:
            for scope in ("current", "both"):
                for k in (10, 1):
                    out = self.index.answer(q, mode=mode, scope=scope, k=k)
                    cites = {c["citation"]: c for c in out["citations"]}
                    self.assertTrue(cites["12 CFR 3.22(a)"]["resolved"], (mode, scope, k))
                    self.assertFalse(cites["12 CFR Part 3 to 6"]["resolved"])
                    self.assertEqual((out["status"], self.gaps(out)), ("partial_evidence", ["citation:12 CFR Part 3 to 6"]))
        ex = self.index.answer(q, mode="exact", scope="current", k=10)
        alone = self.index.answer("12 CFR 3.22(a)", mode="exact", scope="current", k=10)
        self.assertEqual([r["provision_id"] for r in ex["results"]], [r["provision_id"] for r in alone["results"]])
        self.assertEqual(self.index.answer(q, mode="exact", scope="current", parts=["12:6"])["results"], [])
        self.assertEqual(self.index.delta(q)["evidence_status"], "unsupported_multi_citation")

    def test_prose_year_and_quantity_requests_are_answered_like_plain_part_3(self):
        plain = self.index.answer("12 CFR Part 3", mode="exact", scope="current")
        for q in PROSE[:-1]:
            out = self.index.answer(q, mode="exact", scope="current")
            self.assertEqual([r["provision_id"] for r in out["results"]], [r["provision_id"] for r in plain["results"]], q)
            self.assertEqual(self.gaps(out), [], q)
            self.assertNotEqual(self.index.delta(q)["evidence_status"], "unsupported_range", q)

    def test_evaluator_coverage_registered_and_failing_on_the_starting_behaviour(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("part_to_ranges", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["part_to_ranges"]], [f"PT0{n}" for n in range(1, 5)])
        item = {"id": "T1", "kind": "unsupported", "requests": ["12 CFR Part 3 to 6", "12 CFR Part 6 to 3"]}
        self.assertTrue(evaluate.check_query_ranges(self.index, item)["passed"])
        prose = {"id": "T2", "kind": "prose", "requests": [["12 CFR Part 3 to 6 percent", "12 CFR Part 3"], ["12 CFR Part 3 to 6 banks", "12 CFR Part 3"]]}
        self.assertTrue(evaluate.check_query_ranges(self.index, prose)["passed"])
        real = search.parse_query_citations

        def starting(q):  # the previous parser: a bare number after `to` ends no range, so Part 3 is the whole request
            cs = real(q)
            return [dict(cs[0], malformed=None, canonical="12 CFR Part 3", label="12 CFR Part 3", diagnostics=None)] if " to " in q and cs and cs[0]["malformed"] == UNSUPPORTED and "Part" not in q.split(" to ", 1)[1] else cs
        with mock.patch.object(evaluate, "parse_query_citations", starting):
            self.assertFalse(evaluate.check_query_ranges(self.index, item)["passed"])

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, status)


if __name__ == "__main__":
    unittest.main()
