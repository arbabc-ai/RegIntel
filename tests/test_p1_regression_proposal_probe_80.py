"""Tests for scripts/p1_regression_proposal_probe_80.py (S7-P1-80, proposal tooling): a drafted literal is run through the existing check
function, a mutation of its non-vacuous field must fail, absent parts are reported against the manifest and source_document, and the index
is opened read-only and left byte-identical. Offline; synthetic index from the task-77 fixture. Governing scope: founder record
2026-11-04_s7-regintel-p1-founder-decisions_accepted.md."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import p1_regression_proposal_probe_80 as probe
from test_p1_triple_letter_paragraph_list import build_index


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name)  # corpus/manifest.json and i.sqlite under tmp
        cls.root = Path(cls.tmp.name)
        cls.instruments = [g["doc_key"] for g in cls.ix.delta("§ 1010.100")["instruments"]]

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def drafts(self):
        return [
            {"check": "K02", "option": "R", "group": "citation_completeness",
             "literal": {"id": "K02", "query": "12 CFR 229.1", "expect_unresolved_somewhere": ["12 CFR 229.1"],
                         "expect_reasons": {"12 CFR 229.1": ["part_not_acquired"]}},
             "mutation": {"path": ["expect_reasons", "12 CFR 229.1"], "value": ["excluded_by_requested_scope"]},
             "absent_parts": ["12:229", "31:1010"]},
            {"check": "TD06", "option": "P", "group": "titleless_delta",
             "literal": {"id": "TD06", "titleless": "§ 1010.100", "explicit": "31 CFR 1010.100", "title": "31",
                         "instruments": self.instruments, "baseline": True},
             "mutation": {"path": ["instruments"], "value": []}},
        ]

    def test_literals_pass_mutations_fail_and_absence_is_reported(self):
        self.assertTrue(self.instruments)  # the fixture's three proposals connect to 1010.100
        manifest = json.loads((self.root / "corpus" / "manifest.json").read_text())
        rows = probe.probe(self.ix, manifest, self.drafts())
        self.assertEqual([r["passed"] for r in rows], [True, True], [r["failures"] for r in rows])
        self.assertEqual([r["non_vacuity"]["mutated_passed"] for r in rows], [False, False])
        self.assertEqual([(a["part"], a["in_manifest"], a["in_source_document"]) for a in rows[0]["absent_parts"]],
                         [("12:229", False, False), ("31:1010", True, True)])
        reasons = {c["reason"] for a in rows[0]["observed"]["answers"] for c in a["citations"] if not c["resolved"]}
        self.assertEqual(reasons, {"part_not_acquired"})
        self.assertEqual(rows[1]["observed"]["instruments"], self.instruments)

    def test_cli_leaves_the_index_byte_identical(self):
        db = self.root / "i.sqlite"
        drafts, out = self.root / "drafts.json", self.root / "probe.json"
        drafts.write_text(json.dumps({"drafts": self.drafts()}))
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        with mock.patch("builtins.print"):
            code = probe.main(["--index", str(db), "--manifest", str(self.root / "corpus" / "manifest.json"),
                               "--drafts", str(drafts), "--out", str(out)])
        self.assertEqual(code, 0)
        report = json.loads(out.read_text())
        self.assertEqual((report["index"]["sha256"], report["index"]["unchanged_after_run"]), (before, True))
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)
        self.assertEqual((report["legal_validity"], report["applicability_status"]), ("not_determined", "not_assessed"))
        self.assertEqual(report["status"], "proposal; producer-reported; no gate approved; nothing authorised")


    def test_instruction_boundary_title_and_list_drafts_are_observed(self):
        # S7-P1-81 (F-80-2): the instruction_boundary title comes from the literal; query_lists / part_lists drafts are observed too
        ib = {"id": "IBX", "doc_key": "fr:2026-97001", "section": "1010.100", "title": "31", "instruction_prefix": "1. Amend",
              "instruction_max_len": 400, "carried_contains": ["x"], "supported": ["(ooo)"], "unsupported": ["(zzz)"]}
        ql = {"id": "QLX", "kind": "equivalent", "shorthand": "31 CFR 1010.100, 1020.1", "control": "31 CFR 1010.100 and 31 CFR 1020.1",
              "members": ["31 CFR 1010.100", "31 CFR 1020.1"], "complete": False, "gap": "citation:31 CFR 1020.1"}
        manifest = json.loads((self.root / "corpus" / "manifest.json").read_text())
        rows = probe.probe(self.ix, manifest, [
            {"check": "IB01", "option": "R", "group": "instruction_boundary", "literal": ib, "present_parts": ["31:1010"]},
            {"check": "QL01", "option": "R", "group": "query_lists", "literal": ql, "absent_parts": ["31:1020"]},
            {"check": "PL01", "option": "R", "group": "part_lists", "literal": {**ql, "id": "PLX"}}])
        obs = rows[0]["observed"]
        self.assertEqual(obs["title"], "31")
        self.assertIs(rows[0].get("evaluator_title_mismatch"), True)  # S7-P1-82 (F-81-2): the evaluator reads 12 CFR
        twelve = probe.probe(self.ix, manifest, [{"check": "IB01", "option": "P", "group": "instruction_boundary",
                                                  "literal": {**ib, "id": "IBZ", "title": "12"}}])
        self.assertNotIn("evaluator_title_mismatch", twelve[0])
        self.assertEqual(obs["units"][0]["explicit_paragraphs"]["paths"], ["(e)", "(r)", "(nnn)", "(ooo)", "(xxx)"])
        self.assertEqual((obs["paths"]["(ooo)"]["future_resolved"], obs["paths"]["(ooo)"]["resolver_rows"]), (True, 1))  # resolved as 31 CFR
        self.assertEqual(obs["paths"]["(zzz)"]["future_resolved"], False)
        self.assertEqual(rows[0]["present_parts"], [{"part": "31:1010", "manifest_key": "ecfr:31:1010", "in_manifest": True,
                                                     "in_source_document": True}])
        for row in rows[1:]:
            self.assertEqual(row["observed"]["members_parsed"], ["31 CFR 1010.100", "31 CFR 1020.1"])
            self.assertEqual(row["observed"]["current_exact"]["citation_gaps"], ["citation:31 CFR 1020.1"])
        self.assertEqual(rows[1]["absent_parts"][0]["in_manifest"], False)

    def test_an_empty_resolver_result_is_recorded_not_raised(self):
        ib = {"id": "IBY", "doc_key": "fr:2026-97001", "section": "1010.100", "title": "31", "supported": ["(ooo)"], "unsupported": []}
        with mock.patch.object(self.ix, "resolve_citations", return_value=[]):
            obs = probe.observe(self.ix, "instruction_boundary", ib)
        self.assertEqual((obs["paths"]["(ooo)"]["future_resolved"], obs["paths"]["(ooo)"]["resolver_rows"]), (None, 0))

    def test_the_cli_lists_evaluator_title_mismatches(self):
        ib = {"id": "IBX", "doc_key": "fr:2026-97001", "section": "1010.100", "title": "31", "instruction_prefix": "1. Amend",
              "instruction_max_len": 400, "carried_contains": ["x"], "supported": ["(ooo)"], "unsupported": []}
        drafts, out = self.root / "drafts_tm.json", self.root / "probe_tm.json"
        drafts.write_text(json.dumps({"drafts": [{"check": "IB01", "option": "R", "group": "instruction_boundary", "literal": ib},
                                                 {"check": "IB01", "option": "P", "group": "instruction_boundary",
                                                  "literal": {**ib, "id": "IBZ", "title": "12"}}]}))
        with mock.patch("builtins.print"):
            probe.main(["--index", str(self.root / "i.sqlite"), "--manifest", str(self.root / "corpus" / "manifest.json"),
                        "--drafts", str(drafts), "--out", str(out)])
        self.assertEqual(json.loads(out.read_text())["evaluator_title_mismatches"], [["IB01", "R", "31"]])

    def test_a_missing_present_part_makes_the_cli_exit_1(self):
        drafts, out = self.root / "drafts_pp.json", self.root / "probe_pp.json"
        bad = [{**self.drafts()[0], "present_parts": ["31:1010", "12:229"]}]  # 12:229 is held by neither
        drafts.write_text(json.dumps({"drafts": bad}))
        with mock.patch("builtins.print"):
            code = probe.main(["--index", str(self.root / "i.sqlite"), "--manifest", str(self.root / "corpus" / "manifest.json"),
                               "--drafts", str(drafts), "--out", str(out)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.read_text())["present_parts_failing"], [["K02", "R", "12:229"]])


if __name__ == "__main__":
    unittest.main()
