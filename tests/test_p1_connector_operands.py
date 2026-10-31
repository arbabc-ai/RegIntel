"""Tests for S7-P1-CONNECTOR-DESIGNATOR-INTEGRITY-20 (F1): a malformed parenthesized operand after a list separator
(`and (-1)`, `; (.)`, `or (z`) or a range operator (`through (-1)`, `to (z`) is preserved, never dropped. In a list it is its
own unresolved member beside the valid one; in a range the whole expression is one unresolved unsupported range. Nothing is
inferred and the valid first citation is never accepted as the whole request."""
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
UNREAD = "unclosed_or_malformed_designator_suffix"
BAD_OPERANDS = ("(-1)", "(.)", "(*)", "(-1", "(z", "(")
SEPARATORS = (" and ", ", ", "; ", " or ", ", and ", ", or ")
RANGE_OPS = (" through ", " thru ", " to ", "-", "–", " — ")
LIST_REQUESTS = ("CET1 12 CFR 3.22(a) and (-1)", "CET1 12 CFR 3.22(a); (.)", "CET1 12 CFR 3.22(a) or (z", "CET1 12 CFR 3.22(a), (*)")
RANGE_REQUESTS = ("CET1 12 CFR 3.22(a) through (-1)", "CET1 12 CFR 3.22(a) through (z", "CET1 12 CFR 3.22(a) to (-1)", "CET1 12 CFR 3.22(a) thru (.)",
                  "CET1 12 CFR 3.22(a)–(-1)", "CET1 12 CFR 3.22(a)-(z")


class ParserTests(unittest.TestCase):
    def test_required_reproductions(self):
        cs = parse("CET1 12 CFR 3.22(a) and (-1)")
        self.assertEqual([(c["label"], c["malformed"], c["requested"]) for c in cs],
                         [("12 CFR 3.22(a)", None, "12 CFR 3.22(a)"), ("(-1) after 12 CFR 3.22(a)", UNREAD, "(-1)")])
        d = cs[1]["diagnostics"]
        self.assertEqual((d["expression"], d["inherits_from"], d["accepted_valid_prefix"], d["unread_suffix"]), ("(-1)", "12 CFR 3.22(a)", False, "(-1)"))
        self.assertIsNone(cs[1]["canonical"])
        for q, tail in (("CET1 12 CFR 3.22(a) through (-1)", "(-1)"), ("CET1 12 CFR 3.22(a) through (z", "(z")):
            (c,) = parse(q)
            self.assertEqual((c["malformed"], c["requested"], c["label"]), ("unsupported_range", f"12 CFR 3.22(a) through {tail}", f"12 CFR 3.22(a) through {tail}"), q)
            r = c["diagnostics"]["range"]
            self.assertEqual((r["end_form"], r["end"], r["end_requested"], r["start"]), ("malformed_endpoint", None, tail, "12 CFR 3.22(a)"))
            self.assertEqual((c["diagnostics"]["accepted_valid_prefix"], c["diagnostics"]["interior_inferred"]), (False, False))

    def test_list_separators_keep_the_valid_member_and_the_malformed_operand_separately(self):
        for sep in SEPARATORS:
            for bad in BAD_OPERANDS:
                q = f"12 CFR 3.22(a){sep}{bad}"
                cs = parse(q)
                self.assertEqual(len(cs), 2, q)
                self.assertEqual((cs[0]["label"], cs[0]["malformed"]), ("12 CFR 3.22(a)", None), q)
                self.assertEqual((cs[1]["malformed"], cs[1]["requested"]), (UNREAD, bad), q)
                self.assertFalse(cs[1]["diagnostics"]["accepted_valid_prefix"], q)

    def test_range_operators_keep_the_whole_expression_as_one_unresolved_range(self):
        for op in RANGE_OPS:
            for bad in ("(-1)", "(.)", "(z", "(*"):
                q = f"12 CFR 3.22(a){op}{bad}"
                cs = parse(q)
                self.assertEqual(len(cs), 1, q)
                self.assertEqual((cs[0]["malformed"], cs[0]["requested"]), ("unsupported_range", q), q)
                self.assertEqual(cs[0]["diagnostics"]["range"]["end_form"], "malformed_endpoint", q)

    def test_section_ranges_with_malformed_operands_and_section_lists(self):
        (c,) = parse("12 CFR 3.1 through (-1)")
        self.assertEqual((c["malformed"], c["requested"]), ("unsupported_range", "12 CFR 3.1 through (-1)"))
        cs = parse("12 CFR 3.1, 6.1 and (-1)")
        self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR 3.1", None), ("12 CFR 6.1", None), ("(-1) after 12 CFR 6.1", UNREAD)])

    def test_independent_citations_after_a_malformed_expression_are_preserved(self):
        cs = parse("12 CFR 3.22(a) and (-1) and 12 CFR 3.1")
        self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR 3.22(a)", None), ("(-1) after 12 CFR 3.22(a)", UNREAD), ("12 CFR 3.1", None)])
        cs = parse("12 CFR 3.22(a) through (-1) and 12 CFR 3.1")
        self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR 3.22(a) through (-1)", "unsupported_range"), ("12 CFR 3.1", None)])
        cs = parse("12 CFR 3.22(a) and (-1), 6.1")
        self.assertEqual([(c["canonical"], c["malformed"]) for c in cs], [("12 CFR 3.22(a)", None), (None, UNREAD), ("12 CFR 6.1", None)])
        cs = parse("12 CFR 3.22(a) through (z and 12 CFR 3.1")
        self.assertEqual(cs[-1]["canonical"], "12 CFR 3.1")

    def test_valid_shorthand_ranges_and_explanatory_parentheticals_are_unchanged(self):
        self.assertEqual([(c["label"], c["malformed"]) for c in parse("12 CFR 3.22(a) and (z)")], [("12 CFR 3.22(a)", None), ("12 CFR 3.22(z)", None)])
        (c,) = parse("12 CFR 3.22(a) through (z)")
        self.assertEqual((c["malformed"], c["diagnostics"]["range"]["end_form"], c["diagnostics"]["range"]["end"]), ("unsupported_range", "bare_paragraph", "12 CFR 3.22(z)"))
        self.assertEqual(parse("12 CFR 3.1-6.1")[0]["malformed"], "unsupported_range")
        for q in ("12 CFR 3.22(a) and (introductory text)", "12 CFR 3.22(a) through (introductory text)", "12 CFR 3.22(a) or (see below)",
                  "12 CFR 3.22(a) and (—see above)", "12 CFR 3.22(a) (introductory text)"):
            cs = parse(q)
            self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR 3.22(a)", None)], q)
        for q in ("12 CFR 3.1", "12 CFR 3.22(a)", "12 CFR 3.1, 6.1"):
            self.assertTrue(all(c["malformed"] is None for c in parse(q)), q)

    def test_citation_only_accounts_for_the_operand(self):
        for q in ("12 CFR 3.22(a) and (-1)", "12 CFR 3.22(a) through (z", "12 CFR 3.22(a) or (."):
            self.assertTrue(citation_only(q), q)
        self.assertFalse(citation_only("capital 12 CFR 3.22(a) and (-1)"))


