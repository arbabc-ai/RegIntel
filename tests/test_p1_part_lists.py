"""Tests for S7-P1-PART-LIST-COMPLETENESS-23 (F1): every member of a singular or plural part list is preserved
(`12 CFR Part 3 and 6`, `Part 3, Part 6`, `Parts 3 and 6`, `Parts 3, 6, and 217`, semicolon, Oxford comma, or). Members inherit
the title only from an established citation context, resolve independently, and an unacquired or filtered member has its
own citation gap, also next to another topic. delta() reports unsupported_multi_citation with every distinct member;
plural ranges stay one unsupported range; quantities and prose are not members."""
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
LISTS = {"12 CFR Part 3 and 6": ["12 CFR Part 3", "12 CFR Part 6"], "12 CFR Part 3, Part 6": ["12 CFR Part 3", "12 CFR Part 6"],
         "12 CFR Part 3 and Part 6": ["12 CFR Part 3", "12 CFR Part 6"], "12 CFR Parts 3 and 6": ["12 CFR Part 3", "12 CFR Part 6"],
         "12 CFR Part 3; Part 6": ["12 CFR Part 3", "12 CFR Part 6"], "12 CFR Part 3 or 6": ["12 CFR Part 3", "12 CFR Part 6"],
         "12 CFR Parts 3, 6, and 217": ["12 CFR Part 3", "12 CFR Part 6", "12 CFR Part 217"], "12 CFR Parts 3, 6, or 217": ["12 CFR Part 3", "12 CFR Part 6", "12 CFR Part 217"],
         "12 CFR Part 3, 6; and 217": ["12 CFR Part 3", "12 CFR Part 6", "12 CFR Part 217"], "12 CFR Part 3, 6 and 217.": ["12 CFR Part 3", "12 CFR Part 6", "12 CFR Part 217"]}
PROSE = ("12 CFR Part 3 and 6 banks", "12 CFR Part 3 and 6 percent", "12 CFR Part 3, 2026", "12 CFR Part 3, 6 apply", "12 CFR Part 3 and the rule", "12 CFR Part 3 to 6 banks")


class ParserTests(unittest.TestCase):
    def test_every_member_of_every_list_form_is_a_citation(self):
        for q, want in LISTS.items():
            self.assertEqual([c["canonical"] for c in parse(q)], want, q)
            self.assertTrue(all(c["malformed"] is None for c in parse(q)), q)

    def test_requested_text_and_inheritance_provenance_are_kept(self):
        a, b = parse("CET1 12 CFR Parts 3 and 6")
        self.assertEqual((a["requested"], b["requested"]), ("12 CFR Parts 3", "6"))
        self.assertEqual(b["inherited"], {"kind": "part", "from": "12 CFR Part 3", "title_inherited": "12"})
        self.assertIsNone(a["inherited"])
        c = parse("12 CFR Part 3, Part 6, and Part 217")
        self.assertEqual([x["requested"] for x in c], ["12 CFR Part 3", "Part 6", "Part 217"])
        self.assertEqual(c[2]["inherited"]["from"], "12 CFR Part 6")  # each member inherits from the one before it

    def test_explicit_titles_are_retained_and_never_overwritten(self):
        cs = parse("12 CFR Part 3 and 31 CFR Part 1010, Part 1020")
        self.assertEqual([(c["title"], c["canonical"]) for c in cs], [("12", "12 CFR Part 3"), ("31", "31 CFR Part 1010"), ("31", "31 CFR Part 1020")])
        self.assertIsNone(cs[1]["inherited"])
        self.assertEqual(cs[2]["inherited"]["title_inherited"], "31")

    def test_title_is_inherited_only_from_an_established_context(self):
        c = parse("§ 3.1 and Part 6")
        self.assertEqual([x["malformed"] for x in c], [None, "part_without_title_context"])
        self.assertIsNone(c[1]["canonical"])
        self.assertEqual(c[1]["diagnostics"]["inherits_from"], "§ 3.1")
        self.assertEqual(parse("12 CFR 3.1 and Part 6")[1]["canonical"], "12 CFR Part 6")  # a titled section context does establish it

    def test_plural_ranges_stay_one_unsupported_range(self):
        for q in ("12 CFR Parts 3 through 6", "12 CFR Parts 3-6", "12 CFR Parts 3 to Part 6", "12 CFR Parts 3 thru"):
            cs = parse(q)
            self.assertEqual(len(cs), 1, q)
            self.assertEqual((cs[0]["malformed"], cs[0]["diagnostics"]["interior_inferred"]), ("unsupported_range", False), q)
        self.assertEqual(parse("12 CFR Parts 3 through 6")[0]["requested"], "12 CFR Parts 3 through 6")
        mixed = parse("12 CFR Part 3, Part 6 through 8")
        self.assertEqual([(c["malformed"]) for c in mixed], [None, "unsupported_range"])
        self.assertEqual(mixed[1]["diagnostics"]["range"]["start"], "12 CFR Part 6")

    def test_independent_citations_and_other_lists_are_preserved(self):
        cs = parse("12 CFR Part 3 and 6 and 12 CFR 3.22(a)")
        self.assertEqual([c["canonical"] for c in cs], ["12 CFR Part 3", "12 CFR Part 6", "12 CFR 3.22(a)"])
        self.assertEqual([c["canonical"] for c in parse("12 CFR 3.1, 6.1")], ["12 CFR 3.1", "12 CFR 6.1"])
        self.assertEqual([c["canonical"] for c in parse("12 CFR 3.22(a) and (z)")], ["12 CFR 3.22(a)", "12 CFR 3.22(z)"])
        self.assertEqual([c["canonical"] for c in parse("12 CFR Part 3 and 6.1")], ["12 CFR Part 3", "12 CFR 6.1"])
        self.assertEqual(parse("12 CFR Part 3-6")[0]["malformed"], "unsupported_range")
        self.assertEqual(parse("12 CFR Part 3")[0]["canonical"], "12 CFR Part 3")

    def test_quantities_years_and_prose_are_not_members(self):
        for q in PROSE:
            cs = parse(q)
            self.assertEqual([c["canonical"] for c in cs], ["12 CFR Part 3"], q)
            self.assertEqual([c["malformed"] for c in cs], [None], q)

    def test_citation_only_accounts_for_every_member(self):
        for q in LISTS:
            self.assertTrue(citation_only(q), q)
        self.assertFalse(citation_only("capital 12 CFR Part 3 and 6"))


