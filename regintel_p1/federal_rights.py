"""DR-6 federal rights fields for eCFR and Federal Register manifest entries (S7-P1-72).

Decision basis: DR-2 and DR-6 in the founder record `2026-11-04_s7-regintel-p1-founder-decisions_accepted.md`
(AI-OS projects/velodyn/Velodyn_BankOS/decisions/). DR-2: eCFR and the Federal Register are public domain US-government works;
reuse, indexing and quoting are allowed; keep source URLs and dates. DR-6: S7 adds rights/terms fields to the federal manifest and
fills them per DR-2.

Field names. Federal entries use exactly the field names that state entries already carry in their `rights` object
(regintel_p1/state_sources.json `required_rights_fields` plus `permission_reference`, which that contract requires whenever
usage_status is permitted). No new vocabulary is introduced; the DR-6 concepts map onto those names as follows:

    DR-6 concept          state/federal field       federal value and meaning
    rights_status         usage_status              "permitted" (a value from the state contract's usage_statuses), per DR-2
    rights_decision_ref   permission_reference      names DR-2 and DR-6 and the founder record file
    rights_basis          permission_reference      the same string states the basis: public domain US-government work
    terms_source_url      terms_url                 the publisher's site root. NOTE: no publisher terms page has been fetched
    / terms_note                                    or observed (S7-P1-51 recorded none, and this task fetched nothing); the
                                                    rights rest on the DR-2 decision, not on a terms page
    rights_recorded_on    terms_checked_at          RECORDED_ON = "2026-11-04", the DR-6 decision date. It is written for
                                                    every federal entry, including entries downloaded later; it is neither the
                                                    run date nor a date a terms page was checked (S7-P1-73 choice)

Existing entries are not rewritten: the corpus manifest pinned by indexes 49, 58 and 65 keeps its bytes, and their rights live
in a sidecar overlay (federal_rights.json beside the manifest) keyed by doc_key. Whether the pinned manifest is later rewritten
in place is a separate choice that S7-P1-72 does not make. New entries written by scripts/corpus_download.py carry the record
inline under `rights`, and Corpus.record refuses a federal entry without a complete record.

The overlay records the sha256 of the manifest it was built from (`manifest_sha256`, from S7-P1-73). check() compares it with
the manifest actually read: a different value is a manifest mismatch and fails the check; an overlay without the field (the
S7-P1-72 overlay) is reported as `manifest_sha256: absent`, which does not fail. An overlay record whose doc_key has no
federal manifest entry also fails the check.

No rule here determines legal validity or applicability. Corpus.record writes every manifest entry with applicability_status
"not_assessed" (plus indexed/eligible_for_indexing false); manifest entries carry no legal_validity field, and the check report
states legal_validity "not_determined" and applicability_status "not_assessed".
"""
import json
from pathlib import Path

from . import state_sources

DECISION_RECORD = "2026-11-04_s7-regintel-p1-founder-decisions_accepted.md"
RECORDED_ON = "2026-11-04"
OVERLAY_NAME = "federal_rights.json"
OVERLAY_SCHEMA = "p1-federal-rights-overlay-0.1"
CHECK_SCHEMA = "p1-federal-rights-check-0.1"
PUBLISHER_SITES = {"eCFR": "https://www.ecfr.gov/", "Federal Register": "https://www.federalregister.gov/"}
FEDERAL_PREFIXES = ("ecfr:", "fr:")
PERMISSION_REFERENCE = (f"DR-2 (rights) and DR-6 (fields), founder record {DECISION_RECORD}: public domain US-government work "
                        "(eCFR / Federal Register); reuse, indexing and quoting allowed; source URLs and dates kept")


def rights_fields():
    """The state contract's rights field names plus permission_reference: the one field set used for state and federal records."""
    return tuple(state_sources.contract()["required_rights_fields"]) + ("permission_reference",)


def is_federal(doc_key, entry=None):
    return str(doc_key).startswith(FEDERAL_PREFIXES)


