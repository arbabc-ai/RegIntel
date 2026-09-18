"""Phase 2 — the regulatory-thresholds warehouse (star schema, SQLite).

Design intent (see docs/architecture.md, "Phase 2 — DE + Data layer"):
a threshold question ("what's the minimum CET1 ratio?") is a LOOKUP, not a
semantic search. This module is the fact/dimension store that backs
deterministic lookups, separate from the RAG layer in retrieve.py/generate.py.

Schema:
    dim_regulations(regulation_id, source_file, citation, title)
    dim_metrics(metric_id, metric_key, metric_label)
    fct_regulatory_thresholds(threshold_id, regulation_id, metric_id,
        institution_tier, value, unit, is_formula, formula_expr, condition_text,
        chunk_source, chunk_index, citation_excerpt, as_of_date, extracted_at)

`value`/`unit` are nullable: a threshold defined RELATIVE to another value
(e.g. "the lesser of 1.0 percent or 50 percent of the most recent GSIB
surcharge") has no single flat number, and extraction used to guess one
anyway — that's the defect documented in the README's Phase 2 section. Such
rows carry `is_formula=1` and the exact quoted expression in `formula_expr`
instead, with `value`/`unit` left NULL rather than a misleading number.

`as_of_date` is the ingest/extraction date, not a rule-published date — the
eCFR corpus this repo pulls is "current as of fetch", not versioned by date.
A real warehouse (S3 + checksum-tracked incremental ingest, per
docs/architecture.md) would carry a true effective_date per amendment.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

WAREHOUSE_PATH = Path(os.environ.get("WAREHOUSE_DB_PATH", "./data/warehouse.sqlite3"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_regulations (
    regulation_id INTEGER PRIMARY KEY,
    source_file   TEXT UNIQUE NOT NULL,
    citation      TEXT NOT NULL,
    title         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_metrics (
    metric_id    INTEGER PRIMARY KEY,
    metric_key   TEXT UNIQUE NOT NULL,
    metric_label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fct_regulatory_thresholds (
    threshold_id      INTEGER PRIMARY KEY,
    regulation_id     INTEGER NOT NULL REFERENCES dim_regulations(regulation_id),
    metric_id         INTEGER NOT NULL REFERENCES dim_metrics(metric_id),
    institution_tier  TEXT NOT NULL DEFAULT 'all covered institutions',
    value             REAL,
    unit              TEXT,
    is_formula        INTEGER NOT NULL DEFAULT 0,
    formula_expr      TEXT NOT NULL DEFAULT '',
    condition_text    TEXT NOT NULL DEFAULT '',
    chunk_source      TEXT NOT NULL,
    chunk_index       INTEGER NOT NULL,
    citation_excerpt  TEXT NOT NULL,
    as_of_date        TEXT NOT NULL,
    extracted_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_thresholds_metric ON fct_regulatory_thresholds(metric_id);
CREATE INDEX IF NOT EXISTS idx_metrics_key ON dim_metrics(metric_key);
"""

# Deterministic mapping (no LLM needed): source filename -> regulation identity.
# Kept in sync with scripts/download_corpus.py's ECFR_PARTS + the BSA/AML part.
KNOWN_REGULATIONS = {
    "cfr_title12_part217_Regulation-Q-Capital-Adequacy.txt": (
        "12 CFR Part 217", "Regulation Q — Capital Adequacy",
    ),
    "cfr_title12_part249_Regulation-WW-Liquidity-Coverage-Ratio.txt": (
        "12 CFR Part 249", "Regulation WW — Liquidity Coverage Ratio",
    ),
    "cfr_title12_part252_Regulation-YY-Enhanced-Prudential-Standards.txt": (
        "12 CFR Part 252", "Regulation YY — Enhanced Prudential Standards",
    ),
    "cfr_title31_part1020_BSA-AML-Rules-for-Banks.txt": (
        "31 CFR Part 1020", "BSA/AML Rules for Banks (FinCEN)",
    ),
}


def connect() -> sqlite3.Connection:
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(WAREHOUSE_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def reset() -> None:
    """Drop and recreate the warehouse. Called at the start of every extraction run
    for determinism in this demo project (same pattern as src/ingest.py's Chroma reset)."""
    if WAREHOUSE_PATH.exists():
        WAREHOUSE_PATH.unlink()
    connect().close()


def upsert_regulation(conn: sqlite3.Connection, source_file: str) -> int:
    citation, title = KNOWN_REGULATIONS.get(
        source_file, (source_file, source_file)
    )
    with closing(conn.cursor()) as cur:
        cur.execute(
            "INSERT INTO dim_regulations (source_file, citation, title) VALUES (?, ?, ?) "
            "ON CONFLICT(source_file) DO UPDATE SET citation=excluded.citation, title=excluded.title "
            "RETURNING regulation_id",
            (source_file, citation, title),
        )
        return cur.fetchone()["regulation_id"]


def upsert_metric(conn: sqlite3.Connection, metric_key: str, metric_label: str) -> int:
    with closing(conn.cursor()) as cur:
        cur.execute(
            "INSERT INTO dim_metrics (metric_key, metric_label) VALUES (?, ?) "
            "ON CONFLICT(metric_key) DO UPDATE SET metric_label=excluded.metric_label "
            "RETURNING metric_id",
            (metric_key, metric_label),
        )
        return cur.fetchone()["metric_id"]


def insert_threshold(
    conn: sqlite3.Connection,
    *,
    regulation_id: int,
    metric_id: int,
    institution_tier: str,
    value: float | None,
    unit: str | None,
    condition_text: str,
    chunk_source: str,
    chunk_index: int,
    citation_excerpt: str,
    is_formula: bool = False,
    formula_expr: str = "",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO fct_regulatory_thresholds
           (regulation_id, metric_id, institution_tier, value, unit, is_formula, formula_expr,
            condition_text, chunk_source, chunk_index, citation_excerpt, as_of_date, extracted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            regulation_id, metric_id, institution_tier, value, unit, int(is_formula), formula_expr,
            condition_text, chunk_source, chunk_index, citation_excerpt, now, now,
        ),
    )


def lookup_threshold(query: str, limit: int = 5) -> list[dict]:
    """Deterministic fuzzy lookup by metric key/label — the Phase-3
    `lookup_threshold(regulation, institution_tier, metric)` tool contract
    from docs/architecture.md, minus the agent routing (that's Phase 3).
    """
    like = f"%{query.strip()}%"
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT r.citation AS regulation, r.title AS regulation_title,
                      m.metric_label AS metric, t.institution_tier, t.value, t.unit,
                      t.is_formula, t.formula_expr,
                      t.condition_text, t.chunk_source, t.chunk_index,
                      t.citation_excerpt, t.as_of_date
               FROM fct_regulatory_thresholds t
               JOIN dim_regulations r ON r.regulation_id = t.regulation_id
               JOIN dim_metrics m ON m.metric_id = t.metric_id
               WHERE m.metric_label LIKE ? OR m.metric_key LIKE ?
               ORDER BY m.metric_label
               LIMIT ?""",
            (like, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def stats() -> dict:
    with closing(connect()) as conn:
        n_thresh = conn.execute("SELECT COUNT(*) c FROM fct_regulatory_thresholds").fetchone()["c"]
        n_metrics = conn.execute("SELECT COUNT(*) c FROM dim_metrics").fetchone()["c"]
        n_regs = conn.execute("SELECT COUNT(*) c FROM dim_regulations").fetchone()["c"]
        return {"thresholds": n_thresh, "metrics": n_metrics, "regulations": n_regs}
