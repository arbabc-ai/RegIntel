"""Tests for S7-P1-UNVERIFIED-SOURCE-DIAGNOSTICS-35: when dependency_verification.status is unverified, persisted topic_source_gap rows are
not promoted to confirmed `source_unavailable` gaps or to index.unavailable_topic_sources. They are kept as explicitly unverified
observations (confirmed=False, with the recorded verification provenance) inside the `dependencies_unverified` gap. Verified degraded
indexes keep their confirmed diagnostics and verified complete indexes are unchanged. Indexes are never modified."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import evaluate, search
from test_p1_dependency_verification import CBL, MODES, PARTS, SCOPES, build_index, edit, gaps
from test_p1_index_source_coverage import ROOT
from test_p1_required_sections import build_sections
from test_p1_rulemaking_sources import build_rule

ALL_META = "('dependency_check_contract','topic_dependencies_sha256','topics_sha256')"
TAMPERS = {
    "missing_metadata": (f"DELETE FROM index_meta WHERE key IN {ALL_META};", "verification_metadata_missing"),
    "incomplete_metadata": ("DELETE FROM index_meta WHERE key='topic_dependencies_sha256';", "verification_metadata_incomplete"),
    "obsolete_contract": ("UPDATE index_meta SET value='p1-dependency-check-0' WHERE key='dependency_check_contract';", "obsolete_dependency_check_contract"),
    "mismatched_topics": (f"UPDATE index_meta SET value='{'0' * 64}' WHERE key='topics_sha256';", "dependency_fingerprint_mismatch"),
    "mismatched_dependencies": (f"UPDATE index_meta SET value='{'f' * 64}' WHERE key='topic_dependencies_sha256';", "dependency_fingerprint_mismatch"),
    "absent_source_status": ("DROP TABLE source_status;", "dependency_tables_absent"),
}
# name -> (builder, doc_key reported as a confirmed gap on the verified build)
SOURCES = {
    "ecfr_part": (lambda tmp, n: build_rule(tmp, "complete", n, drop_part="324")[0], "ecfr:12:324"),
    "required_section": (lambda tmp, n: build_sections(tmp, ("3.12",), n)[0], "ecfr:12:3#3.12"),
    "rulemaking": (lambda tmp, n: build_rule(tmp, "omitted", n)[0], "fr:2026-08298"),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReproductionTests(unittest.TestCase):
    def test_reported_case_no_longer_promotes_persisted_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = build_index(tmp, "degraded", with_rule=False)
            edit(db, f"DELETE FROM index_meta WHERE key IN {ALL_META};")
            ix = search.Index(db)
            out = ix.answer(CBL, mode="hybrid", scope="current", k=1)
            self.assertEqual(gaps(out, "source_unavailable"), [])
            cbl = [g for g in gaps(out, "dependencies_unverified") if g["topic"] == "cbl_ratio"]
            self.assertEqual(len(cbl), 1)
            self.assertEqual([(o["doc_key"], o["confirmed"], o["unverified_reason"]) for o in cbl[0]["unverified_source_observations"]],
                             [("fr:2026-08298", False, "verification_metadata_missing")])
            self.assertEqual(out["index"]["unavailable_topic_sources"], {})
            self.assertIn("cbl_ratio", out["index"]["unverified_topic_source_observations"])
            self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence")
            ix.conn.close()


class VerifiedBehaviorTests(unittest.TestCase):
    def test_verified_degraded_keeps_confirmed_diagnostics_and_complete_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, (build_fn, key) in SOURCES.items():
                ix = build_fn(tmp, name)
                self.assertEqual(ix.dependency_verification["status"], "verified", name)
                out = ix.answer(CBL, mode="hybrid", scope="both", k=10)
                self.assertEqual({m["doc_key"] for g in gaps(out, "source_unavailable") for m in g["unavailable_sources"]} & {key}, {key}, name)
                self.assertEqual(gaps(out, "dependencies_unverified"), [], name)
                self.assertIn("cbl_ratio", out["index"]["unavailable_topic_sources"], name)
                self.assertEqual(out["index"]["unverified_topic_source_observations"], {}, name)
                self.assertEqual(ix.unverified_source_observations, {}, name)
                ix.conn.close()
            complete, _ = build_rule(tmp, "complete", "complete")
            out = complete.answer(CBL, mode="hybrid", scope="current", k=10)
            self.assertEqual((out["status"], out["coverage_gaps"]), ("evidence_found", []))
            self.assertEqual(out["index"]["unavailable_topic_sources"], {})
            complete.conn.close()


class UnverifiedPersistedGapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix, cls.reason, cls.key, cls.before = {}, {}, {}, {}
        for sname, (build_fn, key) in SOURCES.items():
            for tname, (sql, reason) in TAMPERS.items():
                n = f"{sname}__{tname}"
                ix = build_fn(cls.tmp.name, n)
                db = ix.db_path
                ix.conn.close()
                edit(db, sql)
                cls.before[n] = sha(db)
                cls.ix[n], cls.reason[n], cls.key[n] = search.Index(db), reason, key

    @classmethod
    def tearDownClass(cls):
        for ix in cls.ix.values():
            ix.conn.close()
        cls.tmp.cleanup()

    def test_persisted_rows_exist_and_are_kept_as_unverified_observations(self):
        for n, ix in self.ix.items():
            rows = ix.conn.execute("SELECT COUNT(*) FROM topic_source_gap").fetchone()[0]
            self.assertGreater(rows, 0, n)  # a nonempty persisted gap table
            self.assertEqual((ix.dependency_verification["status"], ix.dependency_verification["reason"]), ("unverified", self.reason[n]), n)
            self.assertEqual(ix.source_gaps, {}, n)
            kept = {o["doc_key"] for obs in ix.unverified_source_observations.values() for o in obs}
            self.assertIn(self.key[n], kept, n)
            for obs in ix.unverified_source_observations.values():
                for o in obs:
                    self.assertFalse(o["confirmed"], n)
                    self.assertEqual(o["unverified_reason"], self.reason[n], n)
                    self.assertIn("recorded_verification", o, n)

    def test_no_persisted_row_becomes_a_confirmed_gap_across_modes_scopes_filters_and_k(self):
        for n, ix in self.ix.items():
            for mode in MODES:
                for scope in SCOPES:
                    for parts in PARTS:
                        for k in (10, 1):
                            out = ix.answer(CBL, mode=mode, scope=scope, parts=parts, k=k)
                            tag = (n, mode, scope, parts, k)
                            self.assertEqual(gaps(out, "source_unavailable"), [], tag)
                            self.assertEqual(out["index"]["unavailable_topic_sources"], {}, tag)
                            got = [g for g in gaps(out, "dependencies_unverified") if g["topic"] == "cbl_ratio"]
                            self.assertEqual(len(got), 1, tag)
                            obs = got[0]["unverified_source_observations"]
                            self.assertIn(self.key[n], {o["doc_key"] for o in obs}, tag)
                            self.assertTrue(all(o["confirmed"] is False and o["unverified_reason"] == self.reason[n] for o in obs), tag)
                            self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence", tag)  # includes empty results
                            self.assertIn("kept only as unverified observations", got[0]["note"], tag)

    def test_empty_result_cases_exist_and_are_missing_evidence(self):
        ix = self.ix["rulemaking__missing_metadata"]
        out = ix.answer(CBL, mode="hybrid", scope="future", parts=["12:3"], k=1)
        self.assertEqual((out["results"], out["status"]), ([], "missing_evidence"))
        self.assertTrue(gaps(out, "dependencies_unverified"))

    def test_citation_delta_and_other_behavior_are_unchanged(self):
        for n, ix in self.ix.items():
            for mode in ("exact", "hybrid"):
                cit = ix.answer("12 CFR 217.10(a)", mode=mode, scope="current", k=1)
                self.assertEqual((cit["status"], cit["citations"][0]["resolved"], gaps(cit, "dependencies_unverified"), gaps(cit, "source_unavailable")),
                                 ("evidence_found", True, [], []), (n, mode))
            d = ix.delta("12 CFR 217.12")
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"), n)
            self.assertEqual({r[0] for r in ix.conn.execute("SELECT DISTINCT review_status FROM relationship")} - {"parsed_unreviewed", "regex_unreviewed"}, set(), n)

    def test_database_bytes_are_unchanged_by_reading(self):
        for n, ix in self.ix.items():
            ix.answer(CBL, mode="hybrid", scope="both", k=10)
            self.assertEqual(sha(ix.db_path), self.before[n], n)

    def test_evaluator_check_is_registered_and_fails_when_persisted_rows_are_promoted(self):
        self.assertIn("dependency_verification", evaluate.REGRESSION_GROUPS)
        reg = json.loads((ROOT.parent / "eval/phase1/regressions.json").read_text())
        item = next(x for x in reg["dependency_verification"] if x["id"] == "DV05")

        class Stub:
            as_of = "2026-10-29"
        self.assertTrue(evaluate.check_dependency_verification(Stub(), item)["passed"])
        original = search.Index.__init__

        def starting_behaviour(self_, *a, **k):  # persisted rows trusted regardless of verification
            original(self_, *a, **k)
            self_.source_gaps = self_._load_source_gaps()
        with mock.patch.object(search.Index, "__init__", starting_behaviour):
            res = evaluate.check_dependency_verification(Stub(), item)
        self.assertFalse(res["passed"])


if __name__ == "__main__":
    unittest.main()
