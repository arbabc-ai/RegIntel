"""Tests for S7-P1-72: DR-6 federal rights fields (DR-2 and DR-6, founder record 2026-11-04_s7-regintel-p1-founder-decisions_accepted.md).
A federal entry written by the downloader carries the rights record and cannot be written without it; the validator and CLI report
incomplete entries; the pinned manifest plus its sidecar overlay passes; federal and state rights use one field set. Offline:
network access is blocked for the whole module."""
import copy
import hashlib
import json
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import federal_rights, state_sources
from scripts import corpus_download as c
from scripts import p1_manifest_rights_check as cli
from test_corpus_download import FakeClient, ecfr_data
from test_p1_tx_source_contract import entry as state_entry

MANIFEST = Path("/home/arbab/projects/regintel-corpus/phase1/manifest.json")
OVERLAY = MANIFEST.with_name("federal_rights.json")
# S7-P1-74: the Tranche C Treasury run (run 1f5976edda804c1a95857577ec0c87b2; DR-5, founder record
# 2026-11-04_s7-regintel-p1-founder-decisions_accepted.md) moved the manifest from 60cf22e1... to this value.
PINNED_MANIFEST_SHA256 = "6b4f56e42ac2b258fb8dbf459071ec55c19f034b3fc40a13885e43c14319e9fb"
PINNED_OVERLAY_SHA256 = "be0fcb0a9a8537989bc52c6bf7eaaf24cd72b766e2d789b3ae5216fe1c0ae4dd"  # not rewritten by S7-P1-74
TRANCHE_C_RUN_LOG = MANIFEST.with_name("runs") / "1f5976edda804c1a95857577ec0c87b2.json"


def setUpModule():
    global _net
    _net = mock.patch.object(socket, "socket", side_effect=AssertionError("network access attempted in an offline test"))
    _net.start()


def tearDownModule():
    _net.stop()


def run(argv):
    with mock.patch("builtins.print"), mock.patch("sys.stderr"):
        try:
            return cli.main(argv)
        except SystemExit as e:
            return e.code


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.corpus = c.Corpus(Path(self.tmp.name))

    def test_a_downloaded_federal_entry_carries_every_rights_field(self):
        c.download_ecfr(c.parser().parse_args(["ecfr", "--part", "12:1"]), FakeClient(ecfr_data()), self.corpus)
        rights = self.corpus.manifest["documents"]["ecfr:12:1"]["rights"]
        self.assertEqual(set(rights), set(federal_rights.rights_fields()))
        self.assertEqual(federal_rights.record_problems(rights), [])
        self.assertEqual(rights["usage_status"], "permitted")
        self.assertIn(federal_rights.DECISION_RECORD, rights["permission_reference"])
        # S7-P1-73 choice: terms_checked_at is RECORDED_ON, the DR-6 decision date, for every federal entry (not the run date)
        self.assertEqual(rights["terms_checked_at"], federal_rights.RECORDED_ON)
        self.assertEqual(federal_rights.RECORDED_ON, "2026-11-04")
        self.assertEqual(rights, federal_rights.federal_rights("eCFR"))
        self.assertEqual(federal_rights.federal_rights("Federal Register")["terms_url"], "https://www.federalregister.gov/")

    def test_a_federal_entry_cannot_be_written_without_rights(self):
        good = {"source_class": "regulation", "publisher": "eCFR", "rights": federal_rights.federal_rights("eCFR")}
        for name, record in {"absent": {k: v for k, v in good.items() if k != "rights"},
                             "partial": {**good, "rights": {k: v for k, v in good["rights"].items() if k != "terms_url"}},
                             "uncited": {**good, "rights": {**good["rights"], "permission_reference": "operator said so"}}}.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.corpus.record("ecfr:12:9", record)
            self.assertNotIn("ecfr:12:9", self.corpus.manifest["documents"])
        self.corpus.record("ecfr:12:9", good)
        self.assertIn("ecfr:12:9", self.corpus.manifest["documents"])

    def test_a_download_whose_rights_builder_fails_is_a_recorded_failure_not_an_entry(self):
        with mock.patch.object(c.federal_rights, "federal_rights", lambda publisher: {}):
            counts = c.download_ecfr(c.parser().parse_args(["ecfr", "--part", "12:1"]), FakeClient(ecfr_data()), self.corpus)
        self.assertEqual(counts["failed"], 1)
        self.assertNotIn("ecfr:12:1", self.corpus.manifest["documents"])
        self.assertIn("DR-6 rights record", self.corpus.manifest["failures"]["ecfr:12:1"]["error"])


