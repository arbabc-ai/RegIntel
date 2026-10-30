"""Tests for S7-P1-SUFFIX-INTEGRITY-14 (F1): an unclosed numeric designator suffix of any length never establishes its
parent path, in a requested citation or in an amendatory instruction; a complete longer designator keeps its full path;
the original request and instruction text are preserved; ordinary explanatory parentheticals stay distinct."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.fedreg import named_paragraph_paths as paths, parse_fr, paren_suffix
from regintel_p1.search import citation_only, parse_query_citations as parse
from test_p1 import asset
from test_p1_delta_precision import fr
from test_p1_query_citations import ECFR

LENGTHS = (6, 7, 8, 9, 20)
MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")
PARTS = (None, ["12:3"], ["12:217"])
UNREAD = "unclosed_or_malformed_designator_suffix"
ANC = "fr:2026-91000"


def digits(n):
    return "9" * n


def instr(tail):
    return f"1. Amend &#xA7; 3.22 by revising paragraph {tail} to read as follows:"


def unclosed(n):
    return f"12 CFR 3.22(a)({digits(n)}"


class ParserTests(unittest.TestCase):
    def test_unclosed_suffix_of_every_length_is_never_its_ancestor(self):
        for n in LENGTHS:
            d = digits(n)
            for q in (f"CET1 12 CFR 3.22(a)({d}", f"12 CFR 3.22(a)( {d}", f"12 CFR 3.22(a)(  {d}  ", f"12 CFR 3.22(a)\n({d}",
                      f"12 CFR 3.22(a) ({d}", f"12 CFR 3.22(a)({d} and more"):
                (c,) = parse(q)
                self.assertEqual(c["malformed"], UNREAD, (n, q))
                self.assertFalse(c["diagnostics"]["accepted_valid_prefix"], (n, q))
                self.assertNotEqual(c["label"], "12 CFR 3.22(a)", (n, q))
                self.assertIn(d, c["label"], (n, q))
                self.assertEqual(c["diagnostics"]["valid_prefix"], "3.22(a)", (n, q))

    def test_closed_suffix_of_every_length_keeps_the_full_path(self):
        for n in LENGTHS:
            d = digits(n)
            for q in (f"12 CFR 3.22(a)({d})", f"12 CFR 3.22(a)( {d} )", f"12 CFR 3.22(a)(\t{d}\n)"):
                (c,) = parse(q)
                self.assertIsNone(c["malformed"], (n, q))
                self.assertEqual((c["paras"], c["canonical"]), (f"(a)({d})", f"12 CFR 3.22(a)({d})"), (n, q))
                self.assertEqual(c["requested"], q)

    def test_closed_detached_suffix_of_every_length_is_ambiguous_not_the_ancestor(self):
        for n in LENGTHS:
            (c,) = parse(f"12 CFR 3.22(a) ({digits(n)})")
            self.assertEqual(c["malformed"], "detached_designator_suffix", n)
            self.assertFalse(c["diagnostics"]["accepted_valid_prefix"], n)

    def test_the_request_is_a_gap_whatever_its_context(self):
        q = "CET1 12 CFR 3.22(a)(9999999"
        (c,) = parse(q)
        self.assertEqual((c["malformed"], c["label"], c["requested"]), (UNREAD, "12 CFR 3.22(a)(9999999", "12 CFR 3.22(a)(9999999"))
        self.assertFalse(citation_only(q))
        self.assertTrue(citation_only("12 CFR 3.22(a)(9999999"))

    def test_instruction_with_an_unclosed_suffix_never_names_the_ancestor(self):
        for n in LENGTHS:
            d = digits(n)
            self.assertEqual(paths(f"Amend § 3.22 by revising paragraph (a)({d} to read as follows:"), [], n)
            self.assertEqual(paths(f"Amend § 3.22 by revising paragraph (a)( {d} to read as follows:"), [], n)
            self.assertEqual(paths(f"Amend § 3.22 by revising paragraphs (a)({d} and (b) to read as follows:"), [], n)
            self.assertEqual(paths(f"Amend § 3.22 by revising paragraph (a)({d}) to read as follows:"), [f"({'a'})({d})"], n)
            self.assertEqual(paths(f"Amend § 3.22 by revising paragraph (a)( {d} ) to read as follows:"), [f"(a)({d})"], n)
        self.assertEqual(paths("Amend § 3.22 by revising paragraph (a)(9999999 to read as follows:"), [])

    def test_instruction_names_everything_before_an_unclosed_item_only(self):
        self.assertEqual(paths("Revise paragraphs (b), (a)(9999999 and (c)."), ["(b)"])

    def test_original_instruction_text_is_preserved_in_the_parsed_unit(self):
        text = "1. Amend &#xA7; 3.22 by revising paragraph (a)(9999999 to read as follows:"
        units = parse_fr(fr("RULE", text, [("3.22", ["(a) U text."])]), "2026-91999")
        flat = json.dumps(units, default=str)
        self.assertIn("(a)(9999999 to read as follows", flat)

    def test_explanatory_parentheticals_stay_distinct(self):
        for q in ("12 CFR 3.22(a)(introductory text)", "12 CFR 3.22(a) (introductory text)", "12 CFR 3.22(a) (see below)",
                  "12 CFR 3.22(a) (the capital rule)", "12 CFR 3.22 (the capital rule)"):
            (c,) = parse(q)
            self.assertIsNone(c["malformed"], q)
        self.assertEqual(paths("Revise paragraph (a)(introductory text)."), ["(a)"])
        self.assertEqual(paths("Revise paragraph (a)(introductory text) and (b)."), ["(a)"])  # unchanged from the previous commit
        # SUFFIX-BOUNDARY-15: this used to expect ["(a)"]. That accepted the ancestor of an unclosed `(introd`
        # because the later, unrelated ")" of "(b)" was taken to close it. An unclosed glued parenthetical is an
        # unread suffix whatever follows it, so the instruction names neither (a) nor (b).
        self.assertEqual(paths("Revise paragraph (a)(introd and (b)."), [])

    def test_canonical_citations_and_instructions_are_unchanged(self):
        for q in ("12 CFR 3.22", "12 CFR 3.22(a)", "§ 3.1(c)(4)(i)", "12 CFR Part 3"):
            (c,) = parse(q)
            self.assertEqual((c["label"], c["malformed"]), (q, None))
        self.assertEqual(paths("Amend § 3.22 by revising paragraph (a)(1)(i) to read as follows:"), ["(a)(1)(i)"])
        self.assertEqual(paren_suffix("(a)(1)", 0), ("complete", "a", 3))
        self.assertIsNone(paren_suffix("abc", 0))


def fixture_docs(root):
    meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
    final = {**meta, "document_type": "Rule", "effective_on": "2027-02-01", "action": "Final rule."}
    docs = {"ecfr:12:3": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 3", "title": "Capital",
                          "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                          "asset": asset(root, "e.html", ECFR.encode())}}
    anc = fr("RULE", instr("(a)"), [("3.22", ["(a) Ancestor-only text."])])
    docs[ANC] = {**final, "document_number": "2026-91000", "title": "Ancestor", "asset": asset(root, "anc.xml", anc)}
    keys = {}
    for i, n in enumerate(LENGTHS):
        d = digits(n)
        u = fr("RULE", instr(f"(a)({d}"), [("3.22", ["(a) * * *", f"({d}) Unclosed text {n}."])])
        c = fr("RULE", instr(f"(a)({d})"), [("3.22", ["(a) * * *", f"({d}) Closed text {n}."])])
        uk, ck = f"fr:2026-9{i}100", f"fr:2026-9{i}200"
        docs[uk] = {**final, "document_number": uk[3:], "title": f"U{n}", "asset": asset(root, f"u{n}.xml", u)}
        docs[ck] = {**final, "document_number": ck[3:], "title": f"C{n}", "asset": asset(root, f"c{n}.xml", c)}
        keys[n] = (uk, ck)
    return docs, keys


class SuffixIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        docs, cls.keys = fixture_docs(root)
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "s.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rec(self, q, scope, parts=None):
        return self.index.resolve_citations(q, scope, parts)[0]

    def test_indexed_future_support_follows_the_complete_path_only(self):
        for n in LENGTHS:
            uk, ck = self.keys[n]
            fut = self.rec(f"12 CFR 3.22(a)({digits(n)})", "future")
            self.assertTrue(fut["resolved"], n)
            self.assertEqual(sorted({s["doc_key"] for s in fut["future_paragraph_support"]}), [ck], n)
            self.assertNotIn(uk, {s["doc_key"] for s in fut["future_paragraph_support"]})
            self.assertNotIn(ANC, {s["doc_key"] for s in fut["future_paragraph_support"]})
            self.assertFalse(self.rec(f"12 CFR 3.22(a)({digits(n)})", "current")["resolved"], n)

    def test_the_unclosed_instrument_supports_no_paragraph_but_the_ancestor_still_has_its_own(self):
        anc = self.rec("12 CFR 3.22(a)", "future")
        self.assertTrue(anc["resolved"])
        self.assertEqual(sorted({s["doc_key"] for s in anc["future_paragraph_support"]}), [ANC])  # independent instrument naming (a)
        for n in LENGTHS:
            uk, _ = self.keys[n]
            self.assertNotIn(uk, {s["doc_key"] for s in anc["future_paragraph_support"]}, n)
            fut = self.rec(f"12 CFR 3.22(a)({digits(n)})", "future")
            self.assertNotIn(uk, {s["doc_key"] for s in fut["future_paragraph_support"]}, n)

    def test_unclosed_request_never_resolves_and_never_returns_evidence_found(self):
        for n in LENGTHS:
            q = f"CET1 {unclosed(n)}"
            label = unclosed(n)
            for scope in SCOPES:
                r = self.rec(q, scope)
                self.assertFalse(r["resolved"], (n, scope))
                self.assertEqual(r["reason"], UNREAD)
                self.assertEqual((r["citation"], r["canonical"]), (label, None))
                self.assertEqual((r["_current_ids"], r["_future_ids"]), ([], []))
                self.assertEqual(self.index.exact(q, 20, scope), [])
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (n, mode, scope, parts, k)
                            self.assertNotEqual(out["status"], "evidence_found", tag)
                            self.assertIn(f"citation:{label}", {g["topic"] for g in out["coverage_gaps"]}, tag)
                            self.assertEqual(out["applicability_status"], "not_assessed")
                            if mode == "exact":
                                self.assertNotIn("3.22(a)", [r["locator"] for r in out["results"]], tag)

    def test_whitespace_variants_of_the_unclosed_request_behave_the_same(self):
        for n in LENGTHS:
            for q in (f"12 CFR 3.22(a)( {digits(n)}", f"12 CFR 3.22(a)(  {digits(n)}  ", f"12 CFR 3.22(a) ({digits(n)}"):
                r = self.rec(q, "both")
                self.assertFalse(r["resolved"], (n, q))
                self.assertEqual(r["reason"], UNREAD, (n, q))
                out = self.index.answer(q, mode="hybrid", scope="both")
                self.assertNotEqual(out["status"], "evidence_found", (n, q))

    def test_whitespace_closed_request_equals_the_canonical_one(self):
        for n in LENGTHS:
            a = self.rec(f"12 CFR 3.22(a)( {digits(n)} )", "future")
            b = self.rec(f"12 CFR 3.22(a)({digits(n)})", "future")
            for key in ("citation", "resolved", "achieved", "reason", "_future_ids"):
                self.assertEqual(a[key], b[key], (n, key))
            self.assertEqual(a["requested"], f"12 CFR 3.22(a)( {digits(n)} )")

    def test_delta_for_the_unclosed_request_names_no_ancestor(self):
        for n in LENGTHS:
            d = self.index.delta(unclosed(n))
            self.assertEqual(d["evidence_status"], "unavailable", n)
            self.assertEqual(d["unavailable"]["reason"], UNREAD)
            self.assertIsNone(d["baseline"])
            self.assertEqual(d["instruments"], [])
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))
            dc = self.index.delta(f"12 CFR 3.22(a)({digits(n)})")
            self.assertEqual(dc["requested_path"], f"(a)({digits(n)})")
            self.assertEqual(sorted(dc["future_precision"]["supported_instruments"]), [self.keys[n][1]], n)

    def test_mixed_valid_and_unresolved_citations_with_small_k_and_filters(self):
        for n in LENGTHS:
            label = unclosed(n)
            q = f"12 CFR 3.22(a) and {label}"
            for mode in MODES:
                for scope in ("future", "both"):
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, k=k)
                        cites = {c["citation"]: c for c in out["citations"]}
                        self.assertTrue(cites["12 CFR 3.22(a)"]["resolved"], (n, mode, scope, k))
                        self.assertFalse(cites[label]["resolved"])
                        self.assertEqual(out["status"], "partial_evidence", (n, mode, scope, k))
                        self.assertEqual({g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:")}, {f"citation:{label}"})
                wrong = self.index.answer(q, mode=mode, scope="future", parts=["12:217"])
                self.assertNotEqual(wrong["status"], "evidence_found")

    def test_closed_and_unclosed_requests_together(self):
        n = 9
        out = self.index.answer(f"12 CFR 3.22(a)({digits(n)}) and {unclosed(7)}", mode="exact", scope="future")
        cites = {c["citation"]: c for c in out["citations"]}
        self.assertTrue(cites[f"12 CFR 3.22(a)({digits(n)})"]["resolved"])
        self.assertFalse(cites[unclosed(7)]["resolved"])
        self.assertEqual(out["status"], "partial_evidence")

    def test_controls_canonical_citation_and_explanatory_parenthetical(self):
        for mode in MODES:
            plain = self.index.answer("12 CFR 3.22(a)", mode=mode, scope="future")
            note = self.index.answer("12 CFR 3.22(a) (introductory text)", mode=mode, scope="future")
            self.assertEqual(plain["status"], "evidence_found")
            self.assertTrue(note["citations"][0]["resolved"])  # the explanation never makes the citation a gap
            self.assertEqual(note["citations"][0]["canonical"], "12 CFR 3.22(a)")
            self.assertFalse([g for g in note["coverage_gaps"] if g["topic"].startswith("citation:")])

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status and status <= {"parsed_unreviewed", "regex_unreviewed"}, status)

    def test_evaluator_group_is_wired_and_fails_when_the_guard_is_broken(self):
        from unittest import mock

        from regintel_p1 import evaluate
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertIn("suffix_integrity", evaluate.REGRESSION_GROUPS)
        self.assertGreaterEqual(reg["version"], "p1-regress-0.13")  # later versions only add checks
        items = {x["id"]: x for x in reg["suffix_integrity"]}
        for key in ("SI01", "SI03", "SI04"):  # the synthetic fixture holds Part 3.22, so these run offline too
            out = evaluate.check_suffix_integrity(self.index, items[key])
            self.assertTrue(out["passed"], (key, out["failures"]))
        self.assertEqual(sorted({n for x in reg["suffix_integrity"] for n in x.get("lengths", [])}), sorted(LENGTHS))
        real = search.parse_query_citations

        def accepts_prefix(q):  # the old behaviour: an unclosed long suffix silently dropped
            return [dict(c, malformed=None, label=c["canonical"] or c["label"]) for c in real(q)]
        with mock.patch.object(evaluate, "parse_query_citations", accepts_prefix):
            self.assertFalse(evaluate.check_suffix_integrity(self.index, items["SI01"])["passed"])


if __name__ == "__main__":
    unittest.main()
