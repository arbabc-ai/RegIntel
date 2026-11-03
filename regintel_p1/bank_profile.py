"""Versioned bank profile for conditional applicability routing (P1.2 step 1), schema `p1-bank-profile-0.1`.

A profile states facts about one SYNTHETIC banking organization. It is input to routing only: no field is inferred from a
name, and nothing here is a legal determination. Facts that are not known stay `unknown`, which the applicability engine
reports as a missing fact rather than guessing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

PROFILE_VERSION = "p1-bank-profile-0.1"

Charter = Literal["national", "federal_savings", "state", "state_savings", "unknown"]
LegalEntity = Literal["bank", "savings_association", "bank_holding_company", "savings_and_loan_holding_company", "unknown"]
Agency = Literal["OCC", "FRB", "FDIC", "unknown"]
Category = Literal["category_i", "category_ii", "category_iii", "category_iv", "not_categorized", "unknown"]
CapitalApproach = Literal["standardized", "advanced_approaches", "unknown"]
CallReportForm = Literal["FFIEC 031", "FFIEC 041", "FFIEC 051", "not_applicable", "unknown"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CharterFacts(_Strict):
    charter_type: Charter
    chartering_authority: str = Field(description="as stated in the profile, e.g. 'OCC' or 'state of <synthetic state>'")
    federal_reserve_member: Literal["yes", "no", "unknown"]
    fdic_insured: Literal["yes", "no", "unknown"]


class LegalEntityFacts(_Strict):
    entity_type: LegalEntity
    domiciled_in_united_states: Literal["yes", "no", "unknown"]
    top_tier_holding_company: Optional[str] = None


class BusinessModel(_Strict):
    activities: tuple[str, ...] = ()
    notes: str = ""


class Regulator(_Strict):
    primary_federal_regulator: Agency
    basis: str = Field(description="the profile author's stated basis; not verified by this schema")


class SizeRiskCategory(_Strict):
    total_consolidated_assets_usd: Optional[int] = Field(default=None, ge=0)
    as_of_date: Optional[str] = None
    prudential_category: Category = "unknown"


class CapitalRegime(_Strict):
    approach: CapitalApproach
    cblr_election: Literal["elected", "not_elected", "unknown"]


class FormEligibility(_Strict):
    call_report_form: CallReportForm
    holding_company_forms: tuple[str, ...] = ()


class BankProfile(_Strict):
    """One synthetic banking organization. `synthetic` must be true: Phase 1 uses public and synthetic data only."""
    schema_version: Literal["p1-bank-profile-0.1"] = PROFILE_VERSION
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    synthetic: Literal[True]
    description: str = ""
    charter: CharterFacts
    legal_entity: LegalEntityFacts
    business_model: BusinessModel
    regulator: Regulator
    size_risk_category: SizeRiskCategory
    capital_regime: CapitalRegime
    form_eligibility: FormEligibility

    @field_validator("description")
    @classmethod
    def _no_real_identifiers(cls, v):
        if any(tok in v.lower() for tok in ("rssd", "cert #", "fdic cert", "routing number")):
            raise ValueError("profiles are synthetic: no real institution identifiers")
        return v

    @computed_field
    @property
    def entity_category(self) -> str:
        """Routing category derived from the stated facts only; `unknown` when any needed fact is unknown."""
        c, e = self.charter, self.legal_entity.entity_type
        if e == "bank_holding_company":
            return "bank_holding_company"
        if e == "savings_and_loan_holding_company":
            return "savings_and_loan_holding_company"
        if c.charter_type == "national" and e == "bank":
            return "national_bank"
        if c.charter_type == "federal_savings" and e == "savings_association":
            return "federal_savings_association"
        if c.charter_type == "state_savings" and e == "savings_association":
            return "state_savings_association"
        if c.charter_type == "state" and e == "bank":
            if c.federal_reserve_member == "yes":
                return "state_member_bank"
            if c.federal_reserve_member == "no" and c.fdic_insured == "yes":
                return "state_nonmember_insured_bank"
        return "unknown"

    def missing_facts(self):
        """Profile facts left `unknown` (or empty) that routing or later conditions may need."""
        out = []
        for path, value in (("charter.charter_type", self.charter.charter_type), ("charter.federal_reserve_member", self.charter.federal_reserve_member),
                            ("charter.fdic_insured", self.charter.fdic_insured), ("legal_entity.entity_type", self.legal_entity.entity_type),
                            ("legal_entity.domiciled_in_united_states", self.legal_entity.domiciled_in_united_states),
                            ("regulator.primary_federal_regulator", self.regulator.primary_federal_regulator),
                            ("size_risk_category.prudential_category", self.size_risk_category.prudential_category),
                            ("capital_regime.approach", self.capital_regime.approach), ("capital_regime.cblr_election", self.capital_regime.cblr_election),
                            ("form_eligibility.call_report_form", self.form_eligibility.call_report_form)):
            if value == "unknown":
                out.append(path)
        if self.size_risk_category.total_consolidated_assets_usd is None:
            out.append("size_risk_category.total_consolidated_assets_usd")
        return out

    def summary(self):
        return {"profile_id": self.profile_id, "schema_version": self.schema_version, "synthetic": True,
                "entity_category": self.entity_category}


def load_profile(path):
    return BankProfile.model_validate(json.loads(Path(path).read_text()))
