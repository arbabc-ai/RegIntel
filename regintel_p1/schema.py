"""Relational schema for the Phase 1 evidence index (SQLite, standard library)."""
import sqlite3

SCHEMA_VERSION = "p1-0.1"

DDL = """
CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE source_document (
  id INTEGER PRIMARY KEY,
  doc_key TEXT NOT NULL UNIQUE,            -- manifest key, e.g. ecfr:12:3 or fr:2026-21706
  source_class TEXT NOT NULL,              -- regulation | rulemaking_candidate
  publisher TEXT NOT NULL,
  citation TEXT,
  title TEXT,
  source_url TEXT,
  asset_path TEXT NOT NULL,                -- relative to the corpus root
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  format TEXT NOT NULL,
  publisher_issue_date TEXT,
  publisher_up_to_date_as_of TEXT,
  retrieved_at TEXT,
  acquisition_rule_status TEXT,
  parse_stats TEXT                         -- JSON
);

CREATE TABLE rulemaking (
  doc_key TEXT PRIMARY KEY REFERENCES source_document(doc_key),
  document_number TEXT NOT NULL,
  document_type TEXT NOT NULL,             -- Rule | Proposed Rule | Notice (publisher label, not legal status)
  action TEXT,
  title TEXT,
  publication_date TEXT,
  comments_close_on TEXT,
  effective_on TEXT,                       -- publisher metadata field; not proof of operative effect
  dates_text TEXT,
  rins TEXT, dockets TEXT, agencies TEXT, cfr_parts TEXT,   -- JSON arrays
  official_url TEXT
);

CREATE TABLE provision (
  id INTEGER PRIMARY KEY,
  doc_id INTEGER NOT NULL REFERENCES source_document(id),
  locator TEXT NOT NULL,
  citation TEXT,
  kind TEXT NOT NULL,                      -- paragraph definition section table ... | fr_preamble fr_table amendment
  title_no TEXT, part_no TEXT, section_no TEXT, subpart TEXT,
  heading TEXT,
  context TEXT,                            -- retrieval context: section heading + parent lead text
  text TEXT NOT NULL,
  defined_term TEXT,
  parent_locator TEXT,
  ordinal INTEGER,
  char_start INTEGER, char_end INTEGER,    -- character offsets into the UTF-8-decoded source bytes
  fr_page TEXT,
  instruction TEXT,                        -- amendment only: the agency's amendatory instruction, verbatim
  operations TEXT,                         -- JSON, regex signal from the instruction (unreviewed)
  instruction_sections TEXT,               -- JSON, section numbers named by the instruction (unreviewed)
  paragraph_leads TEXT,                    -- JSON, paragraph designators that open the carried text
  UNIQUE (doc_id, locator)
);
CREATE INDEX provision_scope ON provision(title_no, part_no, section_no);
CREATE INDEX provision_kind ON provision(kind);

CREATE VIRTUAL TABLE provision_fts USING fts5(
  head, body, content='', tokenize='porter unicode61'
);

CREATE TABLE topic (
  id TEXT PRIMARY KEY, label TEXT NOT NULL, description TEXT,
  synonyms TEXT,                           -- JSON
  coverage TEXT NOT NULL,                  -- acquired | partial | not_acquired
  coverage_note TEXT,
  spec TEXT                                -- JSON assignment rule
);

CREATE TABLE provision_topic (
  provision_id INTEGER NOT NULL REFERENCES provision(id),
  topic_id TEXT NOT NULL REFERENCES topic(id),
  basis TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'auto_provisional',
  PRIMARY KEY (provision_id, topic_id)
);

CREATE TABLE relationship (
  id INTEGER PRIMARY KEY,
  src_provision_id INTEGER NOT NULL REFERENCES provision(id),
  rel_type TEXT NOT NULL,                  -- amends | references | uses_definition | defines
  dst_citation TEXT NOT NULL,
  dst_provision_id INTEGER REFERENCES provision(id),   -- NULL when the target is not in the corpus
  basis TEXT NOT NULL,
  source_locator TEXT NOT NULL,
  review_status TEXT NOT NULL              -- parsed_unreviewed | regex_unreviewed | reviewed
);
CREATE INDEX relationship_src ON relationship(src_provision_id);
CREATE INDEX relationship_dst ON relationship(dst_provision_id);
CREATE INDEX relationship_dst_cite ON relationship(dst_citation);

CREATE TABLE rulemaking_link (
  a_doc_key TEXT NOT NULL, b_doc_key TEXT NOT NULL,
  rel_type TEXT NOT NULL, basis TEXT NOT NULL, review_status TEXT NOT NULL,
  PRIMARY KEY (a_doc_key, b_doc_key, rel_type)
);
"""


def create(path):
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    conn.execute("INSERT INTO index_meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    return conn
