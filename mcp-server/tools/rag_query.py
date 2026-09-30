"""Knowledge-base retrieval: embed query with Ollama, search Qdrant."""
import logging
import os
import threading

from qdrant_client import QdrantClient

from .errors import TriageError
from .providers import embed_texts

logger = logging.getLogger("triage.rag")

MAX_TOP_K = 10
MAX_QUERY_LEN = 2000

_client = None
_client_lock = threading.Lock()


def _get_client() -> QdrantClient:
    """Lazy Qdrant client (created on first use, not at import time)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = QdrantClient(
                url=os.getenv("QDRANT_HOST", "http://localhost:6333"),
                api_key=os.getenv("QDRANT_API_KEY") or None,
                timeout=15,
            )
        return _client


def _embed(text: str) -> list[float]:
    try:
        return embed_texts([text])[0]
    except Exception:
        logger.exception("Embedding provider request failed")
        raise TriageError("Embedding service unavailable", 502)


def query_knowledge_base(query: str, top_k: int = 3) -> dict:
    if not isinstance(query, str) or not query.strip():
        raise TriageError("query must be a non-empty string", 422)
    if len(query) > MAX_QUERY_LEN:
        raise TriageError(f"query must be at most {MAX_QUERY_LEN} characters", 422)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= MAX_TOP_K:
        raise TriageError(f"top_k must be an integer between 1 and {MAX_TOP_K}", 422)

    vector = _embed(query)
    collection = os.getenv("QDRANT_COLLECTION", "knowledge_base")

    try:
        client = _get_client()
        if not client.collection_exists(collection):
            logger.error("Qdrant collection '%s' does not exist - run ingest.py", collection)
            raise TriageError("Knowledge base not available (not ingested yet)", 503)
        response = client.query_points(
            collection_name=collection, query=vector, limit=top_k, with_payload=True
        )
    except TriageError:
        raise
    except Exception:
        logger.exception("Qdrant query failed")
        raise TriageError("Knowledge base service unavailable", 502)

    results = []
    for p in response.points:
        payload = p.payload or {}
        results.append(
            {
                "text": payload.get("text", ""),
                "source": payload.get("source", "unknown"),
                "score": round(float(p.score), 4),
            }
        )
    return {"results": results}