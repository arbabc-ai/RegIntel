r"""S7-P1-83: evidence on stored paragraph paths that do not start with a letter designator (offline, read-only, stdlib only).

Governing scope: founder record 2026-11-04_s7-regintel-p1-founder-decisions_accepted.md. Evidence only (C3 F-82-1): nothing is acquired,
no parser, evaluator or expectation contract changes, and no decision is implied. Non-letter-led paths are reported, never failed.

A1. For every provision with kind='amendment' and a non-NULL instruction, each stored explicit_paragraphs path is classified by its FIRST
    designator with regintel_p1.fedreg.designator_classes: `letter` (classes == {letter}, a normal CFR top-level paragraph), `digit`
    (digit in classes), `roman` (classes == {roman}), or `other_or_ambiguous` (anything else: {letter, roman} such as (i), (v), (x);
    {upper}; no class). Totals per class count paths and units. Every unit with at least one non-letter-led path is listed with its
    doc_key, locator, section, full path list and instruction head, and units that share the same (doc_key, instruction) are grouped:
    explicit_paragraphs is a per-unit column, so a list repeated across a group is stored once per unit.
A2. For each distinct (title, section, non-letter-led path) the citation "<title> CFR <section><path>" (title from the unit's title_no)
    is run through the existing paths exactly as the task-80 probe does: resolve_citations future and current [0], answer exact future
    k=5 (status, first locator), answer exact current k=5 (status), and delta instruments' paragraph_precision. The current baseline
    ecfr:<title>:<part> is queried for a provision whose citation is "<title> CFR <section>(<first designator>)" (top level) and for the
    full path; "baseline part not in index" when the part is not acquired.
A3 (0.2, S7-P1-84). Each observation gains `definition_anchor`: the definition terms the paired instruction names for that path (the
    instruction is split into enumerated items at fedreg.ENUM_BOUNDARY boundaries carrying a fedreg._boundary_token enumerator, as the
    census does; in each item whose parsing view contains the path text, every `definition of|for “…”` term is captured, curly or straight
    quotes), `ambiguous` when more than one distinct term maps to the path, and per term the term-anchored baseline locator
    `<section>(<term>)<path>` and citation `<title> CFR <section> “<term>” <path>`, whether that locator is present in ecfr:<title>:<part>,
    and, when it is not, the literal baseline rows a LIKE query on `<section>(<term>)%` / `% “<term>” %` finds. `term_anchor_census` counts,
    per baseline part queried, the provisions whose locator matches `^\d+\.\d+\([^()]*[A-Za-z ][^()]*\)` (the task's regex, which also
    admits lettered paragraphs such as `32.2(a)`) and, separately, those whose first segment is not a designator shape (a term).
A4 (0.3, S7-P1-85, C3 F-84-1..F-84-5). Every 0.1 and 0.2 field keeps its name and meaning. (F-84-1) Each captured term is
    normalised (surrounding whitespace and trailing ".", ",", ";", ":" stripped) before the locator, citation and LIKE patterns are
    composed; the raw capture is kept as `term_raw` on its anchor row. (F-84-2) An item contains a path only as a whole token: re.escape(path)
    not immediately preceded by ")" and not immediately followed by "(", so "(4)" no longer matches inside "(4)(i)(A)";
    definition_anchor carries `containment: "token"`. Items are cut just after each boundary's punctuation, so an item carries its own
    closing ".", ";" or ":". (F-84-3) When an item that contains the path names no term, the items before it are walked back to the
    nearest one that ends with ":" (after parsing_view); if that parent names exactly one term the term is used and its anchor row has
    `inherited_from_parent: true` and `parent_item_head` (first 120 chars); direct terms have `inherited_from_parent: false`. If the
    parent names zero or several terms (or no earlier item ends with ":"), nothing is inherited and, when no term is found at all,
    `parent_status: "no_single_parent_term"`; `parent_status: "no_item_contains_path"` when no item contains the path. A term reached both
    directly and through a parent keeps its first occurrence in item order. (F-84-4) term_anchor_census adds
    `balanced_term_segment_matches`: the first segment after the section number is read with a depth counter from its "(" to the matching
    ")" (so "3.2(Adjusted allowances for credit losses (AACL))" is one segment) and designator-shaped segments are excluded; up to 5
    `balanced_examples`. regex_matches and term_segment_matches are unchanged. "%", "_" and "\" in a term are escaped in the LIKE patterns
    (ESCAPE '\'); `like_escape` lists the observed terms that contain one of them.
A5 (0.4, S7-P1-86, C3 F-85-1..F-85-5). Every 0.1-0.3 field keeps its name and meaning. View: every term capture, containment test,
    parent ":" test and redesignation test reads fedreg.parsing_view(item) (F-85-4); `term_raw` is the raw capture from that view.
    (F-85-2) TERM also reads the plural "definitions of|for" and a run of quoted terms joined by ",", "and", "or" or ", and"; each quoted
    term is one capture. Each anchor row records the `term_phrase_form` ("singular" | "plural") of the phrase that captured it, and
    definition_anchor records `term_phrase_form` over the containing items' direct captures: "singular", "plural", "mixed", or "none".
    (F-85-1) Sibling guard: the enumerator token of each item is fedreg._boundary_token of the boundary that opens it (the first item has
    none) and its class is the set fedreg.designator_classes gives. Walking back from a containing item, the walk stops with no inheritance
    at the first earlier item whose class set equals the containing item's; otherwise the nearest earlier item ending with ":" is the
    parent. Inherited rows carry `item_enumerator`, `parent_enumerator` and `parent_item_index`; each stop is listed in
    definition_anchor `sibling_blocked`, and `parent_status: "sibling_boundary"` is set when no term is found and a stop occurred. Because
    designator_classes gives "i" {letter, roman} and "ii", "iii" {roman}, an "iii." item stops at its sibling "ii." (measured, reported).
    (F-85-3) In a containing item that matches REDESIGNATION, a path occurrence inside group 1 has role "redesignation_source", inside
    group 2 "redesignation_target", elsewhere "operand" (several distinct roles in one item: "multiple"). Each anchor row has `role`
    (of the item its path was found in); definition_anchor has `path_role` over all containing items; target rows add
    `present_refers_to: "current_baseline_text"`. baseline() and term_anchored_present are unchanged.
A6 (0.5, S7-P1-87, C3 F-86-1, F-86-2). Every 0.1-0.4 field keeps its name and meaning except two that are REMOVED: the
    definition_anchor list `sibling_blocked` and the parent_status value "sibling_boundary". (F-86-1) Sibling skip: walking back from a
    containing item, an earlier item is a sibling when enumerator_class(its token) & the containing item's class is non-empty; a sibling
    is skipped (never a parent) and listed in definition_anchor `sibling_skipped` as {item_index, item_enumerator, skipped_index,
    skipped_enumerator}; the walk continues to the nearest earlier non-sibling item whose parsing view ends with ":" (the parent), or to
    no parent. Candidate items use the full fedreg.designator_classes set ("i", "v", "x" are {letter, roman}). The containing item's own
    class is the single class fedreg._enum_class resolves for its token ("i" opens a roman list, "v" and "x" read as letters), so an
    "i." item is not the sibling of the lettered item that introduces it; with the full set on both sides an "i." item would skip its
    own parent ("a.", "ee."). fedreg.designator_classes and fedreg._enum_class are used read-only and unchanged. With no term found,
    parent_status is "no_single_parent_term" or "no_item_contains_path" as in 0.3. (F-86-2) REDESIGNATION matches only the verb
    redesignate / redesignates / redesignated / redesignating as a word, not after "newly "; groups 1 and 2 are designator lists only
    (runs of `(x)` designators joined by ",", "and", "or", "through", each optionally after "paragraph(s)"), with no ";" or ":" inside.
    definition_anchor also gains `item_walks`: one entry per containing item, with its `direct_terms`, or with the `parent_index`,
    `parent_enumerator` and `inherited_terms` its walk reached, so that a term reached through a later item (anchor rows keep only the
    first occurrence) stays visible.
A7 (0.6, S7-P1-88, C3 F-87-1, F-87-2; optional F-87-3, F-87-4). Every 0.1-0.5 field keeps its name and meaning.
    (F-87-1) observe() keeps one observation per (title, section, path) for the resolver, answer, delta and baseline fields, but builds
    definition_anchor from every listed unit that carries that path: the units are grouped by distinct (doc_key, instruction) in listing
    order, each distinct instruction is anchored once, and the anchor rows are merged in group order, each gaining `doc_key` and
    `instruction_index`. `terms` (distinct, in row order), `ambiguous`, `path_role`, `term_phrase_form` and `parent_status` are computed
    over the merged instructions; `anchored_units`, `instructions_anchored` and `anchored_doc_keys` (sorted) are added. The 0.5 flat
    `sibling_skipped` and `item_walks` are those of the first instruction; `by_instruction` lists, per instruction, its doc_key,
    instruction_index, terms, parent_status, path_role, sibling_skipped and item_walks. `from_unit` stays the first unit.
    (F-87-2) Structural parent: the parent candidate of a containing item is its parent in the item tree, read forward with a stack: an
    item closes every open item up to and including the nearest open item it is a sibling of (enumerator_class(open) & containing_class
    (item) non-empty) and is then the child of the item below; otherwise it is the child of the innermost open item. The walk therefore
    never passes a non-sibling item that is not a descendant of an earlier sibling; `sibling_skipped` lists, as in 0.5, the items between
    the candidate and the containing item that are siblings by the 0.5 test. Inheritance only when the candidate's parsing view ends with
    ":" and names exactly one normalised term (unchanged rule); otherwise no inheritance and, with no term, parent_status
    "no_single_parent_term". Each walk entry gains `parent_candidate_index`, `parent_candidate_enumerator` and
    `parent_candidate_ends_with_colon`; `parent_index` / `parent_enumerator` keep their 0.5 meaning (the ":" parent the walk reached,
    else None). (F-87-4) Containment reads path_spans(views[k], path), the same token test contains() applies.
The index is opened through regintel_p1.search.Index (sqlite3 mode=ro, regintel_p1/search.py:505); the CLI hashes it before and after and
exits 1 only if it changed.

    python3 -I scripts/p1_definition_relative_paths_83.py --index DB [--as-of 2026-10-29] --out PATHS.json
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regintel_p1 import fedreg  # noqa: E402
from regintel_p1.search import Index  # noqa: E402

SCHEMA = "p1-definition-relative-paths-0.6"  # 0.6 (S7-P1-88): per-instruction anchoring, structural parent
DECISION_RECORD = "2026-11-04_s7-regintel-p1-founder-decisions_accepted.md"
CLASSES = ("letter", "digit", "roman", "other_or_ambiguous")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def first_designator(path):
    parts = fedreg.PARA_PART.findall(path)
    return parts[0] if parts else None


def path_class(path):
    first = first_designator(path)
    classes = fedreg.designator_classes(first) if first else set()
    if classes == {"letter"}:
        return "letter", sorted(classes)
    if "digit" in classes:
        return "digit", sorted(classes)
    if classes == {"roman"}:
        return "roman", sorted(classes)
    return "other_or_ambiguous", sorted(classes)


def units(conn):
    return conn.execute("""SELECT p.id, d.doc_key, p.locator, p.title_no, p.part_no, p.section_no, p.instruction, p.explicit_paragraphs
                           FROM provision p JOIN source_document d ON d.id=p.doc_id
                           WHERE p.kind='amendment' AND p.instruction IS NOT NULL ORDER BY p.id""").fetchall()


def classify(rows):
    path_totals, unit_totals = Counter(), Counter()
    listed, groups = [], defaultdict(list)
    for r in rows:
        paths = (json.loads(r["explicit_paragraphs"] or "{}")).get("paths") or []
        kinds = [(p, *path_class(p)) for p in paths]
        for _, c, _ in kinds:
            path_totals[c] += 1
        for c in {c for _, c, _ in kinds}:
            unit_totals[c] += 1
        if any(c != "letter" for _, c, _ in kinds):
            groups[(r["doc_key"], r["instruction"])].append(r["locator"])
            listed.append({"doc_key": r["doc_key"], "locator": r["locator"], "title_no": r["title_no"], "part_no": r["part_no"],
                           "section_no": r["section_no"], "paths": paths,
                           "path_classes": [{"path": p, "class": c, "first_designator_classes": cl} for p, c, cl in kinds],
                           "instruction_head": r["instruction"][:200], "instruction": r["instruction"]})
    for u in listed:
        u["units_sharing_instruction"] = groups[(u["doc_key"], u["instruction"])]  # S7-P1-85 (F-84-6): the carried instruction
    shared = [{"doc_key": k[0], "instruction_head": k[1][:200], "locators": v, "units": len(v)} for k, v in groups.items()]
    return ({"paths": {c: path_totals.get(c, 0) for c in CLASSES}, "units": {c: unit_totals.get(c, 0) for c in CLASSES},
             "amendment_units": len(rows), "units_with_paths": sum(1 for r in rows if (json.loads(r["explicit_paragraphs"] or "{}")).get("paths"))},
            listed, shared)


def baseline(conn, title, part, section, path):
    key = f"ecfr:{title}:{part}"
    if not conn.execute("SELECT 1 FROM source_document WHERE doc_key=?", (key,)).fetchone():
        return {"baseline_doc_key": key, "status": "baseline part not in index"}
    first = first_designator(path)
    exists = lambda cite: bool(conn.execute("""SELECT 1 FROM provision p JOIN source_document d ON d.id=p.doc_id
                                               WHERE d.doc_key=? AND p.citation=?""", (key, cite)).fetchone())
    top, full = f"{title} CFR {section}{first}", f"{title} CFR {section}{path}"
    return {"baseline_doc_key": key, "status": "queried", "top_level_citation": top, "top_level_paragraph_present": exists(top),
            "full_path_citation": full, "full_path_present": exists(full),
            "section_present": exists(f"{title} CFR {section}")}


QUOTED_TERM = r"[“\"]([^”\"]+)[”\"]"
_Q = r"[“\"][^”\"]+[”\"]"
TERM = re.compile(r"(definitions?)\s+(?:of|for)\s+(" + _Q + r"(?:(?:\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+)" + _Q + r")*)")  # S7-P1-86
_RUN = r"(?:paragraphs?\s+)?(?:\([^();:]+\))+"  # S7-P1-87 (F-86-2): one designator run, e.g. (d)(20)
_LIST = _RUN + r"(?:(?:\s*,\s*(?:and\s+|or\s+|through\s+)?|\s+(?:and|or|through)\s+)" + _RUN + r")*"
REDESIGNATION = re.compile(r"(?<!newly\s)\bredesignat(?:e|es|ed|ing)\b\s+(?:the\s+)?(" + _LIST + r")\s+as\s+(" + _LIST + r")", re.I)
TERM_SEGMENT = re.compile(r"^\d+\.\d+\([^()]*[A-Za-z ][^()]*\)")
DESIGNATOR_SHAPE = re.compile(r"^(?:[a-z]{1,3}|[ivxl]+|\d+|[A-Z]{1,2})$")


LIKE_SPECIAL = ("\\", "%", "_")


def enumerated_items(instruction):
    """[(item, enumerator token or None)]: cut just after the punctuation of each ENUM_BOUNDARY that carries an enumerator token
    (S7-P1-85: the item keeps its own closing ".", ";" or ":"); the token is the one that opens the item (S7-P1-86)."""
    cuts = [(0, None)] + [(b.start(1) + 1, fedreg._boundary_token(b)) for b in fedreg.ENUM_BOUNDARY.finditer(instruction)
                          if fedreg._boundary_token(b)]
    bounds = cuts + [(len(instruction), None)]
    return [(instruction[a:b], tok) for (a, tok), (b, _) in zip(bounds, bounds[1:]) if instruction[a:b].strip()]


def items(instruction):
    return [text for text, _ in enumerated_items(instruction)]


def enumerator_class(token):
    return frozenset(fedreg.designator_classes(f"({token})")) if token else frozenset()


def containing_class(token):
    """S7-P1-87: the single list class fedreg._enum_class resolves for the containing item's token."""
    return frozenset({fedreg._enum_class(token)}) if token else frozenset()


