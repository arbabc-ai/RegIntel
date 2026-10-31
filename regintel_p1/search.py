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

from .fedreg import DEPTH_CLASS, designator_classes, expand_shorthand, paren_suffix

STOP = set("""a an and are as at be by can do does for from has have how if in into is it its of on or that the their
there this to under what when where which who whom why will with within would must should shall may not any all
bank banks institution institutions requirement requirements rule rules regulation regulations""".split())
# One parser serves citation_only, resolve_citations (and so exact/hybrid retrieval and answer) and delta. The earlier
# head-plus-paragraphs pattern was removed: it stopped at whitespace inside a designator and so accepted an ancestor prefix.
# An explicit title may be followed by `Part`, by a section sign (`31 CFR \u00a7 3.22`, `31 CFR \u00a7\u00a7 3.22`), or directly by the number;
# the title stays attached to the citation in every form. A bare section sign alone is a titleless citation.
CITE_HEAD = re.compile(r"(?:(\d+)\s*CFR\s*(?:Parts?\s*|\u00a7+\s*)?|\u00a7+\s*)(\d+)(?:\.(\d+[A-Za-z]?))?", re.I)


LIST_SEP = re.compile(r"(?:\s*[,;]\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)", re.I)
SECTION_MEMBER = re.compile(r"(?<![\w.])(\d+)\.(\d+[A-Za-z]?)(?![A-Za-z0-9])(?!\s*(?:%|percent\b))", re.I)
BARE_DESIGNATOR = re.compile(r"\(\s*([A-Za-z0-9]+)\s*\)")
BARE_OPENER = re.compile(r"\(\s*([A-Za-z0-9]+)")


def _read_designators(query, pos):
    """Complete designators written one after another from `pos`, and the unread suffix (if any) that stops them."""
    designators, suffix = [], None
    while True:
        found = paren_suffix(query, pos)
        if found and found[0] == "complete":
            designators.append(found[1])
            pos = found[2]
            continue
        suffix = found if found and found[0] == "incomplete" else None
        return designators, pos, suffix


def _citation_record(title, part, minor, designators, pos, suffix, query, start, inherited=None):
    section = f"{part}.{minor}" if minor else None
    end = suffix[2] if suffix else pos
    paras = "".join(f"({d})" for d in designators)
    canonical = (f"{title} CFR " if title else "\u00a7 ") + (f"{section}{paras}" if section else f"Part {part}")
    requested = query[start:end]
    malformed = diagnostics = None
    if suffix:
        malformed = suffix[1]
        diagnostics = {"requested": " ".join(requested.split()), "accepted_valid_prefix": False,
                       "valid_prefix": f"{section}{paras}", "unread_suffix": query[pos:end].strip()}
    return {"title": title, "part": part, "minor": minor, "section": section, "paras": paras, "canonical": canonical,
            "requested": requested, "label": " ".join(requested.split()) if malformed else canonical,
            "malformed": malformed, "diagnostics": diagnostics, "inherited": inherited, "designators": list(designators), "_end": end,
            "_start": start}


def _unresolved_expression(reason, expression, start, end, prev, candidates, kind="paragraph"):  # `start` is where the expression begins
    """A list member that cannot be settled: kept as a structured unresolved expression, never dropped or guessed."""
    label = f"{' '.join(expression.split())} after {prev['label']}"
    return {"title": prev["title"], "part": prev["part"], "minor": prev["minor"], "section": prev["section"], "paras": "",
            "canonical": None, "requested": expression, "label": label, "malformed": reason,
            "diagnostics": {"requested": " ".join(expression.split()), "accepted_valid_prefix": False,
                            "valid_prefix": prev["canonical"], "unread_suffix": expression.strip(), "expression": expression.strip(),
                            "inherits_from": prev["label"], "candidate_paths": candidates},
            "inherited": {"kind": kind, "from": prev["label"]}, "designators": [], "_end": end, "_unsettled": True, "_start": start}


RANGE_OP = re.compile(r"\s*(through|thru|to|[\-\u2010-\u2015\u2212])\s*", re.I)
RANGE_WORDS = ("through", "thru")
# A bare number after `to` ends a part range only when it is the end of the clause (`Part 3 to 6`, `Part 3 to 6 and ...`):
# `to 2026`, `to 6 percent` and `to 6 banks` are prose, quantities or years.
TO_TAIL = re.compile(r"\s*(?:$|[.,;:)]|(?:and|or)\b|\u00a7|\d+\s*CFR\b)", re.I)
def _bare_part_number(number):
    """Whether a bare number (no `Part` before it) may be a CFR part: up to three digits, or four digits below 1900
    (`1010`, `1020`). Four-digit numbers from 1900 on read as years (`to 2026`, `, 2026`) and are never operands unless written
    `Part 2026`. This is a reading of the form of the text; it does not consult which parts are acquired."""
    return len(number) <= 3 or (len(number) == 4 and 1000 <= int(number) < 1900)


def _part_or_year(number):
    """Four-digit numbers 1900-1999 can be a part (`Part 1950`) or a year (`1999`): a reading the text does not settle."""
    return len(number) == 4 and 1900 <= int(number) < 2000


PART_ENDPOINT = re.compile(r"(?:(Parts?)\s+)?(\d{1,4})(?![A-Za-z0-9]|\.\d)(?!\s*(?:%|percent\b))", re.I)


