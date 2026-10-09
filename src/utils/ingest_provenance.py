"""Ingest-configuration provenance: the snapshot, and drift against it.

The status board must be able to say a flag was used *at ingest*, not merely
*currently configured*. ``static_config`` only ever holds current config, so a
redeploy that flips a flag without re-ingesting would otherwise make the board
claim the corpus was built with a setting it was not built with.

:func:`build_ingest_config_snapshot` is the single definition of "the
ingest-affecting flags". It is deliberately pure — no environment, no database —
so the write path (the data manager, during a run) and the read path (the chat
app, comparing against current config) cannot disagree about what a flag is
called or what it defaults to.

Every default here mirrors the value the ingest path itself applies. They are
restated rather than imported because importing the data manager into a shared
util would drag the whole ingest dependency tree into the web process; the unit
tests name the originating call site for each one so a drift is caught.
"""

from typing import Any, Dict, List, Mapping, Optional

# Declared key order. Drift is reported in this order so a rendered warning does
# not reshuffle between page loads.
INGEST_CONFIG_KEYS = (
    "html_to_markdown",
    "categorization",
    "chunking_strategy",
    "child_chunk_overlap",
    "effective_chunking",
    "embedding_model",
    "embedding_dimensions",
    "chunk_size",
    "chunk_overlap",
    "distance_metric",
    "sitemap_min_pages",
)

# Defaults, each mirroring the ingest path's own fallback:
#   html_to_markdown / categorization -> build_persistence_service
#   chunking_strategy                 -> _resolve_chunking_strategy
#   child_chunk_overlap               -> _resolve_chunk_overlap
#   sitemap_min_pages                 -> scraper_manager's _as_int(..., 1)
#   the rest                          -> the config-seed fallbacks
_DEFAULT_EMBEDDING_MODEL = "HuggingFaceEmbeddings"
_DEFAULT_EMBEDDING_DIMENSIONS = 384

# Per-model dimension defaults, mirroring the `default_dimensions` local table in
# src/data_manager/vectorstore/manager.py (manager.__init__).  The parity test
# parses that file with `ast` and asserts equality, so a drift there is caught.
_DEFAULT_EMBEDDING_DIMENSIONS_BY_MODEL = {
    "all-MiniLM-L6-v2": 384,
    "OpenAIEmbeddings": 1536,
    "HuggingFaceEmbeddings": 384,
}
_DEFAULT_CHUNK_SIZE = 1000
_DEFAULT_CHUNK_OVERLAP = 150
_DEFAULT_DISTANCE_METRIC = "cosine"
_DEFAULT_CHUNKING_STRATEGY = "sentence"
_DEFAULT_CHILD_CHUNK_OVERLAP = 20
_DEFAULT_SITEMAP_MIN_PAGES = 1

# Restated from src/data_manager/vectorstore/node_parsing.py (module docstring):
# the hierarchical path's own defaults and strategy names, so a drift there is
# caught by tests rather than silently reported wrong here.
_MARKDOWN_STRATEGY = "markdown"
_DEFAULT_PARENT_CHUNK_SIZE = 2048
_DEFAULT_CHILD_CHUNK_SIZE = 512


def _mapping(value: Any) -> Dict[str, Any]:
    """Return ``value`` as a dict, or an empty dict when it is not a mapping.

    Provenance must never be able to fail an ingest, so a malformed config
    degrades to defaults instead of raising.
    """
    return dict(value) if isinstance(value, Mapping) else {}


