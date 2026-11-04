"""Tests for S7-P1-49: Texas host approvals in state_sources.json are honoured only through specific, valid, host-matching records in the
versioned evidence registry (state_source_evidence.json), cited by evidence_id. Broken or misbound evidence is a validation problem, never an
acceptance. Offline: network access is blocked for the whole module."""
import contextlib
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

ROOT = Path(__file__).resolve().parent.parent
REF = "2026-11-03T12:00:00Z"
SIX = {"comptroller.texas.gov", "statutes.capitol.texas.gov", "tcss.legis.texas.gov", "www.dob.texas.gov", "www.sos.state.tx.us", "www.sos.texas.gov"}


def setUpModule():
    global _net
    _net = mock.patch.object(socket, "socket", side_effect=AssertionError("network access attempted in an offline test"))
    _net.start()


def tearDownModule():
    _net.stop()


def problems(spec, reg, host, reference=REF):
    return state_sources.approvals("TX", reference, spec, reg)[host]["problems"]


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.reg = state_sources.contract(), state_sources.registry()

    def test_registry_is_versioned_with_a_validity_policy_and_holds_no_page_text(self):
        self.assertEqual(self.reg["version"], "p1-state-source-evidence-0.1")
        self.assertEqual(self.reg["validity_policy"]["policy_version"], "p1-state-evidence-validity-1")
        for rec in self.reg["evidence"].values():
            self.assertFalse({"text", "body", "html", "content"} & set(rec), rec["evidence_id"])
            self.assertLessEqual(len(json.dumps(rec)), 2000, rec["evidence_id"])
        self.assertFalse([k for k in json.dumps(self.reg).lower().split('"') if k in ("permission", "permission_reference", "usage_status")])

    def test_the_six_hosts_are_the_only_approved_hosts_and_every_pair_resolves_to_specific_evidence(self):
        tx = self.spec["jurisdictions"]["TX"]
        self.assertEqual(set(tx["official_hosts"]), SIX)
        result = state_sources.approvals("TX", REF)
        for host in SIX:
            self.assertTrue(result[host]["approved"], (host, result[host]["problems"]))
            bind = tx["hosts"][host]["evidence"]
            self.assertEqual(self.reg["evidence"][bind["host"]]["host"], host)
            for cls in tx["hosts"][host]["source_classes"]:
                rec = self.reg["evidence"][bind["source_classes"][cls]]
                self.assertEqual((rec["host"], rec["level"]), (host, "source_class"), (host, cls))
                self.assertIn(cls, rec["source_classes"])
                if host in ("statutes.capitol.texas.gov",  # class evidence indistinguishable from the entry page (S7-P1-59)
                            "www.sos.state.tx.us", "www.sos.texas.gov"):  # endpoint_basis index_page_only (S7-P1-60)
                    self.assertNotIn(cls, result[host]["source_classes"])
                else:
                    self.assertIn(cls, result[host]["source_classes"])
        for host in ("texas-sos.appianportalsgov.com", "texreg.sos.state.tx.us"):
            self.assertNotIn(host, tx["official_hosts"])
            self.assertIn(host, tx["unconfirmed_candidates"])

    def test_missing_evidence(self):
        spec = copy.deepcopy(self.spec)
        spec["jurisdictions"]["TX"]["hosts"]["www.dob.texas.gov"]["evidence"]["source_classes"]["state_regulator_issuance"] = "EV-DOES-NOT-EXIST"
        self.assertTrue(any("not in the registry" in p for p in problems(spec, self.reg, "www.dob.texas.gov")))
        spec["jurisdictions"]["TX"]["hosts"]["comptroller.texas.gov"]["evidence"] = {}
        out = state_sources.approvals("TX", REF, spec, self.reg)["comptroller.texas.gov"]
        self.assertFalse(out["approved"])
        self.assertTrue(any("no evidence_id is cited" in p for p in out["problems"]))

    def test_wrong_host_evidence(self):
        spec = copy.deepcopy(self.spec)
        spec["jurisdictions"]["TX"]["hosts"]["www.dob.texas.gov"]["evidence"]["host"] = "EV-TX-CPA-HOST-001"
        out = state_sources.approvals("TX", REF, spec, self.reg)["www.dob.texas.gov"]
        self.assertFalse(out["approved"])
        self.assertTrue(any("belongs to host comptroller.texas.gov" in p for p in out["problems"]))
        reg = copy.deepcopy(self.reg)
        reg["evidence"]["EV-TX-DOB-LAWS-001"]["final_url"] = "https://www.dob.example.invalid/laws-regulations"
        self.assertTrue(any("does not match its recorded host" in p for p in problems(self.spec, reg, "www.dob.texas.gov")))

    def test_vendor_or_legacy_host_evidence(self):
        spec = copy.deepcopy(self.spec)
        spec["jurisdictions"]["TX"]["hosts"]["www.sos.state.tx.us"]["evidence"]["source_classes"]["state_administrative_rule"] = "EV-TX-APPIAN-VIEWER-001"
        found = problems(spec, self.reg, "www.sos.state.tx.us")
        self.assertTrue(any("unconfirmed vendor or legacy host texas-sos.appianportalsgov.com" in p for p in found), found)
        spec["jurisdictions"]["TX"]["hosts"]["www.sos.state.tx.us"]["evidence"]["host"] = "EV-TX-TEXREG-LEGACY-001"
        self.assertFalse(state_sources.approvals("TX", REF, spec, self.reg)["www.sos.state.tx.us"]["approved"])

    def test_stale_evidence_under_the_versioned_policy(self):
        out = state_sources.approvals("TX", "2027-09-01T00:00:00Z")
        for host in SIX:
            self.assertFalse(out[host]["approved"], host)
            self.assertTrue(any("is stale" in p and "p1-state-evidence-validity-1" in p for p in out[host]["problems"]), host)
        self.assertTrue(state_sources.approvals("TX", "2027-04-01T00:00:00Z")["tcss.legis.texas.gov"]["approved"])  # within 180 days
        self.assertTrue(state_sources.approvals("TX", "2026-10-01T00:00:00Z")["tcss.legis.texas.gov"]["approved"])  # evidence after reference

    def test_malformed_evidence(self):
        cases = {"sha256": ("sha256", "abc", "malformed sha256"), "bytes": ("bytes", 0, "malformed byte count"),
                 "status": ("status", 404, "is not 200"), "final": ("final_url", "http://tcss.legis.texas.gov/x", "final_url is not https"),
                 "time": ("access_time", "yesterday", "malformed access_time"), "level": ("level", "page", "unknown level"),
                 "basis": ("publisher_basis", "guess", "not an accepted basis"), "title": ("title", "", "lacks title"),
                 "requested": ("requested_url", "http://tcss.legis.texas.gov/x", "no redirect chain ends at final_url"),
                 "id": ("evidence_id", "EV-OTHER", "different evidence_id")}
        for name, (field, value, needle) in cases.items():
            reg = copy.deepcopy(self.reg)
            reg["evidence"]["EV-TX-TCSS-FI31-001"][field] = value
            found = problems(self.spec, reg, "tcss.legis.texas.gov")
            self.assertTrue(any(needle in p for p in found), (name, found))
            self.assertFalse(state_sources.approvals("TX", REF, self.spec, reg)["tcss.legis.texas.gov"]["approved"], name)

    def test_host_and_source_class_mismatch(self):
        spec = copy.deepcopy(self.spec)
        spec["jurisdictions"]["TX"]["hosts"]["www.dob.texas.gov"]["evidence"]["source_classes"]["state_regulator_issuance"] = "EV-TX-DOB-HOST-001"
        out = state_sources.approvals("TX", REF, spec, self.reg)["www.dob.texas.gov"]
        self.assertTrue(out["approved"])  # the host itself is still evidenced
        self.assertEqual(out["source_classes"], [])
        self.assertTrue(any("host-level observation and cannot establish source class" in p for p in out["problems"]))
        reg = copy.deepcopy(self.reg)
        reg["evidence"]["EV-TX-SOS-TEXREG-001"]["source_classes"] = ["state_register_notice"]
        spec = copy.deepcopy(self.spec)
        del spec["jurisdictions"]["TX"]["hosts"]["www.sos.texas.gov"]["endpoint_basis"]  # isolate the class-narrowing rule (S7-P1-60)
        out = state_sources.approvals("TX", REF, spec, reg)["www.sos.texas.gov"]
        self.assertEqual(out["source_classes"], ["state_register_notice"])
        self.assertTrue(any("does not establish source class state_administrative_rule" in p for p in out["problems"]))
        spec = copy.deepcopy(self.spec)
        spec["jurisdictions"]["TX"]["hosts"]["comptroller.texas.gov"]["evidence"]["source_classes"]["state_statute"] = "EV-TX-CPA-RULES-001"
        self.assertTrue(any("does not list" in p for p in problems(spec, self.reg, "comptroller.texas.gov")))


class EntryValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_entries_need_evidence_backed_approval_and_keep_the_existing_rejections(self):
        rec = entry(self.tmp, "tcss.legis.texas.gov", "state_statute")
        self.assertEqual(state_sources.validate("state:TX:state_statute:t", rec), [])
        stale = entry(self.tmp, "tcss.legis.texas.gov", "state_statute")
        stale["asset"]["retrieved_at"] = "2027-12-01T00:00:00Z"  # retrieved long after the host evidence was observed
        self.assertTrue(any("not supported by valid evidence" in p for p in state_sources.validate("state:TX:state_statute:t", stale)))
        reg = state_sources.registry()
        reg["evidence"]["EV-TX-SOS-TEXREG-001"]["source_classes"] = ["state_register_notice"]
        weak = entry(self.tmp, "www.sos.texas.gov", "state_administrative_rule")
        found = state_sources.validate("state:TX:state_administrative_rule:t", weak, None, reg)
        self.assertTrue(any("not supported by valid evidence" in p for p in found), found)
        checks = {"https": ({"source_url": "http://tcss.legis.texas.gov/x"}, "must be https"),
                  "jurisdiction": ({"jurisdiction": "ZZ"}, "not registered"),
                  "class": ({"source_class": "state_blog"}, "unknown source_class"),
                  "rights": ({"rights": {"terms_url": "x"}}, "missing rights field terms_checked_at"),
                  "permitted": ({"rights": {"terms_url": "x", "terms_checked_at": "y", "usage_status": "permitted"}}, "permission_reference"),
                  "vendor": ({"source_url": "https://texas-sos.appianportalsgov.com/x"}, "not a confirmed official host")}
        for name, (over, needle) in checks.items():
            found = state_sources.validate("state:TX:state_statute:t", entry(self.tmp, "tcss.legis.texas.gov", "state_statute", **over))
            self.assertTrue(any(needle in p for p in found), (name, found))

    def test_public_access_evidence_grants_no_permission(self):
        rights = {"terms_url": "https://tcss.legis.texas.gov/t", "terms_checked_at": REF, "usage_status": "permitted"}
        found = state_sources.validate("state:TX:state_statute:t", entry(self.tmp, "tcss.legis.texas.gov", "state_statute", rights=rights))
        self.assertTrue(any("no reuse permission is inferred" in p for p in found))


