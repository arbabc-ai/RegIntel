"""Tests for S7-P1-INDEX-SOURCE-COVERAGE-31 (F1): the eCFR baseline sources that topics declared `acquired` depend on
(regintel_p1/topic_dependencies.json, separate from the keyword rules and the declared coverage) are reconciled at build time against
documents that were verified, parsed and indexed with usable provisions. A missing, hash/size-rejected or empty dependency is
persisted with its reason and surfaced in answer coverage gaps; the matched topic then yields partial_evidence (or
missing_evidence when nothing remains) however many hits other agencies' parts contribute. This is source availability only."""
import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, search
from test_p1 import asset
from test_p1_delta_precision import fr

ROOT = Path(__file__).resolve().parent.parent / "regintel_p1"
CAPITAL = ("3", "217", "324", "252")
TOPIC_QUERIES = ("minimum CET1 ratio", "common equity tier 1 capital ratio requirement", "capital conservation buffer")
MODES = ("hybrid", "bm25", "topic")
SCOPES = ("current", "future", "both")
PARTS = (None, ["12:3"], ["12:217"], ["12:324"])


def part_html(part):
    return (f'<div class="part" id="part-{part}"><h1>PART {part}—CAPITAL</h1><div class="section" id="{part}.10"><h4>§ {part}.10 Minimum capital requirements.</h4>'
            f'<div id="p-{part}.10(a)"><p class="indent-1" data-title="{part}.10(a)">(a) A bank must maintain a common equity tier 1 capital ratio '
            f'(CET1) of at least 4.5 percent under part {part}, and a capital conservation buffer.</p></div></div>'
            f'<div class="section" id="{part}.12"><h4>§ {part}.12 Community bank leverage ratio framework.</h4><div id="p-{part}.12(a)"><p class="indent-1" data-title="{part}.12(a)">(a) Filler paragraph of part {part}.</p></div></div>'
            f'<div class="section" id="{part}.1"><h4>§ {part}.1 Purpose.</h4><div id="p-{part}.1(a)"><p class="indent-1" data-title="{part}.1(a)">(a) Purpose of part {part}.</p></div></div></div>')


FR_217 = fr("RULE", "1. Amend &#xA7; 217.10 by revising paragraph (a) to read as follows:", [("217.10", ["(a) Revised CET1 minimum text."])], part="217")