def capture_terms(view):
    """S7-P1-86 (F-85-2): [(term_raw, "singular" | "plural")] from one parsing view, one capture per quoted term."""
    return [(t, "plural" if m.group(1).endswith("s") else "singular") for m in TERM.finditer(view) for t in re.findall(QUOTED_TERM, m.group(2))]


def path_spans(view, path):
    return [m.start() for m in re.finditer(r"(?<!\))" + re.escape(path) + r"(?!\()", view)]


def item_role(view, path):
    """S7-P1-86 (F-85-3): redesignation_source / redesignation_target / operand, or multiple."""
    moves = list(REDESIGNATION.finditer(view))
    roles = set()
    for pos in path_spans(view, path):
        role = "operand"
        for m in moves:
            if m.start(1) <= pos < m.end(1):
                role = "redesignation_source"
            elif m.start(2) <= pos < m.end(2):
                role = "redesignation_target"
        roles.add(role)
    return roles.pop() if len(roles) == 1 else ("multiple" if roles else None)


def normalise(term):
    """S7-P1-85 (F-84-1): surrounding whitespace and trailing . , ; : removed."""
    return term.strip().rstrip(".,;:").strip()


def contains(item, path):
    """S7-P1-85 (F-84-2): the path as a whole token, not inside a longer path."""
    return re.search(r"(?<!\))" + re.escape(path) + r"(?!\()", fedreg.parsing_view(item)) is not None


