"""Concurrent persists of one resource must be serialised (issue #136 review).

The parallel scrape phase runs many seed crawls against a single shared
``PersistenceService``. ``LinkScraper`` deduplicates only *within* one crawl, so
overlapping seed graphs — a site root and one of its child pages both listed —
make two workers yield the same URL at the same time. ``ScrapedResource``'s hash
is ``md5(url)`` and its filename is derived from that hash, so those two workers
target the *same file path* and the *same catalog row*.

``persist_resource`` is a read-modify-write over that shared state: it checks
``file_path.exists()``, writes the file, ``stat()``s it for ``size_bytes``, then
upserts the catalog row. Interleaved, two callers can both see the file absent,
both truncate-and-write it, and ``stat()`` a partially written file — recording a
``size_bytes`` that does not describe any version of the content, and committing
the two catalog rows in an order unrelated to the file's final bytes.

These tests gate on the *observable* consequence: no two calls for the same
resource hash are ever inside the write window at once, and distinct hashes are
still free to run concurrently (the lock must not serialise the whole phase).
"""

import threading

import pytest

from src.data_manager.collectors import persistence as persistence_module
from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource

URL = "https://docs.example.edu/page"
OTHER_URL = "https://docs.example.edu/other"


class _FakeCatalog:
    """Stands in for PostgresCatalogService so no database is required."""

    def __init__(self, data_path, pg_config=None, **kwargs):
        self.data_path = data_path
        self.upserts = []
        self._lock = threading.Lock()

    def upsert_resource(self, resource_hash, path, metadata):
        with self._lock:
            self.upserts.append((resource_hash, path, dict(metadata or {})))
            return len(self.upserts)

    def refresh(self):
        return None


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "PostgresCatalogService", _FakeCatalog)
    return PersistenceService(tmp_path, pg_config={})


class _WriteGate:
    """Blocks the first thread inside ``_write_content`` and records overlap."""

    def __init__(self):
        self.entered_first = threading.Event()
        self.entered_second = threading.Event()
        self.release = threading.Event()
        self.writes = []
        self._lock = threading.Lock()

    def install(self, monkeypatch):
        original = PersistenceService._write_content
        gate = self

        def tracked(svc, file_path, content):
            with gate._lock:
                is_first = not gate.writes
                gate.writes.append(str(file_path))
            if is_first:
                gate.entered_first.set()
                assert gate.release.wait(10)
            else:
                gate.entered_second.set()
            return original(svc, file_path, content)

        monkeypatch.setattr(PersistenceService, "_write_content", tracked)
        return self


def _resource(url, content):
    return ScrapedResource(url=url, content=content, suffix="html", source_type="web")


class TestSameResourceIsSerialised:
    def test_two_workers_never_write_one_resource_at_once(
        self, service, tmp_path, monkeypatch
    ):
        gate = _WriteGate().install(monkeypatch)
        target = tmp_path / "websites"
        first = _resource(URL, "A" * 120)
        second = _resource(URL, "B" * 40)
        assert first.get_hash() == second.get_hash()

        errors = []

        def persist(resource):
            try:
                service.persist_resource(resource, target)
            except Exception as exc:  # pragma: no cover - surfaced by the assert
                errors.append(exc)

        writer = threading.Thread(target=persist, args=(first,))
        writer.start()
        assert gate.entered_first.wait(10), "first persist never reached the write"

        contender = threading.Thread(target=persist, args=(second,))
        contender.start()
        assert not gate.entered_second.wait(
            0.5
        ), "a second persist entered the write window for the same resource hash"

        gate.release.set()
        writer.join(10)
        contender.join(10)
        assert not errors

        # The contender found the file already present and skipped the write.
        assert len(gate.writes) == 1
        persisted = target / first.get_filename()
        assert persisted.read_text() == "A" * 120

        # Both catalog rows describe the bytes that are actually on disk.
        sizes = {row[2]["size_bytes"] for row in service.catalog.upserts}
        assert sizes == {"120"}

    def test_lock_is_released_when_the_write_raises(self, service, tmp_path):
        target = tmp_path / "websites"
        # Empty content is rejected by _write_content.
        with pytest.raises(ValueError):
            service.persist_resource(_resource(URL, ""), target)
        # A later persist for the same hash must not deadlock on a leaked lock.
        service.persist_resource(_resource(URL, "recovered"), target)
        assert (target / _resource(URL, "x").get_filename()).read_text() == "recovered"


class TestDistinctResourcesStayConcurrent:
    def test_different_hashes_are_not_serialised(self, service, tmp_path, monkeypatch):
        gate = _WriteGate().install(monkeypatch)
        target = tmp_path / "websites"

        def persist(resource):
            service.persist_resource(resource, target)

        writer = threading.Thread(target=persist, args=(_resource(URL, "A" * 10),))
        writer.start()
        assert gate.entered_first.wait(10)

        contender = threading.Thread(
            target=persist, args=(_resource(OTHER_URL, "B" * 10),)
        )
        contender.start()
        # A different resource hash must not queue behind the blocked one.
        assert gate.entered_second.wait(
            5
        ), "an unrelated resource was serialised behind the blocked write"

        gate.release.set()
        writer.join(10)
        contender.join(10)
        assert len(gate.writes) == 2
