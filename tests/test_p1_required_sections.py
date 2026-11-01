"""Tests for S7-P1-REQUIRED-SECTIONS-32: the index-source availability guard extends from whole parts to the sections a topic's
coverage note names (regintel_p1/topic_dependencies.json `required_sections`, e.g. cbl_ratio: 12 CFR 3.12, 217.12, 324.12). An indexed
document that lacks a named section persists a `missing_required_section` gap (doc_key `<document>#<section>`), the matched topic
reports it in answer coverage gaps and is never evidence_found. Section presence only: not completeness, applicability or validity."""
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, evaluate, search
from test_p1 import asset
from test_p1_index_source_coverage import CAPITAL, ROOT, cbl_rule, part_html

CBL = "community bank leverage ratio"
MODES = ("hybrid", "bm25", "topic")
SCOPES = ("current", "future", "both")
PARTS = (None, ["12:3"], ["12:217"], ["12:324"])


def build_sections(tmp, drop=(), name="s"):
    """All four capital parts indexed; sections named in `drop` (e.g. '3.12') are removed from their part."""
    root = Path(tmp) / f"corpus-{name}"
    (root / "raw").mkdir(parents=True)
    docs = {}
    for part in CAPITAL:
        html = part_html(part)
        for sec in drop:
            if sec.split(".")[0] == part:
                start = html.index(f'<div class="section" id="{sec}">')
                end = html.index('<div class="section"', start + 10) if '<div class="section"' in html[start + 10:] else html.rindex("</div>")
                html = html[:start] + html[end:]
        docs[f"ecfr:12:{part}"] = {"source_class": "regulation", "publisher": "eCFR", "citation": f"12 CFR Part {part}", "title": "Capital",
                                   "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                                   "asset": asset(root, f"e{part}.html", html.encode())}
    docs["fr:2026-08298"] = cbl_rule(root)
    (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
    db = str(Path(tmp) / f"{name}.sqlite")
    conn, counts = build.build(root, db, "2026-10-29", log=lambda *_: None)
    conn.close()
    return search.Index(db), counts


class DeclarationTests(unittest.TestCase):
    def test_required_sections_are_declared_only_where_the_coverage_note_names_them(self):
        deps = json.loads((ROOT / "topic_dependencies.json").read_text())
        topics = {t["id"]: t for t in json.loads((ROOT / "topics.json").read_text())["topics"]}
        with_sections = {k: v["required_sections"] for k, v in deps["required_baseline_sources"].items() if v.get("required_sections")}
        self.assertEqual(with_sections, {"cbl_ratio": {"ecfr:12:3": ["3.12"], "ecfr:12:217": ["217.12"], "ecfr:12:324": ["324.12"]}})
        for tid, secs in with_sections.items():
            for key, names in secs.items():
                self.assertIn(key, deps["required_baseline_sources"][tid]["sources"])
                for n in names:
                    self.assertIn(n, topics[tid]["coverage_note"])  # named in the note, not invented
        self.assertIn("not establish", deps["note"])

    def test_undeclared_section_source_is_rejected(self):
        import sqlite3
        from unittest import mock
        bad = {"version": "x", "note": "x", "required_baseline_sources": {"cbl_ratio": {"sources": ["ecfr:12:3"], "required_sections": {"ecfr:12:217": ["217.12"]}}}}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "d.json"
            p.write_text(json.dumps(bad))
            conn = sqlite3.connect(":memory:")
            with mock.patch.object(build, "DEPENDENCIES_PATH", p):
                with self.assertRaises(ValueError):
                    build.record_source_status(conn, {"ecfr:12:3": ("eCFR", "indexed", None, 1, 1)}, {})


class SectionGapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = {"complete": build_sections(cls.tmp.name, (), "complete"),
                  "no3": build_sections(cls.tmp.name, ("3.12",), "no3"),
                  "no217_324": build_sections(cls.tmp.name, ("217.12", "324.12"), "no217_324")}

    @classmethod
    def tearDownClass(cls):
        for index, _ in cls.ix.values():
            index.conn.close()
        cls.tmp.cleanup()

    def gaps(self, out):
        return [g for g in out["coverage_gaps"] if g.get("coverage") == "source_unavailable"]

    def test_complete_build_has_no_section_gap(self):
        index, counts = self.ix["complete"]
        self.assertEqual((counts["unavailable_topic_sources"], index.source_gaps), (0, {}))
        out = index.answer(CBL, mode="hybrid", scope="current", k=10)
        self.assertEqual((self.gaps(out), out["status"]), ([], "evidence_found"))

    def test_missing_sections_are_persisted_with_key_status_and_reason(self):
        for variant, expect in (("no3", {"ecfr:12:3#3.12"}), ("no217_324", {"ecfr:12:217#217.12", "ecfr:12:324#324.12"})):
            index, _ = self.ix[variant]
            rows = index.conn.execute("SELECT topic_id, doc_key, status, reason FROM topic_source_gap").fetchall()
            self.assertEqual({r["doc_key"] for r in rows}, expect, variant)
            self.assertEqual({r["topic_id"] for r in rows}, {"cbl_ratio"}, variant)  # only the topic whose note names the section
            self.assertEqual({r["status"] for r in rows}, {"missing_required_section"}, variant)
            self.assertTrue(all(r["reason"] for r in rows))
            for key in ("ecfr:12:3", "ecfr:12:217", "ecfr:12:324"):  # the parts themselves stay indexed
                self.assertEqual(index.conn.execute("SELECT status FROM source_status WHERE doc_key=?", (key,)).fetchone()[0], "indexed")

    def test_matched_topic_with_a_missing_section_is_never_evidence_found(self):
        for variant, expect in (("no3", {"ecfr:12:3#3.12"}), ("no217_324", {"ecfr:12:217#217.12", "ecfr:12:324#324.12"})):
            index, _ = self.ix[variant]
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            out = index.answer(CBL, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (variant, mode, scope, parts, k)
                            gaps = self.gaps(out)
                            self.assertTrue(gaps, tag)
                            self.assertEqual({m["doc_key"] for g in gaps for m in g["unavailable_sources"]}, expect, tag)
                            self.assertNotEqual(out["status"], "evidence_found", tag)
                            self.assertEqual(out["applicability_status"], "not_assessed")

    def test_other_topics_and_provision_retrieval_are_unaffected(self):
        index, _ = self.ix["no3"]
        for q in ("minimum CET1 ratio", "capital conservation buffer"):
            out = index.answer(q, mode="hybrid", scope="current", k=10)
            self.assertEqual(self.gaps(out), [], q)
            self.assertEqual(out["status"], "evidence_found", q)
        cit = index.answer("12 CFR 3.10(a)", mode="exact", scope="current", k=1)
        self.assertEqual((cit["status"], cit["citations"][0]["resolved"]), ("evidence_found", True))
        r = index.resolve_citations("12 CFR 3.12", "current")[0]  # the absent section is not conjured up
        self.assertFalse(r["resolved"])

    def test_relationships_stay_unreviewed_and_validity_not_determined(self):
        for index, _ in self.ix.values():
            status = {r[0] for r in index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
            self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"})

    def test_evaluator_group_is_registered_and_fails_when_a_section_gap_is_hidden(self):
        from unittest import mock
        self.assertIn("required_sections", evaluate.REGRESSION_GROUPS)
        reg = json.loads((ROOT.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["required_sections"]], [f"RS0{n}" for n in range(1, 4)])
        degraded = next(x for x in reg["required_sections"] if x["kind"] == "degraded")
        self.assertTrue(evaluate.check_required_sections(self.ix["complete"][0], degraded)["passed"])
        with mock.patch.object(search.Index, "_load_source_gaps", lambda self_: {}):  # the starting behaviour: no diagnostics
            res = evaluate.check_required_sections(self.ix["complete"][0], degraded)
        self.assertFalse(res["passed"])


if __name__ == "__main__":
    unittest.main()
