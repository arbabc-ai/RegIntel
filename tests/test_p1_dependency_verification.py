"""Tests for S7-P1-DEPENDENCY-VERIFICATION-34 (F1): a newly built index persists the dependency-check contract id and the exact sha256
fingerprints of topic_dependencies.json and topics.json; loading validates them. A legacy index with no dependency tables, a parent-era
(p1-0.7) index with no verification metadata, or an index whose metadata is incomplete, obsolete or mismatched reports an explicit
`dependencies_unverified` diagnostic (unknown, never a confirmed missing source) and a matched acquired topic is partial_evidence when
results remain and otherwise missing_evidence. Existing indexes stay read-only; only a separately named fresh build verifies."""
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from regintel_p1 import build, evaluate, schema, search
from test_p1 import asset
from test_p1_delta_precision import fr
from test_p1_index_source_coverage import CAPITAL, ROOT, part_html
from test_p1_rulemaking_sources import OTHER, OTHER_XML

REPO = ROOT.parent
PARENT = "246e2e6a05766fda364be8aa548b15642bef2d73"
CBL = "community bank leverage ratio"
MODES = ("hybrid", "bm25", "topic")
SCOPES = ("current", "future", "both")
PARTS = (None, ["12:3"], ["12:217"], ["12:324"])
META = ("dependency_check_contract", "topic_dependencies_sha256", "topics_sha256")


def write_corpus(tmp, name, with_rule):
    """All four capital baseline parts (with their CBLR sections) and an independent proposal; fr:2026-08298 only when with_rule."""
    from test_p1_index_source_coverage import cbl_rule
    root = Path(tmp) / f"corpus-{name}"
    (root / "raw").mkdir(parents=True)
    docs = {f"ecfr:12:{p}": {"source_class": "regulation", "publisher": "eCFR", "citation": f"12 CFR Part {p}", "title": "Capital",
                              "publisher_issue_date": "2026-10-23", "publisher_up_to_date_as_of": "2026-10-28",
                              "asset": asset(root, f"e{p}.html", part_html(p).encode())} for p in CAPITAL}
    if with_rule:
        docs["fr:2026-08298"] = cbl_rule(root)
    docs[OTHER] = {"source_class": "rulemaking_candidate", "publisher": "Federal Register", "publication_date": "2026-10-01",
                   "document_type": "Proposed Rule", "comments_close_on": "2026-12-01", "action": "Proposed rule.",
                   "document_number": "2026-98002", "title": "CBLR alternative", "asset": asset(root, "o.xml", OTHER_XML)}
    (root / "manifest.json").write_text(json.dumps({"documents": docs, "failures": {}, "updated_at": "x"}))
    return root


def build_index(tmp, name, with_rule=True):
    root = write_corpus(tmp, name, with_rule)
    db = str(Path(tmp) / f"{name}.sqlite")
    conn, counts = build.build(root, db, "2026-10-29", log=lambda *_: None)
    conn.close()
    return db


def edit(db, sql, *args):
    conn = sqlite3.connect(db)
    conn.executescript(sql) if not args else conn.execute(sql, args)
    conn.commit()
    conn.close()


def gaps(out, kind):
    return [g for g in out["coverage_gaps"] if g.get("coverage") == kind]


