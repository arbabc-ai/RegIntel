"""Retrieval over the Phase 1 evidence index: lexical, topic-guided and hybrid retrievers, plus the
current-to-future delta view.

Similarity here discovers evidence. Nothing in this module decides whether a rule applies to a bank:
every answer carries `applicability_status = "not_assessed"` and the missing profile facts a reviewer needs.
"""
import json
import re
import sqlite3
from collections import defaultdict

STOP = set("""a an and are as at be by can do does for from has have how if in into is it its of on or that the their
there this to under what when where which who whom why will with within would must should shall may not any all
bank banks institution institutions requirement requirements rule rules regulation regulations""".split())
CITE = re.compile(r"(?:(\d+)\s*CFR\s*(?:Part\s*)?|§+\s*)(\d+)(?:\.(\d+[A-Za-z]?))?((?:\([A-Za-z0-9]+\))*)", re.I)
CONTENT_KINDS = ("paragraph", "definition", "table", "section", "appendix")
FUTURE_KINDS = ("amendment", "fr_preamble", "fr_table")
RRF_K = 60


def lifecycle(row, as_of):
    """Label a Federal Register document's rulemaking state from publisher metadata only.

    These are labels for retrieval filtering and display. Dispositions, stays, reopenings and legal
    effect are not inferred; each label names what it does not establish.
    """
    dtype = row["document_type"]
    title = (row["title"] or "") + " " + (row["action"] or "")
    effective, closes = row["effective_on"], row["comments_close_on"]
    if dtype == "Notice":
        return {"state": "notice_not_a_rule_state", "caveat": "Notice; not a rulemaking state."}
    if re.search(r"correction", title, re.I):
        return {"state": "correction_of_prior_document",
                "caveat": "Correction; identify the corrected document before using."}
    if dtype == "Proposed Rule":
        if re.search(r"extension|reopen", title, re.I):
            note = "Comment-period extension/reopening notice; see the underlying proposal."
        else:
            note = "A proposal is not an operative requirement."
        if closes and closes >= as_of:
            return {"state": "proposed_comment_period_open", "comments_close_on": closes, "caveat": note}
        if closes:
            return {"state": "proposed_comment_period_ended_disposition_unreviewed", "comments_close_on": closes,
                    "caveat": note + " Closing comments does not finalize a rule."}
        return {"state": "proposed_no_comment_date_in_metadata", "caveat": note}
    if dtype == "Rule":
        if effective and effective > as_of:
            return {"state": "final_effective_date_pending", "effective_on": effective,
                    "caveat": "Final rule not yet effective per publisher date; stays and partial effect unreviewed."}
        if effective:
            return {"state": "final_effective_date_passed_operative_effect_unreviewed", "effective_on": effective,
                    "caveat": "Publisher effective date has passed; stays, delays and compliance dates unreviewed."}
        return {"state": "final_no_effective_date_in_metadata", "caveat": "No effective date in publisher metadata."}
    return {"state": "unknown", "caveat": ""}


def terms_of(query):
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']*", query)
    return [w.lower() for w in words if w.lower() not in STOP and len(w) > 1]


def fts_expression(terms):
    if not terms:
        return None
    parts = [f'"{t}"' for t in terms]
    parts += [f'"{a} {b}"' for a, b in zip(terms, terms[1:])]  # adjacent pairs reward phrase matches
    return " OR ".join(parts)


