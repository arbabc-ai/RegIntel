"""Tests for S7-P1-73: the DR-5 Treasury post-filter on Federal Register discovery (founder record
2026-11-04_s7-regintel-p1-founder-decisions_accepted.md, item DR-5: FinCEN/OFAC rulemaking is discovered through the observed
`treasury-department` agency slug and filtered afterwards; kept entries carry the DR-2/DR-6 rights record), plus the S7-P1-72
overlay fixes. FakeClient only; offline: network access is blocked for the whole module."""
import copy
import hashlib
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import federal_rights
from scripts import corpus_download as c
from scripts import p1_manifest_rights_check as cli

FINCEN, OFAC, OTHER = "2026-30001", "2026-30002", "2026-30003"
DETAILS = {
    FINCEN: {"cfr_references": [{"title": 31, "part": 1010}], "agencies": [{"slug": "treasury-department"},
                                                                        {"slug": "financial-crimes-enforcement-network"}]},
    OFAC: {"cfr_references": [{"title": 31, "part": 589}], "agencies": [{"slug": "treasury-department"},
                                                                     {"slug": "foreign-assets-control-office"}]},
    OTHER: {"cfr_references": [{"title": 31, "part": 1}], "agencies": [{"slug": "treasury-department"}]},
}


def setUpModule():
    global _net
    _net = mock.patch.object(socket, "socket", side_effect=AssertionError("network access attempted in an offline test"))
    _net.start()


def tearDownModule():
    _net.stop()


class TreasuryClient:
    """A Federal Register stand-in that records every URL requested (listing, detail and asset)."""

    def __init__(self, details=DETAILS):
        self.details, self.urls, self.requests = details, [], []

    def asset_url(self, number):
        return f"https://www.govinfo.gov/{number}.xml"

    def get(self, url, headers=None):
        self.urls.append(url)
        number = url.rsplit("/", 1)[-1].removesuffix(".xml")
        return c.Response(f"<RULE><PREAMB>{number}</PREAMB></RULE>".encode(), url, {}, c.now())

    def get_json(self, url):
        self.urls.append(url)
        if "documents.json?" in url:
            value = {"count": len(self.details), "results": [{"document_number": n} for n in self.details], "next_page_url": None}
        else:
            number = url.rsplit("/", 1)[-1].removesuffix(".json")
            value = {"document_number": number, "type": "Rule", "title": f"Doc {number}", "full_text_xml_url": self.asset_url(number),
                     **self.details[number]}
        return value, c.Response(json.dumps(value).encode(), url, {}, c.now())


