"""Tests for S7-P1-79 (C3 F-78-1, F-78-2): render() prints deltas checked/skipped on every lowercase_paragraph_clause line, and one roll-up
line after the regression header counts the expectations skipped because their document is absent from the evaluated index. Reporting only:
no pass/fail outcome changes. Governing scope: founder record 2026-11-04_s7-regintel-p1-founder-decisions_accepted.md."""
import copy
import re
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import evaluate
from test_p1_triple_letter_paragraph_list import build_index

EVAL = Path(__file__).resolve().parent.parent / "eval/phase1"
ROLLUP = "expectations skipped because the document is absent from the index:"


class SkipVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name)  # the task-77 synthetic index: none of the acquired documents
        cls.report = evaluate.run(cls.ix, EVAL / "questions_v0.3.json", EVAL / "regressions.json")  # a complete report
        cls.text = evaluate.render(cls.report)

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def rollup_line(self, text):
        lines = [l for l in text.splitlines() if ROLLUP in l]
        self.assertEqual(len(lines), 1, lines)
        return lines[0]

    def test_the_roll_up_follows_the_header_and_counts_the_triple_letter_skips(self):
        lines = self.text.splitlines()
        header = next(i for i, l in enumerate(lines) if l.startswith("Boundary regressions ("))
        self.assertIn(ROLLUP, lines[header + 1])
        skip = self.report["regressions"]["expectations_skipped_absent_document"]
        self.assertEqual(skip["groups"]["triple_letter_paragraph_list"], {"deltas_skipped": 2, "units_skipped": 4})
        self.assertEqual(skip["total"], sum(g["deltas_skipped"] + g["units_skipped"] for g in skip["groups"].values()))
        line = self.rollup_line(self.text)
        self.assertIn("triple_letter_paragraph_list deltas=2 units=4", line)
        self.assertIn(f"{ROLLUP} {skip['total']} (groups: ", line)

    def test_lowercase_clause_lines_carry_the_counts(self):
        lc = [l for l in self.text.splitlines() if "lowercase paragraph clause [" in l]
        self.assertEqual(len(lc), len(self.report["regressions"]["lowercase_paragraph_clause"]))
        for line, x in zip(lc, self.report["regressions"]["lowercase_paragraph_clause"]):
            self.assertRegex(line, rf"^  {x['id']} (PASS|FAIL) lowercase paragraph clause \[{x['kind']}\] deltas checked=\d+ skipped=\d+")
            self.assertIn(f"deltas checked={x['deltas_checked']} skipped={x['deltas_skipped']}", line)

    def test_a_report_without_skips_prints_zero(self):
        report = copy.deepcopy(self.report)
        reg = report["regressions"]
        for group in evaluate.SKIP_COUNTED_GROUPS:
            for x in reg[group]:
                for key in ("deltas_skipped", "units_skipped"):
                    if key in x:
                        x[key] = 0
        reg["expectations_skipped_absent_document"] = evaluate.skipped_absent_document(reg)
        line = self.rollup_line(evaluate.render(report))
        self.assertTrue(line.strip().startswith(f"{ROLLUP} 0 (groups: "), line)
        self.assertEqual(re.findall(r"(?:deltas|units)=(\d+)", line), ["0"] * (2 * len(evaluate.SKIP_COUNTED_GROUPS)))  # S7-P1-81: more groups

    def test_the_roll_up_changes_no_outcome(self):
        reg = self.report["regressions"]
        recomputed = copy.deepcopy(reg)
        del recomputed["expectations_skipped_absent_document"]
        self.assertEqual(evaluate.skipped_absent_document(recomputed), reg["expectations_skipped_absent_document"])
        self.assertEqual(evaluate.overall(self.report), self.report["overall"])


if __name__ == "__main__":
    unittest.main()