def _read_range(query, cursor, prev):
    """A range written after a section or paragraph citation (`3.1-6.1`, `3.22(a) through (z)`, `to`, en dash), or None.

    Ranges are not supported: the interior is never inferred and an endpoint is never accepted as the whole request.
    The result is one structured unresolved expression whose `requested` is the complete expression (both endpoints), with
    the endpoints and any reversal or cross-part reading recorded in `diagnostics.range`. A `through` with nothing readable
    after it is a range with a missing endpoint; `to` and the dashes need a readable endpoint, so ordinary prose
    (`3.1 to the extent...`, `3.22 - a rule`) is not a range.
    """
    op = RANGE_OP.match(query, cursor)
    part_level = bool(prev.get("part") and prev.get("title") and not prev.get("minor"))  # `12 CFR Part 3`
    if not op or not (prev.get("section") or part_level):
        return None
    word = op.group(1).lower()
    pos = op.end()
    end_info = None
    h = CITE_HEAD.match(query, pos)
    if h and (h.group(1) or query[pos] == "\u00a7"):
        title, part, minor = h.groups()
        designators, p, suffix = _read_designators(query, h.end()) if minor else ([], h.end(), None)
        end_pos = suffix[2] if suffix else p
        end_info = {"title": title or prev["title"], "part": part, "minor": minor, "designators": designators, "form": "fully_qualified"}
    else:
        m = SECTION_MEMBER.match(query, pos)
        if m:
            designators, p, suffix = _read_designators(query, m.end())
            end_pos = suffix[2] if suffix else p
            end_info = {"title": prev["title"], "part": m.group(1), "minor": m.group(2), "designators": designators, "form": "bare_section"}
        elif query.startswith("(", pos) and BARE_DESIGNATOR.match(query, pos) and prev.get("section"):
            c = BARE_DESIGNATOR.match(query, pos)
            designators, p, suffix = _read_designators(query, c.end())
            end_pos = suffix[2] if suffix else p
            end_info = {"title": prev["title"], "part": prev["part"], "minor": prev["minor"], "designators": [c.group(1)] + designators,
                        "form": "bare_paragraph"}
        elif PART_ENDPOINT.match(query, pos) and (PART_ENDPOINT.match(query, pos).group(1) or part_level and (
                word != "to" or ((_bare_part_number(PART_ENDPOINT.match(query, pos).group(2)) or _part_or_year(PART_ENDPOINT.match(query, pos).group(2)))
                                 and TO_TAIL.match(query, PART_ENDPOINT.match(query, pos).end())))):
            pe = PART_ENDPOINT.match(query, pos)  # `Part 6`, or (after a part and not for `to`) a bare `6`
            end_pos = pe.end()
            end_info = {"title": prev["title"], "part": pe.group(2), "minor": None, "designators": [],
                        "form": "ambiguous_part_or_year" if (not pe.group(1) and _part_or_year(pe.group(2))) else "bare_part"}
        elif query.startswith("(", pos) and _malformed_operand(query, pos) is not None:
            end_pos = _malformed_operand(query, pos)  # the endpoint is malformed: the whole expression stays one unresolved range
            end_info = {"form": "malformed_endpoint"}
        elif part_level and BARE_DESIGNATOR.match(query, pos):  # a paragraph designator cannot end a range that starts at a part
            _, end_pos, suffix = _read_designators(query, pos)
            end_pos = suffix[2] if suffix else end_pos
            end_info = {"form": "malformed_endpoint"}
    if end_info is None:
        if word in RANGE_WORDS and not re.match(r"[A-Za-z0-9\u00a7(]", query[pos:pos + 1] or " "):
            end_pos = op.start() + len(op.group(0).rstrip())  # `through` at the end of the request or before punctuation
            end_info = {"form": "missing_endpoint"}
        else:
            return None
    start = prev["_start"]
    requested = query[start:end_pos]
    first = prev["canonical"]
    last = None
    if end_info.get("part"):
        paras = "".join(f"({d})" for d in end_info["designators"])
        if end_info["form"] == "bare_paragraph":
            base = expand_shorthand(prev["designators"], end_info["designators"][0]) if prev.get("designators") else None
            last = (f"{prev['title']} CFR " if prev["title"] else "\u00a7 ") + f"{prev['section']}" + "".join(f"({d})" for d in base + end_info["designators"][1:]) if base else None
        else:
            sec = f"{end_info['part']}.{end_info['minor']}" if end_info["minor"] else None
            last = (f"{end_info['title']} CFR " if end_info["title"] else "\u00a7 ") + (f"{sec}{paras}" if sec else f"Part {end_info['part']}")
    reversed_ = cross_part = None
    if end_info.get("part") and end_info["form"] in ("fully_qualified", "bare_part", "bare_section", "ambiguous_part_or_year") and (part_level or not end_info["minor"]) and prev.get("part"):
        sp, ep = int(prev["part"]), int(end_info["part"])  # a part-level range: compare parts (titles only when both are written)
        cross_part = sp != ep or bool(end_info["title"] and prev["title"] and end_info["title"] != prev["title"])
        reversed_ = ep < sp if not (end_info["title"] and prev["title"] and end_info["title"] != prev["title"]) else None
    elif end_info.get("form") in ("fully_qualified", "bare_section") and end_info.get("minor") and prev["minor"]:
        cross_part = end_info["part"] != prev["part"]
        a, b = re.match(r"\d+", prev["minor"]), re.match(r"\d+", end_info["minor"])
        if not cross_part and a and b:
            reversed_ = int(b.group(0)) < int(a.group(0))
        elif cross_part:
            reversed_ = int(end_info["part"]) < int(prev["part"])
        if end_info["title"] and prev["title"] and end_info["title"] != prev["title"]:
            cross_part = True
    return {"title": prev["title"], "part": prev["part"], "minor": prev["minor"], "section": prev["section"], "paras": "",
            "canonical": None, "requested": requested, "label": " ".join(requested.split()), "malformed": "unsupported_range",
            "diagnostics": {"requested": " ".join(requested.split()), "accepted_valid_prefix": False, "valid_prefix": first,
                            "unread_suffix": query[prev["_end"]:end_pos].strip(), "interior_inferred": False,
                            "range": {"operator": word if word.isalpha() else "dash", "start": first, "end": last,
                                      "end_requested": query[pos:end_pos].strip() or None, "end_form": end_info["form"],
                                      "reversed": reversed_, "cross_part_or_title": cross_part}},
            "inherited": prev.get("inherited"), "designators": [], "_end": end_pos, "_start": start, "_unsettled": True}


