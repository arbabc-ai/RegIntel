"""Tests for S7-P1-PUNCTUATED-CFR-TITLE-27 (F1): `N C.F.R.` is read exactly like `N CFR` (signed, unsigned, paragraph, part, list
and range forms). The explicit title survives, the requested span keeps the punctuation, the citation canonicalizes to CFR, and a
title that lacks the section never resolves through another title."""
import json
import re
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
SPELLINGS = ("C.F.R.", "C. F. R.", "c.f.r.", "C.F.R")
UNPUNCTUATED = ("31 CFR § 3.22", "31 CFR 3.22", "12 CFR § 3.22", "12 CFR § 3.22(a)", "31 CFR Part 3", "12 CFR §§ 3.22, 3.1", "31 CFR Parts 1010 and 1020",
                "12 CFR § 3.22(a) and (z)", "31 CFR § 1010.100", "12 CFR § 6.1")
WRONG = ("31 C.F.R. § 6.1", "12 C.F.R. § 1010.100", "31 C.F.R. 6.1", "12 C.F.R. 1010.100(a)")


def punct(q, spelling="C.F.R."):
    return q.replace("CFR", spelling)


class ParserTests(unittest.TestCase):
    def test_required_reproductions_keep_the_title_and_the_requested_span(self):
        (c,) = parse("31 C.F.R. § 3.22")
        self.assertEqual((c["title"], c["canonical"], c["requested"], c["malformed"]), ("31", "31 CFR 3.22", "31 C.F.R. § 3.22", None))
        (c,) = parse("CET1 12 C.F.R. § 1010.100")
        self.assertEqual((c["title"], c["canonical"], c["requested"]), ("12", "12 CFR 1010.100", "12 C.F.R. § 1010.100"))

    def test_every_spelling_equals_the_unpunctuated_form(self):
        for q in UNPUNCTUATED:
            base = parse(q)
            for sp in SPELLINGS:
                got = parse(punct(q, sp))
                self.assertEqual([(c["title"], c["part"], c["minor"], c["paras"], c["canonical"], c["malformed"]) for c in got],
                                 [(c["title"], c["part"], c["minor"], c["paras"], c["canonical"], c["malformed"]) for c in base], (q, sp))
                if len(base) == 1:
                    self.assertEqual(got[0]["requested"], punct(base[0]["requested"], sp), (q, sp))  # the original span, punctuation included
                self.assertEqual([c["inherited"] and c["inherited"]["kind"] for c in got], [c["inherited"] and c["inherited"]["kind"] for c in base], (q, sp))

    def test_unsigned_punctuated_forms_are_no_longer_dropped(self):
        for q in ("31 C.F.R. 3.22", "31 C.F.R. Part 3", "31 C.F.R.3.22", "31 C.F.R. Parts 1010 and 1020"):
            self.assertTrue(parse(q), q)  # these parsed no citation at all before
        self.assertEqual([c["canonical"] for c in parse("31 C.F.R. Parts 1010, 1020, and 1030")], ["31 CFR Part 1010", "31 CFR Part 1020", "31 CFR Part 1030"])

    def test_lists_ranges_and_paragraph_shorthand_keep_the_title(self):
        cs = parse("31 C.F.R. §§ 3.22, 3.12")
        self.assertEqual([(c["canonical"], c["requested"]) for c in cs], [("31 CFR 3.22", "31 C.F.R. §§ 3.22"), ("31 CFR 3.12", "3.12")])
        for q in ("31 C.F.R. § 3.22 through § 3.30", "31 C.F.R. Part 3-6", "31 C.F.R. Part 1010 to 1020", "31 C.F.R. § 3.22(a) through (z)"):
            (c,) = parse(q)
            self.assertEqual((c["malformed"], c["title"], c["requested"]), ("unsupported_range", "31", q), q)
        self.assertEqual(parse("31 C.F.R. § 3.22 through § 3.30")[0]["diagnostics"]["range"]["end"], "31 CFR 3.30")

    def test_independent_citations_and_the_separator_sign_contract_are_unchanged(self):
        cs = parse("31 C.F.R. § 3.22 and 12 C.F.R. § 3.1")
        self.assertEqual([c["canonical"] for c in cs], ["31 CFR 3.22", "12 CFR 3.1"])
        cs = parse("12 C.F.R. 3.1 and § 217.2(a)")  # a sign after a separator still starts its own, titleless citation
        self.assertEqual([(c["label"], c["title"]) for c in cs], [("12 CFR 3.1", "12"), ("§ 217.2(a)", None)])
        for q in ("§ 3.22", "see § 3.22(a)"):
            self.assertIsNone(parse(q)[0]["title"], q)

    def test_non_citations_are_not_read(self):
        for q in ("31 CF 3.22", "31 CFRS 3.22", "31 C.F. 3.22", "3.22 C.F.R.", "abc C.F.R. 3.22", "31 FR 3.22"):
            self.assertTrue(all(c["title"] != "31" for c in parse(q)), q)

    def test_citation_only_matches_the_unpunctuated_form(self):
        for q in UNPUNCTUATED:
            self.assertEqual(citation_only(punct(q)), citation_only(q), q)
        self.assertTrue(citation_only("31 C.F.R. § 3.22"))
        self.assertFalse(citation_only("capital 31 C.F.R. § 3.22"))


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

    def test_punctuated_and_plain_forms_are_identical_everywhere(self):
        for plain in UNPUNCTUATED:
            q = punct(plain)
            for scope in SCOPES:
                for parts in PARTS:
                    self.assertEqual([self.sig(r) for r in self.index.resolve_citations(q, scope, parts)], [self.sig(r) for r in self.index.resolve_citations(plain, scope, parts)], (q, scope, parts))
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            a, b = (self.index.answer(x, mode=mode, scope=scope, parts=parts, k=k) for x in (q, plain))
                            tag = (q, mode, scope, parts, k)
                            self.assertEqual(self.gaps(a), self.gaps(b), tag)
                            self.assertEqual(a["status"] == "evidence_found", b["status"] == "evidence_found", tag)
                            if mode == "exact":  # hybrid also fuses lexical hits on the word "CFR", which `C.F.R.` does not tokenize to
                                self.assertEqual((a["status"], [r["provision_id"] for r in a["results"]]), (b["status"], [r["provision_id"] for r in b["results"]]), tag)
            a, b = self.index.delta(q), self.index.delta(plain)
            key = lambda d: (d["evidence_status"], (d["baseline"] or {}).get("doc_key"), [g["doc_key"] for g in d["instruments"]], d["current_precision"], d["future_precision"],
                             (d["unavailable"] or {}).get("reason"), [c["label"] for c in (d["unavailable"] or {}).get("citations", [])])
            self.assertEqual(key(a), key(b), q)
            if len(parse(plain)) == 1 and a["evidence_status"] != "unavailable":
                self.assertEqual(self.strip(a), self.strip(b), q)  # single citations: the whole delta, apart from the label fields

    def test_every_spelling_resolves_like_cfr(self):
        base = self.index.resolve_citations("31 CFR § 3.22", "both")
        for sp in SPELLINGS:
            for form in ("31 {} § 3.22", "31 {} 3.22", "31 {}§ 3.22"):
                self.assertEqual([self.sig(r) for r in self.index.resolve_citations(form.format(sp), "both")], [self.sig(r) for r in base], (form, sp))

    def test_each_title_keeps_its_own_evidence_under_the_punctuated_spelling(self):
        text = lambda q: " ".join(self.index.evidence(i)["text"] for i in self.index.resolve_citations(q, "current")[0]["_current_ids"])
        self.assertIn("Treasury general adjustments", text("31 C.F.R. § 3.22"))
        self.assertNotIn("Twelve", text("31 C.F.R. § 3.22"))
        self.assertIn("Twelve general adjustments", text("12 C.F.R. § 3.22"))
        self.assertEqual(self.index.delta("31 C.F.R. § 3.22")["baseline"]["doc_key"], "ecfr:31:3")
        self.assertEqual([g["doc_key"] for g in self.index.delta("31 C.F.R. § 3.22")["instruments"]], [KEYS[1]])

    def test_wrong_title_requests_keep_their_titled_gap_and_return_nothing_through_another_title(self):
        for q in WRONG:
            label = parse(q)[0]["label"]
            self.assertTrue(label.startswith(("12 CFR", "31 CFR")), q)
            for scope in SCOPES:
                for parts in PARTS:
                    r = self.index.resolve_citations(q, scope, parts)[0]
                    self.assertEqual((r["resolved"], r["_current_ids"], r["_future_ids"]), (False, [], []), (q, scope, parts))
            for mode in MODES:
                for scope in SCOPES:
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, k=k)
                        self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope, k))
                        self.assertIn(f"citation:{label}", self.gaps(out), (q, mode, scope, k))  # a hybrid contextual hit does not conceal it
                        if mode == "exact":
                            self.assertEqual(out["results"], [], (q, scope, k))
            d = self.index.delta(q)
            self.assertEqual((d["evidence_status"], d["baseline"], d["section_context"], d["instruments"]), ("unavailable", None, None, []), q)

    def test_mixed_cet1_requests_cannot_conceal_a_wrong_title(self):
        for q in ("CET1 31 C.F.R. § 6.1", "CET1 capital 12 C.F.R. § 1010.100", "CET1 31 C.F.R. § 6.1 and 12 C.F.R. § 3.22"):
            for mode in MODES:
                for scope in SCOPES:
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, k=k)
                        self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope, k))
                        self.assertTrue(self.gaps(out), (q, mode, scope, k))
        ok = self.index.answer("CET1 12 C.F.R. § 3.22", mode="exact", scope="current")
        self.assertEqual(self.gaps(ok), [])

    def test_lists_and_titleless_resolution(self):
        out = self.index.answer("31 C.F.R. §§ 3.22, 6.1", mode="exact", scope="current")
        self.assertEqual((out["status"], self.gaps(out)), ("partial_evidence", ["citation:31 CFR 6.1"]))
        self.assertEqual({r["doc_key"] for r in out["results"]}, {"ecfr:31:3"})
        self.assertEqual(self.index.resolve_citations("§ 3.22", "current")[0]["reason"], "ambiguous_title")
        self.assertTrue(self.index.resolve_citations("§ 6.1", "current")[0]["resolved"])

    def test_evaluator_group_is_registered_and_fails_when_the_punctuated_title_is_dropped(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("punctuated_cfr_title", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["punctuated_cfr_title"]], [f"PC0{n}" for n in range(1, 5)])
        eq = {"id": "T1", "kind": "signed_equivalents", "pairs": [["31 C.F.R. § 3.22", "31 CFR 3.22"], ["12 C.F.R. 3.22(a)", "12 CFR 3.22(a)"]]}
        wrong = {"id": "T2", "kind": "wrong_title", "requests": ["31 C.F.R. § 6.1", "12 C.F.R. § 1010.100"]}
        self.assertTrue(evaluate.check_explicit_title_sign(self.index, eq)["passed"])
        self.assertTrue(evaluate.check_explicit_title_sign(self.index, wrong)["passed"])
        real = search.parse_query_citations

        def starting(q):  # the starting parser: only `CFR` carries a title, so `C.F.R. § n` is a titleless tail
            out = []
            for c in real(q):
                if "C.F.R" in c["requested"].upper():
                    sec = f"{c['part']}.{c['minor']}" if c["minor"] else c["part"]
                    c = dict(c, title=None, canonical=f"§ {sec}{c['paras']}", label=f"§ {sec}{c['paras']}", requested=f"§ {sec}")
                out.append(c)
            return out
        with mock.patch.object(search, "parse_query_citations", starting), mock.patch.object(evaluate, "parse_query_citations", starting):
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
        for q, wrong_doc in (("CET1 31 C.F.R. § 3.22", "ecfr:12:3"), ("CET1 12 C.F.R. § 1010.100", "ecfr:31:1010")):
            label = parse(q)[0]["label"]
            for mode in MODES:
                for scope in SCOPES:
                    out = self.index.answer(q, mode=mode, scope=scope, k=1)
                    self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope))
                    self.assertIn(f"citation:{label}", self.gaps(out), (q, mode, scope))
                    if mode == "exact":
                        self.assertEqual(out["results"], [], (q, scope))
            d = self.index.delta(q.replace("CET1 ", ""))
            self.assertEqual((d["evidence_status"], d["baseline"], d["instruments"]), ("unavailable", None, []), q)

    def test_correct_title_equivalents_match_the_unpunctuated_forms(self):
        strip = lambda d: {k: v for k, v in d.items() if k not in ("citation", "requested_citation", "canonical_citation")}
        for plain in ("12 CFR § 3.22", "12 CFR § 3.22(i)", "31 CFR § 1010.100", "12 CFR 252.44", "12 CFR Part 3"):
            q = punct(plain)
            self.assertEqual(strip(self.index.delta(q)), strip(self.index.delta(plain)), q)
            for mode in MODES:
                for scope in SCOPES:
                    x, y = (self.index.answer(s, mode=mode, scope=scope, k=1) for s in (q, plain))
                    self.assertEqual((x["status"], self.gaps(x)), (y["status"], self.gaps(y)), (q, mode, scope))
        self.assertEqual(len(self.index.delta("12 C.F.R. § 3.22")["instruments"]), 4)


if __name__ == "__main__":
    unittest.main()
