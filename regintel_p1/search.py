"""Retrieval over the Phase 1 evidence index: lexical, topic-guided and hybrid retrievers, plus the
current-to-future delta view.

Similarity here discovers evidence. Nothing in this module decides whether a rule applies to a bank:
every answer carries `applicability_status = "not_assessed"` and the missing profile facts a reviewer needs.
"""
import json
import re
import sqlite3
from pathlib import Path
from collections import defaultdict
from itertools import zip_longest

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


CITATION_REASONS = {
    "paragraph_precision_unresolved": "Only section-level amendment evidence was found; no amendatory instruction names the "
                                      "complete requested paragraph path, so its hierarchy is not established "
                                      "(section-level records are returned, flagged).",
    "excluded_by_part_filter": "A record exists, but the requested part filter excludes it.",
    "excluded_by_requested_scope": "A record exists in the other scope, but not in the requested scope.",
    "excluded_by_scope_and_part_filter": "A record exists, but only in the other scope and outside the requested part filter.",
    "part_not_acquired": "No record was acquired for this part, in baseline or in an acquired amendment.",
    "paragraph_not_found_in_acquired_section": "The section is acquired but the requested paragraph is not in it.",
    "not_found_in_acquired_part": "The part is acquired but no record for this section was found.",
}
CITATION_WORDS = {"section", "sections", "cfr", "part", "parts", "title"}


