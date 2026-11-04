"""Tests for S7-P1-65/66: scripts/p1_delta_coverage.py on small synthetic corpora (offline). Target counts reconcile across the
evidence-status tally, the Python distinct count and an independent SQL count over the index tables; part-level rows, unresolved
candidates and unclassified citations are never counted as targets; the before/after comparison attributes new baselines to the
added part derived from the two indexes, reports lost baselines, and checks an S7-P1-54 inventory; no instrument is merged."""
import copy
import importlib.util
import json
import shutil
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_p1_rulemaking_sources import build_rule

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("p1_delta_coverage", ROOT / "scripts" / "p1_delta_coverage.py")
cov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cov)
AS_OF = "2026-10-29"


def setUpModule():
    global _net
    _net = mock.patch.object(socket, "socket", side_effect=AssertionError("network access attempted in an offline test"))
    _net.start()


def tearDownModule():
    _net.stop()


def add_rows(db, amends=(), candidates=(), ranges=()):
    """Insert synthetic producer-shaped rows into a copy of a fixture index (source provision: any Federal Register provision)."""
    conn = sqlite3.connect(db)
    try:
        src = conn.execute("SELECT p.id FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE d.doc_key LIKE 'fr:%' LIMIT 1").fetchone()[0]
        for cite in amends:
            conn.execute("INSERT INTO relationship (src_provision_id, rel_type, dst_citation, basis, source_locator, review_status) "
                         "VALUES (?, 'amends', ?, 'synthetic test row', 'synthetic', 'parsed_unreviewed')", (src, cite))
        for cite in candidates:
            conn.execute("INSERT INTO relationship (src_provision_id, rel_type, dst_citation, basis, source_locator, review_status) "
                         "VALUES (?, 'amends_candidate', ?, 'synthetic test row', 'synthetic', 'unresolved_candidate')", (src, cite))
        for title, part, lo, hi in ranges:
            conn.execute("INSERT INTO amendment_range (src_provision_id, title_no, part_no, lo_section, hi_section, lo_minor, hi_minor, status, "
                         "instruction_ordinal, basis, review_status) VALUES (?, ?, ?, ?, ?, ?, ?, 'numeric_range', 1, 'synthetic', "
                         "'parsed_unreviewed')", (src, title, part, f"{part}.{lo}", f"{part}.{hi}", lo, hi))
        conn.commit()
    finally:
        conn.close()


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        for name, drop in (("full", None), ("no217", "217")):
            ix, _ = build_rule(cls.tmp, "complete", name, drop_part=drop)
            ix.conn.close()
        cls.full, cls.no217 = str(Path(cls.tmp) / "full.sqlite"), str(Path(cls.tmp) / "no217.sqlite")
        cls.rich = str(Path(cls.tmp) / "rich.sqlite")  # the full fixture plus part-level, candidate and range rows
        shutil.copy(cls.full, cls.rich)
        add_rows(cls.rich, amends=["12 CFR Part 217"], candidates=["12 CFR 217.99", "12 CFR 324.12"], ranges=[("12", "324", 1, 3)])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)