class FreshBuildTests(unittest.TestCase):
    def test_fresh_build_persists_contract_and_exact_fingerprints(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = build_index(tmp, "fresh")
            meta = dict(sqlite3.connect(db).execute("SELECT key, value FROM index_meta").fetchall())
            self.assertEqual(meta["dependency_check_contract"], schema.DEPENDENCY_CHECK_CONTRACT)
            self.assertEqual(meta["topic_dependencies_sha256"], hashlib.sha256((ROOT / "topic_dependencies.json").read_bytes()).hexdigest())
            self.assertEqual(meta["topics_sha256"], hashlib.sha256((ROOT / "topics.json").read_bytes()).hexdigest())
            self.assertEqual(meta["schema_version"], "p1-0.8")
            ix = search.Index(db)
            self.assertEqual((ix.dependency_verification["status"], ix.dependency_verification["problems"]), ("verified", []))
            out = ix.answer(CBL, mode="hybrid", scope="current", k=10)
            self.assertEqual((out["status"], gaps(out, "dependencies_unverified")), ("evidence_found", []))
            self.assertEqual(out["index"]["dependency_verification"]["status"], "verified")
            ix.conn.close()

    def test_fresh_degraded_build_is_verified_and_reports_a_confirmed_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            ix = search.Index(build_index(tmp, "degraded", with_rule=False))
            self.assertEqual(ix.dependency_verification["status"], "verified")
            for mode in MODES:
                for scope in SCOPES:
                    out = ix.answer(CBL, mode=mode, scope=scope, k=1)
                    self.assertEqual(gaps(out, "dependencies_unverified"), [], (mode, scope))
                    self.assertEqual([m["doc_key"] for g in gaps(out, "source_unavailable") for m in g["unavailable_sources"]], ["fr:2026-08298"])
                    self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence", (mode, scope))
            ix.conn.close()

    def test_changed_inputs_make_a_previously_verified_index_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = build_index(tmp, "fresh")
            alt = Path(tmp) / "topic_dependencies.json"
            alt.write_text((ROOT / "topic_dependencies.json").read_text() + "\n")  # same declarations, different bytes
            real = Path(search.__file__).parent
            with mock.patch.object(schema, "dependency_fingerprints",
                                   lambda d, t: {"dependency_check_contract": schema.DEPENDENCY_CHECK_CONTRACT,
                                                 "topic_dependencies_sha256": schema.file_sha256(alt), "topics_sha256": schema.file_sha256(real / "topics.json")}):
                ix = search.Index(db)
                self.assertEqual(ix.dependency_verification["reason"], "dependency_fingerprint_mismatch")
                ix.conn.close()


class UnverifiedIndexTests(unittest.TestCase):
    VARIANTS = {
        "legacy_absent_tables": ("DROP TABLE source_status; DROP TABLE topic_source_gap; DELETE FROM index_meta WHERE key IN "
                                 "('dependency_check_contract','topic_dependencies_sha256','topics_sha256'); "
                                 "UPDATE index_meta SET value='p1-0.6' WHERE key='schema_version';", "dependency_tables_absent"),
        "parent_era_p1_0_7": ("DELETE FROM index_meta WHERE key IN ('dependency_check_contract','topic_dependencies_sha256','topics_sha256'); "
                              "UPDATE index_meta SET value='p1-0.7' WHERE key='schema_version';", "verification_metadata_missing"),
        "incomplete": ("DELETE FROM index_meta WHERE key='topics_sha256';", "verification_metadata_incomplete"),
        "obsolete_contract": ("UPDATE index_meta SET value='p1-dependency-check-0' WHERE key='dependency_check_contract';",
                              "obsolete_dependency_check_contract"),
        "mismatched_topics": (f"UPDATE index_meta SET value='{'0' * 64}' WHERE key='topics_sha256';", "dependency_fingerprint_mismatch"),
        "mismatched_dependencies": (f"UPDATE index_meta SET value='{'f' * 64}' WHERE key='topic_dependencies_sha256';", "dependency_fingerprint_mismatch"),
    }

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ix, cls.reason = {}, {}
        for name, (sql, reason) in cls.VARIANTS.items():
            db = build_index(cls.tmp.name, name)  # complete corpus: the stored tables claim nothing is missing
            edit(db, sql)
            cls.ix[name], cls.reason[name] = search.Index(db), reason

    @classmethod
    def tearDownClass(cls):
        for ix in cls.ix.values():
            ix.conn.close()
        cls.tmp.cleanup()

    def test_each_variant_is_unverified_with_its_own_reason(self):
        for name, ix in self.ix.items():
            v = ix.dependency_verification
            self.assertEqual((v["status"], v["reason"]), ("unverified", self.reason[name]), name)
            self.assertTrue(v["problems"], name)

    def test_matched_acquired_topic_is_never_evidence_found_and_is_not_a_confirmed_missing_source(self):
        for name, ix in self.ix.items():
            for q in (CBL, "minimum CET1 ratio"):
                for mode in MODES:
                    for scope in SCOPES:
                        for parts in PARTS:
                            for k in (10, 1):
                                out = ix.answer(q, mode=mode, scope=scope, parts=parts, k=k)
                                tag = (name, q, mode, scope, parts, k)
                                got = gaps(out, "dependencies_unverified")
                                self.assertTrue(got, tag)
                                self.assertEqual({g["verification"]["reason"] for g in got}, {self.reason[name]}, tag)
                                self.assertEqual(gaps(out, "source_unavailable"), [], tag)  # unknown, not confirmed missing
                                self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence", tag)
                                self.assertEqual(out["index"]["dependency_verification"]["status"], "unverified", tag)
                                self.assertEqual(out["applicability_status"], "not_assessed", tag)

    def test_citation_only_current_future_separation_delta_and_other_topics_are_unchanged(self):
        for name, ix in self.ix.items():
            for mode in ("exact", "hybrid"):
                cit = ix.answer("12 CFR 217.10(a)", mode=mode, scope="current", k=1)
                self.assertEqual((cit["status"], cit["citations"][0]["resolved"], gaps(cit, "dependencies_unverified")), ("evidence_found", True, []), (name, mode))
                fut = ix.answer("12 CFR 217.10(a)", mode=mode, scope="future", k=1)
                self.assertTrue(all(r["doc_key"].startswith("fr:") for r in fut["results"]), (name, mode))
            cur = ix.answer(CBL, mode="hybrid", scope="current", k=10)
            self.assertTrue(all(r["doc_key"].startswith("ecfr:") for r in cur["results"]), name)
            d = ix.delta("12 CFR 217.12")
            self.assertIn(OTHER, [g["doc_key"] for g in d["instruments"]], name)  # independent instrument unchanged
            self.assertEqual((d["legal_validity"], d["applicability_status"]), ("not_determined", "not_assessed"), name)
            self.assertEqual(gaps(ix.answer("CECL under ASC 326"), "dependencies_unverified"), [], name)  # not an acquired topic
            self.assertEqual({r[0] for r in ix.conn.execute("SELECT DISTINCT review_status FROM relationship")} - {"parsed_unreviewed", "regex_unreviewed"}, set(), name)

    def test_unverified_is_distinct_from_a_verified_index_that_confirms_a_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            verified_missing = search.Index(build_index(tmp, "vm", with_rule=False))
            unknown = self.ix["parent_era_p1_0_7"]
            a = verified_missing.answer(CBL, mode="hybrid", scope="both", k=10)
            b = unknown.answer(CBL, mode="hybrid", scope="both", k=10)
            self.assertEqual((bool(gaps(a, "source_unavailable")), bool(gaps(a, "dependencies_unverified"))), (True, False))
            self.assertEqual((bool(gaps(b, "source_unavailable")), bool(gaps(b, "dependencies_unverified"))), (False, True))
            verified_missing.conn.close()

    def test_opening_an_index_never_modifies_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = build_index(tmp, "ro")
            edit(db, "DELETE FROM index_meta WHERE key='topics_sha256';")
            before = hashlib.sha256(Path(db).read_bytes()).hexdigest()
            ix = search.Index(db)
            ix.answer(CBL, mode="hybrid", scope="both", k=10)
            ix.conn.close()
            self.assertEqual(hashlib.sha256(Path(db).read_bytes()).hexdigest(), before)
            with self.assertRaises(sqlite3.OperationalError):
                ix2 = search.Index(db)
                try:
                    ix2.conn.execute("DELETE FROM index_meta")
                finally:
                    ix2.conn.close()


@unittest.skipUnless(shutil.which("git") and subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", PARENT + "^{commit}"],
                                                             capture_output=True).returncode == 0, "parent commit not available")
