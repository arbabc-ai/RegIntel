"""Pins the facts behind the S05/S06 reconciliation proposal (2026-11-02, S7-P1). It changes no behavior: the frozen records are read, never edited,
and the producer's current answers for the two frozen queries are checked on synthetic data. Results here are producer-reported."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import evaluate
from test_p1_rulemaking_sources import build_rule

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "eval/phase1/questions.json"
FROZEN_SHA256 = "ce470ba63eb97407af10477fdbf397d51a16a9f97383643f40e010570c90dc62"
S05 = {"id": "S05", "query": "proposed rule comment period capital requirements", "scope": "future",
       "expect": {"status": "evidence_found", "no_proposal_state_prefix_in": ["final", "current"]}}
S06 = {"id": "S06", "query": "stablecoin issuer capital requirements", "scope": "current",
       "expect": {"status": "evidence_found",
                  "note": "no stablecoin text exists in acquired eCFR baseline parts; results must be current-baseline provisions labelled as such, never future-rule text"}}


class FrozenRecordTests(unittest.TestCase):
    def test_questions_json_is_the_frozen_file_and_s05_s06_are_unchanged(self):
        raw = QUESTIONS.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), FROZEN_SHA256)
        status = {x["id"]: x for x in json.loads(raw)["status"]}
        self.assertEqual(status["S05"], S05)
        self.assertEqual(status["S06"], S06)
        changelog = " ".join(json.loads(raw)["changelog"])
        self.assertNotIn("S05", changelog)  # neither expectation has ever been revised
        self.assertNotIn("S06", changelog)

    def test_topic_catalog_facts_the_proposal_relies_on(self):
        topics = {t["id"]: t for t in json.loads((ROOT / "regintel_p1/topics.json").read_text())["topics"]}
        self.assertEqual(topics["stablecoin_genius"]["coverage"], "partial")
        self.assertIn("no stablecoin text exists in current eCFR baseline parts", topics["stablecoin_genius"]["coverage_note"])
        names = [n.lower() for t in topics.values() for n in [t["label"]] + t["synonyms"]]
        self.assertFalse([n for n in names if "capital requirements" in n or "comment period" in n or n == "proposed rule"])


class ProducerReportedBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix, _ = build_rule(cls.tmp.name, "complete", "r")

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def test_s05_the_only_mismatch_is_status_and_it_comes_from_an_unmatched_topic(self):
        self.assertEqual(self.ix.match_topics(S05["query"]), [])
        out = self.ix.answer(S05["query"], mode="hybrid", scope="future", k=10)
        self.assertEqual([(g["topic"], g["coverage"]) for g in out["coverage_gaps"]], [("none_matched", "unassessed")])
        self.assertTrue(out["results"])
        self.assertTrue(all(r["status"]["state"].startswith("proposed") for r in out["results"]))  # no proposal labelled final or current
        res = evaluate.check_status(self.ix, S05)
        self.assertEqual(res["failures"], ["status partial_evidence != evidence_found"])

    def test_s06_the_only_mismatch_is_status_and_it_comes_from_the_partial_stablecoin_topic(self):
        self.assertEqual([t["id"] for t, _ in self.ix.match_topics(S06["query"])], ["stablecoin_genius"])
        out = self.ix.answer(S06["query"], mode="hybrid", scope="current", k=10)
        self.assertEqual([(g["topic"], g["coverage"]) for g in out["coverage_gaps"]], [("stablecoin_genius", "partial")])
        self.assertTrue(out["results"])
        self.assertTrue(all(r["status"]["state"].startswith("current_baseline") for r in out["results"]))  # current scope stays current baseline
        self.assertFalse([r for r in out["results"] if "stablecoin" in r["text"].lower()])  # the baseline has no stablecoin text
        res = evaluate.check_status(self.ix, S06)
        self.assertEqual(res["failures"], ["status partial_evidence != evidence_found"])

    def test_no_applicability_or_validity_is_inferred(self):
        for item in (S05, S06):
            out = self.ix.answer(item["query"], mode="hybrid", scope=item["scope"], k=10)
            self.assertEqual(out["applicability_status"], "not_assessed")


if __name__ == "__main__":
    unittest.main()
