"""Tests for S7-P1-DELTA-CANDIDATE-TITLE-25 (F1): the unresolved ambiguous-range candidates in delta() belong to the resolved CFR
title only. Identical reversed ranges under Titles 12 and 31 each reach their own title's request and never the other's; same-title
candidates, instruments, baselines and conservative range handling are unchanged, and no candidate becomes a confirmed amendment."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from test_p1 import asset
from test_p1_query_lists import ECFR_3, ECFR_31

ECFR_217 = """<div class="part" id="part-217"><h1>PART 217—CAPITAL</h1><div class="section" id="217.1"><h4>§ 217.1 Purpose.</h4>
<div id="p-217.1(a)"><p class="indent-1" data-title="217.1(a)">(a) Purpose.</p></div></div></div>"""
ECFR_1010 = """<div class="part" id="part-1010"><h1>PART 1010—GENERAL</h1><div class="section" id="1010.1"><h4>§ 1010.1 Purpose.</h4>
<div id="p-1010.1(a)"><p class="indent-1" data-title="1010.1(a)">(a) Purpose.</p></div></div></div>"""


def rng(title, part, first, second):
    return (f'<RULE><PREAMB><CFR>{title} CFR Part {part}</CFR></PREAMB><SUPLINF><REGTEXT TITLE="{title}" PART="{part}">'
            f"<AMDPAR>1. Remove &#xA7;&#xA7; {first} through {second}.</AMDPAR></REGTEXT></SUPLINF></RULE>").encode()


# identical reversed ranges 3.9 through 3.4 under Titles 12 and 31; a reversed range only under Title 12 (217) and only under Title 31 (1010)
SPECS = {"R12": ("12", "3", "3.9", "3.4"), "R31": ("31", "3", "3.9", "3.4"), "R217": ("12", "217", "217.9", "217.4"), "R1010": ("31", "1010", "1010.9", "1010.4")}
KEYS = {k: f"fr:2026-9610{n}" for n, k in enumerate(SPECS, 1)}
STRIP = ("citation", "requested_citation", "canonical_citation", "title_resolution")


class CandidateTitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01",
                "document_type": "Rule", "effective_on": "2027-02-01", "action": "Final rule."}
        ecfr = lambda t, p, name, html: {"source_class": "regulation", "publisher": "eCFR", "citation": f"{t} CFR Part {p}", "title": "T",
                                         "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                                         "asset": asset(root, name, html.encode())}
        docs = {"ecfr:12:3": ecfr(12, 3, "e3.html", ECFR_3), "ecfr:31:3": ecfr(31, 3, "e31.html", ECFR_31),
                "ecfr:12:217": ecfr(12, 217, "e217.html", ECFR_217), "ecfr:31:1010": ecfr(31, 1010, "e1010.html", ECFR_1010)}
        for k, (t, p, a, b) in SPECS.items():
            docs[KEYS[k]] = {**meta, "document_number": KEYS[k][3:], "title": k, "asset": asset(root, f"{k}.xml", rng(t, p, a, b))}
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "c.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cand(self, q):
        return sorted(c["doc_key"] for c in self.index.delta(q)["unresolved_candidates"])

    def test_the_fixture_has_identical_unresolved_ranges_under_both_titles(self):
        rows = self.index.conn.execute("SELECT title_no, lo_section, hi_section, status FROM amendment_range ORDER BY title_no, lo_section").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("12", "217.9", "217.4", "unresolved_ambiguous"), ("12", "3.9", "3.4", "unresolved_ambiguous"),
                                                    ("31", "1010.9", "1010.4", "unresolved_ambiguous"), ("31", "3.9", "3.4", "unresolved_ambiguous")])

    def test_section_endpoints_get_only_their_own_titles_candidates(self):
        for endpoint in ("3.9", "3.4"):
            self.assertEqual(self.cand(f"12 CFR {endpoint}"), [KEYS["R12"]], endpoint)
            self.assertEqual(self.cand(f"31 CFR {endpoint}"), [KEYS["R31"]], endpoint)

    def test_paragraph_requests_get_only_their_own_titles_candidates(self):
        self.assertEqual(self.cand("12 CFR 3.9(a)"), [KEYS["R12"]])
        self.assertEqual(self.cand("31 CFR 3.9(a)(1)"), [KEYS["R31"]])
        self.assertEqual(self.cand("12 CFR 3.4(c)"), [KEYS["R12"]])

    def test_whole_part_requests_get_only_their_own_titles_candidates(self):
        self.assertEqual(self.cand("12 CFR Part 3"), [KEYS["R12"]])
        self.assertEqual(self.cand("31 CFR Part 3"), [KEYS["R31"]])
        self.assertEqual(self.cand("12 CFR Part 217"), [KEYS["R217"]])
        self.assertEqual(self.cand("31 CFR Part 1010"), [KEYS["R1010"]])

    def test_candidate_content_is_same_title_and_unchanged(self):
        for q, key in (("12 CFR 3.9", "R12"), ("31 CFR 3.9", "R31")):
            (c,) = self.index.delta(q)["unresolved_candidates"]
            self.assertEqual((c["doc_key"], c["pairing_status"]), (KEYS[key], "ambiguous_range"))
            (ci,) = c["candidate_instructions"]
            self.assertEqual((ci["range"], ci["instruction_ordinal"]), (["3.9", "3.4"], 1))
            self.assertIn("3.9 through 3.4", ci["instruction"])
            self.assertTrue(ci["reason"])

    def test_candidates_are_not_promoted_and_interiors_are_not_inferred(self):
        for q, own in (("12 CFR 3.9", KEYS["R12"]), ("31 CFR 3.9", KEYS["R31"]), ("12 CFR Part 3", KEYS["R12"])):
            d = self.index.delta(q)
            # the instruction names its endpoints explicitly, so each title's own document is an instrument (unchanged behaviour); the
            # range candidate is a separate, unresolved list and the other title's document never appears in either
            self.assertEqual([g["doc_key"] for g in d["instruments"]], [own], q)
            self.assertEqual([u["match"] for g in d["instruments"] for u in g["units"] if q.endswith("3.9")], ["explicit_target"] * len([1 for g in d["instruments"] for u in g["units"] if q.endswith("3.9")]))
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))
        for title in ("12", "31"):
            d = self.index.delta(f"{title} CFR 3.6")  # interior of 3.9 through 3.4
            self.assertEqual((d["unresolved_candidates"], d["instruments"]), ([], []), title)
        dst = {r[0] for r in self.index.conn.execute("SELECT dst_citation FROM relationship WHERE rel_type='amends'")}
        for interior in ("5", "6", "7", "8"):  # nothing between the reversed endpoints is ever an amends target
            for title in ("12", "31"):
                self.assertNotIn(f"{title} CFR 3.{interior}", dst)
        self.assertEqual({r[0] for r in self.index.conn.execute("SELECT review_status FROM amendment_range")}, {"unresolved_candidate"})

    def test_unrelated_sections_and_other_parts_have_no_candidates(self):
        for q in ("12 CFR 3.22", "31 CFR 3.22", "12 CFR 3.1", "12 CFR 217.1", "31 CFR 1010.1", "12 CFR 3.10"):
            self.assertEqual(self.cand(q), [], q)

    def test_titleless_unique_cases_follow_the_resolved_title(self):
        d = self.index.delta("§ 217.9")
        self.assertEqual((d["title_resolution"]["status"], d["title_resolution"]["title"]), ("unique_from_acquired_evidence", "12"))
        self.assertEqual(self.cand("§ 217.9"), [KEYS["R217"]])
        self.assertEqual(self.cand("§ 1010.9"), [KEYS["R1010"]])  # Title 31 only
        self.assertEqual(self.cand("§ 217.9"), self.cand("12 CFR 217.9"))
        self.assertEqual(self.cand("§ 1010.4"), self.cand("31 CFR 1010.4"))

    def test_titleless_ambiguous_cases_expose_no_merged_candidates(self):
        for q in ("§ 3.9", "§ 3.4", "§ 3.9(a)"):
            d = self.index.delta(q)
            self.assertEqual(d["evidence_status"], "ambiguous_title", q)
            self.assertEqual((d["instruments"], d["unresolved_candidates"], d["baseline"]), ([], [], None), q)
            self.assertEqual(d["unavailable"]["candidate_titles"], ["12", "31"], q)

    def test_baselines_instruments_and_precision_are_unchanged(self):
        d = self.index.delta("12 CFR 3.22")
        self.assertEqual((d["evidence_status"], d["unresolved_candidates"]), ("available", []))
        self.assertTrue(d["baseline"]["doc_key"] == "ecfr:12:3" and d["baseline"]["sha256"])
        d31 = self.index.delta("31 CFR 3.22")
        self.assertEqual(d31["baseline"]["doc_key"], "ecfr:31:3")
        self.assertEqual(self.index.delta("12 CFR 3.9")["current_precision"]["resolved"], False)

    def test_relationships_stay_unreviewed(self):
        status = {r[0] for r in self.index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, status)

    def test_evaluator_group_is_registered_and_fails_when_titles_cross(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("candidate_titles", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["candidate_titles"]], [f"CT0{n}" for n in range(1, 4)])
        for x in reg["candidate_titles"][:2]:  # CT03 pins the acquired corpus and is checked by the evaluation itself
            self.assertTrue(evaluate.check_candidate_titles(self.index, x)["passed"], x["id"])
        item = reg["candidate_titles"][0]
        real_exec = search.Index.delta

        def leaking(self_, citation):  # the starting behaviour: candidates of any title are listed
            d = real_exec(self_, citation)
            if "3.9" in citation or "Part 3" in citation:
                d["unresolved_candidates"] = d["unresolved_candidates"] + [{"doc_key": "fr:2026-931" if "12 CFR" in citation else "fr:2026-912", "pairing_status": "ambiguous_range"}]
            return d
        with mock.patch.object(search.Index, "delta", leaking):
            self.assertFalse(evaluate.check_candidate_titles(self.index, item)["passed"])


if __name__ == "__main__":
    unittest.main()