class PartListIndexTests(unittest.TestCase):
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
        cls.db = str(Path(cls.tmp.name) / "l.sqlite")
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

    def test_resolver_gives_each_member_its_own_independent_record(self):
        for q, members in LISTS.items():
            for scope in SCOPES:
                for parts in PARTS:
                    recs = self.index.resolve_citations(q, scope, parts)
                    self.assertEqual([r["citation"] for r in recs], members, (q, scope, parts))
                    control = self.index.resolve_citations(" and ".join(members), scope, parts)  # fully qualified control
                    self.assertEqual([self.sig(r) for r in recs], [self.sig(r) for r in control], (q, scope, parts))
        cur = self.index.resolve_citations("12 CFR Parts 3, 6, and 217", "current")
        self.assertEqual([(r["resolved"], r["reason"]) for r in cur], [(True, None), (True, None), (False, "part_not_acquired")])
        filt = self.index.resolve_citations("12 CFR Part 3 and 6", "current", ["12:3"])
        self.assertEqual([(r["resolved"], r["reason"]) for r in filt], [(True, None), (False, "excluded_by_part_filter")])

    def test_answers_match_the_fully_qualified_control_with_a_gap_per_unresolved_member(self):
        for q in ("12 CFR Part 3 and 6", "12 CFR Parts 3, 6, and 217", "12 CFR Part 3, Part 217"):
            members = [c["canonical"] for c in parse(q)]
            control = " and ".join(members)
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            a, b = (self.index.answer(x, mode=mode, scope=scope, parts=parts, k=k) for x in (q, control))
                            tag = (q, mode, scope, parts, k)
                            self.assertEqual((a["status"], self.gaps(a)), (b["status"], self.gaps(b)), tag)
                            if mode == "exact":
                                self.assertEqual([r["provision_id"] for r in a["results"]], [r["provision_id"] for r in b["results"]], tag)
                            unresolved = [c["citation"] for c in a["citations"] if not c["resolved"]]
                            self.assertEqual(self.gaps(a), sorted(f"citation:{u}" for u in unresolved), tag)
                            if unresolved:
                                self.assertNotEqual(a["status"], "evidence_found", tag)

    def test_unacquired_and_filtered_members_are_never_hidden(self):
        out = self.index.answer("12 CFR Part 3 and 6", mode="exact", scope="current")
        self.assertEqual((out["status"], self.gaps(out)), ("evidence_found", []))
        miss = self.index.answer("12 CFR Part 3 and 217", mode="exact", scope="current")
        self.assertEqual((miss["status"], self.gaps(miss)), ("partial_evidence", ["citation:12 CFR Part 217"]))
        flt = self.index.answer("12 CFR Part 3 and 6", mode="exact", scope="current", parts=["12:3"])
        self.assertEqual((flt["status"], self.gaps(flt)), ("partial_evidence", ["citation:12 CFR Part 6"]))
        self.assertEqual({r["doc_key"] for r in flt["results"]}, {"ecfr:12:3"})

    def test_mixed_topic_queries_keep_the_member_gaps(self):
        for q in ("CET1 12 CFR Part 3 and 217", "CET1 capital 12 CFR Parts 3, 6 and 217"):
            for mode in MODES:
                for scope in ("current", "both"):
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, k=k)
                        self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope, k))
                        self.assertIn("citation:12 CFR Part 217", self.gaps(out), (q, mode, scope, k))
        out = self.index.answer("CET1 12 CFR Part 3 and 6", mode="hybrid", scope="current", parts=["12:3"])
        self.assertIn("citation:12 CFR Part 6", self.gaps(out))

    def test_explicit_cross_title_members_stay_separate(self):
        recs = self.index.resolve_citations("12 CFR Part 3 and 31 CFR Part 3", "current")
        self.assertEqual([r["citation"] for r in recs], ["12 CFR Part 3", "31 CFR Part 3"])
        self.assertTrue(all(r["resolved"] for r in recs))
        docs = lambda r: {self.index.evidence(i)["doc_key"] for i in r["_current_ids"]}
        self.assertEqual((docs(recs[0]), docs(recs[1])), ({"ecfr:12:3"}, {"ecfr:31:3"}))
        flt = self.index.answer("12 CFR Part 3 and 31 CFR Part 3", mode="exact", scope="current", parts=["31:3"])
        self.assertEqual(self.gaps(flt), ["citation:12 CFR Part 3"])

    def test_delta_reports_unsupported_multi_citation_with_every_member(self):
        for q, members in LISTS.items():
            d = self.index.delta(q)
            self.assertEqual(d["evidence_status"], "unsupported_multi_citation", q)
            self.assertEqual((d["baseline"], d["instruments"], d["section_context"]), (None, [], None), q)
            self.assertEqual([c["label"] for c in d["unavailable"]["citations"]], members, q)
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))
        self.assertEqual(self.index.delta("12 CFR Parts 3 and 6")["unavailable"]["citations"][1]["inherited"]["kind"], "part")
        self.assertEqual(self.index.delta("12 CFR Part 3")["evidence_status"], "available")

    def test_plural_ranges_are_one_unsupported_expression_everywhere(self):
        for q in ("12 CFR Parts 3 through 6", "12 CFR Parts 3-6"):
            expr = parse(q)[0]["label"]
            for scope in SCOPES:
                recs = self.index.resolve_citations(q, scope)
                self.assertEqual([(r["reason"], r["_current_ids"], r["_future_ids"]) for r in recs], [("unsupported_range", [], [])])
            for mode in MODES:
                out = self.index.answer(q, mode=mode, scope="both", k=10)
                self.assertNotEqual(out["status"], "evidence_found")
                self.assertIn(f"citation:{expr}", self.gaps(out))
            self.assertEqual(self.index.answer(q, mode="exact", scope="both")["results"], [])
            d = self.index.delta(q)
            self.assertEqual((d["evidence_status"], d["baseline"], d["instruments"]), ("unavailable", None, []))

    def test_independent_citation_after_the_list_and_prose_controls(self):
        out = self.index.answer("12 CFR Part 3 and 217 and 12 CFR 3.22(a)", mode="exact", scope="current")
        self.assertEqual(self.gaps(out), ["citation:12 CFR Part 217"])
        self.assertTrue([c for c in out["citations"] if c["citation"] == "12 CFR 3.22(a)" and c["resolved"]])
        plain = self.index.answer("12 CFR Part 3", mode="exact", scope="current")
        for q in PROSE[:5]:
            o = self.index.answer(q, mode="exact", scope="current")
            self.assertEqual([r["provision_id"] for r in o["results"]], [r["provision_id"] for r in plain["results"]], q)
            self.assertEqual(self.gaps(o), [], q)

    def test_evaluator_group_is_registered_and_fails_at_the_reviewed_head(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("part_lists", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["part_lists"]], [f"PL0{n}" for n in range(1, 6)])
        item = {"id": "T1", "kind": "equivalent", "shorthand": "12 CFR Parts 3 and 6", "control": "12 CFR Part 3 and 12 CFR Part 6",
                "members": ["12 CFR Part 3", "12 CFR Part 6"], "complete": True}
        self.assertTrue(evaluate.check_query_lists(self.index, item)["passed"])
        real = search.parse_query_citations
        with mock.patch.object(evaluate, "parse_query_citations", lambda q: [c for c in real(q) if not c["inherited"]]):  # members after the first vanish
            self.assertFalse(evaluate.check_query_lists(self.index, item)["passed"])

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, status)


if __name__ == "__main__":
    unittest.main()
