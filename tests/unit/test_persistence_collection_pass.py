"""
Tests for PersistenceService.begin_collection_pass / end_collection_pass and
the record_collected call inside _persist_resource_locked.

Design: openspec/changes/fix-issue-534-report-uncollected-catalog-rows/design.md (D2)
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.utils.catalog_reconcile import (
    CollectionPass,
    record_failure,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_persistence(tmp_path: Path, catalog=None) -> PersistenceService:
    """Return a PersistenceService with a fake catalog — no Postgres."""
    svc = object.__new__(PersistenceService)
    svc.data_path = tmp_path
    svc._resource_locks = {}
    svc._resource_locks_guard = threading.Lock()
    svc.collection_pass = None
    svc.catalog = catalog if catalog is not None else MagicMock()
    return svc


def _make_resource(hash_="abc123", content="data", metadata=None):
    """Minimal fake BaseResource."""
    resource = MagicMock()
    resource.get_hash.return_value = hash_
    resource.get_file_path.side_effect = lambda d: d / f"{hash_}.py"
    resource.get_content.return_value = content
    resource.get_metadata.return_value = metadata or {
        "source_type": "git",
        "parent": "repo1",
    }
    return resource


# ---------------------------------------------------------------------------
# begin_collection_pass / end_collection_pass
# ---------------------------------------------------------------------------


def test_begin_returns_collection_pass_and_sets_attribute(tmp_path):
    svc = _make_persistence(tmp_path)
    cp = svc.begin_collection_pass()
    assert isinstance(cp, CollectionPass)
    assert svc.collection_pass is cp


def test_end_collection_pass_returns_recorder_and_resets_to_none(tmp_path):
    svc = _make_persistence(tmp_path)
    cp = svc.begin_collection_pass()
    returned = svc.end_collection_pass()
    assert returned is cp
    assert svc.collection_pass is None


def test_end_without_begin_returns_none(tmp_path):
    svc = _make_persistence(tmp_path)
    assert svc.end_collection_pass() is None
    assert svc.collection_pass is None


# ---------------------------------------------------------------------------
# record_collected in _persist_resource_locked
# ---------------------------------------------------------------------------


def test_persist_with_open_pass_records_hash(tmp_path):
    """With an open pass, persist_resource records the hash under the resource scope."""
    svc = _make_persistence(tmp_path)
    resource = _make_resource()

    cp = svc.begin_collection_pass()
    target_dir = tmp_path / "data"
    svc.persist_resource(resource, target_dir)

    scope = ("git", "repo1")
    assert "abc123" in cp.collected.get(scope, set())


def test_persist_records_hash_only_after_upsert_returns(tmp_path):
    """record_collected must not be called when upsert_resource raises."""
    call_log = []
    fake_catalog = MagicMock()
    fake_catalog.upsert_resource.side_effect = RuntimeError("db down")

    svc = _make_persistence(tmp_path, fake_catalog)
    resource = _make_resource()

    cp = svc.begin_collection_pass()
    target_dir = tmp_path / "data"

    with pytest.raises(RuntimeError):
        svc.persist_resource(resource, target_dir)

    assert cp.collected == {}


def test_persist_without_open_pass_does_not_record(tmp_path):
    """Without an open pass, persist_resource works normally and records nothing."""
    svc = _make_persistence(tmp_path)
    resource = _make_resource()

    target_dir = tmp_path / "data"
    svc.persist_resource(resource, target_dir)

    # No collection pass was opened; catalog upsert still ran.
    svc.catalog.upsert_resource.assert_called_once()
    assert svc.collection_pass is None


def test_persist_with_open_pass_records_correct_scope(tmp_path):
    """Hash is stored under the scope derived from resource metadata."""
    svc = _make_persistence(tmp_path)
    resource = _make_resource(
        hash_="xyz789",
        metadata={"source_type": "git", "parent": "other_repo"},
    )

    cp = svc.begin_collection_pass()
    svc.persist_resource(resource, tmp_path / "out")

    assert "xyz789" in cp.collected.get(("git", "other_repo"), set())
    assert ("git", "repo1") not in cp.collected


# ---------------------------------------------------------------------------
# module-level record_failure helper
# ---------------------------------------------------------------------------


def test_record_failure_is_noop_when_no_pass_open(tmp_path):
    """record_failure does nothing and does not raise when collection_pass is None."""
    svc = _make_persistence(tmp_path)
    # must not raise
    record_failure(svc, "git", "some_repo", "clone failed")
    # and svc still has no pass
    assert svc.collection_pass is None


def test_record_failure_with_open_pass_delegates(tmp_path):
    """record_failure forwards the failure to the open CollectionPass."""
    svc = _make_persistence(tmp_path)
    cp = svc.begin_collection_pass()

    record_failure(svc, "git", "bad_repo", "clone failed")

    assert ("git", "bad_repo") in cp.failed
