"""Generate regintel_p1/jurisdictions.json from the C3 Phase 1 STATE_JURISDICTION_REGISTER.csv (read-only input).

Only names, ids, types and the register's own status/coverage labels are copied. Nothing here records a state-law
source, applicability, preemption or compliance conclusion. Run: python3 -I scripts/build_jurisdictions.py <register.csv>
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

ALIASES = {"DC": ["Washington, D.C.", "Washington DC", "D.C."], "VI": ["Virgin Islands"]}  # added by S7, not in the register


def main(path):
    raw = Path(path).read_bytes()
    rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    out = {
        "source": {"file": Path(path).name, "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                   "owner": "Codex C3 (read-only input; ChatGPT/ is never written)", "rows": len(rows)},
        "copied_fields": ["id", "name", "jurisdiction_type", "status", "coverage"],
        "note": "Names are used only to detect that a request names a jurisdiction. Matching a name asserts no state-law "
                "applicability, preemption or compliance; the register itself marks every row not-assessed and unindexed.",
        "aliases_added_by_s7": ALIASES,
        "jurisdictions": [{"id": r["id"], "name": r["name"], "type": r["jurisdiction_type"],
                           "register_status": r["status"], "register_coverage": r["coverage"],
                           "aliases": ALIASES.get(r["id"], [])} for r in rows],
    }
    target = Path(__file__).resolve().parent.parent / "regintel_p1" / "jurisdictions.json"
    target.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {target} ({len(rows)} jurisdictions, register sha256 {out['source']['sha256'][:16]}…)")


if __name__ == "__main__":
    main(sys.argv[1])