def _malformed_operand(query, pos):
    """End of a parenthesized operand at `pos` that is not a complete designator but must not vanish, or None.

    Unread operands are an unclosed designator-shaped token (`(z`), a punctuation-led or symbol-only one (`(-1)`, `(.)`,
    `(*`) and a bare `(` that ends the request. Ordinary explanatory parentheticals (`(introductory text)`, `(see below)`)
    are not operands.
    """
    if BARE_DESIGNATOR.match(query, pos):
        return None  # a complete designator is not a malformed operand
    o = BARE_OPENER.match(query, pos)
    if o:
        if not designator_classes(o.group(1)):
            return None
        found = paren_suffix(query, pos)
        return found[2] if found and found[0] == "incomplete" else o.end()
    m = re.compile(r"\(\s*([^\s()]*)").match(query, pos)
    if not m:
        return None
    run = m.group(1)
    if not run:
        return m.end() if not query[m.end():].strip() else None
    if re.search(r"[A-Za-z]", run):
        return None
    close = re.compile(r"\s*\)").match(query, m.end())
    return close.end() if close else m.end()


def _read_member(query, pos, prev):
    """The list member that follows a separator, or None when what follows is not a citation member.

    A bare section (`6.1`) inherits the title of the citation it follows. A bare paragraph designator (`(z)`) inherits
    the section and, through the CFR hierarchy, the level of the previous member's paragraph path; it is settled only
    when the hierarchy puts it in one place. Anything else is an unresolved expression with its candidates.
    """
    m = SECTION_MEMBER.match(query, pos)
    if m:
        designators, p, suffix = _read_designators(query, m.end())
        rec = _citation_record(prev["title"], m.group(1), m.group(2), designators, p, suffix, query, pos,
                               {"kind": "section", "from": prev["label"], "title_inherited": prev["title"]})
        if rec["malformed"] and prev["title"]:
            rec["label"] = f"{prev['title']} CFR {rec['label']}"
        return rec
    pm = PART_ENDPOINT.match(query, pos)
    if pm and not query.startswith("(", pos):
        part_context = bool(prev.get("part") and not prev.get("minor"))  # the previous member is itself a part
        if part_context and not pm.group(1) and _part_or_year(pm.group(2)) and TO_TAIL.match(query, pm.end()):
            return _unresolved_expression("ambiguous_part_or_year", query[pos:pm.end()], pos, pm.end(), prev, [f"{prev['title']} CFR Part {pm.group(2)}"], kind="part")
        if pm.group(1) or (part_context and _bare_part_number(pm.group(2)) and TO_TAIL.match(query, pm.end())):
            # `Part 6` after any citation, or a bare `6` at the end of a clause after a part (`Part 3 and 6`, `Parts 3, 6, and 217`)
            if not prev.get("title"):
                return _unresolved_expression("part_without_title_context", query[pos:pm.end()], pos, pm.end(), prev, [], kind="part")
            return _citation_record(prev["title"], pm.group(2), None, [], pm.end(), None, query, pos,
                                    {"kind": "part", "from": prev["label"], "title_inherited": prev["title"]})
    if not query.startswith("(", pos):
        return None
    c = BARE_DESIGNATOR.match(query, pos)
    if not c:
        end = _malformed_operand(query, pos)  # `(z`, `(-1)`, `(`: kept as an unresolved member; prose parentheticals are not
        if end is None:
            return None
        return _unresolved_expression("unclosed_or_malformed_designator_suffix", query[pos:end], pos, end, prev, [])
    first = c.group(1)
    designators, p, suffix = _read_designators(query, c.end())
    end = suffix[2] if suffix else p
    if prev.get("_unsettled"):  # inherits from something that was itself not settled
        return _unresolved_expression("ambiguous_shorthand", query[pos:end], pos, end, prev, [])
    if not prev.get("section") or not prev.get("designators"):
        return _unresolved_expression("shorthand_without_paragraph_context", query[pos:end], pos, end, prev, [])
    base = expand_shorthand(prev["designators"], first)
    if base is None:
        classes = designator_classes(first)
        depth = len(prev["designators"])
        cands = []
        if DEPTH_CLASS.get(depth, "digit") in classes:
            cands.append(prev["designators"][:-1] + [first])
        if "letter" in classes:
            cands.append([first])
        return _unresolved_expression("ambiguous_shorthand", query[pos:end], pos, end, prev,
                                      [f"{prev['section']}" + "".join(f"({d})" for d in x) for x in cands])
    rec = _citation_record(prev["title"], prev["part"], prev["minor"], base + designators, p, suffix, query, pos,
                           {"kind": "paragraph", "from": prev["label"], "inherited_path": "".join(f"({d})" for d in base)})
    return rec


