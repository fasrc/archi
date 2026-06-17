"""Curated benchmark query sets for retrieval evaluation.

This package holds query-set data files plus a lightweight loader. It is kept
free of heavy runtime dependencies (no secrets, Postgres, ragas, or langchain
imports) so it can be imported by both the benchmark harness
(``src/bin/service_benchmark.py``) and unit tests without spinning up a
deployment.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

#: Directory containing the query-set data files.
QUERY_SET_DIR = Path(__file__).resolve().parent

#: Curated query set exercising title- and filename-aware retrieval. Every
#: query's keyword appears only in the document title (``display_name``) or
#: filename (``file_name``), never in the chunk body.
TITLE_AWARE_QUERY_SET_PATH = QUERY_SET_DIR / "title_aware_query_set.json"

#: Query whose keyword appears only in the document title.
CATEGORY_TITLE_ONLY = "title_only"

#: Query whose keyword appears only in the document filename.
CATEGORY_FILENAME_ONLY = "filename_only"

#: The categories carried by the title-aware query set.
TITLE_AWARE_CATEGORIES = (CATEGORY_TITLE_ONLY, CATEGORY_FILENAME_ONLY)

#: Match field expected for each category (title -> display_name, filename ->
#: file_name), mirroring the metadata fields the benchmark harness matches on.
CATEGORY_MATCH_FIELDS = {
    CATEGORY_TITLE_ONLY: "display_name",
    CATEGORY_FILENAME_ONLY: "file_name",
}

#: Fields every query item must define.
REQUIRED_QUERY_FIELDS = ("question", "category", "sources", "source_match_field")


def _validate_query_item(item: Any, index: int) -> None:
    if not isinstance(item, dict):
        raise ValueError(
            f"Query item at index {index} must be a dict, got {type(item).__name__}."
        )
    missing = [field for field in REQUIRED_QUERY_FIELDS if not item.get(field)]
    if missing:
        raise ValueError(
            f"Query item at index {index} is missing required field(s): {', '.join(missing)}."
        )
    category = item["category"]
    if category not in TITLE_AWARE_CATEGORIES:
        raise ValueError(
            f"Query item at index {index} has unknown category '{category}'; "
            f"expected one of {TITLE_AWARE_CATEGORIES}."
        )
    expected_field = CATEGORY_MATCH_FIELDS[category]
    if item["source_match_field"] != expected_field:
        raise ValueError(
            f"Query item at index {index} (category '{category}') must use "
            f"source_match_field '{expected_field}', got '{item['source_match_field']}'."
        )


def _validate_query_set(query_set: Any) -> None:
    if not isinstance(query_set, list) or not query_set:
        raise ValueError("A query set must be a non-empty list of query items.")
    for index, item in enumerate(query_set):
        _validate_query_item(item, index)
    categories = {item["category"] for item in query_set}
    for category in TITLE_AWARE_CATEGORIES:
        if category not in categories:
            raise ValueError(
                f"The title-aware query set must include at least one '{category}' query."
            )


def load_title_aware_query_set(
    path: Path = TITLE_AWARE_QUERY_SET_PATH,
) -> List[Dict[str, Any]]:
    """Load and validate the curated title-/filename-aware benchmark query set.

    Returns a list of query items, each a dict with ``question``, ``category``,
    ``sources`` and ``source_match_field`` keys. Raises ``ValueError`` if the
    file's contents do not satisfy the query-set contract.
    """
    with open(path, "r") as f:
        query_set = json.load(f)
    _validate_query_set(query_set)
    return query_set


def merge_query_sets(
    base: Sequence[Dict[str, Any]],
    extra: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return a new list with ``extra`` query items appended to ``base``.

    Neither input is mutated. Used by the harness to add the title-aware query
    set to a deployment's existing ``QandA.txt`` query set.
    """
    return list(base) + list(extra)
