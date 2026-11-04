"""Tests for S7-P1-65: scripts/p1_delta_coverage.py on a small synthetic corpus (offline). Target counts reconcile across the
evidence-status tally, the distinct-target count and an SQL COUNT(DISTINCT title, part, section); part-level rows and unresolved
candidates are never counted as targets; no two instruments are merged and the producer's labels are kept."""
import copy
import importlib.util
import json
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_p1_rulemaking_sources import build_rule

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("p1_delta_coverage", ROOT / "scripts" / "p1_delta_coverage.py")
cov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cov)


def setUpModule():
    global _net
    _net = mock.patch.object(socket, "socket", side_effect=AssertionError("network access attempted in an offline test"))
    _net.start()


def tearDownModule():
    _net.stop()


class DeltaCoverageRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        ix, _ = build_rule(cls.tmp, "complete", "base")
        ix.conn.close()
        cls.db = str(Path(cls.tmp) / "base.sqlite")
        cls.result = cov.coverage(cls.db, "2026-10-29")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_counts_reconcile(self):
        s = self.result["summary"]
        self.assertGreater(s["distinct_targets"], 0)
        self.assertTrue(s["reconciles"])
        self.assertEqual(sum(s["targets_by_evidence_status"].values()), s["distinct_targets"])
        self.assertEqual(s["sql_distinct_targets"], s["distinct_targets"])
        self.assertEqual(sum(s["targets_by_baseline_kind"].values()), s["distinct_targets"])
        keys = [(r["title"], r["part"], r["section"]) for r in self.result["rows"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_part_level_rows_and_candidates_are_not_targets(self):
        for r in self.result["rows"]:
            self.assertRegex(r["citation"], r"^\d+ CFR \d+\.[0-9A-Za-z]+$")
        for cite in self.result["part_level_targets"]:
            self.assertRegex(cite, r"^\d+ CFR Part \d+$")

    def test_no_instrument_is_merged_and_labels_are_kept(self):
        self.assertEqual(self.result["summary"]["invariant_problems"], [])
        self.assertTrue(any(r["instrument_count"] >= 1 for r in self.result["rows"]))
        for r in self.result["rows"]:
            keys = [g["doc_key"] for g in r["instruments"]]
            self.assertEqual(len(keys), len(set(keys)), r["citation"])
            self.assertEqual((r["legal_validity"], r["applicability_status"]), ("not_determined", "not_assessed"))
        merged = copy.deepcopy(self.result["rows"][0])
        merged["instruments"] = merged["instruments"] * 2 if merged["instruments"] else [{"doc_key": "fr:x", "document_type": "Rule",
                                                                                           "lifecycle_state": None}] * 2
        merged["instrument_count"] = len(merged["instruments"])
        self.assertTrue(any("merged" in p for p in cov.invariants([merged])))
        relabelled = copy.deepcopy(merged)
        relabelled["instruments"] = [{"doc_key": "fr:p", "document_type": "Proposed Rule", "lifecycle_state": "final_effective"}]
        relabelled["instrument_count"] = 1
        self.assertTrue(any("labelled final_effective" in p for p in cov.invariants([relabelled])))

    def test_cli_writes_the_schema_and_exits_zero(self):
        out = Path(self.tmp) / "cov.json"
        with mock.patch("builtins.print"):
            code = cov.main(["--index", self.db, "--before-index", self.db, "--out", str(out)])
        self.assertEqual(code, 0)
        doc = json.loads(out.read_text())
        self.assertEqual(doc["schema"], "p1-delta-coverage-refresh-0.1")
        self.assertEqual(doc["before_after"]["newly_baseline_resolvable"], [])
        self.assertEqual(doc["before_after"]["baseline_lost"], [])


if __name__ == "__main__":
    unittest.main()
