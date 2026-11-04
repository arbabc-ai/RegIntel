"""Manifest ingestion metadata for official state and territorial sources (no parser, no indexing).

A manifest entry whose `source_class` starts with `state_` is validated against `state_sources.json`. A valid entry is recorded in
source_status as `registered_not_indexed`; an invalid one as `skipped_invalid_state_metadata` with every reason listed. Either way its text
never enters the provision index, so no state-law requirement, coverage or applicability is claimed.

Host approvals are honoured only when backed by evidence: every approved host and every host/source-class pair cites a record in the
evidence registry (`state_source_evidence.json`) by `evidence_id`, and the record must be well formed, on the same host, at the right
level, not on an unconfirmed vendor or legacy host, and fresh under the registry's versioned validity policy. From
contract v0.4 (S7-P1-59) a source-class record is not honoured when another host-level record on the same host has the same sha256 and
byte count (a response indistinguishable from the entry page establishes no source class), and a redirect chain relied on for a
non-https requested_url must start at requested_url and be contiguous. From v0.5 (S7-P1-60) a host whose metadata declares
`endpoint_basis: index_page_only` approves none of its source classes (its evidence is an entry page only); the host itself stays
approved, and an endpoint_basis outside the contract's `endpoint_bases` enum fails the host. From v0.6 (S7-P1-61) a host may declare
`path_pattern`, a regular expression the whole path of an entry's source_url must match; an https requested_url on a different host than
final_url needs a redirect chain that starts at requested_url, is contiguous and ends at final_url; and malformed registry or binding
values (a non-object record or binding, a non-list redirect_chain or source_classes, a non-object hop, a boolean byte count) are
reported as problems rather than raised; only those enumerated values are covered, and a registry missing required structure such as
`validity_policy` still raises. None of these rules can turn a problem into an approval. From v0.7 (S7-P1-62) a path_pattern host also
rejects a source_url carrying a query string or path parameters (fragments stay allowed), and `approval_diagnostics()` reports which
record backs each approval and whether the distinctness check was really exercised. The registry grants no right:
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
    records = reg.get("evidence") if isinstance(reg.get("evidence"), dict) else {}
    if not evidence_id:
        return ["no evidence_id is cited"]
    if not isinstance(evidence_id, str):
        return [f"evidence id {evidence_id!r} is not a string"]
    rec = records.get(evidence_id)
    if rec is None:
        return [f"evidence {evidence_id} is not in the registry"]
    if not isinstance(rec, dict):
        return [f"evidence {evidence_id} is not a record object"]
    problems = [f"evidence {evidence_id} lacks {f}" for f in EVIDENCE_FIELDS
                if f != "redirect_chain" and (f not in rec or rec[f] in (None, ""))]
    if problems:
        return problems
    if rec["evidence_id"] != evidence_id:
        problems.append(f"evidence {evidence_id} records a different evidence_id")
    if rec["level"] not in ("host", "source_class"):
        problems.append(f"evidence {evidence_id} has unknown level {rec['level']}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(rec["sha256"])):
        problems.append(f"evidence {evidence_id} has a malformed sha256")
    if isinstance(rec["bytes"], bool) or not isinstance(rec["bytes"], int) or rec["bytes"] <= 0:
        problems.append(f"evidence {evidence_id} has a malformed byte count")
    if rec["status"] != policy["required_status"]:
        problems.append(f"evidence {evidence_id} status {rec['status']} is not {policy['required_status']}")
    final, requested = urlparse(str(rec["final_url"])), urlparse(str(rec["requested_url"]))
    if final.scheme != policy["required_final_scheme"]:
        problems.append(f"evidence {evidence_id} final_url is not https")
    chain = rec.get("redirect_chain")
    if chain is None:
        chain = []
    if not isinstance(chain, list) or not all(isinstance(hop, dict) for hop in chain):
        problems.append(f"evidence {evidence_id} has a malformed redirect_chain")
        chain = None
    if chain is not None:
        ends = bool(chain) and chain[-1].get("to") == rec["final_url"]
        joined = bool(chain) and chain[0].get("from") == rec["requested_url"] and \
            all(a.get("to") == b.get("from") for a, b in zip(chain, chain[1:]))
        if requested.scheme != "https":
            if not ends:
                problems.append(f"evidence {evidence_id} requested_url is not https and no redirect chain ends at final_url")
            elif not joined:
                problems.append(f"evidence {evidence_id} redirect chain does not start at requested_url or is not contiguous")
        elif requested.hostname != final.hostname and not (ends and joined):
            problems.append(f"evidence {evidence_id} requested_url host {requested.hostname} differs from final_url host {final.hostname} "
                            "and no contiguous redirect chain from requested_url to final_url is recorded")
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
            listed = rec.get("source_classes")
            if listed is not None and not isinstance(listed, list):
                problems.append(f"evidence {evidence_id} has malformed source_classes (not a list)")
            elif source_class not in (listed or []):
                problems.append(f"evidence {evidence_id} does not establish source class {source_class}")
            for other_id, other in sorted(records.items()):
                if other_id != evidence_id and isinstance(other, dict) and other.get("host") == rec["host"] and other.get("level") == "host" \
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
        problems = [] if meta.get("evidence") is None or isinstance(meta["evidence"], dict) else [f"host {host}: evidence binding is not an object"]
        problems += [f"host {host}: {p}" for p in evidence_problems(bind.get("host"), reg, host, jur, reference)]
        bound = bind.get("source_classes")
        bound_ok = bound is None or isinstance(bound, dict)
        bound = bound if isinstance(bound, dict) else {}
        basis = meta.get("endpoint_basis")
        if basis is not None and basis not in (spec.get("endpoint_bases") or []):
            problems.append(f"host {host}: unknown endpoint_basis {basis!r} (allowed: {', '.join(spec.get('endpoint_bases') or [])})")
        classes = []
        listed = meta.get("source_classes") or []
        for cls in sorted(set(listed) | set(bound)):
            if cls not in listed:
                problems.append(f"host {host}: evidence is bound for {cls}, which the host does not list")
                continue
            p = [] if bound_ok else ["evidence source_classes binding is not an object"]
            p += evidence_problems(bound.get(cls), reg, host, jur, reference, cls)
            if basis == "index_page_only":
                p.append(f"host {host} declares endpoint_basis index_page_only: entry-page evidence does not establish source class {cls}")
            if p:
                problems += [f"host {host} / {cls}: {x}" for x in p]
            else:
                classes.append(cls)
        host_ok = not any(p.startswith(f"host {host}: ") for p in problems)
        out[host] = {"approved": host_ok, "source_classes": classes if host_ok else [], "problems": problems}
    return out


DISTINCTNESS_CHECKS = ("checked_distinct", "checked_identical", "vacuous_same_record", "no_host_level_baseline", "not_evaluated")


def approval_diagnostics(jurisdiction, reference, spec=None, reg=None):
    """Per listed host: which evidence record backs the host and each listed class, and whether the S7-P1-59 distinctness check was really
    exercised for the class (`distinctness_check`, one of DISTINCTNESS_CHECKS):
      not_evaluated           the class evidence has problems other than the distinctness problem (or no record is cited);
      vacuous_same_record     the class binding cites the host binding's own record, so there is nothing to compare;
      checked_identical       a host-level record on the host has the same sha256 and bytes (the S7-P1-59 problem is present);
      checked_distinct        a valid host-level record on the host exists and differs in sha256 or bytes;
      no_host_level_baseline  no valid host-level record exists on the host to compare against.
    Pure and read-only: diagnostics withhold nothing and widen nothing; approvals() and validate() are unaffected."""
    spec = spec or contract()
    reg = reg if reg is not None else registry(spec)
    jur = spec["jurisdictions"].get(jurisdiction) or {}
    records = reg.get("evidence") if isinstance(reg.get("evidence"), dict) else {}
    out = {}
    for host in jur.get("official_hosts", []):
        meta = (jur.get("hosts") or {}).get(host) or {}
        bind = meta.get("evidence") if isinstance(meta.get("evidence"), dict) else {}
        bound = bind.get("source_classes") if isinstance(bind.get("source_classes"), dict) else {}
        hid = bind.get("host")
        hrec = records.get(hid) if isinstance(hid, str) else None
        hlevel = hrec["level"] if isinstance(hrec, dict) and hrec.get("level") in ("host", "source_class") \
            and not evidence_problems(hid, reg, host, jur, reference) else "missing_or_invalid"
        baselines = [(i, r) for i, r in sorted(records.items()) if isinstance(r, dict) and r.get("host") == host
                     and r.get("level") == "host" and not evidence_problems(i, reg, host, jur, reference)]
        same, classes = {}, {}
        for cls in meta.get("source_classes") or []:
            cid = bound.get(cls)
            same[cls] = cid is not None and cid == hid
            problems = evidence_problems(cid, reg, host, jur, reference, cls)
            other = [p for p in problems if "same sha256 and byte count as host-level record" not in p]
            crec = records.get(cid) if isinstance(cid, str) else None
            if other or not isinstance(crec, dict):
                check = "not_evaluated"
            elif same[cls]:
                check = "vacuous_same_record"
            elif len(problems) > len(other):
                check = "checked_identical"
            elif baselines:
                check = "checked_distinct"
            else:
                check = "no_host_level_baseline"
            classes[cls] = {"class_evidence_id": cid, "distinctness_check": check}
        out[host] = {"host_evidence_id": hid, "host_evidence_level": hlevel, "host_and_class_same_record": same, "classes": classes,
                     "endpoint_basis": meta.get("endpoint_basis"), "path_pattern_declared": "path_pattern" in meta}
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
            pattern = ((jur.get("hosts") or {}).get(url.hostname) or {}).get("path_pattern")
            if pattern is not None and (url.query or url.params):
                problems.append(f"source_url on {url.hostname} carries a query string; path_pattern hosts accept the path alone")
            if pattern is not None:
                try:
                    conforms = re.fullmatch(pattern, url.path) is not None
                except (re.error, TypeError):
                    problems.append(f"path_pattern of host {url.hostname} is not a valid regular expression: {pattern!r}")
                else:
                    if not conforms:
                        problems.append(f"source_url path {url.path} on {url.hostname} does not match the host's path_pattern "
                                        f"{pattern} for source_class {record.get('source_class')}")
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