class OperandIndexTests(unittest.TestCase):
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
        cls.db = str(Path(cls.tmp.name) / "o.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def gaps(self, out):
        return sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))

    def test_list_member_is_its_own_unresolved_record_beside_the_valid_one(self):
        for q in LIST_REQUESTS:
            bad = parse(q)[1]
            for scope in SCOPES:
                for parts in PARTS:
                    recs = self.index.resolve_citations(q, scope, parts)
                    self.assertEqual(len(recs), 2, (q, scope, parts))
                    self.assertEqual(recs[0]["citation"], "12 CFR 3.22(a)")
                    r = recs[1]
                    self.assertFalse(r["resolved"])
                    self.assertEqual((r["reason"], r["requested"], r["canonical"], r["citation"]), (UNREAD, bad["requested"], None, bad["label"]))
                    self.assertEqual((r["_current_ids"], r["_future_ids"]), ([], []))
                    self.assertFalse(r["diagnostics"]["accepted_valid_prefix"])
                    self.assertEqual(r["legal_validity"], "not_determined")

    def test_list_answers_are_partial_with_a_gap_and_only_the_valid_members_records(self):
        for q in LIST_REQUESTS:
            label = parse(q)[1]["label"]
            alone = self.index.answer("12 CFR 3.22(a)", mode="exact", scope="current", k=10)
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (q, mode, scope, parts, k)
                            self.assertNotEqual(out["status"], "evidence_found", tag)
                            self.assertIn(f"citation:{label}", self.gaps(out), tag)
                            self.assertEqual(out["applicability_status"], "not_assessed")
            ex = self.index.answer(q, mode="exact", scope="current", k=10)
            self.assertEqual([r["provision_id"] for r in ex["results"]], [r["provision_id"] for r in alone["results"]], q)
            self.assertEqual(ex["status"], "partial_evidence", q)

    def test_list_delta_is_unsupported_multi_citation(self):
        for q in LIST_REQUESTS:
            d = self.index.delta(q)
            self.assertEqual(d["evidence_status"], "unsupported_multi_citation", q)
            self.assertEqual((d["baseline"], d["instruments"], d["section_context"]), (None, [], None), q)
            cites = d["unavailable"]["citations"]
            self.assertEqual([c["label"] for c in cites], ["12 CFR 3.22(a)", parse(q)[1]["label"]], q)
            self.assertEqual([c["unresolved_reason"] for c in cites], [None, UNREAD], q)
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))

    def test_range_with_a_malformed_endpoint_is_one_unresolved_expression_everywhere(self):
        for q in RANGE_REQUESTS:
            expr = parse(q)[0]["label"]
            self.assertEqual(len(parse(q)), 1, q)
            for scope in SCOPES:
                for parts in PARTS:
                    recs = self.index.resolve_citations(q, scope, parts)
                    self.assertEqual(len(recs), 1, (q, scope, parts))
                    self.assertEqual((recs[0]["reason"], recs[0]["citation"], recs[0]["_current_ids"], recs[0]["_future_ids"]), ("unsupported_range", expr, [], []))
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
            d = self.index.delta(q)
            self.assertEqual((d["evidence_status"], d["baseline"], d["section_context"], d["instruments"]), ("unavailable", None, None, []), q)
            self.assertEqual(d["unavailable"]["reason"], "unsupported_range")
            self.assertEqual(d["unavailable"]["diagnostics"]["range"]["end_form"], "malformed_endpoint")

    def test_independent_citations_after_a_malformed_expression_still_resolve(self):
        for q, bad_reason in (("12 CFR 3.22(a) and (-1) and 12 CFR 3.1", UNREAD), ("12 CFR 3.22(a) through (-1) and 12 CFR 3.1", "unsupported_range")):
            for mode in MODES:
                for scope in ("current", "both"):
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, k=k)
                        cites = out["citations"]
                        self.assertTrue([c for c in cites if c["citation"] == "12 CFR 3.1" and c["resolved"]], (q, mode, scope, k))
                        self.assertEqual(out["status"], "partial_evidence")
                        self.assertEqual(len(self.gaps(out)), 1)
            self.assertEqual(self.index.delta(q)["evidence_status"], "unsupported_multi_citation")
        ex = self.index.answer("12 CFR 3.22(a) through (-1) and 12 CFR 3.1", mode="exact", scope="current")
        alone = self.index.answer("12 CFR 3.1", mode="exact", scope="current")
        self.assertEqual([r["provision_id"] for r in ex["results"]], [r["provision_id"] for r in alone["results"]])
        self.assertEqual(self.index.answer("12 CFR 3.22(a) through (-1) and 12 CFR 3.1", mode="exact", scope="current", parts=["12:6"])["results"], [])

    def test_valid_shorthand_and_single_citations_behave_as_before(self):
        for plain, full in (("12 CFR 3.22(a) and (z)", "12 CFR 3.22(a) and 12 CFR 3.22(z)"),):
            a, b = (self.index.answer(q, mode="exact", scope="current") for q in (plain, full))
            self.assertEqual((a["status"], self.gaps(a)), (b["status"], self.gaps(b)))
        alone = self.index.answer("12 CFR 3.22(a)", mode="exact", scope="current")
        self.assertEqual((alone["status"], self.gaps(alone)), ("evidence_found", []))
        prose = self.index.answer("12 CFR 3.22(a) and (introductory text)", mode="exact", scope="current")  # extra words: topic gap only
        self.assertEqual(self.gaps(prose), [])
        self.assertEqual([r["provision_id"] for r in prose["results"]], [r["provision_id"] for r in alone["results"]])
        self.assertNotEqual(self.index.delta("12 CFR 3.22(a) and (introductory text)")["evidence_status"], "unsupported_multi_citation")

    def test_evaluator_group_is_registered_and_fails_when_the_operand_disappears(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("connector_operands", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["connector_operands"]], [f"CO0{n}" for n in range(1, 6)])
        item = {"id": "T1", "kind": "list", "requests": ["12 CFR 3.22(a) and (-1)", "12 CFR 3.22(a); (z"], "valid": "12 CFR 3.22(a)"}
        self.assertTrue(evaluate.check_connector_operands(self.index, item)["passed"])
        item2 = {"id": "T2", "kind": "range", "requests": ["12 CFR 3.22(a) through (-1)", "12 CFR 3.22(a) to (z"]}
        self.assertTrue(evaluate.check_connector_operands(self.index, item2)["passed"])
        real = search.parse_query_citations
        def old_behaviour(q):  # the previous parser: the malformed operand and the range vanish, leaving the first citation
            cs = real(q)
            return [dict(cs[0], malformed=None, canonical="12 CFR 3.22(a)", label="12 CFR 3.22(a)", diagnostics=None)] if "(" in q.split("3.22(a)", 1)[-1] else cs
        with mock.patch.object(evaluate, "parse_query_citations", old_behaviour):
            self.assertFalse(evaluate.check_connector_operands(self.index, item)["passed"])
            self.assertFalse(evaluate.check_connector_operands(self.index, item2)["passed"])

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, status)


if __name__ == "__main__":
    unittest.main()