def like_escape(text):
    for ch in LIKE_SPECIAL:
        text = text.replace(ch, "\\" + ch)
    return text


def structural_parents(its):
    """S7-P1-88 (F-87-2): the parent index (or None) of every item, read forward with a stack of open items."""
    parents, stack = [], []
    for k, (_, token) in enumerate(its):
        cls = containing_class(token)
        match = next((n for n in range(len(stack) - 1, -1, -1) if enumerator_class(its[stack[n]][1]) & cls), None)
        if match is not None:
            del stack[match:]
        parents.append(stack[-1] if stack else None)
        stack.append(k)
    return parents


def anchor_terms(instruction, path):
    """Anchor rows (dicts) in item order, the containing items' roles and direct phrase forms, the sibling-guard stops, and a
    parent_status when nothing was found."""
    its = enumerated_items(instruction)
    views = [fedreg.parsing_view(text) for text, _ in its]
    tree = structural_parents(its)
    found, roles, forms, skipped, walks, attempted = [], [], [], [], [], False
    for k, (text, token) in enumerate(its):
        if not path_spans(views[k], path):  # S7-P1-88 (F-87-4): the token test contains() applies, on the same view
            continue
        attempted = True
        role = item_role(views[k], path)
        roles.append(role)
        caps = capture_terms(views[k])
        forms += [f for _, f in caps]
        parent = None
        if not caps:
            cls = containing_class(token)
            candidate = tree[k]
            for j in range(k - 1, -1 if candidate is None else candidate, -1):
                if enumerator_class(its[j][1]) & cls:  # a sibling: skipped, never a parent (S7-P1-87, F-86-1)
                    skipped.append({"item_index": k, "item_enumerator": token, "skipped_index": j, "skipped_enumerator": its[j][1]})
            colon = candidate is not None and views[candidate].rstrip().endswith(":")
            parent = candidate if colon else None
            raw = capture_terms(views[parent]) if parent is not None else []
            caps = raw if len({normalise(t) for t, _ in raw}) == 1 else []
            walks.append({"item_index": k, "item_enumerator": token, "parent_index": parent,
                          "parent_enumerator": its[parent][1] if parent is not None else None,
                          "inherited_terms": [normalise(t) for t, _ in caps], "parent_candidate_index": candidate,
                          "parent_candidate_enumerator": its[candidate][1] if candidate is not None else None,
                          "parent_candidate_ends_with_colon": colon})
        else:
            walks.append({"item_index": k, "item_enumerator": token, "direct_terms": [normalise(t) for t, _ in caps]})
        for t, form in caps:
            if normalise(t) and normalise(t) not in [f["term"] for f in found]:
                row = {"term": normalise(t), "term_raw": t, "term_phrase_form": form, "role": role, "inherited_from_parent": parent is not None}
                if parent is not None:
                    row.update({"parent_item_head": views[parent].strip()[:120], "item_enumerator": token,
                                "parent_enumerator": its[parent][1], "parent_item_index": parent})
                found.append(row)
    status = None if found else ("no_single_parent_term" if attempted else "no_item_contains_path")
    distinct = sorted({r for r in roles if r})
    path_role = distinct[0] if len(distinct) == 1 else ("multiple" if distinct else None)
    form = {frozenset(): "none", frozenset({"singular"}): "singular", frozenset({"plural"}): "plural"}.get(frozenset(forms), "mixed")
    return found, status, {"path_role": path_role, "term_phrase_form": form, "sibling_skipped": skipped, "item_walks": walks}


