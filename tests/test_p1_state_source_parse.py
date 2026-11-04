"""Tests for S7-P1-64: an unparseable state-source URL, in a manifest entry, an evidence record or a relied-on redirect hop, is reported
as a problem and never raised, so a build records the entry as skipped_invalid_state_metadata; canonical paths also reject empty segments
and stray percent signs. Also the single place that pins the state-source contract version. Synthetic or already-recorded metadata only.
Offline: network access is blocked for the whole module."""
import copy
import hashlib
import json
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import build, search, state_sources
from test_p1_rulemaking_sources import build_rule
from test_p1_tx_source_contract import entry

REF = "2026-11-03T12:00:00Z"
CONTRACT_VERSION = "p1-state-sources-0.9"
APPROVALS_SHA256 = "bd6c73d97ca153859d712022a7f2a7f0b1408bfeec0dcc63a0903aed34e1b1dd"
DIAGNOSTICS_SHA256 = "882d7c707961aa8a4b27b5ebb07ee03fd49f60630d4eac0fd1fe0995766f3a82"
TCSS, DOB, CAP = "tcss.legis.texas.gov", "www.dob.texas.gov", "statutes.capitol.texas.gov"
BRACKETED = f"https://[{TCSS}/resources/FI/htm/FI.31.htm"


def setUpModule():
    global _net
    _net = mock.patch.object(socket, "socket", side_effect=AssertionError("network access attempted in an offline test"))
    _net.start()


def tearDownModule():
    _net.stop()


def dumps(obj):
    return (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode()


class ContractVersionTests(unittest.TestCase):
    def test_contract_version_is_pinned_here_only(self):
        self.assertEqual(state_sources.contract()["version"], CONTRACT_VERSION)


class EntryParseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_A_an_unparseable_source_url_is_a_problem(self):
        found = state_sources.validate("state:TX:state_statute:t", entry(self.tmp, TCSS, "state_statute", source_url=BRACKETED))
        self.assertTrue(any(p.startswith("source_url cannot be parsed: ") for p in found), found)
        bad = entry(self.tmp, TCSS, "state_statute", source_url=BRACKETED, rights={})
        self.assertIn("missing field rights", state_sources.validate("state:TX:state_statute:t", bad))  # field checks still run

    def test_D_empty_segments_and_stray_percent_signs_are_rejected(self):
        def found(path):
            return state_sources.validate("state:TX:state_regulator_issuance:t",
                                          entry(self.tmp, DOB, "state_regulator_issuance", source_url=f"https://{DOB}{path}"))
        self.assertIn("source_url path is not canonical: empty segment", found("//laws-regulations"))
        self.assertIn("source_url path is not canonical: empty segment", found("/laws//regulations"))
        self.assertIn("source_url path is not canonical: stray percent sign", found("/laws%2regulations"))
        self.assertEqual(found("/laws-regulations"), [])


class EvidenceParseTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.reg = state_sources.contract(), state_sources.registry()

    def test_B_an_unparseable_final_url_withholds_the_class_only(self):
        reg = copy.deepcopy(self.reg)
        reg["evidence"]["EV-TX-DOB-LAWS-001"]["final_url"] = f"https://[{DOB}/laws-regulations"
        out = state_sources.approvals("TX", REF, self.spec, reg)[DOB]
        self.assertEqual((out["approved"], out["source_classes"]), (True, []))
        self.assertTrue(any(p.startswith(f"host {DOB} / state_regulator_issuance: evidence EV-TX-DOB-LAWS-001 final_url cannot be parsed: ")
                            for p in out["problems"]), out["problems"])

    def test_C_unparseable_requested_url_and_relied_on_hop(self):
        reg = copy.deepcopy(self.reg)
        reg["evidence"]["EV-TX-DOB-LAWS-001"]["requested_url"] = f"https://[{DOB}/laws-regulations"
        out = state_sources.approvals("TX", REF, self.spec, reg)[DOB]
        self.assertEqual((out["approved"], out["source_classes"]), (True, []))
        self.assertTrue(any("evidence EV-TX-DOB-LAWS-001 requested_url cannot be parsed: " in p for p in out["problems"]), out["problems"])
        reg = copy.deepcopy(self.reg)
        hop = reg["evidence"]["EV-TX-STAT-CITE-001"]["redirect_chain"][0]
        hop["from"] = hop["from"].replace("http://", "http://[")
        found = state_sources.evidence_problems("EV-TX-STAT-CITE-001", reg, CAP, self.spec["jurisdictions"]["TX"], REF)
        self.assertIn("evidence EV-TX-STAT-CITE-001 redirect hop 1 from cannot be parsed", found)
        self.assertFalse(state_sources.approvals("TX", REF, self.spec, reg)[CAP]["source_classes"])

    def test_F_the_shipped_registry_gains_no_parse_or_path_problem(self):
        jur = self.spec["jurisdictions"]["TX"]
        for eid, rec in self.reg["evidence"].items():
            found = state_sources.evidence_problems(eid, self.reg, rec["host"], jur, REF)
            self.assertFalse([p for p in found if any(n in p for n in ("cannot be parsed", "empty segment", "stray percent"))], eid)

    def test_G_approvals_and_diagnostics_bytes_are_unchanged(self):
        self.assertEqual(hashlib.sha256(dumps(state_sources.approvals("TX", REF))).hexdigest(), APPROVALS_SHA256)
        self.assertEqual(hashlib.sha256(dumps(state_sources.approval_diagnostics("TX", REF))).hexdigest(), DIAGNOSTICS_SHA256)


class ParseBuildBoundaryTests(unittest.TestCase):
    def test_E_a_bracketed_source_url_is_recorded_not_raised_by_the_build(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        ix, _ = build_rule(tmp, "complete", "base")
        ix.conn.close()
        root = Path(tmp) / "corpus-base"
        manifest = json.loads((root / "manifest.json").read_text())
        key = "state:TX:state_statute:synthetic"
        manifest["documents"][key] = entry(root, TCSS, "state_statute", source_url=BRACKETED)
        (root / "manifest.json").write_text(json.dumps(manifest))
        conn, _ = build.build(root, str(Path(tmp) / "x.sqlite"), "2026-10-29", log=lambda *_: None)
        conn.close()
        ix = search.Index(str(Path(tmp) / "x.sqlite"))
        self.addCleanup(ix.conn.close)
        status, reason = ix.conn.execute("SELECT status, reason FROM source_status WHERE doc_key=?", (key,)).fetchone()
        self.assertEqual(status, "skipped_invalid_state_metadata")
        self.assertIn("source_url cannot be parsed", reason)
        self.assertIsNone(ix.conn.execute("SELECT 1 FROM source_document WHERE doc_key LIKE 'state:%'").fetchone())
        self.assertEqual(ix.conn.execute("SELECT COUNT(*) FROM provision p JOIN source_document d ON d.id=p.doc_id "
                                         "WHERE d.doc_key LIKE 'state:%'").fetchone()[0], 0)
        self.assertEqual({t["id"]: t for t in ix.topics}["state_law"]["coverage"], "not_acquired")


if __name__ == "__main__":
    unittest.main()
