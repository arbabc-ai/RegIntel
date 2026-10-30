import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from regintel_p1.ecfr import parse_ecfr
from regintel_p1.fedreg import parse_fr

ECFR_HTML = """<div class="part" id="part-3"><h1>PART 3—CAPITAL</h1>
<div class="section" id="3.1">
<h4 data-hierarchy-metadata='{}'>§ 3.1 Purpose.</h4>
<div id="p-3.1(a)"><p class="indent-1" data-title="3.1(a)"><span class="paragraph-hierarchy"><span class="paren">(</span>a<span class="paren">)</span></span> <em>Purpose.</em> This part sets <a href="/x">minimum</a> capital.</p></div>
<div id="p-3.1(b)">
<p class="indent-1" data-title="3.1(b)">(b) Ratios.</p>
<div id="p-3.1(b)(1)"><p class="indent-2" data-title="3.1(b)(1)">(1) Banks keep a ratio of 4.5 percent.</p></div>
<p class="indent-2" id="p-3.1(b)(2)" data-title="3.1(b)(2)">(2) Leaf paragraph with its own id.</p>
</div>
<div class="table-wrapper"><div class="gpotbl_div"><table><caption><p class="title">Table 1 to § 3.1</p></caption>
<thead><tr><th>Buffer</th><th>Payout</th></tr></thead><tbody><tr><td>Above 2.5</td><td>None</td></tr></tbody></table></div></div>
</div>
<div class="section" id="3.2"><h4>§ 3.2 Definitions.</h4><p>As used in this part:</p>
<div id="p-3.2(Tier%201%20capital)"><p class="indent-1" data-title="3.2 “Tier 1 capital”" data-term="true"><em class="paragraph-hierarchy">Tier 1 capital</em>   means CET1 plus AT1.</p></div>
</div></div>"""

FR_PRORULE = b"""<PRORULE><PREAMB><CFR>12 CFR Part 3</CFR><RIN>RIN 1557-AF99</RIN><SUBJECT>Capital</SUBJECT></PREAMB>
<SUPLINF><PRTPAGE P="100"/><HD SOURCE="HED">I. Background</HD><P>The agency proposes changes.</P>
<HD SOURCE="HD1">A. Detail</HD><P>More detail here.</P>
<PART><HD SOURCE="HED">PART 3--CAPITAL</HD>
<AUTH><HD SOURCE="HED">Authority:</HD><P>12 U.S.C. 93a.</P></AUTH>
<SECTION><SECTNO>&#xA7; 3.1</SECTNO><SUBJECT>[Amended]</SUBJECT></SECTION>
<AMDPAR>2. Amend &#xA7; 3.1 by:</AMDPAR><AMDPAR>a. Revising paragraph (a).</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.2</SECTNO><SUBJECT>Definitions.</SUBJECT><P>(a) New text for 3.2.</P></SECTION>
<AMDPAR>3. Amend &#xA7; 3.2 by adding paragraph (a).</AMDPAR>
</PART></SUPLINF></PRORULE>"""

FR_RULE = b"""<RULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF><HD SOURCE="HED">Summary</HD><P>Final.</P>
<REGTEXT TITLE="12" PART="3"><AMDPAR>1. Amend &#xA7; 3.1 by revising paragraph (a) to read as follows:</AMDPAR>
<SECTION><SECTNO>&#xA7; 3.1</SECTNO><SUBJECT>Purpose.</SUBJECT><P>(a) Purpose. Revised minimum capital text.</P></SECTION></REGTEXT></SUPLINF></RULE>"""


