"""Provision-level parser for eCFR renderer HTML saved by scripts/corpus_download.py."""
import re
from html.parser import HTMLParser
from urllib.parse import unquote

WS = re.compile(r"\s+")
SECTION_NO = re.compile(r"^(?:Section\s+)?([0-9]+\.[0-9A-Za-z\-]+)")


def clean(text):
    return WS.sub(" ", text.replace("\xa0", " ")).strip()


class Provision:
    __slots__ = ("locator", "kind", "heading", "title_no", "part_no", "section_no", "citation", "defined_term",
                 "parent", "parts", "char_start", "char_end", "raw_html_start", "raw_html_end", "subpart")

    def __init__(self, locator, kind, **kw):
        self.locator = locator
        self.kind = kind
        self.heading = kw.get("heading")
        self.title_no = kw.get("title_no")
        self.part_no = kw.get("part_no")
        self.section_no = kw.get("section_no")
        self.subpart = kw.get("subpart")
        self.citation = kw.get("citation")
        self.defined_term = kw.get("defined_term")
        self.parent = kw.get("parent")
        self.parts = []
        self.char_start = kw.get("char_start")
        self.char_end = None
        self.raw_html_start = None
        self.raw_html_end = None

    @property
    def text(self):
        return clean("".join(self.parts))


