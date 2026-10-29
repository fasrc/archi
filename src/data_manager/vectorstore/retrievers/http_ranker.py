"""HTTP reranker backend with FlashRank's ``Ranker.rerank`` contract.

The hierarchical retriever passes a request carrying ``query`` and ``passages``
(``{"id", "text", ...}``) and reads ``id`` and ``score`` from the returned list, best
first. This class sends the texts to ``{url}/rerank`` (Cohere/Jina/vLLM request shape)
so a GPU-hosted cross-encoder can replace FlashRank without touching the retriever.
"""

from typing import Any, Dict, List

import requests


class HttpRanker:
    """Score passages with a remote reranker; raise on any failed or short response."""

    def __init__(self, url: str, model: str, timeout: float = 60.0):
        self.url = url
        self.model = model
        self.timeout = timeout

    def rerank(self, request: Any) -> List[Dict[str, Any]]:
        passages = list(request.passages)
        if not passages:
            return []
        response = requests.post(
            f"{self.url.rstrip('/')}/rerank",
            json={
                "model": self.model,
                "query": request.query,
                "documents": [passage["text"] for passage in passages],
            },
            timeout=self.timeout,
        )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"rerank request to {self.url} failed: "
                f"HTTP {response.status_code} {response.text[:200]}"
            )
        scores = {
            item["index"]: float(item["relevance_score"])
            for item in response.json().get("results") or []
        }
        if set(scores) != set(range(len(passages))):
            raise RuntimeError(
                f"rerank server scored {len(scores)} of {len(passages)} candidates"
            )
        ranked = [{**passage, "score": scores[i]} for i, passage in enumerate(passages)]
        return sorted(ranked, key=lambda item: item["score"], reverse=True)
