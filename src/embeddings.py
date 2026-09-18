"""Embedding-function factory: Amazon Titan on Bedrock, or local MiniLM.

Both ingest and retrieve MUST use the same embedder, so this is the single
source of truth. Toggle with USE_BEDROCK_EMBEDDINGS=1 (Titan) vs unset (MiniLM).

Titan is the in-stack production choice (it's what a Bedrock Knowledge Base /
OpenSearch vector store would use); MiniLM is free + offline for dev and CI.
"""
from __future__ import annotations

import json
import os

from dotenv import load_dotenv

load_dotenv()

USE_BEDROCK = os.environ.get("USE_BEDROCK_EMBEDDINGS", "").lower() in ("1", "true", "yes")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
TITAN_MODEL_ID = os.environ.get("TITAN_MODEL_ID", "amazon.titan-embed-text-v2:0")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
EMBED_PROVIDER = os.environ.get("EMBED_PROVIDER", "").lower()
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


class BedrockTitanEmbeddingFunction:
    """Chroma-compatible embedding function backed by Amazon Titan on Bedrock.

    Implements the callable protocol Chroma expects: __call__(input) -> list[list[float]].
    """

    def __init__(self, model_id: str = TITAN_MODEL_ID, region: str = AWS_REGION) -> None:
        import boto3

        self._client = boto3.client("bedrock-runtime", region_name=region)
        self._model_id = model_id

    def name(self) -> str:  # Chroma uses this to tag the collection's embedder
        return f"bedrock-titan:{self._model_id}"

    def __call__(self, input):  # noqa: A002 (Chroma's required param name)
        embeddings: list[list[float]] = []
        for text in input:
            resp = self._client.invoke_model(
                modelId=self._model_id,
                body=json.dumps({"inputText": text}),
            )
            payload = json.loads(resp["body"].read())
            embeddings.append(payload["embedding"])
        return embeddings


class OllamaEmbeddingFunction:
    """Chroma-compatible embedding function backed by a local Ollama model (free, offline)."""

    def __init__(self, model: str = OLLAMA_EMBED_MODEL, host: str = OLLAMA_HOST) -> None:
        self._model = model
        self._host = host

    def name(self) -> str:
        return f"ollama:{self._model}"

    def __call__(self, input):  # noqa: A002 (Chroma's required param name)
        import httpx

        resp = httpx.post(
            f"{self._host}/api/embed",
            json={"model": self._model, "input": list(input), "truncate": True},
            timeout=300.0,
        )
        resp.raise_for_status()
        return resp.json()["embeddings"]


def get_embedding_function():
    """Return the active embedding function: Titan, Ollama, or local MiniLM (default)."""
    if USE_BEDROCK:
        return BedrockTitanEmbeddingFunction()
    if EMBED_PROVIDER == "ollama":
        return OllamaEmbeddingFunction()
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    return SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
