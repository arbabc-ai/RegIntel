"""Build the Phase 1 SQLite evidence index from a corpus_download.py manifest."""
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from . import schema
from .ecfr import parse_ecfr
from .fedreg import extract_spans, parse_fr

TOPICS_PATH = Path(__file__).with_name("topics.json")
RANGE_SECTION = re.compile(r"^(\d+)\.(\d+)-(\d+)\.(\d+)$")
SECTION_REF = re.compile(r"§§?\s*([0-9]+\.[0-9]+[A-Za-z]?)")
FTS_SKIP_KINDS = {"part", "subpart", "authority", "source", "footnote"}


NON_CONTENT_KINDS = ("part", "subpart", "authority", "source", "footnote")
DEPENDENCIES_PATH = Path(__file__).with_name("topic_dependencies.json")


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
           operations, explicit_targets, explicit_parts, explicit_ranges, carried_text_origin, explicit_paragraphs, referenced_citations, unresolved_candidates,
           pairing_status, instruction_targets_all, instruction_ordinals, paragraph_leads, appendix)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (doc_id, p["locator"], p.get("citation"), p["kind"], p.get("title_no"), p.get("part_no"),
         p.get("section_no"), p.get("subpart"), p.get("heading"), p.get("context"), p["text"],
         p.get("defined_term"), p.get("parent_locator"), p.get("ordinal"), p.get("char_start"), p.get("char_end"),
         p.get("fr_page"), p.get("instruction"),
         json.dumps(p["operations"]) if "operations" in p else None,
         *(json.dumps(p[k]) if k in p else None for k in (
             "explicit_targets", "explicit_parts", "explicit_ranges")),
         p.get("carried_text_origin"),
         *(json.dumps(p[k]) if k in p else None for k in ("explicit_paragraphs", "referenced_citations", "unresolved_candidates")),
         p.get("pairing_status"),
         json.dumps(p["instruction_targets_all"]) if "instruction_targets_all" in p else None,
         json.dumps(p["instruction_ordinals"]) if "instruction_ordinals" in p else None,
         json.dumps(p["paragraph_leads"]) if "paragraph_leads" in p else None,
         json.dumps(p["appendix"]) if p.get("appendix") else None))
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
    source_status = {}  # doc_key -> (publisher, status, reason, provisions, usable provisions)
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
            source_status[key] = (d.get("publisher"), "skipped_hash_or_size_mismatch", "bytes do not match the manifest sha256 or size", 0, 0)
            log(f"SKIP {key}: bytes do not match manifest hash")
            continue
        fmt = path.suffix.lstrip(".")
        if fmt not in ("html", "xml"):
            counts["skipped"].append({"doc_key": key, "reason": f"unsupported_format_{fmt}"})
            source_status[key] = (d.get("publisher"), "skipped_unsupported_format", f"format {fmt} is not supported", 0, 0)
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
            spans = extract_spans(raw.decode("utf-8")) if any(u.get("appendix") for u in units) else []
            n = 0  # position among non-appendix-text units: unchanged by recovered appendix chunks, so earlier locators are stable
            for k, u in enumerate(units, 1):
                ap = u.get("appendix")
                if ap and ap.get("kind") == "appendix_extract" and u["text"]:
                    span = spans[ap["extract_index"]] if ap.get("extract_index") is not None and ap["extract_index"] < len(spans) else (None, None)
                    ap = {**ap, "source_sha256": digest, "source_asset_url": asset.get("url"), "source_url": d.get("source_url"),
                          "source_span": "whole EXTRACT element, characters of the UTF-8-decoded source",
                          "policy_statement": bool(re.search(r"policy statement", ap.get("title") or "", re.I)),
                          "note": "text as published; binding effect, applicability and amended passages are not determined"}
                    where = f"appx-{ap['part']}-{ap['letter'] or 'unknown'}"
                    provs.append({
                        "locator": f"{u['unit_id']}:{where}:{ap['chunk']:03d}of{ap['chunks']:03d}", "kind": "amendment",
                        "citation": (f"FR Doc. {doc_no}, appendix text: {u['title_no'] or '?'} CFR Part {ap['part']}, "
                                     f"Appendix {ap['letter'] or '?'} (passage {ap['chunk']} of {ap['chunks']})"),
                        "title_no": u["title_no"], "part_no": u["part_no"], "section_no": None,
                        "heading": ap["heading"], "context": ap["heading"] or "", "text": u["text"],
                        "instruction": u["instruction"], "operations": u["operations"],
                        "explicit_targets": [], "explicit_parts": [], "explicit_ranges": [],
                        "explicit_paragraphs": u["explicit_paragraphs"], "carried_text_origin": u["carried_text_origin"],
                        "referenced_citations": [], "unresolved_candidates": u["unresolved_candidates"],
                        "pairing_status": u["pairing_status"], "instruction_targets_all": u["instruction_targets_all"],
                        "instruction_ordinals": u["instruction_ordinals"], "paragraph_leads": u["paragraph_leads"],
                        "fr_page": u["fr_page"], "ordinal": 200000 + k, "char_start": span[0], "char_end": span[1],
                        "appendix": ap})
                    continue
                n += 1
                label = u["section_no"] or (f"Part {u['part_no']}" if u["part_no"] else "regulatory text")
                provs.append({
                    "locator": f"{u['unit_id']}:{u['section_no'] or 'part'}:{n}", "kind": "amendment",
                    "citation": f"FR Doc. {doc_no}, amendment to {u['title_no'] or '?'} CFR {label}",
                    "title_no": u["title_no"], "part_no": u["part_no"], "section_no": u["section_no"],
                    "heading": u["subject"], "context": u["subject"] or "", "text": u["text"] or u["instruction"],
                    "instruction": u["instruction"], "operations": u["operations"],
                    "explicit_targets": u["explicit_targets"], "explicit_parts": u["explicit_parts"],
                    "explicit_ranges": u["explicit_ranges"], "explicit_paragraphs": u["explicit_paragraphs"],
                    "carried_text_origin": u["carried_text_origin"],
                    "referenced_citations": u["referenced_citations"],
                    "unresolved_candidates": u["unresolved_candidates"], "pairing_status": u["pairing_status"],
                    "instruction_targets_all": u["instruction_targets_all"],
                    "instruction_ordinals": u["instruction_ordinals"], "paragraph_leads": u["paragraph_leads"],
                    "fr_page": u["fr_page"], "ordinal": 100000 + n,
                    **({"appendix": ap} if ap else {})})
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
        usable = sum(1 for p in provs if p["kind"] not in NON_CONTENT_KINDS)
        source_status[key] = (d["publisher"], "indexed" if usable else "empty_no_usable_provisions",
                              None if usable else "the parser produced no usable provisions from the verified bytes", len(provs), usable)
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
    record_source_status(conn, source_status, counts)
    for k, v in schema.dependency_fingerprints(DEPENDENCIES_PATH, TOPICS_PATH).items():  # the dependency check ran against these exact inputs
        conn.execute("INSERT INTO index_meta VALUES (?, ?)", (k, v))
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
    ranges = 0
    for pid, title, ranges_json in conn.execute(
            "SELECT id, title_no, explicit_ranges FROM provision WHERE kind='amendment' AND explicit_ranges IS NOT NULL "
            "AND explicit_ranges != '[]'").fetchall():
        for r in json.loads(ranges_json):
            numeric = r["status"] == "numeric_range"
            conn.execute(
                "INSERT INTO amendment_range (src_provision_id, title_no, part_no, lo_section, hi_section, lo_minor, "
                "hi_minor, status, instruction_ordinal, basis, review_status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pid, title, r.get("part"), r["lo"], r["hi"], r.get("lo_minor"), r.get("hi_minor"), r["status"],
                 r.get("instruction_ordinal"),
                 f"`{r['lo']} through {r['hi']}` named by an instruction-only amendment; interior membership is "
                 + ("numeric" if numeric else "NOT asserted (ambiguous range)"),
                 "parsed_unreviewed" if numeric else "unresolved_candidate"))
            ranges += 1
    counts["amendment_ranges"] = ranges
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


