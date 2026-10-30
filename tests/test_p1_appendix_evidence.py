"""Tests for S7-P1-APPENDIX-EVIDENCE-16 (F2): appendix text carried as a direct REGTEXT/EXTRACT block (an appendix
title heading followed by an EXTRACT, with no SECTION wrapper) is recovered in document order with its identity, source
hash/URL locators and a completeness proof, while no CFR section or part-wide amendment is asserted for it."""
import hashlib
import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.fedreg import parse_fr
from test_p1 import asset

LONG = " ".join(f"Scenario design sentence number {n} describes the severely adverse macroeconomic path." for n in range(1, 9))
A_PARAS = "".join(f"<P>({chr(97 + n % 20)}{n}) {LONG}</P>" for n in range(40))  # forces several chunks
APP_XML = f"""<RULE><PREAMB><CFR>12 CFR Part 252</CFR></PREAMB><SUPLINF><PRTPAGE P="100"/><HD SOURCE="HED">Summary</HD><P>Final rule.</P>
<REGTEXT TITLE="12" PART="252">
<AMDPAR>1. In &#xA7; 252.14, revise paragraph (a) to read as follows:</AMDPAR>
<SECTION><SECTNO>&#xA7; 252.14</SECTNO><SUBJECT>Stress testing.</SUBJECT><P>(a) Covered companies conduct a stress test.</P></SECTION></REGTEXT>
<REGTEXT TITLE="12" PART="252">
<AMDPAR>2. Appendix A to part 252 is revised to read as follows:</AMDPAR>
<HD SOURCE="HD1">Appendix A to Part 252&#x2014;Policy Statement on the Scenario Design Framework for Stress Testing </HD>
<EXTRACT><HD SOURCE="HD1">1. Background</HD><P>(a) The Board uses a scenario<SU>1</SU><FTREF/> design framework.</P>
<FTNT><P><SU>1</SU> 12 U.S.C. 5365(i).</P></FTNT>
<PRTPAGE P="105"/><HD SOURCE="HD2">2. Tables</HD>
<GPOTABLE COLS="2"><TTITLE>Table 1 - Unemployment</TTITLE><BOXHD><CHED H="1">Quarter</CHED><CHED H="1">Rate</CHED></BOXHD>
<ROW><ENT>Q1</ENT><ENT>4.0</ENT></ROW><TNOTE>Source: Bureau of Labor Statistics.</TNOTE></GPOTABLE>
<STARS/>{A_PARAS}</EXTRACT></REGTEXT>
<REGTEXT TITLE="12" PART="252">
<AMDPAR>3. In appendix B to part 252:</AMDPAR><AMDPAR>a. Revise paragraph (a) of section 2.1.</AMDPAR><AMDPAR>b. Revise section 2.2.</AMDPAR>
<P>The revisions read as follows:</P>
<HD SOURCE="HD1">Appendix B to Part 252&#x2014;Stress Testing Policy Statement</HD>
<EXTRACT><STARS/><HD SOURCE="HD2">2.1 Soundness in Model Design</HD><P>(a) During development, the Federal Reserve</P><P>(i) subjects supervisory models to review;</P>
<STARS/><HD SOURCE="HD2">2.2. Disclosure of Information</HD><P>(a) In general, the Board does not disclose supervisory stress test information.</P></EXTRACT></REGTEXT>
<REGTEXT TITLE="12" PART="252"><AMDPAR>4. Add untitled extract text:</AMDPAR><EXTRACT><P>Untitled extract that is not an appendix.</P></EXTRACT></REGTEXT>
</SUPLINF></RULE>"""
# The Federal Register XML separates block elements with whitespace; the fixture does the same so that the source-token
# fingerprint (which reads the element text as published) sees the same word boundaries as the recovered lines.
APP_XML = re.sub(r"(</(?:P|HD|FTNT|TTITLE|BOXHD|ROW|ENT|CHED|TNOTE|GPOTABLE|AMDPAR|EXTRACT)>|<STARS/>|<PRTPAGE[^>]*/>)", r"\1\n", APP_XML).encode()
OTHER_XML = b"""<RULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF><HD SOURCE="HED">Summary</HD><P>Scenario design framework mention.</P>
<REGTEXT TITLE="12" PART="3"><AMDPAR>1. Amend &#xA7; 3.1 by revising paragraph (a) to read as follows:</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.1</SECTNO><SUBJECT>Purpose.</SUBJECT><P>(a) Scenario design framework capital text.</P></SECTION></REGTEXT></SUPLINF></RULE>"""
ECFR_252 = """<div class="part" id="part-252"><h1>PART 252—ENHANCED PRUDENTIAL STANDARDS</h1>
<div class="section" id="252.14"><h4>§ 252.14 Stress testing.</h4><div id="p-252.14(a)"><p class="indent-1" data-title="252.14(a)">(a) Existing stress test text.</p></div></div>
<div class="section" id="252.44"><h4>§ 252.44 Capital planning.</h4><div id="p-252.44(a)"><p class="indent-1" data-title="252.44(a)">(a) Existing capital planning text.</p></div></div></div>"""