class BuildBoundaryTests(unittest.TestCase):
    def build_with(self, reg=None):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        ix, _ = build_rule(tmp, "complete", "base")
        ix.conn.close()
        root = Path(tmp) / "corpus-base"
        manifest = json.loads((root / "manifest.json").read_text())
        manifest["documents"]["state:TX:state_statute:synthetic"] = entry(root, "tcss.legis.texas.gov", "state_statute")
        (root / "manifest.json").write_text(json.dumps(manifest))
        with mock.patch.object(state_sources, "registry", lambda spec_=None: reg) if reg else contextlib.nullcontext():
            conn, _ = build.build(root, str(Path(tmp) / "x.sqlite"), "2026-10-29", log=lambda *_: None)
        conn.close()
        ix = search.Index(str(Path(tmp) / "x.sqlite"))
        self.addCleanup(ix.conn.close)
        return ix

    def test_valid_and_misbound_entries_never_create_document_or_provision_rows(self):
        broken = state_sources.registry()
        broken["evidence"]["EV-TX-TCSS-FI31-001"]["host"] = "statutes.capitol.texas.gov"
        for reg, status in ((None, "registered_not_indexed"), (broken, "skipped_invalid_state_metadata")):
            ix = self.build_with(reg)
            self.assertEqual(ix.conn.execute("SELECT status FROM source_status WHERE doc_key='state:TX:state_statute:synthetic'").fetchone()[0], status)
            self.assertIsNone(ix.conn.execute("SELECT 1 FROM source_document WHERE doc_key LIKE 'state:%'").fetchone())
            self.assertEqual(ix.conn.execute("SELECT COUNT(*) FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key LIKE 'state:%'").fetchone()[0], 0)
            self.assertEqual({t["id"]: t for t in ix.topics}["state_law"]["coverage"], "not_acquired")
            out = ix.answer("state banking law requirements for Texas chartered banks", mode="hybrid", scope="current", k=10)
            self.assertNotEqual(out["status"], "evidence_found")
            self.assertEqual(out["applicability_status"], "not_assessed")
            self.assertEqual(ix.delta("12 CFR 217.10")["legal_validity"], "not_determined")
            fut = ix.answer("community bank leverage ratio", mode="hybrid", scope="future", k=5)
            self.assertTrue(all(r["doc_key"].startswith("fr:") for r in fut["results"]))  # future scope stays future instruments

    def test_frozen_expectations_are_unchanged(self):
        self.assertEqual(hashlib.sha256((ROOT / "eval/phase1/questions.json").read_bytes()).hexdigest(),
                         "ce470ba63eb97407af10477fdbf397d51a16a9f97383643f40e010570c90dc62")
        self.assertEqual(hashlib.sha256((ROOT / "eval/phase1/questions_v0.3.json").read_bytes()).hexdigest(),
                         "f28dff0a63ded40ded6c7530310aa44c088536366ce9fd9eff24d830e5aa83f8")


