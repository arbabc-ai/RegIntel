"""Tests for S7-P1-62: `approval_diagnostics()` reports, per Texas host and class, which evidence record backs the approval and whether the
S7-P1-59 distinctness check was really exercised; it withholds and widens nothing. Also the reject-only query-string rule for hosts that
declare `path_pattern`. Synthetic or already-recorded metadata only. Offline: network access is blocked for the whole module."""
import copy
import hashlib
import json
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import state_sources
from test_p1_tx_source_contract import entry

REF = "2026-11-03T12:00:00Z"
APPROVALS_SHA256 = "bd6c73d97ca153859d712022a7f2a7f0b1408bfeec0dcc63a0903aed34e1b1dd"  # S7-P1-60/61 AFTER file
TCSS, DOB = "tcss.legis.texas.gov", "www.dob.texas.gov"


def setUpModule():
    global _net
    _net = mock.patch.object(socket, "socket", side_effect=AssertionError("network access attempted in an offline test"))
    _net.start()


def tearDownModule():
    _net.stop()


def approvals_bytes(spec=None, reg=None):
    return (json.dumps(state_sources.approvals("TX", REF, spec, reg), indent=1, sort_keys=True) + "\n").encode()


class ShippedDiagnosticsTests(unittest.TestCase):
    def test_a_diagnostics_from_the_shipped_contract_and_registry(self):
        diag = state_sources.approval_diagnostics("TX", REF)
        expected = {
            "statutes.capitol.texas.gov": ("EV-TX-STAT-HOST-001", "host", {"state_statute": ("EV-TX-STAT-CITE-001", False, "checked_identical")}, None, False),
            TCSS: ("EV-TX-TCSS-FI31-001", "source_class", {"state_statute": ("EV-TX-TCSS-FI31-001", True, "vacuous_same_record")}, None, True),
            "www.sos.state.tx.us": ("EV-TX-SOS-TAC-001", "source_class",
                                    {"state_administrative_rule": ("EV-TX-SOS-TAC-001", True, "vacuous_same_record")}, "index_page_only", False),
            "www.sos.texas.gov": ("EV-TX-SOS-TEXREG-001", "source_class",
                                  {"state_register_notice": ("EV-TX-SOS-TEXREG-001", True, "vacuous_same_record"),
                                   "state_administrative_rule": ("EV-TX-SOS-TEXREG-001", True, "vacuous_same_record")}, "index_page_only", False),
            DOB: ("EV-TX-DOB-HOST-001", "host", {"state_regulator_issuance": ("EV-TX-DOB-LAWS-001", False, "checked_distinct")}, None, False),
            "comptroller.texas.gov": ("EV-TX-CPA-HOST-001", "host",
                                      {"state_regulator_issuance": ("EV-TX-CPA-RULES-001", False, "checked_distinct")}, None, False),
        }
        self.assertEqual(set(diag), set(expected))
        for host, (hid, level, classes, basis, pattern) in expected.items():
            d = diag[host]
            self.assertEqual((d["host_evidence_id"], d["host_evidence_level"], d["endpoint_basis"], d["path_pattern_declared"]),
                             (hid, level, basis, pattern), host)
            self.assertEqual(d["host_and_class_same_record"], {c: v[1] for c, v in classes.items()}, host)
            self.assertEqual({c: (v["class_evidence_id"], v["distinctness_check"]) for c, v in d["classes"].items()},
                             {c: (v[0], v[2]) for c, v in classes.items()}, host)