class EcfrParserTests(unittest.TestCase):
    def setUp(self):
        self.provs, self.stats = parse_ecfr(ECFR_HTML, "12", "3")
        self.by = {p["locator"]: p for p in self.provs}

    def test_paragraph_text_excludes_children_and_joins_inline_spans(self):
        self.assertEqual(self.by["3.1(a)"]["text"], "(a) Purpose. This part sets minimum capital.")
        self.assertEqual(self.by["3.1(b)"]["text"], "(b) Ratios.")
        self.assertEqual(self.by["3.1(b)(1)"]["text"], "(1) Banks keep a ratio of 4.5 percent.")
        self.assertEqual(self.by["3.1(b)"]["kind"], "paragraph")

    def test_leaf_paragraph_with_id_and_citation(self):
        self.assertIn("3.1(b)(2)", self.by)
        self.assertEqual(self.by["3.1(b)(2)"]["citation"], "12 CFR 3.1(b)(2)")
        self.assertEqual(self.by["3.1(b)(1)"]["parent_locator"], "3.1(b)")
        self.assertIn("(b) Ratios.", self.by["3.1(b)(1)"]["context"])

    def test_definition_table_and_section_context(self):
        d = self.by["3.2(Tier 1 capital)"]
        self.assertEqual((d["kind"], d["defined_term"]), ("definition", "Tier 1 capital"))
        table = next(p for p in self.provs if p["kind"] == "table")
        self.assertIn("Buffer | Payout", table["text"])
        self.assertIn("Above 2.5 | None", table["text"])
        self.assertEqual(table["section_no"], "3.1")
        self.assertEqual(self.by["section-3.1"]["heading"], "§ 3.1 Purpose.")

    def test_offsets_point_into_source(self):
        p = self.by["3.1(a)"]
        self.assertTrue(ECFR_HTML[p["char_start"]:p["char_end"]].lstrip().startswith("<div id=\"p-3.1(a)\""))


class FedRegParserTests(unittest.TestCase):
    def test_preamble_headings_pages_and_passages(self):
        passages, units, stats = parse_fr(FR_PRORULE, "2026-99999")
        pre = [p for p in passages if p["kind"] == "preamble"]
        self.assertTrue(any(p["heading"] == "I. Background > A. Detail" and "More detail" in p["text"] for p in pre))
        self.assertEqual(next(p for p in pre if "agency proposes" in p["text"])["fr_page"], "100")

    def test_instruction_pairs_with_section_named_not_by_position(self):
        _, units, _ = parse_fr(FR_PRORULE, "2026-99999")
        s1 = next(u for u in units if u["section_no"] == "3.1")
        self.assertTrue(s1["instruction"].startswith("2. Amend § 3.1 by:"))
        self.assertIn("Revising paragraph (a)", s1["instruction"])  # lettered sub-step merged
        self.assertEqual(s1["instruction_sections"], ["3.1"])
        s2 = next(u for u in units if u["section_no"] == "3.2")
        self.assertIn("3. Amend § 3.2", s2["instruction"])
        self.assertEqual(s2["paragraph_leads"], ["(a)"])
        self.assertIn("add", s2["operations"])
        auth = next(u for u in units if u["subject"] == "Authority citation")
        self.assertIn("12 U.S.C. 93a", auth["text"])

    def test_regtext_unit_and_default_title(self):
        _, units, stats = parse_fr(FR_RULE, "2026-88888")
        self.assertEqual(len(units), 1)
        u = units[0]
        self.assertEqual((u["title_no"], u["part_no"], u["section_no"]), ("12", "3", "3.1"))
        self.assertIn("revise", u["operations"])
        self.assertEqual(stats["root"], "RULE")


class LifecycleTests(unittest.TestCase):
    def row(self, **kw):
        base = {"document_type": "Rule", "title": "T", "action": "Final rule.", "effective_on": None, "comments_close_on": None}
        return {**base, **kw}

    def test_labels_depend_on_as_of_and_never_promote_proposals(self):
        final = self.row(effective_on="2027-01-18")
        self.assertEqual(search.lifecycle(final, "2026-10-29")["state"], "final_effective_date_pending")
        self.assertEqual(search.lifecycle(final, "2027-02-01")["state"], "final_effective_date_passed_operative_effect_unreviewed")
        prop = self.row(document_type="Proposed Rule", comments_close_on="2026-11-23")
        self.assertEqual(search.lifecycle(prop, "2026-10-29")["state"], "proposed_comment_period_open")
        self.assertEqual(search.lifecycle(prop, "2026-12-01")["state"], "proposed_comment_period_ended_disposition_unreviewed")
        self.assertTrue(search.lifecycle(prop, "2027-06-01")["caveat"])
        self.assertEqual(search.lifecycle(self.row(title="Rules; Correction"), "2026-10-29")["state"], "correction_of_prior_document")
        self.assertEqual(search.lifecycle(self.row(document_type="Notice"), "2026-10-29")["state"], "notice_not_a_rule_state")


def asset(root, name, data, **meta):
    (root / "raw").mkdir(exist_ok=True)
    path = root / "raw" / name
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return {"path": f"raw/{name}", "sha256": digest, "bytes": len(data), "retrieved_at": "2026-10-29T00:00:00+00:00"}


class BuildSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "corpus"
        root.mkdir()
        docs = {
            "ecfr:12:3": {"source_class": "regulation", "publisher": "eCFR", "citation": "12 CFR Part 3", "title": "Capital",
                          "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                          "asset": asset(root, "e.html", ECFR_HTML.encode())},
            "fr:2026-99999": {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "document_number": "2026-99999",
                              "title": "Capital proposal", "document_type": "Proposed Rule", "comments_close_on": "2026-11-23",
                              "publication_date": "2026-09-22", "regulation_id_numbers": ["1557-AF99"],
                              "asset": asset(root, "p.xml", FR_PRORULE)},
            "fr:2026-88888": {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "document_number": "2026-88888",
                              "title": "Capital final", "document_type": "Rule", "effective_on": "2027-01-18", "action": "Final rule.",
                              "publication_date": "2026-10-23", "regulation_id_numbers": ["1557-AF99"],
                              "asset": asset(root, "r.xml", FR_RULE)},
        }
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        self.root = root
        self.db = Path(self.tmp.name) / "p1.sqlite"
        conn, self.counts = build.build(root, self.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        self.index = search.Index(str(self.db))

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_counts_and_relationships(self):
        self.assertEqual(self.counts["documents"], 3)
        self.assertEqual(self.counts["skipped"], [])
        rels = self.index.conn.execute("SELECT dst_citation, dst_provision_id FROM relationship WHERE rel_type='amends'").fetchall()
        self.assertIn("12 CFR 3.1", [r[0] for r in rels])
        self.assertTrue(all(r[1] is not None for r in rels if r[0] == "12 CFR 3.1"))  # resolved to acquired section
        links = self.index.conn.execute("SELECT * FROM rulemaking_link").fetchall()
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["review_status"], "candidate_unreviewed")

    def test_delta_keeps_instruments_separate_and_labels_status(self):
        out = self.index.delta("12 CFR 3.1")
        self.assertEqual(out["baseline"]["doc_key"], "ecfr:12:3")
        by = {g["doc_key"]: g for g in out["instruments"]}
        self.assertEqual(by["fr:2026-99999"]["status"]["state"], "proposed_comment_period_open")
        self.assertEqual(by["fr:2026-88888"]["status"]["state"], "final_effective_date_pending")
        self.assertEqual(by["fr:2026-88888"]["baseline_relation"], "effective_after_baseline_issue_date_not_in_baseline")
        self.assertEqual(out["applicability_status"], "not_assessed")
        self.assertEqual(search.Index(str(self.db), "2027-03-01").delta("12 CFR 3.1")["instruments"][1]["status"]["state"],
                         "final_effective_date_passed_operative_effect_unreviewed")

    def test_search_scopes_and_applicability(self):
        cur = self.index.answer("minimum capital", scope="current")
        self.assertEqual(cur["status"], "evidence_found")
        self.assertTrue(all(r["status"]["state"].startswith("current_baseline") for r in cur["results"]))
        fut = self.index.answer("revised minimum capital text", scope="future")
        self.assertTrue(fut["results"])
        self.assertFalse(any(r["status"]["state"].startswith("current") for r in fut["results"]))
        self.assertEqual(fut["applicability_status"], "not_assessed")

    def test_exact_citation_lookup(self):
        ids = self.index.exact("what does 12 CFR 3.1(b) say")
        loc = {self.index.evidence(pid)["locator"] for pid, _ in ids}
        self.assertIn("3.1(b)(1)", loc)

    def test_unacquired_topic_is_refused_and_mixed_is_partial(self):
        self.assertEqual(self.index.answer("CECL under ASC 326")["status"], "missing_evidence")
        mixed = self.index.answer("ASC 326 CECL and the community bank leverage ratio minimum capital")
        self.assertEqual(mixed["status"], "partial_evidence")
        self.assertIn("gaap_asc", [g["topic"] for g in mixed["coverage_gaps"]])

    def test_hash_mismatch_is_skipped_not_indexed(self):
        manifest = json.loads((self.root / "manifest.json").read_text())
        (self.root / "raw" / "e.html").write_bytes(b"tampered")
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        conn, counts = build.build(self.root, Path(self.tmp.name) / "again.sqlite", "2026-10-29", log=lambda *_: None)
        conn.close()
        self.assertEqual([s["doc_key"] for s in counts["skipped"]], ["ecfr:12:3"])
        self.assertEqual(counts["documents"], 2)


if __name__ == "__main__":
    unittest.main()