def citation_only(query):
    """True when the query is nothing but CFR citations (e.g. `12 CFR 3.22`, `§ 217.12`) and filler words."""
    if not CITE.search(query):
        return False
    return not [w for w in terms_of(CITE.sub(" ", query)) if w not in CITATION_WORDS]


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
        self._load_source_requests()

    # ---------- source requests: jurisdictions and accounting intent, independent of topic matches ----------
    def _load_source_requests(self):
        base = Path(__file__).parent
        cfg = json.loads((base / "source_requests.json").read_text())
        jur = json.loads((base / cfg["jurisdiction"]["names_file"]).read_text())
        self._jur_source = jur["source"]
        self._jur_cfg = cfg["jurisdiction"]
        self._jur_names = sorted(((n, j) for j in jur["jurisdictions"] for n in [j["name"]] + j.get("aliases", [])),
                                 key=lambda x: -len(x[0]))
        self._acct_cfg = cfg["accounting_intent"]
        self._acct_patterns = [re.compile(p, re.I) for p in self._acct_cfg["patterns"]]

    def detect_sources(self, query):
        """Source families the request names, whether or not an acquired topic also matches.

        Jurisdiction names come from the C3 STATE_JURISDICTION_REGISTER (read-only copy with provenance). Naming a
        jurisdiction reports a state_law coverage gap and nothing more: no applicability, preemption or compliance is
        asserted. Accounting intent (recognition or measurement of losses and similar) reports a gaap_asc gap.
        """
        found, masked = [], query
        positions = {}
        for name, rec in self._jur_names:
            for m in list(re.finditer(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", masked, re.I)):
                before, after = masked[:m.start()].lower(), masked[m.end():].lower()
                if any(re.search(x, before) for x in self._jur_cfg["exclusions_before"]) or \
                        any(re.search(x, after) for x in self._jur_cfg["exclusions_after"]):
                    continue
                found.append({"kind": "jurisdiction", "topic": self._jur_cfg["topic"], "matched": m.group(0), "id": rec["id"],
                              "name": rec["name"], "type": rec["type"],
                              "register": {"file": self._jur_source["file"], "sha256": self._jur_source["sha256"],
                                           "status": rec["register_status"], "coverage": rec["register_coverage"]},
                              "note": "Naming a jurisdiction does not assert state-law applicability, preemption or compliance."})
                positions[id(found[-1])] = m.start()
                masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
        found.sort(key=lambda f: positions[id(f)])  # query order, not name-length order
        for rx in self._acct_patterns:
            m = rx.search(query)
            if m:
                found.append({"kind": "accounting_intent", "topic": self._acct_cfg["topic"], "matched": m.group(0).strip(),
                              "note": "FASB ASC text is not acquired; detecting accounting intent does not answer the accounting question."})
                break
        return found

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

    @staticmethod
    def _parts_clause(parts):
        if not parts:
            return "", []
        params = []
        for spec in parts:
            params += spec.split(":")
        return " AND (" + " OR ".join("(p.title_no=? AND p.part_no=?)" for _ in parts) + ")", params

    def range_hits(self, title, part, minor=None):
        """Provision ids whose instruction-only row names a numeric `through` range containing the section.

        Membership is numeric and unreviewed; ambiguous ranges are never matched. With no `minor` (a part
        citation) every numeric range in the part matches.
        """
        sql = "SELECT src_provision_id, lo_section, hi_section, instruction_ordinal FROM amendment_range " \
              "WHERE status='numeric_range' AND part_no=?"
        params = [part]
        if title:
            sql += " AND title_no=?"
            params.append(title)
        if minor is not None:
            if not str(minor).isdigit():
                return {}
            sql += " AND lo_minor<=? AND hi_minor>=?"
            params += [int(minor), int(minor)]
        return {r[0]: {"lo": r[1], "hi": r[2], "instruction_ordinal": r[3]} for r in self.conn.execute(sql, params)}

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
        """Direct lookup for every CFR citation in the query, e.g. '12 CFR 217.11(b)' or '§ 3.12'.

        Each citation is resolved independently in the requested scope and part filter (see `resolve_citations`);
        their records are interleaved round-robin before truncation to k, so one citation cannot crowd out another.
        `current` returns eCFR baseline provisions only; `future` returns only amendment units whose explicit amends
        target (or numeric range) covers the citation, never a baseline record; `both` interleaves the two.
        A paragraph that is not in the acquired section is not replaced by its parent section in current scope;
        section-level amendment units stay discoverable and are flagged by `resolve_citations`.
        """
        lists = []
        for c in self.resolve_citations(query, scope, parts):
            lists.append([pid for pair in zip_longest(c["_current_ids"], c["_future_ids"]) for pid in pair if pid is not None])
        merged, seen = [], set()
        for group in zip_longest(*lists):
            for pid in group:
                if pid is not None and pid not in seen:
                    seen.add(pid)
                    merged.append(pid)
        return [(pid, float(len(merged) - i)) for i, pid in enumerate(merged[:k])]

    def resolve_citations(self, query, scope="both", parts=None):
        """One resolution record per distinct citation in the query, in the requested scope and part filter.

        `resolved` is true only at the requested precision. A paragraph-qualified citation that exists only as a
        section-level amendment is `resolved: False, achieved: section_only` and keeps its records discoverable.
        Unresolved records carry a `reason` and always state that an unavailable record is not a determination
        that the citation is legally invalid or nonexistent.
        """
        out, seen = [], set()
        for m in CITE.finditer(query):
            title, part, minor, paras = m.groups()
            section = f"{part}.{minor}" if minor else None
            if not section and not title:
                continue  # a bare '§ 12' is not a citation
            label = (f"{title} CFR " if title else "§ ") + (f"{section}{paras}" if section else f"Part {part}")
            if label in seen:
                continue
            seen.add(label)
            out.append(self._resolve_one(label, title, part, section, paras or "", scope, parts))
        return out

    def _paragraph_support(self, ids, paras):
        """Amendment units whose evidence supports the complete requested paragraph path.

        Evidence is the explicit path an attributed amendatory instruction names as the object of an action
        (`explicit_paragraphs`, an unreviewed text-pattern signal). Matching a first designator, a lead of the
        carried text, or an ancestor is not evidence for a deeper path, and hierarchy is not inferred from the order
        of carried paragraphs. Returns [(doc_key, locator)] of supporting units.
        """
        wanted = "".join(re.findall(r"\([A-Za-z0-9]+\)", paras))
        if not wanted or not ids:
            return []
        out = []
        for row in self.conn.execute(
                f"""SELECT d.doc_key, p.locator, p.explicit_paragraphs FROM provision p JOIN source_document d ON d.id=p.doc_id
                    WHERE p.id IN ({','.join('?' * len(ids))})""", ids):
            ev = json.loads(row["explicit_paragraphs"] or "{}")
            if ev.get("status") == "attributed" and wanted in ev.get("paths", []):
                out.append((row["doc_key"], row["locator"]))
        return out

    def _resolve_one(self, label, title, part, section, paras, scope, parts):
        cur = self._exact_current(title, part, section, paras, parts) if scope in ("current", "both") else []
        fut = self._exact_future(title, part, section, parts, section and section.split(".", 1)[1]) \
            if scope in ("future", "both") else []
        precision = "paragraph" if (section and paras) else ("section" if section else "part")
        support = self._paragraph_support(fut, paras) if (fut and paras) else []
        fut_precise = bool(fut) and (not paras or bool(support))
        resolved = bool(cur) or fut_precise
        achieved = precision if resolved else ("section_only" if (paras and fut) else None)
        record = {"citation": label, "requested_precision": precision, "resolved": resolved, "achieved": achieved,
                  "by_scope": {"current": len(cur), "future": len(fut)}, "legal_validity": "not_determined",
                  "future_paragraph_support": [{"doc_key": d, "locator": loc} for d, loc in support],
                  "_current_ids": cur, "_future_ids": fut}
        if resolved:
            record["reason"] = None
            record["note"] = ""
            return record
        reason = self._diagnose_unresolved(title, part, section, paras, scope, parts, fut)
        record["reason"] = reason
        record["note"] = (CITATION_REASONS[reason] + " This reports unavailable evidence only; it is not a determination "
                          "that the citation is invalid or does not exist.")
        return record

    def _found(self, scopes, title, part, section, paras, parts):
        minor = section and section.split(".", 1)[1]
        if "current" in scopes and self._exact_current(title, part, section, paras, parts):
            return True
        return "future" in scopes and bool(self._exact_future(title, part, section, parts, minor))

    def _diagnose_unresolved(self, title, part, section, paras, scope, parts, fut):
        requested = ("current", "future") if scope == "both" else (scope,)
        other = tuple(x for x in ("current", "future") if x not in requested)
        if paras and fut:
            return "paragraph_precision_unresolved"
        if paras and "current" in requested and self._exact_current(title, part, section, "", parts):
            return "paragraph_not_found_in_acquired_section"
        if parts and self._found(requested, title, part, section, paras, None):
            return "excluded_by_part_filter"
        if other and self._found(other, title, part, section, paras, parts):
            return "excluded_by_requested_scope"
        if parts and other and self._found(other, title, part, section, paras, None):
            return "excluded_by_scope_and_part_filter"
        row = self.conn.execute("SELECT 1 FROM provision WHERE kind='part' AND part_no=?" + (" AND title_no=?" if title else ""),
                                [part] + ([title] if title else [])).fetchone()
        if not row:
            return "part_not_acquired"
        if paras and section and self._exact_current(title, part, section, "", None):
            return "paragraph_not_found_in_acquired_section"
        return "not_found_in_acquired_part"

    def _exact_current(self, title, part, section, paras, parts):
        if section:
            where, params = self._where("current", parts)
            cond, cparams = "p.section_no=? AND p.kind IN ('paragraph','definition','table','section')", [section]
        else:
            # Part citation: the part record then its sections, from eCFR documents only. The generic current filter
            # excludes kind 'part', so it is not used here; the publisher test keeps Federal Register rows out.
            clause, params = self._parts_clause(parts)
            where = "d.publisher = 'eCFR'" + clause
            cond, cparams = "p.part_no=? AND p.kind IN ('part','section')", [part]
        if title:
            cond += " AND p.title_no=?"
            cparams.append(title)
        rows = self.conn.execute(
            f"""SELECT p.id, p.locator FROM provision p JOIN source_document d ON d.id=p.doc_id
                LEFT JOIN rulemaking r ON r.doc_key=d.doc_key WHERE {where} AND {cond} ORDER BY p.ordinal""",
            params + cparams).fetchall()
        if section and paras:  # a missing paragraph stays unresolved; it is never replaced by its parent section
            wanted = f"{section}{paras}"
            rows = [r for r in rows if r["locator"] == wanted or r["locator"].startswith(wanted + "(")]
        return [r["id"] for r in rows]

    def _exact_future(self, title, part, section, parts, minor=None):
        where, params = self._where("future", parts)
        if section and title:
            dst, dparams = "rel.dst_citation = ?", [f"{title} CFR {section}"]
        elif section:
            dst, dparams = "rel.dst_citation LIKE ?", [f"% CFR {section}"]
        elif title:
            dst, dparams = "(rel.dst_citation = ? OR rel.dst_citation LIKE ?)", [f"{title} CFR Part {part}", f"{title} CFR {part}.%"]
        else:
            return []
        rows = self.conn.execute(
            f"""SELECT DISTINCT p.id, p.ordinal FROM provision p JOIN source_document d ON d.id=p.doc_id
                LEFT JOIN rulemaking r ON r.doc_key=d.doc_key
                JOIN relationship rel ON rel.src_provision_id=p.id AND rel.rel_type='amends'
                WHERE {where} AND p.kind='amendment' AND {dst} ORDER BY r.publication_date DESC, p.ordinal""",
            params + dparams).fetchall()
        ids = [r["id"] for r in rows]
        in_range = self.range_hits(title, part, minor) if (part and (minor is not None or not section)) else {}
        if in_range:
            where2, params2 = self._where("future", parts)
            extra = self.conn.execute(
                f"""SELECT p.id FROM provision p JOIN source_document d ON d.id=p.doc_id
                    LEFT JOIN rulemaking r ON r.doc_key=d.doc_key
                    WHERE {where2} AND p.kind='amendment' AND p.id IN ({','.join('?' * len(in_range))})
                    ORDER BY r.publication_date DESC, p.ordinal""", params2 + list(in_range)).fetchall()
            ids += [r["id"] for r in extra if r["id"] not in ids]
        return ids

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
                out["explicit_targets"] = json.loads(row["explicit_targets"] or "[]")
                out["explicit_parts"] = json.loads(row["explicit_parts"] or "[]")
                out["explicit_ranges"] = json.loads(row["explicit_ranges"] or "[]")
                out["explicit_paragraphs"] = json.loads(row["explicit_paragraphs"] or "{}")
                out["pairing_status"] = row["pairing_status"]
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
        partial_evidence: results exist but a matched topic is `not_acquired` (alongside acquired ones) or `partial`,
        a jurisdiction or accounting intent is named (`source_requests`, detected independently of topic matches),
        or no catalog topic matched (coverage `unassessed`); the gaps are reported in `coverage_gaps`, so the results
        must not be read as complete. evidence_found requires every matched topic fully acquired, or a citation-only
        query whose exact lookup resolves in the requested scope and part filter.
        """
        topic_hits = self.match_topics(query)
        gaps = [{"topic": t["id"], "coverage": t["coverage"], "note": t["coverage_note"]}
                for t, _ in topic_hits if t["coverage"] in ("not_acquired", "partial")]
        blocked = [t for t, _ in topic_hits if t["coverage"] == "not_acquired"]
        only_blocked = bool(blocked) and len(blocked) == len(topic_hits) and not self.exact(query)
        results = [] if only_blocked else [self.evidence(pid) for pid, _ in self.retrieve(query, mode, k, scope, parts)]
        partial = [t for t, _ in topic_hits if t["coverage"] == "partial"]
        # A citation-only query is self-describing: it resolves to named provisions in the requested scope and part
        # filter, so it needs no topic. Any other query that matches no catalog topic has unassessed source
        # coverage, and a lexical hit alone does not establish that the sources it needs are acquired.
        sources = self.detect_sources(query)
        by_topic = {g["topic"]: g for g in gaps}
        for src in sources:
            topic = next(t for t in self.topics if t["id"] == src["topic"])
            gap = by_topic.get(src["topic"])
            if gap is None:
                gap = {"topic": topic["id"], "coverage": topic["coverage"], "note": topic["coverage_note"], "detected_by": []}
                gaps.append(gap)
                by_topic[topic["id"]] = gap
            gap.setdefault("detected_by", []).append(f"{src['kind']}: {src['matched']}")
            if src["note"] not in gap["note"]:
                gap["note"] += " " + src["note"]
        citations = self.resolve_citations(query, scope, parts)
        unresolved = [c for c in citations if not c["resolved"]]
        for c in unresolved:  # one gap per unresolved citation, whatever else resolved or matched a topic
            gaps.append({"topic": f"citation:{c['citation']}", "coverage": "unresolved_citation", "reason": c["reason"],
                         "note": c["note"]})
        resolved_citation = (not topic_hits and citation_only(query) and bool(citations) and not unresolved)
        unassessed = not topic_hits and not resolved_citation
        if unassessed:
            gaps.append({"topic": "none_matched", "coverage": "unassessed",
                         "note": "No catalog topic matched this query, so source coverage is unassessed. A lexical hit "
                                 "does not show that the sources needed to answer it are acquired."})
        if not results:
            status = "missing_evidence"
        elif blocked or partial or unassessed or unresolved or sources:
            status = "partial_evidence"   # results exist, but coverage, a named source or a requested citation is incomplete
        else:
            status = "evidence_found"
        return {
            "query": query, "mode": mode, "scope": scope, "as_of": self.as_of, "status": status,
            "applicability_status": "not_assessed",
            "applicability_note": "Retrieval is evidence discovery. A bank profile (charter, regulator, consolidation "
                                  "scope, size/risk category, elections) is required before any applies / does_not_apply "
                                  "conclusion.",
            "topics_matched": [t["id"] for t, _ in topic_hits], "coverage_gaps": gaps, "results": results,
            "citations": [{k: v for k, v in c.items() if not k.startswith("_")} for c in citations],
            "source_requests": sources,
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
                """SELECT DISTINCT r.src_provision_id FROM relationship r
                   WHERE r.rel_type='amends' AND r.dst_citation=?""", (f"{title} CFR {section}",)).fetchall()
            ranged = self.range_hits(title, part, minor)
        else:
            base_rows = self.conn.execute(
                """SELECT p.*, d.doc_key, d.sha256, d.publisher_issue_date, d.publisher_up_to_date_as_of FROM provision p
                   JOIN source_document d ON d.id=p.doc_id WHERE p.title_no=? AND p.part_no=? AND p.kind='part'""",
                (title, part)).fetchall()
            amend = self.conn.execute(
                """SELECT DISTINCT r.src_provision_id FROM relationship r
                   WHERE r.rel_type='amends' AND (r.dst_citation=? OR r.dst_citation LIKE ?)""",
                (f"{title} CFR Part {part}", f"{title} CFR {part}.%")).fetchall()
            ranged = self.range_hits(title, part)
        amend = [r[0] for r in amend] + [pid for pid in ranged if pid not in {r[0] for r in amend}]
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
        for pid in amend:
            ev = self.evidence(pid, with_context=False)
            g = grouped.setdefault(ev["doc_key"], {"doc_key": ev["doc_key"], "document_number": ev["status"]["document_number"],
                                                   "document_type": ev["status"]["document_type"], "status": ev["status"],
                                                   "source_sha256": ev["source_sha256"], "source_url": ev["source_url"],
                                                   "units": []})
            g["units"].append({"locator": ev["locator"], "fr_page": ev.get("fr_page"), "instruction": ev.get("instruction"),
                               "carried_text": ev["text"], "explicit_targets": ev.get("explicit_targets"),
                               "explicit_parts": ev.get("explicit_parts"), "pairing_status": ev.get("pairing_status"),
                               "match": ("range_membership" if pid in ranged and not (
                                   section and section in (ev.get("explicit_targets") or [])) else "explicit_target"),
                               "range": ranged.get(pid), "review_status": "parsed_unreviewed"})
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
        candidates = []
        like = (f"{title} CFR {section}",) if section else (f"{title} CFR {part}.%",)
        op = "=" if section else "LIKE"
        for r in self.conn.execute(
                f"""SELECT DISTINCT d.doc_key, p.locator, p.pairing_status, p.unresolved_candidates FROM relationship rel
                    JOIN provision p ON p.id=rel.src_provision_id JOIN source_document d ON d.id=p.doc_id
                    WHERE rel.rel_type='amends_candidate' AND rel.dst_citation {op} ?""", like):
            candidates.append({"doc_key": r["doc_key"], "locator": r["locator"], "pairing_status": r["pairing_status"],
                               "candidate_instructions": json.loads(r["unresolved_candidates"] or "[]")})
        for r in self.conn.execute(
                """SELECT d.doc_key, p.locator, a.lo_section, a.hi_section, a.instruction_ordinal, a.basis, p.instruction
                   FROM amendment_range a JOIN provision p ON p.id=a.src_provision_id JOIN source_document d ON d.id=p.doc_id
                   WHERE a.status='unresolved_ambiguous' AND (a.lo_section LIKE ? OR a.hi_section LIKE ? OR a.lo_section = ?
                         OR a.hi_section = ?)""",
                (f"{part}.%", f"{part}.%", section or "", section or "")):
            if not section or section in (r["lo_section"], r["hi_section"]):
                candidates.append({"doc_key": r["doc_key"], "locator": r["locator"], "pairing_status": "ambiguous_range",
                                   "candidate_instructions": [{"instruction": r["instruction"],
                                                               "instruction_ordinal": r["instruction_ordinal"],
                                                               "range": [r["lo_section"], r["hi_section"]],
                                                               "reason": r["basis"]}]})
        return {"citation": citation, "as_of": self.as_of, "baseline": baseline, "instruments": instruments,
                "unresolved_candidates": candidates, "applicability_status": "not_assessed",
                "notes": ["Instruments are independent; no cumulative future rulebook is constructed.",
                          "Only explicit amendment targets appear as instruments; unpaired or ambiguous matches are listed under unresolved_candidates.",
                          "baseline_relation compares publisher dates only; confirm against the text before relying on it."]}