def definition_anchor(conn, title, part, section, path, instruction):
    found, status, extra = anchor_terms(instruction, path)
    terms = [f["term"] for f in found]
    key = f"ecfr:{title}:{part}"
    held = bool(conn.execute("SELECT 1 FROM source_document WHERE doc_key=?", (key,)).fetchone())
    rows = []
    for f in found:
        term = f["term"]
        loc, cite = f"{section}({term}){path}", f"{title} CFR {section} \u201c{term}\u201d {path}"
        row = {"term": term, "term_anchored_locator": loc, "term_anchored_citation": cite, **{k: v for k, v in f.items() if k != "term"}}
        if f["role"] == "redesignation_target":
            row["present_refers_to"] = "current_baseline_text"
        if not held:
            row["baseline_status"] = "baseline part not in index"
        else:
            hit = conn.execute("""SELECT p.locator, p.citation FROM provision p JOIN source_document d ON d.id=p.doc_id
                                  WHERE d.doc_key=? AND p.locator=?""", (key, loc)).fetchone()
            row.update({"baseline_status": "queried", "term_anchored_present": hit is not None,
                        "baseline_locator": hit[0] if hit else None, "baseline_citation": hit[1] if hit else None})
            if hit is None:
                like = conn.execute("""SELECT p.locator, p.citation FROM provision p JOIN source_document d ON d.id=p.doc_id
                                       WHERE d.doc_key=? AND p.section_no=? AND (p.locator LIKE ? ESCAPE '\\' OR p.citation LIKE ? ESCAPE '\\')
                                       ORDER BY p.ordinal""",
                                    (key, section, f"{like_escape(section)}({like_escape(term)})%",
                                     f"% \u201c{like_escape(term)}\u201d %")).fetchall()
                row["like_matches"] = [{"locator": a, "citation": b} for a, b in like[:20]]
                row["like_match_count"] = len(like)
        rows.append(row)
    out = {"terms": terms, "ambiguous": len(set(terms)) > 1, "baseline_doc_key": key, "baseline_in_index": held, "anchors": rows,
           "containment": "token", **extra}
    if status:
        out["parent_status"] = status
    return out


