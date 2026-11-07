"""Pin the metadata fallback the r0a category-routing arm depends on (plan W7).

``category`` is not a column (``_METADATA_COLUMN_MAP`` has no entry for it), so
``search_metadata_index`` can only filter on it through the ``extra_text``
substring fallback, and that only works because ``_build_extra_text`` writes each
field as ``key:value``. Neither behavior had a test; a refactor could remove
either without notice and quietly turn r0a into a no-op arm.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

from src.data_manager.collectors.utils.catalog_postgres import (
    _METADATA_COLUMN_MAP,
    PostgresCatalogService,
    _build_extra_text,
)


def _service_capturing_sql():
    service = PostgresCatalogService.__new__(PostgresCatalogService)
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = False

    @contextmanager
    def _connect():
        yield conn

    service._connect = _connect
    return service, cursor


def test_extra_text_carries_key_value_then_value():
    text = _build_extra_text({"category": "Storage", "empty": None, "title": "Tier 1"})
    assert text == "category:Storage Storage title:Tier 1 Tier 1"


def test_category_is_not_a_column():
    assert "category" not in _METADATA_COLUMN_MAP


def test_category_filter_uses_the_extra_text_substring_fallback():
    service, cursor = _service_capturing_sql()

    service.search_metadata("", filters={"category": "Storage"})

    sql, params = cursor.execute.call_args.args
    assert "extra_text ILIKE %s" in sql
    assert "%category:Storage%" in params
    assert "NOT is_deleted" in sql
