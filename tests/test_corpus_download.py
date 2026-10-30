"""Offline acquisition-contract tests; no network, models or cloud bindings."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from scripts import corpus_download as c


HTML = b'<div class="part" id="part-1"><h1>Part 1</h1><div id="p-1.1(a)"><table><tr><td>Value</td></tr></table></div></div>'
TITLES = {"titles": [{"number": 12, "latest_issue_date": "2026-10-23",
                        "up_to_date_as_of": "2026-10-28"}], "meta": {"import_in_progress": False}}
TREE = {"type": "title", "children": [{"type": "chapter", "identifier": "I", "children": [
    {"type": "part", "identifier": "1", "label_description": "Investment Securities"},
    {"type": "part", "identifier": "2", "reserved": True}]}]}


class FakeClient:
    def __init__(self, data, not_modified=False):
        self.data, self.calls, self.not_modified = data, [], not_modified

    def get(self, url, headers=None):
        self.calls.append((url, headers or {}))
        value = self.data[url]
        if self.not_modified and (headers or {}).get("If-None-Match"):
            return c.Response(b"", url, {}, c.now(), 304)
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        return c.Response(body, url, {"etag": '"test"'}, c.now())

    def get_json(self, url):
        response = self.get(url)
        return json.loads(response.body), response


def ecfr_data():
    return {f"{c.ECFR}/api/versioner/v1/titles.json": TITLES,
            f"{c.ECFR}/api/versioner/v1/structure/2026-10-23/title-12.json": TREE,
            f"{c.ECFR}/api/renderer/v1/content/enhanced/2026-10-23/title-12?chapter=I&part=1": HTML}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.output = Path(self.directory.name)
        self.corpus = c.Corpus(self.output)

    def tearDown(self):
        self.directory.cleanup()

    def test_original_bytes_and_locators_survive_download(self):
        args = c.parser().parse_args(["ecfr", "--part", "12:1"])
        counts = c.download_ecfr(args, FakeClient(ecfr_data()), self.corpus)
        row = self.corpus.manifest["documents"]["ecfr:12:1"]
        self.assertEqual(counts["downloaded"], 1)
        self.assertEqual(self.corpus.cached(row["asset"]), HTML)
        self.assertIn("p-1.1(a)", row["locators"])
        self.assertEqual(row["publisher_up_to_date_as_of"], "2026-10-28")
        self.assertFalse(row["eligible_for_indexing"])
        self.assertFalse(row["indexed"])

    def test_resume_revalidates_http_and_does_not_duplicate_assets(self):
        args = c.parser().parse_args(["ecfr", "--part", "12:1"])
        c.download_ecfr(args, FakeClient(ecfr_data()), self.corpus)
        before = set(self.output.glob("raw/*/*"))
        client = FakeClient(ecfr_data(), not_modified=True)
        counts = c.download_ecfr(args, client, self.corpus)
        self.assertEqual(counts["reused"], 1)
        self.assertIn("If-None-Match", client.calls[-1][1])
        self.assertEqual(before, set(self.output.glob("raw/*/*")))

    def test_corrupt_cache_is_downloaded_and_repaired(self):
        args = c.parser().parse_args(["ecfr", "--part", "12:1"])
        c.download_ecfr(args, FakeClient(ecfr_data()), self.corpus)
        asset = self.corpus.manifest["documents"]["ecfr:12:1"]["asset"]
        (self.output / asset["path"]).write_bytes(b"corrupt")
        counts = c.download_ecfr(args, FakeClient(ecfr_data(), not_modified=True), self.corpus)
        self.assertEqual(counts["downloaded"], 1)
        self.assertEqual(self.corpus.cached(asset), HTML)

    def test_captcha_is_a_recorded_failure_not_a_document(self):
        data = ecfr_data()
        data[next(k for k in data if "/renderer/" in k)] = b"<html>Verify you are human</html>"
        counts = c.download_ecfr(c.parser().parse_args(["ecfr", "--part", "12:1"]),
                                 FakeClient(data), self.corpus)
        self.assertEqual(counts["failed"], 1)
        self.assertNotIn("ecfr:12:1", self.corpus.manifest["documents"])
        self.assertIn("ecfr:12:1", self.corpus.manifest["failures"])

    def test_reserved_part_is_not_downloaded(self):
        client = FakeClient(ecfr_data())
        counts = c.download_ecfr(c.parser().parse_args(["ecfr", "--part", "12:2"]), client, self.corpus)
        self.assertEqual(counts["failed"], 1)
        self.assertFalse(any("/renderer/" in url for url, _ in client.calls))

    def test_inventory_limit_is_explicit(self):
        inventory = self.output / "inventory.csv"
        inventory.write_text("title,chapter,part,reserved\n12,I,1,false\n12,I,2,true\n12,I,3,false\n")
        args = c.parser().parse_args(["--max-documents", "1", "ecfr", "--inventory", str(inventory), "--chapter", "I"])
        counts = c.download_ecfr(args, FakeClient(ecfr_data()), self.corpus)
        self.assertEqual(counts["discovered"], 2)
        self.assertTrue(counts["limited"])
        self.assertEqual(counts["attempted"], 1)

    def test_proposal_is_never_promoted_to_current_or_final(self):
        args = c.parser().parse_args(["--max-documents", "1", "federal-register", "--agency", "test", "--since", "2026-01-01"])
        number = "2026-12345"
        class FRClient(FakeClient):
            def get_json(self, url):
                if "documents.json?" in url:
                    value = {"count": 2, "results": [{"document_number": number}], "next_page_url": None}
                else:
                    value = {"document_number": number, "type": "Proposed Rule", "comments_close_on": "2026-01-02",
                             "full_text_xml_url": "https://www.govinfo.gov/example.xml"}
                return value, c.Response(json.dumps(value).encode(), url, {}, c.now())
        client = FRClient({"https://www.govinfo.gov/example.xml": b"<PRORULE><PREAMB>Example</PREAMB></PRORULE>"})
        counts = c.download_fr(args, client, self.corpus)
        row = self.corpus.manifest["documents"][f"fr:{number}"]
        self.assertTrue(counts["limited"])
        self.assertEqual(row["document_type"], "Proposed Rule")
        self.assertIn("unreviewed_disposition", row["rule_status"])
        self.assertFalse(row["eligible_for_indexing"])

    def test_lock_prevents_concurrent_mutation(self):
        with c.output_lock(self.output):
            with self.assertRaises(FileExistsError):
                with c.output_lock(self.output):
                    self.fail("Concurrent lock accepted")
        self.assertFalse((self.output / ".download.lock").exists())

    def test_unofficial_urls_and_false_pdf_xml_are_rejected(self):
        for url in ["http://www.ecfr.gov/", "https://example.com/", "https://user@www.ecfr.gov/", "https://www.ecfr.gov:444/"]:
            with self.assertRaises(ValueError):
                c.allowed_url(url)
        for data, ext in [(b"<html>challenge</html>", "pdf"), (b"<html>challenge</html>", "xml")]:
            with self.assertRaises(ValueError):
                c.validate_asset(data, ext)

    def test_retry_429_but_do_not_retry_or_bypass_403(self):
        class HTTPResponse(io.BytesIO):
            status = 200
            headers = {}
            def geturl(self):
                return "https://www.ecfr.gov/example"
        class Opener:
            def __init__(self, status):
                self.status, self.calls = status, 0
            def open(self, request, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise HTTPError(request.full_url, self.status, "test", {"Retry-After": "0"}, None)
                return HTTPResponse(b"ok")
        retry = Opener(429)
        self.assertEqual(c.Client(opener=retry, sleep=lambda _: None).get("https://www.ecfr.gov/example").body, b"ok")
        self.assertEqual(retry.calls, 2)
        forbidden = Opener(403)
        with self.assertRaises(RuntimeError):
            c.Client(opener=forbidden, sleep=lambda _: None).get("https://www.ecfr.gov/example")
        self.assertEqual(forbidden.calls, 1)


if __name__ == "__main__":
    unittest.main()