class ReconciliationTests(Fixture):
    def test_counts_reconcile_from_the_index_tables(self):
        s = cov.coverage(self.rich, AS_OF)["summary"]
        self.assertTrue(s["reconciles"])
        self.assertEqual(s["distinct_targets"], 4)  # 217.12 from amends; 324.1-324.3 from the range
        self.assertEqual(sum(s["targets_by_evidence_status"].values()), s["distinct_targets"])
        self.assertEqual(s["sql_distinct_targets"], s["distinct_targets"])
        self.assertEqual(s["sql_distinct_targets_basis"], "index tables (SQL_TARGETS)")
        self.assertEqual((s["amendment_range_rows"], s["amendment_range_members"], s["amends_candidate_rows"]), (1, 3, 2))
        self.assertEqual(s["amends_rows_total"], s["amends_rows_section"] + s["amends_rows_part_level"] + s["amends_rows_unclassified"]
                         + s["amends_rows_other_review_status"])

    def test_f_the_sql_count_is_independent_of_the_python_enumeration(self):
        real = cov.sources

        def drop_one(conn):
            targets, *rest = real(conn)
            targets.pop(sorted(targets)[0])
            return (targets, *rest)
        with mock.patch.object(cov, "sources", drop_one):
            s = cov.coverage(self.rich, AS_OF)["summary"]
        self.assertEqual((s["distinct_targets"], s["sql_distinct_targets"]), (3, 4))
        self.assertFalse(s["reconciles"])

    def test_e_part_level_rows_and_candidates_are_reported_but_never_targets(self):
        r = cov.coverage(self.rich, AS_OF)
        self.assertEqual(r["part_level_targets"], ["12 CFR Part 217"])
        self.assertEqual(r["unresolved_candidates"], ["12 CFR 217.99", "12 CFR 324.12"])
        cites = {row["citation"] for row in r["rows"]}
        self.assertFalse(cites & set(r["part_level_targets"]))
        self.assertFalse(cites & set(r["unresolved_candidates"]))
        self.assertEqual(r["summary"]["candidates_that_are_also_targets"], 0)

    def test_d_an_unclassified_amends_citation_is_collected_and_the_cli_exits_1(self):
        db = str(Path(self.tmp) / "unclassified.sqlite")
        shutil.copy(self.full, db)
        add_rows(db, amends=["12 CFR Part 217, Appendix A"])
        r = cov.coverage(db, AS_OF)
        self.assertEqual(r["summary"]["amends_rows_unclassified_citations"], ["12 CFR Part 217, Appendix A"])
        self.assertEqual(r["summary"]["amends_rows_unclassified"], 1)
        self.assertNotIn("12 CFR Part 217, Appendix A", {row["citation"] for row in r["rows"]})
        out = Path(self.tmp) / "u.json"
        with mock.patch("builtins.print"):
            self.assertEqual(cov.main(["--index", db, "--out", str(out)]), 1)
        self.assertEqual(json.loads(out.read_text())["status"]["amends_rows_unclassified_count"], 1)

    def test_no_instrument_is_merged_and_labels_are_kept(self):
        r = cov.coverage(self.full, AS_OF)
        self.assertEqual(r["summary"]["invariant_problems"], [])
        for row in r["rows"]:
            keys = [g["doc_key"] for g in row["instruments"]]
            self.assertEqual(len(keys), len(set(keys)), row["citation"])
            self.assertEqual((row["legal_validity"], row["applicability_status"]), ("not_determined", "not_assessed"))
        merged = copy.deepcopy(r["rows"][0])
        merged["instruments"] = merged["instruments"] * 2
        merged["instrument_count"] = len(merged["instruments"])
        self.assertTrue(any("merged" in p for p in cov.invariants([merged])))
        relabelled = copy.deepcopy(merged)
        relabelled["instruments"] = [{"doc_key": "fr:p", "document_type": "Proposed Rule", "lifecycle_state": "final_effective"}]
        relabelled["instrument_count"] = 1
        self.assertTrue(any("labelled final_effective" in p for p in cov.invariants([relabelled])))


