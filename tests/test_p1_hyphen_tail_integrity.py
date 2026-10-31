"""Tests for S7-P1-HYPHEN-TAIL-INTEGRITY-29 (F1): a hyphen joined tightly to a citation that the range reader cannot consume
(`12 CFR 3.22-a`, `12 CFR 3.22-1`) is an unread tail. The complete expression stays an unresolved request with the tail
identified and its own citation gap; the prefix is never accepted as the whole request. Recognised ranges, spaced prose, valid
citations, punctuated titles and independent list members are unchanged. No legal validity is determined."""
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
TAIL = "unread_hyphen_tail"
BAD = ("12 CFR 3.22-a", "12 CFR 3.22-1", "12 C.F.R. § 3.22-a", "12 CFR Part 3-a", "CET1 12 CFR 3.22-a", "31 CFR 3.22-1", "12 CFR 3.22(a)-b", "12 CFR 3.22–a",
       "12 CFR 3.22-based", "12 CFR 3.22(a)(1)-x", "31 C.F.R. § 3.22-1")
CONTROLS = (("12 CFR 3.22 - a rule", ["12 CFR 3.22"]), ("12 CFR 3.22 -a", ["12 CFR 3.22"]), ("12 CFR 3.22-", ["12 CFR 3.22"]), ("12 CFR 3.22 -- rule", ["12 CFR 3.22"]),
            ("12 CFR 3.1-3.5 percent", ["12 CFR 3.1"]), ("12 CFR 3.22.", ["12 CFR 3.22"]), ("12 CFR 3.22a", ["12 CFR 3.22a"]), ("12 CFR 3.22(a)", ["12 CFR 3.22(a)"]),
            ("12 C.F.R. § 3.22", ["12 CFR 3.22"]), ("12 CFR 3.22, 3.1", ["12 CFR 3.22", "12 CFR 3.1"]), ("12 CFR 3.22 and 31 CFR 3.22", ["12 CFR 3.22", "31 CFR 3.22"]))
RANGES = ("12 CFR 3.1-6.1", "12 CFR 3.1 - 6.1", "12 CFR Part 3-6", "12 CFR 3.22(a)-(c)", "12 CFR 3.22-3.30", "12 CFR 3.1–6.1", "12 CFR Part 3 through 6")


