"""Tests for S7-P1-78 (C3 F-77-1, F-77-2, F-77-3): the `stored` branch of the triple-letter regression check is never vacuous (a document the
index holds at another locator fails; an absent document is counted as skipped), the delta checks report how many expectations they checked
and skipped, and build.py and the attribution diff script share one amendment-locator derivation. Governing scope: founder record
2026-11-04_s7-regintel-p1-founder-decisions_accepted.md."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, evaluate, fedreg
from scripts import p1_paragraph_attribution_diff_77 as diff_script
from test_p1 import asset
from test_p1_appendix_evidence import APP_XML
from test_p1_triple_letter_paragraph_list import KEY, build_index

REGRESSIONS = Path(__file__).resolve().parent.parent / "eval/phase1/regressions.json"


def tl(item_id):
    return next(x for x in json.loads(REGRESSIONS.read_text())["triple_letter_paragraph_list"] if x["id"] == item_id)


class StoredBranchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = build_index(cls.tmp.name)  # the task-77 synthetic index: fr:2026-97001..97003, none of the acquired documents
        cls.locator = cls.ix.conn.execute(
            "SELECT p.locator FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment'",
            (KEY,)).fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        cls.ix.conn.close()
        cls.tmp.cleanup()

    def test_an_indexed_document_at_another_locator_fails(self):
        wrong = self.locator.rsplit(":", 1)[0] + ":99"
        self.assertNotEqual(wrong, self.locator)
        item = {"id": "TLX", "kind": "stored", "units": [{"doc_key": KEY, "locator": wrong, "paths": ["(e)"]}]}
        out = evaluate.check_triple_letter_paragraph_list(self.ix, item)
        self.assertFalse(out["passed"])
        self.assertEqual(out["failures"], [f"{wrong}: document indexed but locator not found"])
        self.assertEqual((out["units_checked"], out["units_skipped"]), (1, 0))

    def test_the_right_locator_still_passes_and_is_counted(self):
        item = {"id": "TLY", "kind": "stored",
                "units": [{"doc_key": KEY, "locator": self.locator, "paths": ["(e)", "(r)", "(nnn)", "(ooo)", "(xxx)"]}]}
        out = evaluate.check_triple_letter_paragraph_list(self.ix, item)
        self.assertEqual((out["passed"], out["units_checked"], out["units_skipped"]), (True, 1, 0))

    def test_tl03_on_the_task_77_synthetic_index_checks_nothing_and_says_so(self):
        out = evaluate.check_triple_letter_paragraph_list(self.ix, tl("TL03"))
        self.assertEqual((out["passed"], out["units_checked"], out["units_skipped"]), (True, 0, 4))

    def test_delta_expectations_for_absent_documents_are_counted_as_skipped(self):
        out = evaluate.check_triple_letter_paragraph_list(self.ix, tl("TL01"))  # both deltas name fr:2026-06963, absent here
        self.assertEqual((out["passed"], out["deltas_checked"], out["deltas_skipped"]), (True, 0, 2))
        neg = evaluate.check_triple_letter_paragraph_list(self.ix, tl("TL02"))
        self.assertEqual((neg["deltas_checked"], neg["deltas_skipped"]), (0, 0))


class SharedLocatorTests(unittest.TestCase):
    def test_build_and_the_diff_script_derive_the_same_locators(self):
        triple = (b"<PRORULE><PREAMB><CFR>31 CFR Part 1010</CFR></PREAMB><SUPLINF><REGTEXT TITLE=\"31\" PART=\"1010\"><AMDPAR>1. Amend "
                  b"\xc2\xa7 1010.100 by adding paragraphs (nnn) and (ooo).</AMDPAR></REGTEXT><REGTEXT TITLE=\"31\" PART=\"1010\"><AMDPAR>2. "
                  b"Amend \xc2\xa7 1010.230 by revising paragraph (c).</AMDPAR></REGTEXT></SUPLINF></PRORULE>")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            (root / "raw").mkdir(parents=True)
            docs = {}
            for key, xml in (("fr:2026-93001", APP_XML), ("fr:2026-97101", triple)):
                docs[key] = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-04-10",
                             "document_type": "Rule", "document_number": key.split(":")[1], "title": "t",
                             "asset": asset(root, key.replace(":", "_") + ".xml", xml)}
            (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
            dumped = diff_script.dump(root / "manifest.json")["units"]
            db = str(Path(tmp) / "i.sqlite")
            conn, _ = build.build(root, db, "2026-10-29", log=lambda *_: None)
            conn.close()
            indexed = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                for key, xml in (("fr:2026-93001", APP_XML), ("fr:2026-97101", triple)):
                    units = fedreg.parse_fr(xml, key.split(":")[1])[1]
                    expected = build.amendment_locators(units)
                    self.assertEqual(len(expected), len(set(expected)), key)
                    self.assertEqual(sorted(k for k, v in dumped.items() if v["doc_key"] == key), sorted(expected), key)
                    stored = [r[0] for r in indexed.execute(
                        "SELECT p.locator FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key=? AND p.kind='amendment'",
                        (key,))]
                    self.assertEqual(sorted(stored), sorted(expected), key)
                appendix = [x for x in build.amendment_locators(fedreg.parse_fr(APP_XML, "2026-93001")[1]) if ":appx-" in x]
                self.assertTrue(appendix)  # the appendix-text branch is exercised
            finally:
                indexed.close()


if __name__ == "__main__":
    unittest.main()
