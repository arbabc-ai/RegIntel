"""Tests for S7-P1-FOUR-DIGIT-PART-OPERANDS-24 (F1): four-digit part operands (`31 CFR Parts 1010 and 1020`, `Part 1010 to 1020`)
are list members and range endpoints like any other part. Years and quantities stay controls, and a number that could be a
part or a year (1900-1999) is a structured ambiguity, never a silent choice. The acquired-part inventory is not consulted when
reading the text."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.search import citation_only, parse_query_citations as parse
from test_p1 import asset
from test_p1_query_lists import ECFR_3

MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")
PARTS = (None, ["31:1010"], ["31:1020"], ["12:3"])
ECFR_1010 = """<div class="part" id="part-1010"><h1>PART 1010—GENERAL PROVISIONS</h1>
<div class="section" id="1010.100"><h4>§ 1010.100 General definitions.</h4>
<div id="p-1010.100(a)"><p class="indent-1" data-title="1010.100(a)">(a) Definitions apply.</p></div></div></div>"""
ECFR_1020 = """<div class="part" id="part-1020"><h1>PART 1020—BANKS</h1>
<div class="section" id="1020.220"><h4>§ 1020.220 Customer identification.</h4>
<div id="p-1020.220(a)"><p class="indent-1" data-title="1020.220(a)">(a) Banks verify identity.</p></div></div></div>"""
LISTS = {"31 CFR Parts 1010 and 1020": ["31 CFR Part 1010", "31 CFR Part 1020"],
         "31 CFR Parts 1010, 1020, and 1030": ["31 CFR Part 1010", "31 CFR Part 1020", "31 CFR Part 1030"],
         "31 CFR Part 1010, Part 1020": ["31 CFR Part 1010", "31 CFR Part 1020"], "31 CFR Part 1010 and Part 1020": ["31 CFR Part 1010", "31 CFR Part 1020"],
         "31 CFR Part 1010; 1020": ["31 CFR Part 1010", "31 CFR Part 1020"], "31 CFR Part 1010 or 1020": ["31 CFR Part 1010", "31 CFR Part 1020"],
         "31 CFR Parts 1010, 1020; and 1030": ["31 CFR Part 1010", "31 CFR Part 1020", "31 CFR Part 1030"]}
RANGES = ("31 CFR Part 1010 to 1020", "31 CFR Part 1020 to 1010", "31 CFR Part 1010 through 1020", "31 CFR Part 1010 thru 1020", "31 CFR Part 1010-1020",
          "31 CFR Part 1010–1020", "31 CFR Parts 1010 to 1020", "CET1 31 CFR Part 1010 to 1020", "31 CFR Part 1010 to Part 1020")
CONTROLS = ("12 CFR Part 3 to 2026", "12 CFR Part 3, 2026", "12 CFR Part 3 and 6 banks", "12 CFR Part 3 to 6 percent", "12 CFR Part 3 and 2026.",
            "31 CFR Part 1010 and 1020 banks", "31 CFR Part 1010 to 1020 percent", "31 CFR Part 1010, 2026")


class ParserTests(unittest.TestCase):
    def test_every_four_digit_list_member_is_a_citation_with_provenance(self):
        for q, want in LISTS.items():
            cs = parse(q)
            self.assertEqual([c["canonical"] for c in cs], want, q)
            self.assertTrue(all(c["malformed"] is None for c in cs), q)
        a, b, c = parse("CET1 31 CFR Parts 1010, 1020, and 1030")
        self.assertEqual((a["requested"], b["requested"], c["requested"]), ("31 CFR Parts 1010", "1020", "1030"))
        self.assertEqual(c["inherited"], {"kind": "part", "from": "31 CFR Part 1020", "title_inherited": "31"})
        self.assertIsNone(a["inherited"])

    def test_explicit_titles_and_independent_citations_after_the_list(self):
        cs = parse("31 CFR Parts 1010 and 1020 and 12 CFR Part 3")
        self.assertEqual([(c["title"], c["canonical"]) for c in cs], [("31", "31 CFR Part 1010"), ("31", "31 CFR Part 1020"), ("12", "12 CFR Part 3")])
        cs = parse("31 CFR Part 1010 and 1020 and 31 CFR 1010.100")
        self.assertEqual([c["canonical"] for c in cs], ["31 CFR Part 1010", "31 CFR Part 1020", "31 CFR 1010.100"])
        self.assertEqual([c["canonical"] for c in parse("12 CFR Part 3 and 31 CFR Part 1010, Part 1020")], ["12 CFR Part 3", "31 CFR Part 1010", "31 CFR Part 1020"])

    def test_four_digit_to_range_is_one_unsupported_expression_with_the_complete_request(self):
        for q in RANGES:
            cs = parse(q)
            self.assertEqual(len(cs), 1, q)
            self.assertEqual((cs[0]["malformed"], cs[0]["canonical"]), ("unsupported_range", None), q)
            d = cs[0]["diagnostics"]
            self.assertEqual((d["accepted_valid_prefix"], d["interior_inferred"]), (False, False), q)
        c = parse("CET1 31 CFR Part 1010 to 1020")[0]
        self.assertEqual(c["requested"], "31 CFR Part 1010 to 1020")
        r = c["diagnostics"]["range"]
        self.assertEqual((r["start"], r["end"], r["end_requested"], r["end_form"], r["reversed"]), ("31 CFR Part 1010", "31 CFR Part 1020", "1020", "bare_part", False))
        self.assertTrue(parse("31 CFR Part 1020 to 1010")[0]["diagnostics"]["range"]["reversed"])
        self.assertNotIn("1015", json.dumps(c["diagnostics"]))  # no interior part is named

    def test_years_quantities_and_prose_stay_a_plain_part(self):
        for q in CONTROLS:
            cs = parse(q)
            self.assertEqual([c["malformed"] for c in cs], [None] * len(cs), q)
            self.assertEqual(len(cs), 1, q)

    def test_a_number_that_could_be_a_part_or_a_year_is_a_structured_ambiguity(self):
        c = parse("12 CFR Part 3 and 1950")
        self.assertEqual([x["malformed"] for x in c], [None, "ambiguous_part_or_year"])
        self.assertEqual((c[1]["requested"], c[1]["diagnostics"]["candidate_paths"], c[1]["diagnostics"]["inherits_from"]), ("1950", ["12 CFR Part 1950"], "12 CFR Part 3"))
        self.assertIsNone(c[1]["canonical"])
        r = parse("12 CFR Part 3 to 1999")
        self.assertEqual((len(r), r[0]["malformed"], r[0]["diagnostics"]["range"]["end_form"]), (1, "unsupported_range", "ambiguous_part_or_year"))
        self.assertEqual([x["canonical"] for x in parse("12 CFR Part 3 and Part 1950")], ["12 CFR Part 3", "12 CFR Part 1950"])  # the word Part settles it
        self.assertEqual([x["label"] for x in parse("12 CFR Part 3 to 2026")], ["12 CFR Part 3"])  # 2000 and later is a year

    def test_previous_list_range_and_section_behaviour_is_unchanged(self):
        self.assertEqual([c["canonical"] for c in parse("12 CFR Part 3 and 6")], ["12 CFR Part 3", "12 CFR Part 6"])
        self.assertEqual(parse("12 CFR Part 3 to 6")[0]["malformed"], "unsupported_range")
        self.assertEqual([c["canonical"] for c in parse("12 CFR 3.1, 6.1")], ["12 CFR 3.1", "12 CFR 6.1"])
        self.assertEqual(parse("12 CFR 3.1-6.1")[0]["malformed"], "unsupported_range")
        self.assertEqual(parse("31 CFR Part 1010")[0]["canonical"], "31 CFR Part 1010")

    def test_citation_only_accounts_for_every_operand(self):
        for q in list(LISTS) + list(RANGES[:7]):
            self.assertTrue(citation_only(q), q)
        self.assertFalse(citation_only("capital 31 CFR Parts 1010 and 1020"))


class FourDigitIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        ecfr = lambda t, p, name, html: {"source_class": "regulation", "publisher": "eCFR", "citation": f"{t} CFR Part {p}", "title": "T",
                                         "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                                         "asset": asset(root, name, html.encode())}
        docs = {"ecfr:12:3": ecfr(12, 3, "e3.html", ECFR_3), "ecfr:31:1010": ecfr(31, 1010, "e1010.html", ECFR_1010), "ecfr:31:1020": ecfr(31, 1020, "e1020.html", ECFR_1020)}
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "d.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def gaps(self, out):
        return sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))

    def sig(self, r):
        return (r["citation"], r["resolved"], r["reason"], r["by_scope"], r["_current_ids"], r["_future_ids"])

    def test_resolver_acquired_unacquired_and_filtered_members(self):
        cur = self.index.resolve_citations("31 CFR Parts 1010, 1020, and 1030", "current")
        self.assertEqual([(r["citation"], r["resolved"], r["reason"]) for r in cur],
                         [("31 CFR Part 1010", True, None), ("31 CFR Part 1020", True, None), ("31 CFR Part 1030", False, "part_not_acquired")])
        filt = self.index.resolve_citations("31 CFR Parts 1010 and 1020", "current", ["31:1010"])
        self.assertEqual([(r["resolved"], r["reason"]) for r in filt], [(True, None), (False, "excluded_by_part_filter")])
        for q, members in LISTS.items():
            for scope in SCOPES:
                for parts in PARTS:
                    recs = self.index.resolve_citations(q, scope, parts)
                    control = self.index.resolve_citations(" and ".join(members), scope, parts)
                    self.assertEqual([self.sig(r) for r in recs], [self.sig(r) for r in control], (q, scope, parts))

    def test_answers_match_the_control_with_a_gap_for_each_unresolved_member(self):
        for q in ("31 CFR Parts 1010 and 1020", "31 CFR Parts 1010, 1020, and 1030", "CET1 31 CFR Parts 1010 and 1030"):
            members = [c["canonical"] for c in parse(q)]
            control = ("CET1 " if q.startswith("CET1") else "") + " and ".join(members)
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            a, b = (self.index.answer(x, mode=mode, scope=scope, parts=parts, k=k) for x in (q, control))
                            tag = (q, mode, scope, parts, k)
                            self.assertEqual((a["status"], self.gaps(a)), (b["status"], self.gaps(b)), tag)
                            unresolved = [c["citation"] for c in a["citations"] if not c["resolved"]]
                            self.assertEqual(self.gaps(a), sorted(f"citation:{u}" for u in unresolved), tag)
                            if unresolved:
                                self.assertNotEqual(a["status"], "evidence_found", tag)
                            if mode == "exact":
                                self.assertEqual([r["provision_id"] for r in a["results"]], [r["provision_id"] for r in b["results"]], tag)

    def test_cet1_mixed_topic_cannot_conceal_an_omitted_operand(self):
        for q, gap in (("CET1 31 CFR Parts 1010 and 1030", "citation:31 CFR Part 1030"), ("CET1 capital 31 CFR Part 1010, 1020 and 1030", "citation:31 CFR Part 1030")):
            for mode in MODES:
                for scope in ("current", "both"):
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, k=k)
                        self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope, k))
                        self.assertIn(gap, self.gaps(out), (q, mode, scope, k))
        out = self.index.answer("CET1 31 CFR Parts 1010 and 1020", mode="hybrid", scope="current", parts=["31:1010"])
        self.assertIn("citation:31 CFR Part 1020", self.gaps(out))

    def test_delta_for_lists_and_ranges(self):
        for q, members in LISTS.items():
            d = self.index.delta(q)
            self.assertEqual(d["evidence_status"], "unsupported_multi_citation", q)
            self.assertEqual((d["baseline"], d["instruments"], d["section_context"]), (None, [], None), q)
            self.assertEqual([c["label"] for c in d["unavailable"]["citations"]], members, q)
        for q in RANGES:
            d = self.index.delta(q)
            self.assertEqual((d["evidence_status"], d["baseline"], d["section_context"], d["instruments"]), ("unavailable", None, None, []), q)
            self.assertEqual(d["unavailable"]["reason"], "unsupported_range", q)
        self.assertEqual(self.index.delta("31 CFR Part 1010")["evidence_status"], "available")

    def test_ranges_resolve_nothing_and_never_report_evidence_found(self):
        for q in RANGES:
            expr = parse(q)[0]["label"]
            for scope in SCOPES:
                for parts in PARTS:
                    recs = self.index.resolve_citations(q, scope, parts)
                    self.assertEqual([(r["reason"], r["_current_ids"], r["_future_ids"]) for r in recs], [("unsupported_range", [], [])], (q, scope, parts))
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (q, mode, scope, parts, k)
                            self.assertNotEqual(out["status"], "evidence_found", tag)
                            self.assertIn(f"citation:{expr}", self.gaps(out), tag)
                            if mode == "exact":
                                self.assertEqual((out["results"], out["status"]), ([], "missing_evidence"), tag)

    def test_ambiguous_part_or_year_has_its_own_gap_and_controls_stay_plain(self):
        out = self.index.answer("12 CFR Part 3 and 1950", mode="exact", scope="current")
        self.assertEqual(self.gaps(out), ["citation:1950 after 12 CFR Part 3"])
        self.assertNotEqual(out["status"], "evidence_found")
        plain = self.index.answer("12 CFR Part 3", mode="exact", scope="current")
        for q in CONTROLS[:4]:
            o = self.index.answer(q, mode="exact", scope="current")
            self.assertEqual([r["provision_id"] for r in o["results"]], [r["provision_id"] for r in plain["results"]], q)
            self.assertEqual(self.gaps(o), [], q)

    def test_subsequent_independent_citation_survives(self):
        out = self.index.answer("31 CFR Part 1010 to 1020 and 12 CFR Part 3", mode="exact", scope="current")
        self.assertEqual(self.gaps(out), ["citation:31 CFR Part 1010 to 1020"])
        self.assertTrue([c for c in out["citations"] if c["citation"] == "12 CFR Part 3" and c["resolved"]])
        self.assertEqual(self.index.delta("31 CFR Part 1010 to 1020 and 12 CFR Part 3")["evidence_status"], "unsupported_multi_citation")

    def test_evaluator_group_is_registered_and_fails_at_the_starting_commit_behaviour(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("four_digit_parts", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["four_digit_parts"]], [f"FD0{n}" for n in range(1, 8)])
        lst = {"id": "T1", "kind": "equivalent", "shorthand": "31 CFR Parts 1010 and 1020", "control": "31 CFR Part 1010 and 31 CFR Part 1020",
               "members": ["31 CFR Part 1010", "31 CFR Part 1020"], "complete": True}
        rng = {"id": "T2", "kind": "unsupported", "requests": ["31 CFR Part 1010 to 1020"]}
        self.assertTrue(evaluate.check_four_digit_parts(self.index, lst)["passed"])
        self.assertTrue(evaluate.check_four_digit_parts(self.index, rng)["passed"])
        real = search.parse_query_citations
        # the starting behaviour: operands of four digits were dropped, leaving the first part as the whole request
        with mock.patch.object(evaluate, "parse_query_citations", lambda q: [c for c in real(q) if c["inherited"] is None and not c["malformed"]][:1] if "1020" in q else real(q)):
            self.assertFalse(evaluate.check_four_digit_parts(self.index, lst)["passed"])
            self.assertFalse(evaluate.check_four_digit_parts(self.index, rng)["passed"])

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, status)


if __name__ == "__main__":
    unittest.main()
