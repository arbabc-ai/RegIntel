"""CLI for the RegIntel Phase 1 index: build | search | delta | eval.

Run as `python3 -I scripts/p1.py ...`; the repo root is added to sys.path explicitly because -I drops the
script directory from the import path.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from regintel_p1 import build as build_mod  # noqa: E402

DEFAULT_CORPUS = "/home/arbab/projects/regintel-corpus/phase1"
DEFAULT_DB = "/home/arbab/projects/regintel-corpus/phase1-index/regintel_p1.sqlite"
DEFAULT_AS_OF = "2026-10-29"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--as-of", default=DEFAULT_AS_OF, help="date used to label rule lifecycle state")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--corpus", default=DEFAULT_CORPUS)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--mode", default="hybrid", choices=["bm25", "topic", "hybrid"])
    s.add_argument("--scope", default="both", choices=["current", "future", "both"])
    s.add_argument("--part", action="append", help="title:part filter, e.g. 12:217")
    s.add_argument("-k", type=int, default=8)
    d = sub.add_parser("delta")
    d.add_argument("citation", help='e.g. "12 CFR 3.22" or "12 CFR Part 217"')
    e = sub.add_parser("eval")
    e.add_argument("--questions", default=str(Path(__file__).resolve().parent.parent / "eval" / "phase1" / "questions.json"))
    e.add_argument("--out", help="write full JSON results here")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
        conn, counts = build_mod.build(args.corpus, args.db, args.as_of)
        conn.close()
        print(json.dumps(counts, indent=1))
        return 0
    from regintel_p1 import search as search_mod
    index = search_mod.Index(args.db, args.as_of)
    if args.cmd == "search":
        out = index.answer(args.query, mode=args.mode, scope=args.scope, parts=args.part, k=args.k)
        print(json.dumps(out, indent=1, ensure_ascii=False))
    elif args.cmd == "delta":
        print(json.dumps(index.delta(args.citation), indent=1, ensure_ascii=False))
    elif args.cmd == "eval":
        from regintel_p1 import evaluate
        report = evaluate.run(index, args.questions)
        print(evaluate.render(report))
        if args.out:
            Path(args.out).write_text(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