class SyntheticDistinctnessTests(unittest.TestCase):
    """Each distinctness_check value from a synthetic registry built on the www.dob.texas.gov records."""

    def setUp(self):
        self.spec, base = state_sources.contract(), state_sources.registry()
        h, c = copy.deepcopy(base["evidence"]["EV-TX-DOB-HOST-001"]), copy.deepcopy(base["evidence"]["EV-TX-DOB-LAWS-001"])
        h.update(evidence_id="EV-SYN-HOST", sha256="a" * 64, bytes=1000)
        c.update(evidence_id="EV-SYN-CLASS", sha256="b" * 64, bytes=2000)
        self.reg = {k: v for k, v in copy.deepcopy(base).items() if k != "evidence"}
        self.reg["evidence"] = {"EV-SYN-HOST": h, "EV-SYN-CLASS": c}
        self.bind(DOB, "EV-SYN-HOST", "EV-SYN-CLASS")

    def bind(self, host, hid, cid):
        self.spec["jurisdictions"]["TX"]["hosts"][host]["evidence"] = {"host": hid, "source_classes": {"state_regulator_issuance": cid}}

    def check(self):
        return state_sources.approval_diagnostics("TX", REF, self.spec, self.reg)[DOB]["classes"]["state_regulator_issuance"]["distinctness_check"]

    def test_b1_checked_distinct(self):
        self.assertEqual(self.check(), "checked_distinct")

    def test_b2_checked_identical(self):
        c = self.reg["evidence"]["EV-SYN-CLASS"]
        c["sha256"], c["bytes"] = "a" * 64, 1000
        self.assertEqual(self.check(), "checked_identical")
        self.assertTrue(any("same sha256" in p for p in state_sources.approvals("TX", REF, self.spec, self.reg)[DOB]["problems"]))

    def test_b3_vacuous_same_record(self):
        self.reg["evidence"]["EV-SYN-HOST"].update(level="source_class", source_classes=["state_regulator_issuance"])
        self.bind(DOB, "EV-SYN-HOST", "EV-SYN-HOST")
        self.assertEqual(self.check(), "vacuous_same_record")

    def test_b4_no_host_level_baseline(self):
        self.reg["evidence"]["EV-SYN-HOST"].update(level="source_class", source_classes=["state_regulator_issuance"])
        self.assertEqual(self.check(), "no_host_level_baseline")
        d = state_sources.approval_diagnostics("TX", REF, self.spec, self.reg)[DOB]
        self.assertEqual((d["host_evidence_level"], d["host_and_class_same_record"]), ("source_class", {"state_regulator_issuance": False}))

    def test_b5_not_evaluated(self):
        self.reg["evidence"]["EV-SYN-CLASS"]["status"] = 404
        self.assertEqual(self.check(), "not_evaluated")
        self.bind(DOB, "EV-SYN-HOST", "EV-DOES-NOT-EXIST")
        self.assertEqual(self.check(), "not_evaluated")

    def test_b6_missing_or_invalid_host_evidence(self):
        self.bind(DOB, "EV-DOES-NOT-EXIST", "EV-SYN-CLASS")
        self.assertEqual(state_sources.approval_diagnostics("TX", REF, self.spec, self.reg)[DOB]["host_evidence_level"], "missing_or_invalid")


class DiagnosticsWidenNothingTests(unittest.TestCase):
    def test_c_approvals_are_unchanged_and_inputs_are_not_mutated(self):
        spec, reg = state_sources.contract(), state_sources.registry()
        spec0, reg0 = copy.deepcopy(spec), copy.deepcopy(reg)
        before = approvals_bytes(spec, reg)
        state_sources.approval_diagnostics("TX", REF, spec, reg)
        self.assertEqual((spec, reg), (spec0, reg0))
        after = approvals_bytes(spec, reg)
        self.assertEqual(before, after)
        self.assertEqual(hashlib.sha256(approvals_bytes()).hexdigest(), APPROVALS_SHA256)
        self.assertEqual(json.loads(after), json.loads(before))


class QueryStringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_d_query_strings_are_rejected_on_path_pattern_hosts_only(self):
        base = f"https://{TCSS}/resources/FI/htm/FI.31.htm"
        self.assertEqual(state_sources.validate("state:TX:state_statute:t", entry(self.tmp, TCSS, "state_statute", source_url=base + "#31.002")), [])
        for url in (base + "?x=1", base + ";v=2", base + "?x=1#31.002"):
            found = state_sources.validate("state:TX:state_statute:t", entry(self.tmp, TCSS, "state_statute", source_url=url))
            self.assertIn(f"source_url on {TCSS} carries a query string; path_pattern hosts accept the path alone", found, url)
        dob = entry(self.tmp, DOB, "state_regulator_issuance", source_url=f"https://{DOB}/laws-regulations?page=2")
        self.assertEqual(state_sources.validate("state:TX:state_regulator_issuance:t", dob), [])


if __name__ == "__main__":
    unittest.main()
