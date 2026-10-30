"""Download Phase 1 official sources without an LLM or third-party packages.

Downloads are evidence candidates, not an applicability or rule-status decision.
The manifest is the current acquisition inventory; this does not build an index.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ECFR = "https://www.ecfr.gov"
FR = "https://www.federalregister.gov"
HOSTS = {"www.ecfr.gov", "www.federalregister.gov", "www.govinfo.gov", "www.gpo.gov"}
PART = re.compile(r"^[0-9]+[a-zA-Z]?$")
DOCUMENT = re.compile(r"^(?:C[0-9]-)?[0-9]{4}-[0-9]+$")  # C1- etc. prefix official correction documents


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def allowed_url(url: str) -> str:
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname not in HOSTS or p.username or
            p.password or p.port not in (None, 443)):
        raise ValueError(f"Source URL outside official allowlist: {url}")
    return url


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class Response:
    body: bytes
    url: str
    headers: dict
    checked_at: str
    status: int = 200


class Client:
    def __init__(self, *, delay=0.5, timeout=30.0, attempts=3,
                 max_bytes=20_000_000, opener=None, sleep=time.sleep):
        self.delay, self.timeout, self.attempts = delay, timeout, attempts
        self.max_bytes, self.sleep = max_bytes, sleep
        self.opener = opener or build_opener(OfficialRedirects())
        self.requests = []

    def get(self, url: str, headers: dict | None = None) -> Response:
        allowed_url(url)
        for attempt in range(self.attempts):
            self.sleep(self.delay)
            request = Request(url, headers={"User-Agent": "RegIntel-corpus/0.1",
                              "Accept": "application/json,application/xml,text/html,*/*",
                              **(headers or {})})
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    allowed_url(response.geturl())
                    body = response.read(self.max_bytes + 1)
                    if len(body) > self.max_bytes:
                        raise ValueError(f"Response exceeds {self.max_bytes} bytes: {url}")
                    if response.status != 200:
                        raise ValueError(f"Unexpected HTTP {response.status}: {url}")
                    result = Response(body, response.geturl(),
                                      {k.lower(): v for k, v in response.headers.items()}, now())
                    self.requests.append({"url": url, "status": 200, "bytes": len(body),
                                          "checked_at": result.checked_at})
                    return result
            except HTTPError as exc:
                self.requests.append({"url": url, "status": exc.code, "checked_at": now()})
                if exc.code == 304:
                    exc.close()
                    return Response(b"", url, {}, now(), 304)
                retry = exc.code in {429, 500, 502, 503, 504}
                wait = retry_after(exc.headers.get("Retry-After"), 2 ** attempt)
                exc.close()
                if not retry or attempt + 1 == self.attempts or wait > 60:
                    raise RuntimeError(f"HTTP {exc.code}: {url}") from None
                self.sleep(wait)
            except (URLError, TimeoutError, OSError) as exc:
                self.requests.append({"url": url, "error": str(exc), "checked_at": now()})
                if attempt + 1 == self.attempts:
                    raise RuntimeError(f"Download failed: {url}: {exc}") from exc
                self.sleep(2 ** attempt)
        raise RuntimeError("Download attempts exhausted")

    def get_json(self, url: str) -> tuple[dict, Response]:
        response = self.get(url)
        data = json.loads(response.body)
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object: {url}")
        return data, response


def retry_after(value: str | None, fallback: float) -> float:
    if not value:
        return fallback
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return fallback


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def output_lock(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    lock = path / ".download.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, str(os.getpid()).encode())
        yield
    finally:
        os.close(descriptor)
        lock.unlink()


class Corpus:
    def __init__(self, output: Path):
        self.output = output
        path = output / "manifest.json"
        self.manifest = json.loads(path.read_text()) if path.exists() else {
            "schema_version": 1, "documents": {}, "failures": {}}
        if (self.manifest.get("schema_version") != 1 or
                not isinstance(self.manifest.get("documents"), dict) or
                not isinstance(self.manifest.get("failures"), dict)):
            raise ValueError("Unsupported or malformed existing manifest; preserve it for review")

    def save(self):
        self.manifest["updated_at"] = now()
        atomic_json(self.output / "manifest.json", self.manifest)

    def cached(self, asset: dict | None) -> bytes | None:
        if not asset:
            return None
        path = (self.output / asset["path"]).resolve()
        if not path.is_relative_to(self.output.resolve()):
            raise ValueError("Manifest asset escapes the output directory")
        if path.is_file():
            body = path.read_bytes()
            if digest(body) == asset["sha256"]:
                return body
        return None

    def store_asset(self, response: Response, extension: str) -> dict:
        checksum = digest(response.body)
        relative = Path("raw") / checksum[:2] / f"{checksum}.{extension}"
        path = self.output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or digest(path.read_bytes()) != checksum:
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(response.body)
            temporary.replace(path)
        return {"path": str(relative), "sha256": checksum, "bytes": len(response.body),
                "url": response.url, "content_type": response.headers.get("content-type"),
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
                "retrieved_at": response.checked_at}

    def record(self, key: str, record: dict):
        self.manifest["documents"][key] = {**record, "checked_at": now(),
            "indexed": False, "eligible_for_indexing": False,
            "applicability_status": "not_assessed"}
        self.manifest["failures"].pop(key, None)
        self.save()

    def failure(self, key: str, exc: Exception):
        self.manifest["failures"][key] = {"error": str(exc), "checked_at": now()}
        self.save()


class PartHTML(HTMLParser):
    def __init__(self, part: str):
        super().__init__(convert_charrefs=True)
        self.part = part
        self.found_part = False
        self.locators = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        identifier = attrs.get("id", "")
        if identifier == f"part-{self.part}" and "part" in attrs.get("class", "").split():
            self.found_part = True
        if identifier.startswith(("p-", "section-", "appendix-")):
            self.locators.add(identifier)


def validate_asset(body: bytes, extension: str, part: str | None = None) -> list[str]:
    if not body.strip():
        raise ValueError("Empty source response")
    if extension == "html":
        parser = PartHTML(part or "")
        parser.feed(body.decode("utf-8"))
        if not parser.found_part:
            raise ValueError("Response is not the requested eCFR part (possible error/challenge page)")
        return sorted(parser.locators)
    if extension == "xml":
        root = ET.fromstring(body)
        if root.tag.rsplit("}", 1)[-1].lower() in {"html", "script"}:
            raise ValueError("HTML/challenge response returned in place of XML")
        return []
    if extension == "pdf" and not body.startswith(b"%PDF-"):
        raise ValueError("Non-PDF response returned in place of PDF")
    return []


def fetch_asset(client: Client, corpus: Corpus, url: str, extension: str,
                previous: dict | None, part: str | None = None) -> tuple[dict, bytes, bool]:
    old_body = corpus.cached(previous)
    conditional = {}
    if previous and previous.get("url") == url and old_body is not None:
        if previous.get("etag"):
            conditional["If-None-Match"] = previous["etag"]
        if previous.get("last_modified"):
            conditional["If-Modified-Since"] = previous["last_modified"]
    response = client.get(url, conditional)
    if response.status == 304:
        if old_body is None or not conditional:
            raise ValueError("HTTP 304 without a verified conditional cache")
        validate_asset(old_body, extension, part)
        return previous, old_body, True
    validate_asset(response.body, extension, part)
    return corpus.store_asset(response, extension), response.body, False


def ecfr_parts(tree: dict) -> dict[str, dict]:
    result = {}

    def walk(node, filters):
        filters = dict(filters)
        kind = node.get("type")
        if kind in {"subtitle", "chapter", "subchapter"}:
            filters[kind] = node["identifier"]
        if kind == "part":
            if not node.get("reserved"):
                result[node["identifier"]] = {"filters": filters,
                    "title": node.get("label_description", node.get("label"))}
            return
        for child in node.get("children", []):
            walk(child, filters)

    walk(tree, {})
    return result


def requested_parts(args) -> list[tuple[int, str]]:
    selected = []
    if args.inventory:
        with Path(args.inventory).open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("reserved", "false").lower() != "false":
                    continue
                if args.title and int(row["title"]) != args.title:
                    continue
                if args.chapter and row["chapter"] != args.chapter:
                    continue
                selected.append((int(row["title"]), row["part"]))
    else:
        for spec in args.part or []:
            title, part = spec.split(":", 1)
            selected.append((int(title), part))
    if not selected:
        raise ValueError("Select at least one --part TITLE:PART or matching --inventory entry")
    if any(not 1 <= title <= 50 or not PART.fullmatch(part) for title, part in selected):
        raise ValueError("Invalid title or part identifier")
    return list(dict.fromkeys(selected))


def download_ecfr(args, client: Client, corpus: Corpus) -> dict:
    selected = requested_parts(args)
    titles, titles_response = client.get_json(f"{ECFR}/api/versioner/v1/titles.json")
    if titles.get("meta", {}).get("import_in_progress"):
        raise ValueError("Publisher import in progress; retry after publication stabilizes")
    title_map = {int(t["number"]): t for t in titles["titles"]}
    catalogs = {}
    counts = {"discovered": len(selected), "attempted": 0, "downloaded": 0, "reused": 0, "failed": 0}
    for title, part in selected[:args.max_documents]:
        key = f"ecfr:{title}:{part}"
        counts["attempted"] += 1
        try:
            info = title_map[title]
            issue, up_to_date = info["latest_issue_date"], info["up_to_date_as_of"]
            date.fromisoformat(issue)
            date.fromisoformat(up_to_date)
            if title not in catalogs:
                structure_url = f"{ECFR}/api/versioner/v1/structure/{issue}/title-{title}.json"
                structure, response = client.get_json(structure_url)
                catalogs[title] = (ecfr_parts(structure), corpus.store_asset(response, "json"))
            catalog, catalog_asset = catalogs[title]
            entry = catalog[part]  # Fails honestly if absent/reserved at the refreshed baseline.
            params = {**entry["filters"], "part": part}
            url = f"{ECFR}/api/renderer/v1/content/enhanced/{issue}/title-{title}?{urlencode(params)}"
            previous = corpus.manifest["documents"].get(key, {})
            asset, body, reused = fetch_asset(client, corpus, url, "html", previous.get("asset"), part)
            counts["reused" if reused else "downloaded"] += 1
            locators = validate_asset(body, "html", part)
            corpus.record(key, {"source_class": "regulation", "publisher": "eCFR",
                "citation": f"{title} CFR Part {part}", "title": entry["title"],
                "source_url": url, "publisher_issue_date": issue,
                "publisher_up_to_date_as_of": up_to_date,
                "rule_status": "published_baseline_requires_operative_effect_review",
                "asset": asset, "catalog_asset": catalog_asset,
                "titles_metadata_sha256": digest(titles_response.body), "locators": locators})
        except (KeyError, ValueError, RuntimeError, OSError) as exc:
            counts["failed"] += 1
            corpus.failure(key, exc)
    counts["limited"] = len(selected) > args.max_documents
    return counts


def download_fr(args, client: Client, corpus: Corpus) -> dict:
    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until)
    if since > until:
        raise ValueError("--since must not be later than --until")
    params = [("conditions[publication_date][gte]", since.isoformat()),
              ("conditions[publication_date][lte]", until.isoformat()),
              ("order", "newest"), ("per_page", min(args.max_documents, 50))]
    params += [("conditions[agencies][]", a) for a in args.agency]
    params += [("conditions[type][]", t) for t in (args.document_type or ["RULE", "PRORULE", "NOTICE"])]
    url = f"{FR}/api/v1/documents.json?{urlencode(params)}"
    counts = {"discovered": 0, "attempted": 0, "downloaded": 0, "reused": 0, "failed": 0}
    seen, pages = set(), set()
    while url and counts["attempted"] < args.max_documents:
        if url in pages:
            raise ValueError("Federal Register pagination cycle")
        pages.add(url)
        listing, _ = client.get_json(url)
        counts["discovered"] = int(listing["count"])
        for hit in listing["results"]:
            number = str(hit["document_number"])
            if number in seen:
                continue
            if counts["attempted"] >= args.max_documents:
                break
            seen.add(number)
            counts["attempted"] += 1
            key = f"fr:{number}"
            try:
                if not DOCUMENT.fullmatch(number):
                    raise ValueError("Invalid Federal Register document number")
                detail, metadata = client.get_json(f"{FR}/api/v1/documents/{number}.json")
                if detail.get("document_number") != number:
                    raise ValueError("Federal Register detail/document mismatch")
                metadata_asset = corpus.store_asset(metadata, "json")
                if detail.get("full_text_xml_url"):
                    asset_url, extension = detail["full_text_xml_url"], "xml"
                elif detail.get("pdf_url"):
                    asset_url, extension = detail["pdf_url"], "pdf"
                else:
                    raise ValueError("No official XML or PDF URL supplied")
                previous = corpus.manifest["documents"].get(key, {}).get("asset")
                asset, _, reused = fetch_asset(client, corpus, asset_url, extension, previous)
                counts["reused" if reused else "downloaded"] += 1
                corpus.record(key, {"source_class": "rulemaking_candidate", "publisher": "Federal Register",
                    "document_number": number, "title": detail.get("title"),
                    "source_url": detail.get("html_url"), "document_type": detail.get("type"),
                    "publication_date": detail.get("publication_date"),
                    "effective_on": detail.get("effective_on"),
                    "comments_close_on": detail.get("comments_close_on"),
                    "dates_text": detail.get("dates"), "action": detail.get("action"),
                    "docket_ids": detail.get("docket_ids"),
                    "regulation_id_numbers": detail.get("regulation_id_numbers"),
                    "cfr_references": detail.get("cfr_references"),
                    "official_pdf_url": detail.get("pdf_url"),
                    "rule_status": "unreviewed_disposition_not_inferred_from_document_type",
                    "asset": asset, "metadata_asset": metadata_asset})
            except (KeyError, ValueError, RuntimeError, OSError, ET.ParseError) as exc:
                counts["failed"] += 1
                corpus.failure(key, exc)
        following = listing.get("next_page_url")
        if following == url:
            raise ValueError("Federal Register repeated its pagination URL")
        url = following
    counts["limited"] = counts["discovered"] > counts["attempted"]
    return counts


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=Path("data/corpus-phase1"))
    ap.add_argument("--max-documents", type=int, default=10)
    ap.add_argument("--delay", type=float, default=0.5)
    sub = ap.add_subparsers(dest="command", required=True)
    ecfr = sub.add_parser("ecfr", help="Latest publisher baseline, explicit parts or inventory slice")
    choice = ecfr.add_mutually_exclusive_group(required=True)
    choice.add_argument("--part", action="append", help="TITLE:PART, repeatable")
    choice.add_argument("--inventory", help="Phase 1 FEDERAL_CFR_PART_CENSUS.csv")
    ecfr.add_argument("--title", type=int)
    ecfr.add_argument("--chapter")
    fr = sub.add_parser("federal-register", help="Bounded rulemaking discovery, not lifecycle adjudication")
    fr.add_argument("--agency", action="append", required=True, help="Official agency slug; repeatable")
    fr.add_argument("--since", required=True, help="Publication date lower bound, YYYY-MM-DD")
    fr.add_argument("--until", default=date.today().isoformat())
    fr.add_argument("--document-type", action="append", choices=["RULE", "PRORULE", "NOTICE"])
    return ap


def main(argv=None) -> int:
    ap = parser()
    args = ap.parse_args(argv)
    if args.max_documents < 1 or not math.isfinite(args.delay) or args.delay < 0:
        ap.error("--max-documents must be positive and --delay must be nonnegative")
    if args.command == "ecfr" and args.part and (args.title or args.chapter):
        ap.error("--title/--chapter apply to --inventory, not --part")
    client = Client(delay=args.delay)
    try:
        with output_lock(args.output):
            corpus = Corpus(args.output)
            run = {"run_id": uuid.uuid4().hex, "started_at": now(), "command": args.command,
                   "selection": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}}
            try:
                function = download_ecfr if args.command == "ecfr" else download_fr
                run["counts"] = function(args, client, corpus)
            except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
                corpus.failure(f"discovery:{args.command}", exc)
                run["error"] = str(exc)
            run["finished_at"] = now()
            run["requests"] = client.requests
            logs = args.output / "runs"
            logs.mkdir(exist_ok=True)
            atomic_json(logs / f'{run["run_id"]}.json', run)
            summary = {k: v for k, v in run.items() if k not in {"requests", "selection"}}
            print(json.dumps(summary, indent=2))
            return 1 if run.get("error") or run.get("counts", {}).get("failed") else 0
    except (OSError, ValueError) as exc:
        print(f"Downloader stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
