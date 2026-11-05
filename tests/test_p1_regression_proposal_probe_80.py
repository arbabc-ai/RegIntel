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


if __name__ == "__main__":
    unittest.main()
