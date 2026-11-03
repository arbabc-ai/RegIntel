"""S7-P1-55: delta() classifies every related future instrument by evidence level (contract p1-delta-provision-classification-1).

Only matched_provision is provision-level support. An instrument that amends the enclosing section but not the requested
paragraph, a part-level instruction, an unpaired candidate and a quoted reference are context or unresolved, never
support; a citation with no acquired instrument gets an explicit no_acquired_instrument record."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from regintel_p1 import build, evaluate, search
from test_p1 import asset

ECFR_3 = """<div class="part" id="part-3"><h1>PART 3—CAPITAL</h1>
<div class="section" id="3.12"><h4>§ 3.12 Community bank leverage ratio framework.</h4>
<div id="p-3.12(c)"><p class="indent-1" data-title="3.12(c)">(c) Treatment.</p>
<div id="p-3.12(c)(1)"><p class="indent-2" data-title="3.12(c)(1)">(1) A grace period.</p></div></div>
<div id="p-3.12(d)"><p class="indent-1" data-title="3.12(d)">(d) Other.</p></div></div>
<div class="section" id="3.22"><h4>§ 3.22 Adjustments.</h4>
<div id="p-3.22(a)"><p class="indent-1" data-title="3.22(a)">(a) General.</p></div></div></div>"""
ECFR_217 = """<div class="part" id="part-217"><h1>PART 217—CAPITAL</h1><div class="section" id="217.1"><h4>§ 217.1 Purpose.</h4>
<div id="p-217.1(a)"><p class="indent-1" data-title="217.1(a)">(a) Purpose.</p></div></div></div>"""


def fr(tag, amdpars, sections=()):
    body = "".join(f"<SECTION><SECTNO>&#xA7; {n}</SECTNO><SUBJECT>S.</SUBJECT>{''.join(f'<P>{p}</P>' for p in ps)}</SECTION>"
                   for n, ps in sections)
    return (f'<{tag}><PREAMB><CFR>12 CFR Part 3</CFR></PREAMB><SUPLINF><REGTEXT TITLE="12" PART="3">'
            + "".join(f"<AMDPAR>{a}</AMDPAR>" for a in amdpars) + f"{body}</REGTEXT></SUPLINF></{tag}>").encode()


FIXTURES = {  # key: (document type, xml)
    "PARA": ("Proposed Rule", fr("PRORULE", ["1. Amend &#xA7; 3.12 by revising paragraph (c)(1) to read as follows:"],
                                 [("3.12", ["(c) * * *", "(1) New c1."])])),
    "SECT": ("Rule", fr("RULE", ["1. Amend &#xA7; 3.12 by revising paragraph (d) to read as follows:"], [("3.12", ["(d) New d."])])),
    "PART": ("Rule", fr("RULE", ["1. Amend part 3 as follows:"])),
    "CAND": ("Rule", fr("RULE", ["1. Revise the following to read as follows:"], [("3.12", ["(c) * * *", "(1) Unpaired."])])),
    "REF": ("Rule", fr("RULE", ["1. Amend &#xA7; 3.22 by removing the words &#x201C;under &#xA7; 3.12&#x201D; and adding in their "
                                "place &#x201C;under this part&#x201D;."])),
}
KEYS = {k: f"fr:2026-8800{n}" for n, k in enumerate(FIXTURES, 1)}


class ProvisionClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "corpus"
        root.mkdir()
        meta = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01"}
        ecfr = {"source_class": "regulation", "publisher": "eCFR", "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28"}
        docs = {"ecfr:12:3": {**ecfr, "citation": "12 CFR Part 3", "title": "C", "asset": asset(root, "e3.html", ECFR_3.encode())},
                "ecfr:12:217": {**ecfr, "citation": "12 CFR Part 217", "title": "C", "asset": asset(root, "e217.html", ECFR_217.encode())}}
        for k, (dtype, xml) in FIXTURES.items():
            extra = {"comments_close_on": "2026-12-01"} if dtype == "Proposed Rule" else {"effective_on": "2027-02-01", "action": "Final rule."}
            docs[KEYS[k]] = {**meta, **extra, "document_number": KEYS[k][3:], "title": k, "document_type": dtype,
                             "asset": asset(root, f"{k}.xml", xml)}
        (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
        cls.db = str(Path(cls.tmp.name) / "d.sqlite")
        conn, _ = build.build(root, cls.db, "2026-10-29", log=lambda *_: None)
        conn.close()
        cls.index = search.Index(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.index.conn.close()
        cls.tmp.cleanup()

    def classes(self, citation):
        d = self.index.delta(citation)
        self.assertEqual(evaluate.delta_classification_failures(d), [])
        self.assertEqual(d["delta_contract"], "p1-delta-provision-classification-1")
        return d, {r["doc_key"]: r for r in d["instrument_classification"]}

    def test_paragraph_request_separates_every_evidence_level(self):
        d, c = self.classes("12 CFR 3.12(c)(1)")
        self.assertEqual({k: c[KEYS[k]]["classification"] for k in FIXTURES},
                         {"PARA": "matched_provision", "SECT": "matched_section_context", "PART": "matched_part_context",
                          "CAND": "unresolved_candidate", "REF": "reference_only"})
        self.assertEqual(d["provision_level_supported_instruments"], [KEYS["PARA"]])
        self.assertTrue(c[KEYS["PARA"]]["provision_level_delta_match"])

    def test_enclosing_section_amendment_is_never_paragraph_support(self):
        _, c = self.classes("12 CFR 3.12(c)(1)")
        r = c[KEYS["SECT"]]
        self.assertFalse(r["provision_level_support"])
        self.assertFalse(r["provision_level_delta_match"])
        self.assertIn("enclosing section", r["reason"])
        _, c = self.classes("12 CFR 3.12(d)")       # the same rule is provision-level only for the paragraph it names
        self.assertEqual(c[KEYS["SECT"]]["classification"], "matched_provision")
        self.assertEqual(c[KEYS["PARA"]]["classification"], "matched_section_context")

    def test_part_level_instrument_is_context_only(self):
        for cite in ("12 CFR 3.12(c)(1)", "12 CFR 3.12", "12 CFR 3.22(a)"):
            _, c = self.classes(cite)
            self.assertEqual(c[KEYS["PART"]]["classification"], "matched_part_context", cite)
            self.assertFalse(c[KEYS["PART"]]["provision_level_support"], cite)
        d, c = self.classes("12 CFR Part 3")
        self.assertEqual(d["provision_level_supported_instruments"], [])
        self.assertTrue(all(r["classification"] in ("matched_part_context", "unresolved_candidate", "reference_only") for r in c.values()))

    def test_candidate_stays_unresolved_and_reference_stays_reference(self):
        for cite in ("12 CFR 3.12(c)(1)", "12 CFR 3.12"):
            _, c = self.classes(cite)
            self.assertEqual(c[KEYS["CAND"]]["classification"], "unresolved_candidate")
            self.assertEqual(c[KEYS["REF"]]["classification"], "reference_only")
            self.assertFalse(c[KEYS["CAND"]]["provision_level_support"] or c[KEYS["REF"]]["provision_level_support"])
        _, c = self.classes("12 CFR 3.22(a)")       # REF amends 3.22 but names no paragraph: section context, not support
        self.assertEqual(c[KEYS["REF"]]["classification"], "matched_section_context")

    def test_no_acquired_instrument_is_an_explicit_gap(self):
        d, c = self.classes("12 CFR 217.1(a)")
        self.assertEqual(list(c), [None])
        self.assertEqual(c[None]["classification"], "no_acquired_instrument")
        self.assertFalse(c[None]["provision_level_support"])
        self.assertIn("not proof that none exists", c[None]["reason"])
        self.assertTrue(c[None]["current"]["resolved_at_requested_precision"])

    def test_section_request_counts_explicit_section_targets_only(self):
        d, c = self.classes("12 CFR 3.12")
        self.assertEqual(sorted(d["provision_level_supported_instruments"]), sorted([KEYS["PARA"], KEYS["SECT"]]))

    def test_records_keep_scopes_boundaries_and_proposal_labels(self):
        _, c = self.classes("12 CFR 3.12(c)(1)")
        for r in c.values():
            self.assertEqual((r["current"]["scope"], r["future"]["scope"]), ("current_baseline", "future_instrument"))
            self.assertEqual((r["applicability_status"], r["legal_validity"]), ("not_assessed", "not_determined"))
        self.assertEqual(c[KEYS["PARA"]]["document_type"], "Proposed Rule")
        self.assertFalse(c[KEYS["PARA"]]["lifecycle_state"].startswith(("final", "current")))

    def test_unclassifiable_requests_promote_nothing(self):
        d = self.index.delta("12 CFR 3.12 and 3.22")
        self.assertTrue(d["classification_status"].startswith("not_classified"))
        self.assertEqual((d["instrument_classification"], d["provision_level_supported_instruments"]), ([], []))
        self.assertEqual(evaluate.delta_classification_failures(d), [])

    def test_contract_check_rejects_promoted_context(self):
        d = self.index.delta("12 CFR 3.12(c)(1)")
        for cls in ("matched_section_context", "matched_part_context", "unresolved_candidate", "reference_only"):
            bad = copy.deepcopy(d)
            r = next(x for x in bad["instrument_classification"] if x["classification"] == cls)
            r["provision_level_support"] = True
            self.assertTrue(evaluate.delta_classification_failures(bad), cls)
        bad = copy.deepcopy(d)
        bad["instrument_classification"] = []
        self.assertTrue(evaluate.delta_classification_failures(bad))


if __name__ == "__main__":
    unittest.main()
