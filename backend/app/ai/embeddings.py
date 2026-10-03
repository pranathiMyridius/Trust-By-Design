import os
from typing import Any

import requests

from app.ai.metering import metered_post
import truststore
from dotenv import load_dotenv

# Load variables from backend/.env -- same pattern as the other AI modules.
load_dotenv()
truststore.inject_into_ssl()

from app.ai import provider

# The configured provider (OpenAI or OpenRouter, app/ai/provider.py); both
# /embeddings endpoints are OpenAI-compatible. Default model:
# text-embedding-3-small (OPENAI_EMBEDDING_MODEL / OPENROUTER_EMBEDDING_MODEL).
OPENROUTER_API_KEY = provider.API_KEY
OPENROUTER_EMBEDDING_MODEL = provider.EMBEDDING_MODEL
OPENROUTER_EMBEDDINGS_URL = provider.EMBEDDINGS_URL

# text-embedding-3-small produces 1536-dimension vectors. If you change
# the embedding model to one with a different output size,
# update EMBEDDING_DIM (app/models/document_embedding.py's Vector column
# width) to match, and re-run migrate_pgvector_embeddings.py.
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


class EmbeddingError(RuntimeError):
    pass


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Returns one embedding vector per input string, in the same order.
    Raises EmbeddingError on any failure (missing API key, request
    failure, unexpected response shape) so callers can decide how to
    degrade (e.g. skip indexing this document rather than failing the
    whole upload).
    """

    if not texts:
        return []

    if not OPENROUTER_API_KEY:
        raise EmbeddingError(
            f"{provider.KEY_NAME} is not configured. Set it in backend/.env "
            "to enable semantic document search."
        )

    try:
        response = metered_post("EMBEDDING",
            OPENROUTER_EMBEDDINGS_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_EMBEDDING_MODEL,
                "input": texts,
            },
            timeout=60,
        )
    except requests.RequestException as exc:
        raise EmbeddingError(f"{provider.LABEL} embeddings request failed: {exc}")

    if response.status_code != 200:
        raise EmbeddingError(
            f"{provider.LABEL} embeddings request failed with "
            f"{response.status_code}: {response.text[:500]}"
        )

    payload: dict[str, Any] = response.json()

    try:
        rows = sorted(payload["data"], key=lambda row: row["index"])
        return [row["embedding"] for row in rows]
    except (KeyError, TypeError) as exc:
        raise EmbeddingError(
            f"Unexpected embeddings response shape from {provider.LABEL}: {exc}"
        )
