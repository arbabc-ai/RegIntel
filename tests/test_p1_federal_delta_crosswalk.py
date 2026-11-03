"""S7-P1-52: offline tests for the federal delta crosswalk builder and checker (synthetic data only)."""
import copy
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = load("build_p1_federal_delta_crosswalk")
C = load("check_p1_federal_delta_crosswalk")
DOC = "fr:2026-00001"


def result(instruments=(), baseline=None, section_context=None, title="resolved"):
    return {"title_resolution": {"status": title}, "instruments": list(instruments), "baseline": baseline,
            "section_context": section_context, "unavailable": None}


def inst(resolved=None, match="explicit_target"):
    g = {"doc_key": DOC, "units": [{"locator": "u1", "match": match, "pairing_status": "explicit"}]}
    if resolved is not None:
        g["paragraph_precision"] = {"resolved": resolved, "supported_by": ["u1"] if resolved else []}
    return g


PARA_BASE = {"doc_key": "ecfr:12:3", "locator": "3.12(a)(1)", "precision": "paragraph", "match": "exact_section"}
SEC_BASE = {"doc_key": "ecfr:12:3", "locator": "section-3.12", "match": "exact_section"}


class Classify(unittest.TestCase):
    def test_paragraph_matched_only_with_both_sides_at_paragraph_precision(self):
        self.assertEqual(B.classify(result([inst(True)], PARA_BASE), DOC, "paragraph")[0], "matched")

    def test_paragraph_named_but_baseline_only_has_the_section(self):
        self.assertEqual(B.classify(result([inst(True)], None, SEC_BASE), DOC, "paragraph")[0], "section_only")

    def test_paragraph_not_named_by_this_instrument(self):
        self.assertEqual(B.classify(result([inst(False)], PARA_BASE), DOC, "paragraph")[0], "paragraph_precision_unresolved")

    def test_another_instruments_support_never_counts(self):
        other = dict(inst(True), doc_key="fr:2026-99999")
        self.assertEqual(B.classify(result([other], PARA_BASE), DOC, "paragraph")[0], "unsupported")

    def test_unavailable_baseline_names_the_reason(self):
        st, why, _ = B.classify(result([inst()]), DOC, "section", part_acquired=False)
        self.assertEqual((st, why), ("unavailable", "the baseline part is not acquired"))
        self.assertIn("not in the acquired baseline", B.classify(result([inst()]), DOC, "section", part_acquired=True)[1])

    def test_ambiguous_title(self):
        self.assertEqual(B.classify(result([inst()], SEC_BASE, title="ambiguous"), DOC, "section")[0], "ambiguous_title")

    def test_section_match_and_range_section_is_not_exact(self):
        self.assertEqual(B.classify(result([inst()], SEC_BASE), DOC, "section")[0], "matched")
        rng = dict(SEC_BASE, match="range_section")
        self.assertEqual(B.classify(result([inst(match="range_membership")], rng), DOC, "section")[0], "section_only")

    def test_part_level_is_unsupported(self):
        self.assertEqual(B.classify(result([inst()], {"doc_key": "ecfr:12:3", "locator": "part-3"}), DOC, "part")[0], "unsupported")


