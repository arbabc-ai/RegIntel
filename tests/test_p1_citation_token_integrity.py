"""Tests for S7-P1-CITATION-TOKEN-INTEGRITY-28 (F1): an identifier that runs on with no space (`3.22.1`, `3.22_a`, `Part 3_a`,
`3.22ab`) is never accepted as its prefix. The complete expression is kept as an unresolved request with the unread
continuation identified, its own citation gap, no exact records, no `evidence_found`, an unavailable delta, and valid members of
the same request are kept. Paragraphs, lettered sections, sentence-final punctuation, punctuated titles, lists and ranges are
unchanged. No legal validity is determined."""
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
UNREAD = "unread_identifier_continuation"
BAD = ("12 CFR 3.22.1", "12 CFR 3.22_a", "12 C.F.R. § 3.22.1", "12 CFR Part 3_a", "CET1 12 CFR 3.22.1", "31 CFR 3.22.1", "12 CFR 3.22ab", "12 CFR 3.22(a)b",
       "12 CFR 3.22.1.2", "12 CFR Part 3a", "31 C.F.R. § 3.22_a")
GOOD = ("12 CFR 3.22", "12 CFR 3.22.", "12 CFR 3.22(a).", "see 12 CFR 3.22.", "12 CFR 3.22; 3.12.", "(12 CFR 3.22)", "12 CFR Part 3.", "12 C.F.R. § 3.22.", "12 CFR 3.22, 3.12",
        "12 CFR 3.22(a)(1)", "12 CFR 3.22's", "12 CFR 3.22 and 6.1")