def first_balanced_segment(locator):
    """S7-P1-85 (F-84-4): the first segment after the section number, read from its "(" to the matching ")"; None if unbalanced."""
    m = re.match(r"^\d+\.\d+\(", locator)
    if not m:
        return None
    depth = 0
    for i in range(m.end() - 1, len(locator)):
        depth += {"(": 1, ")": -1}.get(locator[i], 0)
        if depth == 0:
            return locator[m.end():i]
    return None


def term_anchor_census(conn, keys):
    out = {}
    for key in sorted(keys):
        locs = [r[0] for r in conn.execute("""SELECT p.locator FROM provision p JOIN source_document d ON d.id=p.doc_id
                                              WHERE d.doc_key=?""", (key,))]
        if not locs:
            out[key] = {"in_index": False}
            continue
        regex = [l for l in locs if TERM_SEGMENT.match(l)]
        term = [l for l in regex if not DESIGNATOR_SHAPE.match(re.match(r"^\d+\.\d+\(([^()]*)\)", l).group(1))]
        balanced = [l for l in locs if (seg := first_balanced_segment(l)) is not None and not DESIGNATOR_SHAPE.match(seg)]
        out[key] = {"in_index": True, "provisions": len(locs), "regex_matches": len(regex), "term_segment_matches": len(term),
                    "term_segment_examples": term[:5], "balanced_term_segment_matches": len(balanced), "balanced_examples": balanced[:5]}
    return out


