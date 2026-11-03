"""S7-P1-54: offline synthetic tests for the supplemental delta-coverage builder and checker."""
import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = load("build_p1_federal_delta_coverage_54")
C = load("check_p1_federal_delta_coverage_54")
SHA = lambda b: hashlib.sha256(b).hexdigest()


def prov(kind="paragraph", section="3.12"):
    return {"kind": kind, "section_no": section}


class Reasons(unittest.TestCase):
    def test_precedence_gives_exactly_one_reason(self):
        self.assertEqual(B.assign_reason(prov("subpart", None), [], {1}, {1}, {1})[0], "source_class_not_eligible")
        self.assertEqual(B.assign_reason(prov("section", "3.4-3.9"), ["x"], {1}, {1}, {1})[0], "ambiguous_current_provision_scope")
        self.assertEqual(B.assign_reason(prov(), ["x"], {1}, {1}, {1}), (None, None))
        self.assertEqual(B.assign_reason(prov(), [], {1}, {1}, {1}), ("instrument_scope_mismatch", "enclosing_section_explicitly_amended"))
        self.assertEqual(B.assign_reason(prov(), [], None, {1}, {1})[0], "unsupported_mapping")
        self.assertEqual(B.assign_reason(prov(), [], None, {1}, None),
                         ("instrument_scope_mismatch", "enclosing_part_amended_by_part_level_instruction_only"))
        self.assertEqual(B.assign_reason(prov(), [], None, None, None)[0], "no_future_instrument")

    def test_pair_keys_are_deterministic(self):
        self.assertEqual(B.pair_key("fr:1", "ecfr:12:3", "3.12(a)"), "fr:1|ecfr:12:3|3.12(a)")
        self.assertEqual(B.pair_key("fr:1", "ecfr:12:3", "3.12(a)"), B.pair_key("fr:1", "ecfr:12:3", "3.12(a)"))
        self.assertNotEqual(B.pair_key("fr:1", "ecfr:12:3", "3.12(a)"), B.pair_key("fr:2", "ecfr:12:3", "3.12(a)"))

    def test_represented_pairs_cover_every_task52_record_with_a_baseline(self):
        t52 = {"deltas": [
            {"delta_id": "a", "instrument_doc_key": "fr:1", "requested_citation": "12 CFR 3.12(a)(1)", "baseline_status": "matched",
             "current_baseline": {"doc_key": "ecfr:12:3"}},
            {"delta_id": "b", "instrument_doc_key": "fr:1", "requested_citation": "12 CFR 3.12", "baseline_status": "matched",
             "current_baseline": {"doc_key": "ecfr:12:3"}},
            {"delta_id": "c", "instrument_doc_key": "fr:2", "requested_citation": "12 CFR 3.22(i)", "baseline_status": "section_only",
             "current_baseline": {"doc_key": "ecfr:12:3"}},
            {"delta_id": "d", "instrument_doc_key": "fr:3", "requested_citation": "12 CFR 5.1", "baseline_status": "unavailable",
             "current_baseline": "none"}]}
        self.assertEqual(sorted(B.represented_pairs(t52)), ["fr:1|ecfr:12:3|3.12(a)(1)", "fr:1|ecfr:12:3|section-3.12",
                                                           "fr:2|ecfr:12:3|3.22(i)"])


