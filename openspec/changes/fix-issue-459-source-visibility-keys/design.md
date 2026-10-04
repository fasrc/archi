## Context

`get_top_sources` (`src/interfaces/chat_app/app.py:673-700`) calls
`self._get_doc_visibility(self, metadata)` at `:690`. `_get_doc_visibility` (`:813-828`) is a
`@staticmethod` that takes `self` as its first argument and does
`self.sources_config[source_type].get("visible", True)`.

Stored `source_type` values come from the writers: `scraper_manager.py:297` (`web`), `:379`
(`git`), `:402` (`sso`); `tickets/integrations/jira.py:134` and `redmine_tickets.py:129`
(`ticket`); `localfile_resource.py:20` (`local_files`). The markers that tell the `web` and
`ticket` sources apart already exist: `indico_scraper.py:926,1051,1208`
(`"scraper": "indico"`), `elog_scraper.py:126` (`"scraper": "elog"`), `jira.py:128`
(`"ticket_provider": "jira"`), `redmine_tickets.py:116` (`"ticket_provider": "redmine"`).

File-level metadata is merged into every chunk before embedding
(`src/data_manager/vectorstore/manager.py`, the `file_level_metadata` path), and
`_merge_row_metadata` (`postgres_vectorstore.py:36`) keeps the chunk's own metadata as the
base and overlays only `resource_hash`, `display_name`, `source_type`, `url`, and `title`. So
`scraper` and `ticket_provider` reach `document.metadata` at chat time. A chunk from an older
ingest with no marker falls back: `web` → `links`, `ticket` with no provider → visible.

## Decision

Map at lookup time in a new module, `src/interfaces/chat_app/source_visibility.py`.
`app.py` is imported by `tests/unit/test_get_top_sources.py`, but the logic still goes in a
helper so that `app.py`'s diff stays a few lines.

```python
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
        logger.debug(...)            # uploaded file, unknown type, or ticket with no provider
        return True
    entry = (sources_config or {}).get(key)
    if entry is None:
        logger.warning(...)          # a real config gap
        return True
    return bool(entry.get("visible", True))
```

The marker match uses `strip().lower()`, the same normalization `manager.py` applies to
`scraper` for the Indico chunk prefix. A non-dict `entry` (malformed config) is treated as
visible with the same WARNING, so the chat path never raises on a config shape.

`_get_doc_visibility` keeps its signature (`@staticmethod`, `self` first) and its body
becomes `return is_visible(metadata, self.sources_config)`. Fixing the odd call shape is
not needed for this issue, so the call at `:690` does not change.

## Tests

`tests/unit/test_source_visibility.py` (new):

1. Parametrized: each row of the mapping gives the expected key, including `web` with no
   marker → `links`, `web` with `scraper="Indico "` → `indico`, `ticket` with no provider →
   `None`, no `source_type` → `None`, `source_type="uploaded"` → `None`.
2. `jira.visible: false` hides `{"source_type": "ticket", "ticket_provider": "jira"}`;
   a redmine document stays visible.
3. `indico.visible: false` hides `{"source_type": "web", "scraper": "indico"}`; a plain
   `web` document stays visible.
4. `links.visible: false` hides a plain `web` document.
5. A document with no `source_type` is visible and `caplog` has no ERROR record.
6. A mapped key missing from `sources_config` is visible and logs one WARNING.
7. A non-dict entry (`{"links": None}`) is visible and does not raise.

`tests/unit/test_get_top_sources.py` (append): with `wrapper.sources_config =
{"jira": {"visible": False}, "redmine": {"visible": True}}`, `get_top_sources` returns the
redmine document and skips the jira document.