def federal_rights(publisher):
    """The DR-2 rights record for a federal publisher ("eCFR" or "Federal Register")."""
    if publisher not in PUBLISHER_SITES:
        raise ValueError(f"no DR-2 rights basis is recorded for publisher {publisher!r}")
    return {"terms_url": PUBLISHER_SITES[publisher], "terms_checked_at": RECORDED_ON, "usage_status": "permitted",
            "permission_reference": PERMISSION_REFERENCE}


def record_problems(rights):
    """Why `rights` is not a complete federal rights record; empty when it is."""
    if not isinstance(rights, dict):
        return ["no rights record"]
    fields = rights_fields()
    problems = [f"missing rights field {f}" for f in fields if not rights.get(f)]
    problems += [f"unexpected rights field {f}" for f in sorted(set(rights) - set(fields))]
    if rights.get("usage_status") and rights["usage_status"] not in state_sources.contract()["usage_statuses"]:
        problems.append(f"unknown usage_status {rights['usage_status']}")
    if rights.get("usage_status") == "permitted" and DECISION_RECORD not in str(rights.get("permission_reference") or ""):
        problems.append(f"usage_status permitted must cite the founder record {DECISION_RECORD}")
    return problems


def build_overlay(manifest, manifest_sha256):
    """One DR-2 rights record per existing federal entry, keyed by doc_key, pinned to the manifest's sha256."""
    records = {}
    for key, entry in sorted(manifest["documents"].items()):
        if is_federal(key, entry):
            records[key] = federal_rights(entry.get("publisher"))
    return {"schema": OVERLAY_SCHEMA, "decision_record": DECISION_RECORD, "decisions": ["DR-2", "DR-6"], "manifest_sha256": manifest_sha256,
            "recorded_on": RECORDED_ON, "note": "Sidecar rights records for federal entries of a pinned manifest whose bytes are not "
            "rewritten; field names as in state entries' rights objects (see regintel_p1/federal_rights.py).",
            "records": records}


def check(manifest, overlay=None, manifest_sha256=None):
    """Every federal entry's rights status: inline `rights` first, else the overlay record; incomplete entries are listed.
    Fails on an incomplete entry, an orphan overlay record, or an overlay pinned to a different manifest sha256."""
    records = (overlay or {}).get("records") or {}
    rows, incomplete = [], []
    for key, entry in sorted(manifest["documents"].items()):
        if not is_federal(key, entry):
            continue
        source = "inline" if isinstance(entry.get("rights"), dict) else ("overlay" if key in records else None)
        rights = entry.get("rights") if source == "inline" else records.get(key)
        problems = record_problems(rights)
        rows.append({"doc_key": key, "publisher": entry.get("publisher"), "rights_source": source, "complete": not problems,
                     "problems": problems})
        if problems:
            incomplete.append(key)
    orphans = sorted(set(records) - {r["doc_key"] for r in rows})
    pinned = (overlay or {}).get("manifest_sha256")
    overlay_pin = None if overlay is None else (pinned or "absent")
    mismatch = bool(pinned) and manifest_sha256 is not None and pinned != manifest_sha256
    return {"schema": CHECK_SCHEMA, "decision_record": DECISION_RECORD, "rights_fields": list(rights_fields()),
            "federal_entries": len(rows), "complete": len(rows) - len(incomplete), "incomplete": incomplete,
            "overlay_records_without_federal_entry": orphans, "overlay_manifest_sha256": overlay_pin,
            "manifest_mismatch": mismatch, "passed": not incomplete and not orphans and not mismatch,
            "legal_validity": "not_determined", "applicability_status": "not_assessed", "entries": rows}


def load_overlay(manifest_path, overlay_path=None):
    path = Path(overlay_path) if overlay_path else Path(manifest_path).with_name(OVERLAY_NAME)
    return (json.loads(path.read_text()), path) if path.exists() else (None, path)