class ValidatorTests(unittest.TestCase):
    def manifest(self):
        return {"schema_version": 1, "failures": {}, "documents": {
            "ecfr:12:1": {"source_class": "regulation", "publisher": "eCFR"},
            "fr:2026-00001": {"source_class": "rulemaking_candidate", "publisher": "Federal Register"},
            "state:TX:state_statute:x": {"source_class": "state_statute"}}}

    def test_incomplete_entries_are_reported_and_the_cli_exits_1(self):
        m = self.manifest()
        overlay = federal_rights.build_overlay(m, "0" * 64)
        self.assertEqual(sorted(overlay["records"]), ["ecfr:12:1", "fr:2026-00001"])  # the state entry is not federal
        self.assertTrue(federal_rights.check(m, overlay)["passed"])
        partial = copy.deepcopy(overlay)
        del partial["records"]["fr:2026-00001"]["terms_checked_at"]
        report = federal_rights.check(m, partial)
        self.assertEqual(report["incomplete"], ["fr:2026-00001"])
        self.assertIn("missing rights field terms_checked_at", report["entries"][1]["problems"])
        with tempfile.TemporaryDirectory() as tmp:
            mp, op, out = Path(tmp) / "manifest.json", Path(tmp) / "federal_rights.json", Path(tmp) / "out.json"
            mp.write_text(json.dumps(m))
            overlay["manifest_sha256"] = hashlib.sha256(mp.read_bytes()).hexdigest()  # pin the overlay to the manifest written
            op.write_text(json.dumps(overlay))
            self.assertEqual(run(["--manifest", str(mp), "--out", str(out)]), 0)
            del overlay["records"]["ecfr:12:1"]
            op.write_text(json.dumps(overlay))
            self.assertEqual(run(["--manifest", str(mp), "--out", str(out)]), 1)
            self.assertEqual(json.loads(out.read_text())["incomplete"], ["ecfr:12:1"])
            self.assertEqual(run(["--manifest", str(mp), "--write-overlay", str(op)]), 2)  # never overwrites

    def test_the_field_set_is_identical_for_state_and_federal_records(self):
        spec = state_sources.contract()
        self.assertEqual(spec["required_rights_fields"], ["terms_url", "terms_checked_at", "usage_status"])  # state shape unchanged
        self.assertEqual(federal_rights.rights_fields(), ("terms_url", "terms_checked_at", "usage_status", "permission_reference"))
        with tempfile.TemporaryDirectory() as tmp:
            rights = {"terms_url": "https://tcss.legis.texas.gov/t", "terms_checked_at": "2026-11-03T00:00:00Z", "usage_status": "permitted",
                      "permission_reference": federal_rights.PERMISSION_REFERENCE}
            st = state_entry(Path(tmp), "tcss.legis.texas.gov", "state_statute", rights=rights)
            self.assertEqual(set(st["rights"]), set(federal_rights.federal_rights("eCFR")))
            self.assertEqual(state_sources.validate("state:TX:state_statute:t", st), [])
            st["rights"] = {k: v for k, v in rights.items() if k != "permission_reference"}
            self.assertTrue(any("permission_reference" in p for p in state_sources.validate("state:TX:state_statute:t", st)))


@unittest.skipUnless(MANIFEST.exists() and OVERLAY.exists(), "current corpus manifest and overlay not present")
class CurrentCorpusTests(unittest.TestCase):
    def test_the_pinned_manifest_plus_overlay_passes_and_a_removed_record_fails(self):
        self.assertEqual(hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), PINNED_MANIFEST_SHA256)
        self.assertEqual(hashlib.sha256(OVERLAY.read_bytes()).hexdigest(), PINNED_OVERLAY_SHA256)
        report = federal_rights.check(json.loads(MANIFEST.read_text()), json.loads(OVERLAY.read_text()))
        self.assertTrue(report["passed"])
        self.assertEqual((report["federal_entries"], report["complete"]), (140, 140))
        sources = [r["rights_source"] for r in report["entries"]]
        self.assertEqual(sources.count("overlay"), 89)  # the pre-run federal entries, from the unchanged overlay
        counts = json.loads(TRANCHE_C_RUN_LOG.read_text())["counts"]
        self.assertEqual(sources.count("inline"), counts["downloaded"] + counts["reused"])  # the Tranche C entries
        pinned = federal_rights.check(json.loads(MANIFEST.read_text()), json.loads(OVERLAY.read_text()),
                                      hashlib.sha256(MANIFEST.read_bytes()).hexdigest())
        self.assertTrue(pinned["passed"])  # still passes with the manifest sha256 given (the overlay pin is absent)
        self.assertEqual((pinned["overlay_manifest_sha256"], pinned["manifest_mismatch"]), ("absent", False))
        with tempfile.TemporaryDirectory() as tmp:
            mp, op, out = Path(tmp) / "manifest.json", Path(tmp) / "federal_rights.json", Path(tmp) / "out.json"
            shutil.copy(MANIFEST, mp)
            overlay = json.loads(OVERLAY.read_text())
            removed = sorted(overlay["records"])[0]
            del overlay["records"][removed]
            op.write_text(json.dumps(overlay))
            self.assertEqual(run(["--manifest", str(mp), "--out", str(out)]), 1)
            self.assertEqual(json.loads(out.read_text())["incomplete"], [removed])
        self.assertEqual(hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), PINNED_MANIFEST_SHA256)


if __name__ == "__main__":
    unittest.main()
