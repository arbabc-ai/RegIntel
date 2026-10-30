"""Tests for S7-P1-SUFFIX-BOUNDARY-15 (F1): a suffix that begins with punctuation (`(a)(-1)`), and an unclosed glued
parenthetical that a later unrelated ")" would otherwise appear to close (`(a)(introd and (b)`), never establish the
ancestor path, in a requested citation or in an amendatory instruction. The request and instruction are preserved,
independent support for the genuine ancestor is untouched, and ordinary closed explanations stay ordinary."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.fedreg import named_paragraph_paths as paths, paren_suffix, parse_fr
from regintel_p1.search import citation_only, parse_query_citations as parse
from test_p1 import asset
from test_p1_delta_precision import fr
from test_p1_query_citations import ECFR

UNREAD = "unclosed_or_malformed_designator_suffix"
MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")
PARTS = (None, ["12:3"], ["12:217"])
BAD_REQUESTS = ("CET1 12 CFR 3.22(a)(-1)", "CET1 12 CFR 3.22(a)(introd and (b)")
BAD_INSTRUCTIONS = ("Revise paragraph (a)(-1).", "Revise paragraph (a)(introd and (b).")
ANC, M1, M2, GOOD = "fr:2026-92000", "fr:2026-92001", "fr:2026-92002", "fr:2026-92003"


def instr(tail):
    return f"1. Amend &#xA7; 3.22 by revising paragraph {tail} to read as follows:"


class ParserTests(unittest.TestCase):
    def test_required_request_reproductions_are_unread_and_never_the_ancestor(self):
        for q, label in (("CET1 12 CFR 3.22(a)(-1)", "12 CFR 3.22(a)(-1)"), ("CET1 12 CFR 3.22(a)(introd and (b)", "12 CFR 3.22(a)(introd")):
            (c,) = parse(q)
            self.assertEqual(c["malformed"], UNREAD, q)
            self.assertEqual(c["label"], label, q)
            self.assertFalse(c["diagnostics"]["accepted_valid_prefix"], q)
            self.assertEqual(c["diagnostics"]["valid_prefix"], "3.22(a)", q)
            self.assertNotEqual(c["label"], "12 CFR 3.22(a)", q)
            self.assertFalse(citation_only(q), q)

    def test_required_instruction_reproductions_name_nothing(self):
        for t in BAD_INSTRUCTIONS:
            self.assertEqual(paths(t), [], t)
        self.assertEqual(paths("Revise paragraphs (b), (a)(-1) and (c)."), ["(b)"])  # what precedes the unread item stands
        self.assertEqual(paths("Revise paragraph (a) (-1)."), [])

    def test_punctuation_led_forms_in_every_position(self):
        for q in ("12 CFR 3.22(a)(-1", "12 CFR 3.22(a)(.)", "12 CFR 3.22(a)(*)", "12 CFR 3.22(a)(-)", "12 CFR 3.22(a)(+1)", "12 CFR 3.22(a)(–1)"):
            (c,) = parse(q)
            self.assertEqual(c["malformed"], UNREAD, q)
            self.assertFalse(c["diagnostics"]["accepted_valid_prefix"], q)
        (d,) = parse("12 CFR 3.22(a) (-1)")
        self.assertEqual(d["malformed"], "detached_designator_suffix")
        for t in ("Revise paragraph (a)(.).", "Revise paragraph (a)(*) and (b).", "Revise paragraph (a)(–1)."):
            self.assertEqual(paths(t), [], t)

    def test_a_later_unrelated_close_never_closes_an_unclosed_parenthetical(self):
        for q in ("12 CFR 3.22(a)(introd and (b)", "12 CFR 3.22(a)(introd or (b) or (c)", "12 CFR 3.22(a)(see (b)", "12 CFR 3.22(a)(1 and (b)"):
            (c,) = parse(q)
            self.assertEqual(c["malformed"], UNREAD, q)
        for t in ("Revise paragraph (a)(introd or (b) or (c).", "Revise paragraph (a)(see (b).", "Revise paragraph (a)(1 and (b)."):
            self.assertEqual(paths(t), [], t)

    def test_a_later_valid_citation_in_the_same_request_is_still_read(self):
        cs = parse("12 CFR 3.22(a)(introd and (b) and 12 CFR 3.1(e)")
        self.assertEqual([(c["label"], c["malformed"]) for c in cs], [("12 CFR 3.22(a)(introd", UNREAD), ("12 CFR 3.1(e)", None)])

    def test_the_original_request_and_instruction_are_preserved(self):
        (c,) = parse("CET1 12 CFR 3.22(a)(-1)")
        self.assertEqual(c["requested"], "12 CFR 3.22(a)(-1)")
        text = "1. Amend &#xA7; 3.22 by revising paragraph (a)(-1) to read as follows:"
        flat = json.dumps(parse_fr(fr("RULE", text, [("3.22", ["(a) U text."])]), "2026-91999"), default=str)
        self.assertIn("(a)(-1) to read as follows", flat)

    def test_controls_closed_explanations_canonical_and_long_designators(self):
        for q in ("12 CFR 3.22(a)(introductory text)", "12 CFR 3.22(a) (introductory text)", "12 CFR 3.22(a)(see below)",
                  "12 CFR 3.22(a) (see below)", "12 CFR 3.22(a)(introductory text) and 12 CFR 3.1(e)"):
            self.assertIsNone(parse(q)[0]["malformed"], q)
        self.assertEqual(paths("Revise paragraph (a)(introductory text)."), ["(a)"])
        self.assertEqual(paths("Revise paragraph (a)(introductory text) and (b)."), ["(a)"])
        self.assertEqual(paths("Revise paragraph (a)(1) and (b)."), ["(a)(1)", "(b)"])
        for n in (6, 7, 8, 9, 20):
            d = "9" * n
            self.assertEqual(paths(f"Revise paragraph (a)({d})."), [f"(a)({d})"])
            self.assertEqual(paths(f"Revise paragraph (a)( {d} )."), [f"(a)({d})"])
            self.assertEqual(parse(f"12 CFR 3.22(a)( {d} )")[0]["canonical"], f"12 CFR 3.22(a)({d})")
            self.assertEqual(parse(f"12 CFR 3.22(a)({d}")[0]["malformed"], UNREAD)
        for q in ("12 CFR 3.22", "12 CFR 3.22(a)", "§ 3.1(c)(4)(i)", "12 CFR 3.1(c)( 4 )(i)"):
            self.assertIsNone(parse(q)[0]["malformed"], q)
        self.assertEqual(paren_suffix("(a)(1)", 0), ("complete", "a", 3))


class SuffixBoundaryIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        final = {**meta, "document_type": "Rule", "effective_on": "2027-02-01", "action": "Final rule."}
        xml = {ANC: fr("RULE", instr("(a)"), [("3.22", ["(a) Ancestor-only text."])]),
               M1: fr("RULE", instr("(a)(-1)"), [("3.22", ["(a) * * *", "(-1) Punctuation text."])]),
               M2: fr("RULE", instr("(a)(introd and (b)"), [("3.22", ["(a) Introd text.", "(b) B text."])]),
               GOOD: fr("RULE", instr("(a)(1)"), [("3.22", ["(a) * * *", "(1) Good one text."])])}
        docs = {"ecfr:12:3": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 3", "title": "Capital",
                              "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                              "asset": asset(root, "e.html", ECFR.encode())}}
        for key, body in xml.items():
            docs[key] = {**final, "document_number": key[3:], "title": key[-4:], "asset": asset(root, key[-4:] + ".xml", body)}
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "b.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rec(self, q, scope, parts=None):
        return self.index.resolve_citations(q, scope, parts)[0]

    def test_malformed_instructions_are_stored_unattributed_and_the_text_is_kept(self):
        for key, frag in ((M1, "(a)(-1)"), (M2, "(a)(introd and (b)")):
            rows = self.index.conn.execute("SELECT p.instruction, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment'", (key,)).fetchall()
            self.assertTrue(rows)
            for ins, ep in rows:
                self.assertIn(frag, ins)  # verbatim instruction preserved
                data = json.loads(ep) if ep else {}
                self.assertNotEqual(data.get("status"), "attributed", key)
                self.assertEqual(data.get("paths", []), [], key)

    def test_malformed_instructions_never_support_the_ancestor_but_the_independent_one_does(self):
        for scope in ("future", "both"):
            r = self.rec("12 CFR 3.22(a)", scope)
            self.assertTrue(r["resolved"], scope)
            self.assertEqual(sorted({s["doc_key"] for s in r["future_paragraph_support"]}), [ANC], scope)
        d = self.index.delta("12 CFR 3.22(a)")
        self.assertEqual(sorted(d["future_precision"]["supported_instruments"]), [ANC])
        for bad in (M1, M2):
            self.assertNotIn(bad, d["future_precision"]["supported_instruments"])
        good = self.rec("12 CFR 3.22(a)(1)", "future")
        self.assertEqual(sorted({s["doc_key"] for s in good["future_paragraph_support"]}), [GOOD])  # a complete path still resolves

    def test_requests_never_resolve_and_never_return_evidence_found(self):
        for q in BAD_REQUESTS:
            label = parse(q)[0]["label"]
            for scope in SCOPES:
                r = self.rec(q, scope)
                self.assertFalse(r["resolved"], (q, scope))
                self.assertEqual((r["reason"], r["citation"], r["canonical"]), (UNREAD, label, None))
                self.assertEqual((r["_current_ids"], r["_future_ids"]), ([], []))
                self.assertEqual(self.index.exact(q, 20, scope), [])
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (q, mode, scope, parts, k)
                            self.assertNotEqual(out["status"], "evidence_found", tag)
                            self.assertIn(f"citation:{label}", {g["topic"] for g in out["coverage_gaps"]}, tag)
                            self.assertEqual(out["applicability_status"], "not_assessed")
                            if mode == "exact":
                                self.assertEqual([r for r in out["results"] if r["locator"] == "3.22(a)"], [], tag)

    def test_delta_returns_no_ancestor_baseline_or_instruments(self):
        for q in BAD_REQUESTS:
            d = self.index.delta(q)
            self.assertEqual(d["evidence_status"], "unavailable", q)
            self.assertIsNone(d["baseline"])
            self.assertEqual(d["instruments"], [])
            self.assertEqual(d["unavailable"]["reason"], UNREAD)
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))
            self.assertFalse(d["current_precision"]["resolved"] or d["future_precision"]["resolved"])

    def test_mixed_valid_and_malformed_citations(self):
        for q in BAD_REQUESTS:
            label = parse(q)[0]["label"]
            mixed = f"12 CFR 3.22(a) and {q}"
            for mode in MODES:
                for scope in ("future", "both"):
                    for k in (10, 1):
                        out = self.index.answer(mixed, mode=mode, scope=scope, k=k)
                        cites = {c["citation"]: c for c in out["citations"]}
                        self.assertTrue(cites["12 CFR 3.22(a)"]["resolved"], (q, mode, scope, k))
                        self.assertFalse(cites[label]["resolved"])
                        self.assertEqual(out["status"], "partial_evidence", (q, mode, scope, k))
                        self.assertEqual({g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:")}, {f"citation:{label}"})
                wrong = self.index.answer(mixed, mode=mode, scope="future", parts=["12:217"])
                self.assertNotEqual(wrong["status"], "evidence_found")

    def test_controls_explanation_and_canonical(self):
        for mode in MODES:
            plain = self.index.answer("12 CFR 3.22(a)", mode=mode, scope="future")
            note = self.index.answer("12 CFR 3.22(a)(introductory text)", mode=mode, scope="future")
            self.assertEqual(plain["status"], "evidence_found")
            self.assertTrue(note["citations"][0]["resolved"])
            self.assertEqual(note["citations"][0]["canonical"], "12 CFR 3.22(a)")
            self.assertFalse([g for g in note["coverage_gaps"] if g["topic"].startswith("citation:")])

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status and status <= {"parsed_unreviewed", "regex_unreviewed"}, status)

    def test_evaluator_checks_are_registered_and_fail_when_the_guard_is_broken(self):
        from unittest import mock

        from regintel_p1 import evaluate
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertGreaterEqual(reg["version"], "p1-regress-0.14")  # later versions only add checks
        self.assertIn("suffix_integrity", evaluate.REGRESSION_GROUPS)
        items = {x["id"]: x for x in reg["suffix_integrity"]}
        self.assertTrue({"SB01", "SB02", "SB03"} <= set(items))
        out = evaluate.check_suffix_integrity(self.index, items["SB01"])
        self.assertTrue(out["passed"], out["failures"])
        real_parse = search.parse_query_citations

        def accepts_prefix(q):  # the previous behaviour: the unclosed/punctuation-led suffix silently dropped
            return [dict(c, malformed=None, label=c["canonical"] or c["label"]) for c in real_parse(q)]
        with mock.patch.object(evaluate, "parse_query_citations", accepts_prefix):
            self.assertFalse(evaluate.check_suffix_integrity(self.index, items["SB01"])["passed"])
        with mock.patch("regintel_p1.fedreg.named_paragraph_paths", lambda t: ["(a)"]):
            self.assertFalse(evaluate.check_suffix_integrity(self.index, items["SB01"])["passed"])


if __name__ == "__main__":
    unittest.main()