def anchored(conn, listed, u, path):
    sharing = [v for v in listed if (v["title_no"], v["section_no"]) == (u["title_no"], u["section_no"])
               and any(pc["path"] == path for pc in v["path_classes"])]
    instructions = list(dict.fromkeys((v["doc_key"], v["instruction"]) for v in sharing))
    out = merged_definition_anchor(conn, u["title_no"], u["part_no"], u["section_no"], path, instructions)
    out["anchored_units"] = len(sharing)
    return out


def merged_definition_anchor(conn, title, part, section, path, instructions):
    """S7-P1-88 (F-87-1): definition_anchor over every distinct (doc_key, instruction), rows merged in group order."""
    per = [(doc, definition_anchor(conn, title, part, section, path, text)) for doc, text in instructions]
    first = per[0][1]
    rows = [{**r, "doc_key": doc, "instruction_index": n} for n, (doc, a) in enumerate(per) for r in a["anchors"]]
    terms = list(dict.fromkeys(r["term"] for r in rows))
    roles = sorted({a["path_role"] for _, a in per if a["path_role"]} - {"multiple"})
    multiple = any(a["path_role"] == "multiple" for _, a in per)
    forms = {a["term_phrase_form"] for _, a in per} - {"none"}
    out = {**first, "terms": terms, "ambiguous": len(set(terms)) > 1, "anchors": rows,
           "path_role": "multiple" if multiple or len(roles) > 1 else (roles[0] if roles else None),
           "term_phrase_form": "none" if not forms else (forms.pop() if len(forms) == 1 and "mixed" not in forms else "mixed"),
           "anchored_units": None, "instructions_anchored": len(per), "anchored_doc_keys": sorted({doc for doc, _ in per}),
           "by_instruction": [{"doc_key": doc, "instruction_index": n, "terms": a["terms"], "parent_status": a.get("parent_status"),
                               "path_role": a["path_role"], "sibling_skipped": a["sibling_skipped"], "item_walks": a["item_walks"]}
                              for n, (doc, a) in enumerate(per)]}
    out.pop("parent_status", None)
    if not terms:
        statuses = [a.get("parent_status") for _, a in per]
        out["parent_status"] = "no_single_parent_term" if "no_single_parent_term" in statuses else "no_item_contains_path"
    return out


