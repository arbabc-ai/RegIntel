"""Tests for S7-P1-TITLELESS-DELTA-17 (F3): delta() and the citation resolver share one title decision. A titleless
section citation takes the single title that has acquired evidence for it, or is explicitly ambiguous with the candidate
titles; a missing title is never interpolated into 'None CFR ...' and evidence from different titles is never merged."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from test_p1 import asset
from test_p1_delta_precision import fr

MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")

ECFR_12 = """<div class="part" id="part-3"><h1>PART 3—CAPITAL</h1>
<div class="section" id="3.1"><h4>§ 3.1 Purpose.</h4>
<div id="p-3.1(c)"><p class="indent-1" data-title="3.1(c)">(c) Applicability.</p>
<div id="p-3.1(c)(1)"><p class="indent-2" data-title="3.1(c)(1)">(1) First applicability item.</p></div></div></div>
<div class="section" id="3.4-3.9"><h4>§§ 3.4-3.9 [Reserved]</h4></div>
<div class="section" id="3.22"><h4>§ 3.22 Adjustments and deductions.</h4>
<div id="p-3.22(a)"><p class="indent-1" data-title="3.22(a)">(a) Twelve general adjustments.</p></div></div></div>"""
ECFR_31_3 = """<div class="part" id="part-3"><h1>PART 3—TREASURY RULES</h1>
<div class="section" id="3.22"><h4>§ 3.22 Treasury adjustments.</h4>
<div id="p-3.22(a)"><p class="indent-1" data-title="3.22(a)">(a) Treasury general adjustments.</p></div></div></div>"""
ECFR_31_1010 = """<div class="part" id="part-1010"><h1>PART 1010—GENERAL PROVISIONS</h1>
<div class="section" id="1010.100"><h4>§ 1010.100 General definitions.</h4>
<div id="p-1010.100(a)"><p class="indent-1" data-title="1010.100(a)">(a) Definitions apply.</p></div></div></div>"""

# Title 12, Part 3: 3.1(c)(1) named by X; Y revises 3.1(c) only; 3.22(i) named by Z; 3.30 exists only in a proposal;
# 3.40 through 3.45 is a numeric range in an instruction-only row.
X = fr("RULE", "1. Amend &#xA7; 3.1 by revising paragraph (c)(1) to read as follows:", [("3.1", ["(c) * * *", "(1) X text."])])
Y = fr("PRORULE", "1. Amend &#xA7; 3.1 by revising paragraph (c) to read as follows:", [("3.1", ["(c) Y text."])])
Z12 = fr("RULE", "1. Amend &#xA7; 3.22 by revising paragraph (i) to read as follows:", [("3.22", ["(i) Twelve new text."])])
Z31 = fr("RULE", "1. Amend &#xA7; 3.22 by revising paragraph (b) to read as follows:", [("3.22", ["(b) Treasury new text."])], title="31", part="3")
F30 = fr("PRORULE", "1. Add &#xA7; 3.30 to read as follows:", [("3.30", ["(a) Future-only text."])])
RNG = (b'<RULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF><REGTEXT TITLE="12" PART="3">'
       b"<AMDPAR>1. Remove &#xA7;&#xA7; 3.40 through 3.45.</AMDPAR></REGTEXT></SUPLINF></RULE>")
KEYS = {"X": "fr:2026-94001", "Y": "fr:2026-94002", "Z12": "fr:2026-94003", "Z31": "fr:2026-94004", "F30": "fr:2026-94005", "RNG": "fr:2026-94006"}
STRIP = ("citation", "requested_citation", "canonical_citation", "title_resolution")


class TitlelessIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        final = {**meta, "document_type": "Rule", "effective_on": "2027-02-01", "action": "Final rule."}
        prop = {**meta, "document_type": "Proposed Rule", "comments_close_on": "2026-12-01"}
        ecfr = lambda t, p, name, html: {"source_class": "regulation", "publisher": "eCFR", "citation": f"{t} CFR Part {p}", "title": "T",
                                         "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                                         "asset": asset(root, name, html.encode())}
        docs = {"ecfr:12:3": ecfr(12, 3, "e12.html", ECFR_12), "ecfr:31:3": ecfr(31, 3, "e31.html", ECFR_31_3),
                "ecfr:31:1010": ecfr(31, 1010, "e1010.html", ECFR_31_1010)}
        kinds = {"X": final, "Y": prop, "Z12": final, "Z31": final, "F30": prop, "RNG": final}
        data = {"X": X, "Y": Y, "Z12": Z12, "Z31": Z31, "F30": F30, "RNG": RNG}
        for k, key in KEYS.items():
            docs[key] = {**kinds[k], "document_number": key[3:], "title": k, "asset": asset(root, f"{k}.xml", data[k])}
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "t.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rec(self, q, scope, parts=None):
        return self.index.resolve_citations(q, scope, parts)[0]

    def norm(self, d):
        return {k: v for k, v in d.items() if k not in STRIP}

    def docs(self, d):
        return [g["doc_key"] for g in d["instruments"]]

    # ---- unique title: titleless equals fully qualified, in resolver and delta
    def test_unique_title_sections_and_paragraphs_match_their_explicit_equivalents(self):
        for plain, explicit in (("§ 3.1", "12 CFR 3.1"), ("§ 3.1(c)", "12 CFR 3.1(c)"), ("§ 3.1(c)(1)", "12 CFR 3.1(c)(1)"),
                                ("§ 3.1(c)( 1 )", "12 CFR 3.1(c)(1)"), ("§ 3.30", "12 CFR 3.30"),
                                ("§ 3.30(a)", "12 CFR 3.30(a)"), ("§ 1010.100", "31 CFR 1010.100"), ("§ 1010.100(a)", "31 CFR 1010.100(a)")):
            a, b = self.index.delta(plain), self.index.delta(explicit)
            self.assertEqual(self.norm(a), self.norm(b), plain)
            self.assertEqual(a["title_resolution"]["status"], "unique_from_acquired_evidence", plain)
            self.assertEqual(b["title_resolution"]["status"], "explicit")
            for scope in SCOPES:
                ra, rb = self.rec(plain, scope), self.rec(explicit, scope)
                for key in ("resolved", "achieved", "reason", "by_scope", "future_paragraph_support", "baseline_match", "_current_ids", "_future_ids"):
                    self.assertEqual(ra[key], rb[key], (plain, scope, key))
                self.assertEqual(ra["title_resolution"]["title"], rb["title_resolution"]["title"])

    def test_the_title_is_never_interpolated_as_none(self):
        d = self.index.delta("§ 3.1(c)(1)")
        self.assertEqual(d["title_resolution"]["title"], "12")
        self.assertEqual(set(self.docs(d)), {KEYS["X"], KEYS["Y"]})
        self.assertNotIn("None", json.dumps(d["title_resolution"]))
        self.assertTrue(d["baseline"])

    def test_paragraph_support_is_per_instrument_and_unchanged(self):
        d = self.index.delta("§ 3.1(c)(1)")
        by = {g["doc_key"]: g for g in d["instruments"]}
        self.assertEqual(by[KEYS["X"]]["role"], "paragraph_supported")
        self.assertEqual(by[KEYS["Y"]]["role"], "section_context")
        self.assertEqual(d["future_precision"]["supported_instruments"], [KEYS["X"]])
        self.assertEqual(d["requested_path"], "(c)(1)")

    # ---- future-only evidence, numeric ranges, missing evidence
    def test_future_only_section_is_found_under_its_unique_title(self):
        d = self.index.delta("§ 3.30")
        self.assertIsNone(d["baseline"])
        self.assertEqual(self.docs(d), [KEYS["F30"]])
        self.assertEqual((d["title_resolution"]["status"], d["title_resolution"]["title"], d["evidence_status"]), ("unique_from_acquired_evidence", "12", "available"))
        self.assertEqual(d["title_resolution"]["candidates"][0]["baseline_records"], 0)
        self.assertEqual(self.norm(d), self.norm(self.index.delta("12 CFR 3.30")))

    def test_numeric_range_membership_is_kept(self):
        for q, explicit in (("§ 3.42", "12 CFR 3.42"), ("§ 3.5", "12 CFR 3.5")):
            a, b = self.index.delta(q), self.index.delta(explicit)
            self.assertEqual(self.norm(a), self.norm(b), q)
        d = self.index.delta("§ 3.42")
        self.assertEqual(self.docs(d), [KEYS["RNG"]])
        self.assertEqual(d["instruments"][0]["units"][0]["match"], "range_membership")
        self.assertEqual(d["title_resolution"]["candidates"][0]["numeric_range_units"], 1)
        base = self.index.delta("§ 3.5")  # inside the acquired eCFR range section 3.4-3.9
        self.assertEqual(base["baseline"]["match"], "range_section")
        self.assertEqual(base["baseline"]["range_membership"]["range_section"], "3.4-3.9")

    def test_missing_evidence_is_explicit_and_not_a_silent_omission(self):
        d = self.index.delta("§ 1010.999")  # the part is acquired under one title; the section is not
        self.assertEqual((d["title_resolution"]["title"], d["evidence_status"]), ("31", "unavailable"))
        self.assertEqual(d["unavailable"]["reason"], "not_found_in_acquired_part")
        self.assertEqual(self.norm(d), self.norm(self.index.delta("31 CFR 1010.999")))
        n = self.index.delta("§ 77.1")  # no title has the part
        self.assertEqual((n["title_resolution"]["status"], n["evidence_status"], n["unavailable"]["reason"]), ("unidentified", "unavailable", "part_not_acquired"))
        self.assertEqual(n["instruments"], [])

    # ---- identical section numbers under two titles
    def test_identical_section_under_two_titles_is_explicit_ambiguity(self):
        d = self.index.delta("§ 3.22")
        self.assertEqual(d["evidence_status"], "ambiguous_title")
        self.assertEqual((d["baseline"], d["section_context"], d["instruments"]), (None, None, []))
        self.assertEqual(d["unavailable"]["reason"], "ambiguous_title")
        self.assertEqual(d["unavailable"]["candidate_titles"], ["12", "31"])
        self.assertEqual({c["title"]: (c["baseline_records"], c["future_amendment_units"]) for c in d["title_resolution"]["candidates"]},
                         {"12": (2, 1), "31": (2, 1)})
        self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))
        self.assertIn("12 CFR 3.22", " ".join(d["notes"]))
        for scope in SCOPES:
            r = self.rec("§ 3.22", scope)
            self.assertFalse(r["resolved"], scope)
            self.assertEqual((r["reason"], r["candidate_titles"], r["_current_ids"], r["_future_ids"]), ("ambiguous_title", ["12", "31"], [], []))
            self.assertEqual(self.index.exact("§ 3.22", 20, scope), [])

    def test_evidence_from_different_titles_is_never_merged(self):
        t12, t31 = self.index.delta("12 CFR 3.22"), self.index.delta("31 CFR 3.22")
        self.assertEqual(self.docs(t12), [KEYS["Z12"]])
        self.assertEqual(self.docs(t31), [KEYS["Z31"]])
        texts = lambda q: " ".join(self.index.evidence(i)["text"] for i in self.rec(q, "current")["_current_ids"])
        self.assertIn("Twelve general adjustments", texts("12 CFR 3.22"))
        self.assertNotIn("Treasury", texts("12 CFR 3.22"))
        self.assertIn("Treasury general adjustments", texts("31 CFR 3.22"))
        self.assertNotIn("Twelve", texts("31 CFR 3.22"))
        self.assertNotEqual(t12["baseline"]["doc_key"], t31["baseline"]["doc_key"])
        p12, p31 = self.index.delta("12 CFR 3.22(i)"), self.index.delta("31 CFR 3.22(b)")
        self.assertEqual(p12["future_precision"]["supported_instruments"], [KEYS["Z12"]])
        self.assertEqual(p31["future_precision"]["supported_instruments"], [KEYS["Z31"]])
        self.assertEqual(self.index.delta("31 CFR 3.22(i)")["future_precision"]["supported_instruments"], [])  # Z12's path is not Z31's
        amb = self.index.delta("§ 3.22(i)")
        self.assertEqual((amb["evidence_status"], amb["instruments"]), ("ambiguous_title", []))
        self.assertEqual(amb["requested_path"], "(i)")

    def test_answers_for_an_ambiguous_citation_carry_a_gap_and_never_evidence_found(self):
        for mode in MODES:
            for scope in SCOPES:
                for parts in (None, ["12:3"], ["31:3"]):
                    for k in (10, 1):
                        out = self.index.answer("§ 3.22", mode=mode, scope=scope, parts=parts, k=k)
                        tag = (mode, scope, parts, k)
                        self.assertNotEqual(out["status"], "evidence_found", tag)
                        gaps = {g["topic"]: g for g in out["coverage_gaps"]}
                        self.assertEqual(gaps["citation:§ 3.22"]["reason"], "ambiguous_title", tag)
                        if mode == "exact":
                            self.assertEqual(out["results"], [], tag)
        mixed = self.index.answer("12 CFR 3.1 and § 3.22", mode="exact", scope="both")
        cites = {c["citation"]: c for c in mixed["citations"]}
        self.assertTrue(cites["12 CFR 3.1"]["resolved"])
        self.assertFalse(cites["§ 3.22"]["resolved"])
        self.assertEqual(mixed["status"], "partial_evidence")

    def test_answers_for_unique_titleless_equal_the_explicit_ones(self):
        for plain, explicit in (("§ 3.1(c)(1)", "12 CFR 3.1(c)(1)"), ("§ 3.30", "12 CFR 3.30")):
            for mode in MODES:
                for scope in SCOPES:
                    for parts in (None, ["12:3"], ["31:3"]):
                        a, b = (self.index.answer(q, mode=mode, scope=scope, parts=parts, k=10) for q in (plain, explicit))
                        if mode == "hybrid":  # hybrid also fuses lexical hits on "CFR"/"12", so only the shared pinned record is compared
                            ra = self.rec(plain, scope, parts)
                            if ra["resolved"] and a["results"] and b["results"]:
                                self.assertEqual(a["results"][0]["provision_id"], b["results"][0]["provision_id"], (plain, scope, parts))
                            self.assertEqual(a["status"] == "evidence_found", b["status"] == "evidence_found", (plain, scope, parts))
                            continue
                        self.assertEqual(a["status"], b["status"], (plain, mode, scope, parts))
                        self.assertEqual([r["provision_id"] for r in a["results"]], [r["provision_id"] for r in b["results"]], (plain, mode, scope, parts))

    def test_controls_explicit_titles_preserve_baseline_hash_and_locators(self):
        d = self.index.delta("12 CFR 3.1")
        self.assertEqual(d["title_resolution"], {"status": "explicit", "title": "12", "candidates": []})
        row = self.index.conn.execute("SELECT sha256 FROM source_document WHERE doc_key='ecfr:12:3'").fetchone()
        self.assertEqual(d["baseline"]["sha256"], row[0])
        self.assertEqual(self.index.delta("§ 3.1")["baseline"]["sha256"], row[0])
        for g in self.index.delta("§ 3.1(c)(1)")["instruments"]:
            self.assertTrue(g["source_sha256"] and g["units"][0]["locator"].startswith("2026-9400"))

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, status)

    def test_evaluator_group_is_registered_and_fails_when_the_title_is_not_shared(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("titleless_delta", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["titleless_delta"]], [f"TD0{n}" for n in range(1, 7)])
        item = {"id": "T1", "titleless": "§ 3.1(c)(1)", "explicit": "12 CFR 3.1(c)(1)", "title": "12", "instruments": [KEYS["X"], KEYS["Y"]],
                "baseline": True, "supported": [KEYS["X"]], "section_context": [KEYS["Y"]]}
        self.assertTrue(evaluate.check_titleless_delta(self.index, item)["passed"])
        self.assertFalse(evaluate.check_titleless_delta(self.index, {**item, "instruments": [KEYS["X"]]})["passed"])
        broken = lambda c: {"status": "unique_from_acquired_evidence", "title": None, "candidates": []} if not c["title"] else \
            {"status": "explicit", "title": c["title"], "candidates": []}
        with mock.patch.object(search.Index, "title_resolution", lambda self_, c: broken(c)):  # the old behaviour: no title
            self.assertFalse(evaluate.check_titleless_delta(self.index, item)["passed"])


if __name__ == "__main__":
    unittest.main()
