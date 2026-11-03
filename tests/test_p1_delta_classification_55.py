"""S7-P1-55: offline synthetic tests for the delta-classification bundle builder and checker."""
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


B = load("build_p1_delta_classification_55")
C = load("check_p1_delta_classification_55")
SHA = lambda b: hashlib.sha256(b).hexdigest()


def rec(cls, doc="fr:1", dtype="Rule"):
    return {"doc_key": doc, "document_type": dtype, "lifecycle_state": "final_effective_date_pending", "classification": cls,
            "reason": "r", "provision_level_support": cls == "matched_provision", "provision_level_delta_match": cls == "matched_provision",
            "other_evidence": [], "current": {"scope": "current_baseline"}, "future": {"scope": "future_instrument"},
            "current_scope": "current_baseline", "future_scope": "future_instrument",
            "applicability_status": "not_assessed", "legal_validity": "not_determined"}


class Builder(unittest.TestCase):
    def test_only_enumerated_paths_and_true_sections_are_addressable(self):
        self.assertTrue(B.addressable({"locator": "section-3.12", "kind": "section", "section_no": "3.12"}))
        self.assertTrue(B.addressable({"locator": "3.12(c)(1)(iv)", "kind": "paragraph", "section_no": "3.12"}))
        for loc, kind in (("217.2(Advanced approaches total risk-weighted assets)(1)", "paragraph"),
                          ("217.101(Effective maturity (M))(1)", "paragraph"), ("217.2(Additional tier 1 capital)", "definition"),
                          ("section-217.11#table-3", "table"), ("217.11(a)(4)(iv)#table-1", "table")):
            self.assertFalse(B.addressable({"locator": loc, "kind": kind, "section_no": loc.split("(")[0].replace("section-", "").split("#")[0]}), loc)

    def test_downgrade_never_leaves_provision_support(self):
        out = B.downgrade([rec("matched_provision"), rec("matched_part_context", "fr:2")])
        self.assertEqual([r["classification"] for r in out], ["matched_section_context", "matched_part_context"])
        self.assertFalse(any(r["provision_level_support"] or r["provision_level_delta_match"] for r in out))

    def test_best_is_the_strongest_class_and_empty_is_an_explicit_gap(self):
        self.assertEqual(B.best(["reference_only", "matched_section_context"]), "matched_section_context")
        self.assertEqual(B.best([]), "no_acquired_instrument")

    def test_wrong_pinned_hash_raises_and_preserves_outputs(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "m").write_bytes(b"m"); (d / "i").write_bytes(b"i")
            (d / "v").write_text(json.dumps({"schema": "p1-federal-delta-coverage-inventory-0.1"}))
            inputs = {"manifest": (d / "m", SHA(b"m")), "index": (d / "i", SHA(b"i")),
                      "inventory_54": (d / "v", SHA((d / "v").read_bytes()))}
            B.verify(inputs, check_head=False)
            out = d / "out"; out.mkdir()
            for n in B.NAMES.values():
                (out / n).write_bytes(b"PRE")
            for k in inputs:
                with self.assertRaises(B.ProvenanceError):
                    B.build(out, inputs=inputs, expected={k: "0" * 64}, check_head=False)
            self.assertTrue(all((out / n).read_bytes() == b"PRE" for n in B.NAMES.values()))
            self.assertEqual([p.name for p in out.iterdir() if p.name.endswith(".tmp")], [])