def fr_args(*extra):
    return c.parser().parse_args(["--max-documents", "10", "federal-register", "--agency", "treasury-department",
                                  "--since", "2026-01-01", "--until", "2026-10-29", *extra])


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.corpus = c.Corpus(Path(self.tmp.name))

    def test_1_without_flags_nothing_is_filtered_and_the_run_is_unchanged(self):
        args = fr_args()
        self.assertFalse(hasattr(args, "keep_cfr") or hasattr(args, "keep_agency_slug"))  # selection recorded exactly as before
        log, client = {}, TreasuryClient()
        counts = c.download_fr(args, client, self.corpus, log)
        self.assertEqual(counts, {"discovered": 3, "attempted": 3, "downloaded": 3, "reused": 0, "failed": 0, "limited": False})
        self.assertEqual(log, {})
        self.assertEqual(sorted(self.corpus.manifest["documents"]), [f"fr:{n}" for n in sorted(DETAILS)])
        with mock.patch.object(c, "Client", lambda delay: TreasuryClient()), mock.patch("builtins.print"):
            out = Path(self.tmp.name) / "run"
            self.assertEqual(c.main(["--output", str(out), "--max-documents", "10", "federal-register", "--agency",
                                     "treasury-department", "--since", "2026-01-01", "--until", "2026-10-29"]), 0)
        run = json.loads(next((out / "runs").glob("*.json")).read_text())
        self.assertEqual(set(run), {"run_id", "started_at", "command", "selection", "counts", "finished_at", "requests"})
        self.assertNotIn("keep_cfr", run["selection"])

    def test_2_only_the_fincen_range_is_kept_and_filtered_documents_cost_no_asset(self):
        log, client = {}, TreasuryClient()
        counts = c.download_fr(fr_args("--keep-cfr", "31:1000-1099"), client, self.corpus, log)
        self.assertEqual(sorted(self.corpus.manifest["documents"]), [f"fr:{FINCEN}"])
        self.assertEqual((counts["attempted"], counts["downloaded"], counts["filtered_out"]), (3, 1, 2))  # attempted = all seen
        self.assertEqual(sorted(f["document_number"] for f in log["filtered_out"]), [OFAC, OTHER])
        for f in log["filtered_out"]:
            self.assertIn("no cfr_reference in a kept range", f["reason"])
            self.assertEqual(f["agency_slugs"][-1], "treasury-department")
        self.assertEqual(log["filtered_out"][0]["cfr_references"], [{"title": 31, "part": 589}])
        requested = set(client.urls)
        for number in (OFAC, OTHER):
            self.assertNotIn(client.asset_url(number), requested)
            self.assertIn(f"{c.FR}/api/v1/documents/{number}.json", requested)  # one metadata request each
        self.assertIn(client.asset_url(FINCEN), requested)
        stored = list(Path(self.tmp.name, "raw").rglob("*.json"))
        self.assertEqual(len(stored), 1)  # only the kept document's metadata asset is stored
        self.assertEqual(json.loads(stored[0].read_text())["document_number"], FINCEN)

    def test_3_a_kept_agency_slug_keeps_a_document_without_cfr_references(self):
        details = {**DETAILS, "2026-30004": {"cfr_references": [], "agencies": [{"slug": "financial-crimes-enforcement-network"}]}}
        log = {}
        c.download_fr(fr_args("--keep-agency-slug", "financial-crimes-enforcement-network"), TreasuryClient(details), self.corpus, log)
        self.assertEqual(sorted(self.corpus.manifest["documents"]), ["fr:2026-30001", "fr:2026-30004"])
        self.assertEqual(sorted(f["document_number"] for f in log["filtered_out"]), [OFAC, OTHER])

    def test_4_invalid_ranges_and_slugs_are_rejected(self):
        for bad in ("31", "31:1000", "31:1099-1000", "0:1-2", "51:1-2", "31:a-b", "x:1-2", "31:1-2-3"):
            with self.subTest(bad), mock.patch("sys.stderr"), self.assertRaises(SystemExit) as e:
                fr_args("--keep-cfr", bad)
            self.assertEqual(e.exception.code, 2)
        for bad in ("Treasury", "fin cen", "-x", ""):
            with self.subTest(bad), mock.patch("sys.stderr"), self.assertRaises(SystemExit):
                fr_args("--keep-agency-slug", bad)
        self.assertEqual(fr_args("--keep-cfr", "31:500-599", "--keep-cfr", "31:1000-1099").keep_cfr, [(31, 500, 599), (31, 1000, 1099)])

    def test_5_a_kept_entry_carries_a_complete_rights_record(self):
        c.download_fr(fr_args("--keep-cfr", "31:1000-1099", "--keep-cfr", "31:500-599"), TreasuryClient(), self.corpus, {})
        docs = self.corpus.manifest["documents"]
        self.assertEqual(sorted(docs), [f"fr:{FINCEN}", f"fr:{OFAC}"])
        for entry in docs.values():
            self.assertEqual(federal_rights.record_problems(entry["rights"]), [])
            self.assertEqual(entry["rights"], federal_rights.federal_rights("Federal Register"))


class OverlayFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.mp = self.tmp / "manifest.json"
        self.mp.write_text(json.dumps({"schema_version": 1, "failures": {}, "documents": {
            "ecfr:12:1": {"source_class": "regulation", "publisher": "eCFR"}}}))
        self.sha = hashlib.sha256(self.mp.read_bytes()).hexdigest()
        self.overlay = federal_rights.build_overlay(json.loads(self.mp.read_text()), self.sha)

    def run_cli(self, overlay):
        (self.tmp / "federal_rights.json").write_text(json.dumps(overlay))
        out = self.tmp / "out.json"
        with mock.patch("builtins.print"):
            code = cli.main(["--manifest", str(self.mp), "--out", str(out)])
        return code, json.loads(out.read_text())

    def test_6a_an_orphan_overlay_record_fails(self):
        self.assertEqual(self.run_cli(self.overlay)[0], 0)
        orphan = copy.deepcopy(self.overlay)
        orphan["records"]["fr:2026-99999"] = federal_rights.federal_rights("Federal Register")
        code, report = self.run_cli(orphan)
        self.assertEqual((code, report["passed"], report["overlay_records_without_federal_entry"]), (1, False, ["fr:2026-99999"]))

    def test_6b_a_manifest_sha256_mismatch_fails(self):
        wrong = {**self.overlay, "manifest_sha256": "f" * 64}
        code, report = self.run_cli(wrong)
        self.assertEqual((code, report["passed"], report["manifest_mismatch"]), (1, False, True))
        code, report = self.run_cli(self.overlay)
        self.assertEqual((code, report["manifest_mismatch"], report["overlay_manifest_sha256"]), (0, False, self.sha))

    def test_6c_an_absent_manifest_sha256_is_reported_not_failed(self):
        legacy = {k: v for k, v in self.overlay.items() if k != "manifest_sha256"}  # the S7-P1-72 overlay shape
        code, report = self.run_cli(legacy)
        self.assertEqual((code, report["passed"], report["overlay_manifest_sha256"], report["manifest_mismatch"]), (0, True, "absent", False))


if __name__ == "__main__":
    unittest.main()
