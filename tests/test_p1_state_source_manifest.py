"""Tests for S7-P1-47: manifest ingestion metadata for official state sources. A state entry is hash-verified, validated against
regintel_p1/state_sources.json and recorded as registered_not_indexed (or skipped_invalid_state_metadata); its text never enters the index,
and no state-law coverage is claimed. All data here is synthetic; nothing is acquired."""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import build, search, state_sources
from test_p1_rulemaking_sources import build_rule

KEY = "state:TX:state_statute:synthetic-charter-powers"
HOST = "statutes.example.invalid"  # a synthetic host; real hosts are added only after confirmation


def entry(root, **over):
    body = b"<html><body><p>SYNTHETIC test bytes standing in for an official state source.</p></body></html>"
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "tx.html").write_bytes(body)
    rec = {"source_class": "state_statute", "jurisdiction": "TX", "publisher": "Synthetic Texas publisher",
           "source_url": f"https://{HOST}/synthetic", "legal_entity_scope": "Texas state-chartered banks (synthetic)",
           "locator": "synthetic § 1.001", "publication_date": "2026-01-01", "effective_date": "2026-01-01",
           "rights": {"terms_url": f"https://{HOST}/terms", "terms_checked_at": "2026-11-02T00:00:00Z", "usage_status": "pending_review"},
           "acquisition_authorization": "synthetic test fixture (no real authorisation)",
           "asset": {"path": "raw/tx.html", "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "retrieved_at": "2026-11-02T00:00:00Z"}}
    rec.update(over)
    return rec


def confirmed(hosts):
    """The shipped contract with synthetic hosts approved through synthetic evidence (task 49: approvals must cite evidence)."""
    spec = state_sources.contract()
    tx = spec["jurisdictions"]["TX"]
    tx["official_hosts"] = list(hosts)
    for h in hosts:
        tx.setdefault("hosts", {})[h] = {"publisher": "synthetic", "source_classes": ["state_statute"],
                                          "evidence": {"host": "EV-SYN-" + h, "source_classes": {"state_statute": "EV-SYN-" + h}}}
    return spec


def synthetic_registry(hosts):
    reg = state_sources.registry()
    for h in hosts:
        reg["evidence"]["EV-SYN-" + h] = {"evidence_id": "EV-SYN-" + h, "level": "source_class", "host": h, "requested_url": f"https://{h}/",
                                          "final_url": f"https://{h}/", "redirect_chain": [], "status": 200, "access_time": "2026-11-02T00:00:00Z",
                                          "sha256": "0" * 64, "bytes": 1, "title": "synthetic", "publisher": "synthetic", "publisher_basis": "title",
                                          "publisher_basis_detail": "synthetic test evidence", "role": "synthetic", "source_classes": ["state_statute"]}
    return reg


class ContractTests(unittest.TestCase):
    def test_shipped_contract_does_not_confirm_the_synthetic_test_host(self):
        # S7-P1-48 documented real Texas hosts; the synthetic test host must never be among them.
        spec = state_sources.contract()
        self.assertNotIn(HOST, spec["jurisdictions"]["TX"]["official_hosts"])
        self.assertIn("state_statute", spec["source_classes"])

    def test_valid_entry_passes_only_with_a_confirmed_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = entry(Path(tmp))
            self.assertEqual(state_sources.validate(KEY, rec, confirmed([HOST]), synthetic_registry([HOST])), [])
            self.assertTrue(any("not a confirmed official host" in p for p in state_sources.validate(KEY, rec)))

    def test_missing_or_bad_metadata_is_reported_field_by_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec, reg = confirmed([HOST]), synthetic_registry([HOST])
            cases = {"rights": ({"rights": {"terms_url": "x"}}, "missing rights field terms_checked_at"),
                     "class": ({"source_class": "state_blog"}, "unknown source_class"),
                     "scheme": ({"source_url": f"http://{HOST}/x"}, "must be https"),
                     "jurisdiction": ({"jurisdiction": "ZZ"}, "not registered"),
                     "authorisation": ({"acquisition_authorization": ""}, "missing field acquisition_authorization"),
                     "locator": ({"locator": ""}, "missing field locator"),
                     "usage": ({"rights": {"terms_url": "x", "terms_checked_at": "y", "usage_status": "free"}}, "unknown rights usage_status")}
            for name, (over, needle) in cases.items():
                problems = state_sources.validate(KEY, entry(Path(tmp), **over), spec, reg)
                self.assertTrue(any(needle in p for p in problems), (name, problems))
            self.assertTrue(any("manifest key" in p for p in state_sources.validate("tx:statute", entry(Path(tmp)), spec, reg)))


class BuildTests(unittest.TestCase):
    def build_with(self, rec, spec=None, tamper=False):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        ix, _ = build_rule(tmp, "complete", "base")  # a normal synthetic corpus
        root = Path(tmp) / "corpus-base"
        manifest = json.loads((root / "manifest.json").read_text())
        rec = entry(root, **rec)
        if tamper:
            rec["asset"]["sha256"] = "0" * 64
        manifest["documents"][KEY] = rec
        (root / "manifest.json").write_text(json.dumps(manifest))
        ix.conn.close()
        db = str(Path(tmp) / "with_state.sqlite")
        shipped = state_sources.json.loads(state_sources.CONTRACT_PATH.read_text())
        reg = synthetic_registry([HOST]) if spec else state_sources.registry()
        with mock.patch.object(state_sources, "contract", lambda: spec or shipped), \
                mock.patch.object(state_sources, "registry", lambda spec_=None: reg):
            conn, counts = build.build(root, db, "2026-10-29", log=lambda *_: None)
        conn.close()
        return search.Index(db), counts

    def status(self, ix):
        return ix.conn.execute("SELECT status, reason, provisions FROM source_status WHERE doc_key=?", (KEY,)).fetchone()

    def test_valid_entry_is_registered_not_indexed_and_adds_no_text(self):
        ix, counts = self.build_with({}, confirmed([HOST]))
        st = self.status(ix)
        self.assertEqual((st["status"], st["provisions"]), ("registered_not_indexed", 0))
        self.assertEqual(counts["state_sources"], [{"doc_key": KEY, "status": "registered_not_indexed"}])
        self.assertIsNone(ix.conn.execute("SELECT 1 FROM source_document WHERE doc_key=?", (KEY,)).fetchone())
        self.assertEqual(ix.answer("SYNTHETIC test bytes official state source", mode="bm25", scope="both", k=5)["results"], [])
        out = ix.answer("state banking law requirements for Texas chartered banks", mode="hybrid", scope="current", k=10)
        self.assertIn("state_law", [g["topic"] for g in out["coverage_gaps"]])  # still not acquired
        self.assertNotEqual(out["status"], "evidence_found")
        ix.conn.close()

    def test_unconfirmed_host_invalid_metadata_and_bad_bytes_are_skipped_with_reasons(self):
        ix, counts = self.build_with({})  # the synthetic host is not among the confirmed Texas hosts in the shipped contract
        st = self.status(ix)
        self.assertEqual(st["status"], "skipped_invalid_state_metadata")
        self.assertIn("not a confirmed official host", st["reason"])
        ix.conn.close()
        ix, _ = self.build_with({"rights": {}}, confirmed([HOST]))
        self.assertIn("missing field rights", self.status(ix)["reason"])
        ix.conn.close()
        ix, _ = self.build_with({}, confirmed([HOST]), tamper=True)
        self.assertEqual(self.status(ix)["status"], "skipped_hash_or_size_mismatch")
        ix.conn.close()

    def test_federal_evidence_and_dependency_verification_are_unchanged(self):
        ix, counts = self.build_with({}, confirmed([HOST]))
        self.assertEqual(ix.dependency_verification["status"], "verified")
        self.assertEqual(counts["unavailable_topic_sources"], 0)
        out = ix.answer("community bank leverage ratio", mode="hybrid", scope="current", k=5)
        self.assertEqual(out["status"], "evidence_found")
        self.assertTrue(all(r["doc_key"].startswith("ecfr:") for r in out["results"]))
        ix.conn.close()


if __name__ == "__main__":
    unittest.main()
