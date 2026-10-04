from src.utils.logging import get_logger

logger = get_logger(__name__)

_DIRECT_KEYS = frozenset({"git", "sso", "local_files"})
_WEB_SCRAPERS = {"indico": "indico", "elog": "elog"}
_TICKET_PROVIDERS = {"jira": "jira", "redmine": "redmine"}


def _marker(metadata, name):
    value = metadata.get(name)
    return value.strip().lower() if isinstance(value, str) else None


def visibility_config_key(metadata) -> str | None:
    source_type = metadata.get("source_type")
    if source_type in _DIRECT_KEYS:
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
