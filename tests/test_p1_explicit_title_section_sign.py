"""Tests for S7-P1-EXPLICIT-TITLE-SECTION-SIGN-26 (F1): an explicit CFR title in front of a section sign (`31 CFR § 3.22`,
`31 CFR §§ 3.22, 3.12`) stays attached to the complete citation. It resolves exactly like `31 CFR 3.22`, never through
another title, and a title that does not have the section keeps an unresolved citation gap."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.search import citation_only, parse_query_citations as parse
from test_p1 import asset
from test_p1_delta_precision import fr
from test_p1_query_lists import ECFR_3, ECFR_6, ECFR_31, F1

MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")
PARTS = (None, ["12:3"], ["31:3"], ["12:6"])
Z31 = fr("RULE", "1. Amend &#xA7; 3.22 by revising paragraph (b) to read as follows:", [("3.22", ["(b) Treasury new text."])], title="31", part="3")
KEYS = ("fr:2026-97001", "fr:2026-97002")
ECFR_1010 = """<div class="part" id="part-1010"><h1>PART 1010—GENERAL</h1><div class="section" id="1010.100"><h4>§ 1010.100 General definitions.</h4>
<div id="p-1010.100(a)"><p class="indent-1" data-title="1010.100(a)">(a) Definitions apply.</p></div></div></div>"""
PAIRS = (("31 CFR § 3.22", "31 CFR 3.22"), ("12 CFR § 3.22", "12 CFR 3.22"), ("31 CFR §§ 3.22", "31 CFR 3.22"), ("31 CFR §3.22", "31 CFR 3.22"),
         ("31 CFR § 3.22(a)", "31 CFR 3.22(a)"), ("12 CFR § 3.22(a)", "12 CFR 3.22(a)"), ("31 CFR § 1010.100", "31 CFR 1010.100"), ("12 CFR § 6.1", "12 CFR 6.1"))
WRONG = ("31 CFR § 6.1", "12 CFR § 1010.100", "31 CFR § 6.1(a)", "31 CFR 6.1", "12 CFR 1010.100")


class ParserTests(unittest.TestCase):
    def test_required_reproduction_keeps_the_title_and_the_requested_span(self):
        (c,) = parse("31 CFR § 3.22")
        self.assertEqual((c["title"], c["canonical"], c["label"], c["requested"], c["malformed"]), ("31", "31 CFR 3.22", "31 CFR 3.22", "31 CFR § 3.22", None))
        (c,) = parse("CET1 31 CFR § 3.22")
        self.assertEqual((c["title"], c["canonical"], c["requested"]), ("31", "31 CFR 3.22", "31 CFR § 3.22"))

    def test_every_sign_form_equals_the_unsigned_citation(self):
        for signed, plain in PAIRS:
            a, b = parse(signed), parse(plain)
            self.assertEqual([(c["title"], c["part"], c["minor"], c["paras"], c["canonical"]) for c in a], [(c["title"], c["part"], c["minor"], c["paras"], c["canonical"]) for c in b], signed)
            self.assertEqual(a[0]["requested"], signed, signed)  # the original span, sign included

    def test_titles_are_inherited_in_lists_after_a_signed_head(self):
        cs = parse("31 CFR §§ 3.22, 3.12")
        self.assertEqual([(c["canonical"], c["requested"]) for c in cs], [("31 CFR 3.22", "31 CFR §§ 3.22"), ("31 CFR 3.12", "3.12")])
        self.assertEqual(cs[1]["inherited"], {"kind": "section", "from": "31 CFR 3.22", "title_inherited": "31"})
        self.assertEqual([c["canonical"] for c in parse("31 CFR §§ 3.22 and 3.12 or 6.1; 3.30")], ["31 CFR 3.22", "31 CFR 3.12", "31 CFR 6.1", "31 CFR 3.30"])
        self.assertEqual([c["canonical"] for c in parse("12 CFR § 3.22(a) and (z)")], ["12 CFR 3.22(a)", "12 CFR 3.22(z)"])
        self.assertEqual([c["canonical"] for c in parse("31 CFR § 3.22, Part 6")][1], "31 CFR Part 6")

    def test_ranges_and_independent_citations_keep_their_titles(self):
        for q in ("31 CFR § 3.22 through § 3.30", "31 CFR § 3.22 to 3.30", "31 CFR §§ 3.22-3.30", "31 CFR § 3.22 through 12 CFR 3.30"):
            (c,) = parse(q)
            self.assertEqual(c["malformed"], "unsupported_range", q)
            self.assertEqual((c["title"], c["diagnostics"]["range"]["start"]), ("31", "31 CFR 3.22"), q)
        self.assertEqual(parse("31 CFR § 3.22 through § 3.30")[0]["diagnostics"]["range"]["end"], "31 CFR 3.30")  # the endpoint inherits the title
        cs = parse("31 CFR § 3.22 and 12 CFR § 3.1")
        self.assertEqual([c["canonical"] for c in cs], ["31 CFR 3.22", "12 CFR 3.1"])
        cs = parse("31 CFR § 3.22(a)(")
        self.assertEqual((cs[0]["malformed"], cs[0]["title"]), ("unclosed_or_malformed_designator_suffix", "31"))

    def test_genuinely_titleless_citations_are_unchanged(self):
        for q in ("§ 3.22", "§§ 3.22", "see § 3.22(a)"):
            self.assertIsNone(parse(q)[0]["title"], q)
        cs = parse("12 CFR 3.1 and § 217.2(a)")
        self.assertEqual([(c["label"], c["title"]) for c in cs], [("12 CFR 3.1", "12"), ("§ 217.2(a)", None)])  # a section sign starts its own citation
        self.assertEqual([c["canonical"] for c in parse("§§ 3.22, 3.12")], ["§ 3.22", "§ 3.12"])
        self.assertEqual(parse("12 CFR Part 3")[0]["canonical"], "12 CFR Part 3")
        self.assertEqual(parse("12 CFR 3.22(a)")[0]["canonical"], "12 CFR 3.22(a)")

    def test_citation_only(self):
        for q, _ in PAIRS:
            self.assertTrue(citation_only(q), q)
        self.assertFalse(citation_only("capital 31 CFR § 3.22"))


class SyntheticTwoTitleTests(unittest.TestCase):
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
        cls.db = str(Path(cls.tmp.name) / "s.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def gaps(self, out):
        return sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))

    def sig(self, r):
        return (r["citation"], r["resolved"], r["achieved"], r["reason"], r["by_scope"], r["_current_ids"], r["_future_ids"], r["future_paragraph_support"])

    def strip(self, d):
        return {k: v for k, v in d.items() if k not in ("citation", "requested_citation", "canonical_citation")}

    def test_signed_and_unsigned_forms_are_identical_in_resolver_answers_and_delta(self):
        for signed, plain in PAIRS:
            for scope in SCOPES:
                for parts in PARTS:
                    self.assertEqual([self.sig(r) for r in self.index.resolve_citations(signed, scope, parts)], [self.sig(r) for r in self.index.resolve_citations(plain, scope, parts)], (signed, scope, parts))
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            a, b = (self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k) for q in (signed, plain))
                            tag = (signed, mode, scope, parts, k)
                            self.assertEqual((a["status"], self.gaps(a)), (b["status"], self.gaps(b)), tag)
                            if mode == "exact":
                                self.assertEqual([r["provision_id"] for r in a["results"]], [r["provision_id"] for r in b["results"]], tag)
            self.assertEqual(self.strip(self.index.delta(signed)), self.strip(self.index.delta(plain)), signed)

    def test_each_title_keeps_its_own_evidence(self):
        text = lambda q: " ".join(self.index.evidence(i)["text"] for i in self.index.resolve_citations(q, "current")[0]["_current_ids"])
        self.assertIn("Treasury general adjustments", text("31 CFR § 3.22"))
        self.assertNotIn("Twelve", text("31 CFR § 3.22"))
        self.assertIn("Twelve general adjustments", text("12 CFR § 3.22"))
        self.assertEqual([g["doc_key"] for g in self.index.delta("31 CFR § 3.22")["instruments"]], [KEYS[1]])
        self.assertEqual([g["doc_key"] for g in self.index.delta("12 CFR § 3.22")["instruments"]], [KEYS[0]])
        self.assertEqual(self.index.delta("31 CFR § 3.22")["baseline"]["doc_key"], "ecfr:31:3")
        self.assertEqual(self.index.delta("31 CFR § 3.22(b)")["future_precision"]["supported_instruments"], [KEYS[1]])

    def test_wrong_title_requests_stay_unresolved_with_a_gap_and_never_resolve_through_another_title(self):
        for q in WRONG:
            label = parse(q)[0]["label"]
            for scope in SCOPES:
                for parts in PARTS:
                    r = self.index.resolve_citations(q, scope, parts)[0]
                    self.assertFalse(r["resolved"], (q, scope, parts))
                    self.assertEqual((r["_current_ids"], r["_future_ids"]), ([], []), (q, scope, parts))
                    self.assertEqual(r["title_resolution"]["status"], "explicit")
            for mode in MODES:
                for scope in SCOPES:
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, k=k)
                        self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope, k))
                        self.assertIn(f"citation:{label}", self.gaps(out), (q, mode, scope, k))
                        if mode == "exact":
                            self.assertEqual(out["results"], [], (q, scope, k))
            d = self.index.delta(q)
            self.assertEqual((d["evidence_status"], d["baseline"], d["section_context"], d["instruments"]), ("unavailable", None, None, []), q)

    def test_mixed_cet1_requests_cannot_conceal_a_wrong_title(self):
        for q in ("CET1 31 CFR § 6.1", "CET1 capital 12 CFR § 1010.100", "CET1 31 CFR § 6.1 and 12 CFR § 3.22"):
            for mode in MODES:
                for scope in SCOPES:
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, k=k)
                        self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope, k))
                        self.assertTrue([g for g in self.gaps(out)], (q, mode, scope, k))
        out = self.index.answer("CET1 12 CFR § 3.22", mode="exact", scope="current")
        self.assertEqual((out["status"], self.gaps(out)), ("partial_evidence" if out["status"] != "evidence_found" else "evidence_found", []))

    def test_lists_paragraphs_and_ranges_through_the_resolver_answers_and_delta(self):
        recs = self.index.resolve_citations("31 CFR §§ 3.22, 6.1", "current")
        self.assertEqual([(r["citation"], r["resolved"]) for r in recs], [("31 CFR 3.22", True), ("31 CFR 6.1", False)])  # 6.1 exists only in Title 12
        out = self.index.answer("31 CFR §§ 3.22, 6.1", mode="exact", scope="current")
        self.assertEqual((out["status"], self.gaps(out)), ("partial_evidence", ["citation:31 CFR 6.1"]))
        self.assertEqual({r["doc_key"] for r in out["results"]}, {"ecfr:31:3"})
        d = self.index.delta("31 CFR §§ 3.22, 3.12")
        self.assertEqual((d["evidence_status"], [c["label"] for c in d["unavailable"]["citations"]]), ("unsupported_multi_citation", ["31 CFR 3.22", "31 CFR 3.12"]))
        rng = self.index.delta("31 CFR § 3.22 through § 3.30")
        self.assertEqual((rng["evidence_status"], rng["baseline"], rng["instruments"]), ("unavailable", None, []))
        para = self.index.resolve_citations("31 CFR § 3.22(a)", "current")[0]
        self.assertTrue(para["resolved"] and para["title_resolution"]["title"] == "31")
        self.assertFalse(self.index.resolve_citations("31 CFR § 3.1(c)", "current")[0]["resolved"])  # 3.1 exists only under Title 12

    def test_genuinely_titleless_resolution_is_unchanged(self):
        r = self.index.resolve_citations("§ 6.1", "current")[0]
        self.assertTrue(r["resolved"])
        self.assertEqual(r["title_resolution"]["title"], "12")
        amb = self.index.resolve_citations("§ 3.22", "current")[0]
        self.assertEqual((amb["reason"], amb["candidate_titles"]), ("ambiguous_title", ["12", "31"]))
        self.assertEqual(self.index.delta("§ 3.22")["evidence_status"], "ambiguous_title")

    def test_evaluator_group_is_registered_and_fails_when_the_title_is_dropped(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("explicit_title_sign", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["explicit_title_sign"]], [f"ES0{n}" for n in range(1, 6)])
        eq = {"id": "T1", "kind": "signed_equivalents", "pairs": [["31 CFR § 3.22", "31 CFR 3.22"], ["12 CFR § 3.22(a)", "12 CFR 3.22(a)"]]}
        wrong = {"id": "T2", "kind": "wrong_title", "requests": ["31 CFR § 6.1", "12 CFR § 1010.100"]}
        self.assertTrue(evaluate.check_explicit_title_sign(self.index, eq)["passed"])
        self.assertTrue(evaluate.check_explicit_title_sign(self.index, wrong)["passed"])
        real = search.parse_query_citations
        drop = lambda q: [dict(c, title=None, canonical=c["canonical"].replace("31 CFR ", "§ ").replace("12 CFR ", "§ ")) if "§" in q else c for c in real(q)]
        def drop(q):  # the starting parser: the explicit title in front of a section sign is lost
            out = []
            for c in real(q):
                if c["requested"].lstrip("0123456789 ").startswith("CFR \u00a7") or "CFR \u00a7" in c["requested"]:
                    sec = f"{c['part']}.{c['minor']}" if c["minor"] else c["part"]
                    c = dict(c, title=None, canonical=f"\u00a7 {sec}{c['paras']}", label=f"\u00a7 {sec}{c['paras']}")
                out.append(c)
            return out
        with mock.patch.object(search, "parse_query_citations", drop), mock.patch.object(evaluate, "parse_query_citations", drop):
            self.assertFalse(evaluate.check_explicit_title_sign(self.index, eq)["passed"])
            self.assertFalse(evaluate.check_explicit_title_sign(self.index, wrong)["passed"])

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
        (c,) = parse("31 CFR § 3.22")
        self.assertEqual((c["title"], c["canonical"]), ("31", "31 CFR 3.22"))
        for mode in MODES:
            out = self.index.answer("CET1 31 CFR § 3.22", mode=mode, scope="current")  # Title 31 has no Part 3: never Title 12's 3.22
            self.assertNotEqual(out["status"], "evidence_found", mode)
            self.assertIn("citation:31 CFR 3.22", self.gaps(out), mode)
            self.assertEqual([r for r in out["results"] if r["doc_key"] == "ecfr:12:3"] if mode == "exact" else [], [])
        d = self.index.delta("31 CFR § 3.22")
        self.assertEqual((d["evidence_status"], d["baseline"], d["instruments"], d["unavailable"]["reason"]), ("unavailable", None, [], "part_not_acquired"))
        r = self.index.delta("12 CFR § 1010.100")
        self.assertEqual((r["evidence_status"], r["baseline"], r["instruments"]), ("unavailable", None, []))

    def test_correct_title_equivalents_with_and_without_the_sign(self):
        for signed, plain in (("12 CFR § 3.22", "12 CFR 3.22"), ("12 CFR § 3.22(i)", "12 CFR 3.22(i)"), ("31 CFR § 1010.100", "31 CFR 1010.100")):
            a, b = self.index.delta(signed), self.index.delta(plain)
            strip = lambda d: {k: v for k, v in d.items() if k not in ("citation", "requested_citation", "canonical_citation")}
            self.assertEqual(strip(a), strip(b), signed)
            for mode in MODES:
                for scope in SCOPES:
                    x, y = (self.index.answer(q, mode=mode, scope=scope, k=1) for q in (signed, plain))
                    self.assertEqual((x["status"], self.gaps(x)), (y["status"], self.gaps(y)), (signed, mode, scope))
        self.assertEqual(len(self.index.delta("12 CFR § 3.22")["instruments"]), 4)


if __name__ == "__main__":
    unittest.main()
