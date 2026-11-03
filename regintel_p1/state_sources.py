"""Manifest ingestion metadata for official state and territorial sources (no parser, no indexing).

A manifest entry whose `source_class` starts with `state_` is validated against `state_sources.json`. A valid entry is recorded in
source_status as `registered_not_indexed`; an invalid one as `skipped_invalid_state_metadata` with every reason listed. Either way its text
never enters the provision index, so no state-law requirement, coverage or applicability is claimed.
"""
import json
from pathlib import Path
from urllib.parse import urlparse

CONTRACT_PATH = Path(__file__).with_name("state_sources.json")


def contract():
    return json.loads(CONTRACT_PATH.read_text())


def is_state_entry(record):
    return str(record.get("source_class", "")).startswith("state_")


def validate(key, record, spec=None):
    """Problems (empty when valid) for a state manifest entry `key` -> `record`."""
    spec = spec or contract()
    problems = [f"missing field {f}" for f in spec["required_fields"] if not record.get(f)]
    if record.get("source_class") and record["source_class"] not in spec["source_classes"]:
        problems.append(f"unknown source_class {record['source_class']}")
    jur = spec["jurisdictions"].get(record.get("jurisdiction") or "")
    if record.get("jurisdiction") and jur is None:
        problems.append(f"jurisdiction {record['jurisdiction']} is not registered in the state-source contract")
    if not key.startswith(f"state:{record.get('jurisdiction')}:"):
        problems.append("manifest key must start with state:<jurisdiction>:")
    url = urlparse(record.get("source_url") or "")
    if record.get("source_url") and url.scheme != "https":
        problems.append("source_url must be https")
    if jur is not None and url.hostname not in jur["official_hosts"]:
        problems.append(f"source host {url.hostname} is not a confirmed official host for {record['jurisdiction']}")
    rights = record.get("rights") or {}
    problems += [f"missing rights field {f}" for f in spec["required_rights_fields"] if not rights.get(f)]
    if rights.get("usage_status") and rights["usage_status"] not in spec["usage_statuses"]:
        problems.append(f"unknown rights usage_status {rights['usage_status']}")
    asset = record.get("asset") or {}
    problems += [f"missing asset field {f}" for f in ("path", "sha256", "bytes", "retrieved_at") if not asset.get(f)]
    return problems
