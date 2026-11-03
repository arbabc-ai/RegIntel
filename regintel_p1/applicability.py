"""Conditional applicability routing of acquired provisions against a synthetic bank profile (P1.2 step 1).

Statuses:
  not_assessed             no profile; no verified scope condition for the part; or a needed profile fact is unknown.
  assessed_conditional     the profile's entity category is inside the scope stated by the cited scope provision. It stays
                           conditional: the condition's qualifiers, the provision's own terms and (for future instruments)
                           adoption and effective dates are NOT evaluated.
  assessed_not_applicable  the profile's entity category is outside the scope stated by the cited scope provision. This is
                           routing by stated scope only: a counterpart rule of another agency, or a later amendment, may apply.

No status is a legal determination: every result carries legal_validity "not_determined", the cited sources and the
unevaluated qualifiers. Conditions are used only when every quoted source is present, verbatim, in the index.
"""
import hashlib
import json
from pathlib import Path

CONDITIONS_FILE = Path(__file__).with_name("applicability_conditions.json")
ENGINE_VERSION = "p1-applicability-engine-0.1"
STATUSES = ("not_assessed", "assessed_conditional", "assessed_not_applicable")
NOTE = ("Conditional routing by the scope stated in the cited acquired provisions; not a legal determination of "
        "applicability or compliance. Unevaluated qualifiers and the provision's own terms still apply.")


def _norm(s):
    return " ".join((s or "").replace("’", "'").split())


class ApplicabilityEngine:
    def __init__(self, conn, conditions_path=CONDITIONS_FILE):
        raw = Path(conditions_path).read_bytes()
        spec = json.loads(raw)
        self.version = spec["version"]
        self.conditions_sha256 = hashlib.sha256(raw).hexdigest()
        self.conditions = {}
        for c in spec["conditions"]:
            checked, ok = [], True
            for s in c["sources"]:
                row = conn.execute("""SELECT p.text, p.citation, d.sha256 FROM provision p JOIN source_document d ON d.id=p.doc_id
                                      WHERE d.doc_key=? AND p.locator=?""", (s["doc_key"], s["locator"])).fetchone()
                found = bool(row) and _norm(s["quote"]) in _norm(row[0])
                ok = ok and found
                checked.append({**s, "verified": found, "citation": row[1] if row else None,
                                "provision_text_sha256": hashlib.sha256(row[0].encode("utf-8")).hexdigest() if row else None,
                                "document_sha256": row[2] if row else None})
            self.conditions[(c["title"], c["part"])] = {**c, "sources": checked, "verified": ok}

    def _base(self, profile):
        return {"profile": profile.summary() if profile else None, "engine_version": ENGINE_VERSION,
                "conditions_version": self.version, "conditions_sha256": self.conditions_sha256,
                "legal_validity": "not_determined", "note": NOTE}

    def assess_part(self, profile, title, part, future=None):
        """Assess one CFR part's stated scope for a profile. `future`: {document_type, lifecycle_state} for an instrument."""
        out = self._base(profile)
        out.update({"title": title, "part": part, "condition_id": None, "basis": [], "unevaluated_qualifiers": [], "missing_facts": []})
        if profile is None:
            return {**out, "applicability_status": "not_assessed", "reason": "no bank profile supplied"}
        cond = self.conditions.get((str(title), str(part)))
        if not cond:
            return {**out, "applicability_status": "not_assessed", "reason": f"no scope condition is recorded for {title} CFR part {part}"}
        out["condition_id"] = cond["condition_id"]
        out["basis"] = [{k: s[k] for k in ("doc_key", "locator", "citation", "quote", "verified", "provision_text_sha256", "document_sha256")}
                        for s in cond["sources"]]
        if not cond["verified"]:
            return {**out, "applicability_status": "not_assessed", "reason": "a quoted scope source was not found verbatim in this index"}
        cat = profile.entity_category
        if cat == "unknown":
            return {**out, "applicability_status": "not_assessed", "reason": "the profile's entity category cannot be derived",
                    "missing_facts": profile.missing_facts()}
        qualifiers = list(cond["unevaluated_qualifiers"])
        if future:
            qualifiers.append(_future_qualifier(future))
        if cat not in cond["in_scope_entity_categories"]:
            return {**out, "applicability_status": "assessed_not_applicable", "matched_entity_category": cat,
                    "unevaluated_qualifiers": qualifiers,
                    "reason": f"entity category {cat} is outside the scope stated by {_cites(cond)}; a counterpart rule may apply"}
        if cond.get("requires_domestic_holding_company") and cat.endswith("holding_company") \
                and profile.legal_entity.domiciled_in_united_states != "yes":
            return {**out, "applicability_status": "not_assessed", "matched_entity_category": cat,
                    "reason": "the cited scope requires a holding company domiciled in the United States; the profile does not state it",
                    "missing_facts": ["legal_entity.domiciled_in_united_states"]}
        return {**out, "applicability_status": "assessed_conditional", "matched_entity_category": cat,
                "unevaluated_qualifiers": qualifiers, "missing_facts": profile.missing_facts(),
                "reason": f"entity category {cat} is inside the scope stated by {_cites(cond)}; conditional on the unevaluated qualifiers"}

    def assess_provision(self, conn, profile, provision_id):
        row = conn.execute("""SELECT p.title_no, p.part_no, d.doc_key FROM provision p JOIN source_document d ON d.id=p.doc_id
                              WHERE p.id=?""", (provision_id,)).fetchone()
        if not row or not row[0] or not row[1]:
            out = self._base(profile)
            return {**out, "applicability_status": "not_assessed", "title": None, "part": None, "condition_id": None, "basis": [],
                    "unevaluated_qualifiers": [], "missing_facts": [],
                    "reason": "the provision has no CFR part (for example Federal Register preamble text)"}
        future = None
        if row[2].startswith("fr:"):
            rm = conn.execute("SELECT document_type FROM rulemaking WHERE doc_key=?", (row[2],)).fetchone()
            future = {"document_type": rm[0] if rm else None, "lifecycle_state": None}
        return self.assess_part(profile, row[0], row[1], future)


def _cites(cond):
    return ", ".join(f"12 CFR {s['locator']}" if s["doc_key"].startswith("ecfr:12") else s["locator"] for s in cond["sources"])


def _future_qualifier(future):
    dtype, state = future.get("document_type"), future.get("lifecycle_state") or ""
    if dtype == "Proposed Rule":
        return "future instrument is a proposal: not an operative requirement; scope after any adoption is not evaluated"
    if state == "final_effective_date_pending":
        return "future instrument is not yet effective per its publisher date; stays and partial effect not evaluated"
    return "future instrument: operative effect, stays and compliance dates not evaluated; it may itself change the stated scope"


def aggregate(statuses):
    """Top-level status over several items: conditional if any item is conditional; not applicable only if every item is;
    otherwise (including a mix of not applicable and not assessed) not assessed, because an unassessed item could apply."""
    s = set(statuses)
    if "assessed_conditional" in s:
        return "assessed_conditional"
    if s == {"assessed_not_applicable"}:
        return "assessed_not_applicable"
    return "not_assessed"