def parse_query_citations(query):
    """Every CFR citation in a query, read once, the same way for every consumer.

    Whitespace inside a complete designator (`( 4 )`, tabs, newlines) is normalized for lookup (`paras` is the
    normalized path, `canonical` the citation built from it) while `requested` keeps the text exactly as asked. A path
    followed by an unread designator-like fragment is never accepted as its own ancestor: an unclosed or malformed
    designator (`3.22(a)(999`) or one set apart by whitespace (`3.1(c)(4) (i)`, `3.22 (a)`) makes the request
    `malformed`, to be reported as an unresolved citation with diagnostics. An ordinary trailing parenthetical
    (`(introductory text)`) does not.

    List members after a comma, semicolon, `and` or `or` are citations too. A bare section (`12 CFR 3.1, 6.1`) inherits
    the title of the citation before it; a bare paragraph designator (`12 CFR 3.22(a) and (z)`) inherits the section
    and, by the CFR hierarchy, the paragraph level of the previous member. When the hierarchy does not settle it
    (`3.1(c)(4)(i) and (v)`), or there is no paragraph to inherit from, the member is a structured unresolved
    expression (`ambiguous_shorthand`, `shorthand_without_paragraph_context`), never dropped and never merged into
    the citation before it. Members carry `inherited`; their `requested` is the member text alone.
    """
    out, consumed = [], 0
    for m in CITE_HEAD.finditer(query):
        if m.start() < consumed:
            continue  # inside a range already read as one expression
        title, part, minor = m.groups()
        if not minor and not title:
            continue  # a bare '\u00a7 12' is not a citation
        designators, pos, suffix = _read_designators(query, m.end()) if minor else ([], m.end(), None)
        rec = _citation_record(title, part, minor, designators, pos, suffix, query, m.start())
        out.append(rec)
        consumed = rec["_end"]
        if rec["malformed"]:
            continue
        rng = _read_range(query, rec["_end"], rec)
        if rng:
            out[-1] = rec = rng
            consumed = rng["_end"]
        prev, cursor = rec, rec["_end"]
        while True:
            sep = LIST_SEP.match(query, cursor)
            if not sep:
                break
            mem = _read_member(query, sep.end(), prev)
            if mem is None:
                break
            out.append(mem)
            cursor = mem["_end"]
            if mem["malformed"] and not mem.get("_unsettled"):
                break  # an unclosed/malformed tail: nothing after it is read as a member
            if not mem["malformed"]:
                rng = _read_range(query, cursor, mem)
                if rng:
                    out[-1] = mem = rng
                    cursor = rng["_end"]
            consumed = cursor
            prev = mem
    return out


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
    "unclosed_or_malformed_designator_suffix": "The request ends in an unclosed or malformed designator, so its parent path "
                                               "was not accepted and no record is returned for the cited paragraph.",
    "detached_designator_suffix": "A designator set apart by whitespace follows the citation, which makes the requested path "
                                  "ambiguous; no ancestor path was accepted and no record is returned for it.",
    "paragraph_precision_unresolved": "Only section-level amendment evidence was found; no amendatory instruction names the "
                                      "complete requested paragraph path, so its hierarchy is not established "
                                      "(section-level records are returned, flagged).",
    "ambiguous_title": "The citation names no CFR title and acquired evidence for this section exists under more than one title; "
                       "the titles' evidence is not merged. Name the title (for example `12 CFR 3.22`) to select one.",
    "unsupported_range": "The request names a range of sections or paragraphs. Ranges are not supported: the interior is not inferred "
                         "and neither endpoint is accepted as the whole request, so nothing is returned for the expression. "
                         "Request each citation separately.",
    "ambiguous_part_or_year": "A four-digit number from 1900 to 1999 follows a part and could be a part number or a year; the text does "
                              "not settle it, so it is reported as an unresolved expression and neither reading is chosen.",
    "part_without_title_context": "A part is listed after a citation that names no CFR title, so no title can be inherited for it; "
                                  "it is reported as an unresolved expression. Name the title (for example `12 CFR Part 6`).",
    "ambiguous_shorthand": "A bare paragraph designator follows a citation but the CFR hierarchy does not settle which paragraph it "
                           "names; it is reported as an unresolved expression and is not guessed or merged into the citation before it.",
    "shorthand_without_paragraph_context": "A bare paragraph designator follows a citation that names no paragraph to inherit from; "
                                           "it is reported as an unresolved expression.",
    "excluded_by_part_filter": "A record exists, but the requested part filter excludes it.",
    "excluded_by_requested_scope": "A record exists in the other scope, but not in the requested scope.",
    "excluded_by_scope_and_part_filter": "A record exists, but only in the other scope and outside the requested part filter.",
    "part_not_acquired": "No record was acquired for this part, in baseline or in an acquired amendment.",
    "paragraph_not_found_in_acquired_section": "The section is acquired but the requested paragraph is not in it.",
    "paragraph_not_found_in_acquired_range": "The section lies inside an acquired eCFR range section (typically reserved) "
                                             "that has no such paragraph; the paragraph is not resolved through the range.",
    "not_found_in_acquired_part": "The part is acquired but no record for this section was found.",
}
CITATION_WORDS = {"section", "sections", "cfr", "part", "parts", "title"}