def bundle():
    """A consistent synthetic bundle: two eligible pairs, one already in task 52, one missing."""
    elig = [{"pair_key": "fr:1|ecfr:12:3|3.12(a)(1)", "instrument_doc_key": "fr:1", "baseline_doc_key": "ecfr:12:3", "locator": "3.12(a)(1)",
             "delta_status": "matched", "reason": "none", "represented_by_task_52": {"delta_id": "a", "baseline_status": "matched"}},
            {"pair_key": "fr:2|ecfr:12:3|3.12(a)(1)", "instrument_doc_key": "fr:2", "baseline_doc_key": "ecfr:12:3", "locator": "3.12(a)(1)",
             "delta_status": "matched", "reason": "none", "represented_by_task_52": "no"}]
    exc = [{"baseline_doc_key": "ecfr:12:3", "locator": "3.12(b)", "kind": "paragraph", "reason": "instrument_scope_mismatch"},
           {"baseline_doc_key": "ecfr:12:3", "locator": "part-3", "kind": "part", "reason": "source_class_not_eligible"}]
    by_reason = {r: sum(1 for x in exc if x["reason"] == r) for r in B.REASONS}
    inv = {"schema": B.INVENTORY_SCHEMA, "head": B.HEAD, "status": "producer-reported; no gate approved; no independent rerun",
           "inputs": {k: {"sha256": "i" * 64} for k in ("manifest", "index")},
           "counts": {"compared_population_provisions": 3, "eligible_pairs": 2, "missing_pairs": 1, "excluded_provisions": 2,
                      "represented_eligible_pairs": 1, "excluded_by_reason": by_reason},
           "eligible_pairs": elig, "missing_pairs": [elig[1]], "excluded_provisions": exc,
           "applicability_status": "not_assessed", "legal_validity": "not_determined"}
    rec = {"pair_key": elig[1]["pair_key"],
           "current": {"scope": "current_baseline", "doc_key": "ecfr:12:3", "locator": "3.12(a)(1)"},
           "future": {"scope": "future_instrument", "doc_key": "fr:2", "document_type": "Proposed Rule", "lifecycle_state": "proposed_comment_period_open"},
           "applicability_status": "not_assessed", "legal_validity": "not_determined"}
    cw = {"schema": B.CROSSWALK_SCHEMA, "head": B.HEAD, "record_count": 1, "records": [rec]}
    pv = {"schema": B.PROVENANCE_SCHEMA, "head": B.HEAD, "inputs": {k: {"sha256": "i" * 64} for k in ("manifest", "index")},
          "code": {"a.py": "c" * 64}, "outputs": {"inventory": {"sha256": "o" * 64}, "crosswalk": {"sha256": "p" * 64}}}
    t52 = {"deltas": [{"delta_id": "a", "instrument_doc_key": "fr:1", "requested_citation": "12 CFR 3.12(a)(1)", "baseline_status": "matched",
                       "current_baseline": {"doc_key": "ecfr:12:3"}}]}
    actual = {"inputs": {"manifest": "i" * 64, "index": "i" * 64}, "code": {"a.py": "c" * 64},
              "outputs": {"inventory": "o" * 64, "crosswalk": "p" * 64}}
    pins = {"head": B.HEAD, "inputs": {"manifest": "i" * 64, "index": "i" * 64}, "code": {"a.py": "c" * 64},
            "inventory_schema": B.INVENTORY_SCHEMA, "crosswalk_schema": B.CROSSWALK_SCHEMA, "provenance_schema": B.PROVENANCE_SCHEMA}
    return inv, cw, pv, t52, actual, pins


class Checker(unittest.TestCase):
    def failing(self, *args, population=3):
        return {n for n, good, _ in C.check(*args, population=population) if not good}

    def test_consistent_bundle_passes(self):
        self.assertEqual(self.failing(*bundle()), set())

    def test_incomplete_subtraction_fails(self):
        inv, cw, pv, t52, actual, pins = bundle()
        inv["missing_pairs"] = []; inv["counts"]["missing_pairs"] = 0
        self.assertIn("complete subtraction: missing = eligible minus task-52-represented", self.failing(inv, cw, pv, t52, actual, pins))

    def test_task52_pair_duplicated_fails(self):
        inv, cw, pv, t52, actual, pins = bundle()
        dup = copy.deepcopy(cw["records"][0]); dup["pair_key"] = inv["eligible_pairs"][0]["pair_key"]; dup["future"]["doc_key"] = "fr:1"
        cw["records"].append(dup); cw["record_count"] = 2
        self.assertIn("no task-52 pair duplicated in the supplement", self.failing(inv, cw, pv, t52, actual, pins))

    def test_missing_pair_listed_twice_fails(self):
        inv, cw, pv, t52, actual, pins = bundle()
        cw["records"].append(copy.deepcopy(cw["records"][0])); cw["record_count"] = 2
        self.assertIn("one supplemental record per missing pair, exactly once", self.failing(inv, cw, pv, t52, actual, pins))

    def test_merged_instrument_record_fails(self):
        inv, cw, pv, t52, actual, pins = bundle()
        cw["records"][0]["future"]["doc_key"] = "fr:2; fr:3"
        self.assertIn("current and future kept separate; one instrument per record", self.failing(inv, cw, pv, t52, actual, pins))

    def test_current_and_future_merged_fails(self):
        inv, cw, pv, t52, actual, pins = bundle()
        cw["records"][0]["current"]["scope"] = "future_instrument"
        self.assertIn("current and future kept separate; one instrument per record", self.failing(inv, cw, pv, t52, actual, pins))

    def test_provision_with_two_reasons_fails(self):
        inv, cw, pv, t52, actual, pins = bundle()
        inv["excluded_provisions"].append(dict(inv["excluded_provisions"][0], reason="no_future_instrument"))
        self.assertIn("every compared provision is eligible or excluded exactly once", self.failing(inv, cw, pv, t52, actual, pins))

    def test_input_hash_mismatch_fails(self):
        inv, cw, pv, t52, actual, pins = bundle()
        actual["inputs"]["manifest"] = "x" * 64
        self.assertIn("pinned input hashes: expected = recorded (provenance and inventory) = actual files", self.failing(inv, cw, pv, t52, actual, pins))

    def test_code_provenance_mismatch_fails(self):
        inv, cw, pv, t52, actual, pins = bundle()
        actual["code"]["a.py"] = "x" * 64
        self.assertIn("code provenance: recorded code hashes = actual files = pinned contracts", self.failing(inv, cw, pv, t52, actual, pins))

    def test_output_hash_mismatch_fails(self):
        inv, cw, pv, t52, actual, pins = bundle()
        actual["outputs"]["crosswalk"] = "x" * 64
        self.assertIn("output hashes recorded in the provenance sidecar = actual output files", self.failing(inv, cw, pv, t52, actual, pins))

    def test_mislabelled_proposal_fails(self):
        inv, cw, pv, t52, actual, pins = bundle()
        cw["records"][0]["future"]["lifecycle_state"] = "final_effective_date_pending"
        self.assertIn("proposals never labelled final, current or operative", self.failing(inv, cw, pv, t52, actual, pins))


class ProvenanceAndOutputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.d = Path(self.tmp.name)
        (d / "m.json").write_text("{}"); (d / "i.db").write_bytes(b"idx")
        (d / "s.json").write_text(json.dumps({"schema": B.SCHEMAS["supplement_51"]}))
        (d / "c.json").write_text(json.dumps({"schema": B.SCHEMAS["crosswalk_52"]}))
        self.inputs = {"manifest": (d / "m.json", SHA(b"{}")), "index": (d / "i.db", SHA(b"idx")),
                       "supplement_51": (d / "s.json", SHA((d / "s.json").read_bytes())),
                       "crosswalk_52": (d / "c.json", SHA((d / "c.json").read_bytes()))}
        self.out = d / "out"; self.out.mkdir()
        for name in B.NAMES.values():
            (self.out / name).write_bytes(b"PRE-EXISTING")

    def tearDown(self):
        self.tmp.cleanup()

    def test_matching_pins_verify(self):
        # input pins only: the code contract is supplied as the files' current hashes (code mismatch is tested separately;
        # the real task-54 builder keeps its pinned search.py hash and refuses to rebuild after S7-P1-55 changed search.py)
        code = {rel: SHA((ROOT / rel).read_bytes()) for rel in B.CODE}
        rec = B.verify(self.inputs, code=code, check_head=False)
        self.assertEqual(rec["inputs"]["manifest"]["sha256"], SHA(b"{}"))

    def test_each_wrong_input_hash_raises(self):
        code = {rel: SHA((ROOT / rel).read_bytes()) for rel in B.CODE}
        for k in self.inputs:
            with self.assertRaises(B.ProvenanceError) as ctx:
                B.verify(self.inputs, code=code, expected={k: "0" * 64}, check_head=False)
            self.assertIn(k, str(ctx.exception))

    def test_wrong_schema_raises(self):
        (self.d / "c.json").write_text(json.dumps({"schema": "p1-federal-delta-crosswalk-9.9"}))
        self.inputs["crosswalk_52"] = (self.d / "c.json", SHA((self.d / "c.json").read_bytes()))
        code = {rel: SHA((ROOT / rel).read_bytes()) for rel in B.CODE}
        with self.assertRaises(B.ProvenanceError) as ctx:
            B.verify(self.inputs, code=code, check_head=False)
        self.assertIn("schema", str(ctx.exception))

    def test_code_hash_mismatch_raises(self):
        with self.assertRaises(B.ProvenanceError):
            B.verify(self.inputs, code={"regintel_p1/search.py": "0" * 64}, check_head=False)

    def test_failed_build_preserves_existing_outputs(self):
        with self.assertRaises(B.ProvenanceError):
            B.build(self.out, inputs=self.inputs, expected={"manifest": "0" * 64}, check_head=False)
        for name in B.NAMES.values():
            self.assertEqual((self.out / name).read_bytes(), b"PRE-EXISTING")
        self.assertEqual([p.name for p in self.out.iterdir() if p.name.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
