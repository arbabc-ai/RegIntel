"""Load documents → chunk → embed → ChromaDB index + BM25 index.

Run: python -m src.ingest
"""
from __future__ import annotations

import os
import pickle
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from rank_bm25 import BM25Okapi
from tqdm import tqdm

from src.embeddings import get_embedding_function

load_dotenv()

DATA_RAW = Path("data/raw")
CHROMA_PATH = Path(os.environ.get("CHROMA_DB_PATH", "./data/chroma"))
BM25_PATH = Path("data/bm25_index.pkl")
COLLECTION = "reg_corpus"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def load_documents() -> list[dict]:
    """Read every .txt, .md, .pdf in data/raw/. Return list of {source, text} dicts."""
    docs: list[dict] = []
    if not DATA_RAW.exists():
        raise SystemExit(
            f"No corpus found at {DATA_RAW}. "
            "Run `python -m scripts.download_corpus` first."
        )
    for p in sorted(DATA_RAW.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() in (".txt", ".md"):
            docs.append({"source": p.name, "text": p.read_text(encoding="utf-8", errors="replace")})
        elif p.suffix.lower() == ".pdf":
            try:
                reader = PdfReader(str(p))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                if text.strip():
                    docs.append({"source": p.name, "text": text})
            except Exception as e:  # noqa: BLE001
                print(f"  ! skipping {p.name}: {e}")
    print(f"Loaded {len(docs)} documents.")
    return docs


def chunk_documents(docs: list[dict]) -> list[dict]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks: list[dict] = []
    for doc in docs:
        for i, piece in enumerate(splitter.split_text(doc["text"])):
            chunks.append(
                {
                    "id": f"{doc['source']}::chunk-{i:04d}",
                    "text": piece,
                    "source": doc["source"],
                    "chunk_index": i,
                }
            )
    print(f"Produced {len(chunks)} chunks (size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}).")
    return chunks


def index_chroma(chunks: list[dict]) -> None:
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    embed_fn = get_embedding_function()
    # Replace collection on each run for determinism in this demo project.
    try:
        client.delete_collection(COLLECTION)
    except Exception:  # noqa: BLE001
        pass
    coll = client.create_collection(name=COLLECTION, embedding_function=embed_fn)

    # Batch to avoid memory spikes
    batch = 64
    for i in tqdm(range(0, len(chunks), batch), desc="embedding"):
        slc = chunks[i : i + batch]
        coll.add(
            ids=[c["id"] for c in slc],
            documents=[c["text"] for c in slc],
            metadatas=[{"source": c["source"], "chunk_index": c["chunk_index"]} for c in slc],
        )
    print(f"Indexed {coll.count()} chunks into Chroma at {CHROMA_PATH}.")


def index_bm25(chunks: list[dict]) -> None:
    """Tokenize and persist a BM25 index alongside Chroma. Used for hybrid retrieval."""
    BM25_PATH.parent.mkdir(parents=True, exist_ok=True)
    tokenized = [c["text"].lower().split() for c in chunks]
    bm25 = BM25Okapi(tokenized)
    with BM25_PATH.open("wb") as f:
        pickle.dump({"bm25": bm25, "chunks": chunks}, f)
    print(f"Saved BM25 index ({len(chunks)} chunks) to {BM25_PATH}.")


def main() -> None:
    docs = load_documents()
    if not docs:
        raise SystemExit("No documents to ingest.")
    chunks = chunk_documents(docs)
    index_chroma(chunks)
    index_bm25(chunks)
    print("Ingest complete.")


if __name__ == "__main__":
    main()
