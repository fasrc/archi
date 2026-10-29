"""Embeddings served by an OpenAI-compatible HTTP endpoint (e.g. a GPU model server).

Unlike ``langchain_openai.OpenAIEmbeddings``, this sends plain text (no tiktoken
token IDs, which a non-OpenAI server would misread) and uses a separate model name for
search queries, so the server can apply each model's own query and document prompts.
"""

from typing import Any, Dict, List, Optional

import requests
from langchain_core.embeddings import Embeddings
from pydantic import BaseModel


class RemoteEmbeddings(BaseModel, Embeddings):
    """POST ``{"model", "input"}`` to ``{base_url}/embeddings``; vectors in input order."""

    base_url: str
    model: str
    query_model: Optional[str] = None
    api_key: Optional[str] = None
    batch_size: int = 32
    timeout: float = 120.0

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(
                self._embed(texts[start : start + self.batch_size], self.model)
            )
        return vectors

    def embed_query(self, text: str) -> List[float]:
        return self._embed([text], self.query_model or self.model)[0]

    def _embed(self, texts: List[str], model: str) -> List[List[float]]:
        headers: Dict[str, Any] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = requests.post(
            f"{self.base_url.rstrip('/')}/embeddings",
            json={"model": model, "input": texts},
            headers=headers,
            timeout=self.timeout,
        )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"embedding request to {self.base_url} failed: "
                f"HTTP {response.status_code} {response.text[:200]}"
            )
        items = response.json().get("data") or []
        if len(items) != len(texts):
            raise RuntimeError(
                f"embedding server returned {len(items)} vectors for {len(texts)} inputs"
            )
        return [
            item["embedding"] for item in sorted(items, key=lambda item: item["index"])
        ]
