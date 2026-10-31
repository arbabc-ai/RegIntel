"""Regression tests for S7-P1-MIXED-SOURCE-COVERAGE-06 (finding F1): jurisdiction and accounting-intent requests are
detected independently of acquired-topic matches and always expose a not_acquired gap."""
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from test_p1 import ECFR_HTML, asset, complete_baseline
from test_p1_completeness import FR_EXPLICIT

ROOT = Path(__file__).resolve().parent.parent / "regintel_p1"
REGISTER = Path("/mnt/c/Users/arbab/OneDrive/Documents/AI-OS/projects/velodyn/Velodyn_BankOS/ChatGPT/"
                "2026-10-24_204204_EDT_Codex_Three_Sessions/C3-RegIntel-RAG/2026-10-26_PHASE1_US_RULES/"
                "STATE_JURISDICTION_REGISTER.csv")
MODES = ("exact", "hybrid")
SCOPES = ("current", "future", "both")
MIXED = {
    "Texas common equity tier 1 capital": ("state_law", "jurisdiction: Texas"),
    "New York CET1": ("state_law", "jurisdiction: New York"),
    "CET1 capital under Puerto Rico banking law": ("state_law", "jurisdiction: Puerto Rico"),
    "How should a bank recognize expected loan losses and calculate CET1?": ("gaap_asc", "accounting_intent"),
}


class CatalogTests(unittest.TestCase):
    def test_jurisdiction_catalog_has_register_provenance_and_asserts_nothing(self):
        cat = json.loads((ROOT / "jurisdictions.json").read_text())
        self.assertEqual(len(cat["jurisdictions"]), 56)
        self.assertEqual({j["type"] for j in cat["jurisdictions"]}, {"state", "territory", "district"})
        self.assertEqual({j["register_status"] for j in cat["jurisdictions"]}, {"official-endpoints-and-scope-unresolved"})
        self.assertEqual({j["register_coverage"] for j in cat["jurisdictions"]}, {"not-assessed"})
        self.assertIn("asserts no state-law", cat["note"])
        ids = {j["id"] for j in cat["jurisdictions"]}
        self.assertTrue({"TX", "NY", "PR", "DC", "GU", "AS", "MP", "VI"} <= ids)
        self.assertEqual(len(cat["source"]["sha256"]), 64)

    @unittest.skipUnless(REGISTER.exists(), "C3 register not mounted")
    def test_catalog_matches_the_read_only_register_exactly(self):
        raw = REGISTER.read_bytes()
        cat = json.loads((ROOT / "jurisdictions.json").read_text())
        self.assertEqual(cat["source"]["sha256"], hashlib.sha256(raw).hexdigest())
        rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
        self.assertEqual([(r["id"], r["name"]) for r in rows], [(j["id"], j["name"]) for j in cat["jurisdictions"]])

    def test_source_request_config_declares_caveats(self):
        cfg = json.loads((ROOT / "source_requests.json").read_text())
        self.assertEqual(cfg["jurisdiction"]["topic"], "state_law")
        self.assertEqual(cfg["accounting_intent"]["topic"], "gaap_asc")
        self.assertIn("never asserts applicability", cfg["note"])


class MixedSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        docs = {
            "ecfr:12:3": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 3", "title": "Capital",
                          "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                          "asset": asset(root, "e.html", ECFR_HTML.encode())},
            "fr:2026-70012": {**meta, "document_number": "2026-70012", "title": "Explicit", "document_type": "Rule",
                              "effective_on": "2027-02-01", "action": "Final rule.", "asset": asset(root, "x.xml", FR_EXPLICIT)},
        }
        complete_baseline(root, docs)  # the declared eCFR baseline dependencies of the acquired topics
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "m.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def gap(self, out, topic):
        return next((g for g in out["coverage_gaps"] if g["topic"] == topic), None)

    def check_invariants(self, out, scope, parts=None):
        self.assertEqual(out["applicability_status"], "not_assessed")
        for r in out["results"]:
            if scope == "future":
                self.assertTrue(r["kind"] in ("amendment", "fr_preamble", "fr_table")
                                and not r["status"]["state"].startswith("current"), r["citation"])
            if scope == "current":
                self.assertTrue(r["doc_key"].startswith("ecfr:") and r["status"]["state"].startswith("current_baseline"))
            if parts:
                row = self.index.conn.execute("SELECT title_no, part_no FROM provision WHERE id=?", (r["provision_id"],)).fetchone()
                self.assertIn(f"{row['title_no']}:{row['part_no']}", parts)

    # ---- F1: acquired CET1 no longer hides a named jurisdiction or accounting intent
    def test_mixed_requests_expose_the_not_acquired_gap_in_every_mode_scope_and_k(self):
        for q, (topic, detected) in MIXED.items():
            for mode in MODES:
                for scope in SCOPES:
                    for k in (8, 1):
                        out = self.index.answer(q, mode=mode, scope=scope, k=k)
                        g = self.gap(out, topic)
                        self.assertIsNotNone(g, (q, mode, scope, k))
                        self.assertEqual(g["coverage"], "not_acquired")
                        self.assertTrue(any(d.startswith(detected) for d in g["detected_by"]), g["detected_by"])
                        self.assertNotEqual(out["status"], "evidence_found", (q, mode, scope, k))
                        self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence",
                                         (q, mode, scope, k))
                        self.assertIn("cet1_capital", out["topics_matched"])  # the acquired topic still matches
                        self.check_invariants(out, scope)

    def test_useful_federal_results_are_preserved(self):
        for q in MIXED:
            out = self.index.answer(q, mode="hybrid", scope="current", k=8)
            self.assertEqual(out["status"], "partial_evidence", q)
            self.assertTrue(out["results"], q)
            self.assertEqual(len(self.index.answer(q, mode="hybrid", scope="current", k=1)["results"]), 1)

    def test_excluding_part_filter_is_missing_evidence_with_the_gap_kept(self):
        for q, (topic, _) in MIXED.items():
            for mode in MODES:
                for scope in SCOPES:
                    out = self.index.answer(q, mode=mode, scope=scope, parts=["12:217"])
                    self.assertEqual(out["results"], [], (q, mode, scope))
                    self.assertEqual(out["status"], "missing_evidence")
                    self.assertEqual(self.gap(out, topic)["coverage"], "not_acquired")
                    self.check_invariants(out, scope, ["12:217"])

    # ---- naming a jurisdiction asserts nothing
    def test_jurisdiction_record_carries_provenance_and_no_legal_conclusion(self):
        out = self.index.answer("Texas common equity tier 1 capital")
        s = next(x for x in out["source_requests"] if x["kind"] == "jurisdiction")
        self.assertEqual((s["id"], s["name"], s["type"], s["topic"]), ("TX", "Texas", "state", "state_law"))
        self.assertEqual(s["register"]["coverage"], "not-assessed")
        self.assertEqual(len(s["register"]["sha256"]), 64)
        self.assertIn("does not assert state-law applicability, preemption or compliance", s["note"])
        blob = json.dumps({"gaps": out["coverage_gaps"], "sources": out["source_requests"]}).lower()
        for banned in ('"applies"', "preempted by", '"compliant"', "does_not_apply", "is subject to"):
            self.assertNotIn(banned, blob)
        self.assertEqual(out["applicability_status"], "not_assessed")

    def test_territory_and_district_names_are_recognised_and_longest_name_wins(self):
        def names(q):
            return [x["name"] for x in self.index.detect_sources(q) if x["kind"] == "jurisdiction"]
        self.assertEqual(names("CET1 capital under Puerto Rico banking law"), ["Puerto Rico"])
        self.assertEqual(names("West Virginia CET1"), ["West Virginia"])  # not also Virginia
        self.assertEqual(names("Washington, D.C. CET1"), ["District of Columbia"])
        self.assertEqual(names("CET1 in Guam and Virginia"), ["Guam", "Virginia"])
        self.assertEqual(names("New York CET1 and New Jersey CET1"), ["New York", "New Jersey"])

    def test_federal_bank_names_are_not_jurisdiction_requests(self):
        for q in ("Federal Reserve Bank of New York CET1", "Federal Home Loan Bank of Texas CET1", "New York Fed CET1"):
            self.assertEqual([x for x in self.index.detect_sources(q) if x["kind"] == "jurisdiction"], [], q)
            out = self.index.answer(q, mode="hybrid", scope="current")
            self.assertIsNone(self.gap(out, "state_law"), q)
            self.assertEqual(out["status"], "evidence_found", q)

    # ---- accounting intent
    def test_accounting_intent_is_detected_without_swallowing_capital_questions(self):
        yes = ("How should a bank recognize expected loan losses?", "measurement of the allowance for credit losses",
               "accounting for impairment of securities")
        for q in yes:
            self.assertTrue([x for x in self.index.detect_sources(q) if x["kind"] == "accounting_intent"], q)
        for q in ("minimum common equity tier 1 capital ratio", "recognize tier 1 capital instruments",
                  "12 CFR 3.22", "capital conservation buffer payout"):
            self.assertEqual([x for x in self.index.detect_sources(q) if x["kind"] == "accounting_intent"], [], q)

    # ---- both source gaps together, plus other protections
    def test_jurisdiction_and_accounting_gaps_are_both_reported(self):
        out = self.index.answer("Texas: how should a bank recognize expected loan losses and calculate CET1?")
        self.assertEqual({g["topic"] for g in out["coverage_gaps"]} & {"state_law", "gaap_asc"}, {"state_law", "gaap_asc"})
        self.assertEqual(out["status"], "partial_evidence")

    def test_unresolved_citation_gap_coexists_with_source_gaps(self):
        out = self.index.answer("CET1 capital 12 CFR 999.999 in Texas", mode="hybrid", scope="current")
        topics = {g["topic"] for g in out["coverage_gaps"]}
        self.assertTrue({"state_law", "citation:12 CFR 999.999"} <= topics)
        self.assertEqual(out["status"], "partial_evidence")

    def test_positive_controls_stay_evidence_found(self):
        for q, scope in (("minimum common equity tier 1 capital ratio", "current"), ("12 CFR 3.1", "current"),
                         ("12 CFR 3.1", "future"), ("12 CFR 3.1(b)(1)", "current")):
            for mode in MODES:
                if mode == "exact" and "CFR" not in q:
                    continue
                out = self.index.answer(q, mode=mode, scope=scope)
                self.assertEqual(out["status"], "evidence_found", (q, mode, scope))
                self.assertEqual(out["source_requests"], [])
                self.assertEqual(out["coverage_gaps"], [], (q, mode, scope))

    def test_existing_asc_and_liquidity_protections_unchanged(self):
        self.assertEqual(self.index.answer("CECL under ASC 326")["status"], "missing_evidence")
        asc = self.index.answer("CECL under ASC 326")
        self.assertEqual(len([g for g in asc["coverage_gaps"] if g["topic"] == "gaap_asc"]), 1)  # deduped with the synonym match
        lcr = self.index.answer("liquidity coverage ratio requirements for a national bank")
        self.assertIn(("liquidity_lcr", "partial"), {(g["topic"], g["coverage"]) for g in lcr["coverage_gaps"]})
        self.assertNotEqual(lcr["status"], "evidence_found")
        tx = self.index.answer("Texas minimum capital")
        self.assertEqual(tx["status"], "partial_evidence")
        self.assertIn("none_matched", {g["topic"] for g in tx["coverage_gaps"]})
        self.assertIn("state_law", {g["topic"] for g in tx["coverage_gaps"]})


if __name__ == "__main__":
    unittest.main()
