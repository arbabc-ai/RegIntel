"""S7-P1-77: explicit_paragraphs of every Federal Register amendment unit in the manifest, and the diff between two dumps.

Offline, read-only, stdlib only. Governing scope: founder record 2026-11-04_s7-regintel-p1-founder-decisions_accepted.md.
`dump` parses every manifest entry whose publisher is "Federal Register" from its pinned raw bytes (the sha256 is checked)
with the checkout's regintel_p1.fedreg and writes {locator: explicit_paragraphs} keyed by the
index locator that regintel_p1/build.py gives each amendment unit (`unit_id:section:n`, appendix text `unit_id:appx-…`). `diff` compares two
dumps: a change is `addition_only` when the old paths are a prefix-preserving subset of the new ones and the status is
unchanged or moved from none_named to attributed; anything else (a removed path, a reordered path, a status change, a unit
that appears or disappears) is listed under `other_changes`, and the command exits 1.

    python3 -I scripts/p1_paragraph_attribution_diff_77.py dump --out BEFORE.json
    python3 -I scripts/p1_paragraph_attribution_diff_77.py diff BEFORE.json AFTER.json --out DIFF.json
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regintel_p1 import fedreg  # noqa: E402

DEFAULT_MANIFEST = "/home/arbab/projects/regintel-corpus/phase1/manifest.json"


def dumps(obj):
    return (json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode()


def dump(manifest_path):
    raw_manifest = Path(manifest_path).read_bytes()
    corpus = Path(manifest_path).parent
    units = {}
    for key, entry in sorted(json.loads(raw_manifest)["documents"].items()):
        if entry.get("publisher") != "Federal Register":
            continue
        raw = (corpus / entry["asset"]["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["asset"]["sha256"]:
            raise ValueError(f"{key}: raw bytes do not match the pinned sha256")
        _provisions, parsed, _stats = fedreg.parse_fr(raw, key.split(":", 1)[1])
        n = 0  # the index locator of regintel_p1/build.py: unit_id:section:n over non-appendix-text units
        for u in parsed:
            ap = u.get("appendix")
            if ap and ap.get("kind") == "appendix_extract" and u["text"]:
                locator = f"{u['unit_id']}:appx-{ap['part']}-{ap['letter'] or 'unknown'}:{ap['chunk']:03d}of{ap['chunks']:03d}"
            else:
                n += 1
                locator = f"{u['unit_id']}:{u['section_no'] or 'part'}:{n}"
            if locator in units:
                raise ValueError(f"{key}: duplicate locator {locator}")
            units[locator] = {"doc_key": key, "section_no": u.get("section_no"), "instruction": u.get("instruction"),
                              "explicit_paragraphs": u.get("explicit_paragraphs")}
    return {"manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(), "units": units}


def classify(old, new):
    op, np_ = (old or {}).get("paths") or [], (new or {}).get("paths") or []
    os_, ns = (old or {}).get("status"), (new or {}).get("status")
    kept = [p for p in np_ if p in op]
    status_ok = os_ == ns or (os_ == "none_named" and ns == "attributed")
    if kept == op and len(np_) > len(op) and status_ok:
        return "addition_only"
    return "other"


def diff(before, after):
    changed, other = [], []
    if before["manifest_sha256"] != after["manifest_sha256"]:
        other.append({"unit": None, "reason": "the two dumps were made from different manifests"})
    for key in sorted(set(before["units"]) | set(after["units"])):
        b, a = before["units"].get(key), after["units"].get(key)
        if b is None or a is None:
            other.append({"unit": key, "reason": "unit appears in only one dump"})
            continue
        if b["explicit_paragraphs"] == a["explicit_paragraphs"]:
            continue
        row = {"unit": key, "doc_key": a["doc_key"], "section_no": a["section_no"], "instruction": a["instruction"],
               "old": b["explicit_paragraphs"], "new": a["explicit_paragraphs"],
               "added_paths": [p for p in (a["explicit_paragraphs"] or {}).get("paths") or []
                               if p not in ((b["explicit_paragraphs"] or {}).get("paths") or [])],
               "kind": classify(b["explicit_paragraphs"], a["explicit_paragraphs"])}
        (changed if row["kind"] == "addition_only" else other).append(row)
    return {"manifest_sha256": after["manifest_sha256"], "units_compared": len(after["units"]),
            "changed_units": changed, "other_changes": other, "additions_only": not other,
            "legal_validity": "not_determined", "applicability_status": "not_assessed",
            "status": "producer-reported; offline; read-only; no gate approved"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--manifest", default=DEFAULT_MANIFEST)
    d.add_argument("--out", required=True)
    c = sub.add_parser("diff")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.command == "dump":
        obj = dump(a.manifest)
        code = 0
    else:
        obj = diff(json.loads(Path(a.before).read_text()), json.loads(Path(a.after).read_text()))
        code = 0 if obj["additions_only"] else 1
    data = dumps(obj)
    Path(a.out).write_bytes(data)
    print(json.dumps({"out": a.out, "sha256": hashlib.sha256(data).hexdigest(),
                      **({"changed": len(obj["changed_units"]), "other": len(obj["other_changes"])} if a.command == "diff" else
                         {"units": len(obj["units"])})}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
