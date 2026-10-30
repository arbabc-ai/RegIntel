"""Passage and amendment-unit parser for Federal Register full-text XML (RULE, PRORULE, NOTICE)."""
import re
import xml.etree.ElementTree as ET

WS = re.compile(r"\s+")
SECTNO = re.compile(r"§+\s*([0-9]+)\.([0-9A-Za-z\-]+)")
PART_HD = re.compile(r"PART\s+([0-9]+)", re.I)
NUMBERED = re.compile(r"^\s*\d+\.\s")
PARA_LEAD = re.compile(r"^((?:\([A-Za-z0-9]+\))+)")
OPERATIONS = (
    ("revise", re.compile(r"\brevis(?:e|ed|ing)\b", re.I)),
    ("add", re.compile(r"\badd(?:ed|ing)?\b", re.I)),
    ("remove", re.compile(r"\bremov(?:e|ed|ing)\b", re.I)),
    ("redesignate", re.compile(r"\bredesignat(?:e|ed|ing)\b", re.I)),
    ("amend", re.compile(r"\bamend(?:ed|ing)?\b", re.I)),
    ("reserve", re.compile(r"\breserv(?:e|ed|ing)\b", re.I)),
    ("suspend", re.compile(r"\bsuspend(?:ed|ing)?\b", re.I)),
)
REG_TAGS = {"REGTEXT", "AMDPAR", "SECTION", "PART", "SUBPART", "AUTH", "STARS", "CONTENTS", "APPENDIX"}
PASSAGE_CHARS = 1800
UNIT_CHARS = 3000


# A paragraph that follows an amendatory instruction is a continuation of it only when it is a short formulaic closer
# ("The revisions and additions read as follows:") that does not open with a paragraph designator. Anything else is
# carried regulatory text.
CONTINUATION = re.compile(
    r"^(?!\()[^()]{0,260}?\b(?:read|reads)\s+as\s+follows\s*:?\s*$|^(?!\()[^()]{0,200}\bas\s+follows\s*:\s*$", re.I)


def is_continuation(text):
    return bool(text) and len(text) <= 300 and bool(CONTINUATION.match(text))


def clean(text):
    return WS.sub(" ", text.replace("\xa0", " ")).strip()


def text_of(el):
    return clean("".join(el.itertext()))


def table_text(el):
    title = el.find("TTITLE")
    lines = [text_of(title)] if title is not None else []
    for row in el.iter("ROW"):
        cells = [text_of(c) for c in row if c.tag in ("ENT", "CHED")]
        if any(cells):
            lines.append(" | ".join(cells))
    for row in el.iter("BOXHD"):
        lines.insert(1 if lines else 0, " | ".join(text_of(c) for c in row.iter("CHED")))
    return "\n".join(lines)


def split_chunks(paragraphs, limit):
    chunks, cur, size = [], [], 0
    for para in paragraphs:
        if cur and size + len(para) > limit:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(para)
        size += len(para) + 1
    if cur:
        chunks.append(cur)
    return chunks


QUOTED = re.compile(r"\u201c[^\u201d]*\u201d|\"[^\"]*\"|\u2018[^\u2019]*\u2019")
SEC_ITEM = r"\d+\.\d+[A-Za-z]?(?:\([A-Za-z0-9]+\))*"
# List separators, longest first so an Oxford comma (", and" / ", or") is consumed as one separator, not as a bare comma
# that then fails to find a section number.
LIST_SEPARATOR = r"(?:,\s*(?:and|or)\b|,|\band\b|\bor\b|through|to|[\-\u2013\u2014])"
SEC_LIST_BODY = rf"{SEC_ITEM}(?:\s*{LIST_SEPARATOR}\s*(?:\u00a7+\s*)?{SEC_ITEM})*"
SECTION_TOKEN = re.compile(r"(\d+)\.(\d+[A-Za-z]?)")
LIST_SEP = re.compile(rf"\s*({LIST_SEPARATOR})\s*(?:\u00a7+\s*)?", re.I)
VERB = (r"(?:amend(?:ing)?|revis(?:e|ing)|add(?:ing)?|remov(?:e|ing)(?:\s+and\s+reserv(?:e|ing))?|redesignat(?:e|ing)"
        r"|reserv(?:e|ing)|suspend(?:ing)?|republish(?:ing)?|delet(?:e|ing)|establish(?:ing)?|designat(?:e|ing))")
