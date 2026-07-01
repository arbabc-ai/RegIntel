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


def get_embedding_function():
    """Return the active embedding function (Titan if USE_BEDROCK_EMBEDDINGS, else MiniLM)."""
    if USE_BEDROCK:
        return BedrockTitanEmbeddingFunction()
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    return SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
