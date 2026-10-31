"""Tests for S7-P1-MALFORMED-LIST-RECOVERY-30: after an unresolved identifier, hyphen tail or designator suffix, reading resumes at the
next clear list separator. The unresolved expression keeps its own reason, diagnostics and gap; independently readable section or
part members after it are still requested, resolved and retrievable; paragraph shorthand after an unresolved expression stays
unresolved; delta reports unsupported_multi_citation with every member."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.search import citation_only, parse_query_citations as parse
from test_p1 import asset
from test_p1_explicit_title_section_sign import ECFR_1010, KEYS, Z31
from test_p1_query_lists import ECFR_3, ECFR_6, ECFR_31, F1

MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")
PARTS = (None, ["12:3"], ["31:3"], ["12:6"])
UC, HT, DS = "unread_identifier_continuation", "unread_hyphen_tail", "unclosed_or_malformed_designator_suffix"
CASES = {  # request -> [(label, reason or None)]
    "12 CFR 3.22.1 and 3.12": [("12 CFR 3.22.1", UC), ("12 CFR 3.12", None)],
    "12 CFR 3.22-a and 3.12": [("12 CFR 3.22-a", HT), ("12 CFR 3.12", None)],
    "12 CFR 3.1 and 3.22.1 and 6.1": [("12 CFR 3.1", None), ("12 CFR 3.22.1", UC), ("12 CFR 6.1", None)],
    "12 CFR Parts 3_a and 6": [("12 CFR Parts 3_a", UC), ("12 CFR Part 6", None)],
    "12 CFR Parts 3-a and 6": [("12 CFR Parts 3-a", HT), ("12 CFR Part 6", None)],
    "12 CFR 3.22(a)(999 and 3.1": [("12 CFR 3.22(a)(999", DS), ("12 CFR 3.1", None)],
}


class ParserTests(unittest.TestCase):
    def test_required_reproductions_keep_every_member(self):
        for q, want in CASES.items():
            self.assertEqual([(c["label"], c["malformed"]) for c in parse(q)], want, q)
        for q in ("12 CFR 3.22.1 and 217.2", "12 CFR Parts 3_a and 217", "12 CFR Parts 3-a and 217", "12 CFR 3.1 and 3.22.1 and 217.2"):
            self.assertTrue(any(c["canonical"] and "217" in c["canonical"] for c in parse(q)), q)

    def test_unresolved_expressions_keep_their_reason_diagnostics_and_original_text(self):
        a, b = parse("12 CFR 3.22.1 and 3.12")
        self.assertEqual((a["requested"], a["diagnostics"]["unread_continuation"], a["diagnostics"]["accepted_valid_prefix"]), ("12 CFR 3.22.1", ".1", False))
        self.assertEqual((b["requested"], b["inherited"]["kind"], b["inherited"]["title_inherited"]), ("3.12", "section", "12"))
        a, b = parse("12 CFR 3.22-a and 3.12")
        self.assertEqual((a["diagnostics"]["unread_continuation"], a["diagnostics"]["unread_tail_kind"]), ("-a", "hyphen"))
        a, b = parse("12 CFR 3.22(a)(999 and 3.1")
        self.assertEqual(a["diagnostics"]["unread_suffix"], "(999")

    def test_title_context_is_retained_but_a_malformed_prefix_is_never_accepted(self):
        cs = parse("31 CFR 3.22.1 and 3.12")
        self.assertEqual([(c["title"], c["canonical"] if not c["malformed"] else None) for c in cs], [("31", None), ("31", "31 CFR 3.12")])
        cs = parse("31 C.F.R. § 3.22-a and 3.12 and 12 CFR 3.1")
        self.assertEqual([(c["title"], c["malformed"]) for c in cs], [("31", HT), ("31", None), ("12", None)])
        self.assertEqual([c["canonical"] for c in parse("31 CFR Parts 1010_a and 1020")][1], "31 CFR Part 1020")

    def test_separators_and_middle_and_trailing_members(self):
        for sep in (" and ", ", ", "; ", " or ", ", and ", ", or "):
            cs = parse(f"12 CFR 3.1{sep}3.22.1{sep}6.1")
            self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR 3.1", None), ("12 CFR 3.22.1", UC), ("12 CFR 6.1", None)], sep)
            cs = parse(f"12 CFR 3.22-a{sep}3.12{sep}6.1")
            self.assertEqual([c["malformed"] for c in cs], [HT, None, None], sep)
        cs = parse("12 CFR 3.22.1 and 3.12-a and 6.1")  # two unresolved members with a valid one after them
        self.assertEqual([c["malformed"] for c in cs], [UC, HT, None])

    def test_paragraph_shorthand_after_an_unresolved_expression_stays_unresolved(self):
        for q in ("12 CFR 3.22.1 and (a)", "12 CFR 3.22-a and (a)"):
            cs = parse(q)
            self.assertEqual([c["malformed"] for c in cs][1:], ["ambiguous_shorthand"], q)  # no paragraph is inherited from an unresolved expression
            self.assertFalse(any(c["canonical"] and c["canonical"].endswith("(a)") for c in cs), q)
        cs = parse("12 CFR 3.22(a)(999 and 3.12 and (z)")  # (z) follows 3.12, which names no paragraph to inherit from
        self.assertEqual([c["malformed"] for c in cs], [DS, None, "shorthand_without_paragraph_context"])
        self.assertIsNone(cs[2]["canonical"])

    def test_a_parenthetical_inside_an_unclosed_designator_is_not_a_member(self):
        self.assertEqual([c["malformed"] for c in parse("CET1 12 CFR 3.22(a)(introd and (b)")], [DS])
        self.assertEqual([c["malformed"] for c in parse("12 CFR 3.22(a)(999, and (z)")], [DS])

    def test_valid_citations_ranges_and_controls_are_unchanged(self):
        self.assertEqual([c["canonical"] for c in parse("12 CFR 3.1, 6.1")], ["12 CFR 3.1", "12 CFR 6.1"])
        self.assertEqual([c["canonical"] for c in parse("12 CFR Parts 3, 6, and 217")], ["12 CFR Part 3", "12 CFR Part 6", "12 CFR Part 217"])
        self.assertEqual([c["malformed"] for c in parse("12 CFR 3.1-6.1 and 3.12")], ["unsupported_range", None])
        self.assertEqual([c["malformed"] for c in parse("12 CFR 3.22(a) and (z)")], [None, None])
        for q in ("12 CFR Part 3 and 6 banks", "12 CFR Part 3 to 2026", "12 CFR 3.22 - a rule", "12 CFR 3.22."):
            self.assertEqual(len(parse(q)), 1, q)
        self.assertEqual(len(parse("12 CFR 3.22.1")), 1)

    def test_citation_only_accounts_for_every_member(self):
        for q in CASES:
            self.assertTrue(citation_only(q), q)
        self.assertFalse(citation_only("capital 12 CFR 3.22.1 and 3.12"))


class IndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01", "document_type": "Rule",
                "effective_on": "2027-02-01", "action": "Final rule."}
        ecfr = lambda t, p, name, html: {"source_class": "regulation", "publisher": "eCFR", "citation": f"{t} CFR Part {p}", "title": "T",
                                         "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28", "asset": asset(root, name, html.encode())}
        docs = {"ecfr:12:3": ecfr(12, 3, "e3.html", ECFR_3), "ecfr:12:6": ecfr(12, 6, "e6.html", ECFR_6), "ecfr:31:3": ecfr(31, 3, "e31.html", ECFR_31),
                "ecfr:31:1010": ecfr(31, 1010, "e1010.html", ECFR_1010),
                KEYS[0]: {**meta, "document_number": KEYS[0][3:], "title": "F12", "asset": asset(root, "f12.xml", F1)},
                KEYS[1]: {**meta, "document_number": KEYS[1][3:], "title": "F31", "asset": asset(root, "f31.xml", Z31)}}
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

    def valid_control(self, q):
        return " and ".join(c["canonical"] for c in parse(q) if not c["malformed"])

    def test_resolver_gives_every_member_its_own_record(self):
        for q, want in CASES.items():
            for scope in SCOPES:
                for parts in PARTS:
                    recs = self.index.resolve_citations(q, scope, parts)
                    self.assertEqual([r["citation"] for r in recs], [w[0] for w in want], (q, scope, parts))
                    for r, (_, reason) in zip(recs, want):
                        if reason:
                            self.assertEqual((r["resolved"], r["reason"], r["_current_ids"], r["_future_ids"], r["legal_validity"]), (False, reason, [], [], "not_determined"), (q, r["citation"]))
        cur = self.index.resolve_citations("12 CFR 3.22.1 and 3.12", "current")
        self.assertEqual((cur[1]["citation"], cur[1]["reason"]), ("12 CFR 3.12", "not_found_in_acquired_part"))  # the valid member is looked up, not hidden
        ok = self.index.resolve_citations("12 CFR 3.1 and 3.22.1 and 6.1", "current")
        self.assertEqual([r["resolved"] for r in ok], [True, False, True])

    def test_answers_keep_a_gap_for_each_unresolved_member_and_the_valid_members_remain_retrievable(self):
        for q, want in CASES.items():
            unresolved = sorted(f"citation:{l}" for l, r in want if r)
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (q, mode, scope, parts, k)
                            self.assertNotEqual(out["status"], "evidence_found", tag)
                            for gap in unresolved:
                                self.assertIn(gap, self.gaps(out), tag)
                            self.assertEqual(out["applicability_status"], "not_assessed")
        out = self.index.answer("12 CFR 3.1 and 3.22.1 and 6.1", mode="exact", scope="current", k=10)
        self.assertEqual(self.gaps(out), ["citation:12 CFR 3.22.1"])
        self.assertEqual({r["doc_key"] for r in out["results"]}, {"ecfr:12:3", "ecfr:12:6"})  # 3.1 and 6.1 are retrievable
        control = self.index.answer(self.valid_control("12 CFR 3.1 and 3.22.1 and 6.1"), mode="exact", scope="current", k=10)
        self.assertEqual([r["provision_id"] for r in out["results"]], [r["provision_id"] for r in control["results"]])  # nothing from 3.22 or its prefix
        part = self.index.answer("12 CFR Parts 3_a and 6", mode="exact", scope="current")
        self.assertEqual(({r["doc_key"] for r in part["results"]}, self.gaps(part)), ({"ecfr:12:6"}, ["citation:12 CFR Parts 3_a"]))

    def test_delta_reports_unsupported_multi_citation_with_every_member(self):
        for q, want in CASES.items():
            d = self.index.delta(q)
            self.assertEqual(d["evidence_status"], "unsupported_multi_citation", q)
            self.assertEqual((d["baseline"], d["instruments"], d["section_context"]), (None, [], None), q)
            self.assertEqual([c["label"] for c in d["unavailable"]["citations"]], [w[0] for w in want], q)
            self.assertEqual([c["unresolved_reason"] for c in d["unavailable"]["citations"]], [w[1] for w in want], q)
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))

    def test_explicit_title_recovery_and_wrong_title_members(self):
        recs = self.index.resolve_citations("31 CFR 3.22.1 and 3.12", "current")
        self.assertEqual([(r["citation"], r["resolved"]) for r in recs], [("31 CFR 3.22.1", False), ("31 CFR 3.12", False)])
        self.assertEqual(recs[1]["title_resolution"]["title"], "31")  # the title context is kept, so 3.12 is not resolved through Title 12
        out = self.index.answer("31 CFR 3.22.1 and 3.22", mode="exact", scope="current")
        self.assertEqual({r["doc_key"] for r in out["results"]}, {"ecfr:31:3"})
        self.assertEqual(self.gaps(out), ["citation:31 CFR 3.22.1"])
        mixed = self.index.answer("31 CFR 3.22-a and 12 CFR 3.1", mode="exact", scope="current")
        self.assertEqual(({r["doc_key"] for r in mixed["results"]}, self.gaps(mixed)), ({"ecfr:12:3"}, ["citation:31 CFR 3.22-a"]))

    def test_cet1_mixed_queries_cannot_conceal_the_unresolved_member(self):
        for q in ("CET1 12 CFR 3.22.1 and 3.1", "CET1 capital 12 CFR 3.1 and 3.22-a and 6.1"):
            label = [c["label"] for c in parse(q) if c["malformed"]][0]
            for mode in MODES:
                for scope in SCOPES:
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, k=k)
                        self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope, k))
                        self.assertIn(f"citation:{label}", self.gaps(out), (q, mode, scope, k))

    def test_shorthand_after_an_unresolved_member_stays_unresolved_through_the_resolver(self):
        recs = self.index.resolve_citations("12 CFR 3.22.1 and (a)", "current")
        self.assertEqual([(r["citation"], r["reason"]) for r in recs], [("12 CFR 3.22.1", UC), ("(a) after 12 CFR 3.22.1", "ambiguous_shorthand")])
        out = self.index.answer("12 CFR 3.22.1 and (a)", mode="exact", scope="current")
        self.assertEqual(out["results"], [])

    def test_valid_lists_ranges_and_single_unresolved_requests_behave_as_before(self):
        self.assertEqual(self.index.answer("12 CFR 3.1, 6.1", mode="exact", scope="current")["status"], "evidence_found")
        single = self.index.delta("12 CFR 3.22.1")
        self.assertEqual((single["evidence_status"], single["baseline"], single["instruments"]), ("unavailable", None, []))
        rng = self.index.answer("12 CFR 3.1-6.1 and 3.22", mode="exact", scope="current")
        self.assertEqual(self.gaps(rng), ["citation:12 CFR 3.1-6.1"])

    def test_evaluator_group_is_registered_and_fails_at_the_starting_behaviour(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("list_recovery", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["list_recovery"]], [f"LR0{n}" for n in range(1, 7)])
        items = [{"id": "T1", "request": "12 CFR 3.1 and 3.22.1 and 6.1", "members": [["12 CFR 3.1", None], ["12 CFR 3.22.1", UC], ["12 CFR 6.1", None]]},
                 {"id": "T2", "request": "12 CFR Parts 3-a and 6", "members": [["12 CFR Parts 3-a", HT], ["12 CFR Part 6", None]]}]
        for x in items:
            self.assertTrue(evaluate.check_list_recovery(self.index, x)["passed"], x["id"])
        real = search.parse_query_citations
        stops = lambda q: (lambda cs: cs[:next((i + 1 for i, c in enumerate(cs) if c["malformed"]), len(cs))])(real(q))  # the starting parser: nothing after an unresolved member
        with mock.patch.object(search, "parse_query_citations", stops), mock.patch.object(evaluate, "parse_query_citations", stops):
            for x in items:
                self.assertFalse(evaluate.check_list_recovery(self.index, x)["passed"], x["id"])

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, status)


CORPUS = Path("/home/arbab/projects/regintel-corpus/phase1")


@unittest.skipUnless((CORPUS / "manifest.json").exists(), "public corpus not present")
class PublicCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = str(Path(cls.tmp.name) / "p.sqlite")
        conn, _ = build.build(CORPUS, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def gaps(self, out):
        return sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))

    def test_required_reproductions_on_the_public_corpus(self):
        for q, bad, good in (("12 CFR 3.22.1 and 3.12", "12 CFR 3.22.1", "12 CFR 3.12"), ("12 CFR 3.22-a and 3.12", "12 CFR 3.22-a", "12 CFR 3.12"),
                             ("12 CFR 3.1 and 3.22.1 and 217.2", "12 CFR 3.22.1", "12 CFR 217.2"), ("12 CFR Parts 3_a and 217", "12 CFR Parts 3_a", "12 CFR Part 217"),
                             ("12 CFR Parts 3-a and 217", "12 CFR Parts 3-a", "12 CFR Part 217"), ("12 CFR 3.22(a)(999 and 3.12", "12 CFR 3.22(a)(999", "12 CFR 3.12")):
            labels = [c["label"] for c in parse(q)]
            self.assertIn(bad, labels, q)
            self.assertIn(good, labels, q)  # every requested member is accounted for
            for mode in MODES:
                for scope in SCOPES:
                    out = self.index.answer(q, mode=mode, scope=scope, k=1)
                    self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope))
                    self.assertIn(f"citation:{bad}", self.gaps(out), (q, mode, scope))
            d = self.index.delta(q)
            self.assertEqual((d["evidence_status"], d["baseline"], d["instruments"]), ("unsupported_multi_citation", None, []), q)
            self.assertIn(good, [c["label"] for c in d["unavailable"]["citations"]], q)
        # an acquired valid member stays retrievable (Part 217 and 12 CFR 3.1 are acquired)
        out = self.index.answer("12 CFR Parts 3_a and 217", mode="exact", scope="current")
        self.assertTrue({r["doc_key"] for r in out["results"]} == {"ecfr:12:217"} and self.gaps(out) == ["citation:12 CFR Parts 3_a"])


if __name__ == "__main__":
    unittest.main()