class Index:
    def __init__(self, db_path, as_of=None):
        self.db_path = db_path
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        meta = {r["key"]: r["value"] for r in self.conn.execute("SELECT * FROM index_meta")}
        self.meta = meta
        self.as_of = as_of or meta.get("as_of")
        self.topics = [dict(r) for r in self.conn.execute("SELECT * FROM topic")]
        for t in self.topics:
            t["synonyms"] = json.loads(t["synonyms"] or "[]")
        self._rulemaking = {r["doc_key"]: dict(r) for r in self.conn.execute("SELECT * FROM rulemaking")}

    # ---------- filters ----------
    def _where(self, scope, parts, doc_types=None, topic=None):
        clauses, params = [], []
        if scope == "current":
            clauses.append(f"p.kind IN ({','.join('?' * len(CONTENT_KINDS))})")
            params += CONTENT_KINDS
        elif scope == "future":
            clauses.append(f"p.kind IN ({','.join('?' * len(FUTURE_KINDS))})")
            params += FUTURE_KINDS
            clauses.append("r.document_type IN ('Rule','Proposed Rule')")
        else:
            clauses.append(f"p.kind IN ({','.join('?' * (len(CONTENT_KINDS) + len(FUTURE_KINDS)))})")
            params += CONTENT_KINDS + FUTURE_KINDS
            clauses.append("(r.doc_key IS NULL OR r.document_type IN ('Rule','Proposed Rule'))")
        if parts:
            sub = []
            for spec in parts:
                title, part = spec.split(":")
                sub.append("(p.title_no=? AND p.part_no=?)")
                params += [title, part]
            clauses.append("(" + " OR ".join(sub) + ")")
        if doc_types:
            clauses.append(f"r.document_type IN ({','.join('?' * len(doc_types))})")
            params += doc_types
        if topic:
            clauses.append("p.id IN (SELECT provision_id FROM provision_topic WHERE topic_id=?)")
            params.append(topic)
        return " AND ".join(clauses), params

    # ---------- retrievers: each returns [(provision_id, score)] best first ----------
    def bm25(self, query, k=50, scope="both", parts=None, expression=None):
        expr = expression or fts_expression(terms_of(query))
        if not expr:
            return []
        where, params = self._where(scope, parts)
        sql = f"""SELECT p.id, bm25(provision_fts, 3.0, 1.0) AS s FROM provision_fts
                  JOIN provision p ON p.id = provision_fts.rowid
                  JOIN source_document d ON d.id = p.doc_id
                  LEFT JOIN rulemaking r ON r.doc_key = d.doc_key
                  WHERE provision_fts MATCH ? AND {where} ORDER BY s LIMIT ?"""
        return [(r[0], -r[1]) for r in self.conn.execute(sql, [expr] + params + [k])]

    def match_topics(self, query):
        q = query.lower()
        hits = []
        for t in self.topics:
            names = [t["label"].lower()] + [s.lower() for s in t["synonyms"]]
            matched = [n for n in names if re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", q)]
            if matched:
                hits.append((t, matched))
        return hits

    def topic_guided(self, query, k=50, scope="both", parts=None):
        """Query expanded with the shared topic catalog's synonyms; provisions tagged to a matched topic are kept
        in the candidate list and ranked by lexical score on the original terms."""
        matched = [t for t, _ in self.match_topics(query) if t["coverage"] != "not_acquired"]
        if not matched:
            return []
        base = terms_of(query)
        extra = []
        for t in matched:
            for s in t["synonyms"]:
                extra += terms_of(s)
        expr = fts_expression(base) or ""
        expr = " OR ".join(x for x in [expr, " OR ".join(f'"{e}"' for e in dict.fromkeys(extra))] if x)
        where, params = self._where(scope, parts)
        topic_ids = [t["id"] for t in matched]
        sql = f"""SELECT p.id, bm25(provision_fts, 3.0, 1.0) AS s,
                         EXISTS (SELECT 1 FROM provision_topic pt WHERE pt.provision_id = p.id
                                 AND pt.topic_id IN ({','.join('?' * len(topic_ids))})) AS tagged
                  FROM provision_fts JOIN provision p ON p.id = provision_fts.rowid
                  JOIN source_document d ON d.id = p.doc_id
                  LEFT JOIN rulemaking r ON r.doc_key = d.doc_key
                  WHERE provision_fts MATCH ? AND {where} ORDER BY tagged DESC, s LIMIT ?"""
        return [(r[0], -r[1] + (100.0 if r[2] else 0.0))
                for r in self.conn.execute(sql, topic_ids + [expr] + params + [k])]

    def exact(self, query, k=20, scope="both", parts=None):
        """Direct lookup when the query names a CFR citation such as '12 CFR 217.11(b)' or '§ 3.12'."""
        out = []
        for m in CITE.finditer(query):
            title, part, minor, paras = m.groups()
            section = f"{part}.{minor}" if minor else None
            if not section and not title:
                continue
            if section:
                rows = self.conn.execute(
                    """SELECT p.id, p.locator FROM provision p WHERE p.section_no=? AND p.kind IN ('paragraph','definition','table','section')
                       AND (? IS NULL OR p.title_no=?) ORDER BY p.ordinal""", (section, title, title)).fetchall()
                if paras:
                    wanted = f"{section}{paras}"
                    rows = [r for r in rows if r["locator"] == wanted or r["locator"].startswith(wanted + "(")] or rows
            else:
                rows = self.conn.execute("SELECT id, locator FROM provision WHERE title_no=? AND part_no=? AND kind='part'",
                                         (title, part)).fetchall()
            out += [(r["id"], float(len(rows) - i)) for i, r in enumerate(rows[:k])]
        if parts:
            keep = {r[0] for r in self.conn.execute(
                f"SELECT id FROM provision p WHERE id IN ({','.join('?' * len(out))}) AND "
                + "(" + " OR ".join("(title_no=? AND part_no=?)" for _ in parts) + ")",
                [o[0] for o in out] + [x for s in parts for x in s.split(":")])} if out else set()
            out = [o for o in out if o[0] in keep]
        return out

    def hybrid(self, query, k=50, scope="both", parts=None):
        runs = [self.bm25(query, k, scope, parts), self.topic_guided(query, k, scope, parts),
                self.exact(query, k, scope, parts)]
        fused = defaultdict(float)
        for run in runs:
            for rank, (pid, _) in enumerate(run, 1):
                fused[pid] += 1.0 / (RRF_K + rank)
        return sorted(fused.items(), key=lambda x: -x[1])[:k]

    def retrieve(self, query, mode="hybrid", k=10, scope="both", parts=None):
        fn = {"bm25": lambda: self.bm25(query, k, scope, parts),
              "topic": lambda: self.topic_guided(query, k, scope, parts),
              "exact": lambda: self.exact(query, k, scope, parts),
              "hybrid": lambda: self.hybrid(query, max(k, 50), scope, parts)}[mode]
        return fn()[:k]

    # ---------- evidence records ----------
    def evidence(self, pid, with_context=True):
        row = self.conn.execute(
            """SELECT p.*, d.doc_key, d.publisher, d.sha256, d.publisher_issue_date, d.publisher_up_to_date_as_of,
                      d.source_url, d.asset_path FROM provision p JOIN source_document d ON d.id=p.doc_id WHERE p.id=?""",
            (pid,)).fetchone()
        out = {"provision_id": pid, "doc_key": row["doc_key"], "locator": row["locator"], "citation": row["citation"],
               "kind": row["kind"], "heading": row["heading"], "text": row["text"], "source_sha256": row["sha256"],
               "source_url": row["source_url"], "char_start": row["char_start"], "char_end": row["char_end"]}
        rm = self._rulemaking.get(row["doc_key"])
        if rm:
            out["status"] = {**lifecycle(rm, self.as_of), "document_type": rm["document_type"],
                             "document_number": rm["document_number"], "publication_date": rm["publication_date"]}
            out["fr_page"] = row["fr_page"]
            if row["kind"] == "amendment":
                out["instruction"] = row["instruction"]
        else:
            out["status"] = {"state": "current_baseline_requires_operative_effect_review",
                             "publisher_issue_date": row["publisher_issue_date"],
                             "publisher_up_to_date_as_of": row["publisher_up_to_date_as_of"],
                             "caveat": "eCFR baseline text; applicability and compliance dates not assessed."}
        if with_context:
            out["definitions_used"] = [
                {"term": r["dst_citation"].split("definition: ")[-1], "citation": r["citation"], "text": r["text"][:400]}
                for r in self.conn.execute(
                    """SELECT r.dst_citation, d.citation, d.text FROM relationship r JOIN provision d ON d.id=r.dst_provision_id
                       WHERE r.src_provision_id=? AND r.rel_type='uses_definition' LIMIT 6""", (pid,))]
            out["topics"] = [r[0] for r in self.conn.execute(
                "SELECT topic_id FROM provision_topic WHERE provision_id=?", (pid,))]
        return out

    # ---------- answer bundle ----------
    def answer(self, query, mode="hybrid", scope="both", parts=None, k=8):
        """Evidence bundle. `status` is evidence_found | partial_evidence | missing_evidence.

        missing_evidence: the query names only topics whose sources are not acquired (no results returned).
        partial_evidence: it names an unacquired source alongside acquired ones; results come from the acquired
        sources and the gap is reported, so they must not be read as answering the unacquired part.
        """
        topic_hits = self.match_topics(query)
        gaps = [{"topic": t["id"], "coverage": t["coverage"], "note": t["coverage_note"]}
                for t, _ in topic_hits if t["coverage"] in ("not_acquired", "partial")]
        blocked = [t for t, _ in topic_hits if t["coverage"] == "not_acquired"]
        only_blocked = bool(blocked) and len(blocked) == len(topic_hits) and not self.exact(query)
        results = [] if only_blocked else [self.evidence(pid) for pid, _ in self.retrieve(query, mode, k, scope, parts)]
        if not results:
            status = "missing_evidence"
        elif blocked:
            status = "partial_evidence"
        else:
            status = "evidence_found"
        return {
            "query": query, "mode": mode, "scope": scope, "as_of": self.as_of, "status": status,
            "applicability_status": "not_assessed",
            "applicability_note": "Retrieval is evidence discovery. A bank profile (charter, regulator, consolidation "
                                  "scope, size/risk category, elections) is required before any applies / does_not_apply "
                                  "conclusion.",
            "topics_matched": [t["id"] for t, _ in topic_hits], "coverage_gaps": gaps, "results": results,
            "index": {"manifest_sha256": self.meta.get("manifest_sha256"), "built_at": self.meta.get("built_at")},
        }

    # ---------- current-to-future ----------
    def delta(self, citation):
        """Current baseline versus each acquired future instrument for one section or part.

        Instruments are listed independently and are never merged: competing or cumulative proposals do not form
        one future rulebook, and a final rule whose effective date predates the baseline issue date may already
        be reflected in the baseline text.
        """
        m = CITE.search(citation)
        if not m:
            raise ValueError(f"cannot parse citation: {citation}")
        title, part, minor, _ = m.groups()
        section = f"{part}.{minor}" if minor else None
        if section:
            base_rows = self.conn.execute(
                """SELECT p.*, d.doc_key, d.sha256, d.publisher_issue_date, d.publisher_up_to_date_as_of FROM provision p
                   JOIN source_document d ON d.id=p.doc_id
                   WHERE p.title_no=? AND p.section_no=? AND p.kind IN ('section','paragraph','definition','table')
                   ORDER BY p.ordinal""", (title, section)).fetchall()
            amend = self.conn.execute(
                """SELECT DISTINCT p.id FROM relationship r JOIN provision p ON p.id=r.src_provision_id
                   WHERE r.rel_type='amends' AND r.dst_citation=?""", (f"{title} CFR {section}",)).fetchall()
        else:
            base_rows = self.conn.execute(
                """SELECT p.*, d.doc_key, d.sha256, d.publisher_issue_date, d.publisher_up_to_date_as_of FROM provision p
                   JOIN source_document d ON d.id=p.doc_id WHERE p.title_no=? AND p.part_no=? AND p.kind='part'""",
                (title, part)).fetchall()
            amend = self.conn.execute(
                """SELECT DISTINCT p.id FROM provision p WHERE p.kind='amendment' AND p.title_no=? AND p.part_no=?""",
                (title, part)).fetchall()
        if not base_rows and section:  # target may sit inside a reserved/range section such as 3.4-3.9
            for r in self.conn.execute("SELECT DISTINCT section_no FROM provision WHERE title_no=? AND part_no=? AND section_no LIKE '%-%'",
                                       (title, part)):
                span = re.match(r"^(\d+)\.(\d+)-(\d+)\.(\d+)$", r["section_no"] or "")
                if span and span.group(1) == part and int(span.group(2)) <= int(minor or 0) <= int(span.group(4)) and minor.isdigit():
                    base_rows = self.conn.execute(
                        """SELECT p.*, d.doc_key, d.sha256, d.publisher_issue_date, d.publisher_up_to_date_as_of FROM provision p
                           JOIN source_document d ON d.id=p.doc_id WHERE p.title_no=? AND p.section_no=? ORDER BY p.ordinal""",
                        (title, r["section_no"])).fetchall()
                    break
        baseline = None
        if base_rows:
            b = base_rows[0]
            baseline = {"doc_key": b["doc_key"], "sha256": b["sha256"], "issue_date": b["publisher_issue_date"],
                        "up_to_date_as_of": b["publisher_up_to_date_as_of"], "provisions_in_scope": len(base_rows),
                        "heading": b["heading"]}
        grouped = {}
        for (pid,) in amend:
            ev = self.evidence(pid, with_context=False)
            g = grouped.setdefault(ev["doc_key"], {"doc_key": ev["doc_key"], "document_number": ev["status"]["document_number"],
                                                   "document_type": ev["status"]["document_type"], "status": ev["status"],
                                                   "source_sha256": ev["source_sha256"], "source_url": ev["source_url"],
                                                   "units": []})
            g["units"].append({"locator": ev["locator"], "fr_page": ev.get("fr_page"), "instruction": ev.get("instruction"),
                               "carried_text": ev["text"]})
        instruments = sorted(grouped.values(), key=lambda g: self._rulemaking[g["doc_key"]]["publication_date"] or "")
        for g in instruments:
            rm = self._rulemaking[g["doc_key"]]
            g["rins"] = json.loads(rm["rins"] or "[]")
            g["publication_date"] = rm["publication_date"]
            if baseline and g["document_type"] == "Rule" and rm["effective_on"]:
                g["baseline_relation"] = ("effective_on_or_before_baseline_issue_date_baseline_may_already_reflect"
                                          if rm["effective_on"] <= baseline["issue_date"]
                                          else "effective_after_baseline_issue_date_not_in_baseline")
            elif g["document_type"] == "Proposed Rule":
                g["baseline_relation"] = "proposal_not_in_baseline"
            else:
                g["baseline_relation"] = "unknown"
            links = self.conn.execute("SELECT * FROM rulemaking_link WHERE a_doc_key=? OR b_doc_key=?",
                                      (g["doc_key"], g["doc_key"])).fetchall()
            g["candidate_links"] = [{"other": r["b_doc_key"] if r["a_doc_key"] == g["doc_key"] else r["a_doc_key"],
                                     "rel": r["rel_type"], "basis": r["basis"], "review": r["review_status"]} for r in links]
        return {"citation": citation, "as_of": self.as_of, "baseline": baseline, "instruments": instruments,
                "applicability_status": "not_assessed",
                "notes": ["Instruments are independent; no cumulative future rulebook is constructed.",
                          "baseline_relation compares publisher dates only; confirm against the text before relying on it."]}
