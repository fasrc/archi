"""The report must be able to show that two runs saw the same corpus.

``corpus_snapshot_id`` is a fresh UUID per invocation. It tells two invocations
apart, but two runs over an unchanged corpus also get different ids, so it can
never support the claim the benchmark actually depends on: that the arms being
compared were scored against the same documents.

``corpus_fingerprint`` is derived from the corpus content instead, so equal
digests mean equal corpora. It is recorded alongside the nonce rather than
replacing it -- the Argilla analysis notebook consumes the nonce.
"""

import pytest
import yaml

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import ResultHandler
from src.utils.benchmark_provenance import corpus_fingerprint

LIVE_ROWS = [("aaa", 10), ("bbb", 20)]


class _FakePool:
    """Records the SQL it was asked to run and replays canned rows."""

    def __init__(self, rows=LIVE_ROWS):
        self.rows = rows
        self.queries = []

    def execute(self, query, params=None, *, fetch=True):
        self.queries.append(query)
        return self.rows


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(ResultHandler, "metadata", {})
    monkeypatch.setattr(ResultHandler, "_corpus_snapshot_id", None)
    git_info = tmp_path / "git_info.yaml"
    git_info.write_text(yaml.safe_dump({"last_commit": "abc123\n", "git_diff": ""}))
    monkeypatch.setattr(sb, "EXTRA_METADATA_PATH", str(git_info))
    monkeypatch.delenv("ARCHI_CORPUS_SNAPSHOT_ID", raising=False)


def _install_pool(monkeypatch, pool):
    monkeypatch.setattr(
        sb.ConnectionPool, "get_instance", classmethod(lambda cls: pool)
    )
    return pool


def test_records_a_fingerprint_derived_from_the_corpus(monkeypatch):
    _install_pool(monkeypatch, _FakePool())

    ResultHandler.add_metadata()

    assert ResultHandler.metadata["corpus_fingerprint"] == corpus_fingerprint(LIVE_ROWS)


def test_two_runs_over_an_unchanged_corpus_agree(monkeypatch):
    _install_pool(monkeypatch, _FakePool())
    ResultHandler.add_metadata()
    first = ResultHandler.metadata["corpus_fingerprint"]

    ResultHandler.metadata = {}
    _install_pool(monkeypatch, _FakePool())
    ResultHandler.add_metadata()

    assert ResultHandler.metadata["corpus_fingerprint"] == first


def test_a_changed_corpus_produces_a_different_fingerprint(monkeypatch):
    _install_pool(monkeypatch, _FakePool())
    ResultHandler.add_metadata()
    before = ResultHandler.metadata["corpus_fingerprint"]

    ResultHandler.metadata = {}
    _install_pool(monkeypatch, _FakePool(rows=LIVE_ROWS + [("ccc", 30)]))
    ResultHandler.add_metadata()

    assert ResultHandler.metadata["corpus_fingerprint"] != before


def test_deleted_documents_are_excluded_from_the_corpus(monkeypatch):
    """Soft-deleted rows stay in the table but are not part of the corpus."""
    pool = _install_pool(monkeypatch, _FakePool())

    ResultHandler.add_metadata()

    assert "is_deleted" in pool.queries[0]


def test_an_unreadable_corpus_is_marked_rather_than_crashing(monkeypatch):
    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("connection refused")

    _install_pool(monkeypatch, _Broken())

    ResultHandler.add_metadata()

    assert ResultHandler.metadata["corpus_fingerprint"].startswith("<unavailable:")
    assert "connection refused" in ResultHandler.metadata["corpus_fingerprint"]


def test_the_per_invocation_nonce_is_still_recorded(monkeypatch):
    """The Argilla notebook consumes corpus_snapshot_id; it must keep working."""
    _install_pool(monkeypatch, _FakePool())

    ResultHandler.add_metadata()

    assert ResultHandler.metadata["corpus_snapshot_id"]
    assert (
        ResultHandler.metadata["corpus_snapshot_id"]
        != ResultHandler.metadata["corpus_fingerprint"]
    )


def test_git_info_is_labelled_as_deploy_time(monkeypatch):
    """git_info is captured by `archi create`, not by the running image.

    Every run of the ragas-205 campaign reported the same commit even though the
    arms ran different code, because the file is written once at deploy and the
    benchmark container is re-executed against it.
    """
    _install_pool(monkeypatch, _FakePool())

    ResultHandler.add_metadata()

    assert "deploy" in ResultHandler.metadata["git_info_captured_at"]