class ParentEraIndexTests(unittest.TestCase):
    def test_index_built_by_the_parent_commit_is_unverified_and_no_longer_evidence_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "parent"
            export.mkdir()
            archive = subprocess.run(["git", "-C", str(REPO), "archive", PARENT], capture_output=True, check=True).stdout
            subprocess.run(["tar", "-x", "-C", str(export)], input=archive, check=True)
            root = write_corpus(tmp, "p", with_rule=False)  # all capital parts and CBLR sections, an independent proposal, no fr:2026-08298
            db = str(Path(tmp) / "parent.sqlite")
            code = ("import sys; sys.path.insert(0, %r); from regintel_p1 import build; c, _ = build.build(%r, %r, '2026-10-29', log=lambda *a: None); c.close()"
                    % (str(export), str(root), db))
            subprocess.run([sys.executable, "-c", code], check=True, cwd=str(export))
            self.assertEqual(dict(sqlite3.connect(db).execute("SELECT key, value FROM index_meta")).get("schema_version"), "p1-0.7")
            ix = search.Index(db)  # read with the current code
            self.assertEqual(ix.dependency_verification["reason"], "verification_metadata_missing")
            for scope in SCOPES:
                out = ix.answer(CBL, mode="hybrid", scope=scope, k=1)
                self.assertNotEqual(out["status"], "evidence_found", scope)
                self.assertEqual(out["status"], "partial_evidence" if out["results"] else "missing_evidence", scope)
                self.assertTrue(gaps(out, "dependencies_unverified"), scope)
            ix.conn.close()


class EvaluatorGroupTests(unittest.TestCase):
    def test_group_is_registered_in_the_aggregate_and_fails_when_the_diagnostic_is_hidden(self):
        self.assertIn("dependency_verification", evaluate.REGRESSION_GROUPS)
        reg = json.loads((REPO / "eval/phase1/regressions.json").read_text())
        self.assertEqual([x["id"] for x in reg["dependency_verification"]], ["DV01", "DV02", "DV03", "DV04", "DV05"])
        with tempfile.TemporaryDirectory() as tmp:
            ix = search.Index(build_index(tmp, "e"))
            for x in reg["dependency_verification"]:
                if x["kind"] == "corpus":
                    self.assertTrue(evaluate.check_dependency_verification(ix, x)["passed"], x["id"])
            unverified = next(x for x in reg["dependency_verification"] if x["id"] == "DV03")
            self.assertTrue(evaluate.check_dependency_verification(ix, unverified)["passed"])
            verified = {"status": "verified", "reason": None, "problems": [], "contract": schema.DEPENDENCY_CHECK_CONTRACT,
                        "index_schema_version": None, "recorded": {}}
            with mock.patch.object(search.Index, "_verify_dependencies", lambda self_: verified):  # the starting behaviour: always trusted
                self.assertFalse(evaluate.check_dependency_verification(ix, unverified)["passed"])
                self.assertTrue(evaluate.check_dependency_verification(ix, reg["dependency_verification"][0])["passed"])
            ix.conn.close()


if __name__ == "__main__":
    unittest.main()