def build_variant(tmp, variant, name="v"):
    """variant: complete | omitted (ecfr:12:3 absent from the manifest) | rejected (ecfr:12:3 bytes do not match) | empty (ecfr:12:3 parses to nothing)."""
    root = Path(tmp) / f"corpus-{name}"
    (root / "raw").mkdir(parents=True)
    docs = {}
    for part in CAPITAL:
        key = f"ecfr:12:{part}"
        if part == "3" and variant == "omitted":
            continue
        html = "<div></div>" if (part == "3" and variant == "empty") else part_html(part)
        a = asset(root, f"e{part}.html", html.encode())
        if part == "3" and variant == "rejected":
            a["sha256"] = hashlib.sha256(b"different bytes").hexdigest()
        docs[key] = {"source_class": "regulation", "publisher": "eCFR", "citation": f"12 CFR Part {part}", "title": "Capital",
                     "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28", "asset": a}
    meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01", "document_type": "Rule",
            "effective_on": "2027-02-01", "action": "Final rule."}
    docs["fr:2026-98001"] = {**meta, "document_number": "2026-98001", "title": "CET1 revision", "asset": asset(root, "f.xml", FR_217)}
    (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
    db = str(Path(tmp) / f"{name}.sqlite")
    conn, counts = build.build(root, db, "2026-10-29", log=lambda *_: None)
    conn.close()
    return search.Index(db), counts


class DeclarationTests(unittest.TestCase):
    def test_dependencies_are_a_separate_declaration_from_rules_and_coverage(self):
        deps = json.loads((ROOT / "topic_dependencies.json").read_text())
        topics = {t["id"]: t for t in json.loads((ROOT / "topics.json").read_text())["topics"]}
        self.assertNotIn("required_baseline_sources", json.dumps(topics))  # nothing was added to the topic catalog
        declared = deps["required_baseline_sources"]
        acquired = {k for k, t in topics.items() if t["coverage"] == "acquired"}
        self.assertEqual(set(declared), acquired)  # every acquired topic, and only acquired topics
        for tid, d in declared.items():
            self.assertEqual(d["declared_from_coverage_note"], topics[tid]["coverage_note"])
            for key in d["sources"]:
                self.assertRegex(key, r"^ecfr:\d+:\d+$")
                part = key.split(":")[2]
                self.assertTrue(re.search(rf"(?<![\d.]){part}(?![\d])", topics[tid]["coverage_note"]), (tid, key))  # named in the topic's note
        self.assertIn("source availability only", deps["note"])

    def test_every_dependency_is_indexed_in_the_complete_corpus_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            index, counts = build_variant(tmp, "complete")
            self.assertEqual(counts["unavailable_topic_sources"], 0)
            self.assertEqual(index.source_gaps, {})
            rows = {r[0]: r[1] for r in index.conn.execute("SELECT doc_key, status FROM source_status")}
            self.assertEqual({k: v for k, v in rows.items() if k.startswith("ecfr")}, {f"ecfr:12:{p}": "indexed" for p in CAPITAL})
            index.conn.close()


class DegradedBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = {v: build_variant(cls.tmp.name, v, v) for v in ("complete", "omitted", "rejected", "empty")}

    @classmethod
    def tearDownClass(cls):
        for index, _ in cls.ix.values():
            index.conn.close()
        cls.tmp.cleanup()

    def source_gaps(self, out):
        return [g for g in out["coverage_gaps"] if g.get("coverage") == "source_unavailable"]

    def test_unavailable_dependencies_are_persisted_with_key_status_and_reason(self):
        expect = {"omitted": "missing_from_manifest", "rejected": "skipped_hash_or_size_mismatch", "empty": "empty_no_usable_provisions"}
        for variant, status in expect.items():
            index, counts = self.ix[variant]
            self.assertGreater(counts["unavailable_topic_sources"], 0, variant)
            rows = index.conn.execute("SELECT topic_id, doc_key, status, reason FROM topic_source_gap").fetchall()
            self.assertEqual({r["doc_key"] for r in rows}, {"ecfr:12:3"}, variant)
            self.assertEqual({r["status"] for r in rows}, {status}, variant)
            self.assertTrue(all(r["reason"] for r in rows), variant)
            self.assertEqual({r["topic_id"] for r in rows},
                             {"cet1_capital", "tier1_tier2_capital", "risk_weighted_assets", "leverage_ratio", "cbl_ratio", "capital_buffers"}, variant)
            st = index.conn.execute("SELECT status, usable_provisions FROM source_status WHERE doc_key='ecfr:12:3'").fetchone()
            self.assertEqual(st["status"], status)
            self.assertEqual(st["usable_provisions"], 0)
        self.assertEqual(self.ix["rejected"][1]["skipped"], [{"doc_key": "ecfr:12:3", "reason": "hash_or_size_mismatch"}])

    def test_complete_baseline_keeps_evidence_found_with_no_source_gap(self):
        index, _ = self.ix["complete"]
        for q in TOPIC_QUERIES:
            for mode in MODES:
                for scope in ("current", "both"):
                    for parts in (None, ["12:3"], ["12:217"]):
                        for k in (10, 1):
                            out = index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (q, mode, scope, parts, k)
                            self.assertEqual(self.source_gaps(out), [], tag)
                            self.assertEqual(out["index"]["unavailable_topic_sources"], {}, tag)
                            if out["results"]:
                                self.assertEqual(out["status"], "evidence_found", tag)

    def test_a_matched_topic_with_an_unavailable_source_is_never_evidence_found(self):
        for variant in ("omitted", "rejected", "empty"):
            index, _ = self.ix[variant]
            for q in TOPIC_QUERIES:
                for mode in MODES:
                    for scope in SCOPES:
                        for parts in PARTS:
                            for k in (10, 1):
                                out = index.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                                tag = (variant, q, mode, scope, parts, k)
                                gaps = self.source_gaps(out)
                                self.assertTrue(gaps, tag)
                                self.assertEqual({m["doc_key"] for g in gaps for m in g["unavailable_sources"]}, {"ecfr:12:3"}, tag)
                                self.assertNotEqual(out["status"], "evidence_found", tag)
                                self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence", tag)
                                self.assertEqual(out["applicability_status"], "not_assessed")

    def test_other_agencies_hits_do_not_conceal_the_unavailable_source(self):
        for variant in ("omitted", "rejected", "empty"):
            index, _ = self.ix[variant]
            out = index.answer("minimum CET1 ratio", mode="hybrid", scope="current", parts=["12:217"], k=10)
            self.assertTrue(out["results"] and all(r["doc_key"] == "ecfr:12:217" for r in out["results"]), variant)
            self.assertEqual(out["status"], "partial_evidence", variant)  # evidence from part 217 is present, and the gap for part 3 stays
            only3 = index.answer("minimum CET1 ratio", mode="hybrid", scope="current", parts=["12:3"], k=10)
            self.assertEqual((only3["results"], only3["status"]), ([], "missing_evidence"), variant)

    def test_exact_citation_precision_current_future_separation_and_delta_are_unchanged(self):
        for variant in ("complete", "omitted", "rejected", "empty"):
            index, _ = self.ix[variant]
            for mode in ("exact", "hybrid"):
                out = index.answer("12 CFR 217.10(a)", mode=mode, scope="current", k=1)
                self.assertEqual((out["status"], out["citations"][0]["resolved"], [g for g in out["coverage_gaps"] if g["topic"].startswith("citation:")]),
                                 ("evidence_found", True, []), (variant, mode))
                fut = index.answer("12 CFR 217.10(a)", mode=mode, scope="future", k=1)
                self.assertEqual(fut["status"], "evidence_found", (variant, mode))
                self.assertTrue(all(r["doc_key"].startswith("fr:") for r in fut["results"]), (variant, mode))
            d = index.delta("12 CFR 217.10")
            self.assertEqual([g["doc_key"] for g in d["instruments"]], ["fr:2026-98001"], variant)  # independent instrument unchanged
            self.assertEqual(d["baseline"]["doc_key"], "ecfr:12:217", variant)
            r3 = index.resolve_citations("12 CFR 3.10", "current")[0]
            self.assertEqual(r3["resolved"], variant == "complete", variant)  # the unavailable part is not conjured up
            if variant != "complete":
                self.assertEqual(r3["reason"], "part_not_acquired")
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))

    def test_topics_without_declared_dependencies_are_unaffected(self):
        index, _ = self.ix["omitted"]
        for q in ("CECL under ASC 326", "customer identification program"):
            out = index.answer(q, mode="hybrid", scope="current", k=10)
            self.assertEqual(self.source_gaps(out), [], q)
        self.assertEqual(index.answer("CECL under ASC 326")["status"], "missing_evidence")

    def test_relationships_stay_unreviewed(self):
        for variant, (index, _) in self.ix.items():
            status = {r[0] for r in index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
            self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, (variant, status))

    def test_evaluator_group_is_registered_and_fails_when_a_dependency_gap_is_hidden(self):
        from unittest import mock

        from regintel_p1 import evaluate
        self.assertIn("source_coverage", evaluate.REGRESSION_GROUPS)
        reg = json.loads((ROOT.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["source_coverage"]], [f"SC0{n}" for n in range(1, 6)])
        for x in reg["source_coverage"]:
            if x["kind"] == "degraded":
                self.assertTrue(evaluate.check_source_coverage(self.ix["complete"][0], x)["passed"], x["id"])
        degraded = next(x for x in reg["source_coverage"] if x["kind"] == "degraded")
        with mock.patch.object(search.Index, "_load_source_gaps", lambda self_: {}):  # the starting behaviour: no dependency diagnostics
            self.assertFalse(evaluate.check_source_coverage(self.ix["complete"][0], degraded)["passed"])