DONE = r"(?:amended|revised|added|removed|redesignated|reserved|suspended|republished|deleted|established)"
# An instruction explicitly targets a section only through these constructions, outside quoted text.
EXPLICIT_SECTION_PATTERNS = (
    re.compile(rf"\b{VERB}\s+(?:new\s+)?(?:paragraphs?\s+[^\u00a7]{{0,60}}?\s+(?:of|in)\s+)?\u00a7\u00a7?\s*({SEC_LIST_BODY})", re.I),
    re.compile(rf"(?<![A-Za-z])In\s+\u00a7\u00a7?\s*({SEC_LIST_BODY})"),            # "In § 3.2, revise ..." (capital I only)
    re.compile(rf"(?:\u00a7\u00a7?|\bSections?)\s*({SEC_LIST_BODY})\s+(?:is|are)\s+{DONE}", re.I),
    re.compile(rf"\bconsisting of\s+\u00a7\u00a7?\s*({SEC_LIST_BODY})", re.I),    # "Add subpart G, consisting of §§ 5.100 through 5.104"
)
EXPLICIT_PART_PATTERNS = (
    re.compile(r"\bauthority citation for (?:the\s+)?part\s+(\d+)", re.I),
    re.compile(rf"\bPart\s+(\d+)\s+is\s+{DONE}", re.I),
    re.compile(rf"\b{VERB}\s+(?:the\s+)?(?:(?:subpart|appendix|appendices)\s+[A-Za-z0-9\-]+\s+(?:of|to)\s+)?part\s+(\d+)\b", re.I),
)


PARA_TOKEN = r"(?:\([A-Za-z0-9]+\))+"
PARA_LIST = (rf"({PARA_TOKEN}(?:\s*(?:,|and|or|through|to|[\-\u2013\u2014])\s*(?:and\s+)?{PARA_TOKEN})*)")
# A paragraph counts as named only as the object of an action ("revising paragraph (c)(1)", "In paragraph (a)(2),"),
# outside quotation marks. A bare mention elsewhere is not evidence.
PARA_ACTION = re.compile(
    rf"(?:\b{VERB}\s+(?:new\s+)?|(?:^|[.;:,]\s+|\b[a-z0-9]{{1,3}}\.\s+)(?-i:In)\s+)paragraphs?\s+{PARA_LIST}", re.I)
# "In paragraph (c)(2), removing ..." names a path only as a clause-initial capital "In"; "provided in paragraph (e)(3)"
# is a cross-reference and never evidence.
PARA_PART = re.compile(r"\([A-Za-z0-9]+\)")


def _list_members(body):
    """(sections, ranges) from `3.10, 3.11 and 3.12(a)` or `3.120 through 3.121`; paragraph designators dropped."""
    tokens = [SECTION_TOKEN.search(t).group(0) for t in re.findall(SEC_ITEM, body)]
    separators = [m.group(1).lower() for m in LIST_SEP.finditer(re.sub(SEC_ITEM, "\u0000", body))]
    ranges = []
    for i, sep in enumerate(separators[: max(len(tokens) - 1, 0)]):
        if sep in ("through", "to", "-", "\u2013", "\u2014"):  # an Oxford ", and" is a list separator, never a range
            ranges.append([tokens[i], tokens[i + 1]])
    return list(dict.fromkeys(tokens)), ranges


ROMAN_LOWER = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv", "xvi",
               "xvii", "xviii", "xix", "xx"}
# CFR paragraph hierarchy by depth: (a) (1) (i) (A) (1) (i); the class a designator at each depth must have.
DEPTH_CLASS = {1: "letter", 2: "digit", 3: "roman", 4: "upper", 5: "digit", 6: "roman"}


