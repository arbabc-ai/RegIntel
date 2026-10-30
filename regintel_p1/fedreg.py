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


SECTION_LIST = re.compile(
    r"§§?\s*(\d+\.\d+[A-Za-z]?(?:\([A-Za-z0-9]+\))*"
    r"(?:\s*(?:,|and|or|through|to|[\-\u2013\u2014])\s*(?:§\s*)?\d+\.\d+[A-Za-z]?(?:\([A-Za-z0-9]+\))*)*)")
SECTION_TOKEN = re.compile(r"(\d+)\.(\d+[A-Za-z]?)")


def instruction_targets(text, default_title):
    """Raw section references from an amendatory instruction, including lists such as `§§ 3.120 through 3.121`.

    Signals only: a named section is not necessarily changed, and a range names its two ends, not what lies between.
    """
    targets = []
    for m in SECTION_LIST.finditer(text):
        for token in SECTION_TOKEN.finditer(m.group(1)):
            ref = (default_title, token.group(1), f"{token.group(1)}.{token.group(2)}")
            if ref not in targets:
                targets.append(ref)
    return targets


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
                if self.loose is not None and self.loose["instr_open"]:
                    self.loose["instructions"][-1] += " " + text_of(child)  # e.g. "The revision reads as follows:"
                    continue
                self.close_loose()
                self.add_para(text_of(child))
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
            self.loose = {"instructions": [], "sections": [], "parts": [], "instr_open": False}
        if child.tag == "AMDPAR":
            text = text_of(child)
            if self.loose["instructions"] and not NUMBERED.match(text):
                self.loose["instructions"][-1] += " " + text  # "a. ..." sub-steps belong to "2. Amend § N by:"
            else:
                self.loose["instructions"].append(text)
            self.loose["instr_open"] = True
            return
        self.loose["instr_open"] = False
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
                      instructions=loose["instructions"])

    def add_unit(self, el, title, part, is_regtext, instructions=None):
        """Pair each SECTION with the amendatory instruction(s) that name it; keep unpaired instructions too."""
        self.unit_no += 1
        unit_id = f"{self.doc}:reg:{self.unit_no}"
        if instructions is None:
            instructions = []
            for x in el.findall("AMDPAR"):
                text = text_of(x)
                if instructions and not NUMBERED.match(text):
                    instructions[-1] += " " + text
                else:
                    instructions.append(text)
        items = []
        for text in instructions:
            if text:
                items.append({"text": text, "refs": [t[2] for t in instruction_targets(text, title)], "used": False})
        rows = []
        for sec in (x for x in el.iter() if x.tag == "SECTION"):
            sectno = sec.find("SECTNO")
            subj = sec.find("SUBJECT")
            ref = SECTNO.search(text_of(sectno)) if sectno is not None else None
            paragraphs = []
            for p in sec:
                if p.tag in ("P", "FP", "EXTRACT"):
                    paragraphs.append(text_of(p))
                elif p.tag == "GPOTABLE":
                    paragraphs.append(table_text(p))
            rows.append({"section_no": f"{ref.group(1)}.{ref.group(2)}" if ref else None,
                         "part_no": ref.group(1) if ref else part,
                         "subject": text_of(subj) if subj is not None else None, "paragraphs": paragraphs})
        auth = [text_of(a) for a in el.iter("AUTH")]
        if auth:
            rows.append({"section_no": None, "part_no": part, "subject": "Authority citation", "paragraphs": auth,
                         "authority": True})
        for r in rows:
            if r.get("authority"):
                matched = [i for i in items if re.search(r"authority citation", i["text"], re.I)]
            else:
                matched = [i for i in items if r["section_no"] and r["section_no"] in i["refs"]]
            if not matched and len(items) == 1:
                matched = items
            elif not matched and len(rows) == 1 and not r.get("authority"):
                matched = items
            for i in matched:
                i["used"] = True
            r["instruction"] = " ".join(i["text"] for i in matched)
        for i in items:  # instructions that edit text without carrying a SECTION body
            if not i["used"]:
                first = i["refs"][0] if i["refs"] else None
                rows.append({"section_no": first, "part_no": first.split(".")[0] if first else part, "subject": None,
                             "paragraphs": [], "instruction": i["text"]})
        for r in rows:
            instruction = r["instruction"]
            if not instruction and not r["paragraphs"]:
                continue
            operations = [name for name, rx in OPERATIONS if rx.search(instruction)]
            referenced = [t[2] for t in instruction_targets(instruction, title)]
            for chunk_no, chunk in enumerate(split_chunks(r["paragraphs"], UNIT_CHARS) or [[]], 1):
                leads = []
                for para in chunk:
                    m = PARA_LEAD.match(para)
                    if m and m.group(1) not in leads:
                        leads.append(m.group(1))
                self.units.append({
                    "unit_id": unit_id, "chunk": chunk_no, "title_no": title, "part_no": r["part_no"] or part,
                    "section_no": r["section_no"], "subject": r["subject"], "instruction": instruction,
                    "operations": operations, "instruction_sections": referenced, "paragraph_leads": leads,
                    "text": "\n".join(chunk), "fr_page": self.page, "from_regtext": is_regtext})
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
    `operations`/`instruction_sections` are regex signals from that instruction, not interpreted changes.
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