class ParserTests(unittest.TestCase):
    def test_required_reproductions_are_one_unresolved_expression_with_the_unread_continuation(self):
        want = {"12 CFR 3.22.1": ".1", "12 CFR 3.22_a": "_a", "12 C.F.R. § 3.22.1": ".1", "12 CFR Part 3_a": "_a", "CET1 12 CFR 3.22.1": ".1"}
        for q, cont in want.items():
            cs = parse(q)
            self.assertEqual(len(cs), 1, q)
            c = cs[0]
            self.assertEqual(c["malformed"], UNREAD, q)
            self.assertEqual(c["requested"], q.replace("CET1 ", ""), q)  # the complete expression, as written
            self.assertEqual(c["label"], " ".join(c["requested"].split()), q)
            d = c["diagnostics"]
            self.assertEqual((d["accepted_valid_prefix"], d["unread_continuation"], d["unread_suffix"]), (False, cont, cont), q)
        self.assertEqual(parse("12 CFR 3.22.1")[0]["diagnostics"]["valid_prefix"], "3.22")
        self.assertEqual(parse("12 CFR Part 3_a")[0]["diagnostics"]["valid_prefix"], "Part 3")

    def test_inherited_list_members_keep_the_valid_member_and_flag_the_continuation(self):
        cs = parse("12 CFR 3.1 and 3.22.1")
        self.assertEqual([(c["label"], c["malformed"], c["requested"]) for c in cs], [("12 CFR 3.1", None, "12 CFR 3.1"), ("12 CFR 3.22.1", UNREAD, "3.22.1")])
        self.assertEqual(cs[1]["diagnostics"]["unread_continuation"], ".1")
        cs = parse("12 CFR Parts 3 and 6_a")
        self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR Part 3", None), ("12 CFR Part 6_a", UNREAD)])
        self.assertEqual([c["malformed"] for c in parse("31 CFR §§ 3.22, 3.12.x")], [None, UNREAD])
        self.assertEqual([c["malformed"] for c in parse("12 CFR Part 3 and 6a")], [None, UNREAD])

    def test_more_forms_are_unresolved_not_prefix_accepted(self):
        for q in BAD:
            self.assertEqual(parse(q)[0]["malformed"], UNREAD, q)
            self.assertFalse(citation_only(q) and not q.startswith(("12 C", "31 C")), q)

    def test_continuations_in_ranges_stay_one_expression(self):
        for q in ("12 CFR Part 3 through 6_a", "12 CFR Part 3 to 6a", "12 CFR 3.1 through 6.1.2"):
            cs = parse(q)
            self.assertEqual((len(cs), cs[0]["malformed"], cs[0]["requested"]), (1, "unsupported_range", q), q)

    def test_valid_forms_are_unchanged(self):
        for q in GOOD:
            cs = parse(q)
            self.assertTrue(cs and all(c["malformed"] is None for c in cs), q)
        self.assertEqual(parse("12 CFR 3.22.")[0]["requested"], "12 CFR 3.22")  # a sentence-final period is not part of the citation
        self.assertEqual(parse("12 CFR 3.22a")[0]["canonical"], "12 CFR 3.22a")  # a lettered section
        self.assertEqual([c["canonical"] for c in parse("12 CFR 3.22(a) and (z)")], ["12 CFR 3.22(a)", "12 CFR 3.22(z)"])
        self.assertEqual([c["canonical"] for c in parse("12 CFR Parts 3, 6, and 217")], ["12 CFR Part 3", "12 CFR Part 6", "12 CFR Part 217"])
        self.assertEqual(parse("12 CFR 3.1-6.1")[0]["malformed"], "unsupported_range")
        self.assertEqual(parse("12 C.F.R. § 3.22")[0]["title"], "12")
        self.assertEqual([c["canonical"] for c in parse("12 CFR 3.1 and 31 CFR 3.22")], ["12 CFR 3.1", "31 CFR 3.22"])
        self.assertEqual([c["malformed"] for c in parse("12 CFR Part 3 and 6 banks")], [None])
        self.assertEqual(parse("§ 3.22.")[0]["title"], None)


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
        cls.db = str(Path(cls.tmp.name) / "t.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def gaps(self, out):
        return sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))

    def test_resolver_returns_one_unresolved_record_with_no_ids(self):
        for q in BAD:
            label = parse(q)[0]["label"]
            for scope in SCOPES:
                for parts in PARTS:
                    recs = self.index.resolve_citations(q, scope, parts)
                    self.assertEqual(len(recs), 1, (q, scope, parts))
                    r = recs[0]
                    self.assertFalse(r["resolved"])
                    self.assertEqual((r["reason"], r["citation"], r["canonical"], r["_current_ids"], r["_future_ids"]), (UNREAD, label, None, [], []), (q, scope, parts))
                    self.assertEqual(r["legal_validity"], "not_determined")
                    self.assertIn("not a determination", r["note"])
                    self.assertTrue(r["diagnostics"]["unread_continuation"])

    def test_exact_and_hybrid_answers_keep_the_gap_and_never_report_evidence_found(self):
        for q in BAD:
            label = parse(q)[0]["label"]
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:  # absent, allowing (12:3 / 31:3) and excluding (12:6, 31:3 for the Title 12 prefix) filters
                        for k in (10, 1):
                            out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (q, mode, scope, parts, k)
                            self.assertNotEqual(out["status"], "evidence_found", tag)
                            self.assertIn(f"citation:{label}", self.gaps(out), tag)  # a hybrid contextual hit does not conceal it
                            self.assertEqual(out["applicability_status"], "not_assessed")
                            if mode == "exact":
                                self.assertEqual((out["results"], out["status"]), ([], "missing_evidence"), tag)

    def test_delta_is_unavailable_with_no_baseline_or_instruments(self):
        for q in BAD:
            d = self.index.delta(q)
            self.assertEqual((d["evidence_status"], d["baseline"], d["section_context"], d["instruments"]), ("unavailable", None, None, []), q)
            self.assertEqual(d["unavailable"]["reason"], UNREAD, q)
            self.assertEqual(d["unavailable"]["diagnostics"]["accepted_valid_prefix"], False)
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))
        self.assertEqual(self.index.delta("12 CFR 3.22")["evidence_status"], "available")  # the prefix itself is still a valid request

    def test_mixed_requests_keep_their_valid_members_and_the_multi_citation_delta(self):
        q = "12 CFR 3.1 and 3.22.1"
        for mode in MODES:
            for scope in ("current", "both"):
                for k in (10, 1):
                    out = self.index.answer(q, mode=mode, scope=scope, k=k)
                    cites = {c["citation"]: c for c in out["citations"]}
                    self.assertTrue(cites["12 CFR 3.1"]["resolved"], (mode, scope, k))
                    self.assertFalse(cites["12 CFR 3.22.1"]["resolved"])
                    self.assertEqual((out["status"], self.gaps(out)), ("partial_evidence", ["citation:12 CFR 3.22.1"]))
        ex = self.index.answer(q, mode="exact", scope="current")
        alone = self.index.answer("12 CFR 3.1", mode="exact", scope="current")
        self.assertEqual([r["provision_id"] for r in ex["results"]], [r["provision_id"] for r in alone["results"]])  # nothing from 3.22
        d = self.index.delta(q)
        self.assertEqual((d["evidence_status"], [c["unresolved_reason"] for c in d["unavailable"]["citations"]]), ("unsupported_multi_citation", [None, UNREAD]))
        self.assertEqual(self.index.delta("12 CFR Parts 3 and 6_a")["evidence_status"], "unsupported_multi_citation")

    def test_prefix_requests_are_not_confused_with_their_valid_prefix(self):
        for bad, prefix in (("12 CFR 3.22.1", "12 CFR 3.22"), ("12 CFR 3.22_a", "12 CFR 3.22"), ("12 CFR Part 3_a", "12 CFR Part 3"), ("31 CFR 3.22.1", "31 CFR 3.22")):
            self.assertEqual(self.index.answer(prefix, mode="exact", scope="current")["status"], "evidence_found", prefix)
            out = self.index.answer(bad, mode="exact", scope="current")
            self.assertEqual(out["results"], [], bad)
            self.assertNotEqual(out["status"], "evidence_found", bad)

    def test_two_title_controls_keep_their_own_title(self):
        for q, wrong_doc in (("31 CFR 3.22.1", "ecfr:12:3"), ("12 CFR 3.22.1", "ecfr:31:3"), ("12 C.F.R. § 3.22_a", "ecfr:31:3")):
            out = self.index.answer(q, mode="hybrid", scope="current", k=10)
            self.assertNotEqual(out["status"], "evidence_found", q)
            self.assertEqual(parse(q)[0]["title"], q[:2], q)
            exact = self.index.answer(q, mode="exact", scope="current")
            self.assertFalse([r for r in exact["results"] if r["doc_key"] == wrong_doc], q)
        self.assertEqual(self.index.resolve_citations("§ 3.22.1", "current")[0]["reason"], UNREAD)  # titleless is unresolved too, not ambiguous-titled

    def test_valid_forms_still_resolve(self):
        for q in ("12 CFR 3.22.", "see 12 CFR 3.22.", "12 CFR 3.22(a).", "12 C.F.R. § 3.22.", "(12 CFR 3.22)"):
            for mode in MODES:
                out = self.index.answer(q, mode=mode, scope="current", k=1)
                self.assertEqual(self.gaps(out), [], (q, mode))
                self.assertTrue(out["citations"][0]["resolved"], (q, mode))
        self.assertTrue(self.index.resolve_citations("12 CFR 6.1", "current")[0]["resolved"])

    def test_evaluator_group_is_registered_and_fails_at_the_starting_behaviour(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("citation_tokens", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["citation_tokens"]], [f"CK0{n}" for n in range(1, 6)])
        item = {"id": "T1", "kind": "unresolved", "requests": ["12 CFR 3.22.1", "12 CFR 3.22_a", "12 CFR Part 3_a", "31 CFR 3.22.1"]}
        mixed = {"id": "T2", "kind": "mixed", "request": "12 CFR 3.1 and 3.22.1", "valid": ["12 CFR 3.1"]}
        controls = {"id": "T3", "kind": "controls", "requests": [["12 CFR 3.22.", ["12 CFR 3.22"]], ["12 CFR 3.22a", ["12 CFR 3.22a"]]]}
        for x in (item, mixed, controls):
            self.assertTrue(evaluate.check_citation_tokens(self.index, x)["passed"], x["id"])
        real = search.parse_query_citations

        def starting(q):  # the previous parser: the unread continuation is dropped and the prefix accepted
            out = []
            for c in real(q):
                if c["malformed"] == UNREAD:
                    c = dict(c, malformed=None, diagnostics=None, label=c["canonical"], requested=c["canonical"])
                out.append(c)
            return out
        with mock.patch.object(search, "parse_query_citations", starting), mock.patch.object(evaluate, "parse_query_citations", starting):
            self.assertFalse(evaluate.check_citation_tokens(self.index, item)["passed"])
            self.assertFalse(evaluate.check_citation_tokens(self.index, mixed)["passed"])

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
        for q in ("12 CFR 3.22.1", "12 CFR 3.22_a", "12 C.F.R. § 3.22.1", "12 CFR Part 3_a", "CET1 12 CFR 3.22.1"):
            label = parse(q)[0]["label"]
            for mode in MODES:
                for scope in SCOPES:
                    for parts in (None, ["12:3"]):
                        out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=1)
                        self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope, parts))
                        self.assertIn(f"citation:{label}", self.gaps(out), (q, mode, scope, parts))
                        if mode == "exact":
                            self.assertEqual(out["results"], [], (q, scope, parts))
            d = self.index.delta(q.replace("CET1 ", ""))
            self.assertEqual((d["evidence_status"], d["baseline"], d["instruments"]), ("unavailable", None, []), q)
        mixed = self.index.answer("12 CFR 3.1 and 3.22.1", mode="exact", scope="current")
        self.assertEqual((mixed["status"], self.gaps(mixed)), ("partial_evidence", ["citation:12 CFR 3.22.1"]))
        self.assertEqual(len(self.index.delta("12 CFR 3.22")["instruments"]), 4)  # the prefix itself is unchanged


if __name__ == "__main__":
    unittest.main()