class ParserTests(unittest.TestCase):
    def test_required_reproductions_keep_the_complete_expression_and_identify_the_tail(self):
        for q, tail in (("12 CFR 3.22-a", "-a"), ("12 CFR 3.22-1", "-1"), ("CET1 12 CFR 3.22-a", "-a"), ("12 CFR 3.22–a", "–a")):
            cs = parse(q)
            self.assertEqual(len(cs), 1, q)
            c = cs[0]
            self.assertEqual((c["malformed"], c["requested"]), (TAIL, q.replace("CET1 ", "")), q)
            d = c["diagnostics"]
            self.assertEqual((d["accepted_valid_prefix"], d["unread_continuation"], d["unread_tail_kind"], d["valid_prefix"]), (False, tail, "hyphen", "3.22"), q)
            self.assertEqual(c["label"], " ".join(c["requested"].split()), q)

    def test_all_bad_forms_are_unresolved_and_not_citation_only_when_mixed_with_words(self):
        for q in BAD:
            self.assertEqual(parse(q)[0]["malformed"], TAIL, q)
        self.assertFalse(citation_only("capital 12 CFR 3.22-a"))
        self.assertTrue(citation_only("12 CFR 3.22-a"))

    def test_paragraph_and_part_prefixes(self):
        self.assertEqual(parse("12 CFR 3.22(a)-b")[0]["diagnostics"]["valid_prefix"], "3.22(a)")
        self.assertEqual(parse("12 CFR Part 3-a")[0]["diagnostics"]["valid_prefix"], "Part 3")

    def test_list_members_keep_the_valid_member_and_flag_the_tail(self):
        cs = parse("12 CFR 3.1 and 3.22-a")
        self.assertEqual([(c["label"], c["malformed"], c["requested"]) for c in cs], [("12 CFR 3.1", None, "12 CFR 3.1"), ("12 CFR 3.22-a", TAIL, "3.22-a")])
        cs = parse("12 CFR Parts 3 and 6-a")
        self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR Part 3", None), ("12 CFR Part 6-a", TAIL)])
        self.assertEqual(parse("12 CFR 3.22, 3.1")[1]["canonical"], "12 CFR 3.1")

    def test_recognised_ranges_are_still_ranges(self):
        for q in RANGES:
            cs = parse(q)
            self.assertEqual((len(cs), cs[0]["malformed"], cs[0]["requested"]), (1, "unsupported_range", q), q)

    def test_spaced_prose_quantities_and_valid_citations_are_unchanged(self):
        for q, want in CONTROLS:
            cs = parse(q)
            self.assertEqual([c["canonical"] for c in cs], want, q)
            self.assertTrue(all(c["malformed"] is None for c in cs), q)

    def test_earlier_unread_forms_keep_their_reasons(self):
        self.assertEqual(parse("12 CFR 3.22.1")[0]["malformed"], "unread_identifier_continuation")
        self.assertEqual(parse("12 CFR 3.22_a")[0]["malformed"], "unread_identifier_continuation")
        self.assertEqual(parse("12 CFR 3.22(a)(")[0]["malformed"], "unclosed_or_malformed_designator_suffix")


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

    def test_resolver_one_unresolved_record_with_no_ids_and_no_validity_determination(self):
        for q in BAD:
            label = parse(q)[0]["label"]
            for scope in SCOPES:
                for parts in PARTS:
                    recs = self.index.resolve_citations(q, scope, parts)
                    self.assertEqual(len(recs), 1, (q, scope, parts))
                    r = recs[0]
                    self.assertEqual((r["resolved"], r["reason"], r["citation"], r["canonical"], r["_current_ids"], r["_future_ids"]), (False, TAIL, label, None, [], []), (q, scope, parts))
                    self.assertEqual(r["legal_validity"], "not_determined")
                    self.assertIn("not a determination", r["note"])
                    self.assertTrue(r["diagnostics"]["unread_continuation"])

    def test_exact_and_hybrid_answers_keep_the_gap_and_never_report_evidence_found(self):
        for q in BAD:
            label = parse(q)[0]["label"]
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (q, mode, scope, parts, k)
                            self.assertNotEqual(out["status"], "evidence_found", tag)
                            self.assertIn(f"citation:{label}", self.gaps(out), tag)
                            self.assertEqual(out["applicability_status"], "not_assessed")
                            if mode == "exact":
                                self.assertEqual((out["results"], out["status"]), ([], "missing_evidence"), tag)

    def test_delta_unavailable_and_the_prefix_stays_valid(self):
        for q in BAD:
            d = self.index.delta(q)
            self.assertEqual((d["evidence_status"], d["baseline"], d["section_context"], d["instruments"]), ("unavailable", None, None, []), q)
            self.assertEqual(d["unavailable"]["reason"], TAIL, q)
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))
        self.assertEqual(self.index.delta("12 CFR 3.22")["evidence_status"], "available")
        self.assertEqual(self.index.answer("12 CFR 3.22", mode="exact", scope="current")["status"], "evidence_found")

    def test_mixed_requests_keep_valid_members_and_the_multi_citation_delta(self):
        q = "12 CFR 3.1 and 3.22-a"
        for mode in MODES:
            for scope in ("current", "both"):
                for k in (10, 1):
                    out = self.index.answer(q, mode=mode, scope=scope, k=k)
                    cites = {c["citation"]: c for c in out["citations"]}
                    self.assertTrue(cites["12 CFR 3.1"]["resolved"], (mode, scope, k))
                    self.assertFalse(cites["12 CFR 3.22-a"]["resolved"])
                    self.assertEqual((out["status"], self.gaps(out)), ("partial_evidence", ["citation:12 CFR 3.22-a"]))
        ex = self.index.answer(q, mode="exact", scope="current")
        alone = self.index.answer("12 CFR 3.1", mode="exact", scope="current")
        self.assertEqual([r["provision_id"] for r in ex["results"]], [r["provision_id"] for r in alone["results"]])
        d = self.index.delta(q)
        self.assertEqual((d["evidence_status"], [c["unresolved_reason"] for c in d["unavailable"]["citations"]]), ("unsupported_multi_citation", [None, TAIL]))

    def test_cet1_mixed_topic_cannot_conceal_the_tail(self):
        for q in ("CET1 12 CFR 3.22-a", "CET1 capital 31 CFR 3.22-1", "CET1 12 CFR 3.22(a)-b"):
            for mode in MODES:
                for scope in SCOPES:
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, k=k)
                        self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope, k))
                        self.assertTrue(self.gaps(out), (q, mode, scope, k))

    def test_two_title_controls_and_punctuated_titles_keep_their_title(self):
        for q, title, wrong_doc in (("31 CFR 3.22-1", "31", "ecfr:12:3"), ("12 CFR 3.22-a", "12", "ecfr:31:3"), ("31 C.F.R. § 3.22-1", "31", "ecfr:12:3")):
            self.assertEqual(parse(q)[0]["title"], title, q)
            exact = self.index.answer(q, mode="exact", scope="current")
            self.assertEqual(exact["results"], [], q)
            self.assertNotEqual(self.index.answer(q, mode="hybrid", scope="current", k=1)["status"], "evidence_found", q)
            self.assertEqual(self.index.delta(q)["instruments"], [], q)

    def test_ranges_prose_and_valid_citations_behave_as_before(self):
        for q in RANGES[:3]:
            out = self.index.answer(q, mode="exact", scope="both")
            self.assertEqual(out["results"], [], q)
            self.assertIn(f"citation:{parse(q)[0]['label']}", self.gaps(out), q)
        for q, want in CONTROLS[:4]:
            out = self.index.answer(q, mode="exact", scope="current")
            self.assertEqual(self.gaps(out), [], q)
        self.assertEqual(self.index.answer("12 CFR 3.22; 3.1", mode="exact", scope="current")["status"], "evidence_found")

    def test_evaluator_group_is_registered_and_fails_at_the_starting_behaviour(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("hyphen_tails", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["hyphen_tails"]], [f"HT0{n}" for n in range(1, 6)])
        item = {"id": "T1", "kind": "unresolved", "reason": TAIL, "requests": ["12 CFR 3.22-a", "12 CFR 3.22-1", "31 CFR 3.22-1"]}
        mixed = {"id": "T2", "kind": "mixed", "reason": TAIL, "request": "12 CFR 3.1 and 3.22-a", "valid": ["12 CFR 3.1"]}
        for x in (item, mixed):
            self.assertTrue(evaluate.check_citation_tokens(self.index, x)["passed"], x["id"])
        real = search.parse_query_citations

        def starting(q):  # the previous parser: a tightly joined hyphen tail is dropped and the prefix accepted
            out = []
            for c in real(q):
                if c["malformed"] == TAIL:
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
        for q in ("12 CFR 3.22-a", "12 CFR 3.22-1", "CET1 12 CFR 3.22-a", "12 C.F.R. § 3.22-1"):
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
        self.assertEqual(len(self.index.delta("12 CFR 3.22")["instruments"]), 4)  # the prefix itself is unchanged
        self.assertEqual(self.index.answer("12 CFR 3.1-6.1", mode="exact", scope="current")["results"], [])


if __name__ == "__main__":
    unittest.main()
