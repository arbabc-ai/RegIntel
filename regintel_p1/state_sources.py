"""Manifest ingestion metadata for official state and territorial sources (no parser, no indexing).

A manifest entry whose `source_class` starts with `state_` is validated against `state_sources.json`. A valid entry is recorded in
source_status as `registered_not_indexed`; an invalid one as `skipped_invalid_state_metadata` with every reason listed. Either way its text
never enters the provision index, so no state-law requirement, coverage or applicability is claimed.

Host approvals are honoured only when backed by evidence: every approved host and every host/source-class pair cites a record in the
evidence registry (`state_source_evidence.json`) by `evidence_id`, and the record must be well formed, on the same host, at the right
level, not on an unconfirmed vendor or legacy host, and fresh under the registry's versioned validity policy. From
contract v0.4 (S7-P1-59) a source-class record is not honoured when another host-level record on the same host has the same sha256 and
byte count (a response indistinguishable from the entry page establishes no source class), and a redirect chain relied on for a
non-https requested_url must start at requested_url and be contiguous. The registry grants no right:
public access is not reuse permission, so `usage_status: permitted` still needs a named operator permission reference.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

CONTRACT_PATH = Path(__file__).with_name("state_sources.json")
EVIDENCE_FIELDS = ("evidence_id", "level", "host", "requested_url", "final_url", "redirect_chain", "status", "access_time", "sha256",
                   "bytes", "title", "publisher", "publisher_basis", "publisher_basis_detail", "role")


def contract():
    return json.loads(CONTRACT_PATH.read_text())


def registry(spec=None):
    spec = spec or contract()
    return json.loads(CONTRACT_PATH.with_name(spec.get("evidence_registry", "state_source_evidence.json")).read_text())


def is_state_entry(record):
    return str(record.get("source_class", "")).startswith("state_")


def _when(value):
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def evidence_problems(evidence_id, reg, host, jur, reference, source_class=None):
    """Why evidence `evidence_id` does not support approving `host` (or `host` for `source_class`); empty when it does."""
    policy = reg["validity_policy"]
    rec = reg.get("evidence", {}).get(evidence_id) if evidence_id else None
    if not evidence_id:
        return ["no evidence_id is cited"]
    if rec is None:
        return [f"evidence {evidence_id} is not in the registry"]
    problems = [f"evidence {evidence_id} lacks {f}" for f in EVIDENCE_FIELDS if f not in rec or rec[f] in (None, "")
                and f != "redirect_chain"]
    if problems:
        return problems
    if rec["evidence_id"] != evidence_id:
        problems.append(f"evidence {evidence_id} records a different evidence_id")
    if rec["level"] not in ("host", "source_class"):
        problems.append(f"evidence {evidence_id} has unknown level {rec['level']}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(rec["sha256"])):
        problems.append(f"evidence {evidence_id} has a malformed sha256")
    if not isinstance(rec["bytes"], int) or rec["bytes"] <= 0:
        problems.append(f"evidence {evidence_id} has a malformed byte count")
    if rec["status"] != policy["required_status"]:
        problems.append(f"evidence {evidence_id} status {rec['status']} is not {policy['required_status']}")
    final, requested = urlparse(rec["final_url"]), urlparse(rec["requested_url"])
    if final.scheme != policy["required_final_scheme"]:
        problems.append(f"evidence {evidence_id} final_url is not https")
    chain = rec["redirect_chain"] or []
    if requested.scheme != "https":
        if not (chain and chain[-1].get("to") == rec["final_url"]):
            problems.append(f"evidence {evidence_id} requested_url is not https and no redirect chain ends at final_url")
        elif chain[0].get("from") != rec["requested_url"] or any(a.get("to") != b.get("from") for a, b in zip(chain, chain[1:])):
            problems.append(f"evidence {evidence_id} redirect chain does not start at requested_url or is not contiguous")
    if rec["publisher_basis"] not in policy["publisher_bases"]:
        problems.append(f"evidence {evidence_id} publisher_basis {rec['publisher_basis']} is not an accepted basis")
    if final.hostname != rec["host"]:
        problems.append(f"evidence {evidence_id} final_url host {final.hostname} does not match its recorded host {rec['host']}")
    if rec["host"] in (jur.get("unconfirmed_candidates") or {}):
        problems.append(f"evidence {evidence_id} is on the unconfirmed vendor or legacy host {rec['host']}")
    if rec["host"] != host:
        problems.append(f"evidence {evidence_id} belongs to host {rec['host']}, not {host}")
    if source_class is not None:
        if rec["level"] != "source_class":
            problems.append(f"evidence {evidence_id} is a host-level observation and cannot establish source class {source_class}")
        else:
            if source_class not in (rec.get("source_classes") or []):
                problems.append(f"evidence {evidence_id} does not establish source class {source_class}")
            for other_id, other in sorted((reg.get("evidence") or {}).items()):
                if other_id != evidence_id and other.get("host") == rec["host"] and other.get("level") == "host" \
                        and other.get("sha256") == rec["sha256"] and other.get("bytes") == rec["bytes"]:
                    problems.append(f"evidence {evidence_id} has the same sha256 and byte count as host-level record {other_id} on "
                                    f"{rec['host']}: a response indistinguishable from the entry page does not establish source class "
                                    f"{source_class}")
    seen, ref = _when(rec["access_time"]), _when(reference)
    if seen is None:
        problems.append(f"evidence {evidence_id} has a malformed access_time")
    elif ref is None:
        problems.append("no valid reference date for the evidence validity policy")
    elif (ref - seen).days > policy["max_age_days"]:
        problems.append(f"evidence {evidence_id} is stale: observed {rec['access_time']}, more than {policy['max_age_days']} days "
                        f"before {reference} ({policy['policy_version']})")
    return problems


def approvals(jurisdiction, reference, spec=None, reg=None):
    """host -> {"approved": bool, "source_classes": [approved classes], "problems": [...]} for the jurisdiction's listed hosts."""
    spec = spec or contract()
    reg = reg if reg is not None else registry(spec)
    jur = spec["jurisdictions"].get(jurisdiction) or {}
    out = {}
    for host in jur.get("official_hosts", []):
        meta = (jur.get("hosts") or {}).get(host) or {}
        bind = meta.get("evidence") if isinstance(meta.get("evidence"), dict) else {}
        problems = [f"host {host}: {p}" for p in evidence_problems(bind.get("host"), reg, host, jur, reference)]
        classes = []
        listed = meta.get("source_classes") or []
        for cls in sorted(set(listed) | set((bind.get("source_classes") or {}))):
            if cls not in listed:
                problems.append(f"host {host}: evidence is bound for {cls}, which the host does not list")
                continue
            p = evidence_problems((bind.get("source_classes") or {}).get(cls), reg, host, jur, reference, cls)
            if p:
                problems += [f"host {host} / {cls}: {x}" for x in p]
            else:
                classes.append(cls)
        host_ok = not any(p.startswith(f"host {host}: ") for p in problems)
        out[host] = {"approved": host_ok, "source_classes": classes if host_ok else [], "problems": problems}
    return out