def designator_classes(designator):
    """Hierarchy classes a bare designator could belong to (`i`, `v`, `x` are both a letter and a roman numeral)."""
    d = designator.strip("()")
    classes = set()
    if d.isdigit():
        classes.add("digit")
    if d in ROMAN_LOWER:
        classes.add("roman")
    if d.islower() and (len(d) == 1 or (len(set(d)) == 1 and len(d) == 2 and d not in ROMAN_LOWER)) and d not in ROMAN_LOWER - {"i", "v", "x"}:
        classes.add("letter")
    if d.isupper() and (len(d) == 1 or len(set(d)) == 1 and len(d) == 2):
        classes.add("upper")
    return classes


def expand_shorthand(previous, designator):
    """The path a bare designator names after `previous`, or None when the hierarchy does not settle it.

    A bare designator is a sibling of the previous path's last designator (`(c)(5) ... (7)` is (c)(7)) only when its
    class is the one the hierarchy puts at that depth. Otherwise it is a top-level paragraph (`(c)(4)(i), (e)` names
    top-level (e), not (c)(4)(e)) only when it can be a top-level letter. If both readings fit and differ
    (`(a)(1)(i) and (v)`: roman v, or letter v) the shorthand is ambiguous and nothing is claimed.
    """
    classes = designator_classes(designator)
    depth = len(previous)
    sibling = DEPTH_CLASS.get(depth, "digit") in classes
    top = "letter" in classes
    if sibling and top and depth > 1:
        return None
    if sibling:
        return previous[:-1] + [designator]
    if top:
        return [designator]
    return None


def named_paragraph_paths(text):
    """Complete paragraph paths an instruction names as objects of an action, e.g. `(c)(1)`, `(a)(2)(i)`, `(i)`.

    Paths written out in full count. A bare designator after a path in the same list is expanded only where the CFR
    hierarchy supports it (see `expand_shorthand`): `(c)(5) through (7)` names (c)(5) and (c)(7) and not the interior;
    `(c)(4)(i), (e) and (f)` names (c)(4)(i), (e), (f); `(b)(1), (c), (d)` names (b)(1), (c), (d). Ambiguous shorthand,
    and every bare designator after it, is left unresolved. Descendants of a named paragraph are never inferred.
    """
    stripped = QUOTED.sub(" \u2039q\u203a ", text)
    paths = []
    for m in PARA_ACTION.finditer(stripped):
        previous = None  # list of designators of the previous path; "unknown" once shorthand could not be settled
        for token in re.finditer(PARA_TOKEN, m.group(1)):
            parts = PARA_PART.findall(token.group(0))
            if len(parts) > 1 or previous is None:
                resolved = parts  # written in full, or the first item of a list
            elif previous == "unknown":
                resolved = None
            else:
                resolved = expand_shorthand(previous, parts[0])
            if resolved is None:
                previous = "unknown"
                continue
            path = "".join(resolved)
            if path not in paths:
                paths.append(path)
            previous = resolved
    return paths


def parse_instruction(text):
    """Split an amendatory instruction into explicit targets and merely referenced citations.

    `targets` / `ranges` / `parts` are what the instruction says it amends (verb-object, "In § N," or "§ N is
    amended" constructions outside quotation marks). Every other section citation, including ones inside quoted
    text being removed or added and ones in clauses such as "as defined in § 3.22", is a `reference` and never a
    target. These are text-pattern signals, not reviewed legal readings.
    """
    stripped = QUOTED.sub(" \u2039q\u203a ", text)
    targets, ranges, parts = [], [], []
    for rx in EXPLICIT_SECTION_PATTERNS:
        for m in rx.finditer(stripped):
            members, spans = _list_members(m.group(1))
            targets += [t for t in members if t not in targets]
            ranges += [r for r in spans if r not in ranges]
    for rx in EXPLICIT_PART_PATTERNS:
        for m in rx.finditer(stripped):
            if m.group(1) not in parts:
                parts.append(m.group(1))
    references = []
    for quoted, source in ((False, stripped), (True, " ".join(QUOTED.findall(text)))):
        for m in re.finditer(rf"\u00a7\u00a7?\s*({SEC_LIST_BODY})", source):
            for token in SECTION_TOKEN.finditer(m.group(1)):
                section = f"{token.group(1)}.{token.group(2)}"
                entry = {"section": section, "quoted": quoted}
                if section not in targets and entry not in references:
                    references.append(entry)
    return {"targets": targets, "ranges": ranges, "parts": parts, "references": references}