class EcfrParser(HTMLParser):
    """One Provision per anchored paragraph, definition, section, appendix item, footnote and table.

    A paragraph's own text excludes nested child paragraphs, so every provision is a distinct unit of
    evidence; the parent lead text is carried separately as retrieval context.
    """

    def __init__(self, source, title_no, part_no):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.title_no = title_no
        self.part_no = part_no
        self.line_starts = [0]
        for i, ch in enumerate(source):
            if ch == "\n":
                self.line_starts.append(i + 1)
        self.provisions = []
        self.seen = {}
        self.divs = []  # frames: {"tag","id","cls","prov","kind"}
        self.section_no = None
        self.section_heading = None
        self.subpart = None
        self.heading_target = None
        self.heading_buf = []
        self.in_table = 0
        self.table = None
        self.skip_depth = 0
        self.stats = {"images": 0, "tables": 0, "footnotes": 0, "duplicate_locators": 0}

    def char_pos(self):
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def unique(self, locator):
        n = self.seen.get(locator, 0) + 1
        self.seen[locator] = n
        if n > 1:
            self.stats["duplicate_locators"] += 1
            return f"{locator}~{n}"
        return locator

    def add(self, locator, kind, **kw):
        kw.setdefault("title_no", self.title_no)
        kw.setdefault("part_no", self.part_no)
        kw.setdefault("section_no", self.section_no)
        kw.setdefault("subpart", self.subpart)
        kw.setdefault("heading", self.section_heading)
        kw.setdefault("char_start", self.char_pos())
        p = Provision(self.unique(locator), kind, **kw)
        self.provisions.append(p)
        return p

    def owner(self):
        for frame in reversed(self.divs):
            if frame["prov"] is not None:
                return frame["prov"]
        return None

    def cite(self, data_title, locator):
        label = data_title or unquote(locator)
        if label.startswith("p-"):
            label = label[2:]
        label = label.replace("Appendix-", "Appendix ").replace("-to-Part-", " to Part ").replace("-", " ") \
            if label.startswith("Appendix") else label
        return f"{self.title_no} CFR {label}"

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "img":
            self.stats["images"] += 1
        if self.in_table:
            if tag == "table":
                self.in_table += 1
            elif tag == "caption":
                self.table["in_caption"] = True
            elif tag == "tr":
                self.table["row"] = []
            elif tag in ("td", "th"):
                self.table["cell"] = []
            return
        if tag == "table":
            self.in_table = 1
            self.stats["tables"] += 1
            self.table = {"start": self.char_pos(), "caption": [], "rows": [], "row": None, "cell": None,
                          "in_caption": False, "owner": self.owner()}
            return
        if tag == "div":
            ident = unquote(a.get("id", "")) if a.get("id") else None
            cls = a.get("class", "")
            frame = {"tag": tag, "id": ident, "cls": cls, "prov": None}
            if cls == "section" and ident:
                self.section_no = ident
                self.section_heading = None
                frame["prov"] = self.add(f"section-{ident}", "section", section_no=ident, heading=None)
                frame["section"] = True
            elif cls == "subpart" and ident:
                self.subpart = ident
                frame["prov"] = self.add(ident, "subpart", section_no=None)
            elif cls == "appendix" and ident:
                self.section_no = ident
                self.section_heading = None
                frame["prov"] = self.add(ident, "appendix", section_no=ident, heading=None)
                frame["section"] = True
            elif cls == "footnote" and ident:
                self.stats["footnotes"] += 1
                frame["prov"] = self.add(f"footnote-{ident}", "footnote", citation=f"{self.title_no} CFR Part {self.part_no} footnote {ident}")
            elif cls in ("authority", "source"):
                frame["prov"] = self.add(f"{cls}-part-{self.part_no}", cls,
                                         citation=f"{self.title_no} CFR Part {self.part_no} {cls}")
            elif cls == "part" and ident:
                frame["prov"] = self.add(ident, "part", section_no=None,
                                         citation=f"{self.title_no} CFR Part {self.part_no}")
            elif ident and ident.startswith("p-"):
                frame["prov"] = self.add(ident[2:], "paragraph", citation=None)
                frame["pending_p"] = True
            self.divs.append(frame)
            return
        if tag in ("h1", "h2", "h3", "h4"):
            owner = self.owner()
            if owner is not None and owner.kind in ("part", "subpart", "section", "appendix"):
                self.heading_target = owner
                self.heading_buf = []
                return
        if tag == "p" and a.get("id", "").startswith("p-"):
            ident = unquote(a["id"])
            if not (self.divs and self.divs[-1]["id"] == ident):  # leaf paragraph with no wrapping div
                prov = self.add(ident[2:], "definition" if a.get("data-term") == "true" else "paragraph",
                                citation=self.cite(a.get("data-title"), ident))
                prov.raw_html_start = self.char_pos()
                self.divs.append({"tag": "p", "id": ident, "cls": "", "prov": prov})
                return
        if tag == "p":
            owner = self.owner()
            if owner is not None and owner.kind == "paragraph" and self.divs[-1].get("pending_p"):
                self.divs[-1]["pending_p"] = False
                owner.raw_html_start = self.char_pos()
                if a.get("data-term") == "true":
                    owner.kind = "definition"
                owner.citation = self.cite(a.get("data-title"), self.divs[-1]["id"])
        if tag in ("p", "br", "li", "h4", "caption"):
            self.feed_text(" ")

    def handle_endtag(self, tag):
        if self.in_table:
            t = self.table
            if tag == "table":
                self.in_table -= 1
                if self.in_table == 0:
                    self.finish_table()
            elif tag == "caption":
                t["in_caption"] = False
            elif tag in ("td", "th") and t["cell"] is not None and t["row"] is not None:
                t["row"].append(clean("".join(t["cell"])))
                t["cell"] = None
            elif tag == "tr" and t["row"] is not None:
                if any(t["row"]):
                    t["rows"].append(t["row"])
                t["row"] = None
            return
        if tag in ("h1", "h2", "h3", "h4") and self.heading_target is not None:
            text = clean("".join(self.heading_buf))
            prov = self.heading_target
            if prov.kind in ("section", "appendix"):
                prov.heading = text
                self.section_heading = text
                prov.citation = self.cite_heading(prov, text)
            elif prov.kind in ("part", "subpart"):
                prov.heading = text
                prov.citation = prov.citation or f"{self.title_no} CFR Part {self.part_no} {text}"
            else:
                prov.parts.append(text)
            self.heading_target = None
            self.heading_buf = []
            return
        if tag in ("div", "p") and self.divs and self.divs[-1]["tag"] == tag:
            frame = self.divs.pop()
            if frame["prov"] is not None:
                frame["prov"].char_end = self.char_pos()
                frame["prov"].raw_html_end = self.char_pos()

    def cite_heading(self, prov, text):
        if prov.kind == "appendix":
            return f"{self.title_no} CFR {text.split('—')[0].strip()}"
        m = re.match(r"§\s*([0-9]+\.[0-9A-Za-z\-]+)", text)
        return f"{self.title_no} CFR {m.group(1)}" if m else f"{self.title_no} CFR {prov.section_no}"

    def feed_text(self, data):
        if self.in_table:
            t = self.table
            if t["in_caption"]:
                t["caption"].append(data)
            elif t["cell"] is not None:
                t["cell"].append(data)
            return
        if self.heading_target is not None:
            self.heading_buf.append(data)
            return
        owner = self.owner()
        if owner is not None:
            owner.parts.append(data)

    def handle_data(self, data):
        self.feed_text(data)

    def finish_table(self):
        t = self.table
        caption = clean("".join(t["caption"]))
        rows = [" | ".join(r) for r in t["rows"]]
        owner = t["owner"]
        number = self.stats["tables"]
        anchor = owner.locator if owner is not None else f"part-{self.part_no}"
        prov = Provision(self.unique(f"{anchor}#table-{number}"), "table", title_no=self.title_no,
                         part_no=self.part_no, section_no=self.section_no, subpart=self.subpart,
                         heading=self.section_heading, citation=f"{self.title_no} CFR {caption or 'table'}",
                         parent=anchor, char_start=t["start"])
        prov.char_end = self.char_pos()
        prov.raw_html_start, prov.raw_html_end = t["start"], self.char_pos()
        prov.parts = [caption + "\n" if caption else "", "\n".join(rows)]
        self.provisions.append(prov)
        self.table = None