CORPUS = Path("/home/arbab/projects/regintel-corpus/phase1")


@unittest.skipUnless((CORPUS / "manifest.json").exists(), "public corpus not present")
class PublicCorpusTests(unittest.TestCase):
    def test_the_complete_public_corpus_has_every_dependency_and_unchanged_topic_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "p.sqlite")
            conn, counts = build.build(CORPUS, db, "2026-10-29", log=lambda *_: None)
            conn.close()
            index = search.Index(db)
            self.assertEqual((counts["unavailable_topic_sources"], counts["skipped"]), (0, []))
            self.assertEqual(index.source_gaps, {})
            self.assertEqual({r[0] for r in index.conn.execute("SELECT status FROM source_status")}, {"indexed"})
            for mode in ("hybrid", "bm25"):
                out = index.answer("minimum CET1 ratio", mode=mode, scope="current", k=10)
                self.assertEqual(out["status"], "evidence_found", mode)
                self.assertEqual([g for g in out["coverage_gaps"] if g.get("coverage") == "source_unavailable"], [], mode)
            index.conn.close()

    def test_a_derived_manifest_with_one_replaced_hash_degrades_without_touching_the_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = hashlib.sha256((CORPUS / "manifest.json").read_bytes()).hexdigest()
            derived = Path(tmp) / "derived"
            derived.mkdir()
            m = json.loads((CORPUS / "manifest.json").read_text())
            m["documents"]["ecfr:12:3"]["asset"]["sha256"] = hashlib.sha256(b"x").hexdigest()
            for key, d in m["documents"].items():
                src = CORPUS / d["asset"]["path"]
                dst = derived / d["asset"]["path"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
            (derived / "manifest.json").write_text(json.dumps(m))
            db = str(Path(tmp) / "d.sqlite")
            conn, counts = build.build(derived, db, "2026-10-29", log=lambda *_: None)
            conn.close()
            index = search.Index(db)
            for mode in ("hybrid", "bm25"):
                out = index.answer("minimum CET1 ratio", mode=mode, scope="current", k=1)
                self.assertEqual(out["status"], "partial_evidence", mode)  # other parts still return hits
                self.assertTrue([g for g in out["coverage_gaps"] if g.get("coverage") == "source_unavailable"], mode)
            index.conn.close()
            self.assertEqual(hashlib.sha256((CORPUS / "manifest.json").read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