def observe(index, listed):
    seen, out = set(), []
    for u in listed:
        for pc in u["path_classes"]:
            if pc["class"] == "letter":
                continue
            key = (u["title_no"], u["section_no"], pc["path"])
            if key in seen:
                continue
            seen.add(key)
            cite = f"{u['title_no']} CFR {u['section_no']}{pc['path']}"
            fut, cur = index.resolve_citations(cite, "future"), index.resolve_citations(cite, "current")
            af = index.answer(cite, mode="exact", scope="future", k=5)
            ac = index.answer(cite, mode="exact", scope="current", k=5)
            d = index.delta(cite)
            row = lambda r: ({"resolved": r[0]["resolved"], "achieved": r[0].get("achieved"), "reason": r[0].get("reason"),
                              "future_paragraph_support": [x["doc_key"] for x in r[0].get("future_paragraph_support") or []]}
                             if r else {"resolver_rows": 0})
            out.append({"title": u["title_no"], "section": u["section_no"], "path": pc["path"], "class": pc["class"], "citation": cite,
                        "from_unit": u["locator"], "resolve_future": row(fut), "resolve_current": row(cur),
                        "answer_exact_future": {"status": af["status"], "first_locator": af["results"][0]["locator"] if af["results"] else None},
                        "answer_exact_current": {"status": ac["status"]},
                        "delta_instruments": [{"doc_key": g["doc_key"], "role": g.get("role"),
                                               "paragraph_precision": {k: (g.get("paragraph_precision") or {}).get(k)
                                                                       for k in ("resolved", "achieved", "reason", "supported_by")}}
                                              for g in d["instruments"]],
                        "delta_legal_validity": d["legal_validity"], "delta_applicability_status": d["applicability_status"],
                        "baseline": baseline(index.conn, u["title_no"], u["part_no"], u["section_no"], pc["path"]),
                        "definition_anchor": anchored(index.conn, listed, u, pc["path"])})
    return out