def validate(key, record, spec=None, reg=None):
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
    if jur is not None:
        if url.hostname not in jur["official_hosts"]:
            problems.append(f"source host {url.hostname} is not a confirmed official host for {record['jurisdiction']}")
        else:
            reference = (record.get("asset") or {}).get("retrieved_at")
            approval = approvals(record["jurisdiction"], reference, spec, reg).get(url.hostname, {})
            if not approval.get("approved"):
                problems.append(f"approval of host {url.hostname} is not supported by valid evidence: " + "; ".join(approval.get("problems", [])))
            elif record.get("source_class") and record["source_class"] not in approval["source_classes"]:
                host = (jur.get("hosts") or {}).get(url.hostname) or {}
                if record["source_class"] in (host.get("source_classes") or []):
                    problems.append(f"source_class {record['source_class']} on {url.hostname} is not supported by valid evidence: " +
                                    "; ".join(p for p in approval["problems"] if record["source_class"] in p))
                else:
                    problems.append(f"source_class {record['source_class']} is not served by {url.hostname} "
                                    f"({', '.join(host.get('source_classes') or [])})")
    rights = record.get("rights") or {}
    problems += [f"missing rights field {f}" for f in spec["required_rights_fields"] if not rights.get(f)]
    if rights.get("usage_status") and rights["usage_status"] not in spec["usage_statuses"]:
        problems.append(f"unknown rights usage_status {rights['usage_status']}")
    if rights.get("usage_status") == "permitted" and not rights.get("permission_reference"):
        problems.append("usage_status permitted needs rights.permission_reference naming an operator decision (no reuse permission is inferred)")
    asset = record.get("asset") or {}
    problems += [f"missing asset field {f}" for f in ("path", "sha256", "bytes", "retrieved_at") if not asset.get(f)]
    return problems
