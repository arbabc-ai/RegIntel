"""Tests for S7-P1-81 (C3 F-79-1 annex follow-up): the paragraph_evidence `ranking` and enumerated_relative_paragraphs `acquired` checks count
the expectations they skip because the document is absent from the index (both groups now in SKIP_COUNTED_GROUPS), and the seven `stored`-kind
corpus-scan checks report `units_matched`, with a roll-up of those that matched no unit. Reporting only: overall() and every `passed` term are
unchanged. Governing scope: founder record 2026-11-04_s7-regintel-p1-founder-decisions_accepted.md."""
import copy
import re
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import evaluate
from test_p1_triple_letter_paragraph_list import build_index

EVAL = Path(__file__).resolve().parent.parent / "eval/phase1"
SKIP = "expectations skipped because the document is absent from the index:"
ZERO = "stored-kind checks with zero matching units:"
NEW_FIELDS = ("units_checked", "units_skipped", "units_matched")


class SkipVacuityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name)  # the task-77 synthetic index: no acquired Federal Register document
        cls.report = evaluate.run(cls.ix, EVAL / "questions_v0.3.json", EVAL / "regressions.json")
        cls.reg = cls.report["regressions"]
        cls.text = evaluate.render(cls.report)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def line(self, prefix, text=None):
        found = [l for l in (text or self.text).splitlines() if prefix in l]
        self.assertEqual(len(found), 1, found)
        return found[0]

    def test_absent_documents_are_counted_for_the_two_new_groups_and_the_total_is_the_sum(self):
        pe = next(x for x in self.reg["paragraph_evidence"] if x["kind"] == "ranking")
        er = next(x for x in self.reg["enumerated_relative_paragraphs"] if x["kind"] == "acquired")
        self.assertEqual((pe["units_checked"], pe["units_skipped"]), (0, 1))  # PE02 names one citation, its document is absent here
        self.assertEqual((er["units_checked"], er["units_skipped"]), (0, 1))
        self.assertTrue(pe["passed"] and er["passed"])  # skipped, not failed: outcome unchanged
        skip = self.reg["expectations_skipped_absent_document"]
        self.assertEqual(skip["groups"]["paragraph_evidence"], {"deltas_skipped": 0, "units_skipped": 1})
        self.assertEqual(skip["groups"]["enumerated_relative_paragraphs"], {"deltas_skipped": 0, "units_skipped": 1})
        self.assertEqual(skip["total"], sum(g["deltas_skipped"] + g["units_skipped"] for g in skip["groups"].values()))
        self.assertIn(f"{SKIP} {skip['total']} (groups: ", self.line(SKIP))
        self.assertRegex(self.line("PE02 "), r"paragraph evidence \[ranking\] checked=0 skipped=1")
        self.assertRegex(self.line("ER02 "), r"enumerated relative paragraphs \[acquired\] checked=0 skipped=1")

    def test_stored_scans_without_a_matching_unit_are_listed_not_failed(self):
        zero = self.reg["stored_kind_zero_match"]
        for group in evaluate.STORED_SCAN_GROUPS:
            for x in self.reg[group]:
                if x["kind"] != "stored":
                    continue
                self.assertIn("units_matched", x)
                self.assertRegex(self.line(f"  {x['id']} "), rf"\[stored\] units matched={x['units_matched']} ")
                self.assertEqual(x["id"] in zero, x["units_matched"] == 0)
        passing_zero = [x for g in evaluate.STORED_SCAN_GROUPS[1:] for x in self.reg[g] if x["kind"] == "stored" and x["units_matched"] == 0]
        self.assertTrue(passing_zero)  # the synthetic index carries none of these constructions
        self.assertTrue(all(x["passed"] for x in passing_zero))  # a zero count is reported, never failed
        self.assertEqual(self.line(ZERO).strip(), f"{ZERO} {len(zero)} (ids: {', '.join(zero)})")

    def test_a_report_with_all_counts_zero_renders_zero_lines(self):
        report = copy.deepcopy(self.report)
        reg = report["regressions"]
        for group in evaluate.SKIP_COUNTED_GROUPS:
            for x in reg[group]:
                for key in ("deltas_skipped", "units_skipped"):
                    if key in x:
                        x[key] = 0
        for group in evaluate.STORED_SCAN_GROUPS:
            for x in reg[group]:
                if "units_matched" in x:
                    x["units_matched"] = 1
        reg["expectations_skipped_absent_document"] = evaluate.skipped_absent_document(reg)
        reg["stored_kind_zero_match"] = evaluate.stored_kind_zero_match(reg)
        text = evaluate.render(report)
        self.assertTrue(self.line(SKIP, text).strip().startswith(f"{SKIP} 0 (groups: "))
        self.assertEqual(set(re.findall(r"(?:deltas|units)=(\d+)", self.line(SKIP, text))), {"0"})
        self.assertEqual(self.line(ZERO, text).strip(), f"{ZERO} 0 (ids: none)")

    def test_overall_is_unchanged_by_the_new_fields(self):
        stripped = copy.deepcopy(self.report)
        reg = stripped["regressions"]
        for value in reg.values():
            for x in (value if isinstance(value, list) else []):
                if isinstance(x, dict):
                    for key in NEW_FIELDS:
                        x.pop(key, None)
        reg.pop("stored_kind_zero_match", None)
        self.assertEqual(evaluate.overall(stripped), self.report["overall"])
        self.assertEqual(evaluate.overall(self.report), self.report["overall"])


if __name__ == "__main__":
    unittest.main()
