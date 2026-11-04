"""S7-P1-71: read-only data-shape probe of a Phase 1 index (stdlib only, SQLite only; the index is opened mode=ro).

For each index it records:
  (1) PRAGMA table_info for relationship, amendment_range and source_document (name, declared type, notnull);
  (2) the typeof() distribution of amendment_range title_no, part_no, lo_minor and hi_minor;
  (3) per rel_type, the count of relationship rows whose dst_citation IS NULL;
  (4) every distinct source_document doc_key beginning 'ecfr:' that does not match ^ecfr:[0-9]+:[0-9]+$;
  (5) amends (parsed_unreviewed) section citations whose minor (after the first '.') has a leading zero, and amendment_range
      lo_minor/hi_minor values stored as text with a leading zero.

    python3 -I scripts/p1_index_shape_probe.py --out OUT.json INDEX.sqlite [INDEX.sqlite ...]
"""
import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

SCHEMA = "p1-index-shape-probe-0.1"
ECFR_KEY = re.compile(r"ecfr:[0-9]+:[0-9]+", re.ASCII)

LEADING_ZERO_AMENDS = """
WITH a(cite, rest) AS (
  SELECT dst_citation, substr(dst_citation, instr(dst_citation, ' CFR ') + 5) FROM relationship
  WHERE rel_type = 'amends' AND review_status = 'parsed_unreviewed' AND instr(dst_citation, ' CFR ') > 1),
m(cite, minor) AS (SELECT cite, substr(rest, instr(rest, '.') + 1) FROM a WHERE instr(rest, '.') > 1)
SELECT DISTINCT cite FROM m WHERE minor GLOB '0*' AND minor <> '0' ORDER BY cite
"""


def probe(path):
    raw = Path(path).read_bytes()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {t: [{"name": r[1], "declared_type": r[2], "notnull": bool(r[3])} for r in conn.execute(f"PRAGMA table_info({t})")]
                  for t in ("relationship", "amendment_range", "source_document")}
        typeof = {col: dict(conn.execute(f"SELECT typeof({col}), COUNT(*) FROM amendment_range GROUP BY 1 ORDER BY 1").fetchall())
                  for col in ("title_no", "part_no", "lo_minor", "hi_minor")}
        rel_types = [r[0] for r in conn.execute("SELECT DISTINCT rel_type FROM relationship ORDER BY 1")]
        null_by_rel = {rt: conn.execute("SELECT COUNT(*) FROM relationship WHERE rel_type = ? AND dst_citation IS NULL", (rt,)).fetchone()[0]
                       for rt in rel_types}
        null_rel_type = conn.execute("SELECT COUNT(*) FROM relationship WHERE rel_type IS NULL AND dst_citation IS NULL").fetchone()[0]
        keys = [r[0] for r in conn.execute("SELECT DISTINCT doc_key FROM source_document WHERE doc_key LIKE 'ecfr:%' ORDER BY 1")]
        unparsed = [k for k in keys if not ECFR_KEY.fullmatch(str(k))]
        lz_amends = [r[0] for r in conn.execute(LEADING_ZERO_AMENDS)]
        lz_range = {col: [r[0] for r in conn.execute(f"SELECT DISTINCT {col} FROM amendment_range WHERE typeof({col}) = 'text' "
                                                     f"AND {col} GLOB '0*' AND {col} <> '0' ORDER BY 1")]
                    for col in ("lo_minor", "hi_minor")}
        counts = {"relationship_rows": conn.execute("SELECT COUNT(*) FROM relationship").fetchone()[0],
                  "amendment_range_rows": conn.execute("SELECT COUNT(*) FROM amendment_range").fetchone()[0],
                  "ecfr_doc_keys": len(keys)}
    finally:
        conn.close()
    return {"path": str(path), "name": Path(path).name, "sha256": hashlib.sha256(raw).hexdigest(), "counts": counts,
            "table_info": tables, "amendment_range_typeof": typeof,
            "relationship_null_dst_citation_by_rel_type": null_by_rel, "relationship_null_dst_citation_null_rel_type": null_rel_type,
            "ecfr_doc_keys_unparsed": unparsed,
            "amends_section_citations_minor_leading_zero": {"count": len(lz_amends), "citations": lz_amends},
            "amendment_range_text_leading_zero": {col: {"count": len(v), "values": v} for col, v in lz_range.items()}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("index", nargs="+")
    a = ap.parse_args(argv)
    out = {"schema": SCHEMA, "status": "producer-reported; offline; read-only (indexes opened mode=ro); no gate approved",
           "indexes": [probe(p) for p in a.index]}
    data = (json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode()
    Path(a.out).write_bytes(data)
    print(json.dumps({"out": a.out, "sha256": hashlib.sha256(data).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