def bundle():
    provs = [
        {"baseline_doc_key": "ecfr:12:3", "locator": "3.12(c)(1)", "kind": "paragraph", "section_no": "3.12", "task_54_category": "eligible",
         "addressable_by_own_citation": True, "best_classification": "matched_provision",
         "instruments": [rec("matched_provision"), rec("matched_section_context", "fr:2")], "provision_level_pairs": ["fr:1"]},
        {"baseline_doc_key": "ecfr:12:3", "locator": "3.12(d)", "kind": "paragraph", "section_no": "3.12",
         "task_54_category": "instrument_scope_mismatch:enclosing_section_explicitly_amended", "addressable_by_own_citation": True,
         "best_classification": "matched_section_context", "instruments": [rec("matched_section_context")], "provision_level_pairs": []},
        {"baseline_doc_key": "ecfr:12:3", "locator": "3.22(a)", "kind": "paragraph", "section_no": "3.22", "task_54_category": "no_future_instrument",
         "addressable_by_own_citation": True, "best_classification": "no_acquired_instrument",
         "instruments": [dict(rec("no_acquired_instrument"), doc_key=None, document_type=None, lifecycle_state=None)], "provision_level_pairs": []}]
    inv54 = {"eligible_pairs": [{"pair_key": "fr:1|ecfr:12:3|3.12(c)(1)", "baseline_doc_key": "ecfr:12:3", "locator": "3.12(c)(1)"}],
             "excluded_provisions": [{"baseline_doc_key": "ecfr:12:3", "locator": "3.12(d)"}, {"baseline_doc_key": "ecfr:12:3", "locator": "3.22(a)"}]}
    inv = {"schema": B.SCHEMA, "head": B.HEAD, "delta_contract": "p1-delta-provision-classification-1",
           "status": "producer-reported; no gate approved; no independent rerun; excluded provisions are not claimed to have no future instruments",
           "inputs": {"index": {"sha256": "i" * 64}}, "provisions": provs,
           "reconciliation_task_54_category_by_best_classification": B.reconcile(provs),
           "reconciliation_task_54_category_by_evidence_presence": B.presence(provs),
           "provision_level_pair_keys": ["fr:1|ecfr:12:3|3.12(c)(1)"],
           "counts": {"provisions": 3, "provision_level_pairs": 1, "provision_level_pairs_equal_task_54_eligible": True},
           "applicability_status": "not_assessed", "legal_validity": "not_determined"}
    prov = {"schema": B.PROVENANCE_SCHEMA, "head": B.HEAD, "inputs": {"index": {"sha256": "i" * 64}}, "code": {"x.py": "c" * 64},
            "outputs": {"inventory": {"sha256": "o" * 64}}}
    actual = {"inputs": {"index": "i" * 64}, "code": {"x.py": "c" * 64}, "outputs": {"inventory": "o" * 64}}
    pins = {"head": B.HEAD, "schema": B.SCHEMA, "provenance_schema": B.PROVENANCE_SCHEMA, "inputs": {"index": "i" * 64}}
    return inv, prov, inv54, actual, pins


class Checker(unittest.TestCase):
    def failing(self, *a):
        return {n for n, g, _ in C.check(*a) if not g}

    def test_consistent_bundle_passes(self):
        self.assertEqual(self.failing(*bundle()), set())

    def test_promoted_section_context_fails(self):
        a = bundle()
        r = a[0]["provisions"][1]["instruments"][0]
        r["provision_level_support"] = True
        self.assertIn("only matched_provision is provision-level support (context never promoted)", self.failing(*a))

    def test_invented_pair_from_context_fails(self):
        a = bundle()
        a[0]["provisions"][1]["provision_level_pairs"] = ["fr:1"]
        self.assertIn("provision-level pairs derive only from provision-level delta matches", self.failing(*a))

    def test_hidden_gap_fails(self):
        a = bundle()
        a[0]["provisions"][2]["instruments"] = []
        self.assertIn("no_acquired_instrument is an explicit record, never an empty list", self.failing(*a))

    def test_input_and_code_hash_mismatches_fail(self):
        a = bundle(); a[3]["inputs"]["index"] = "x" * 64
        self.assertIn("input hashes: pinned = recorded = actual", self.failing(*a))
        a = bundle(); a[3]["code"]["x.py"] = "x" * 64
        self.assertIn("code hashes recorded in provenance = actual files", self.failing(*a))
        a = bundle(); a[3]["outputs"]["inventory"] = "x" * 64
        self.assertIn("output hash recorded in provenance = actual inventory", self.failing(*a))

    def test_merged_scopes_and_mislabelled_proposal_fail(self):
        a = bundle(); a[0]["provisions"][0]["instruments"][0]["future_scope"] = "current_baseline"
        a[0]["provisions"][0]["instruments"][0].update(current_scope=None)
        self.assertIn("one instrument per record; current and future scopes separate", self.failing(*a))
        a = bundle(); r = a[0]["provisions"][0]["instruments"][0]
        r.update(document_type="Proposed Rule", lifecycle_state="final_effective_date_pending")
        self.assertIn("proposals never labelled final or current", self.failing(*a))


if __name__ == "__main__":
    unittest.main()