def head():
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", required=True)
    ap.add_argument("--as-of", default="2026-10-29")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    before = sha256(a.index)
    index = Index(a.index, a.as_of)
    totals, listed, shared = classify(units(index.conn))
    observations = observe(index, listed)
    census = term_anchor_census(index.conn, {o["definition_anchor"]["baseline_doc_key"] for o in observations if o["class"] == "digit"})
    observed_terms = sorted({a["term"] for o in observations for a in o["definition_anchor"]["anchors"]})
    for u in listed:
        u.pop("instruction", None)  # used for anchoring only; the 0.1 instruction_head stays
    meta = dict(index.meta)
    index.conn.close()
    after = sha256(a.index)
    out = {"schema": SCHEMA, "task": "S7-P1-83", "decision_record": DECISION_RECORD, "head": head(), "as_of": a.as_of,
           "index": {"path": a.index, "sha256": before, "unchanged_after_run": before == after, "built_at": meta.get("built_at"),
                     "manifest_sha256": meta.get("manifest_sha256")},
           "totals": totals, "non_letter_led_units": listed, "instruction_groups": shared, "observations": observations, "term_anchor_census": census,
           "like_escape": {"escaped": list(LIKE_SPECIAL), "observed_terms": len(observed_terms),
                           "observed_terms_containing_escaped": [t for t in observed_terms if any(c in t for c in LIKE_SPECIAL)]},
           "storage": "explicit_paragraphs is stored per amendment unit; units that share one paired instruction repeat the same list",
           "legal_validity": "not_determined", "applicability_status": "not_assessed",
           "status": "proposal; producer-reported; no gate approved; nothing authorised"}
    data = (json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode()
    Path(a.out).write_bytes(data)
    print(json.dumps({"out": a.out, "sha256": hashlib.sha256(data).hexdigest(), "non_letter_led_units": len(listed),
                      "observations": len(observations), "index_unchanged": before == after}))
    return 0 if before == after else 1


if __name__ == "__main__":
    raise SystemExit(main())
