from src.utils.logging import get_logger

logger = get_logger(__name__)

_DIRECT_KEYS = frozenset({"git", "sso", "local_files"})
# Older ingests stored the source name itself as `source_type`: `jira`/`redmine`
# until bfba6303, `links` until 1121a059, `elog` from d27bf43f until 01104574.
# Rows that were not re-ingested still carry them, and the old direct lookup
# honoured their flag, so they keep mapping to their own key.
_LEGACY_KEYS = frozenset({"links", "elog", "jira", "redmine"})
_WEB_SCRAPERS = {"indico": "indico", "elog": "elog"}
_TICKET_PROVIDERS = {"jira": "jira", "redmine": "redmine"}


def _marker(metadata, name):
    value = metadata.get(name)
    return value.strip().lower() if isinstance(value, str) else None


def visibility_config_key(metadata) -> str | None:
    source_type = metadata.get("source_type")
    if source_type in _DIRECT_KEYS or source_type in _LEGACY_KEYS:
        return source_type
    if source_type == "web":
        return _WEB_SCRAPERS.get(_marker(metadata, "scraper"), "links")
    if source_type == "ticket":
        return _TICKET_PROVIDERS.get(_marker(metadata, "ticket_provider"))
    return None


def is_visible(metadata, sources_config) -> bool:
    key = visibility_config_key(metadata)
    if key is None:
        logger.debug("no visibility config key for metadata; treating as visible")
        return True
    entry = (sources_config or {}).get(key)
    if entry is None:
        logger.warning(
            "source key %r not found in sources_config; treating as visible", key
        )
        return True
    if not isinstance(entry, dict):
        logger.warning("sources_config[%r] is not a dict; treating as visible", key)
        return True
    return bool(entry.get("visible", True))


def visible_sources(documents, scores, sources_config):
    """Drop each document that ``is_visible`` hides, together with its own score.

    ``scores`` pairs with ``documents`` by position, the way ``format_citations``
    reads them. A kept document that has no score keeps none, so the formatter still
    pads it with ``-1.0``. A ``sources_config`` that is not a dict means no filter:
    the two arguments come back unchanged (issue #769).
    """
    if not isinstance(sources_config, dict):
        return documents, scores
    scores = list(scores or [])
    kept_documents = []
    kept_scores = []
    for index, document in enumerate(documents or []):
        metadata = getattr(document, "metadata", None)
        if not isinstance(metadata, dict):
            metadata = {}
        if not is_visible(metadata, sources_config):
            continue
        kept_documents.append(document)
        if index < len(scores):
            kept_scores.append(scores[index])
    return kept_documents, kept_scores