def citation_only(query):
    """True when the query is nothing but CFR citations (e.g. `12 CFR 3.22`, `§ 217.12`) and filler words."""
    cites = parse_query_citations(query)
    if not cites:
        return False
    rest = query
    for c in sorted(cites, key=lambda c: -len(c["requested"])):
        rest = rest.replace(c["requested"], " ")  # drop each cited span, including any unread malformed fragment
    return not [w for w in terms_of(rest) if w not in CITATION_WORDS]


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

    def title_resolution(self, c):
        """The CFR title a citation refers to, decided once for resolver and delta from acquired evidence.

        An explicit title is used as written. A titleless section citation (`§ 3.22`) takes the one title that has
        evidence for that section: an acquired eCFR section or numeric range containing it, an `amends` or
        `amends_candidate` target, or a numeric amendment range. If no title has section evidence but exactly one
        title has evidence for the part, that title is used (the section is then simply absent from it). More than one
        title is `ambiguous`: the candidates are listed and their evidence is never merged. No evidence gives
        `unidentified`, which behaves as the titleless lookup always did (nothing found).
        """
        if c["title"]:
            return {"status": "explicit", "title": c["title"], "candidates": []}
        part, section, minor = c["part"], c["section"], c["minor"]
        if not section:
            return {"status": "unidentified", "title": None, "candidates": []}
        titles = {r[0] for r in self.conn.execute("SELECT DISTINCT title_no FROM provision WHERE part_no=? AND title_no IS NOT NULL", (part,))}
        for tab, col in (("amendment_range", "title_no"),):
            titles |= {r[0] for r in self.conn.execute(f"SELECT DISTINCT {col} FROM {tab} WHERE part_no=? AND {col} IS NOT NULL", (part,))}
        for (dst,) in self.conn.execute("SELECT DISTINCT dst_citation FROM relationship WHERE rel_type IN ('amends','amends_candidate') "
                                        "AND (dst_citation LIKE ? OR dst_citation LIKE ?)", (f"% CFR {part}.%", f"% CFR Part {part}")):
            head = dst.split(" CFR ", 1)[0]
            if head.isdigit():
                titles.add(head)
        evidence = {}
        for t in sorted(titles):
            n_base = len(self.current_baseline(t, part, section, None)["rows"])
            n_amend = self.conn.execute("SELECT COUNT(DISTINCT src_provision_id) FROM relationship WHERE rel_type='amends' AND dst_citation=?",
                                        (f"{t} CFR {section}",)).fetchone()[0]
            n_cand = self.conn.execute("SELECT COUNT(DISTINCT src_provision_id) FROM relationship WHERE rel_type='amends_candidate' AND dst_citation=?",
                                       (f"{t} CFR {section}",)).fetchone()[0]
            n_range = len(self.range_hits(t, part, minor))
            evidence[t] = {"title": t, "citation": f"{t} CFR {section}", "baseline_records": n_base, "future_amendment_units": n_amend,
                           "unresolved_candidate_units": n_cand, "numeric_range_units": n_range}
        with_section = [t for t, e in evidence.items() if e["baseline_records"] or e["future_amendment_units"] or e["unresolved_candidate_units"] or e["numeric_range_units"]]
        if len(with_section) == 1:
            return {"status": "unique_from_acquired_evidence", "title": with_section[0], "basis": "section evidence",
                    "candidates": [evidence[with_section[0]]]}
        if len(with_section) > 1:
            return {"status": "ambiguous", "title": None, "candidates": [evidence[t] for t in with_section]}
        if len(titles) == 1:
            (t,) = titles
            return {"status": "unique_from_acquired_evidence", "title": t, "basis": "part evidence only; the section has no acquired evidence under it",
                    "candidates": [evidence[t]]}
        if len(titles) > 1:
            return {"status": "ambiguous", "title": None, "candidates": [evidence[t] for t in sorted(titles)]}
        return {"status": "unidentified", "title": None, "candidates": []}

    def _ambiguous_record(self, c, resolution):
        precision = "paragraph" if c["paras"] else "section"
        return {"citation": c["canonical"], "requested": c["requested"], "canonical": c["canonical"], "requested_precision": precision,
                "resolved": False, "achieved": None, "baseline_match": None, "by_scope": {"current": 0, "future": 0},
                "legal_validity": "not_determined", "future_paragraph_support": [], "reason": "ambiguous_title",
                "title_resolution": resolution, "candidate_titles": [x["title"] for x in resolution["candidates"]],
                "note": CITATION_REASONS["ambiguous_title"] + " This reports unavailable evidence only; it is not a determination "
                                                              "that the citation is invalid or does not exist.",
                "_current_ids": [], "_future_ids": []}

    def resolve_citations(self, query, scope="both", parts=None):
        """One resolution record per distinct citation in the query, in the requested scope and part filter.

        `resolved` is true only at the requested precision. A paragraph-qualified citation that exists only as a
        section-level amendment is `resolved: False, achieved: section_only` and keeps its records discoverable.
        Unresolved records carry a `reason` and always state that an unavailable record is not a determination
        that the citation is legally invalid or nonexistent.
        """
        out, seen = [], set()
        for c in parse_query_citations(query):
            if c["label"] in seen:
                continue
            seen.add(c["label"])
            if c["malformed"]:
                out.append(self._malformed_record(c, scope, parts))
                continue
            resolution = self.title_resolution(c)
            if resolution["status"] == "ambiguous":
                out.append(self._ambiguous_record(c, resolution))
                continue
            record = self._resolve_one(c["canonical"], resolution["title"], c["part"], c["section"], c["paras"], scope, parts)
            record["requested"] = c["requested"]
            record["canonical"] = c["canonical"]
            record["title_resolution"] = resolution
            record["inherited"] = c["inherited"]
            out.append(record)
        return out

    def _malformed_record(self, c, scope, parts):
        """An explicit unresolved citation: its parent path is not accepted and nothing is returned for it.

        The section the valid prefix belongs to is reported only as a count of broader context records.
        """
        reason = c["malformed"]
        resolution = self.title_resolution(c)
        title = resolution["title"]
        counted = resolution["status"] != "ambiguous"  # an ambiguous title is never counted across titles
        cur = self._exact_current(title, c["part"], c["section"], "", parts) if counted and scope in ("current", "both") else []
        fut = self._exact_future(title, c["part"], c["section"], parts, c["minor"]) if counted and scope in ("future", "both") else []
        return {"citation": c["label"], "requested": c["requested"], "canonical": None,
                "requested_precision": "paragraph" if c["paras"] else "section", "resolved": False, "achieved": None,
                "baseline_match": None, "by_scope": {"current": 0, "future": 0}, "legal_validity": "not_determined",
                "future_paragraph_support": [], "reason": reason, "diagnostics": c["diagnostics"],
                "context_available": {"role": "section_context_not_the_requested_citation",
                                      "section": f"{c['title'] + ' CFR ' if c['title'] else ''}{c['section']}",
                                      "current_records": len(cur), "future_records": len(fut)},
                "note": CITATION_REASONS[reason] + " This reports unavailable evidence only; it is not a determination "
                                                   "that the citation is invalid or does not exist.",
                "_current_ids": [], "_future_ids": []}

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
        match = None
        if section and scope in ("current", "both"):
            cb = self.current_baseline(title, part, section, parts)
            match = {"via": cb["via"] if (cb["via"] == "exact_section" or not paras) else None, "range": cb["range"] if not paras else None}
        record = {"citation": label, "requested_precision": precision, "resolved": resolved, "achieved": achieved,
                  "baseline_match": match,
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
        if paras and "current" in requested and section:
            cb = self.current_baseline(title, part, section, parts)
            if cb["via"]:
                return ("paragraph_not_found_in_acquired_range" if cb["via"] == "range_section"
                        else "paragraph_not_found_in_acquired_section")
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
        if paras and section:
            via = self.current_baseline(title, part, section, None)["via"]
            if via:
                return "paragraph_not_found_in_acquired_range" if via == "range_section" else "paragraph_not_found_in_acquired_section"
        return "not_found_in_acquired_part"

    RANGE_SECTION = re.compile(r"^(\d+)\.(\d+)-(\d+)\.(\d+)$")
    BASELINE_COLUMNS = ("p.*, d.doc_key, d.sha256, d.publisher_issue_date, d.publisher_up_to_date_as_of")

    def current_baseline(self, title, part, section, parts=None):
        """The one current-baseline lookup, shared by resolve_citations / exact / answer and delta.

        Returns {"rows", "via", "range"}. `via` is "exact_section" when the acquired eCFR has the section itself
        (always preferred), or "range_section" when the numeric section lies inside an acquired range section such
        as `3.4-3.9`; otherwise None. Range membership needs the same title and part, a plain numeric minor, ordered
        numeric endpoints within one part, and the requested part filter. Lettered minors never match a range, and
        rows are the publisher's own records (original locator, citation, hash, dates). A range section is acquired
        evidence of that range, usually "[Reserved]", not an operative obligation for the requested section.
        """
        clause, params = self._parts_clause(parts)
        tclause, tparams = (" AND p.title_no=?", [title]) if title else ("", [])
        base = (f"SELECT {self.BASELINE_COLUMNS} FROM provision p JOIN source_document d ON d.id=p.doc_id "
                f"WHERE d.publisher='eCFR'{clause}{tclause}")
        rows = self.conn.execute(
            base + " AND p.section_no=? AND p.kind IN ('section','paragraph','definition','table') ORDER BY p.ordinal",
            params + tparams + [section]).fetchall()
        if rows:
            return {"rows": rows, "via": "exact_section", "range": None}
        minor = section.split(".", 1)[1]
        if not minor.isdigit():  # lettered sections are never inferred to belong to a range
            return {"rows": [], "via": None, "range": None}
        for r in self.conn.execute(base + " AND p.part_no=? AND p.kind='section' AND p.section_no LIKE '%-%' ORDER BY p.ordinal",
                                   params + tparams + [part]).fetchall():
            span = self.RANGE_SECTION.match(r["section_no"] or "")
            if not span or not (span.group(1) == span.group(3) == part == r["part_no"]):
                continue  # not a numeric same-part range
            lo, hi = int(span.group(2)), int(span.group(4))
            if lo <= hi and lo <= int(minor) <= hi:
                return {"rows": [r], "via": "range_section",
                        "range": {"requested_section": section, "range_section": r["section_no"], "range_locator": r["locator"],
                                  "range_citation": r["citation"], "lo": f"{part}.{lo}", "hi": f"{part}.{hi}",
                                  "note": "The requested section lies inside an acquired eCFR range section (typically "
                                          "[Reserved]). That text is acquired evidence of the range, not an operative "
                                          "obligation for the requested section."}}
        return {"rows": [], "via": None, "range": None}

    def _exact_current(self, title, part, section, paras, parts):
        if section:
            if paras:
                # A paragraph is never resolved through its section or range ancestor: only an acquired exact section
                # can contain it, and only a locator match (or a descendant) counts.
                rows = self.current_baseline(title, part, section, parts)
                wanted = f"{section}{paras}"
                found = rows["rows"] if rows["via"] == "exact_section" else []
                return [r["id"] for r in found if r["locator"] == wanted or r["locator"].startswith(wanted + "(")]
            return [r["id"] for r in self.current_baseline(title, part, section, parts)["rows"]]
        # Part citation: the part record then its sections, from eCFR documents only (the generic current filter
        # excludes kind 'part'; the publisher test keeps Federal Register rows out).
        clause, params = self._parts_clause(parts)
        cond, cparams = "p.part_no=? AND p.kind IN ('part','section')", [part]
        if title:
            cond += " AND p.title_no=?"
            cparams.append(title)
        rows = self.conn.execute(
            f"""SELECT p.id FROM provision p JOIN source_document d ON d.id=p.doc_id
                WHERE d.publisher = 'eCFR'{clause} AND {cond} ORDER BY p.ordinal""", params + cparams).fetchall()
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
        ranked = sorted(fused.items(), key=lambda x: -x[1])
        # Pin the first record of every resolved citation ahead of the fused ranking, in citation order, so a lexical
        # hit from elsewhere can never crowd a requested citation's own evidence out of a small k (for example a
        # numeric section whose only baseline record is its reserved range section).
        pins = []
        for c in self.resolve_citations(query, scope, parts):
            lst = [pid for pair in zip_longest(c["_current_ids"], c["_future_ids"]) for pid in pair if pid is not None]
            if lst and lst[0] not in pins:
                pins.append(lst[0])
        pinned = [(pid, fused.get(pid, 0.0) + 1.0) for pid in pins]
        return (pinned + [x for x in ranked if x[0] not in set(pins)])[:k]

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
                out["carried_text_origin"] = row["carried_text_origin"]
                out["pairing_status"] = row["pairing_status"]
                if row["appendix"]:
                    out["appendix"] = json.loads(row["appendix"])
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
        citations = self.resolve_citations(query, scope, parts)
        for c in citations:  # identify range membership on the publisher's own record
            rng = (c.get("baseline_match") or {}).get("range")
            if rng:
                for ev in results:
                    if ev["provision_id"] in c["_current_ids"]:
                        ev["range_membership"] = rng
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
    def _malformed_delta(self, citation, parsed):
        """delta() for a request whose designator suffix is unclosed, malformed or detached: an explicit unresolved result.

        No baseline, instrument or paragraph precision is asserted for it, and the valid prefix is not accepted as its
        own ancestor. The section it sits in is reported only as a count of context records, and no legal validity is
        determined.
        """
        record = self._malformed_record(parsed, "both", None)
        unresolved = {"scope": None, "requested": record["requested_precision"], "resolved": False, "achieved": None,
                      "reason": record["reason"], "note": record["note"]}
        return {"citation": citation, "requested_citation": parsed["requested"], "canonical_citation": None,
                "as_of": self.as_of, "requested_precision": record["requested_precision"], "requested_path": None,
                "baseline": None, "section_context": None, "instruments": [],
                "current_precision": {**unresolved, "scope": "current"}, "future_precision": {**unresolved, "scope": "future"},
                "evidence_status": "unavailable",
                "unavailable": {"reason": record["reason"], "legal_validity": "not_determined", "note": record["note"],
                                "diagnostics": record["diagnostics"], "context_available": record["context_available"]},
                "legal_validity": "not_determined", "unresolved_candidates": [], "applicability_status": "not_assessed",
                "notes": ["The request has an unread designator suffix, so no ancestor path was accepted and no baseline or "
                          "instrument is reported for it. Ask for the complete citation."]}

    def _ambiguous_delta(self, citation, parsed, resolution):
        """delta() for a titleless citation whose section has evidence under more than one title: explicit ambiguity.

        Nothing is merged across titles: no baseline, no instruments. Each candidate title is listed with the size of
        its own evidence so the caller can ask again with the title.
        """
        record = self._ambiguous_record(parsed, resolution)
        unresolved = {"scope": None, "requested": record["requested_precision"], "resolved": False, "achieved": None,
                      "reason": "ambiguous_title", "note": record["note"]}
        return {"citation": citation, "requested_citation": parsed["requested"], "canonical_citation": parsed["canonical"],
                "as_of": self.as_of, "requested_precision": record["requested_precision"], "requested_path": parsed["paras"] or None,
                "baseline": None, "section_context": None, "instruments": [],
                "current_precision": {**unresolved, "scope": "current"}, "future_precision": {**unresolved, "scope": "future"},
                "evidence_status": "ambiguous_title",
                "unavailable": {"reason": "ambiguous_title", "legal_validity": "not_determined", "note": record["note"],
                                "candidate_titles": record["candidate_titles"]},
                "title_resolution": resolution, "legal_validity": "not_determined", "unresolved_candidates": [],
                "applicability_status": "not_assessed",
                "notes": ["The citation names no CFR title and more than one title has acquired evidence for this section; evidence "
                          "from different titles is never merged. Repeat the request with an explicit title, e.g. "
                          + " or ".join(x["citation"] for x in resolution["candidates"]) + "."]}

    def _unsupported_multi_delta(self, citation, cites):
        """delta() compares one citation; a request naming several (a list, or shorthand members) is reported, not truncated."""
        seen, members = set(), []
        for c in cites:
            if c["label"] in seen:
                continue
            seen.add(c["label"])
            members.append({"requested": c["requested"], "label": c["label"], "canonical": c["canonical"], "inherited": c.get("inherited"),
                            "unresolved_reason": c["malformed"]})
        unresolved = {"scope": None, "requested": None, "resolved": False, "achieved": None, "reason": "multiple_citations_not_supported",
                      "note": "delta() supports one citation per request."}
        return {"citation": citation, "requested_citation": citation, "canonical_citation": None, "as_of": self.as_of,
                "requested_precision": None, "requested_path": None, "baseline": None, "section_context": None, "instruments": [],
                "current_precision": {**unresolved, "scope": "current"}, "future_precision": {**unresolved, "scope": "future"},
                "evidence_status": "unsupported_multi_citation",
                "unavailable": {"reason": "multiple_citations_not_supported", "legal_validity": "not_determined",
                                "note": "delta() compares one citation at a time and does not select the first of several. Request "
                                        "each member separately.", "citations": members},
                "legal_validity": "not_determined", "unresolved_candidates": [], "applicability_status": "not_assessed",
                "notes": ["The request names %d distinct citations; none was selected. Ask for each one: %s." % (
                    len(members), "; ".join(m["canonical"] or m["label"] for m in members))]}

    def delta(self, citation):
        """Current baseline versus each acquired future instrument for one section or part.

        Instruments are listed independently and are never merged: competing or cumulative proposals do not form
        one future rulebook, and a final rule whose effective date predates the baseline issue date may already
        be reflected in the baseline text.
        """
        cites = parse_query_citations(citation)
        if not cites:
            raise ValueError(f"cannot parse citation: {citation}")
        if len({c["label"] for c in cites}) > 1:
            return self._unsupported_multi_delta(citation, cites)  # never select the first member silently
        parsed = cites[0]
        if parsed["malformed"]:
            return self._malformed_delta(citation, parsed)
        resolution = self.title_resolution(parsed)  # the same title decision the citation resolver makes
        if resolution["status"] == "ambiguous":
            return self._ambiguous_delta(citation, parsed, resolution)
        title, part, minor, paras = resolution["title"], parsed["part"], parsed["minor"], parsed["paras"]
        section = f"{part}.{minor}" if minor else None
        wanted = paras  # normalized requested paragraph path, e.g. "(c)(1)"; whitespace inside designators already closed
        match_via, range_info = None, None
        if section:
            cb = self.current_baseline(title, part, section, None)  # the same lookup resolve_citations uses
            base_rows, match_via, range_info = cb["rows"], cb["via"], cb["range"]
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
        def summarize(rows, **extra):
            b = rows[0]
            base = {"doc_key": b["doc_key"], "sha256": b["sha256"], "issue_date": b["publisher_issue_date"],
                    "up_to_date_as_of": b["publisher_up_to_date_as_of"], "provisions_in_scope": len(rows),
                    "heading": b["heading"], "locator": b["locator"], "citation": b["citation"]}
            if match_via:
                base["match"] = match_via
                base["range_membership"] = range_info
            return {**base, **extra}

        baseline, section_context = None, None
        if base_rows and section and wanted:
            # A paragraph is in the baseline only if it (or something beneath it) is; it never resolves via its parent.
            target = f"{section}{wanted}"
            para_rows = [r for r in base_rows if r["locator"] == target or r["locator"].startswith(target + "(")]
            if para_rows:
                baseline = summarize(para_rows, precision="paragraph", requested_path=wanted)
            else:
                section_context = summarize(base_rows, role="section_context_not_the_requested_paragraph",
                                            requested_path=wanted)
        elif base_rows:
            baseline = summarize(base_rows)
        reference = baseline or section_context  # dates for baseline_relation come from whichever baseline text exists
        grouped = {}
        ids_by_doc = {}
        for pid in amend:
            ev = self.evidence(pid, with_context=False)
            ids_by_doc.setdefault(ev["doc_key"], []).append(pid)
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
            if reference and g["document_type"] == "Rule" and rm["effective_on"]:
                g["baseline_relation"] = ("effective_on_or_before_baseline_issue_date_baseline_may_already_reflect"
                                          if rm["effective_on"] <= reference["issue_date"]
                                          else "effective_after_baseline_issue_date_not_in_baseline")
            elif g["document_type"] == "Proposed Rule":
                g["baseline_relation"] = "proposal_not_in_baseline"
            else:
                g["baseline_relation"] = "unknown"
            links = self.conn.execute("SELECT * FROM rulemaking_link WHERE a_doc_key=? OR b_doc_key=?",
                                      (g["doc_key"], g["doc_key"])).fetchall()
            g["candidate_links"] = [{"other": r["b_doc_key"] if r["a_doc_key"] == g["doc_key"] else r["a_doc_key"],
                                     "rel": r["rel_type"], "basis": r["basis"], "review": r["review_status"]} for r in links]
        # Paragraph precision is decided per instrument from that instrument's own units only; one instrument's
        # explicitly attributed path never establishes another's. Instruments without it stay as section context.
        if section and wanted:
            for g in instruments:
                support = self._paragraph_support(ids_by_doc[g["doc_key"]], wanted)
                supported = {loc for _, loc in support}
                g["paragraph_precision"] = {
                    "requested_path": wanted, "resolved": bool(support),
                    "achieved": "paragraph" if support else "section_only",
                    "supported_by": sorted(supported),
                    "reason": None if support else "paragraph_precision_unresolved",
                    "evidence": "explicit_paragraphs: complete path named by an attributed amendatory instruction "
                                "(unreviewed text pattern)"}
                g["role"] = "paragraph_supported" if support else "section_context"
                for u in g["units"]:
                    u["paragraph_supported"] = u["locator"] in supported
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
                   WHERE a.status='unresolved_ambiguous' AND (? IS NULL OR a.title_no = ?)
                         AND (a.lo_section LIKE ? OR a.hi_section LIKE ? OR a.lo_section = ? OR a.hi_section = ?)""",
                (title, title, f"{part}.%", f"{part}.%", section or "", section or "")):  # only the resolved title's candidates
            if not section or section in (r["lo_section"], r["hi_section"]):
                candidates.append({"doc_key": r["doc_key"], "locator": r["locator"], "pairing_status": "ambiguous_range",
                                   "candidate_instructions": [{"instruction": r["instruction"],
                                                               "instruction_ordinal": r["instruction_ordinal"],
                                                               "range": [r["lo_section"], r["hi_section"]],
                                                               "reason": r["basis"]}]})
        precision = "paragraph" if (section and wanted) else ("section" if section else "part")
        cur_rec = fut_rec = None
        if precision == "paragraph":  # reuse the citation resolver, once per scope, so precision is reported separately
            cur_rec = self.resolve_citations(citation, "current")[0]
            fut_rec = self.resolve_citations(citation, "future")[0]
        supported_docs = [g["doc_key"] for g in instruments if (g.get("paragraph_precision") or {}).get("resolved")]
        if precision == "paragraph":
            current_precision = {"scope": "current", "requested": precision, "requested_path": wanted,
                                 "resolved": baseline is not None, "achieved": "paragraph" if baseline else None,
                                 "reason": None if baseline else cur_rec["reason"],
                                 "note": "" if baseline else cur_rec["note"]}
            future_precision = {"scope": "future", "requested": precision, "requested_path": wanted,
                                "resolved": bool(supported_docs),
                                "achieved": "paragraph" if supported_docs else ("section_only" if instruments else None),
                                "supported_instruments": supported_docs,
                                "section_context_instruments": [g["doc_key"] for g in instruments if g["doc_key"] not in supported_docs],
                                "reason": None if supported_docs else (fut_rec["reason"] if not instruments
                                                                       else "paragraph_precision_unresolved"),
                                "note": "" if supported_docs else (fut_rec["note"] if fut_rec else "")}
        else:
            current_precision = {"scope": "current", "requested": precision, "resolved": baseline is not None,
                                 "achieved": precision if baseline else None}
            future_precision = {"scope": "future", "requested": precision, "resolved": bool(instruments),
                                "achieved": precision if instruments else None}
        if precision == "paragraph":
            evidence_status = ("available" if (baseline or supported_docs) else
                               "section_context_only" if (section_context or instruments) else "unavailable")
        else:
            evidence_status = "available" if (baseline or instruments) else "unavailable"
        unavailable = None
        if evidence_status == "unavailable":
            reason = self._diagnose_unresolved(title, part, section, paras or "", "both", None, [])
            unavailable = {"reason": reason, "legal_validity": "not_determined",
                           "note": CITATION_REASONS[reason] + " This reports unavailable evidence only; it is not a "
                                   "determination that the citation is invalid or does not exist."}
        return {"citation": citation, "requested_citation": parsed["requested"], "canonical_citation": parsed["canonical"],
                "as_of": self.as_of, "requested_precision": precision, "requested_path": wanted or None,
                "baseline": baseline, "section_context": section_context, "instruments": instruments,
                "current_precision": current_precision, "future_precision": future_precision,
                "evidence_status": evidence_status, "unavailable": unavailable, "legal_validity": "not_determined",
                "title_resolution": resolution,
                "unresolved_candidates": candidates, "applicability_status": "not_assessed",
                "notes": ["Instruments are independent; no cumulative future rulebook is constructed.",
                          "For a paragraph citation, section_context and instruments with role section_context are broader "
                          "section evidence only; paragraph precision is resolved separately for current and for each instrument.",
                          "Only explicit amendment targets appear as instruments; unpaired or ambiguous matches are listed under unresolved_candidates.",
                          "baseline_relation compares publisher dates only; confirm against the text before relying on it."]}