def _is_non_negative_int(value: Any) -> bool:
    """Return whether ``value`` is a plain non-negative ``int`` (not ``bool``)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _clamped_overlap(chunk_size: int, overlap: int) -> int:
    """Restated from ``node_parsing._clamped_overlap``: clamp to at most half ``chunk_size``."""
    return max(0, min(overlap, chunk_size // 2))


def effective_chunking(data_manager_config: Any) -> Dict[str, Any]:
    """Report the chunking parameters the ingest path actually applies.

    Mirrors the manager's path selection (``manager.py:169-172``): the
    hierarchical path is used for the ``sentence`` and ``markdown`` strategies,
    the ``CharacterTextSplitter`` path otherwise (including an unrecognized
    strategy, which the manager also routes to the character splitter). Never
    raises: an invalid parent/child size or overlap is reported unclamped
    rather than failing provenance over a config the manager itself would
    refuse (see ``_resolve_chunk_overlap``).
    """
    dm = _mapping(data_manager_config)
    chunking = _mapping(dm.get("chunking"))
    strategy = chunking.get("strategy", _DEFAULT_CHUNKING_STRATEGY)

    if strategy in (_DEFAULT_CHUNKING_STRATEGY, _MARKDOWN_STRATEGY):
        parent = chunking.get("parent_chunk_size", _DEFAULT_PARENT_CHUNK_SIZE)
        child = chunking.get("child_chunk_size", _DEFAULT_CHILD_CHUNK_SIZE)
        overlap = chunking.get("chunk_overlap")
        if overlap is None:
            overlap = _DEFAULT_CHILD_CHUNK_OVERLAP

        if (
            _is_non_negative_int(parent)
            and _is_non_negative_int(child)
            and _is_non_negative_int(overlap)
        ):
            sentence_overlap = _clamped_overlap(min(parent, child), overlap)
            markdown_overlap = _clamped_overlap(child, overlap)
        else:
            sentence_overlap = markdown_overlap = overlap

        result = {
            "path": "hierarchical",
            "strategy": strategy,
            "parent_chunk_size": parent,
            "child_chunk_size": child,
            "child_chunk_overlap": (
                markdown_overlap if strategy == _MARKDOWN_STRATEGY else sentence_overlap
            ),
        }
        if strategy == _MARKDOWN_STRATEGY:
            # Only Markdown files use the markdown parser; every other file falls
            # back to the sentence parser (resolve_effective_strategy), which
            # clamps on min(parent, child).
            result["non_markdown_child_chunk_overlap"] = sentence_overlap
        return result

    return {
        "path": "character",
        "strategy": strategy,
        "chunk_size": dm.get("chunk_size", _DEFAULT_CHUNK_SIZE),
        "chunk_overlap": dm.get("chunk_overlap", _DEFAULT_CHUNK_OVERLAP),
    }


def build_ingest_config_snapshot(data_manager_config: Any) -> Dict[str, Any]:
    """Build the ingest-flag snapshot from a ``data_manager`` config mapping.

    Pure: reads no environment and opens no connection.
    """
    dm = _mapping(data_manager_config)
    processing = _mapping(dm.get("processing"))
    chunking = _mapping(dm.get("chunking"))
    html_cfg = _mapping(processing.get("html_to_markdown"))
    cat_cfg = _mapping(processing.get("categorization"))

    sitemap_cfg = _mapping(
        _mapping(_mapping(dm.get("sources")).get("links")).get("sitemap")
    )

    embedding_model = dm.get("embedding_name") or _DEFAULT_EMBEDDING_MODEL
    embedding_entry = _mapping(
        _mapping(dm.get("embedding_class_map")).get(embedding_model)
    )

    return {
        "html_to_markdown": bool(html_cfg.get("enabled", True)),
        "categorization": bool(cat_cfg.get("enabled", False)),
        "chunking_strategy": chunking.get("strategy", _DEFAULT_CHUNKING_STRATEGY),
        # None (an empty YAML value) resolves to the default, as in the manager.
        "child_chunk_overlap": (
            _DEFAULT_CHILD_CHUNK_OVERLAP
            if chunking.get("chunk_overlap") is None
            else chunking["chunk_overlap"]
        ),
        "effective_chunking": effective_chunking(dm),
        "embedding_model": embedding_model,
        "embedding_dimensions": embedding_entry.get(
            "dimensions",
            _DEFAULT_EMBEDDING_DIMENSIONS_BY_MODEL.get(
                embedding_model, _DEFAULT_EMBEDDING_DIMENSIONS
            ),
        ),
        "chunk_size": dm.get("chunk_size", _DEFAULT_CHUNK_SIZE),
        "chunk_overlap": dm.get("chunk_overlap", _DEFAULT_CHUNK_OVERLAP),
        "distance_metric": dm.get("distance_metric", _DEFAULT_DISTANCE_METRIC),
        "sitemap_min_pages": sitemap_cfg.get("min_pages", _DEFAULT_SITEMAP_MIN_PAGES),
    }


def compare_ingest_config(
    current: Any, at_ingest: Optional[Any]
) -> List[Dict[str, Any]]:
    """Report, per flag, where current config differs from the ingest snapshot.

    Returns one entry per differing key, carrying the value now and the value
    at ingest, because the operator asked to see which flag changed rather than
    a bare "something changed".

    An empty or missing ``at_ingest`` yields no drift: a run recorded before the
    snapshot existed must not read as though every flag had changed. For the
    same reason a declared key absent from ``at_ingest`` is skipped: the builder
    always emits every declared key, so its absence means the snapshot predates
    the key, not that the flag changed.
    """
    recorded = _mapping(at_ingest)
    if not recorded:
        return []

    live = _mapping(current)
    keys = [key for key in INGEST_CONFIG_KEYS if key in recorded]
    keys += sorted((set(live) | set(recorded)) - set(INGEST_CONFIG_KEYS))

    drift: List[Dict[str, Any]] = []
    for key in keys:
        now = live.get(key)
        then = recorded.get(key)
        if now != then:
            drift.append({"key": key, "current": now, "at_ingest": then})
    return drift
