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
#   sitemap_min_pages                 -> scraper_manager's _as_int(..., 1)
#   the rest                          -> the config-seed fallbacks
_DEFAULT_EMBEDDING_MODEL = "HuggingFaceEmbeddings"
_DEFAULT_EMBEDDING_DIMENSIONS = 384
_DEFAULT_CHUNK_SIZE = 1000
_DEFAULT_CHUNK_OVERLAP = 150
_DEFAULT_DISTANCE_METRIC = "cosine"
_DEFAULT_CHUNKING_STRATEGY = "sentence"
_DEFAULT_SITEMAP_MIN_PAGES = 1


def _mapping(value: Any) -> Dict[str, Any]:
    """Return ``value`` as a dict, or an empty dict when it is not a mapping.

    Provenance must never be able to fail an ingest, so a malformed config
    degrades to defaults instead of raising.
    """
    return dict(value) if isinstance(value, Mapping) else {}


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
        "embedding_model": embedding_model,
        "embedding_dimensions": embedding_entry.get(
            "dimensions", _DEFAULT_EMBEDDING_DIMENSIONS
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
    snapshot existed must not read as though every flag had changed.
    """
    recorded = _mapping(at_ingest)
    if not recorded:
        return []

    live = _mapping(current)
    keys = [key for key in INGEST_CONFIG_KEYS if key in live or key in recorded]
    keys += sorted((set(live) | set(recorded)) - set(INGEST_CONFIG_KEYS))

    drift: List[Dict[str, Any]] = []
    for key in keys:
        now = live.get(key)
        then = recorded.get(key)
        if now != then:
            drift.append({"key": key, "current": now, "at_ingest": then})
    return drift
