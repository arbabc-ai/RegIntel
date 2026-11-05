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

SCHEMA = "p1-definition-relative-paths-0.2"  # 0.2 (S7-P1-84) adds definition_anchor and term_anchor_census; 0.1 fields kept
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
        u["units_sharing_instruction"] = groups[(u["doc_key"], next(r["instruction"] for r in rows if r["locator"] == u["locator"]))]
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


TERM = re.compile(r"definition\s+(?:of|for)\s+[\u201c\"]([^\u201d\"]+)[\u201d\"]")
TERM_SEGMENT = re.compile(r"^\d+\.\d+\([^()]*[A-Za-z ][^()]*\)")
DESIGNATOR_SHAPE = re.compile(r"^(?:[a-z]{1,3}|[ivxl]+|\d+|[A-Z]{1,2})$")


def items(instruction):
    """Enumerated items of an instruction: text between ENUM_BOUNDARY boundaries that carry an enumerator token."""
    cuts = [0] + [b.start() for b in fedreg.ENUM_BOUNDARY.finditer(instruction) if fedreg._boundary_token(b)] + [len(instruction)]
    return [instruction[a:b] for a, b in zip(cuts, cuts[1:]) if instruction[a:b].strip()]


def anchor_terms(instruction, path):
    terms = []
    for item in items(instruction):
        if path in fedreg.parsing_view(item):
            terms += [t.strip() for t in TERM.findall(item) if t.strip() not in terms]
    return terms


def definition_anchor(conn, title, part, section, path, instruction):
    terms = anchor_terms(instruction, path)
    key = f"ecfr:{title}:{part}"
    held = bool(conn.execute("SELECT 1 FROM source_document WHERE doc_key=?", (key,)).fetchone())
    rows = []
    for term in terms:
        loc, cite = f"{section}({term}){path}", f"{title} CFR {section} \u201c{term}\u201d {path}"
        row = {"term": term, "term_anchored_locator": loc, "term_anchored_citation": cite}
        if not held:
            row["baseline_status"] = "baseline part not in index"
        else:
            hit = conn.execute("""SELECT p.locator, p.citation FROM provision p JOIN source_document d ON d.id=p.doc_id
                                  WHERE d.doc_key=? AND p.locator=?""", (key, loc)).fetchone()
            row.update({"baseline_status": "queried", "term_anchored_present": hit is not None,
                        "baseline_locator": hit[0] if hit else None, "baseline_citation": hit[1] if hit else None})
            if hit is None:
                like = conn.execute("""SELECT p.locator, p.citation FROM provision p JOIN source_document d ON d.id=p.doc_id
                                       WHERE d.doc_key=? AND p.section_no=? AND (p.locator LIKE ? OR p.citation LIKE ?) ORDER BY p.ordinal""",
                                    (key, section, f"{section}({term})%", f"% \u201c{term}\u201d %")).fetchall()
                row["like_matches"] = [{"locator": a, "citation": b} for a, b in like[:20]]
                row["like_match_count"] = len(like)
        rows.append(row)
    return {"terms": terms, "ambiguous": len(set(terms)) > 1, "baseline_doc_key": key, "baseline_in_index": held, "anchors": rows}


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
        out[key] = {"in_index": True, "provisions": len(locs), "regex_matches": len(regex), "term_segment_matches": len(term),
                    "term_segment_examples": term[:5]}
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
                        "definition_anchor": definition_anchor(index.conn, u["title_no"], u["part_no"], u["section_no"], pc["path"],
                                                               u["instruction"])})
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
    for u in listed:
        u.pop("instruction", None)  # used for anchoring only; the 0.1 instruction_head stays
    meta = dict(index.meta)
    index.conn.close()
    after = sha256(a.index)
    out = {"schema": SCHEMA, "task": "S7-P1-83", "decision_record": DECISION_RECORD, "head": head(), "as_of": a.as_of,
           "index": {"path": a.index, "sha256": before, "unchanged_after_run": before == after, "built_at": meta.get("built_at"),
                     "manifest_sha256": meta.get("manifest_sha256")},
           "totals": totals, "non_letter_led_units": listed, "instruction_groups": shared, "observations": observations, "term_anchor_census": census,
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