def parse_ecfr(source, title_no, part_no):
    """Return (provisions, stats). `source` is the decoded HTML; offsets are character offsets into it."""
    parser = EcfrParser(source, title_no, part_no)
    parser.feed(source)
    parser.close()
    out = []
    lead = {}
    for p in parser.provisions:
        if p.kind == "paragraph" and not p.citation:
            p.citation = f"{title_no} CFR {p.locator}"
        text = p.text if p.kind != "table" else "\n".join(x for x in p.parts if x).strip()
        if not text and p.heading:
            text = p.heading  # headings are retrievable evidence for container provisions
        out.append((p, text))
        lead[p.locator] = text
    result = []
    for ordinal, (p, text) in enumerate(out):
        term = None
        if p.kind == "definition":
            term = re.split(r"\s{2,}|\s(?:means|is|has|are|refers)\b", text, maxsplit=1)[0][:160]
        parent = p.parent
        if parent is None and p.locator.count("(") > 1 or (parent is None and p.kind in ("paragraph", "definition")):
            cut = p.locator.rfind("(")
            while cut > 0:
                cand = p.locator[:cut]
                if cand in lead:
                    parent = cand
                    break
                cut = cand.rfind("(")
        context = p.heading or ""
        if parent and parent in lead and p.kind != "table":
            context = f"{context} / {lead[parent][:220]}".strip(" /")
        result.append({
            "locator": p.locator, "kind": p.kind, "citation": p.citation, "title_no": p.title_no,
            "part_no": p.part_no, "section_no": p.section_no, "subpart": p.subpart, "heading": p.heading,
            "context": context, "text": text, "defined_term": term, "parent_locator": parent,
            "ordinal": ordinal, "char_start": p.char_start, "char_end": p.char_end,
        })
    stats = dict(parser.stats)
    stats["provisions"] = len(result)
    stats["by_kind"] = {}
    for r in result:
        stats["by_kind"][r["kind"]] = stats["by_kind"].get(r["kind"], 0) + 1
    return result, stats