class EvidenceDistinctnessTests(unittest.TestCase):
    """S7-P1-59: class-level evidence must be distinguishable from the host entry page, and a relied-on redirect chain must be contiguous."""

    def setUp(self):
        self.spec, self.reg = state_sources.contract(), state_sources.registry()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_statute_class_evidence_identical_to_the_entry_page_is_not_honoured(self):
        tx = self.spec["jurisdictions"]["TX"]
        self.assertEqual(self.spec["version"], "p1-state-sources-0.6")
        self.assertEqual(tx["hosts"]["statutes.capitol.texas.gov"]["source_classes"], ["state_statute"])  # fails on evidence, not configuration
        self.assertEqual(tx["hosts"]["statutes.capitol.texas.gov"]["evidence"]["source_classes"], {"state_statute": "EV-TX-STAT-CITE-001"})
        self.assertEqual({h: m.get("endpoint_basis") for h, m in tx["hosts"].items() if m.get("endpoint_basis")},
                         {"www.sos.state.tx.us": "index_page_only", "www.sos.texas.gov": "index_page_only"})
        self.assertEqual(hashlib.sha256((ROOT / "regintel_p1/state_source_evidence.json").read_bytes()).hexdigest(),
                         "e758f717da8ebdc9584c4c63ccaa120fec31e706f6455f136c740521c8076ad5")
        out = state_sources.approvals("TX", REF)["statutes.capitol.texas.gov"]
        self.assertTrue(out["approved"])  # the host itself stays evidenced
        self.assertEqual(out["source_classes"], [])
        self.assertTrue(any("EV-TX-STAT-HOST-001" in p and "same sha256" in p and p.startswith("host statutes.capitol.texas.gov / state_statute: ")
                            for p in out["problems"]), out["problems"])

    def test_b_the_other_five_hosts_keep_their_approvals(self):
        out = state_sources.approvals("TX", REF)
        expected = {"tcss.legis.texas.gov": ["state_statute"], "www.dob.texas.gov": ["state_regulator_issuance"],
                    "comptroller.texas.gov": ["state_regulator_issuance"]}
        for host, classes in expected.items():
            self.assertEqual((out[host]["approved"], sorted(out[host]["source_classes"]), out[host]["problems"]), (True, classes, []), host)
        for host in ("www.sos.state.tx.us", "www.sos.texas.gov"):  # from v0.5 their index-page-only classes are withheld (S7-P1-60)
            self.assertEqual((out[host]["approved"], out[host]["source_classes"]), (True, []), host)

    def test_c_statute_entries_validate_only_on_the_document_server(self):
        bad = entry(self.tmp, "statutes.capitol.texas.gov", "state_statute", source_url="https://statutes.capitol.texas.gov/Docs/FI/htm/FI.31.htm")
        found = state_sources.validate("state:TX:state_statute:t", bad)
        self.assertTrue(any("not supported by valid evidence" in p for p in found), found)
        good = entry(self.tmp, "tcss.legis.texas.gov", "state_statute", source_url="https://tcss.legis.texas.gov/resources/FI/htm/FI.31.htm")
        self.assertEqual(state_sources.validate("state:TX:state_statute:t", good), [])

    def synthetic(self, same):
        host = "www.dob.texas.gov"
        reg = {k: v for k, v in copy.deepcopy(self.reg).items() if k != "evidence"}
        h, c = copy.deepcopy(self.reg["evidence"]["EV-TX-DOB-HOST-001"]), copy.deepcopy(self.reg["evidence"]["EV-TX-DOB-LAWS-001"])
        h["evidence_id"], c["evidence_id"] = "EV-SYN-HOST", "EV-SYN-CLASS"
        h["sha256"], h["bytes"], c["sha256"], c["bytes"] = "a" * 64, 1000, "b" * 64, 2000
        if same:
            c["sha256"], c["bytes"] = h["sha256"], h["bytes"]
        reg["evidence"] = {"EV-SYN-HOST": h, "EV-SYN-CLASS": c}
        spec = copy.deepcopy(self.spec)
        spec["jurisdictions"]["TX"]["hosts"][host]["evidence"] = {"host": "EV-SYN-HOST", "source_classes": {"state_regulator_issuance": "EV-SYN-CLASS"}}
        return state_sources.approvals("TX", REF, spec, reg)[host]

    def test_d_a_class_record_is_honoured_only_while_its_bytes_differ_from_the_host_record(self):
        distinct = self.synthetic(False)
        self.assertEqual((distinct["approved"], distinct["source_classes"], distinct["problems"]), (True, ["state_regulator_issuance"], []))
        same = self.synthetic(True)
        self.assertTrue(same["approved"])
        self.assertEqual(same["source_classes"], [])
        self.assertTrue(any("EV-SYN-HOST" in p and "same sha256" in p for p in same["problems"]), same["problems"])

    def test_e_a_relied_on_redirect_chain_must_start_at_requested_url_and_be_contiguous(self):
        cite = self.reg["evidence"]["EV-TX-STAT-CITE-001"]
        self.assertFalse([p for p in state_sources.evidence_problems("EV-TX-STAT-CITE-001", self.reg, "statutes.capitol.texas.gov",
                                                                     self.spec["jurisdictions"]["TX"], REF) if "redirect chain" in p])
        req, final = cite["requested_url"], cite["final_url"]
        mid = "http://www.statutes.legis.texas.gov/redirect"
        bad = {"start": [{"status": 301, "from": "http://elsewhere.invalid/x", "to": final}],
               "gap": [{"status": 301, "from": req, "to": mid}, {"status": 301, "from": "http://other.invalid/y", "to": final}]}
        for name, chain in bad.items():
            reg = copy.deepcopy(self.reg)
            reg["evidence"]["EV-TX-STAT-CITE-001"]["redirect_chain"] = chain
            found = state_sources.evidence_problems("EV-TX-STAT-CITE-001", reg, "statutes.capitol.texas.gov", self.spec["jurisdictions"]["TX"], REF)
            self.assertTrue(any("redirect chain does not start at requested_url or is not contiguous" in p for p in found), (name, found))
        reg = copy.deepcopy(self.reg)
        reg["evidence"]["EV-TX-STAT-CITE-001"]["redirect_chain"] = [{"status": 301, "from": req, "to": mid}, {"status": 301, "from": mid, "to": final}]
        found = state_sources.evidence_problems("EV-TX-STAT-CITE-001", reg, "statutes.capitol.texas.gov", self.spec["jurisdictions"]["TX"], REF)
        self.assertFalse([p for p in found if "redirect chain" in p], found)


