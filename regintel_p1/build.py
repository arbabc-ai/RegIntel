"""Build the Phase 1 SQLite evidence index from a corpus_download.py manifest."""
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from . import schema
from .ecfr import parse_ecfr
from .fedreg import parse_fr

TOPICS_PATH = Path(__file__).with_name("topics.json")
RANGE_SECTION = re.compile(r"^(\d+)\.(\d+)-(\d+)\.(\d+)$")
SECTION_REF = re.compile(r"§§?\s*([0-9]+\.[0-9]+[A-Za-z]?)")
FTS_SKIP_KINDS = {"part", "subpart", "authority", "source", "footnote"}


def fts_rows(prov):
    head = " ".join(x for x in (prov.get("citation"), prov.get("heading"), prov.get("defined_term"),
                                prov.get("subject")) if x)
    body = " ".join(x for x in (prov.get("context"), prov.get("instruction"), prov["text"]) if x)
    return head, body


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def insert_provision(conn, doc_id, p):
    cur = conn.execute(
        """INSERT INTO provision (doc_id, locator, citation, kind, title_no, part_no, section_no, subpart, heading,
           context, text, defined_term, parent_locator, ordinal, char_start, char_end, fr_page, instruction,
           operations, explicit_targets, explicit_parts, referenced_citations, unresolved_candidates,
           pairing_status, instruction_targets_all, instruction_ordinals, paragraph_leads)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (doc_id, p["locator"], p.get("citation"), p["kind"], p.get("title_no"), p.get("part_no"),
         p.get("section_no"), p.get("subpart"), p.get("heading"), p.get("context"), p["text"],
         p.get("defined_term"), p.get("parent_locator"), p.get("ordinal"), p.get("char_start"), p.get("char_end"),
         p.get("fr_page"), p.get("instruction"),
         json.dumps(p["operations"]) if "operations" in p else None,
         *(json.dumps(p[k]) if k in p else None for k in (
             "explicit_targets", "explicit_parts", "referenced_citations", "unresolved_candidates")),
         p.get("pairing_status"),
         json.dumps(p["instruction_targets_all"]) if "instruction_targets_all" in p else None,
         json.dumps(p["instruction_ordinals"]) if "instruction_ordinals" in p else None,
         json.dumps(p["paragraph_leads"]) if "paragraph_leads" in p else None))
    pid = cur.lastrowid
    if p["kind"] not in FTS_SKIP_KINDS:
        head, body = fts_rows(p)
        conn.execute("INSERT INTO provision_fts(rowid, head, body) VALUES (?,?,?)", (pid, head, body))
    return pid


def build(corpus_dir, db_path, as_of, log=print):
    corpus = Path(corpus_dir)
    manifest = json.loads((corpus / "manifest.json").read_text())
    db = Path(db_path)
    if db.exists():
        db.unlink()
    conn = schema.create(db)
    started = time.time()
    counts = {"documents": 0, "provisions": 0, "skipped": []}
    section_ids = {}   # (title, section_no) -> provision id
    range_ids = {}     # (title, section_no inside a range section) -> (provision id, range section_no)
    part_ids = {}      # (title, part) -> provision id
    definitions = {}   # (title, part) -> [(term, provision id)]
    ecfr_rows = []     # (pid, title, part, text, locator, kind)

    for key, d in sorted(manifest["documents"].items()):
        asset = d["asset"]
        path = corpus / asset["path"]
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != asset["sha256"] or len(raw) != asset["bytes"]:
            counts["skipped"].append({"doc_key": key, "reason": "hash_or_size_mismatch"})
            log(f"SKIP {key}: bytes do not match manifest hash")
            continue
        fmt = path.suffix.lstrip(".")
        if fmt not in ("html", "xml"):
            counts["skipped"].append({"doc_key": key, "reason": f"unsupported_format_{fmt}"})
            continue
        if d["publisher"] == "eCFR":
            title, part = key.split(":")[1:]
            provs, stats = parse_ecfr(raw.decode("utf-8"), title, part)
        else:
            provs_fr, units, stats = parse_fr(raw, d["document_number"])
            provs = []
            doc_no = d["document_number"]
            for n, passage in enumerate(provs_fr, 1):
                provs.append({
                    "locator": f"{doc_no}:{'tbl' if passage['kind'] == 'table' else 'pre'}:{n}",
                    "kind": "fr_table" if passage["kind"] == "table" else "fr_preamble",
                    "citation": f"FR Doc. {doc_no}" + (f", p. {passage['fr_page']}" if passage["fr_page"] else ""),
                    "heading": passage["heading"], "context": passage["heading"], "text": passage["text"],
                    "fr_page": passage["fr_page"], "ordinal": n})
            for n, u in enumerate(units, 1):
                label = u["section_no"] or (f"Part {u['part_no']}" if u["part_no"] else "regulatory text")
                provs.append({
                    "locator": f"{u['unit_id']}:{u['section_no'] or 'part'}:{n}", "kind": "amendment",
                    "citation": f"FR Doc. {doc_no}, amendment to {u['title_no'] or '?'} CFR {label}",
                    "title_no": u["title_no"], "part_no": u["part_no"], "section_no": u["section_no"],
                    "heading": u["subject"], "context": u["subject"] or "", "text": u["text"] or u["instruction"],
                    "instruction": u["instruction"], "operations": u["operations"],
                    "explicit_targets": u["explicit_targets"], "explicit_parts": u["explicit_parts"],
                    "referenced_citations": u["referenced_citations"],
                    "unresolved_candidates": u["unresolved_candidates"], "pairing_status": u["pairing_status"],
                    "instruction_targets_all": u["instruction_targets_all"],
                    "instruction_ordinals": u["instruction_ordinals"], "paragraph_leads": u["paragraph_leads"],
                    "fr_page": u["fr_page"], "ordinal": 100000 + n})
            stats = {**stats, "provisions": len(provs)}
        cur = conn.execute(
            """INSERT INTO source_document (doc_key, source_class, publisher, citation, title, source_url, asset_path,
               sha256, bytes, format, publisher_issue_date, publisher_up_to_date_as_of, retrieved_at,
               acquisition_rule_status, parse_stats) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, d["source_class"], d["publisher"], d.get("citation"), d.get("title"), d.get("source_url"),
             asset["path"], digest, asset["bytes"], fmt, d.get("publisher_issue_date"),
             d.get("publisher_up_to_date_as_of"), asset.get("retrieved_at"), d.get("rule_status"),
             json.dumps(stats)))
        doc_id = cur.lastrowid
        if d["publisher"] == "Federal Register":
            conn.execute(
                """INSERT INTO rulemaking VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, d["document_number"], d["document_type"], d.get("action"), d.get("title"),
                 d.get("publication_date"), d.get("comments_close_on"), d.get("effective_on"), d.get("dates_text"),
                 json.dumps(d.get("regulation_id_numbers") or []), json.dumps(d.get("docket_ids") or []),
                 json.dumps(d.get("agencies") or []),
                 json.dumps(sorted({f"{c['title']}:{c['part']}" for c in d.get("cfr_references") or []})),
                 d.get("official_pdf_url") or d.get("source_url")))
        for p in provs:
            pid = insert_provision(conn, doc_id, p)
            if d["publisher"] == "eCFR":
                if p["kind"] == "section" and p["section_no"]:
                    section_ids[(p["title_no"], p["section_no"])] = pid
                    span = RANGE_SECTION.match(p["section_no"])
                    if span and span.group(1) == span.group(3):  # reserved/range section such as 3.4-3.9
                        for n in range(int(span.group(2)), int(span.group(4)) + 1):
                            range_ids.setdefault((p["title_no"], f"{span.group(1)}.{n}"), (pid, p["section_no"]))
                elif p["kind"] == "part":
                    part_ids[(p["title_no"], p["part_no"])] = pid
                elif p["kind"] == "definition" and p.get("defined_term"):
                    definitions.setdefault((p["title_no"], p["part_no"]), []).append((p["defined_term"], pid))
                ecfr_rows.append((pid, p["title_no"], p["part_no"], p["text"], p["locator"], p["kind"]))
        counts["documents"] += 1
        counts["provisions"] += len(provs)
        log(f"{key}: {len(provs)} provisions")

    link_amendments(conn, section_ids, range_ids, part_ids, counts)
    link_ecfr_text(conn, section_ids, definitions, ecfr_rows, counts)
    link_rulemakings(conn)
    load_topics(conn, counts)
    conn.execute("INSERT INTO index_meta VALUES ('as_of', ?)", (as_of,))
    conn.execute("INSERT INTO index_meta VALUES ('built_at', ?)", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),))
    conn.execute("INSERT INTO index_meta VALUES ('manifest_sha256', ?)", (sha256_file(corpus / "manifest.json"),))
    conn.execute("INSERT INTO index_meta VALUES ('manifest_updated_at', ?)", (manifest.get("updated_at") or "",))
    conn.commit()
    counts["seconds"] = round(time.time() - started, 1)
    return conn, counts


def link_amendments(conn, section_ids, range_ids, part_ids, counts):
    """amends only from explicit targets; citations merely referenced stay `references`; ambiguity stays a candidate."""
    n = resolved = refs = cands = 0

    def resolve(title, section):
        dst = section_ids.get((title, section))
        note = ""
        if dst is None and (title, section) in range_ids:
            dst, span = range_ids[(title, section)]
            note = f" (resolved to range section {span})"
        return dst, note

    rows = conn.execute("SELECT id, title_no, part_no, section_no, locator, pairing_status, explicit_targets, "
                        "explicit_parts, referenced_citations, unresolved_candidates, instruction_targets_all "
                        "FROM provision WHERE kind='amendment'").fetchall()
    for pid, title, part, section, locator, status, targets, parts, refs_json, cand_json, all_json in rows:
        if not title:
            continue
        named = set(json.loads(all_json or "{}").get("targets", []))
        for sec in json.loads(targets or "[]"):
            dst, note = resolve(title, sec)
            how = ("explicit target of the paired amendatory instruction" if sec in named
                   else "inside a range named by the paired instruction")
            conn.execute("INSERT INTO relationship (src_provision_id, rel_type, dst_citation, dst_provision_id, basis, "
                         "source_locator, review_status) VALUES (?,?,?,?,?,?,?)",
                         (pid, "amends", f"{title} CFR {sec}", dst, f"{how} [{status}]{note}", locator,
                          "parsed_unreviewed"))
            n += 1
            resolved += dst is not None
        for prt in json.loads(parts or "[]"):
            dst = part_ids.get((title, prt))
            conn.execute("INSERT INTO relationship (src_provision_id, rel_type, dst_citation, dst_provision_id, basis, "
                         "source_locator, review_status) VALUES (?,?,?,?,?,?,?)",
                         (pid, "amends", f"{title} CFR Part {prt}", dst,
                          f"explicit part-level target of the paired instruction [{status}]", locator,
                          "parsed_unreviewed"))
            n += 1
            resolved += dst is not None
        for ref in json.loads(refs_json or "[]"):
            dst, note = resolve(title, ref["section"])
            conn.execute("INSERT INTO relationship (src_provision_id, rel_type, dst_citation, dst_provision_id, basis, "
                         "source_locator, review_status) VALUES (?,?,?,?,?,?,?)",
                         (pid, "references", f"{title} CFR {ref['section']}", dst,
                          "citation inside amendatory instruction, not an amendment target"
                          + (" (quoted text)" if ref["quoted"] else "") + note, locator, "regex_unreviewed"))
            refs += 1
        candidate_sections = set()
        if status == "carried_text_unpaired" and section:
            candidate_sections.add(section)
        for cand in json.loads(cand_json or "[]"):
            candidate_sections.update(cand.get("candidate_sections", []))
        for sec in sorted(candidate_sections):
            dst, note = resolve(title, sec)
            conn.execute("INSERT INTO relationship (src_provision_id, rel_type, dst_citation, dst_provision_id, basis, "
                         "source_locator, review_status) VALUES (?,?,?,?,?,?,?)",
                         (pid, "amends_candidate", f"{title} CFR {sec}", dst,
                          f"could not be paired explicitly [{status}]; see unresolved_candidates{note}", locator,
                          "unresolved_candidate"))
            cands += 1
    counts["amends_relationships"] = n
    counts["amends_resolved_to_acquired_section"] = resolved
    counts["amendment_instruction_references"] = refs
    counts["amends_candidates_unresolved"] = cands


def link_ecfr_text(conn, section_ids, definitions, ecfr_rows, counts):
    refs = uses = 0
    matchers = {}
    for key, defs in definitions.items():
        terms = sorted({t for t, _ in defs}, key=len, reverse=True)
        pattern = "|".join(re.escape(t) for t in terms if len(t) > 3)
        by_term = {t.lower(): pid for t, pid in defs}
        if pattern:
            matchers[key] = (re.compile(r"(?<![A-Za-z])(?:" + pattern + r")(?![A-Za-z])", re.I), by_term)
    for pid, title, part, text, locator, kind in ecfr_rows:
        if kind in ("part", "subpart", "authority", "source", "footnote", "table"):
            continue
        seen = set()
        for m in SECTION_REF.finditer(text):
            sec = m.group(1)
            if sec in seen:
                continue
            seen.add(sec)
            dst = section_ids.get((title, sec))
            conn.execute("INSERT INTO relationship (src_provision_id, rel_type, dst_citation, dst_provision_id, basis, "
                         "source_locator, review_status) VALUES (?,?,?,?,?,?,?)",
                         (pid, "references", f"{title} CFR {sec}", dst, "textual section reference",
                          locator, "regex_unreviewed"))
            refs += 1
        matcher = matchers.get((title, part))
        if matcher and kind != "definition":
            found = {m.group(0).lower() for m in matcher[0].finditer(text)}
            for term in sorted(found):
                dst = matcher[1].get(term)
                if dst and dst != pid:
                    conn.execute("INSERT INTO relationship (src_provision_id, rel_type, dst_citation, dst_provision_id,"
                                 " basis, source_locator, review_status) VALUES (?,?,?,?,?,?,?)",
                                 (pid, "uses_definition", f"{title} CFR Part {part} definition: {term}", dst,
                                  "defined term appears in text (same part)", locator, "regex_unreviewed"))
                    uses += 1
    for pid, term in conn.execute("SELECT id, defined_term FROM provision WHERE kind='definition'").fetchall():
        loc = conn.execute("SELECT locator FROM provision WHERE id=?", (pid,)).fetchone()[0]
        conn.execute("INSERT INTO relationship (src_provision_id, rel_type, dst_citation, dst_provision_id, basis, "
                     "source_locator, review_status) VALUES (?,?,?,?,?,?,?)",
                     (pid, "defines", f"term: {term}", pid, "definition paragraph (data-term)", loc,
                      "parsed_unreviewed"))
    counts["references_relationships"] = refs
    counts["uses_definition_relationships"] = uses


def link_rulemakings(conn):
    rows = conn.execute("SELECT doc_key, rins, title FROM rulemaking WHERE document_type IN ('Rule','Proposed Rule')").fetchall()
    by_rin = {}
    for key, rins, title in rows:
        for rin in json.loads(rins or "[]"):
            by_rin.setdefault(rin, []).append(key)
    for rin, keys in by_rin.items():
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                conn.execute("INSERT OR IGNORE INTO rulemaking_link VALUES (?,?,?,?,?)",
                             (a, b, "shared_rin", f"both name RIN {rin}", "candidate_unreviewed"))


def load_topics(conn, counts):
    catalog = json.loads(TOPICS_PATH.read_text())
    docs = {r[0]: r[1] for r in conn.execute("SELECT id, publisher FROM source_document")}
    assigned = 0
    for t in catalog["topics"]:
        conn.execute("INSERT INTO topic VALUES (?,?,?,?,?,?,?)",
                     (t["id"], t["label"], t["description"], json.dumps(t["synonyms"]), t["coverage"],
                      t["coverage_note"], json.dumps(t["spec"])))
        spec = t["spec"]
        if spec.get("query_only"):
            continue
        keywords = [k.lower() for k in spec["any"]]
        parts = set(spec["parts"])
        rows = conn.execute("SELECT id, doc_id, title_no, part_no, heading, text, instruction, kind FROM provision "
                            "WHERE kind NOT IN ('part','subpart','authority','source','footnote')").fetchall()
        for pid, doc_id, title, part, heading, text, instruction, kind in rows:
            is_fr = docs[doc_id] == "Federal Register"
            if is_fr and "FR" not in parts:
                continue
            if not is_fr and f"{title}:{part}" not in parts:
                continue
            hay = f"{heading or ''} {instruction or ''} {text}".lower()
            hit = next((k for k in keywords if k in hay), None)
            if hit:
                conn.execute("INSERT OR IGNORE INTO provision_topic VALUES (?,?,?,?)",
                             (pid, t["id"], f"keyword '{hit}' in provision text", "auto_provisional"))
                assigned += 1
    counts["topic_assignments"] = assigned