def record_source_status(conn, source_status, counts):
    """Persist every document's indexing outcome and reconcile each acquired topic's declared eCFR baseline dependencies.

    A dependency is available only if its document was verified against the manifest, parsed and indexed with usable
    provisions. Missing, skipped and empty dependencies are persisted per topic with the reason, so a degraded build
    cannot keep an unsupported coverage claim. Federal Register documents the coverage note names (`required_rulemaking_sources`) are reconciled the same way. Sections the coverage note names (`required_sections`) must also be present as indexed
    sections of an indexed document. This is source availability, not provision coverage or applicability.
    """
    dependencies = json.loads(DEPENDENCIES_PATH.read_text())
    declared = dependencies["required_baseline_sources"]
    rulemaking = dependencies.get("required_rulemaking_sources") or {}
    catalog = {t["id"]: t for t in json.loads(TOPICS_PATH.read_text())["topics"]}
    unknown = sorted((set(declared) | set(rulemaking)) - set(catalog))
    if unknown:
        raise ValueError(f"topic_dependencies.json names unknown topics: {unknown}")
    gaps = 0
    for topic_id, dep in sorted(declared.items()):
        if catalog[topic_id]["coverage"] != "acquired":
            continue  # only topics declared acquired make a source claim to reconcile
        for key in dep["sources"]:
            if key not in source_status:
                source_status[key] = (None, "missing_from_manifest", "the document is not in the manifest", 0, 0)
            publisher, status, reason, _, _ = source_status[key]
            if status != "indexed":
                conn.execute("INSERT INTO topic_source_gap VALUES (?,?,?,?)", (topic_id, key, status, reason))
                gaps += 1
        # Sections the coverage note names: only checked in documents that are indexed (an unindexed document already has its own gap).
        for key, sections in sorted((dep.get("required_sections") or {}).items()):
            if key not in dep["sources"]:
                raise ValueError(f"topic_dependencies.json: {topic_id} requires sections of {key}, which is not one of its sources")
            if source_status[key][1] != "indexed":
                continue
            for section in sections:
                found = conn.execute("""SELECT 1 FROM provision p JOIN source_document d ON d.id = p.doc_id
                                        WHERE d.doc_key = ? AND p.kind = 'section' AND p.section_no = ? LIMIT 1""", (key, section)).fetchone()
                if not found:
                    conn.execute("INSERT INTO topic_source_gap VALUES (?,?,?,?)",
                                 (topic_id, f"{key}#{section}", "missing_required_section",
                                  f"the indexed document has no section {section}, which the topic's coverage note names"))
                    gaps += 1
    # Federal Register documents the coverage note names as acquired: a separate declaration, reconciled identically.
    for topic_id, dep in sorted(rulemaking.items()):
        if catalog[topic_id]["coverage"] != "acquired":
            continue
        for key in dep["sources"]:
            if not key.startswith("fr:"):
                raise ValueError(f"topic_dependencies.json: {topic_id} declares rulemaking source {key}, which is not a Federal Register document key")
            if key not in source_status:
                source_status[key] = (None, "missing_from_manifest", "the document is not in the manifest", 0, 0)
            status, reason = source_status[key][1:3]
            if status != "indexed":
                conn.execute("INSERT INTO topic_source_gap VALUES (?,?,?,?)", (topic_id, key, status, reason))
                gaps += 1
    for key, (publisher, status, reason, provisions, usable) in sorted(source_status.items()):
        conn.execute("INSERT INTO source_status VALUES (?,?,?,?,?,?)", (key, publisher, status, reason, provisions, usable))
    counts["unavailable_topic_sources"] = gaps


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