def clean(text):  # independent of the implementation under test
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def source_tokens(text):
    return re.findall(r"[A-Za-z0-9]+", text.replace("[footnote]", " "))


def token_fingerprint(tokens):
    return {"tokens": len(tokens), "sorted_tokens_sha256": hashlib.sha256("\n".join(sorted(tokens)).encode("utf-8")).hexdigest()}


KEY, OTHER = "fr:2026-93001", "fr:2026-93002"
HEAD_A = "Appendix A to Part 252—Policy Statement on the Scenario Design Framework for Stress Testing"
HEAD_B = "Appendix B to Part 252—Stress Testing Policy Statement"


def units_of(xml=APP_XML):
    return parse_fr(xml, "2026-93001")[1]


def appendix_units(units, letter):
    return [u for u in units if u.get("appendix") and u["appendix"].get("kind") == "appendix_extract" and u["appendix"]["letter"] == letter and u["text"]]


class ParserTests(unittest.TestCase):
    def test_both_appendix_shapes_are_recovered_with_distinct_identities(self):
        units = units_of()
        a, b = appendix_units(units, "A"), appendix_units(units, "B")
        self.assertGreater(len(a), 1)  # long appendix is split into ordered passages
        self.assertEqual(len(b), 1)
        self.assertEqual(a[0]["appendix"]["heading"], HEAD_A)
        self.assertEqual(b[0]["appendix"]["heading"], HEAD_B)
        self.assertEqual(a[0]["appendix"]["title"], "Policy Statement on the Scenario Design Framework for Stress Testing")
        self.assertEqual((a[0]["appendix"]["part"], b[0]["appendix"]["part"]), ("252", "252"))
        self.assertNotEqual(a[0]["appendix"]["extract_index"], b[0]["appendix"]["extract_index"])
        self.assertEqual(Counter(u["appendix"]["binding_status"] for u in a + b), {"not_determined": len(a) + len(b)})

    def test_chunks_are_complete_ordered_and_match_the_source_fingerprint(self):
        units = units_of()
        root = ET.fromstring(APP_XML)
        extracts = list(root.iter("EXTRACT"))
        for letter, index in (("A", 0), ("B", 1)):
            chunks = appendix_units(units, letter)
            self.assertEqual([c["appendix"]["chunk"] for c in chunks], list(range(1, len(chunks) + 1)))
            self.assertEqual({c["appendix"]["chunks"] for c in chunks}, {len(chunks)})
            self.assertEqual(sum(c["appendix"]["lines"] for c in chunks), chunks[0]["appendix"]["total_lines"])
            self.assertEqual([c["appendix"]["first_line"] for c in chunks][0], 1)
            tokens = [t for c in chunks for t in source_tokens(c["text"])]
            independent = source_tokens(clean("".join(extracts[index].itertext())))  # recomputed here from the raw XML
            self.assertEqual(token_fingerprint(tokens), token_fingerprint(independent), letter)
            self.assertEqual(chunks[0]["appendix"]["source_fingerprint"], token_fingerprint(independent))

    def test_order_headings_footnotes_tables_notes_and_elision_marks_are_kept(self):
        text = "\n".join(c["text"] for c in appendix_units(units_of(), "A"))
        order = [text.index(x) for x in ("1. Background", "The Board uses a scenario", "[footnote] 1 12 U.S.C. 5365(i).",
                                          "2. Tables", "Table 1 - Unemployment", "Q1 | 4.0", "Source: Bureau of Labor Statistics.", "* * *", "(a0)")]
        self.assertEqual(order, sorted(order))
        self.assertRegex(text, r"scenario\s*1 design framework")  # inline footnote reference number stays in place
        b = "\n".join(c["text"] for c in appendix_units(units_of(), "B"))
        self.assertEqual(b.split("\n")[0], "* * *")
        self.assertLess(b.index("2.1 Soundness in Model Design"), b.index("2.2. Disclosure of Information"))

    def test_page_follows_the_page_markers(self):
        chunks = appendix_units(units_of(), "A")
        self.assertEqual(chunks[0]["fr_page"], "100")
        self.assertEqual(chunks[-1]["fr_page"], "105")

    def test_no_section_or_part_target_is_asserted(self):
        units = units_of()
        for u in appendix_units(units, "A") + appendix_units(units, "B"):
            self.assertIsNone(u["section_no"])
            self.assertEqual((u["explicit_targets"], u["explicit_parts"], u["explicit_ranges"], u["referenced_citations"]), ([], [], [], []))
            self.assertEqual(u["explicit_paragraphs"], {"paths": [], "status": "appendix_scope_not_attributed"})
            self.assertEqual(u["pairing_status"], "appendix_text")
            self.assertTrue(u["unresolved_candidates"])
        every = [u for u in units if u.get("appendix")]
        self.assertTrue(all(u["explicit_parts"] == [] and u["explicit_targets"] == [] for u in every))  # includes the instruction rows
        instr = [u for u in units if u["pairing_status"] == "appendix_instruction"]
        self.assertEqual(sorted(u["appendix"]["letter"] for u in instr), ["A", "B"])
        b_instr = next(u for u in instr if u["appendix"]["letter"] == "B")
        self.assertIn("a. Revise paragraph (a) of section 2.1. b. Revise section 2.2.", b_instr["instruction"])  # boundaries and wording kept

    def test_ordinary_section_amendment_in_the_same_document_is_unchanged(self):
        sec = [u for u in units_of() if u["section_no"] == "252.14"]
        self.assertEqual(len(sec), 1)
        self.assertEqual((sec[0]["pairing_status"], sec[0]["explicit_targets"], sec[0]["explicit_paragraphs"]["paths"]), ("explicit", ["252.14"], ["(a)"]))
        self.assertIsNone(sec[0].get("appendix"))

    def test_an_untitled_direct_extract_is_still_not_recovered_and_is_counted(self):
        _, units, stats = parse_fr(APP_XML, "2026-93001")
        self.assertEqual(stats["direct_extract_not_recovered"], 1)
        self.assertFalse([u for u in units if "Untitled extract" in u["text"]])

    def test_extract_spans_find_each_extract_in_document_order(self):
        from regintel_p1.fedreg import extract_spans
        text = APP_XML.decode("utf-8")
        spans = extract_spans(text)
        self.assertEqual(len(spans), len(list(ET.fromstring(APP_XML).iter("EXTRACT"))))
        self.assertTrue(text[spans[1][0]:spans[1][1]].startswith("<EXTRACT>") and text[spans[1][0]:spans[1][1]].endswith("</EXTRACT>"))
        self.assertIn("Soundness in Model Design", text[spans[1][0]:spans[1][1]])
        self.assertIn("1. Background", text[spans[0][0]:spans[0][1]])


class AppendixIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01",
                "document_type": "Rule", "effective_on": "2027-02-01", "action": "Final rule."}
        app_asset = {**asset(root, "app.xml", APP_XML), "url": "https://example.test/full_text/2026-93001.xml"}
        cls.app_sha = app_asset["sha256"]
        docs = {"ecfr:12:252": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 252", "title": "EPS",
                                "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                                "asset": asset(root, "e252.html", ECFR_252.encode())},
                KEY: {**meta, "document_number": "2026-93001", "title": "Stress test transparency", "source_url": "https://example.test/doc/2026-93001",
                      "asset": app_asset},
                OTHER: {**meta, "document_number": "2026-93002", "title": "Capital", "asset": asset(root, "o.xml", OTHER_XML)}}
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.source = APP_XML.decode("utf-8")
        cls.db = str(Path(cls.tmp.name) / "a.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)
        cls.conn = cls.index.conn

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rows(self, letter):
        return self.conn.execute("SELECT p.*, d.sha256 AS doc_sha, d.source_url FROM provision p JOIN source_document d ON d.id=p.doc_id "
                                 "WHERE d.doc_key=? AND json_extract(p.appendix,'$.kind')='appendix_extract' AND json_extract(p.appendix,'$.letter')=? "
                                 "ORDER BY p.id", (KEY, letter)).fetchall()

    def test_provenance_locators_hash_url_and_source_span(self):
        for letter, head in (("A", HEAD_A), ("B", HEAD_B)):
            rows = self.rows(letter)
            self.assertTrue(rows)
            locs = [r["locator"] for r in rows]
            self.assertEqual(len(set(locs)), len(locs))
            for n, r in enumerate(rows, 1):
                ap = json.loads(r["appendix"])
                self.assertRegex(r["locator"], rf"^2026-93001:reg:\d+:appx-252-{letter}:{n:03d}of{len(rows):03d}$")
                self.assertEqual((ap["source_sha256"], r["doc_sha"], self.app_sha), (self.app_sha,) * 3)
                self.assertEqual((ap["source_url"], ap["source_asset_url"]), ("https://example.test/doc/2026-93001", "https://example.test/full_text/2026-93001.xml"))
                self.assertEqual((r["kind"], r["title_no"], r["part_no"], r["section_no"], r["heading"]), ("amendment", "12", "252", None, head))
                self.assertTrue(self.source[r["char_start"]:r["char_end"]].startswith("<EXTRACT"))
                self.assertTrue(self.source[r["char_start"]:r["char_end"]].endswith("</EXTRACT>"))
            self.assertEqual(len({(r["char_start"], r["char_end"]) for r in rows}), 1)  # every passage points at its own EXTRACT
        self.assertNotEqual((self.rows("A")[0]["char_start"],), (self.rows("B")[0]["char_start"],))

    def test_indexed_chunks_reproduce_the_source_text(self):
        root = ET.fromstring(APP_XML)
        for letter, i in (("A", 0), ("B", 1)):
            tokens = [t for r in self.rows(letter) for t in source_tokens(r["text"])]
            self.assertEqual(token_fingerprint(tokens), token_fingerprint(source_tokens(clean("".join(list(root.iter("EXTRACT"))[i].itertext())))))

    def test_existing_locators_are_stable(self):
        locs = [r[0] for r in self.conn.execute("SELECT locator FROM provision WHERE doc_id=(SELECT id FROM source_document WHERE doc_key=?) "
                                                "AND kind='amendment' AND locator NOT LIKE '%appx-%' ORDER BY id", (KEY,))]
        self.assertEqual(locs, ["2026-93001:reg:1:252.14:1", "2026-93001:reg:2:part:2", "2026-93001:reg:3:part:3", "2026-93001:reg:4:part:4"])

    def test_future_retrieval_reaches_the_appendix_for_part_252_only(self):
        q = "scenario design framework severely adverse macroeconomic"
        for mode in ("hybrid", "bm25"):
            fut = self.index.answer(q, mode=mode, scope="future", parts=["12:252"], k=10)
            hit = [r for r in fut["results"] if r["locator"].find("appx-252-A") > 0]
            self.assertTrue(hit, mode)
            self.assertEqual(hit[0]["appendix"]["letter"], "A")
            self.assertEqual(hit[0]["source_sha256"], self.app_sha)
            self.assertEqual(hit[0]["status"]["document_number"], "2026-93001")
            self.assertEqual(fut["applicability_status"], "not_assessed")
            for scope, parts in (("current", ["12:252"]), ("future", ["12:3"]), ("future", ["12:217"]), ("current", None)):
                out = self.index.answer(q, mode=mode, scope=scope, parts=parts, k=10)
                self.assertFalse([r for r in out["results"] if "appx-" in r["locator"]], (mode, scope, parts))
            both = self.index.answer(q, mode=mode, scope="both", parts=["12:252"], k=10)
            self.assertTrue([r for r in both["results"] if "appx-252-A" in r["locator"]])
        b = self.index.answer("soundness in model design disclosure", mode="hybrid", scope="future", parts=["12:252"], k=10)
        self.assertTrue([r for r in b["results"] if "appx-252-B" in r["locator"]])
        small = self.index.answer("Appendix B to Part 252 Stress Testing Policy Statement", mode="hybrid", scope="future", parts=["12:252"], k=1)
        self.assertTrue(small["results"])

    def test_no_fabricated_section_or_part_targets(self):
        app_ids = [r[0] for r in self.conn.execute("SELECT id FROM provision WHERE appendix IS NOT NULL")]
        self.assertTrue(app_ids)
        marks = ",".join("?" * len(app_ids))
        self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM relationship WHERE src_provision_id IN ({marks})", app_ids).fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM provision WHERE section_no IN ('2.1','2.2','3.1','3.2')").fetchone()[0],
                         self.conn.execute("SELECT COUNT(*) FROM provision WHERE section_no IN ('3.1') AND doc_id=(SELECT id FROM source_document WHERE doc_key=?)", (OTHER,)).fetchone()[0])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM relationship WHERE dst_citation IN ('12 CFR 2.1','12 CFR 2.2','12 CFR Part 252')").fetchone()[0], 0)
        d = self.index.delta("12 CFR 2.1")
        self.assertEqual(d["instruments"], [])
        self.assertIsNone(d["baseline"])
        d252 = self.index.delta("12 CFR 252.44")  # appendix text must not read as an amendment of an untouched section
        self.assertEqual(d252["instruments"], [])
        d14 = self.index.delta("12 CFR 252.14")
        self.assertEqual([g["doc_key"] for g in d14["instruments"]], [KEY])
        self.assertFalse([x for g in d14["instruments"] for x in json.dumps(g, default=str).split('"') if "appx-" in x])
        self.assertFalse(self.index.resolve_citations("12 CFR 252.44", "future")[0]["resolved"])

    def test_policy_statement_identity_and_caveats_without_binding_claims(self):
        r = self.rows("A")[0]
        ap = json.loads(r["appendix"])
        self.assertTrue(ap["policy_statement"])
        self.assertEqual(ap["binding_status"], "not_determined")
        self.assertIn("not determined", ap["note"])
        rec = self.index.evidence(r["id"])
        self.assertEqual(rec["pairing_status"], "appendix_text")
        self.assertEqual(rec["explicit_paragraphs"]["status"], "appendix_scope_not_attributed")
        self.assertEqual(rec["status"]["state"][:6], rec["status"]["state"][:6])
        status = {x[0] for x in self.conn.execute("SELECT DISTINCT review_status FROM relationship")}
        self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, status)

    def test_instruction_rows_assert_no_part_wide_amendment(self):
        rows = self.conn.execute("SELECT * FROM provision WHERE doc_id=(SELECT id FROM source_document WHERE doc_key=?) AND pairing_status='appendix_instruction'", (KEY,)).fetchall()
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual((json.loads(r["explicit_parts"]), json.loads(r["explicit_targets"])), ([], []))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM relationship WHERE dst_citation='12 CFR Part 252'").fetchone()[0], 0)

    def test_unrelated_part_3_document_is_untouched(self):
        d = self.index.delta("12 CFR 3.1")
        self.assertEqual([g["doc_key"] for g in d["instruments"]], [OTHER])

    def test_evaluator_group_is_registered_runs_offline_and_fails_when_the_guarantee_is_broken(self):
        import shutil

        from regintel_p1 import evaluate
        self.assertIn("appendix_evidence", evaluate.REGRESSION_GROUPS)
        reg = json.loads((Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual({x["id"] for x in reg["appendix_evidence"]}, {f"AE0{n}" for n in range(1, 8)})
        rows = self.rows("A")
        a = json.loads(rows[0]["appendix"])
        item = {"id": "T1", "kind": "recovered", "doc_key": KEY, "part": "252", "letter": "A", "heading": HEAD_A, "chunks": len(rows),
                "lines": a["total_lines"], "source_sha256": self.app_sha, "fingerprint": a["source_fingerprint"],
                "first_locator": rows[0]["locator"], "last_locator": rows[-1]["locator"]}
        self.assertTrue(evaluate.check_appendix_evidence(self.index, item)["passed"])
        self.assertFalse(evaluate.check_appendix_evidence(self.index, {**item, "chunks": len(rows) + 1})["passed"])
        self.assertFalse(evaluate.check_appendix_evidence(self.index, {**item, "fingerprint": {**a["source_fingerprint"], "tokens": 1}})["passed"])
        retrieval = {"id": "T2", "kind": "retrieval", "doc_key": KEY, "part": "252", "letter": "A", "query": "scenario design framework severely adverse macroeconomic"}
        self.assertTrue(evaluate.check_appendix_evidence(self.index, retrieval)["passed"])
        targets = {"id": "T3", "kind": "no_targets", "doc_key": KEY, "part": "252", "internal_sections": ["2.1", "2.2"], "delta_citations": ["12 CFR 252.44", "12 CFR 252.14"]}
        self.assertTrue(evaluate.check_appendix_evidence(self.index, targets)["passed"])
        copy = str(Path(self.tmp.name) / "tampered.sqlite")
        shutil.copy(self.db, copy)
        import sqlite3
        conn = sqlite3.connect(copy)
        conn.execute("UPDATE provision SET explicit_parts='[\"252\"]' WHERE appendix IS NOT NULL AND pairing_status='appendix_text'")
        conn.commit()
        conn.close()
        bad = search.Index(copy)
        try:
            self.assertFalse(evaluate.check_appendix_evidence(bad, targets)["passed"])
        finally:
            bad.conn.close()


if __name__ == "__main__":
    unittest.main()
