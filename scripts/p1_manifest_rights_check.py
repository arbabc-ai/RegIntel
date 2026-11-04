"""S7-P1-72: report every federal (eCFR / Federal Register) manifest entry lacking a complete DR-6 rights record.

Rights come from an entry's inline `rights` object, else from the sidecar overlay (default: federal_rights.json beside the
manifest). Basis: DR-2 and DR-6 in the founder record 2026-11-04_s7-regintel-p1-founder-decisions_accepted.md. Offline; the
manifest is only read. Exit 1 when the check fails: an incomplete federal entry, an overlay record with no federal entry, or an
overlay pinned (manifest_sha256) to a different manifest. An overlay without manifest_sha256 (the S7-P1-72 overlay) is reported
as absent, which does not fail.

    python3 -I scripts/p1_manifest_rights_check.py --manifest PATH [--overlay PATH] --out OUT.json
    python3 -I scripts/p1_manifest_rights_check.py --manifest PATH --write-overlay PATH   # create the overlay; never overwrites
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regintel_p1 import federal_rights  # noqa: E402


def dumps(obj):
    return (json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--overlay")
    ap.add_argument("--out")
    ap.add_argument("--write-overlay")
    a = ap.parse_args(argv)
    if bool(a.out) == bool(a.write_overlay):
        ap.error("give exactly one of --out or --write-overlay")
    raw = Path(a.manifest).read_bytes()
    manifest = json.loads(raw)
    if a.write_overlay:
        path = Path(a.write_overlay)
        if path.exists():
            ap.error(f"{path} exists; the overlay is never overwritten")
        data = dumps(federal_rights.build_overlay(manifest, hashlib.sha256(raw).hexdigest()))
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        print(json.dumps({"overlay": str(path), "sha256": hashlib.sha256(data).hexdigest()}))
        return 0
    overlay, overlay_path = federal_rights.load_overlay(a.manifest, a.overlay)
    report = federal_rights.check(manifest, overlay, hashlib.sha256(raw).hexdigest())
    report["inputs"] = {"manifest": {"path": a.manifest, "sha256": hashlib.sha256(raw).hexdigest()},
                        "overlay": {"path": str(overlay_path),
                                    "sha256": hashlib.sha256(overlay_path.read_bytes()).hexdigest() if overlay is not None else None}}
    report["status"] = "producer-reported; offline; read-only over the manifest; no gate approved"
    data = dumps(report)
    Path(a.out).write_bytes(data)
    print(json.dumps({"out": a.out, "sha256": hashlib.sha256(data).hexdigest(), "federal_entries": report["federal_entries"],
                      "incomplete": len(report["incomplete"]), "passed": report["passed"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
