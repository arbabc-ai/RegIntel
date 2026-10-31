"""Regression tests for S7-P1-COVERAGE-GUARDS-03: coverage must be exposed, never assumed from a lexical hit."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.search import citation_only
from test_p1 import ECFR_HTML, asset, complete_baseline
from test_p1_completeness import FR_EXPLICIT

TOPICS = Path(__file__).resolve().parent.parent / "regintel_p1" / "topics.json"


class CitationOnlyTests(unittest.TestCase):
    def test_pure_citations_only(self):
        for q in ("12 CFR 3.22", "§ 217.12", "12 CFR Part 252", "section 12 CFR 3.1", "12 CFR 3.22 and § 217.22"):
            self.assertTrue(citation_only(q), q)
        for q in ("what does 12 CFR 3.1 require", "Texas minimum capital", "capital", "12 CFR 3.22 stablecoin"):
            self.assertFalse(citation_only(q), q)


class TopicCatalogTests(unittest.TestCase):
    def test_liquidity_topics_are_partial_and_infer_no_applicability(self):
        topics = {t["id"]: t for t in json.loads(TOPICS.read_text())["topics"]}
        for tid in ("liquidity_lcr", "liquidity_nsfr"):
            t = topics[tid]
            self.assertEqual(t["coverage"], "partial", tid)
            note = t["coverage_note"]
            self.assertIn("does not establish coverage of the missing OCC", note)
            self.assertIn("FDIC", note)
            self.assertIn("not inferred", note)
        self.assertEqual(topics["cet1_capital"]["coverage"], "acquired")  # positive control stays acquired


class CoverageGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        docs = {
            "ecfr:12:3": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 3", "title": "Capital",
                          "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                          "asset": asset(root, "e.html", ECFR_HTML.encode())},
            "fr:2026-70012": {**meta, "document_number": "2026-70012", "title": "Explicit", "document_type": "Rule",
                              "effective_on": "2027-02-01", "action": "Final rule.", "asset": asset(root, "x.xml", FR_EXPLICIT)},
        }
        complete_baseline(root, docs)  # the declared eCFR baseline dependencies of the acquired topics
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        db = Path(cls.tmp.name) / "g.sqlite"
        conn, _ = build.build(root, db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(str(db))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def gaps(self, out):
        return {(g["topic"], g["coverage"]) for g in out["coverage_gaps"]}

    # ---- queries that previously came back evidence_found with no gap
    def test_unmatched_topic_with_results_is_partial_with_unassessed_gap(self):
        out = self.index.answer("Texas minimum capital")
        self.assertTrue(out["results"])
        self.assertEqual(out["status"], "partial_evidence")
        self.assertIn(("none_matched", "unassessed"), self.gaps(out))
        self.assertEqual(out["topics_matched"], [])
        self.assertEqual(out["applicability_status"], "not_assessed")
        gap = next(g for g in out["coverage_gaps"] if g["topic"] == "none_matched")
        self.assertIn("lexical hit", gap["note"])

    def test_unmatched_topic_without_results_is_missing_evidence_with_the_gap(self):
        out = self.index.answer("New York lending limits")
        self.assertEqual(out["results"], [])
        self.assertEqual(out["status"], "missing_evidence")
        self.assertIn(("none_matched", "unassessed"), self.gaps(out))

    def test_accounting_question_in_plain_words_is_not_evidence_found(self):
        out = self.index.answer("How should a bank recognize expected loan losses?")
        self.assertNotEqual(out["status"], "evidence_found")
        self.assertIn(("none_matched", "unassessed"), self.gaps(out))

    def test_liquidity_queries_expose_partial_source_coverage(self):
        lcr = self.index.answer("liquidity coverage ratio requirements for a national bank")
        nsfr = self.index.answer("net stable funding ratio requirements for an FDIC supervised bank")
        for out, topic in ((lcr, "liquidity_lcr"), (nsfr, "liquidity_nsfr")):
            self.assertEqual(out["topics_matched"], [topic])
            self.assertIn((topic, "partial"), self.gaps(out))
            self.assertNotEqual(out["status"], "evidence_found")
            self.assertEqual(out["applicability_status"], "not_assessed")
            note = next(g["note"] for g in out["coverage_gaps"] if g["topic"] == topic)
            self.assertIn("does not establish coverage of the missing OCC", note)
        self.assertEqual(lcr["status"], "partial_evidence")  # fixture has lexical hits for 'ratio' / 'national bank'

    def test_all_five_guarded_queries_expose_incomplete_or_unassessed_coverage(self):
        for q in ("New York lending limits", "Texas minimum capital", "How should a bank recognize expected loan losses?",
                  "liquidity coverage ratio requirements for a national bank",
                  "net stable funding ratio requirements for an FDIC supervised bank"):
            for scope in ("current", "future", "both"):
                out = self.index.answer(q, scope=scope)
                self.assertIn(out["status"], ("partial_evidence", "missing_evidence"), (q, scope))
                self.assertTrue(out["coverage_gaps"], (q, scope))
                self.assertTrue({c for _, c in self.gaps(out)} & {"unassessed", "partial", "not_acquired"}, (q, scope))

    def test_a_lexical_hit_alone_never_establishes_coverage(self):
        out = self.index.answer("ratio", scope="current")
        self.assertTrue(out["results"])
        self.assertEqual(out["status"], "partial_evidence")
        out2 = self.index.answer("what does 12 CFR 3.1 require", scope="current")  # citation plus words is not citation-only
        self.assertTrue(out2["results"])
        self.assertEqual(out2["status"], "partial_evidence")
        self.assertIn(("none_matched", "unassessed"), self.gaps(out2))

    # ---- positive controls
    def test_acquired_topic_stays_evidence_found(self):
        out = self.index.answer("minimum common equity tier 1 capital ratio", scope="current")
        self.assertEqual(out["status"], "evidence_found")
        self.assertIn("cet1_capital", out["topics_matched"])
        self.assertEqual(out["coverage_gaps"], [])
        self.assertEqual(out["applicability_status"], "not_assessed")

    def test_citation_only_query_resolving_in_scope_is_evidence_found(self):
        for q in ("12 CFR 3.1", "§ 3.1", "section 12 CFR 3.1"):
            cur = self.index.answer(q, scope="current")
            self.assertEqual(cur["status"], "evidence_found", q)
            self.assertEqual(cur["coverage_gaps"], [], q)
            self.assertTrue(all(r["status"]["state"].startswith("current_baseline") for r in cur["results"]))
        fut = self.index.answer("12 CFR 3.1", scope="future")  # an explicit amendment in the fixture
        self.assertEqual(fut["status"], "evidence_found")
        self.assertTrue(all(r["kind"] in ("amendment", "fr_preamble", "fr_table") for r in fut["results"]))
        self.assertTrue(any(r["kind"] == "amendment" for r in fut["results"]))
        self.assertFalse(any(r["status"]["state"].startswith("current") for r in fut["results"]))
        for mode in ("exact", "hybrid"):
            self.assertEqual(self.index.answer("12 CFR 3.1", mode=mode, scope="both")["status"], "evidence_found", mode)

    # ---- negative controls
    def test_excluding_part_filter_defeats_the_citation_guarantee(self):
        for scope in ("current", "future", "both"):
            # The fixture now also holds the filler baseline parts that acquired topics depend on (complete_baseline), and
            # part 217 is one of them, so the excluding filter names a part the fixture does not contain; assertions unchanged.
            out = self.index.answer("12 CFR 3.1", scope=scope, parts=["12:999"])
            self.assertNotEqual(out["status"], "evidence_found", scope)
            self.assertEqual(out["results"], [], scope)
            self.assertIn(("none_matched", "unassessed"), self.gaps(out), scope)

    def test_unresolved_citation_is_not_evidence_found(self):
        for scope in ("current", "future"):
            out = self.index.answer("12 CFR 3.999", scope=scope)
            self.assertNotEqual(out["status"], "evidence_found", scope)
            self.assertIn(("none_matched", "unassessed"), self.gaps(out), scope)

    def test_unacquired_topic_behaviour_is_unchanged(self):
        self.assertEqual(self.index.answer("CECL under ASC 326")["status"], "missing_evidence")
        self.assertEqual(self.index.answer("customer identification program for minimum capital", scope="current")["status"],
                         "partial_evidence")


if __name__ == "__main__":
    unittest.main()
