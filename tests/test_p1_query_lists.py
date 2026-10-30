"""Tests for S7-P1-QUERY-LIST-COMPLETENESS-18 (F1): every member of a comma/and/or section list and of paragraph shorthand is
accounted for. A bare section inherits the title of the citation before it; a bare paragraph designator inherits section and
hierarchy level only when the CFR hierarchy settles it. Otherwise the member is a structured unresolved expression with a
coverage gap. The resolved first member is never silently accepted as the whole request."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.search import citation_only, parse_query_citations as parse
from test_p1 import asset
from test_p1_delta_precision import fr

MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")
PARTS = (None, ["12:3"], ["12:6"], ["12:217"])

ECFR_3 = """<div class="part" id="part-3"><h1>PART 3—CAPITAL</h1>
<div class="section" id="3.1"><h4>§ 3.1 Purpose.</h4>
<div id="p-3.1(a)"><p class="indent-1" data-title="3.1(a)">(a) Purpose text.</p></div>
<div id="p-3.1(c)"><p class="indent-1" data-title="3.1(c)">(c) Applicability.</p>
<div id="p-3.1(c)(4)"><p class="indent-2" data-title="3.1(c)(4)">(4) Transition.</p>
<div id="p-3.1(c)(4)(i)"><p class="indent-3" data-title="3.1(c)(4)(i)">(i) First transition item.</p></div></div></div>
<div id="p-3.1(e)"><p class="indent-1" data-title="3.1(e)">(e) Existing e text.</p></div>
<div id="p-3.1(f)"><p class="indent-1" data-title="3.1(f)">(f) Existing f text.</p></div></div>
<div class="section" id="3.22"><h4>§ 3.22 Adjustments and deductions.</h4>
<div id="p-3.22(a)"><p class="indent-1" data-title="3.22(a)">(a) Twelve general adjustments.</p></div></div></div>"""
ECFR_6 = """<div class="part" id="part-6"><h1>PART 6—PROMPT CORRECTIVE ACTION</h1>
<div class="section" id="6.1"><h4>§ 6.1 Authority.</h4>
<div id="p-6.1(a)"><p class="indent-1" data-title="6.1(a)">(a) Six a text.</p></div>
<div id="p-6.1(b)"><p class="indent-1" data-title="6.1(b)">(b) Six b text.</p></div></div></div>"""
ECFR_31 = """<div class="part" id="part-3"><h1>PART 3—TREASURY RULES</h1>
<div class="section" id="3.22"><h4>§ 3.22 Treasury adjustments.</h4>
<div id="p-3.22(a)"><p class="indent-1" data-title="3.22(a)">(a) Treasury general adjustments.</p></div></div></div>"""
F1 = fr("RULE", "1. Amend &#xA7; 3.22 by revising paragraph (a) to read as follows:", [("3.22", ["(a) New 3.22(a) text."])])
F2 = fr("PRORULE", "1. Amend &#xA7; 6.1 by revising paragraph (b) to read as follows:", [("6.1", ["(a) * * *", "(b) New 6.1(b) text."])], part="6")
KEYS = ("fr:2026-95001", "fr:2026-95002")


def sig(r):
    return (r["resolved"], r["achieved"], r["reason"], r["by_scope"], r["future_paragraph_support"])


class ParserTests(unittest.TestCase):
    def labels(self, q):
        return [(c["label"], c["malformed"]) for c in parse(q)]

    def test_required_reproductions_are_accounted_for_member_by_member(self):
        self.assertEqual(self.labels("12 CFR 3.1, 6.1"), [("12 CFR 3.1", None), ("12 CFR 6.1", None)])
        self.assertEqual(self.labels("12 CFR 3.22(a) and (z)"), [("12 CFR 3.22(a)", None), ("12 CFR 3.22(z)", None)])
        a, b = parse("12 CFR 3.1, 6.1")
        self.assertEqual((b["requested"], b["canonical"], b["inherited"]["kind"], b["inherited"]["from"]), ("6.1", "12 CFR 6.1", "section", "12 CFR 3.1"))
        z = parse("12 CFR 3.22(a) and (z)")[1]
        self.assertEqual((z["requested"], z["paras"], z["inherited"]["inherited_path"]), ("(z)", "(z)", "(z)"))

    def test_shorthand_equals_the_fully_qualified_citation(self):
        for short, full in (("12 CFR 3.1, 6.1", "12 CFR 3.1 and 12 CFR 6.1"), ("12 CFR 3.22(a) and (z)", "12 CFR 3.22(a) and 12 CFR 3.22(z)"),
                            ("12 CFR 3.1(c)(4)(i), (e) and (f)", "12 CFR 3.1(c)(4)(i), 12 CFR 3.1(e) and 12 CFR 3.1(f)"),
                            ("§ 3.1, 3.22 and 6.1", "§ 3.1, § 3.22 and § 6.1"), ("12 CFR 3.1, 6.1(a) and (b)", "12 CFR 3.1, 12 CFR 6.1(a), 12 CFR 6.1(b)"),
                            ("12 CFR 3.1 or 6.1; 3.22", "12 CFR 3.1, 12 CFR 6.1, 12 CFR 3.22")):
            self.assertEqual([(c["canonical"], c["paras"], c["malformed"]) for c in parse(short)],
                             [(c["canonical"], c["paras"], c["malformed"]) for c in parse(full)], short)

    def test_requested_text_of_each_member_is_preserved(self):
        q = "see 12 CFR 3.22(a) and (z), then 12 CFR 3.1."
        cs = parse(q)
        self.assertEqual([c["requested"] for c in cs], ["12 CFR 3.22(a)", "(z)", "12 CFR 3.1"])
        self.assertTrue(all(c["requested"] in q for c in cs))

    def test_unresolved_expressions_are_structured_not_guessed(self):
        cases = {"12 CFR 3.1(c)(4)(i) and (v)": ("ambiguous_shorthand", "(v)", "12 CFR 3.1(c)(4)(i)"),
                 "12 CFR 3.22(a) and (2)": ("ambiguous_shorthand", "(2)", "12 CFR 3.22(a)"),
                 "12 CFR 3.22 and (a)": ("shorthand_without_paragraph_context", "(a)", "12 CFR 3.22"),
                 "12 CFR Part 3, (a)": ("shorthand_without_paragraph_context", "(a)", "12 CFR Part 3")}
        for q, (reason, expr, prev) in cases.items():
            c = parse(q)[1]
            self.assertEqual((c["malformed"], c["requested"]), (reason, expr), q)
            self.assertIsNone(c["canonical"])
            d = c["diagnostics"]
            self.assertEqual((d["expression"], d["inherits_from"], d["accepted_valid_prefix"]), (expr, prev, False))
            self.assertEqual(c["label"], f"{expr} after {prev}")
        self.assertEqual(parse("12 CFR 3.1(c)(4)(i) and (v)")[1]["diagnostics"]["candidate_paths"], ["3.1(c)(4)(v)", "3.1(v)"])

    def test_the_members_after_an_unsettled_one_are_not_dropped(self):
        cs = parse("12 CFR 3.1(c)(4)(i) and (v) and (e)")
        self.assertEqual([c["malformed"] for c in cs], [None, "ambiguous_shorthand", "ambiguous_shorthand"])
        self.assertEqual([c["requested"] for c in cs], ["12 CFR 3.1(c)(4)(i)", "(v)", "(e)"])

    def test_malformed_tails_are_unresolved_and_stop_the_list(self):
        for q in ("12 CFR 3.22(a) and (z", "12 CFR 3.22(a), 6.1(", "12 CFR 3.22(a) and (z)("):
            cs = parse(q)
            self.assertTrue(cs[-1]["malformed"], q)
            self.assertFalse(cs[-1]["diagnostics"]["accepted_valid_prefix"], q)
        self.assertEqual([c["malformed"] for c in parse("12 CFR 3.1, 6.1(-1)")], [None, "unclosed_or_malformed_designator_suffix"])

    def test_title_ambiguity_is_preserved_for_titleless_lists(self):
        a, b = parse("§ 3.22, 6.1")
        self.assertEqual((a["title"], b["title"], b["canonical"]), (None, None, "§ 6.1"))  # no title is invented for either member

    def test_non_members_are_left_alone(self):
        for q in ("12 CFR 3.1, 1.5 percent", "12 CFR 3.1 and the rule", "12 CFR 3.22(a) or (introductory text)", "12 CFR 3.1, in 2026", "12 CFR 3.1 and (see below)"):
            self.assertEqual(len(parse(q)), 1, q)
            self.assertIsNone(parse(q)[0]["malformed"], q)

    def test_single_citation_parsing_is_unchanged(self):
        for q, want in (("12 CFR 3.1", "12 CFR 3.1"), ("§ 3.1(e)", "§ 3.1(e)"), ("12 CFR Part 3", "12 CFR Part 3"), ("12 CFR 3.12(c)(1)", "12 CFR 3.12(c)(1)"),
                        ("12 CFR 3.22(a)(9999999", "12 CFR 3.22(a)(9999999"), ("12 CFR 3.1(c)( 4 )(i)", "12 CFR 3.1(c)(4)(i)")):
            cs = parse(q)
            self.assertEqual(len(cs), 1, q)
            self.assertEqual(cs[0]["label"], want, q)
            self.assertIsNone(cs[0]["inherited"], q)

    def test_citation_only_accounts_for_the_members(self):
        for q in ("12 CFR 3.1, 6.1", "12 CFR 3.22(a) and (z)", "12 CFR 3.1(c)(4)(i) and (v)", "§§ 3.1, 3.22 and 6.1"):
            self.assertTrue(citation_only(q), q)
        self.assertFalse(citation_only("capital 12 CFR 3.1, 6.1"))


class ListIndexTests(unittest.TestCase):
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

    def recs(self, q, scope, parts=None):
        return self.index.resolve_citations(q, scope, parts)

    def gaps(self, out):
        return sorted(g["topic"] for g in out["coverage_gaps"] if g["topic"].startswith("citation:"))

    # ---- shorthand against fully qualified controls
    def test_shorthand_resolves_exactly_like_the_fully_qualified_control(self):
        pairs = (("12 CFR 3.1, 6.1", "12 CFR 3.1 and 12 CFR 6.1"), ("12 CFR 3.22(a) and (z)", "12 CFR 3.22(a) and 12 CFR 3.22(z)"),
                 ("12 CFR 3.1, 7.9", "12 CFR 3.1 and 12 CFR 7.9"), ("12 CFR 3.1(c)(4)(i), (e) and (f)", "12 CFR 3.1(c)(4)(i), 12 CFR 3.1(e), 12 CFR 3.1(f)"),
                 ("12 CFR 3.1, 6.1(a) and (b)", "12 CFR 3.1, 12 CFR 6.1(a), 12 CFR 6.1(b)"))
        for short, full in pairs:
            for scope in SCOPES:
                for parts in PARTS:
                    a, b = self.recs(short, scope, parts), self.recs(full, scope, parts)
                    self.assertEqual([r["citation"] for r in a], [r["citation"] for r in b], (short, scope, parts))
                    self.assertEqual([sig(r) for r in a], [sig(r) for r in b], (short, scope, parts))
                    self.assertEqual([(r["_current_ids"], r["_future_ids"]) for r in a], [(r["_current_ids"], r["_future_ids"]) for r in b])
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            x, y = (self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k) for q in (short, full))
                            tag = (short, mode, scope, parts, k)
                            self.assertEqual(x["status"], y["status"], tag)
                            self.assertEqual(self.gaps(x), self.gaps(y), tag)
                            if mode == "exact":
                                self.assertEqual([r["provision_id"] for r in x["results"]], [r["provision_id"] for r in y["results"]], tag)

    def test_required_reproductions_never_return_evidence_found_with_a_missing_member(self):
        for q, gap in (("12 CFR 3.1, 6.1", None), ("12 CFR 3.22(a) and (z)", "citation:12 CFR 3.22(z)"), ("12 CFR 3.1, 7.9", "citation:12 CFR 7.9")):
            for mode in MODES:
                for scope in ("current", "both"):
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, k=k)
                        cites = {c["citation"]: c for c in out["citations"]}
                        self.assertEqual(len(cites), 2, (q, mode, scope))  # every member is accounted for
                        if gap:
                            self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope, k))
                            self.assertIn(gap, self.gaps(out))

    def test_fully_acquired_lists_are_evidence_found_and_list_every_member(self):
        for q in ("12 CFR 3.1, 6.1", "12 CFR 3.1(c)(4)(i), (e) and (f)", "12 CFR 3.1, 6.1(a) and (b)", "§ 3.1, 6.1 and 3.22(a)" if False else "§ 3.1 and 6.1"):
            for mode in MODES:
                out = self.index.answer(q, mode=mode, scope="current", k=10)
                self.assertEqual(self.gaps(out), [], (q, mode))
                self.assertTrue(all(c["resolved"] for c in out["citations"]), (q, mode))
                self.assertEqual(out["status"], "evidence_found", (q, mode))
        out = self.index.answer("12 CFR 3.1, 6.1", mode="exact", scope="current", k=10)
        self.assertEqual({r["doc_key"] for r in out["results"]}, {"ecfr:12:3", "ecfr:12:6"})
        small = self.index.answer("12 CFR 3.1, 6.1", mode="exact", scope="current", k=1)
        self.assertEqual(len(small["results"]), 1)

    def test_future_support_follows_each_member(self):
        r = self.recs("12 CFR 3.22(a), 6.1(b)", "future")
        self.assertTrue(r[0]["resolved"] and r[1]["resolved"])
        self.assertEqual([x["doc_key"] for x in r[0]["future_paragraph_support"]], [KEYS[0]])
        self.assertEqual([x["doc_key"] for x in r[1]["future_paragraph_support"]], [KEYS[1]])
        self.assertEqual(sig(self.recs("12 CFR 3.22(a) and (z)", "future")[1])[0], False)

    # ---- unresolved expressions, malformed tails, ambiguity
    def test_ambiguous_inheritance_is_a_structured_unresolved_expression_with_a_gap(self):
        q = "12 CFR 3.1(c)(4)(i) and (v)"
        label = "(v) after 12 CFR 3.1(c)(4)(i)"
        for scope in SCOPES:
            r = self.recs(q, scope)
            self.assertTrue(r[0]["resolved"] or scope == "future")
            self.assertFalse(r[1]["resolved"])
            self.assertEqual((r[1]["reason"], r[1]["citation"], r[1]["requested"], r[1]["canonical"]), ("ambiguous_shorthand", label, "(v)", None))
            self.assertEqual((r[1]["_current_ids"], r[1]["_future_ids"]), ([], []))
            self.assertEqual(r[1]["diagnostics"]["candidate_paths"], ["3.1(c)(4)(v)", "3.1(v)"])
            self.assertEqual(r[1]["legal_validity"], "not_determined")
        for mode in MODES:
            for scope in SCOPES:
                for parts in PARTS:
                    for k in (10, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                        self.assertNotEqual(out["status"], "evidence_found", (mode, scope, parts, k))
                        self.assertIn(f"citation:{label}", self.gaps(out))
                        self.assertEqual(out["applicability_status"], "not_assessed")

    def test_malformed_tails_never_accept_the_resolved_prefix(self):
        for q, label in (("12 CFR 3.22(a) and (z", "(z after 12 CFR 3.22(a)"), ("12 CFR 3.1, 6.1(-1)", "12 CFR 6.1(-1)")):
            for mode in MODES:
                out = self.index.answer(q, mode=mode, scope="current")
                self.assertNotEqual(out["status"], "evidence_found", (q, mode))
                self.assertIn(f"citation:{label}", self.gaps(out), (q, mode))

    def test_title_ambiguity_survives_a_list(self):
        r = self.recs("§ 3.22, 6.1", "both")
        self.assertEqual((r[0]["reason"], r[0]["candidate_titles"]), ("ambiguous_title", ["12", "31"]))
        self.assertTrue(r[1]["resolved"])  # 6.1 has one title; the ambiguity of 3.22 is not hidden by it or spread to it
        self.assertEqual(r[1]["title_resolution"]["title"], "12")
        out = self.index.answer("§ 3.22, 6.1", mode="exact", scope="both")
        self.assertNotEqual(out["status"], "evidence_found")
        self.assertIn("citation:§ 3.22", self.gaps(out))

    def test_paragraph_precision_is_kept_per_member(self):
        r = self.recs("12 CFR 3.22(a) and (z)", "current")
        self.assertEqual((r[0]["requested_precision"], r[1]["requested_precision"]), ("paragraph", "paragraph"))
        self.assertEqual(r[1]["reason"], "paragraph_not_found_in_acquired_section")
        self.assertEqual(r[0]["achieved"], "paragraph")

    # ---- delta supports one citation
    def test_delta_reports_unsupported_multi_citation_requests_and_never_selects_the_first(self):
        for q in ("12 CFR 3.1, 6.1", "12 CFR 3.22(a) and (z)", "12 CFR 3.1 and 12 CFR 6.1", "12 CFR 3.1(c)(4)(i) and (v)", "12 CFR 3.22(a) and (z"):
            d = self.index.delta(q)
            self.assertEqual(d["evidence_status"], "unsupported_multi_citation", q)
            self.assertEqual((d["baseline"], d["instruments"], d["section_context"]), (None, [], None), q)
            self.assertEqual(d["unavailable"]["reason"], "multiple_citations_not_supported")
            self.assertGreaterEqual(len(d["unavailable"]["citations"]), 2)
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))
        d = self.index.delta("12 CFR 3.1, 6.1")
        self.assertEqual([c["label"] for c in d["unavailable"]["citations"]], ["12 CFR 3.1", "12 CFR 6.1"])
        self.assertEqual(d["unavailable"]["citations"][1]["inherited"]["kind"], "section")

    def test_single_citation_behaviour_is_intact(self):
        for q in ("12 CFR 3.1", "12 CFR 3.22(a)", "§ 3.1(c)(4)(i)", "12 CFR 6.1(b)"):
            d = self.index.delta(q)
            self.assertNotEqual(d["evidence_status"], "unsupported_multi_citation", q)
            self.assertEqual(len(parse(q)), 1)
        d = self.index.delta("12 CFR 3.22(a) (introductory text)")
        self.assertEqual(d["requested_path"], "(a)")
        for mode in MODES:
            out = self.index.answer("12 CFR 3.1(e)", mode=mode, scope="current")
            self.assertEqual(out["status"], "evidence_found")
            self.assertEqual(self.gaps(out), [])
        self.assertEqual(self.index.delta("12 CFR 3.22(a)")["future_precision"]["supported_instruments"], [KEYS[0]])

    def test_evaluator_group_is_registered_and_fails_when_a_member_is_dropped(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("query_lists", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["query_lists"]], [f"QL0{n}" for n in range(1, 8)])
        item = {"id": "T1", "kind": "equivalent", "shorthand": "12 CFR 3.1, 6.1", "control": "12 CFR 3.1 and 12 CFR 6.1", "members": ["12 CFR 3.1", "12 CFR 6.1"], "complete": True}
        self.assertTrue(evaluate.check_query_lists(self.index, item)["passed"])
        real = search.parse_query_citations
        with mock.patch.object(evaluate, "parse_query_citations", lambda q: real(q)[:1]):  # the old behaviour: the list tail is dropped
            self.assertFalse(evaluate.check_query_lists(self.index, item)["passed"])

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, status)


if __name__ == "__main__":
    unittest.main()
