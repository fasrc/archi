"""The report must be able to show that two runs saw the same corpus.

``corpus_snapshot_id`` is a fresh UUID per invocation. It tells two invocations
apart, but two runs over an unchanged corpus also get different ids, so it can
never support the claim the benchmark actually depends on: that the arms being
compared were scored against the same documents.

``corpus_fingerprint`` is derived from the corpus content instead, so equal
digests mean equal corpora. It is recorded alongside the nonce rather than
replacing it -- the Argilla analysis notebook consumes the nonce.

The pool these tests install is the one ``_init_runtime`` installs: a
``PostgresServiceFactory`` singleton. ``ConnectionPool``'s own singleton is
deliberately left alone, because nothing in production ever initializes it -- see
``TestReadsThroughTheInitializedPool`` and issue #273.
"""

import pytest
import yaml

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import ResultHandler
from src.utils.benchmark_provenance import corpus_fingerprint
from src.utils.connection_pool import ConnectionPool
from src.utils.postgres_service_factory import PostgresServiceFactory

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
    # Leave no factory behind for the next test, and start from none.
    monkeypatch.setattr(PostgresServiceFactory, "_instance", None)


def _install_pool(monkeypatch, pool):
    """Install *pool* the way ``_init_runtime`` does: behind the factory.

    Goes through the real ``PostgresServiceFactory.connection_pool`` property so
    the wiring under test is the production wiring, not a stub of it.
    """
    PostgresServiceFactory.set_instance(PostgresServiceFactory(connection_pool=pool))
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


class TestReadsThroughTheInitializedPool:
    """Regression tests for #273: the fingerprint was inert on every real run.

    The query used to go to ``ConnectionPool.get_instance()``, a singleton that
    no production path ever initializes -- ``_init_runtime`` builds a
    ``PostgresServiceFactory`` and calls ``set_instance`` on *that*, while the
    factory constructs its pools directly. So the call raised ``ValueError`` and
    the surrounding ``except`` filed the result as unavailable, silently, on
    every benchmark run. The old tests missed it by monkeypatching
    ``ConnectionPool.get_instance`` itself.

    These tests never touch ``ConnectionPool``'s singleton, so they only pass if
    the query reads through the pool the run actually opened.
    """

    def test_the_fingerprint_is_a_real_digest_on_a_normal_run(self, monkeypatch):
        """The whole defect in one assertion: a digest, not a marker."""
        _install_pool(monkeypatch, _FakePool())

        fingerprint = ResultHandler.get_corpus_fingerprint()

        assert fingerprint == corpus_fingerprint(LIVE_ROWS)
        assert not ResultHandler.corpus_reading_failed(fingerprint)

    def test_the_bare_connection_pool_singleton_is_not_consulted(self, monkeypatch):
        """Fails if the bare singleton is reintroduced, even were it to work."""
        calls = []

        def _record(cls, *args, **kwargs):
            calls.append(kwargs or args)
            raise AssertionError("get_corpus_fingerprint used the bare singleton")

        monkeypatch.setattr(ConnectionPool, "get_instance", classmethod(_record))
        _install_pool(monkeypatch, _FakePool())

        assert ResultHandler.get_corpus_fingerprint() == corpus_fingerprint(LIVE_ROWS)
        assert calls == []

    def test_an_uninitialized_factory_is_marked_and_says_so(self, monkeypatch):
        """No factory is a real possibility -- it must not crash the run."""
        monkeypatch.setattr(PostgresServiceFactory, "_instance", None)

        fingerprint = ResultHandler.get_corpus_fingerprint()

        assert ResultHandler.corpus_reading_failed(fingerprint)
        assert "PostgresServiceFactory" in fingerprint

    def test_a_failure_is_logged_not_only_filed_in_the_artifact(
        self, monkeypatch, caplog
    ):
        """The silence is why this shipped: the key was written either way.

        A reader of the artifact sees an unavailable-marker only if they look for
        it, and nothing in the run's own logs said the collection had failed.
        """

        class _Broken:
            def execute(self, *a, **k):
                raise RuntimeError("connection refused")

        _install_pool(monkeypatch, _Broken())

        with caplog.at_level("WARNING", logger="src.bin.service_benchmark"):
            ResultHandler.get_corpus_fingerprint()

        assert any(
            "connection refused" in record.getMessage()
            for record in caplog.records
            if record.levelname == "WARNING"
        )

    def test_the_run_still_keeps_its_scores_when_the_corpus_is_unreadable(
        self, monkeypatch
    ):
        """Provenance is never fatal -- a finished benchmark keeps its results."""

        class _Broken:
            def execute(self, *a, **k):
                raise RuntimeError("connection refused")

        _install_pool(monkeypatch, _Broken())
        ResultHandler.results = [{"scores": {"relevancy": 0.68}}]

        ResultHandler.add_metadata()

        assert ResultHandler.metadata["corpus_fingerprint"].startswith("<unavailable:")
        assert ResultHandler.results[0]["scores"]["relevancy"] == 0.68


class TestTheDigestCoversWhatTheAgentActuallyReceives:
    """Codex findings 5 and 6 on #272.

    The digest existed to make "these arms saw the same corpus" checkable. Two
    gaps meant it could answer wrongly in both directions:

    * It keyed chunks by ``document_chunks.document_id``, a SERIAL row id. Two
      ingests of an identical corpus get different serials, so the digests
      differ and comparable runs are REJECTED -- the cross-deployment property
      the field claims is exactly what it could not deliver.
    * It hashed only leaf ``chunk_text``. This deployment runs
      ``hierarchical_rerank`` for every chunk, so the agent is handed
      ``document_parent_nodes.parent_text``. Re-grouping children or rewriting
      parent text left the digest unchanged, so arms that fed the agent
      different context were CERTIFIED comparable.
    """

    def _query(self, monkeypatch):
        pool = _install_pool(monkeypatch, _FakePool())
        ResultHandler.get_corpus_fingerprint()
        return pool.queries[0]

    def test_chunks_are_keyed_by_content_identity_not_a_serial_row_id(
        self, monkeypatch
    ):
        """Finding 6: a fresh ingest of the same corpus must digest the same."""
        assert "'chunk:' || d.resource_hash" in self._query(monkeypatch)

    def test_parent_context_text_is_hashed(self, monkeypatch):
        """Finding 5: parent text is what the agent reads under reranking."""
        query = self._query(monkeypatch)

        assert "document_parent_nodes" in query
        assert "parent_text" in query

    def test_the_child_to_parent_grouping_is_hashed(self, monkeypatch):
        """Regrouping children changes the context even if every text is intact."""
        assert "parent_id" in self._query(monkeypatch)

    def test_parents_are_keyed_by_content_identity_too(self, monkeypatch):
        """A parent's serial id is as unstable as a document's."""
        assert "'parent:' || d.resource_hash" in self._query(monkeypatch)

    def test_deleted_documents_are_excluded_from_every_branch(self, monkeypatch):
        """The old chunk half had no is_deleted filter; the corpus is live rows."""
        assert self._query(monkeypatch).count("is_deleted = FALSE") == 3
