"""Corpus provenance for a QA run (#570).

A QA run retrieves only while it answers, in ``run()`` and in the fresh
attempts of ``retry()``. So the two corpus readings bracket that phase, and
``score()``, which can run hours later, copies them instead of taking its own.
The readings use the same v2 routine as the golden-set harness, and the start
guard is the harness's guard, so a QA run and a RAGAS arm over one stack agree.
"""

from contextlib import contextmanager
from typing import Any, Dict, Iterator, Mapping, Optional

import psycopg2

from src.archi.utils.vectorstore_connector import postgres_connection_params
from src.utils.benchmark_provenance import (
    collection_readiness,
    live_corpus_fingerprint,
    retrieval_identity,
    retrieval_record,
)

SEARCH_TOOL = "search_vectorstore_hybrid"
PROVENANCE_KEYS = (
    "retrieval_identity",
    "corpus_fingerprint_before",
    "corpus_fingerprint",
    "corpus_unchanged_at_endpoints",
)
UNAVAILABLE = "<unavailable:"


class DirectPool:
    """The one ``ConnectionPool`` method the readings need, over psycopg2."""

    def __init__(self, params: Dict[str, Any]):
        self._params = params

    @contextmanager
    def get_connection(self) -> Iterator[Any]:
        connection = psycopg2.connect(**self._params)
        try:
            yield connection
        finally:
            connection.close()


def direct_pool(config: Mapping[str, Any]) -> DirectPool:
    return DirectPool(postgres_connection_params(config))


def uses_search(spec: Any) -> bool:
    return SEARCH_TOOL in (getattr(spec, "tools", None) or [])


def _reading(config: Mapping[str, Any]) -> str:
    """One fingerprint reading; a failure is a marker, never an exception."""
    try:
        return live_corpus_fingerprint(direct_pool(config), config)
    except Exception as exc:  # noqa: BLE001 - provenance is never fatal
        return f"{UNAVAILABLE} {exc}>"


def start_readings(config: Mapping[str, Any], spec: Any) -> Dict[str, Any]:
    """Run the start guard and take the first reading, before any question.

    Raises ``CollectionNotReadyError`` from the guard: no attempt has run, so
    nothing is lost.
    """
    if not uses_search(spec):
        return {"retrieval_identity": None, "corpus_fingerprint_before": None}
    identity = retrieval_identity(config)
    readiness = collection_readiness(direct_pool(config), identity)
    return {
        "retrieval_identity": retrieval_record(identity, readiness),
        "corpus_fingerprint_before": _reading(config),
    }


def end_readings(
    config: Mapping[str, Any], spec: Any, before: Optional[str]
) -> Dict[str, Any]:
    """Take the second reading when the attempts finish, and compare."""
    if not uses_search(spec):
        return {"corpus_fingerprint": None, "corpus_unchanged_at_endpoints": None}
    after = _reading(config)
    readable = all(
        value is not None and not value.startswith(UNAVAILABLE)
        for value in (before, after)
    )
    return {
        "corpus_fingerprint": after,
        "corpus_unchanged_at_endpoints": (before == after) if readable else None,
    }


def summary_fields(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """The readings and identity scoring copies into ``summary.provenance``."""
    return {key: manifest.get(key) for key in PROVENANCE_KEYS}
