"""Tests for scripts/p1_instruction_construction_census_82.py (S7-P1-82, C3 F-81-4/F-81-5): the census partitions every amendment unit,
classifies the triple-letter fixture unit, agrees with the evaluator's stored-scan units_matched, exits 1 on a mismatch and leaves the index
byte-identical. Offline; task-77 synthetic fixture. Governing scope: founder record 2026-11-04_s7-regintel-p1-founder-decisions_accepted.md."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import evaluate
from scripts import p1_instruction_construction_census_82 as census
from test_p1_triple_letter_paragraph_list import KEY, build_index


class CensusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name)
        cls.root = Path(cls.tmp.name)
        cls.result = census.census(cls.ix.conn)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def test_families_and_unclassified_partition_every_amendment_unit(self):
        units = self.ix.conn.execute("SELECT count(*) FROM provision WHERE kind='amendment' AND instruction IS NOT NULL").fetchone()[0]
        self.assertGreater(units, 0)
        self.assertEqual(self.result["amendment_units"], units)
        self.assertEqual(sum(self.result["partition"].values()), units)
        u = self.result["unclassified"]
        self.assertEqual(u["with_paths"] + u["without_paths"], self.result["partition"]["unclassified"])
        self.assertEqual(set(self.result["families"]), set(evaluate.STORED_SCAN_GROUPS))

    def test_the_triple_letter_fixture_unit_is_classified(self):
        tl = self.result["tags"]["triple_letter_designator"]
        self.assertIn(KEY, {doc for doc, _ in tl["examples"]})
        self.assertGreaterEqual(tl["units_with_paths"], 1)
        self.assertNotIn(KEY, {e["doc_key"] for e in self.result["unclassified"]["with_paths_examples"]})

    def test_census_counts_equal_the_evaluator_units_matched(self):
        check = census.consistency(self.result, census.evaluator_units_matched(self.ix))
        self.assertTrue(check["consistent"], check["mismatches"])
        self.assertEqual(set(check["evaluator_units_matched"]), set(evaluate.STORED_SCAN_GROUPS))

    def run_cli(self, out):
        with mock.patch("builtins.print"):
            return census.main(["--index", str(self.root / "i.sqlite"), "--out", str(out)])

    def test_the_cli_leaves_the_index_byte_identical(self):
        db = self.root / "i.sqlite"
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        out = self.root / "census.json"
        self.assertEqual(self.run_cli(out), 0)
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)
        report = json.loads(out.read_text())
        self.assertEqual((report["index"]["sha256"], report["index"]["unchanged_after_run"]), (before, True))
        self.assertEqual((report["legal_validity"], report["applicability_status"]), ("not_determined", "not_assessed"))
        self.assertEqual(report["status"], "proposal; producer-reported; no gate approved; nothing authorised")
        self.assertEqual(report["schema"], "p1-instruction-construction-census-0.1")

    def test_a_mismatch_between_census_and_evaluator_exits_1(self):
        real = evaluate.check_passive_location_paragraphs
        fake = lambda index, item: {**real(index, item), "units_matched": 99}
        out = self.root / "census_bad.json"
        with mock.patch.dict(census.CHECKS, {"passive_location_paragraphs": fake}):
            self.assertEqual(self.run_cli(out), 1)
        mism = json.loads(out.read_text())["consistency"]["mismatches"]
        self.assertEqual([(m["group"], m["evaluator"]) for m in mism], [("passive_location_paragraphs", 99)])


if __name__ == "__main__":
    unittest.main()
