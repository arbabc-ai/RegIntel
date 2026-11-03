"""S7-P1-56 (P1.2 step 1): versioned synthetic BankProfile, conditional applicability routing, answer()/delta() integration.

Synthetic fixture index and fixture scope conditions only; no corpus dependency."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from regintel_p1 import build, evaluate, search
from regintel_p1.applicability import ApplicabilityEngine, aggregate
from regintel_p1.bank_profile import PROFILE_VERSION, BankProfile, load_profile
from test_p1 import asset

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "eval" / "phase1" / "bank_profiles"


def section(sid, heading, paras):
    body = "".join(f'<div id="p-{sid}{p}"><p class="indent-1" data-title="{sid}{p}">{t}</p></div>' for p, t in paras)
    return f'<div class="section" id="{sid}"><h4>§ {sid} {heading}</h4>{body}</div>'


ECFR = {
    "3": section("3.1", "Purpose, applicability.", [("(c)", "(c) Applicability."),
                 ("(c)(1)", "(1) Minimum capital. Each national bank or Federal savings association must calculate its minimum capital requirements.")])
         + section("3.10", "Minimum capital requirements.", [("(a)", "(a) Minimum capital requirements."),
                   ("(a)(1)", "(1) A common equity tier 1 capital ratio of 4.5 percent.")]),
    "217": section("217.1", "Purpose, applicability.", [("(c)", "(c) Applicability."),
                   ("(c)(1)", "(1) This part applies on a consolidated basis to every Board-regulated institution that is:"),
                   ("(c)(1)(i)", "(i) A state member bank;"), ("(c)(1)(ii)", "(ii) A bank holding company domiciled in the United States.")])
           + section("217.10", "Minimum capital requirements.", [("(a)", "(a) Minimum capital requirements."),
                     ("(a)(1)", "(1) A common equity tier 1 capital ratio of 4.5 percent.")]),
    "324": section("324.1", "Purpose, applicability.", [("(c)", "(c) Applicability."),
                   ("(c)(1)", "(1) Each FDIC-supervised institution must calculate its minimum capital requirements.")])
           + section("324.10", "Minimum capital requirements.", [("(a)", "(a) Minimum capital requirements."),
                     ("(a)(1)", "(1) A common equity tier 1 capital ratio of 4.5 percent.")]),
    "249": section("249.10", "Liquidity coverage ratio.", [("(a)", "(a) A liquidity coverage ratio.")]),
}
CONDITIONS = {"version": "p1-applicability-conditions-fixture", "note": "test fixture", "conditions": [
    {"condition_id": "FX-3", "title": "12", "part": "3", "in_scope_entity_categories": ["national_bank", "federal_savings_association"],
     "sources": [{"doc_key": "ecfr:12:3", "locator": "3.1(c)(1)", "quote": "Each national bank or Federal savings association must calculate"}],
     "unevaluated_qualifiers": ["subject to 3.1(d) and (f) (not evaluated)"]},
    {"condition_id": "FX-217", "title": "12", "part": "217",
     "in_scope_entity_categories": ["state_member_bank", "bank_holding_company", "savings_and_loan_holding_company"],
     "requires_domestic_holding_company": True,
     "sources": [{"doc_key": "ecfr:12:217", "locator": "217.1(c)(1)", "quote": "applies on a consolidated basis to every Board-regulated institution"},
                 {"doc_key": "ecfr:12:217", "locator": "217.1(c)(1)(i)", "quote": "A state member bank"}],
     "unevaluated_qualifiers": ["consolidated basis (not evaluated)"]},
    {"condition_id": "FX-324", "title": "12", "part": "324", "in_scope_entity_categories": ["state_nonmember_insured_bank", "state_savings_association"],
     "sources": [{"doc_key": "ecfr:12:324", "locator": "324.1(c)(1)", "quote": "Each FDIC-supervised institution must calculate"}],
     "unevaluated_qualifiers": ["subject to 324.1(d) and (f) (not evaluated)"]}]}
FR_PROP = (b'<PRORULE><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF><REGTEXT TITLE="12" PART="3">'
           b"<AMDPAR>1. Amend &#xA7; 3.10 by revising paragraph (a)(1) to read as follows:</AMDPAR>"
           b"<SECTION><SECTNO>&#xA7; 3.10</SECTNO><SUBJECT>S.</SUBJECT><P>(a) * * *</P><P>(1) A common equity tier 1 capital ratio of 5 percent.</P>"
           b"</SECTION></REGTEXT></SUPLINF></PRORULE>")
FR_KEY = "fr:2026-88101"


def profile(name):
    return load_profile(PROFILES / f"synthetic_{name}.json")


class Schema(unittest.TestCase):
    def test_three_shipped_profiles_validate_with_the_expected_categories(self):
        got = {p.stem: BankProfile.model_validate_json(p.read_text()) for p in sorted(PROFILES.glob("*.json"))}
        self.assertEqual({k: v.entity_category for k, v in got.items()},
                         {"synthetic_national_bank": "national_bank", "synthetic_state_member_bank": "state_member_bank",
                          "synthetic_state_nonmember_bank": "state_nonmember_insured_bank"})
        self.assertTrue(all(v.schema_version == PROFILE_VERSION == "p1-bank-profile-0.1" and v.synthetic for v in got.values()))

    def test_schema_rejects_non_synthetic_unknown_fields_wrong_version_and_real_identifiers(self):
        base = json.loads((PROFILES / "synthetic_national_bank.json").read_text())
        for mutate in (lambda d: d.update(synthetic=False), lambda d: d.update(extra_field=1),
                       lambda d: d.update(schema_version="p1-bank-profile-9.9"), lambda d: d.update(profile_id="Bad Id!"),
                       lambda d: d.update(description="FDIC cert 12345"), lambda d: d["charter"].update(charter_type="credit_union"),
                       lambda d: d["size_risk_category"].update(total_consolidated_assets_usd=-1)):
            d = copy.deepcopy(base)
            mutate(d)
            with self.assertRaises(ValidationError):
                BankProfile.model_validate(d)

    def test_unknown_facts_never_guess_a_category(self):
        d = json.loads((PROFILES / "synthetic_state_member_bank.json").read_text())
        d["charter"]["federal_reserve_member"] = "unknown"
        p = BankProfile.model_validate(d)
        self.assertEqual(p.entity_category, "unknown")
        self.assertIn("charter.federal_reserve_member", p.missing_facts())

    def test_aggregate_is_conservative(self):
        self.assertEqual(aggregate(["assessed_not_applicable", "assessed_conditional"]), "assessed_conditional")
        self.assertEqual(aggregate(["assessed_not_applicable"] * 2), "assessed_not_applicable")
        self.assertEqual(aggregate(["assessed_not_applicable", "not_assessed"]), "not_assessed")
        self.assertEqual(aggregate([]), "not_assessed")


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        ecfr = {"source_class": "regulation", "publisher": "eCFR", "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28"}
        docs = {f"ecfr:12:{p}": {**ecfr, "citation": f"12 CFR Part {p}", "title": "T",
                                 "asset": asset(root, f"e{p}.html", f'<div class="part" id="part-{p}"><h1>PART {p}—X</h1>{h}</div>'.encode())}
                for p, h in ECFR.items()}
        docs[FR_KEY] = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01",
                        "document_number": FR_KEY[3:], "title": "P", "document_type": "Proposed Rule", "comments_close_on": "2026-12-01",
                        "asset": asset(root, "p.xml", FR_PROP)}
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "d.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.cond = Path(cls.tmp.name) / "conditions.json"
        cls.cond.write_text(json.dumps(CONDITIONS))
        cls.index = search.Index(cls.db)
        cls.index._applicability = ApplicabilityEngine(cls.index.conn, cls.cond)

    @classmethod
    def tearDownClass(cls):
        cls.index.conn.close()
        cls.tmp.cleanup()

    def test_conditions_are_verified_against_the_index(self):
        self.assertTrue(all(c["verified"] for c in self.index.applicability.conditions.values()))
        bad = copy.deepcopy(CONDITIONS)
        bad["conditions"][0]["sources"][0]["quote"] = "Each credit union must"
        path = Path(self.tmp.name) / "bad.json"
        path.write_text(json.dumps(bad))
        eng = ApplicabilityEngine(self.index.conn, path)
        r = eng.assess_part(profile("national_bank"), "12", "3")
        self.assertEqual(r["applicability_status"], "not_assessed")
        self.assertIn("not found verbatim", r["reason"])

    def test_each_profile_routes_to_its_own_capital_part(self):
        want = {"national_bank": {"3": "assessed_conditional", "217": "assessed_not_applicable", "324": "assessed_not_applicable"},
                "state_member_bank": {"3": "assessed_not_applicable", "217": "assessed_conditional", "324": "assessed_not_applicable"},
                "state_nonmember_bank": {"3": "assessed_not_applicable", "217": "assessed_not_applicable", "324": "assessed_conditional"}}
        for name, parts in want.items():
            for part, status in parts.items():
                r = self.index.applicability.assess_part(profile(name), "12", part)
                self.assertEqual(r["applicability_status"], status, (name, part))
                self.assertEqual(r["legal_validity"], "not_determined")
                self.assertTrue(r["basis"] and all(b["verified"] for b in r["basis"]))

    def test_part_without_condition_and_missing_profile_stay_not_assessed(self):
        eng = self.index.applicability
        self.assertEqual(eng.assess_part(profile("national_bank"), "12", "249")["applicability_status"], "not_assessed")
        self.assertEqual(eng.assess_part(None, "12", "3")["applicability_status"], "not_assessed")

    def test_holding_company_needs_a_stated_domestic_domicile(self):
        d = json.loads((PROFILES / "synthetic_state_member_bank.json").read_text())
        d["legal_entity"].update(entity_type="bank_holding_company", domiciled_in_united_states="unknown")
        r = self.index.applicability.assess_part(BankProfile.model_validate(d), "12", "217")
        self.assertEqual(r["applicability_status"], "not_assessed")
        self.assertIn("legal_entity.domiciled_in_united_states", r["missing_facts"])

    def test_answer_without_profile_is_unchanged_and_with_profile_is_assessed(self):
        q = "minimum common equity tier 1 capital ratio"
        plain = self.index.answer(q, k=6)
        self.assertEqual(plain["applicability_status"], "not_assessed")
        self.assertNotIn("bank_profile", plain)
        self.assertTrue(all("applicability" not in r for r in plain["results"]))
        a = self.index.answer(q, k=6, bank_profile=profile("national_bank"))
        self.assertNotEqual(a["applicability_status"], "not_assessed")
        self.assertEqual(a["legal_validity"], "not_determined")
        self.assertIn("3", {r["applicability"]["part"] for r in a["results"]})   # the national bank's own part is among the results
        self.assertEqual(a["applicability_status"], "assessed_conditional")
        for r in a["results"]:
            ap = r["applicability"]
            if ap["part"] == "3":
                self.assertEqual(ap["applicability_status"], "assessed_conditional")
            if ap["part"] in ("217", "324"):
                self.assertEqual(ap["applicability_status"], "assessed_not_applicable")

    def test_delta_assesses_current_and_each_instrument_separately(self):
        d = self.index.delta("12 CFR 3.10(a)(1)", bank_profile=profile("national_bank"))
        self.assertEqual(d["applicability_status"], "assessed_conditional")
        recs = {r["doc_key"]: r for r in d["instrument_classification"]}
        r = recs[FR_KEY]
        self.assertEqual(r["classification"], "matched_provision")
        self.assertEqual(r["applicability_status"], "assessed_conditional")
        self.assertTrue(any("proposal" in q for q in r["applicability"]["unevaluated_qualifiers"]))
        self.assertFalse(r["lifecycle_state"].startswith(("final", "current")))
        self.assertEqual((r["current"]["scope"], r["future"]["scope"]), ("current_baseline", "future_instrument"))
        self.assertEqual(evaluate._delta_applicability_failures(d), [])
        other = self.index.delta("12 CFR 3.10(a)(1)", bank_profile=profile("state_nonmember_bank"))
        self.assertEqual(other["applicability_status"], "assessed_not_applicable")

    def test_delta_without_profile_and_unclassified_delta_stay_not_assessed(self):
        d = self.index.delta("12 CFR 3.10(a)(1)")
        self.assertNotIn("bank_profile", d)
        self.assertTrue(all(r["applicability_status"] == "not_assessed" for r in d["instrument_classification"]))
        u = self.index.delta("12 CFR 3.10 and 324.10", bank_profile=profile("national_bank"))
        self.assertEqual(u["applicability"]["applicability_status"], "not_assessed")
        self.assertEqual(u["applicability_status"], "not_assessed")

    def test_evaluation_rejects_unsupported_or_unprofiled_assessments(self):
        d = self.index.delta("12 CFR 3.10(a)(1)", bank_profile=profile("national_bank"))
        bad = copy.deepcopy(d)
        rec = next(r for r in bad["instrument_classification"] if r["doc_key"] == FR_KEY)
        rec["applicability"]["basis"] = []
        self.assertTrue(evaluate._delta_applicability_failures(bad))
        bad = copy.deepcopy(d)
        next(r for r in bad["instrument_classification"] if r["doc_key"] == FR_KEY)["applicability"]["unevaluated_qualifiers"] = []
        self.assertTrue(evaluate._delta_applicability_failures(bad))
        plain = copy.deepcopy(self.index.delta("12 CFR 3.10(a)(1)"))
        plain["instrument_classification"][0]["applicability_status"] = "assessed_conditional"
        self.assertTrue(evaluate.delta_classification_failures(plain))


if __name__ == "__main__":
    unittest.main()
