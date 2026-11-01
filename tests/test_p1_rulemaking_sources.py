"""Tests for S7-P1-RULEMAKING-SOURCE-AVAILABILITY-33: the topic dependency guard covers the Federal Register rulemaking source the cbl_ratio
coverage note names (fr:2026-08298), declared separately from the eCFR baseline (regintel_p1/topic_dependencies.json
`required_rulemaking_sources`). A missing, hash/size-rejected, unsupported-format or empty-parser rule is persisted through
source_status/topic_source_gap, the answer names it as a rulemaking source, and a matched CBLR query is partial_evidence when results
remain and otherwise missing_evidence, whatever other instruments contribute. Availability only: not complete rulemaking coverage or operative effect."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import build, evaluate, search
from test_p1 import asset
from test_p1_delta_precision import fr
from test_p1_index_source_coverage import CAPITAL, ROOT, cbl_rule, part_html

KEY = "fr:2026-08298"
CBL = "community bank leverage ratio"
MODES = ("hybrid", "bm25", "topic")
SCOPES = ("current", "future", "both")
PARTS = (None, ["12:3"], ["12:217"], ["12:324"])
STATUS = {"omitted": "missing_from_manifest", "rejected": "skipped_hash_or_size_mismatch", "unsupported": "skipped_unsupported_format",
          "empty": "empty_no_usable_provisions"}
# An independent proposal about the same section: it is real rulemaking evidence that must not stand in for the missing final rule.
OTHER = "fr:2026-98002"
OTHER_XML = fr("PRORULE", "1. Amend &#xA7; 217.12 by revising paragraph (b).", [("217.12", ["(b) Proposed community bank leverage ratio alternative."])], part="217")


def build_rule(tmp, rule="complete", name="r", drop_part=None):
    """All capital parts plus the CBLR rule in the given state (complete | omitted | rejected | unsupported | empty) and an independent proposal."""
    root = Path(tmp) / f"corpus-{name}"
    (root / "raw").mkdir(parents=True)
    docs = {}
    for part in CAPITAL:
        if part == drop_part:
            continue
        docs[f"ecfr:12:{part}"] = {"source_class": "regulation", "publisher": "eCFR", "citation": f"12 CFR Part {part}", "title": "Capital",
                                   "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                                   "asset": asset(root, f"e{part}.html", part_html(part).encode())}
    if rule != "omitted":
        doc = cbl_rule(root)
        if rule == "empty":
            doc["asset"] = asset(root, "empty.xml", b"<RULE></RULE>")
        elif rule == "unsupported":
            doc["asset"] = asset(root, "rule.pdf", b"%PDF-1.4 not parsed")
        elif rule == "rejected":
            doc["asset"]["sha256"] = hashlib.sha256(b"different bytes").hexdigest()
        docs[KEY] = doc
    meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01", "document_type": "Proposed Rule",
            "comments_close_on": "2026-12-01", "action": "Proposed rule."}
    docs[OTHER] = {**meta, "document_number": "2026-98002", "title": "CBLR alternative", "asset": asset(root, "o.xml", OTHER_XML)}
    (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
    db = str(Path(tmp) / f"{name}.sqlite")
    conn, counts = build.build(root, db, "2026-10-29", log=lambda *_: None)
    conn.close()
    return search.Index(db), counts


class DeclarationTests(unittest.TestCase):
    def test_rulemaking_sources_are_declared_separately_from_the_baseline_with_the_coverage_note_as_provenance(self):
        deps = json.loads((ROOT / "topic_dependencies.json").read_text())
        topics = {t["id"]: t for t in json.loads((ROOT / "topics.json").read_text())["topics"]}
        declared = deps["required_rulemaking_sources"]
        self.assertEqual(set(declared), {"cbl_ratio"})
        self.assertEqual(declared["cbl_ratio"]["sources"], [KEY])
        self.assertEqual(declared["cbl_ratio"]["declared_from_coverage_note"], topics["cbl_ratio"]["coverage_note"])
        self.assertIn("2026-08298", topics["cbl_ratio"]["coverage_note"])
        for dep in deps["required_baseline_sources"].values():  # the eCFR baseline list holds no rulemaking source
            self.assertTrue(all(k.startswith("ecfr:") for k in dep["sources"]))
        self.assertIn("not that rulemaking coverage is complete", deps["note"])

    def test_non_federal_register_rulemaking_source_is_rejected(self):
        bad = {"version": "x", "note": "x", "required_baseline_sources": {}, "required_rulemaking_sources": {"cbl_ratio": {"sources": ["ecfr:12:3"]}}}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "d.json"
            p.write_text(json.dumps(bad))
            with mock.patch.object(build, "DEPENDENCIES_PATH", p):
                with self.assertRaises(ValueError):
                    build.record_source_status(sqlite3.connect(":memory:"), {}, {})


class RulemakingGapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix = {v: build_rule(cls.tmp.name, v, v) for v in ("complete", "omitted", "rejected", "unsupported", "empty")}

    @classmethod
    def tearDownClass(cls):
        for index, _ in cls.ix.values():
            index.conn.close()
        cls.tmp.cleanup()

    def gaps(self, out):
        return [g for g in out["coverage_gaps"] if g.get("coverage") == "source_unavailable"]

    def test_complete_build_has_no_gap_and_the_rule_is_indexed_with_usable_provisions(self):
        index, counts = self.ix["complete"]
        self.assertEqual((counts["unavailable_topic_sources"], index.source_gaps), (0, {}))
        st = index.conn.execute("SELECT status, usable_provisions FROM source_status WHERE doc_key=?", (KEY,)).fetchone()
        self.assertEqual(st["status"], "indexed")
        self.assertGreater(st["usable_provisions"], 0)
        for scope in SCOPES:
            out = index.answer(CBL, mode="hybrid", scope=scope, k=10)
            self.assertEqual(self.gaps(out), [], scope)
            if out["results"]:
                self.assertEqual(out["status"], "evidence_found", scope)

    def test_each_outcome_is_persisted_through_source_status_and_topic_source_gap(self):
        for variant, status in STATUS.items():
            index, counts = self.ix[variant]
            rows = index.conn.execute("SELECT topic_id, doc_key, status, reason FROM topic_source_gap").fetchall()
            self.assertEqual([(r["topic_id"], r["doc_key"], r["status"]) for r in rows], [("cbl_ratio", KEY, status)], variant)
            self.assertTrue(rows[0]["reason"], variant)
            self.assertEqual(counts["unavailable_topic_sources"], 1, variant)
            st = index.conn.execute("SELECT status, usable_provisions FROM source_status WHERE doc_key=?", (KEY,)).fetchone()
            self.assertEqual((st["status"], st["usable_provisions"]), (status, 0), variant)

    def test_matched_cbl_query_is_never_evidence_found_across_modes_scopes_filters_and_k1(self):
        for variant, status in STATUS.items():
            index, _ = self.ix[variant]
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            out = index.answer(CBL, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (variant, mode, scope, parts, k)
                            found = [m for g in self.gaps(out) for m in g["unavailable_sources"]]
                            self.assertEqual([(m["doc_key"], m["source_kind"], m["status"]) for m in found], [(KEY, "rulemaking", status)], tag)
                            self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence", tag)
                            self.assertEqual((out["applicability_status"], out["legal_validity"] if "legal_validity" in out else "not_determined"),
                                             ("not_assessed", "not_determined"), tag)

    def test_diagnostic_names_the_rulemaking_source_not_an_ecfr_baseline_source(self):
        for variant, status in STATUS.items():
            index, _ = self.ix[variant]
            gap = self.gaps(index.answer(CBL, mode="hybrid", scope="both", k=10))[0]
            self.assertIn("Federal Register rulemaking source", gap["note"], variant)
            self.assertIn(f"{KEY}: {status}", gap["note"], variant)
            self.assertNotIn("eCFR baseline", gap["note"], variant)
            self.assertIn("not provision coverage, complete rulemaking coverage, operative effect or applicability", gap["note"], variant)

    def test_another_instrument_does_not_conceal_the_gap(self):
        for variant in STATUS:
            index, _ = self.ix[variant]
            out = index.answer(CBL, mode="hybrid", scope="future", k=10)
            self.assertIn(OTHER, {r["doc_key"] for r in out["results"]}, variant)  # the independent proposal is returned
            self.assertEqual(out["status"], "partial_evidence", variant)           # and does not stand in for the missing final rule
            self.assertEqual([m["doc_key"] for g in self.gaps(out) for m in g["unavailable_sources"]], [KEY], variant)

    def test_baseline_and_rulemaking_gaps_are_reported_together_with_their_own_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            index, counts = build_rule(tmp, "omitted", "both", drop_part="324")
            self.assertEqual(counts["unavailable_topic_sources"], 6 + 1)  # six topics depend on ecfr:12:324, plus the CBLR rule
            kinds = {(m["doc_key"], m["source_kind"]) for m in index.source_gaps["cbl_ratio"]}
            self.assertEqual(kinds, {("ecfr:12:324", "ecfr_baseline"), (KEY, "rulemaking")})
            note = next(g for g in self.gaps(index.answer(CBL, mode="hybrid", scope="current", k=10)) if g["topic"] == "cbl_ratio")["note"]
            self.assertIn("eCFR baseline source or section", note)
            self.assertIn("Federal Register rulemaking source", note)
            index.conn.close()

    def test_other_topics_exact_citations_and_independent_delta_instruments_are_unchanged(self):
        complete, _ = self.ix["complete"]
        for variant in STATUS:
            index, _ = self.ix[variant]
            for q in ("minimum CET1 ratio", "capital conservation buffer"):
                out = index.answer(q, mode="hybrid", scope="current", k=10)
                self.assertEqual(self.gaps(out), [], (variant, q))
                self.assertEqual(out["status"], "evidence_found", (variant, q))
            for mode in ("exact", "hybrid"):
                cit = index.answer("12 CFR 217.10(a)", mode=mode, scope="current", k=1)
                self.assertEqual((cit["status"], cit["citations"][0]["resolved"]), ("evidence_found", True), (variant, mode))
            surviving = index.resolve_citations("12 CFR 217.12", "current")[0]  # the baseline section survives; only the rule is unavailable
            self.assertTrue(surviving["resolved"], variant)
            d = index.delta("12 CFR 217.12")
            self.assertIn(OTHER, [g["doc_key"] for g in d["instruments"]], variant)
            self.assertNotIn(KEY, [g["doc_key"] for g in d["instruments"]], variant)  # the unavailable rule is not conjured up
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"))
            self.assertEqual(index.answer("CECL under ASC 326")["status"], "missing_evidence")
        d_complete = complete.delta("12 CFR 217.12")
        self.assertEqual({g["doc_key"] for g in d_complete["instruments"]}, {KEY, OTHER})

    def test_relationships_stay_unreviewed(self):
        for variant, (index, _) in self.ix.items():
            status = {r[0] for r in index.conn.execute("SELECT DISTINCT review_status FROM relationship")}
            self.assertTrue(status <= {"parsed_unreviewed", "regex_unreviewed"}, variant)

    def test_evaluator_group_is_registered_and_fails_when_the_rulemaking_gap_is_hidden(self):
        self.assertIn("rulemaking_sources", evaluate.REGRESSION_GROUPS)
        reg = json.loads((ROOT.parent / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["rulemaking_sources"]], ["RM01", "RM02", "RM03", "RM04"])
        index = self.ix["complete"][0]
        for x in reg["rulemaking_sources"]:
            if x["kind"] != "corpus":
                self.assertTrue(evaluate.check_rulemaking_sources(index, x)["passed"], x["id"])
        degraded = next(x for x in reg["rulemaking_sources"] if x["id"] == "RM02")
        with mock.patch.object(search.Index, "_load_source_gaps", lambda self_: {}):  # the starting behaviour: no diagnostics
            self.assertFalse(evaluate.check_rulemaking_sources(index, degraded)["passed"])


if __name__ == "__main__":
    unittest.main()
