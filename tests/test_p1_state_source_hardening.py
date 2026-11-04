"""Tests for S7-P1-61: state-source validator hardening. Class approval on a host that declares `path_pattern` is limited to conforming URL
paths; an https requested_url on another host needs a contiguous redirect chain to final_url; malformed registry or binding values are
evidence problems, never exceptions, including during a build. Synthetic or already-recorded metadata only. Offline: network access is
blocked for the whole module."""
import copy
import json
import re
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

from regintel_p1 import build, search, state_sources
from test_p1_rulemaking_sources import build_rule
from test_p1_tx_source_contract import entry

REF = "2026-11-03T12:00:00Z"
TCSS = "tcss.legis.texas.gov"
DOB = "www.dob.texas.gov"


def setUpModule():
    global _net
    _net = mock.patch.object(socket, "socket", side_effect=AssertionError("network access attempted in an offline test"))
    _net.start()


def tearDownModule():
    _net.stop()


def tx(spec):
    return spec["jurisdictions"]["TX"]


class PathPatternTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.reg = state_sources.contract(), state_sources.registry()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_path_pattern_is_declared_for_tcss_only_and_matches_its_recorded_evidence(self):
        hosts = tx(self.spec)["hosts"]
        self.assertEqual({h for h, m in hosts.items() if "path_pattern" in m}, {TCSS})
        pattern = hosts[TCSS]["path_pattern"]
        self.assertTrue(re.fullmatch(pattern, urlparse(self.reg["evidence"]["EV-TX-TCSS-FI31-001"]["final_url"]).path))
        self.assertTrue(re.fullmatch(pattern, "/resources/GV/htm/GV.323.htm"))

    def test_a_non_conforming_tcss_path_is_rejected_and_a_conforming_one_validates(self):
        self.assertEqual(state_sources.validate("state:TX:state_statute:t", entry(self.tmp, TCSS, "state_statute")), [])
        for path in ("/synthetic", "/resources/FI/htm/", "/Docs/FI/htm/FI.31.htm", "/resources/FI/pdf/FI.31.pdf"):
            bad = entry(self.tmp, TCSS, "state_statute", source_url=f"https://{TCSS}{path}")
            found = state_sources.validate("state:TX:state_statute:t", bad)
            self.assertTrue(any("path_pattern" in p and TCSS in p and "state_statute" in p and path in p for p in found), (path, found))

    def test_hosts_without_path_pattern_keep_todays_behaviour(self):
        self.assertEqual(state_sources.validate("state:TX:state_regulator_issuance:t", entry(self.tmp, DOB, "state_regulator_issuance")), [])

    def test_an_invalid_path_pattern_is_a_validation_problem_not_an_exception(self):
        spec = copy.deepcopy(self.spec)
        tx(spec)["hosts"][TCSS]["path_pattern"] = "([unclosed"
        found = state_sources.validate("state:TX:state_statute:t", entry(self.tmp, TCSS, "state_statute"), spec)
        self.assertTrue(any("path_pattern" in p and "not a valid" in p for p in found), found)


class RequestedUrlConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.reg = state_sources.contract(), state_sources.registry()

    def problems(self, chain, requested="https://www.texas.gov/banking"):
        reg = copy.deepcopy(self.reg)
        rec = reg["evidence"]["EV-TX-DOB-LAWS-001"]
        rec["requested_url"], rec["redirect_chain"] = requested, chain
        return state_sources.evidence_problems("EV-TX-DOB-LAWS-001", reg, DOB, tx(self.spec), REF, "state_regulator_issuance")

    def test_a_cross_host_https_request_needs_a_contiguous_chain_to_final_url(self):
        final = self.reg["evidence"]["EV-TX-DOB-LAWS-001"]["final_url"]
        req, mid = "https://www.texas.gov/banking", "https://www.texas.gov/banking/"
        needle = "requested_url host www.texas.gov differs from final_url host"
        for name, chain in {"none": None, "empty": [], "wrong_start": [{"status": 301, "from": mid, "to": final}],
                            "gap": [{"status": 301, "from": req, "to": mid}, {"status": 301, "from": "https://x.invalid/", "to": final}],
                            "wrong_end": [{"status": 301, "from": req, "to": mid}]}.items():
            self.assertTrue(any(needle in p for p in self.problems(chain)), name)
        good = [{"status": 301, "from": req, "to": mid}, {"status": 301, "from": mid, "to": final}]
        self.assertEqual(self.problems(good), [])

    def test_same_host_https_requests_and_the_shipped_registry_are_unchanged(self):
        self.assertEqual(self.problems([], requested=self.reg["evidence"]["EV-TX-DOB-LAWS-001"]["requested_url"]), [])
        jur = tx(self.spec)
        for host in jur["official_hosts"]:
            bind = jur["hosts"][host]["evidence"]
            for eid in [bind["host"], *bind["source_classes"].values()]:
                self.assertFalse([p for p in state_sources.evidence_problems(eid, self.reg, host, jur, REF) if "differs from final_url" in p], eid)


class MalformedRegistryTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.reg = state_sources.contract(), state_sources.registry()

    def record_problems(self, eid, host, field, value, cls=None):
        reg = copy.deepcopy(self.reg)
        reg["evidence"][eid][field] = value
        return state_sources.evidence_problems(eid, reg, host, tx(self.spec), REF, cls)

    def test_malformed_redirect_chains_are_problems(self):
        for eid, host in (("EV-TX-STAT-CITE-001", "statutes.capitol.texas.gov"), ("EV-TX-TCSS-FI31-001", TCSS)):  # relied on, and not
            for chain in ("oops", {"from": "x"}, 7, [{"status": 301}, 7], ["hop"]):
                found = self.record_problems(eid, host, "redirect_chain", chain)
                self.assertIn(f"evidence {eid} has a malformed redirect_chain", found, (eid, chain))

    def test_non_list_source_classes_in_a_record_is_a_problem(self):
        found = self.record_problems("EV-TX-DOB-LAWS-001", DOB, "source_classes", "state_regulator_issuance", "state_regulator_issuance")
        self.assertIn("evidence EV-TX-DOB-LAWS-001 has malformed source_classes (not a list)", found)
        reg = copy.deepcopy(self.reg)
        reg["evidence"]["EV-TX-DOB-LAWS-001"]["source_classes"] = "state_regulator_issuance"
        self.assertEqual(state_sources.approvals("TX", REF, self.spec, reg)[DOB]["source_classes"], [])

    def test_bool_byte_count_is_a_problem(self):
        self.assertIn("evidence EV-TX-DOB-HOST-001 has a malformed byte count", self.record_problems("EV-TX-DOB-HOST-001", DOB, "bytes", True))

    def test_non_dict_records_and_bindings_are_problems(self):
        reg = copy.deepcopy(self.reg)
        reg["evidence"]["EV-TX-DOB-HOST-001"] = "not a record"
        out = state_sources.approvals("TX", REF, self.spec, reg)[DOB]
        self.assertFalse(out["approved"])
        self.assertTrue(any("evidence EV-TX-DOB-HOST-001 is not a record object" in p for p in out["problems"]), out["problems"])
        spec = copy.deepcopy(self.spec)
        tx(spec)["hosts"][DOB]["evidence"] = "EV-TX-DOB-HOST-001"
        out = state_sources.approvals("TX", REF, spec, self.reg)[DOB]
        self.assertFalse(out["approved"])
        self.assertTrue(any(p == f"host {DOB}: evidence binding is not an object" for p in out["problems"]), out["problems"])
        spec = copy.deepcopy(self.spec)
        tx(spec)["hosts"][DOB]["evidence"]["source_classes"] = ["EV-TX-DOB-LAWS-001"]
        out = state_sources.approvals("TX", REF, spec, self.reg)[DOB]
        self.assertTrue(out["approved"])  # the host binding itself is intact
        self.assertEqual(out["source_classes"], [])
        self.assertIn(f"host {DOB} / state_regulator_issuance: evidence source_classes binding is not an object", out["problems"])


class MalformedRegistryBuildTests(unittest.TestCase):
    def test_a_state_entry_checked_against_a_malformed_registry_is_skipped_without_a_crash(self):
        cases = {"chain_hop": {"requested_url": "http://tcss.legis.texas.gov/resources/FI/htm/FI.31.htm", "redirect_chain": [{"status": 301}, 7]},
                 "classes_str": {"source_classes": "state_statute"}, "bytes_bool": {"bytes": True}, "chain_str": {"redirect_chain": "oops"}}
        for name, over in cases.items():
            with self.subTest(name):
                tmp = tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, tmp, True)
                ix, _ = build_rule(tmp, "complete", "base")
                ix.conn.close()
                root = Path(tmp) / "corpus-base"
                manifest = json.loads((root / "manifest.json").read_text())
                key = "state:TX:state_statute:synthetic"
                manifest["documents"][key] = entry(root, TCSS, "state_statute")
                (root / "manifest.json").write_text(json.dumps(manifest))
                reg = state_sources.registry()
                reg["evidence"]["EV-TX-TCSS-FI31-001"].update(over)
                with mock.patch.object(state_sources, "registry", lambda spec_=None: reg):
                    conn, _ = build.build(root, str(Path(tmp) / "x.sqlite"), "2026-10-29", log=lambda *_: None)
                conn.close()
                ix = search.Index(str(Path(tmp) / "x.sqlite"))
                try:
                    status, reason = ix.conn.execute("SELECT status, reason FROM source_status WHERE doc_key=?", (key,)).fetchone()
                    self.assertEqual(status, "skipped_invalid_state_metadata", reason)
                    self.assertIsNone(ix.conn.execute("SELECT 1 FROM source_document WHERE doc_key LIKE 'state:%'").fetchone())
                    self.assertEqual(ix.conn.execute("SELECT COUNT(*) FROM provision p JOIN source_document d ON d.id=p.doc_id "
                                                     "WHERE d.doc_key LIKE 'state:%'").fetchone()[0], 0)
                    self.assertEqual({t["id"]: t for t in ix.topics}["state_law"]["coverage"], "not_acquired")
                finally:
                    ix.conn.close()


if __name__ == "__main__":
    unittest.main()
