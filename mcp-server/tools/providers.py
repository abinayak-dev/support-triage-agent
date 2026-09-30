"""Ollama and OpenAI-compatible LLM/embedding provider adapters."""
import os
from typing import TypedDict

import httpx


class ChatMessage(TypedDict):
    role: str
    content: str


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be configured for the selected provider")
    return value


def chat_completion(messages: list[ChatMessage]) -> str:
    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    if provider == "ollama":
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        model = os.getenv("LLM_MODEL") or os.getenv("CLASSIFY_MODEL", "tinyllama")
        response = httpx.post(
            f"{host}/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
                "messages": messages,
            },
            timeout=120.0,
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content")
    elif provider == "openai_compatible":
        base_url = _required("LLM_BASE_URL").rstrip("/")
        api_key = _required("LLM_API_KEY")
        model = _required("LLM_MODEL")
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=120.0,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    else:
        raise ValueError("LLM_PROVIDER must be 'ollama' or 'openai_compatible'")

    if not isinstance(content, str) or not content.strip():
        raise ValueError("Classification provider returned empty content")
    return content


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    provider = os.getenv("EMBEDDING_PROVIDER", "ollama").strip().lower()
    model = os.getenv("EMBED_MODEL", "nomic-embed-text")
    if provider == "ollama":
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        response = httpx.post(
            f"{host}/api/embed",
            json={"model": model, "input": texts},
            timeout=120.0,
        )
        if response.status_code == 404:
            vectors = []
            for text in texts:
                legacy_response = httpx.post(
                    f"{host}/api/embeddings",
                    json={"model": model, "prompt": text},
                    timeout=120.0,
                )
                legacy_response.raise_for_status()
                vectors.append(legacy_response.json()["embedding"])
        else:
            response.raise_for_status()
            vectors = response.json().get("embeddings")
    elif provider == "openai_compatible":
        base_url = _required("EMBEDDING_BASE_URL").rstrip("/")
        api_key = _required("EMBEDDING_API_KEY")
        response = httpx.post(
            f"{base_url}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "input": texts},
            timeout=120.0,
        )
        response.raise_for_status()
        items = response.json().get("data")
        if isinstance(items, list):
            items = sorted(items, key=lambda item: item.get("index", 0))
            vectors = [item.get("embedding") for item in items]
        else:
            vectors = None
    else:
        raise ValueError("EMBEDDING_PROVIDER must be 'ollama' or 'openai_compatible'")

    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise ValueError("Embedding provider returned an unexpected number of vectors")
    if any(
        not isinstance(vector, list)
        or not vector
        or any(not isinstance(value, (float, int)) for value in vector)
        for vector in vectors
    ):
        raise ValueError("Embedding provider returned an invalid vector")
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1:
        raise ValueError("Embedding provider returned vectors with inconsistent dimensions")
    return [[float(value) for value in vector] for vector in vectors]
