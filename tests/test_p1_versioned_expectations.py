"""Tests for S7-P1-S05-S06-VERSIONED-EXPECTATIONS-46: the operator/founder decision of 2026-11-02 is implemented as a new versioned record
(eval/phase1/questions_v0.3.json) that supersedes S05 and S06 of the frozen v0.2 record (eval/phase1/questions.json, never edited, hash-pinned).
The evaluator reports the v0.2 outcome next to the v0.3 one, as superseded and not waived. Producer-reported results; nothing is independently verified."""
import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import evaluate
from test_p1_rulemaking_sources import build_rule

ROOT = Path(__file__).resolve().parent.parent
V02 = ROOT / "eval/phase1/questions.json"
V03 = ROOT / "eval/phase1/questions_v0.3.json"
V02_SHA256 = "ce470ba63eb97407af10477fdbf397d51a16a9f97383643f40e010570c90dc62"
DECISION = {"file": "2026-11-02_S7_P1_S05_S06_FOUNDER_DECISION.md", "sha256": "a3c1d0c971de8f40fe2f51556f57e4d48759b7236a6e7498e08d9665e488251c"}
NOTE = "no stablecoin text exists in acquired eCFR baseline parts; results must be current-baseline provisions labelled as such, never future-rule text"
S05_V02 = {"id": "S05", "query": "proposed rule comment period capital requirements", "scope": "future",
           "expect": {"status": "evidence_found", "no_proposal_state_prefix_in": ["final", "current"]}}
S06_V02 = {"id": "S06", "query": "stablecoin issuer capital requirements", "scope": "current", "expect": {"status": "evidence_found", "note": NOTE}}
S05_V03 = {"id": "S05", "query": "proposed rule comment period capital requirements", "scope": "future",
           "expect": {"status": "partial_evidence", "gap_topic": "none_matched", "no_proposal_state_prefix_in": ["final", "current"]}}
S06_V03 = {"id": "S06", "query": "stablecoin issuer capital requirements", "scope": "current",
           "expect": {"status": "partial_evidence", "gap_topic": "stablecoin_genius", "note": NOTE}}


def make_v03(base, decision=DECISION):
    """The v0.3 transformation, restated independently of the shipped file, over any base record."""
    spec = copy.deepcopy(base)
    spec["version"] = "p1-eval-0.3"
    status = {s["id"]: s for s in spec["status"]}
    status["S05"]["expect"] = {"status": "partial_evidence", "gap_topic": "none_matched", "no_proposal_state_prefix_in": ["final", "current"]}
    status["S06"]["expect"] = {"status": "partial_evidence", "gap_topic": "stablecoin_genius", "note": NOTE}
    old = {s["id"]: s for s in base["status"]}
    spec["supersedes"] = {"file": "questions.json", "version": base["version"], "sha256": hashlib.sha256(json.dumps(base).encode()).hexdigest(),
                          "items": {sid: {"decision": {**decision, "file": decision["file"]}, "v0_2_expect": old[sid]["expect"], "change": "test"} for sid in ("S05", "S06")}}
    return spec


class FrozenV02Tests(unittest.TestCase):
    def test_v02_is_byte_identical_and_hash_pinned(self):
        raw = V02.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), V02_SHA256)
        status = {s["id"]: s for s in json.loads(raw)["status"]}
        self.assertEqual(status["S05"], S05_V02)
        self.assertEqual(status["S06"], S06_V02)
        self.assertEqual(json.loads(raw)["version"], "p1-eval-0.2")


class V03RecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v02 = json.loads(V02.read_bytes())
        cls.v03 = json.loads(V03.read_bytes())

    def test_s05_and_s06_records_are_exactly_the_decided_ones(self):
        status = {s["id"]: s for s in self.v03["status"]}
        self.assertEqual(status["S05"], S05_V03)
        self.assertEqual(status["S06"], S06_V03)
        self.assertEqual(self.v03["version"], "p1-eval-0.3")

    def test_ids_queries_scopes_and_guards_are_preserved(self):
        old = {s["id"]: s for s in self.v02["status"]}
        new = {s["id"]: s for s in self.v03["status"]}
        for sid in ("S05", "S06"):
            self.assertEqual((new[sid]["query"], new[sid]["scope"]), (old[sid]["query"], old[sid]["scope"]))
        self.assertEqual(new["S05"]["expect"]["no_proposal_state_prefix_in"], ["final", "current"])
        self.assertEqual(new["S06"]["expect"]["note"], NOTE)

    def test_nothing_else_differs_from_v02(self):
        rest = copy.deepcopy(self.v03)
        for key in ("supersedes", "changelog"):
            rest.pop(key)
        rest["version"] = self.v02["version"]
        status = {s["id"]: s for s in rest["status"]}
        status["S05"]["expect"] = S05_V02["expect"]
        status["S06"]["expect"] = S06_V02["expect"]
        expect = copy.deepcopy(self.v02)
        expect.pop("changelog")
        self.assertEqual(rest, expect)  # retrieval, delta, the other status items, flags and note are identical to v0.2
        self.assertEqual(self.v03["changelog"][:len(self.v02["changelog"])], self.v02["changelog"])

    def test_the_supersedes_block_and_changelog_cite_the_decision(self):
        sup = self.v03["supersedes"]
        self.assertEqual((sup["file"], sup["version"], sup["sha256"]), ("questions.json", "p1-eval-0.2", V02_SHA256))
        self.assertEqual(sorted(sup["items"]), ["S05", "S06"])
        for sid, entry in sup["items"].items():
            self.assertEqual((entry["decision"]["file"], entry["decision"]["sha256"]), (DECISION["file"], DECISION["sha256"]))
            self.assertEqual(entry["v0_2_expect"], {s["id"]: s for s in self.v02["status"]}[sid]["expect"])
        entry = self.v03["changelog"][-1]
        for needle in ("S05", "S06", DECISION["file"], DECISION["sha256"], "none_matched", "stablecoin_genius", "retrieval-relevance"):
            self.assertIn(needle, entry)

    def test_the_shipped_pair_passes_the_record_integrity_check(self):
        base, problems = evaluate.record_integrity(V03, self.v03)
        self.assertEqual(problems, [])
        self.assertEqual(base["version"], "p1-eval-0.2")

    def test_cli_default_runs_the_versioned_record(self):
        self.assertIn("questions_v0.3.json", (ROOT / "scripts/p1.py").read_text())


class EvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix, _ = build_rule(cls.tmp.name, "complete", "r")
        cls.dir = Path(cls.tmp.name) / "rec"
        cls.dir.mkdir()
        base = {"version": "p1-eval-0.2", "frozen_before_first_run": True, "note": "synthetic", "retrieval": [], "delta": [],
                "status": [S05_V02, S06_V02], "changelog": []}
        (cls.dir / "questions.json").write_text(json.dumps(base))
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def write_v03(self, mutate=None, base_bytes=None):
        d = Path(tempfile.mkdtemp(dir=self.tmp.name))
        raw = base_bytes if base_bytes is not None else (self.dir / "questions.json").read_bytes()
        (d / "questions.json").write_bytes(raw)
        spec = make_v03(json.loads((self.dir / "questions.json").read_bytes()))
        spec["supersedes"]["sha256"] = hashlib.sha256((self.dir / "questions.json").read_bytes()).hexdigest()
        if mutate:
            mutate(spec)
        (d / "questions_v0.3.json").write_text(json.dumps(spec))
        return d / "questions_v0.3.json"

    def test_v03_reports_s05_s06_as_superseded_and_passes_with_the_old_outcome_recorded(self):
        report = evaluate.run(self.ix, self.write_v03())
        self.assertTrue(report["overall"]["passed"], report["overall"]["failures"])
        self.assertEqual(report["overall"]["superseded"], ["S05", "S06"])
        self.assertEqual(report["record_integrity"], [])
        for x in report["superseded"]:
            self.assertFalse(x["superseded_record"]["result"]["passed"])  # the v0.2 outcome is kept, not waived
            self.assertEqual(x["superseded_record"]["result"]["failures"], ["status partial_evidence != evidence_found"])
            self.assertTrue(x["current_record"]["result"]["passed"])
            self.assertEqual(x["superseded_by"]["file"], DECISION["file"])
        text = evaluate.render(report)
        self.assertIn("SUPERSEDED by", text)
        self.assertIn("old outcome recorded, not waived", text)

    def test_v02_alone_still_reports_s05_s06_as_failures(self):
        report = evaluate.run(self.ix, self.dir / "questions.json")
        self.assertFalse(report["overall"]["passed"])
        self.assertEqual(report["overall"]["failures"]["status_checks"], ["S05", "S06"])
        self.assertEqual(report["superseded"], [])

    def test_a_changed_frozen_record_fails_the_evaluation(self):
        path = self.write_v03(base_bytes=(self.dir / "questions.json").read_bytes() + b" ")
        report = evaluate.run(self.ix, path)
        self.assertFalse(report["overall"]["passed"])
        self.assertTrue(any("sha256" in p for p in report["record_integrity"]), report["record_integrity"])
        self.assertEqual(report["overall"]["failure_counts"]["record_integrity"], len(report["record_integrity"]))

    def test_undeclared_or_guard_changes_fail_the_evaluation(self):
        def other_item(spec):  # a status item that is not declared as superseded
            spec["status"].append({"id": "S99", "query": "x", "expect": {"status": "missing_evidence"}})
        def guard(spec):
            [s for s in spec["status"] if s["id"] == "S05"][0]["expect"].pop("no_proposal_state_prefix_in")
        def scope(spec):
            [s for s in spec["status"] if s["id"] == "S06"][0]["scope"] = "both"
        def decision(spec):
            spec["supersedes"]["items"]["S05"]["decision"] = {}
        def retrieval(spec):
            spec["retrieval"] = [{"id": "R99"}]
        for name, fn, needle in (("extra item", other_item, "ids or order"), ("guard", guard, "guards or notes"), ("scope", scope, "query or scope"),
                                 ("decision", decision, "decision file"), ("retrieval", retrieval, "retrieval differs")):
            _, problems = evaluate.record_integrity(self.write_v03(fn), json.loads(self.write_v03(fn).read_text()))
            self.assertTrue(any(needle in p for p in problems), (name, problems))

    def test_positive_controls_for_scope_separation_and_boundaries(self):
        fut = self.ix.answer(S05_V03["query"], mode="hybrid", scope="future", k=10)
        self.assertTrue(fut["results"] and all(r["status"]["state"].startswith("proposed") for r in fut["results"]))  # every future result carries a proposal state
        self.assertFalse([r for r in fut["results"] if r["status"]["state"].startswith(("final", "current"))])
        cur = self.ix.answer(S06_V03["query"], mode="hybrid", scope="current", k=10)
        self.assertTrue(cur["results"] and all(r["status"]["state"].startswith("current_baseline") for r in cur["results"]))
        self.assertFalse([r for r in cur["results"] if r["doc_key"].startswith("fr:")])  # no future-rule record in current scope
        for out in (fut, cur):
            self.assertEqual(out["applicability_status"], "not_assessed")
        self.assertEqual(self.ix.delta("12 CFR 217.10")["legal_validity"], "not_determined")


if __name__ == "__main__":
    unittest.main()