class Candidates(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.executescript("""
            CREATE TABLE source_document (id INTEGER PRIMARY KEY, doc_key TEXT, publisher TEXT);
            CREATE TABLE provision (id INTEGER PRIMARY KEY, doc_id INTEGER, kind TEXT, title_no TEXT, part_no TEXT, section_no TEXT,
                                    explicit_paragraphs TEXT, pairing_status TEXT);
            CREATE TABLE relationship (id INTEGER PRIMARY KEY, src_provision_id INTEGER, rel_type TEXT, dst_citation TEXT);
            CREATE TABLE amendment_range (id INTEGER PRIMARY KEY, src_provision_id INTEGER, title_no TEXT, part_no TEXT, lo_section TEXT,
                                          hi_section TEXT, lo_minor INTEGER, hi_minor INTEGER, status TEXT);
            INSERT INTO source_document VALUES (1,'fr:2026-00001','Federal Register'),(2,'ecfr:12:8','eCFR');
            INSERT INTO provision VALUES (10,1,'amendment',NULL,NULL,NULL,'{"paths": ["(a)(1)"], "status": "attributed"}','explicit'),
                                         (11,1,'amendment',NULL,NULL,NULL,'{"paths": ["(b)"], "status": "unattributed_multiple_targets"}','explicit'),
                                         (12,1,'amendment',NULL,NULL,NULL,NULL,'carried_text_unpaired'),
                                         (20,2,'section','12','8','8.2',NULL,NULL),(21,2,'section','12','8','8.9',NULL,NULL);
            INSERT INTO relationship VALUES (1,10,'amends','12 CFR 3.12'),(2,11,'amends','12 CFR 3.13'),(3,12,'amends_candidate','12 CFR 4.1'),
                                            (4,10,'references','12 CFR 3.99');
            INSERT INTO amendment_range VALUES (1,10,'12','8','8.1','8.8',1,8,'numeric_range');
        """)

    def test_candidates_come_only_from_parsed_rows(self):
        got = B.candidates_for(self.c, 1)
        self.assertEqual(got["12 CFR 3.12(a)(1)"]["precision"], "paragraph")      # attributed path only
        self.assertNotIn("12 CFR 3.13(b)", got)                                     # unattributed path is not a candidate
        self.assertIn("12 CFR 3.13", got)
        self.assertTrue(all(s.startswith("unresolved_candidate") for s in got["12 CFR 4.1"]["sources"]))
        self.assertNotIn("12 CFR 3.99", got)                                        # references are never amendment targets
        self.assertIn("12 CFR 8.2", got)                                            # baseline section inside the parsed range
        self.assertNotIn("12 CFR 8.9", got)                                         # outside the range


def fixture():
    sup = {"records": [
        {"record_id": "CB-ecfr:12:3", "manifest_key": "ecfr:12:3", "record_kind": "current_baseline",
         "lifecycle_state": "current_baseline_publisher_snapshot", "gap": "none"},
        {"record_id": f"FR-{DOC}", "manifest_key": DOC, "record_kind": "future_instrument", "document_number": "2026-00001",
         "document_type": "Proposed Rule", "lifecycle_state": "proposed_comment_period_open", "gap": "none"},
        {"record_id": "FR-fr:2026-00002", "manifest_key": "fr:2026-00002", "record_kind": "notice", "document_number": "2026-00002",
         "document_type": "Notice", "lifecycle_state": "notice_not_a_rule_state", "gap": "Notice"}]}
    base = {"doc_key": "ecfr:12:3", "locator": "3.12(a)(1)", "citation": "12 CFR 3.12(a)(1)", "sha256": "0" * 64, "issue_date": "2026-10-23",
            "role": "baseline", "precision": "paragraph", "match": "exact_section"}
    common = {"inventory_id": f"FR-{DOC}", "instrument_doc_key": DOC, "document_number": "2026-00001", "document_type": "Proposed Rule",
              "lifecycle_state": "proposed_comment_period_open", "current_or_future": "future_instrument", "baseline_relation": "proposal_not_in_baseline",
              "applicability_status": "not_assessed", "legal_validity": "not_determined"}
    deltas = [
        dict(common, delta_id=f"{DOC}|12 CFR 3.12(a)(1)", requested_citation="12 CFR 3.12(a)(1)", requested_precision="paragraph",
             baseline_status="matched", baseline_reason="none", gap="none", current_baseline=base,
             paragraph_precision={"resolved": True, "supported_by": ["u1"]}),
        dict(common, delta_id=f"{DOC}|12 CFR 3.12(c)(9)", requested_citation="12 CFR 3.12(c)(9)", requested_precision="paragraph",
             baseline_status="section_only", baseline_reason="baseline has only the section",
             gap={"class": "section_only", "instrument": DOC, "requested_path": "12 CFR 3.12(c)(9)", "reason": "baseline has only the section"},
             current_baseline=dict(base, locator="section-3.12", role="section_context", precision="section_or_part"),
             paragraph_precision={"resolved": True, "supported_by": ["u1"]})]
    acc = [{"inventory_id": "CB-ecfr:12:3", "manifest_key": "ecfr:12:3", "source_kind": "current_baseline", "role": "current_baseline_source",
            "lifecycle_state": "current_baseline_publisher_snapshot", "existing_gap": "none", "delta_records": 0},
           {"inventory_id": f"FR-{DOC}", "manifest_key": DOC, "source_kind": "future_instrument", "document_number": "2026-00001",
            "document_type": "Proposed Rule", "role": "future_instrument_source", "lifecycle_state": "proposed_comment_period_open", "existing_gap": "none", "delta_records": 2},
           {"inventory_id": "FR-fr:2026-00002", "manifest_key": "fr:2026-00002", "source_kind": "notice", "document_number": "2026-00002",
            "role": "excluded_notice_not_in_future_scope", "lifecycle_state": "notice_not_a_rule_state", "existing_gap": "Notice", "delta_records": 0}]
    cw = {"schema": "p1-federal-delta-crosswalk-0.1", "status": "producer-reported; no gate approved; no independent rerun",
          "counts": {"inventory_records": 3, "delta_records": 2, "baseline_status": {"matched": 1, "section_only": 1}},
          "input_accounting": acc, "deltas": deltas}
    return cw, sup


class Checker(unittest.TestCase):
    def failing(self, cw, sup):
        return {name for name, good, _ in C.check(cw, sup) if not good}

    def test_valid_fixture_passes(self):
        self.assertEqual(self.failing(*fixture()), set())

    def test_dropped_inventory_record_fails(self):
        cw, sup = fixture()
        cw["input_accounting"].pop(2)
        self.assertIn("every inventory record accounted for exactly once", self.failing(cw, sup))

    def test_duplicate_pair_fails(self):
        cw, sup = fixture()
        cw["deltas"].append(copy.deepcopy(cw["deltas"][0]))
        self.assertIn("no duplicate instrument/provision pair; delta_id = instrument|citation", self.failing(cw, sup))

    def test_mislabelled_proposal_fails(self):
        cw, sup = fixture()
        cw["deltas"][0]["lifecycle_state"] = "final_effective_date_pending"
        self.assertIn("proposals never labelled final, current or operative", self.failing(cw, sup))

    def test_cross_scope_leakage_fails(self):
        cw, sup = fixture()
        cw["deltas"][0]["current_baseline"]["doc_key"] = "fr:2026-00003"
        self.assertIn("current side of every delta is an eCFR current-baseline record (no cross-scope leakage)", self.failing(cw, sup))

    def test_missing_gap_fails(self):
        cw, sup = fixture()
        cw["deltas"][1]["gap"] = "none"
        self.assertIn("every non-matched record carries a structured gap with instrument, path and reason", self.failing(cw, sup))

    def test_invented_exact_paragraph_match_fails(self):
        cw, sup = fixture()
        d = cw["deltas"][1]
        d.update(baseline_status="matched", baseline_reason="none", gap="none")
        self.assertIn("no false exact match (matched needs the instrument and the baseline at the requested precision)", self.failing(cw, sup))


    def test_mismatched_pair_id_fails(self):
        cw, sup = fixture()
        cw["deltas"][0]["delta_id"] = f"{DOC}|12 CFR 3.13"
        self.assertIn("no duplicate instrument/provision pair; delta_id = instrument|citation", self.failing(cw, sup))

    def test_pair_with_another_instruments_document_number_fails(self):
        cw, sup = fixture()
        cw["deltas"][0]["document_number"] = "2026-99999"
        self.assertIn("pair records consistent with their instrument (document number, type, key)", self.failing(cw, sup))


# ---- S7-P1-53: input provenance and atomic output ----
SHA = lambda b: hashlib.sha256(b).hexdigest()


def provenance_fixture():
    cw, sup = fixture()
    sup = dict(sup, schema="p1-federal-coverage-supplement-0.1",
               provenance={"manifest": {"sha256": "m" * 64}, "index": {"path": "/idx.sqlite", "sha256": "i" * 64}})
    cw["provenance"] = {"supplement_51": {"sha256": "s" * 64}, "index": {"path": "/idx.sqlite", "sha256": "i" * 64}}
    supplied = {"supplement_sha256": "s" * 64, "manifest_sha256": "m" * 64, "index_path": "/idx.sqlite", "index_sha256": "i" * 64}
    expected = {"supplement_sha256": "s" * 64, "manifest_sha256": "m" * 64, "index_sha256": "i" * 64}
    return cw, sup, supplied, expected


class Provenance(unittest.TestCase):
    def failing(self, cw, sup, supplied, expected):
        return {n for n, good, _ in C.check(cw, sup, None, supplied, expected) if not good}

    def test_matching_provenance_passes(self):
        self.assertEqual(self.failing(*provenance_fixture()), set())

    def test_wrong_manifest_hash_fails(self):
        cw, sup, supplied, expected = provenance_fixture()
        supplied["manifest_sha256"] = "x" * 64
        self.assertIn("manifest hash: supplied file = recorded by supplement = expected", self.failing(cw, sup, supplied, expected))
        cw, sup, supplied, expected = provenance_fixture()
        sup["provenance"]["manifest"]["sha256"] = "x" * 64
        self.assertIn("manifest hash: supplied file = recorded by supplement = expected", self.failing(cw, sup, supplied, expected))

    def test_wrong_supplement_hash_fails(self):
        cw, sup, supplied, expected = provenance_fixture()
        supplied["supplement_sha256"] = "x" * 64
        self.assertIn("supplement hash: supplied file = recorded in crosswalk = expected", self.failing(cw, sup, supplied, expected))

    def test_unexpected_schema_or_version_fails(self):
        cw, sup, supplied, expected = provenance_fixture()
        cw["schema"] = "p1-federal-delta-crosswalk-0.2"
        self.assertIn("expected crosswalk schema/version", self.failing(cw, sup, supplied, expected))
        cw, sup, supplied, expected = provenance_fixture()
        sup["schema"] = "p1-federal-coverage-supplement-0.0"
        self.assertIn("expected supplement schema/version", self.failing(cw, sup, supplied, expected))

    def test_wrong_index_path_or_hash_fails(self):
        for field, value in (("index_path", "/other.sqlite"), ("index_sha256", "x" * 64)):
            cw, sup, supplied, expected = provenance_fixture()
            supplied[field] = value
            self.assertIn("index: supplied path and hash = recorded in crosswalk and supplement = expected",
                          self.failing(cw, sup, supplied, expected))


class BuilderInputsAndAtomicOutput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.man = self.d / "manifest.json"; self.man.write_bytes(b'{"documents": {}}')
        self.idx = self.d / "index.sqlite"; self.idx.write_bytes(b"not-a-real-index")
        self.sup = self.d / "supplement.json"
        self.sup.write_text(json.dumps({"schema": "p1-federal-coverage-supplement-0.1", "records": [],
                                        "provenance": {"manifest": {"sha256": SHA(self.man.read_bytes())},
                                                       "index": {"path": str(self.idx), "sha256": SHA(self.idx.read_bytes())}}}))
        self.exp = {"manifest_sha256": SHA(self.man.read_bytes()), "supplement_sha256": SHA(self.sup.read_bytes()),
                    "index_sha256": SHA(self.idx.read_bytes())}
        self.out = self.d / "crosswalk.json"
        self.out.write_bytes(b"PRE-EXISTING DELIVERABLE")

    def tearDown(self):
        self.tmp.cleanup()

    def leftovers(self):
        return sorted(p.name for p in self.d.iterdir() if p.name.endswith(".tmp"))

    def test_matching_inputs_are_recorded(self):
        rec = B.verify_inputs(self.sup, self.idx, self.man, self.exp)
        self.assertEqual(rec["manifest"]["sha256"], self.exp["manifest_sha256"])
        self.assertEqual(rec["supplement"]["sha256"], self.exp["supplement_sha256"])
        self.assertEqual(rec["index"]["path"], str(self.idx))
        self.assertEqual(rec["crosswalk_schema"], "p1-federal-delta-crosswalk-0.1")

    def test_wrong_inputs_raise_deterministically(self):
        for key in ("manifest_sha256", "supplement_sha256", "index_sha256"):
            with self.assertRaises(B.ProvenanceError) as ctx:
                B.verify_inputs(self.sup, self.idx, self.man, {**self.exp, key: "0" * 64})
            self.assertIn("0" * 64, str(ctx.exception))
        bad = json.loads(self.sup.read_text()); bad["schema"] = "p1-federal-coverage-supplement-9.9"
        self.sup.write_text(json.dumps(bad))
        with self.assertRaises(B.ProvenanceError):
            B.verify_inputs(self.sup, self.idx, self.man, {**self.exp, "supplement_sha256": SHA(self.sup.read_bytes())})

    def test_build_failure_preserves_existing_output(self):
        with self.assertRaises(B.ProvenanceError):
            B.main(self.sup, self.idx, self.out, self.man, {**self.exp, "manifest_sha256": "0" * 64})
        self.assertEqual(self.out.read_bytes(), b"PRE-EXISTING DELIVERABLE")
        self.assertFalse(B.provenance_path(self.out).exists())
        self.assertEqual(self.leftovers(), [])

    def test_atomic_write_failure_preserves_existing_output(self):
        with self.assertRaises(TypeError):                       # unserializable: fails before any file is opened
            B.atomic_write_json(self.out, {"bad": object()})
        with mock.patch.object(B.os, "replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaises(OSError):
                B.atomic_write_json(self.out, {"ok": 1})
        self.assertEqual(self.out.read_bytes(), b"PRE-EXISTING DELIVERABLE")
        self.assertEqual(self.leftovers(), [])

    def test_atomic_write_replaces_whole_file(self):
        B.atomic_write_json(self.out, {"a": 1})
        self.assertEqual(json.loads(self.out.read_text()), {"a": 1})
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