def in_range(section, ranges):
    """True when `section` lies inside a named `through` range with a shared part and numeric minors."""
    m = SECTION_TOKEN.fullmatch(section)
    if not m:
        return False
    for lo, hi in ranges:
        a, b = SECTION_TOKEN.fullmatch(lo), SECTION_TOKEN.fullmatch(hi)
        if a and b and a.group(1) == b.group(1) == m.group(1) and a.group(2).isdigit() and b.group(2).isdigit() \
                and m.group(2).isdigit() and int(a.group(2)) <= int(m.group(2)) <= int(b.group(2)):
            return True
    return False


def classify_ranges(ranges):
    """Numeric membership is only asserted for `A.x through A.y` with plain integer minors in one part and x <= y.

    Anything else (different parts, lettered minors, reversed ends) stays `unresolved_ambiguous`: its endpoints
    are still explicit targets, but nothing between them is claimed.
    """
    out = []
    for lo, hi in ranges:
        a, b = SECTION_TOKEN.fullmatch(lo), SECTION_TOKEN.fullmatch(hi)
        ok = bool(a and b and a.group(1) == b.group(1) and a.group(2).isdigit() and b.group(2).isdigit()
                  and int(a.group(2)) <= int(b.group(2)))
        entry = {"lo": lo, "hi": hi, "status": "numeric_range" if ok else "unresolved_ambiguous"}
        if ok:
            entry.update({"part": a.group(1), "lo_minor": int(a.group(2)), "hi_minor": int(b.group(2))})
        out.append(entry)
    return out


def targeted(parsed, section):
    return section in parsed["targets"] or in_range(section, parsed["ranges"])