class ComparisonTests(Fixture):
    def test_a_a_new_baseline_is_attributed_to_the_derived_added_part(self):
        before, after = cov.coverage(self.no217, AS_OF), cov.coverage(self.full, AS_OF)
        ba = cov.compare(after, before, None, {"12:999"})
        self.assertEqual(ba["added_parts_derived"], ["12:217"])
        self.assertEqual(ba["added_parts_argument"], ["12:999"])
        self.assertEqual(ba["added_parts_mismatch"], ["12:217", "12:999"])
        self.assertTrue(ba["newly_baseline_resolvable"])
        self.assertTrue(all(n["attributed_to_added_part"] == "12:217" for n in ba["newly_baseline_resolvable"]))
        self.assertEqual(ba["newly_baseline_resolvable_unattributed"], [])
        self.assertEqual((ba["baseline_lost"], ba["targets_removed"]), ([], []))
        self.assertIsNone(ba["inventory_54_check"])

    def test_b_a_removed_baseline_is_reported_as_lost(self):
        before, after = cov.coverage(self.full, AS_OF), cov.coverage(self.no217, AS_OF)
        ba = cov.compare(after, before, None, set())
        self.assertEqual(ba["baseline_lost"], ["12 CFR 217.12"])
        self.assertEqual(ba["added_parts_derived"], [])
        self.assertEqual(ba["newly_baseline_resolvable"], [])

    def test_b2_a_target_absent_after_is_reported_as_removed(self):
        before, after = cov.coverage(self.rich, AS_OF), cov.coverage(self.full, AS_OF)
        self.assertEqual(cov.compare(after, before, None, set())["targets_removed"], ["12 CFR 324.1", "12 CFR 324.2", "12 CFR 324.3"])

    def test_c_the_inventory_54_cross_check_is_machine_checked(self):
        inv = {"schema": "p1-federal-delta-coverage-inventory-0.1", "eligible_pairs": [
            {"baseline_doc_key": "ecfr:12:217", "locator": "section-217.12"},
            {"baseline_doc_key": "ecfr:12:217", "locator": "217.12(a)(2)(i)"},
            {"baseline_doc_key": "ecfr:12:3", "locator": "3.10(a)"}]}  # deliberately not a target
        path = Path(self.tmp) / "inv54.json"
        path.write_text(json.dumps(inv))
        sections, schema = cov.inventory_54_sections(path)
        self.assertEqual((sections, schema), ({("12", "217", "217.12"), ("12", "3", "3.10")}, inv["schema"]))
        before, after = cov.coverage(self.full, AS_OF), cov.coverage(self.full, AS_OF)
        check = cov.compare(after, before, sections, set())["inventory_54_check"]
        self.assertEqual(check["inventory_54_sections_not_in_targets"], ["12 CFR 3.10"])
        self.assertEqual(check["before_exact_section_not_in_54"], [])
        self.assertEqual(check["in_54_but_before_baseline_not_exact"], [])
        self.assertFalse(check["inventory_54_matches_before_exact_section"])
        check = cov.compare(cov.coverage(self.full, AS_OF), cov.coverage(self.no217, AS_OF), sections, set())["inventory_54_check"]
        self.assertEqual(check["in_54_but_before_baseline_not_exact"], ["12 CFR 217.12"])
        check = cov.compare(after, before, set(), set())["inventory_54_check"]
        self.assertEqual(check["before_exact_section_not_in_54"], ["12 CFR 217.12"])
        exact = cov.compare(after, before, {("12", "217", "217.12")}, set())["inventory_54_check"]
        self.assertTrue(exact["inventory_54_matches_before_exact_section"])

    def test_cli_writes_schema_0_2_with_a_computed_status_and_identical_reruns(self):
        out1, out2 = Path(self.tmp) / "c1.json", Path(self.tmp) / "c2.json"
        with mock.patch("builtins.print"):
            self.assertEqual(cov.main(["--index", self.full, "--before-index", self.no217, "--added-parts", "12:217", "--out", str(out1)]), 0)
            cov.main(["--index", self.full, "--before-index", self.no217, "--added-parts", "12:217", "--out", str(out2)])
        self.assertEqual(out1.read_bytes(), out2.read_bytes())
        doc = json.loads(out1.read_text())
        self.assertEqual(doc["schema"], "p1-delta-coverage-refresh-0.2")
        self.assertEqual(doc["status"]["reconciles"], True)
        self.assertEqual(doc["status"]["invariant_problem_count"], 0)
        self.assertEqual(doc["before_after"]["added_parts_mismatch"], [])


if __name__ == "__main__":
    unittest.main()
