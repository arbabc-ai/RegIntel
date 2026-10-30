"""Tests for S7-P1-EVAL-EXIT-10: one explicit overall evaluation result, rendered, with a nonzero CLI exit status on any
failure category and the full --out report written before the failure status is returned."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, evaluate, search
from test_p1 import ECFR_HTML, asset
from test_p1_completeness import FR_EXPLICIT

REPO = Path(__file__).resolve().parent.parent
FROZEN_QUESTIONS_SHA256 = "ce470ba63eb97407af10477fdbf397d51a16a9f97383643f40e010570c90dc62"

QUESTIONS = {
    "version": "synthetic-exit-0.1",
    "retrieval": [{"id": "R1", "category": "concept", "query": "minimum capital", "scope": "current",
                   "gold": [{"doc_key": "ecfr:12:3", "locator": "3.1(a)"}]}],
    "delta": [{"id": "D1", "citation": "12 CFR 3.1",
               "expect": {"baseline_doc": "ecfr:12:3", "instruments": {"fr:2026-70012": {"state": "final_effective_date_pending"}}}}],
    "status": [{"id": "S1", "query": "CECL under ASC 326", "expect": {"status": "missing_evidence", "gap_topic": "gaap_asc"}}],
}
REGRESSIONS = {
    "version": "synthetic-reg-0.1", "task": "synthetic",
    "delta": [{"id": "G1", "citation": "12 CFR 3.1", "expect": {"instruments": {"fr:2026-70012": {}}, "all_units_target_queried": True}}],
    "relationship_integrity": {"id": "G8"},
    "citation_scope": [{"id": "C1", "query": "12 CFR 3.1", "future_nonempty": True, "current_nonempty": True}],
    "status": [{"id": "P1", "query": "CECL under ASC 326", "scope": "current", "expect_status": "missing_evidence", "gap_topic": "gaap_asc"}],
    "citation_completeness": [{"id": "K1", "query": "12 CFR 3.1", "expect_all_resolved": True}],
    "mixed_source": [{"id": "M1", "query": "Texas common equity tier 1 capital", "expect_gap_topic": "state_law",
                      "expect_acquired_topic": "cet1_capital", "expect_jurisdiction": "TX"}],
    "delta_precision": [{"id": "DP1", "citation": "12 CFR 3.1", "expect": {"evidence_status": "available", "baseline_doc": "ecfr:12:3"}}],
    "current_agreement": [{"id": "CA1", "citation": "12 CFR 3.1", "expect_via": "exact_section", "expect_doc": "ecfr:12:3",
                           "allow": ["12:3"], "exclude": ["12:217"]}],
}
# One impossible expectation per regression group.
BREAKERS = {
    "delta": lambda r: r["delta"][0]["expect"].update(absent_instruments=["fr:2026-70012"]),
    "citation_scope": lambda r: r["citation_scope"][0].update(query="12 CFR 3.2", future_nonempty=True),
    "status": lambda r: r["status"][0].update(expect_status="evidence_found"),
    "citation_completeness": lambda r: r["citation_completeness"][0].update(query="12 CFR 3.999", expect_all_resolved=True),
    "mixed_source": lambda r: r["mixed_source"][0].update(expect_gap_topic="gaap_asc"),
    "delta_precision": lambda r: r["delta_precision"][0]["expect"].update(evidence_status="unavailable"),
    "instruction_boundary": None,  # added with its own item below
    "current_agreement": lambda r: r["current_agreement"][0].update(expect_via="range_section"),
}


def load_cli():
    spec = importlib.util.spec_from_file_location("p1_cli", REPO / "scripts" / "p1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OverallResultTests(unittest.TestCase):
    """overall() on hand-built reports: each failure category alone, and the measurement-only rule for retrieval."""

    def base(self):
        return {"delta": [{"id": "D1", "passed": True}], "status": [{"id": "S1", "passed": True}],
                "retrieval_items": [{"id": "R1", "invalid_gold": []}], "filter_checks": [{"id": "R1", "retriever": "bm25", "respected": True}],
                "regressions": {"passed": True, "delta": [{"id": "G1", "passed": True}], "relationship_integrity": {"id": "G8", "passed": True}}}

    def only(self, report, category):
        ov = evaluate.overall(report)
        self.assertFalse(ov["passed"])
        for key, count in ov["failure_counts"].items():
            self.assertEqual(count > 0, key == category, (key, ov["failure_counts"]))
        return ov

    def test_pass(self):
        ov = evaluate.overall(self.base())
        self.assertTrue(ov["passed"])
        self.assertEqual(sum(ov["failure_counts"].values()), 0)

    def test_each_failure_category_independently(self):
        r = self.base(); r["delta"][0]["passed"] = False
        self.assertEqual(self.only(r, "delta_checks")["failures"]["delta_checks"], ["D1"])
        r = self.base(); r["status"][0]["passed"] = False
        self.assertEqual(self.only(r, "status_checks")["failures"]["status_checks"], ["S1"])
        r = self.base(); r["retrieval_items"][0]["invalid_gold"] = [{"doc_key": "x"}]
        self.assertEqual(self.only(r, "invalid_gold")["failures"]["invalid_gold"], ["R1"])
        r = self.base(); r["filter_checks"][0]["respected"] = False
        self.assertEqual(self.only(r, "part_filter_violations")["failures"]["part_filter_violations"], ["R1/bm25"])
        r = self.base(); r["regressions"]["passed"] = False; r["regressions"]["delta"][0]["passed"] = False
        ov = self.only(r, "regression_groups")
        self.assertEqual(ov["failures"]["regression_groups"], {"delta": ["G1"]})
        r = self.base(); r["regressions"]["passed"] = False; r["regressions"]["relationship_integrity"]["passed"] = False
        self.assertEqual(self.only(r, "regression_groups")["failures"]["regression_groups"], {"relationship_integrity": ["G8"]})

    def test_a_failed_regression_summary_without_a_failed_group_still_fails(self):
        r = self.base(); r["regressions"]["passed"] = False
        self.assertEqual(self.only(r, "regression_groups")["failures"]["regression_groups"],
                         {"regression_summary": ["regressions.passed is false"]})

    def test_retrieval_misses_are_measurements_not_failures(self):
        r = self.base()
        r["retrieval_summary"] = {"bm25": {"ALL": {"n": 1, "hit@1": 0.0, "hit@10": 0.0, "mrr": 0.0}}}
        self.assertTrue(evaluate.overall(r)["passed"])

    def test_regressions_not_run_is_stated_not_hidden(self):
        r = self.base(); r["regressions"] = None
        ov = evaluate.overall(r)
        self.assertTrue(ov["passed"])
        self.assertFalse(ov["regressions_run"])


class EvaluationEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        root = cls.dir / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        docs = {
            "ecfr:12:3": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 3", "title": "Capital",
                          "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                          "asset": asset(root, "e.html", ECFR_HTML.encode())},
            "fr:2026-70012": {**meta, "document_number": "2026-70012", "title": "Explicit", "document_type": "Rule",
                              "effective_on": "2027-02-01", "action": "Final rule.", "asset": asset(root, "x.xml", FR_EXPLICIT)},
        }
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(cls.dir / "e.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)
        cls.p1 = load_cli()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def write(self, name, obj):
        path = self.dir / name
        path.write_text(json.dumps(obj))
        return str(path)

    def run_eval(self, questions=None, regressions=None, index=None):
        q = self.write("q.json", questions or QUESTIONS)
        r = self.write("r.json", regressions or REGRESSIONS)
        return evaluate.run(index or self.index, q, r)

    # ---- success and each failure category through the real evaluation
    def test_clean_evaluation_passes(self):
        report = self.run_eval()
        self.assertTrue(report["overall"]["passed"], report["overall"]["failures"])
        self.assertIn("Overall evaluation: PASS", evaluate.render(report))

    def test_failed_delta_check(self):
        q = copy.deepcopy(QUESTIONS); q["delta"][0]["expect"]["min_instruments"] = 99
        report = self.run_eval(questions=q)
        self.assertFalse(report["overall"]["passed"])
        self.assertEqual(report["overall"]["failures"]["delta_checks"], ["D1"])
        self.assertEqual(report["overall"]["failure_counts"]["status_checks"], 0)
        self.assertIn("Overall evaluation: FAIL", evaluate.render(report))

    def test_failed_status_check(self):
        q = copy.deepcopy(QUESTIONS); q["status"][0]["expect"]["status"] = "evidence_found"
        report = self.run_eval(questions=q)
        self.assertEqual(report["overall"]["failures"]["status_checks"], ["S1"])
        self.assertEqual(report["overall"]["failure_counts"]["delta_checks"], 0)
        self.assertFalse(report["overall"]["passed"])

    def test_invalid_gold_matcher(self):
        q = copy.deepcopy(QUESTIONS); q["retrieval"][0]["gold"] = [{"doc_key": "ecfr:12:3", "locator": "9.9(z)"}]
        report = self.run_eval(questions=q)
        self.assertEqual(report["overall"]["failures"]["invalid_gold"], ["R1"])
        self.assertFalse(report["overall"]["passed"])
        self.assertIn("invalid gold matchers: 1", evaluate.render(report))

    def test_violated_part_filter(self):
        class IgnoresParts(search.Index):
            def retrieve(self, query, mode="hybrid", k=10, scope="both", parts=None):
                return super().retrieve(query, mode, k, scope, None)

        q = copy.deepcopy(QUESTIONS)
        q["retrieval"].append({"id": "R2", "category": "agency_scope", "query": "minimum capital", "scope": "current",
                               "parts": ["12:217"], "gold": [{"doc_key": "ecfr:12:3", "locator": "3.1(a)"}],
                               "all_results_in_parts": ["12:217"]})
        self.assertTrue(self.run_eval(questions=q)["overall"]["passed"])  # a correct index honours the filter
        bad = self.run_eval(questions=q, index=IgnoresParts(self.db, "2026-10-29"))
        self.assertTrue(bad["overall"]["failures"]["part_filter_violations"])
        self.assertTrue(all(v.startswith("R2/") for v in bad["overall"]["failures"]["part_filter_violations"]))
        self.assertFalse(bad["overall"]["passed"])

    def test_unrun_regressions_are_stated_in_the_rendering(self):
        report = evaluate.run(self.index, self.write("q.json", QUESTIONS), None)
        self.assertTrue(report["overall"]["passed"])
        self.assertIn("regression groups were not run", evaluate.render(report))

    def test_retrieval_misses_alone_do_not_fail(self):
        q = copy.deepcopy(QUESTIONS)
        q["retrieval"][0]["query"] = "zzzz qqqq unrelated"
        report = self.run_eval(questions=q)
        self.assertEqual(report["retrieval_summary"]["bm25"]["ALL"]["hit@10"], 0.0)  # a measured miss
        self.assertTrue(report["overall"]["passed"])

    def test_each_regression_group_fails_independently(self):
        for group, breaker in BREAKERS.items():
            regs = copy.deepcopy(REGRESSIONS)
            if group == "instruction_boundary":
                regs["instruction_boundary"] = [{"id": "IB1", "doc_key": "fr:2026-00000", "section": "1.1", "instruction_prefix": "x",
                                                 "instruction_max_len": 10, "carried_contains": ["x"], "supported": [], "unsupported": []}]
            else:
                breaker(regs)
            report = self.run_eval(regressions=regs)
            ov = report["overall"]
            self.assertFalse(ov["passed"], group)
            self.assertEqual(list(ov["failures"]["regression_groups"]), [group], (group, ov["failures"]))
            for cat in ("delta_checks", "status_checks", "invalid_gold", "part_filter_violations"):
                self.assertEqual(ov["failures"][cat], [], (group, cat))
            self.assertIn(f"regression group {group}", evaluate.render(report))

    def test_relationship_integrity_failure(self):
        bad = self.dir / "tampered.sqlite"
        shutil.copy(self.db, bad)
        conn = sqlite3.connect(bad)
        conn.execute("UPDATE relationship SET review_status='reviewed' WHERE rel_type='amends'")
        conn.commit()
        conn.close()
        report = self.run_eval(index=search.Index(str(bad), "2026-10-29"))
        self.assertEqual(list(report["overall"]["failures"]["regression_groups"]), ["relationship_integrity"])
        self.assertFalse(report["overall"]["passed"])

    # ---- CLI exit status and report persistence
    def run_cli(self, qfile, rfile, out):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = self.p1.main(["--db", self.db, "--as-of", "2026-10-29", "eval", "--questions", qfile,
                                 "--regressions", rfile, "--out", out])
        return code, buf.getvalue()

    def test_cli_exits_zero_on_a_clean_evaluation_and_writes_the_report(self):
        out = str(self.dir / "ok.json")
        code, text = self.run_cli(self.write("q.json", QUESTIONS), self.write("r.json", REGRESSIONS), out)
        self.assertEqual(code, 0)
        self.assertIn("Overall evaluation: PASS", text)
        self.assertTrue(json.loads(Path(out).read_text())["overall"]["passed"])

    def test_cli_exits_nonzero_for_every_failure_category_and_still_writes_the_full_report(self):
        cases = {}
        q = copy.deepcopy(QUESTIONS); q["delta"][0]["expect"]["min_instruments"] = 99; cases["delta_checks"] = (q, REGRESSIONS)
        q = copy.deepcopy(QUESTIONS); q["status"][0]["expect"]["status"] = "evidence_found"; cases["status_checks"] = (q, REGRESSIONS)
        q = copy.deepcopy(QUESTIONS); q["retrieval"][0]["gold"] = [{"doc_key": "ecfr:12:3", "locator": "9.9(z)"}]; cases["invalid_gold"] = (q, REGRESSIONS)
        regs = copy.deepcopy(REGRESSIONS); BREAKERS["status"](regs); cases["regression_groups"] = (QUESTIONS, regs)
        for category, (q, r) in cases.items():
            out = str(self.dir / f"fail_{category}.json")
            code, text = self.run_cli(self.write("q.json", q), self.write("r.json", r), out)
            self.assertNotEqual(code, 0, category)
            self.assertEqual(code, self.p1.EXIT_EVAL_FAILED)
            self.assertIn("Overall evaluation: FAIL", text)
            saved = json.loads(Path(out).read_text())  # the complete report, written before the failure status
            self.assertFalse(saved["overall"]["passed"])
            self.assertTrue(saved["overall"]["failure_counts"][category])
            for key in ("retrieval_summary", "retrieval_items", "delta", "status", "filter_checks", "regressions"):
                self.assertIn(key, saved, (category, key))

    def test_real_process_exit_status(self):
        qpass, rpass = self.write("qp.json", QUESTIONS), self.write("rp.json", REGRESSIONS)
        bad = copy.deepcopy(QUESTIONS); bad["status"][0]["expect"]["status"] = "evidence_found"
        qfail = self.write("qf.json", bad)
        for qfile, want in ((qpass, 0), (qfail, 1)):
            out = self.dir / f"proc_{want}.json"
            proc = subprocess.run([sys.executable, "-I", str(REPO / "scripts" / "p1.py"), "--db", self.db, "--as-of", "2026-10-29",
                                   "eval", "--questions", qfile, "--regressions", rpass, "--out", str(out)],
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, want, proc.stdout[-400:] + proc.stderr[-400:])
            self.assertIn("Overall evaluation: " + ("PASS" if want == 0 else "FAIL"), proc.stdout)
            self.assertEqual(json.loads(out.read_text())["overall"]["passed"], want == 0)

    def test_other_commands_still_exit_zero(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(self.p1.main(["--db", self.db, "delta", "12 CFR 3.1"]), 0)
            self.assertEqual(self.p1.main(["--db", self.db, "search", "minimum capital", "-k", "2"]), 0)


class FrozenArtifactTests(unittest.TestCase):
    def test_frozen_questions_are_unchanged_and_s05_s06_keep_their_expectations(self):
        raw = (REPO / "eval" / "phase1" / "questions.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), FROZEN_QUESTIONS_SHA256)
        status = {s["id"]: s for s in json.loads(raw)["status"]}
        self.assertEqual(status["S05"]["expect"]["status"], "evidence_found")
        self.assertEqual(status["S06"]["expect"]["status"], "evidence_found")


if __name__ == "__main__":
    unittest.main()