class FrParser:
    def __init__(self, root, doc_number, default_title):
        self.root = root
        self.doc = doc_number
        self.default_title = default_title
        self.passages = []
        self.units = []
        self.page = None
        self.headings = []  # (level, text)
        self.buffer = []
        self.buffer_page = None
        self.loose = None
        self.unit_no = 0
        self.stats = {"preamble_passages": 0, "amendment_units": 0, "tables": 0, "footnotes": 0}

    def heading_path(self):
        return " > ".join(t for _, t in self.headings)

    def flush(self):
        if self.buffer:
            for chunk in split_chunks(self.buffer, PASSAGE_CHARS):
                self.passages.append({"heading": self.heading_path(), "text": "\n".join(chunk),
                                      "fr_page": self.buffer_page, "kind": "preamble"})
        self.buffer = []
        self.buffer_page = self.page

    def add_para(self, text):
        if not text:
            return
        if not self.buffer:
            self.buffer_page = self.page
        self.buffer.append(text)

    def on_heading(self, el):
        text = text_of(el)
        if not text:
            return
        level = {"HED": 0, "HD1": 1, "HD2": 2, "HD3": 3, "HD4": 4, "HD5": 5}.get(el.get("SOURCE", "HD1"), 1)
        self.flush()
        self.headings = [h for h in self.headings if h[0] < level] + [(level, text)]

    def walk(self, el):
        """Preamble walk: reg elements are diverted to the unit collector, everything else is passage text."""
        for child in el:
            tag = child.tag
            if tag == "PRTPAGE":
                self.page = child.get("P") or self.page
            elif tag == "REGTEXT":
                self.close_loose()
                self.flush()
                self.add_unit(child, child.get("TITLE") or self.default_title, child.get("PART"), is_regtext=True)
            elif tag in ("AMDPAR", "SECTION", "PART", "SUBPART", "AUTH", "APPENDIX"):
                self.flush()
                self.collect_loose(child)
            elif tag in ("HD",):
                self.close_loose()
                self.on_heading(child)
            elif tag in ("P", "FP", "LI"):
                text = text_of(child)
                if self.loose is not None and self.loose["instr_open"] and is_continuation(text):
                    self.loose["instructions"][-1] += " " + text  # e.g. "The revision reads as follows:"
                    continue
                if self.loose is not None and self.loose["block_open"]:
                    self.loose["instr_open"] = False  # the instruction is complete; this is carried regulatory text
                    self.loose["blocks"][-1]["paragraphs"].append(text)
                    continue
                self.close_loose()
                self.add_para(text)
            elif tag == "FTNT":
                self.stats["footnotes"] += 1
                self.add_para("[footnote] " + text_of(child))
            elif tag == "GPOTABLE":
                self.close_loose()
                self.stats["tables"] += 1
                self.flush()
                self.passages.append({"heading": self.heading_path(), "text": table_text(child),
                                      "fr_page": self.page, "kind": "table"})
            elif tag in ("STARS", "LSTSUB", "SIG", "BILCOD", "FRDOC", "GPH", "CONTENTS", "NAME", "TITLE"):
                continue
            elif tag in ("EXTRACT", "AGY", "ACT", "SUM", "DATES", "ADD", "FURINF", "EFFDATE", "AGENCY", "SUBAGY",
                         "PREAMB", "SUPLINF", "DEPDOC", "RIN", "CFR", "SUBJECT", "EXAMPLE", "NOTE", "DATE"):
                if tag in ("AGENCY", "SUBAGY", "DEPDOC", "RIN", "CFR", "SUBJECT"):
                    self.add_para(text_of(child))
                else:
                    self.walk(child)
            else:
                self.add_para(text_of(child))

    # --- regulatory text units ---
    def collect_loose(self, child):
        """Accumulate one contiguous run of AMDPAR/SECTION/PART/AUTH elements.

        The Federal Register often lists a section heading *before* the instruction that amends it, so
        pairing is done afterwards by section reference (see add_unit), not by position.
        """
        if self.loose is None:
            self.loose = {"instructions": [], "sections": [], "parts": [], "instr_open": False,
                          "block_open": False, "blocks": []}
        if child.tag == "AMDPAR":
            text = text_of(child)
            if self.loose["instructions"] and not NUMBERED.match(text):
                self.loose["instructions"][-1] += " " + text  # "a. ..." sub-steps belong to "2. Amend § N by:"
            else:
                self.loose["instructions"].append(text)
            self.loose["instr_open"] = True
            # loose carried paragraphs that follow are kept apart from the instruction, tied to its position
            self.loose["block_open"] = True
            self.loose["blocks"].append({"instruction_idx": len(self.loose["instructions"]), "paragraphs": []})
            return
        self.loose["instr_open"] = False
        self.loose["block_open"] = False
        if child.tag == "PART":
            self.loose["parts"].append(child)
        self.loose["sections"].append(child)

    def close_loose(self):
        loose, self.loose = self.loose, None
        if not loose:
            return
        holder = ET.Element("LOOSE")
        for el in loose["sections"]:
            holder.append(el)
        for el in loose["sections"]:  # an AMDPAR nested inside a PART/SUBPART wrapper still counts as an instruction
            if el.tag in ("PART", "SUBPART", "APPENDIX"):
                for nested in el.iter("AMDPAR"):
                    text = text_of(nested)
                    if loose["instructions"] and not NUMBERED.match(text):
                        loose["instructions"][-1] += " " + text
                    else:
                        loose["instructions"].append(text)
        part = None
        for el in loose["parts"]:
            hd = el.find("HD")
            m = PART_HD.search(text_of(hd)) if hd is not None else None
            part = part or (m.group(1) if m else None)
        self.add_unit(holder, self.default_title, part, is_regtext=False,
                      instructions=loose["instructions"], blocks=loose["blocks"])

    def collect_rows(self, el, part, rows):
        """Carried regulatory text in document order, each row with the part it sits in."""
        for child in el:
            if child.tag == "PART":
                hd = child.find("HD")
                m = PART_HD.search(text_of(hd)) if hd is not None else None
                self.collect_rows(child, m.group(1) if m else part, rows)
            elif child.tag in ("SUBPART", "APPENDIX"):
                self.collect_rows(child, part, rows)
            elif child.tag == "AUTH":
                rows.append({"authority": True, "section_no": None, "part_no": part, "subject": "Authority citation",
                             "paragraphs": [text_of(child)]})
            elif child.tag == "SECTION":
                sectno, subj = child.find("SECTNO"), child.find("SUBJECT")
                ref = SECTNO.search(text_of(sectno)) if sectno is not None else None
                paragraphs = []
                for p in child:
                    if p.tag in ("P", "FP", "EXTRACT"):
                        paragraphs.append(text_of(p))
                    elif p.tag == "GPOTABLE":
                        paragraphs.append(table_text(p))
                rows.append({"authority": False, "section_no": f"{ref.group(1)}.{ref.group(2)}" if ref else None,
                             "part_no": ref.group(1) if ref else part,
                             "subject": text_of(subj) if subj is not None else None, "paragraphs": paragraphs})

    @staticmethod
    def paragraph_evidence(row, items):
        """Paragraph paths named by the row's own instruction(s), attributed only when unambiguous.

        If a paired instruction targets several sections or ranges, the paragraph it names cannot be assigned to one
        of them, so nothing is attributed (`unattributed_multiple_targets`).
        """
        ordinals = set(row.get("instruction_ordinals") or [])
        paired = [i for i in items if i["idx"] in ordinals]
        paths, status = [], "none_named"
        for i in paired:
            named = named_paragraph_paths(i["text"])
            if not named:
                continue
            parsed = i["parsed"]
            if len(parsed["targets"]) == 1 and not parsed["ranges"] and not parsed["parts"]:
                paths += [x for x in named if x not in paths]
                status = "attributed"
            else:
                status = "unattributed_multiple_targets" if status != "attributed" else status
        return {"paths": paths, "status": status}

    def add_unit(self, el, title, part, is_regtext, instructions=None, blocks=None):
        """Pair carried section text only with instructions that explicitly target that section.

        Instruction text is kept complete. A citation merely referenced inside an instruction is not a target.
        Anything that cannot be paired explicitly stays as an unresolved candidate, with no amends target.
        """
        self.unit_no += 1
        unit_id = f"{self.doc}:reg:{self.unit_no}"
        if instructions is None:
            instructions = []
            for x in el.iter("AMDPAR"):
                text = text_of(x)
                if instructions and not NUMBERED.match(text):
                    instructions[-1] += " " + text
                else:
                    instructions.append(text)
        items = [{"idx": n, "text": t, "parsed": parse_instruction(t), "covered": set(), "used": False}
                 for n, t in enumerate(instructions, 1) if t]
        rows = []
        self.collect_rows(el, part, rows)
        out = []  # (row-like dict, carried paragraphs)

        def candidates_for(section):
            found = []
            for i in items:
                refs = {r["section"] for r in i["parsed"]["references"]}
                part_scope = section.split(".")[0] in i["parsed"]["parts"]
                if section in refs or part_scope or not (i["parsed"]["targets"] or i["parsed"]["parts"]):
                    found.append({"instruction": i["text"], "instruction_ordinal": i["idx"],
                                  "reason": "names the section only as a reference" if section in refs
                                  else "instruction targets the whole part; the section inside it is not paired explicitly"
                                  if part_scope else "instruction states no explicit target"})
            return found

        for r in rows:
            if r["authority"]:
                matched = [i for i in items if re.search(r"authority citation", i["text"], re.I)
                           and r["part_no"] in i["parsed"]["parts"]]
            else:
                matched = [i for i in items if r["section_no"] and targeted(i["parsed"], r["section_no"])]
            for i in matched:
                i["used"] = True
                if r["section_no"]:
                    i["covered"].add(r["section_no"])
            if r["authority"]:
                explicit_targets, explicit_parts = [], [r["part_no"]] if matched else []
            else:
                explicit_targets, explicit_parts = ([r["section_no"]] if matched else []), []
            if not matched:
                status = "carried_text_unpaired"
                unresolved = candidates_for(r["section_no"]) if r["section_no"] else \
                    [{"instruction": i["text"], "instruction_ordinal": i["idx"], "reason": "authority citation not matched to this part"}
                     for i in items if re.search(r"authority citation", i["text"], re.I)]
            else:
                status = "explicit" if len(matched) == 1 else "multiple_instructions_same_target"
                unresolved = []
            out.append({
                "section_no": r["section_no"], "part_no": r["part_no"], "subject": r["subject"],
                "instruction": "\n".join(i["text"] for i in matched),
                "instruction_ordinals": [i["idx"] for i in matched],
                "instruction_targets_all": {"targets": sorted({t for i in matched for t in i["parsed"]["targets"]}),
                                            "ranges": [x for i in matched for x in i["parsed"]["ranges"]]},
                "explicit_targets": explicit_targets, "explicit_parts": explicit_parts,
                "referenced_citations": [x for i in matched for x in i["parsed"]["references"]],
                "unresolved_candidates": unresolved, "pairing_status": status, "paragraphs": r["paragraphs"]})
        for block in blocks or []:  # loose carried paragraphs (no SECTION wrapper) that followed an instruction
            if not block["paragraphs"]:
                continue
            item = next((i for i in items if i["idx"] == block["instruction_idx"]), None)
            parsed = item["parsed"] if item else None
            if item and len(parsed["targets"]) == 1 and not parsed["ranges"] and not parsed["parts"]:
                section = parsed["targets"][0]
                item["used"] = True
                item["covered"].add(section)
                out.append({
                    "section_no": section, "part_no": section.split(".")[0], "subject": None, "instruction": item["text"],
                    "instruction_ordinals": [item["idx"]],
                    "instruction_targets_all": {"targets": parsed["targets"], "ranges": parsed["ranges"]},
                    "explicit_targets": [section], "explicit_parts": [], "referenced_citations": parsed["references"],
                    "unresolved_candidates": [], "pairing_status": "explicit_loose_text",
                    "paragraphs": block["paragraphs"], "carried_text_origin": "loose_paragraphs"})
            else:  # association uncertain: keep the text and the candidate instruction, assert no target
                out.append({
                    "section_no": None, "part_no": part, "subject": None, "instruction": "", "instruction_ordinals": [],
                    "instruction_targets_all": {"targets": [], "ranges": []}, "explicit_targets": [], "explicit_parts": [],
                    "referenced_citations": [],
                    "unresolved_candidates": ([{"instruction": item["text"], "instruction_ordinal": item["idx"],
                                                "reason": "loose carried paragraphs follow an instruction that does not "
                                                          "have a single explicit section target"}] if item else []),
                    "pairing_status": "carried_text_unpaired", "paragraphs": block["paragraphs"],
                    "carried_text_origin": "loose_paragraphs"})
        for i in items:  # instruction-only amendments: targets not covered by a carried-text row
            parsed = i["parsed"]
            remaining = [t for t in parsed["targets"] if t not in i["covered"]]
            part_only = [p for p in parsed["parts"] if not re.search(r"authority citation", i["text"], re.I)]
            ranges = classify_ranges(parsed["ranges"])
            if remaining or part_only or ranges:
                first_part = (remaining[0].split(".")[0] if remaining else
                              (part_only[0] if part_only else ranges[0]["lo"].split(".")[0]))
                out.append({
                    "section_no": remaining[0] if remaining else None, "part_no": first_part, "subject": None,
                    "instruction": i["text"], "instruction_ordinals": [i["idx"]],
                    "instruction_targets_all": {"targets": parsed["targets"], "ranges": parsed["ranges"]},
                    "explicit_targets": remaining, "explicit_parts": part_only, "explicit_ranges": [
                        {**r, "instruction_ordinal": i["idx"]} for r in ranges],
                    "referenced_citations": parsed["references"],
                    "unresolved_candidates": [
                        {"instruction": i["text"], "instruction_ordinal": i["idx"], "range": [r["lo"], r["hi"]],
                         "reason": "range endpoints are not plain numeric sections of one part; sections between them are unresolved"}
                        for r in ranges if r["status"] != "numeric_range"],
                    "pairing_status": "instruction_only", "paragraphs": []})
            elif not i["used"] and not parsed["targets"] and not parsed["parts"]:
                out.append({
                    "section_no": None, "part_no": part, "subject": None, "instruction": i["text"],
                    "instruction_ordinals": [i["idx"]], "instruction_targets_all": {"targets": [], "ranges": []},
                    "explicit_targets": [], "explicit_parts": [], "referenced_citations": parsed["references"],
                    "unresolved_candidates": [{"instruction": i["text"], "instruction_ordinal": i["idx"],
                                               "candidate_sections": [x["section"] for x in parsed["references"]],
                                               "reason": "instruction states no explicit target"}],
                    "pairing_status": "instruction_no_explicit_target", "paragraphs": []})
        for r in out:
            if not r["instruction"] and not r["paragraphs"] and not r["unresolved_candidates"]:
                continue
            operations = [name for name, rx in OPERATIONS if rx.search(r["instruction"])]
            r["explicit_paragraphs"] = self.paragraph_evidence(r, items)
            for chunk_no, chunk in enumerate(split_chunks(r["paragraphs"], UNIT_CHARS) or [[]], 1):
                leads = []
                for para in chunk:
                    m = PARA_LEAD.match(para)
                    if m and m.group(1) not in leads:
                        leads.append(m.group(1))
                self.units.append({
                    "unit_id": unit_id, "chunk": chunk_no, "title_no": title, "part_no": r["part_no"] or part,
                    "section_no": r["section_no"], "subject": r["subject"], "instruction": r["instruction"],
                    "instruction_ordinals": r["instruction_ordinals"], "operations": operations,
                    "explicit_targets": r["explicit_targets"], "explicit_parts": r["explicit_parts"],
                    "explicit_ranges": r.get("explicit_ranges", []),
                    "explicit_paragraphs": r["explicit_paragraphs"],
                    "instruction_targets_all": r["instruction_targets_all"],
                    "referenced_citations": r["referenced_citations"],
                    "unresolved_candidates": r["unresolved_candidates"], "pairing_status": r["pairing_status"],
                    "paragraph_leads": leads, "text": "\n".join(chunk), "fr_page": self.page,
                    "from_regtext": is_regtext, "carried_text_origin": r.get("carried_text_origin", "section_element")})
        self.stats["amendment_units"] += 1

    def run(self):
        self.walk(self.root)
        self.close_loose()
        self.flush()
        self.stats["preamble_passages"] = len(self.passages)
        return self.passages, self.units, self.stats


def parse_fr(xml_bytes, doc_number, default_title=None):
    """Parse one Federal Register document into preamble passages and regulatory-text amendment units.

    Amendment units keep the agency's own amendatory instruction verbatim and the section text it carries;
    `operations`, `explicit_targets` and `referenced_citations` are text-pattern signals from that instruction, not interpreted changes.
    """
    root = ET.fromstring(xml_bytes)
    cfr = root.find(".//CFR")
    if default_title is None:
        m = re.search(r"([0-9]+)\s+CFR", text_of(cfr)) if cfr is not None else None
        default_title = m.group(1) if m else None
    passages, units, stats = FrParser(root, doc_number, default_title).run()
    stats["root"] = root.tag
    stats["cfr_line"] = text_of(cfr) if cfr is not None else None
    return passages, units, stats
