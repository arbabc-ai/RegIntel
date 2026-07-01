"""Hybrid retrieval: dense (Chroma) + BM25, fused via Reciprocal Rank Fusion.

Programmatic use:
    from src.retrieve import retrieve
    hits = retrieve("What minimum LCR must a covered institution maintain?", k=5)
    # hits = [{"text": ..., "source": ..., "chunk_index": ..., "score": ...}, ...]
"""
from __future__ import annotations

import os
import pickle
from pathlib import Path

import chromadb
from dotenv import load_dotenv

from src.embeddings import get_embedding_function

load_dotenv()

CHROMA_PATH = Path(os.environ.get("CHROMA_DB_PATH", "./data/chroma"))
BM25_PATH = Path("data/bm25_index.pkl")
COLLECTION = "reg_corpus"

# RRF: see Cormack, Clarke, Buettcher (2009).
# score(d) = sum over rankers r: 1 / (k_rrf + rank_r(d))
K_RRF = 60


def _load_chroma():
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    embed_fn = get_embedding_function()
    return client.get_collection(name=COLLECTION, embedding_function=embed_fn)


def _load_bm25():
    with BM25_PATH.open("rb") as f:
        payload = pickle.load(f)
    return payload["bm25"], payload["chunks"]


def _dense_search(coll, query: str, k: int) -> list[dict]:
    res = coll.query(query_texts=[query], n_results=k)
    out = []
    for doc, meta, doc_id, dist in zip(
        res["documents"][0],
        res["metadatas"][0],
        res["ids"][0],
        res["distances"][0],
    ):
        out.append(
            {
                "id": doc_id,
                "text": doc,
                "source": meta["source"],
                "chunk_index": meta["chunk_index"],
                "dense_score": 1.0 - dist,  # rough convert distance → similarity
            }
        )
    return out


def _bm25_search(bm25, chunks: list[dict], query: str, k: int) -> list[dict]:
    tokenized = query.lower().split()
    scores = bm25.get_scores(tokenized)
    indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:k]
    out = []
    for rank, (idx, score) in enumerate(indexed):
        c = chunks[idx]
        out.append(
            {
                "id": c["id"],
                "text": c["text"],
                "source": c["source"],
                "chunk_index": c["chunk_index"],
                "bm25_score": float(score),
            }
        )
    return out


def _rrf_fuse(dense: list[dict], sparse: list[dict], k: int) -> list[dict]:
    rrf: dict[str, dict] = {}
    for rank, hit in enumerate(dense):
        rrf.setdefault(hit["id"], {**hit, "rrf": 0.0})
        rrf[hit["id"]]["rrf"] += 1.0 / (K_RRF + rank + 1)
    for rank, hit in enumerate(sparse):
        if hit["id"] in rrf:
            rrf[hit["id"]]["rrf"] += 1.0 / (K_RRF + rank + 1)
        else:
            rrf[hit["id"]] = {**hit, "rrf": 1.0 / (K_RRF + rank + 1)}
    fused = sorted(rrf.values(), key=lambda x: x["rrf"], reverse=True)[:k]
    return [{**h, "score": h["rrf"]} for h in fused]


def retrieve(query: str, k: int = 5, mode: str = "hybrid") -> list[dict]:
    """Return top-k chunks for the query.

    mode = "dense" | "bm25" | "hybrid" (default).
    """
    if mode == "dense":
        coll = _load_chroma()
        return _dense_search(coll, query, k)
    if mode == "bm25":
        bm25, chunks = _load_bm25()
        return _bm25_search(bm25, chunks, query, k)
    # hybrid
    coll = _load_chroma()
    bm25, chunks = _load_bm25()
    dense_hits = _dense_search(coll, query, k=20)
    sparse_hits = _bm25_search(bm25, chunks, query, k=20)
    return _rrf_fuse(dense_hits, sparse_hits, k=k)


if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) or "What minimum liquidity coverage ratio must a covered institution maintain?"
    for i, hit in enumerate(retrieve(query, k=5), 1):
        print(f"\n#{i}  source={hit['source']}  score={hit.get('score', 0):.4f}")
        print(hit["text"][:300] + ("..." if len(hit["text"]) > 300 else ""))