class EndpointBasisTests(unittest.TestCase):
    """S7-P1-60: a host declaring endpoint_basis index_page_only has entry-page evidence only, which establishes no source class."""
    SOS = {"www.sos.state.tx.us": ["state_administrative_rule"], "www.sos.texas.gov": ["state_administrative_rule", "state_register_notice"]}

    def setUp(self):
        self.spec, self.reg = state_sources.contract(), state_sources.registry()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_contract_version_and_declaring_hosts(self):
        self.assertEqual(self.spec["version"], "p1-state-sources-0.6")  # v0.5 introduced the rule; v0.6 keeps it (S7-P1-61)
        self.assertEqual(self.spec["endpoint_bases"], ["index_page_only"])
        hosts = self.spec["jurisdictions"]["TX"]["hosts"]
        self.assertEqual({h for h, m in hosts.items() if m.get("endpoint_basis") == "index_page_only"}, set(self.SOS))
        self.assertEqual({h for h, m in hosts.items() if "endpoint_basis" in m}, set(self.SOS))

    def test_b_index_page_only_hosts_stay_approved_without_classes(self):
        out = state_sources.approvals("TX", REF)
        for host, classes in self.SOS.items():
            self.assertEqual((out[host]["approved"], out[host]["source_classes"]), (True, []), host)
            for cls in classes:
                self.assertEqual(out[host]["problems"].count(f"host {host} / {cls}: host {host} declares endpoint_basis index_page_only: "
                                                             f"entry-page evidence does not establish source class {cls}"), 1, (host, cls))
            self.assertEqual(len(out[host]["problems"]), len(classes), out[host]["problems"])
            self.assertTrue(all(p.startswith(f"host {host} / ") and "endpoint_basis index_page_only" in p for p in out[host]["problems"]))

    def test_c_the_other_hosts_are_unchanged(self):
        out = state_sources.approvals("TX", REF)
        for host, classes in {"tcss.legis.texas.gov": ["state_statute"], "www.dob.texas.gov": ["state_regulator_issuance"],
                              "comptroller.texas.gov": ["state_regulator_issuance"]}.items():
            self.assertEqual(out[host], {"approved": True, "source_classes": classes, "problems": []}, host)
        cap = out["statutes.capitol.texas.gov"]
        self.assertEqual((cap["approved"], cap["source_classes"], len(cap["problems"])), (True, [], 1))
        self.assertIn("same sha256 and byte count as host-level record EV-TX-STAT-HOST-001", cap["problems"][0])
        self.assertEqual(self.spec["jurisdictions"]["TX"]["hosts"]["statutes.capitol.texas.gov"]["evidence"]["source_classes"],
                         {"state_statute": "EV-TX-STAT-CITE-001"})

    def test_d_synthetic_flag_on_a_class_approved_host(self):
        host = "www.dob.texas.gov"
        spec = copy.deepcopy(self.spec)
        meta = spec["jurisdictions"]["TX"]["hosts"][host]
        meta["endpoint_basis"] = "index_page_only"
        out = state_sources.approvals("TX", REF, spec, self.reg)[host]
        self.assertEqual((out["approved"], out["source_classes"]), (True, []))
        self.assertEqual(out["problems"], [f"host {host} / state_regulator_issuance: host {host} declares endpoint_basis index_page_only: "
                                           "entry-page evidence does not establish source class state_regulator_issuance"])
        del meta["endpoint_basis"]
        self.assertEqual(state_sources.approvals("TX", REF, spec, self.reg)[host],
                         {"approved": True, "source_classes": ["state_regulator_issuance"], "problems": []})
        meta["endpoint_basis"] = "bogus"
        out = state_sources.approvals("TX", REF, spec, self.reg)[host]
        self.assertFalse(out["approved"])
        self.assertEqual(out["source_classes"], [])
        self.assertTrue(any(p.startswith(f"host {host}: ") and "bogus" in p for p in out["problems"]), out["problems"])

    def test_d2_both_problems_appear_when_both_apply(self):
        spec = copy.deepcopy(self.spec)
        spec["jurisdictions"]["TX"]["hosts"]["statutes.capitol.texas.gov"]["endpoint_basis"] = "index_page_only"
        found = state_sources.approvals("TX", REF, spec, self.reg)["statutes.capitol.texas.gov"]["problems"]
        self.assertTrue(any("endpoint_basis index_page_only" in p for p in found), found)
        self.assertTrue(any("same sha256" in p for p in found), found)

    def test_e_validation_of_entries(self):
        bad = entry(self.tmp, "www.sos.state.tx.us", "state_administrative_rule")
        found = state_sources.validate("state:TX:state_administrative_rule:t", bad)
        self.assertTrue(any("not supported by valid evidence" in p for p in found), found)
        self.assertEqual(state_sources.validate("state:TX:state_statute:t", entry(self.tmp, "tcss.legis.texas.gov", "state_statute")), [])

    def test_f_redirect_chain_is_optional_whether_absent_or_null(self):
        for absent in (True, False):
            reg = copy.deepcopy(self.reg)
            if absent:
                del reg["evidence"]["EV-TX-TCSS-FI31-001"]["redirect_chain"]
            else:
                reg["evidence"]["EV-TX-TCSS-FI31-001"]["redirect_chain"] = None
            out = state_sources.approvals("TX", REF, self.spec, reg)["tcss.legis.texas.gov"]
            self.assertEqual((out["approved"], out["source_classes"], out["problems"]), (True, ["state_statute"], []), absent)


if __name__ == "__main__":
    unittest.main()
