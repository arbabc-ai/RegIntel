"""Tests for S7-P1-63: reject-only URL canonicalization. A state entry's source_url must have a bare lowercase netloc and a canonical
path on every host; evidence records must have bare lowercase netlocs in requested_url, final_url and every relied-on redirect hop; the
tcss path_pattern binds the CODE segments and forbids empty chapter components. Every rule only adds problems. Synthetic or
already-recorded metadata only. Offline: network access is blocked for the whole module."""
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
DIAGNOSTICS_SHA256 = "882d7c707961aa8a4b27b5ebb07ee03fd49f60630d4eac0fd1fe0995766f3a82"  # S7-P1-62 DIAGNOSTICS_AFTER file
TCSS, DOB = "tcss.legis.texas.gov", "www.dob.texas.gov"
PATTERN = r"^/resources/(?P<code>[A-Z]{2,3})/htm/(?P=code)\.[0-9A-Za-z]+(\.[0-9A-Za-z]+)*\.htm$"


def setUpModule():
    global _net
    _net = mock.patch.object(socket, "socket", side_effect=AssertionError("network access attempted in an offline test"))
    _net.start()


def tearDownModule():
    _net.stop()


def netloc(n):
    return f"source_url netloc {n} must be the bare lowercase host"


def noncanonical(reason):
    return f"source_url path is not canonical: {reason}"


def unmatched(path):
    return f"source_url path {path} on {TCSS} does not match the host's path_pattern {PATTERN} for source_class state_statute"


class EntryCanonicalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def found(self, host, cls, url):
        return state_sources.validate(f"state:TX:{cls}:t", entry(self.tmp, host, cls, source_url=url))

    def test_a_canonical_tcss_entry_with_a_fragment_still_validates(self):
        self.assertEqual(self.found(TCSS, "state_statute", f"https://{TCSS}/resources/FI/htm/FI.31.htm#31.002"), [])

    def test_b1_userinfo_port_and_uppercase_netlocs_are_rejected_on_tcss(self):
        path = "/resources/FI/htm/FI.31.htm"
        for n in (f"x@{TCSS}", f"{TCSS}:443", TCSS.upper()):
            with self.subTest(n):
                self.assertIn(netloc(n), self.found(TCSS, "state_statute", f"https://{n}{path}"))

    def test_b2_non_canonical_tcss_paths_are_rejected(self):
        cases = {"/resources/FI/htm/FI%2E31.htm": noncanonical("percent-encoded octet"),
                 "/resources/../FI/htm/FI.31.htm": noncanonical("dot segment"),
                 "/resources/FI/htm/GV.31.htm": unmatched("/resources/FI/htm/GV.31.htm"),
                 "/resources/FI/htm/FI..htm": unmatched("/resources/FI/htm/FI..htm")}
        for path, problem in cases.items():
            with self.subTest(path):
                self.assertIn(problem, self.found(TCSS, "state_statute", f"https://{TCSS}{path}"))

    def test_b3_the_rules_apply_on_hosts_without_path_pattern(self):
        self.assertIn(noncanonical("percent-encoded octet"),
                      self.found(DOB, "state_regulator_issuance", f"https://{DOB}/laws%2Dregulations"))
        self.assertIn(netloc(f"a@{DOB}"), self.found(DOB, "state_regulator_issuance", f"https://a@{DOB}/laws-regulations"))
        self.assertEqual(self.found(DOB, "state_regulator_issuance", f"https://{DOB}/laws-regulations"), [])

    def test_b4_backslash_and_whitespace_paths_are_rejected(self):
        self.assertIn(noncanonical("backslash"), self.found(DOB, "state_regulator_issuance", f"https://{DOB}/laws\\regulations"))
        self.assertIn(noncanonical("whitespace or control character"),
                      self.found(DOB, "state_regulator_issuance", f"https://{DOB}/laws regulations"))


class EvidenceNetlocTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.reg = state_sources.contract(), state_sources.registry()

    def test_c_evidence_final_url_with_userinfo_or_port_is_a_problem(self):
        for bad in (f"https://u@{DOB}/laws-regulations", f"https://{DOB}:443/laws-regulations", f"https://WWW.DOB.TEXAS.GOV/laws-regulations"):
            with self.subTest(bad):
                reg = copy.deepcopy(self.reg)
                reg["evidence"]["EV-TX-DOB-LAWS-001"]["final_url"] = bad
                out = state_sources.approvals("TX", REF, self.spec, reg)[DOB]
                self.assertTrue(out["approved"])  # the host record is untouched
                self.assertEqual(out["source_classes"], [])
                n = bad.split("/")[2]
                self.assertIn(f"host {DOB} / state_regulator_issuance: evidence EV-TX-DOB-LAWS-001 final_url netloc {n} "
                              "must be the bare lowercase host", out["problems"])

    def test_c2_requested_url_and_relied_on_hops_are_checked(self):
        reg = copy.deepcopy(self.reg)
        reg["evidence"]["EV-TX-DOB-LAWS-001"]["requested_url"] = f"https://{DOB}:8443/laws-regulations"
        found = state_sources.evidence_problems("EV-TX-DOB-LAWS-001", reg, DOB, self.spec["jurisdictions"]["TX"], REF, "state_regulator_issuance")
        self.assertIn(f"evidence EV-TX-DOB-LAWS-001 requested_url netloc {DOB}:8443 must be the bare lowercase host", found)
        reg = copy.deepcopy(self.reg)
        cite = reg["evidence"]["EV-TX-STAT-CITE-001"]
        cite["redirect_chain"] = [dict(cite["redirect_chain"][0], to=cite["redirect_chain"][0]["to"].replace("statutes.capitol", "u@statutes.capitol"))]
        cite["final_url"] = cite["redirect_chain"][0]["to"]
        found = state_sources.evidence_problems("EV-TX-STAT-CITE-001", reg, "statutes.capitol.texas.gov", self.spec["jurisdictions"]["TX"], REF)
        self.assertIn("evidence EV-TX-STAT-CITE-001 redirect hop 1 to netloc u@statutes.capitol.texas.gov must be the bare lowercase host", found)

    def test_c3_the_shipped_registry_gains_no_netloc_problem(self):
        jur = self.spec["jurisdictions"]["TX"]
        for eid, rec in self.reg["evidence"].items():
            found = state_sources.evidence_problems(eid, self.reg, rec["host"], jur, REF)
            self.assertFalse([p for p in found if "netloc" in p], eid)


class DiagnosticsPinTests(unittest.TestCase):
    def test_d_diagnostics_bytes_are_unchanged(self):
        raw = (json.dumps(state_sources.approval_diagnostics("TX", REF), indent=1, sort_keys=True) + "\n").encode()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), DIAGNOSTICS_SHA256)


if __name__ == "__main__":
    unittest.main()
