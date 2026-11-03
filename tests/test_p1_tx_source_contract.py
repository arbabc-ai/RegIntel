"""Tests for S7-P1-48: the Texas host list in regintel_p1/state_sources.json is backed by recorded public-page metadata
(tests/fixtures/tx_source_verification_48.json, observed 2026-11-03, no page text), each host is limited to the source classes it serves, the
vendor-hosted and legacy viewers stay unconfirmed, and no reuse permission is inferred. Offline: no test touches the network."""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

from regintel_p1 import build, search, state_sources
from test_p1_rulemaking_sources import build_rule

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((Path(__file__).resolve().parent / "fixtures/tx_source_verification_48.json").read_text())
TX = state_sources.contract()["jurisdictions"]["TX"]


def entry(root, host, cls, **over):
    body = b"<html><body><p>SYNTHETIC bytes for a contract test; not official text.</p></body></html>"
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "tx.html").write_bytes(body)
    rec = {"source_class": cls, "jurisdiction": "TX", "publisher": "synthetic", "source_url": f"https://{host}/synthetic",
           "legal_entity_scope": "synthetic", "locator": "synthetic",
           "rights": {"terms_url": f"https://{host}/terms", "terms_checked_at": "2026-11-03T00:00:00Z", "usage_status": "pending_review"},
           "acquisition_authorization": "synthetic test fixture",
           "asset": {"path": "raw/tx.html", "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "retrieved_at": "2026-11-03T00:00:00Z"}}
    rec.update(over)
    return rec


class EvidenceBackedContractTests(unittest.TestCase):
    def test_every_listed_host_has_documented_publisher_and_a_200_https_observation(self):
        self.assertTrue(TX["official_hosts"])
        for host in TX["official_hosts"]:
            meta = TX["hosts"][host]
            for field in ("publisher", "source_classes", "locator_form", "verified_at", "evidence"):
                self.assertTrue(meta.get(field), (host, field))
            seen = [p for p in FIXTURE["pages"] if urlparse(p["final_url"] or "").hostname == host]
            self.assertTrue(seen, host)
            for p in seen:
                self.assertEqual(urlparse(p["final_url"]).scheme, "https", host)
                self.assertEqual(p["status"], 200, host)
                self.assertTrue(p["access_time"] and p["title"] and len(p["sha256"]) == 64, host)

    def test_a_texas_legislative_council_statute_link_resolves_to_a_listed_statutes_host(self):
        chained = [p for p in FIXTURE["pages"] if (p["redirect_chain"] or []) and "statutes.legis.texas.gov" in p["requested_url"]]
        self.assertTrue(chained)
        self.assertEqual(chained[0]["redirect_chain"][0]["status"], 301)
        self.assertEqual(urlparse(chained[0]["final_url"]).hostname, "statutes.capitol.texas.gov")
        self.assertIn("statutes.capitol.texas.gov", TX["official_hosts"])
        chapter = [p for p in FIXTURE["pages"] if p["final_url"] == "https://tcss.legis.texas.gov/resources/FI/htm/FI.31.htm"]
        self.assertEqual(chapter[0]["title"], "FINANCE CODE CHAPTER 31. GENERAL PROVISIONS")

    def test_administrative_code_publishers_are_separated_by_domain_and_the_vendor_viewer_is_not_confirmed(self):
        self.assertEqual(TX["hosts"]["www.sos.state.tx.us"]["source_classes"], ["state_administrative_rule"])
        self.assertIn("state_register_notice", TX["hosts"]["www.sos.texas.gov"]["source_classes"])
        for host in ("texas-sos.appianportalsgov.com", "texreg.sos.state.tx.us"):
            self.assertNotIn(host, TX["official_hosts"])
            self.assertIn(host, TX["unconfirmed_candidates"])


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def check(self, host, cls, **over):
        return state_sources.validate("state:TX:%s:t" % cls, entry(self.tmp, host, cls, **over))

    def test_classes_are_limited_per_host(self):
        self.assertEqual(self.check("tcss.legis.texas.gov", "state_statute"), [])
        # www.sos.texas.gov declares endpoint_basis index_page_only, so it approves no class (S7-P1-60)
        self.assertTrue(any("not supported by valid evidence" in p for p in self.check("www.sos.texas.gov", "state_register_notice")))
        self.assertEqual(self.check("www.dob.texas.gov", "state_regulator_issuance"), [])
        self.assertTrue(any("is not served by" in p for p in self.check("www.dob.texas.gov", "state_statute")))
        self.assertTrue(any("is not served by" in p for p in self.check("tcss.legis.texas.gov", "state_administrative_rule")))

    def test_unconfirmed_hosts_are_rejected(self):
        for host in ("texas-sos.appianportalsgov.com", "texreg.sos.state.tx.us", "tlc.texas.gov", "statutes.example.invalid"):
            self.assertTrue(any("not a confirmed official host" in p for p in self.check(host, "state_statute")), host)

    def test_no_reuse_permission_is_inferred(self):
        rights = {"terms_url": "https://tcss.legis.texas.gov/t", "terms_checked_at": "2026-11-03T00:00:00Z", "usage_status": "permitted"}
        self.assertTrue(any("permission_reference" in p for p in self.check("tcss.legis.texas.gov", "state_statute", rights=rights)))
        self.assertEqual(self.check("tcss.legis.texas.gov", "state_statute", rights={**rights, "permission_reference": "synthetic decision"}), [])


class BuildBoundaryTests(unittest.TestCase):
    def test_a_valid_texas_entry_is_registered_but_not_indexed_and_state_law_stays_not_acquired(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        ix, _ = build_rule(tmp, "complete", "base")
        ix.conn.close()
        root = Path(tmp) / "corpus-base"
        manifest = json.loads((root / "manifest.json").read_text())
        key = "state:TX:state_statute:synthetic"
        manifest["documents"][key] = entry(root, "tcss.legis.texas.gov", "state_statute")
        (root / "manifest.json").write_text(json.dumps(manifest))
        conn, counts = build.build(root, str(Path(tmp) / "x.sqlite"), "2026-10-29", log=lambda *_: None)
        conn.close()
        ix = search.Index(str(Path(tmp) / "x.sqlite"))
        try:
            self.assertEqual(ix.conn.execute("SELECT status FROM source_status WHERE doc_key=?", (key,)).fetchone()[0], "registered_not_indexed")
            self.assertEqual(ix.answer("SYNTHETIC bytes for a contract test", mode="bm25", scope="both", k=5)["results"], [])
            topics = {t["id"]: t for t in ix.topics}
            self.assertEqual(topics["state_law"]["coverage"], "not_acquired")
            out = ix.answer("state banking law requirements for Texas chartered banks", mode="hybrid", scope="current", k=10)
            self.assertNotEqual(out["status"], "evidence_found")
            self.assertEqual(out["applicability_status"], "not_assessed")
            self.assertEqual(ix.delta("12 CFR 217.10")["legal_validity"], "not_determined")
        finally:
            ix.conn.close()

    def test_frozen_v02_is_unchanged_and_v03_stays_the_default(self):
        raw = (ROOT / "eval/phase1/questions.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "ce470ba63eb97407af10477fdbf397d51a16a9f97383643f40e010570c90dc62")
        self.assertIn("questions_v0.3.json", (ROOT / "scripts/p1.py").read_text())


if __name__ == "__main__":
    unittest.main()
