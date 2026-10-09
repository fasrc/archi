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

from contextlib import contextmanager

import pytest
import yaml

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import ResultHandler
from src.utils.benchmark_provenance import CORPUS_STATE_V2_QUERY, corpus_fingerprint
from src.utils.connection_pool import ConnectionPool
from src.utils.postgres_service_factory import PostgresServiceFactory

LIVE_ROWS = [("aaa", "10"), ("bbb", "20")]
RUNNING_CONFIG = {
    "data_manager": {
        "collection_name": "fasrc",
        "embedding_name": "HuggingFaceEmbeddings",
        "embedding_class_map": {
            "HuggingFaceEmbeddings": {"kwargs": {"model_name": "m"}}
        },
    }
}
COLLECTION = "fasrc_with_HuggingFaceEmbeddings"


def _v2(rows=LIVE_ROWS):
    return corpus_fingerprint(rows, version="v2")


class _FakeCursor:
    def __init__(self, pool):
        self.pool = pool

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self.pool.queries.append(query)
        self.pool.params.append(params)

    def fetchall(self):
        return list(self.pool.rows)


class _FakePool:
    """``ConnectionPool`` stand-in: records the SQL and replays canned rows."""

    def __init__(self, rows=LIVE_ROWS, error=None):
        self.rows = rows
        self.error = error
        self.queries = []
        self.params = []

    @contextmanager
    def get_connection(self):
        if self.error is not None:
            raise self.error
        pool = self

        class _Connection:
            def cursor(self):
                return _FakeCursor(pool)

        yield _Connection()


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
    # add_metadata reads the corpus of the collection the last arm searched.
    monkeypatch.setattr(
        ResultHandler, "results", [{"running_configuration": RUNNING_CONFIG}]
    )


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

    assert ResultHandler.metadata["corpus_fingerprint"] == _v2()


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
    _install_pool(monkeypatch, _FakePool(rows=LIVE_ROWS + [("ccc", "30")]))
    ResultHandler.add_metadata()

    assert ResultHandler.metadata["corpus_fingerprint"] != before


def test_deleted_documents_are_excluded_from_the_corpus(monkeypatch):
    """Soft-deleted rows stay in the table but are not part of the corpus."""
    pool = _install_pool(monkeypatch, _FakePool())

    ResultHandler.add_metadata()

    assert "is_deleted" in pool.queries[0]


def test_an_unreadable_corpus_is_marked_rather_than_crashing(monkeypatch):
    _install_pool(monkeypatch, _FakePool(error=RuntimeError("connection refused")))

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

        fingerprint = ResultHandler.get_corpus_fingerprint(RUNNING_CONFIG)

        assert fingerprint == _v2()
        assert not ResultHandler.corpus_reading_failed(fingerprint)

    def test_the_bare_connection_pool_singleton_is_not_consulted(self, monkeypatch):
        """Fails if the bare singleton is reintroduced, even were it to work."""
        calls = []

        def _record(cls, *args, **kwargs):
            calls.append(kwargs or args)
            raise AssertionError("get_corpus_fingerprint used the bare singleton")

        monkeypatch.setattr(ConnectionPool, "get_instance", classmethod(_record))
        _install_pool(monkeypatch, _FakePool())

        assert ResultHandler.get_corpus_fingerprint(RUNNING_CONFIG) == _v2()
        assert calls == []

    def test_an_uninitialized_factory_is_marked_and_says_so(self, monkeypatch):
        """No factory is a real possibility -- it must not crash the run."""
        monkeypatch.setattr(PostgresServiceFactory, "_instance", None)

        fingerprint = ResultHandler.get_corpus_fingerprint(RUNNING_CONFIG)

        assert ResultHandler.corpus_reading_failed(fingerprint)
        assert "PostgresServiceFactory" in fingerprint

    def test_a_failure_is_logged_not_only_filed_in_the_artifact(
        self, monkeypatch, caplog
    ):
        """The silence is why this shipped: the key was written either way.

        A reader of the artifact sees an unavailable-marker only if they look for
        it, and nothing in the run's own logs said the collection had failed.
        """

        _install_pool(monkeypatch, _FakePool(error=RuntimeError("connection refused")))

        with caplog.at_level("WARNING", logger="src.bin.service_benchmark"):
            ResultHandler.get_corpus_fingerprint(RUNNING_CONFIG)

        assert any(
            "connection refused" in record.getMessage()
            for record in caplog.records
            if record.levelname == "WARNING"
        )

    def test_the_run_still_keeps_its_scores_when_the_corpus_is_unreadable(
        self, monkeypatch
    ):
        """Provenance is never fatal -- a finished benchmark keeps its results."""

        _install_pool(monkeypatch, _FakePool(error=RuntimeError("connection refused")))
        ResultHandler.results = [
            {"scores": {"relevancy": 0.68}, "running_configuration": RUNNING_CONFIG}
        ]

        ResultHandler.add_metadata()

        assert ResultHandler.metadata["corpus_fingerprint"].startswith("<unavailable:")
        assert ResultHandler.results[0]["scores"]["relevancy"] == 0.68


class TestTheHarnessReadsTheSharedV2Routine:
    """The harness hashes through ``live_corpus_fingerprint`` (#570).

    The SQL itself, and what it covers, is tested in
    ``test_corpus_fingerprint_v2.py``; here the harness must send that query,
    scoped to the collection its running config searches.
    """

    def test_the_query_is_the_shared_v2_query(self, monkeypatch):
        pool = _install_pool(monkeypatch, _FakePool())
        ResultHandler.get_corpus_fingerprint(RUNNING_CONFIG)
        assert pool.queries == [CORPUS_STATE_V2_QUERY]
        assert pool.params == [(COLLECTION,)]

    def test_the_digest_carries_the_v2_prefix(self, monkeypatch):
        _install_pool(monkeypatch, _FakePool())
        assert ResultHandler.get_corpus_fingerprint(RUNNING_CONFIG).startswith(
            "sha256/v2:"
        )

    def test_no_running_config_is_a_marker_not_a_crash(self, monkeypatch):
        _install_pool(monkeypatch, _FakePool())
        fingerprint = ResultHandler.get_corpus_fingerprint(None)
        assert ResultHandler.corpus_reading_failed(fingerprint)
        assert "collection" in fingerprint

    def test_add_metadata_reads_the_last_arms_collection(self, monkeypatch):
        pool = _install_pool(monkeypatch, _FakePool())
        ResultHandler.add_metadata()
        assert pool.params[-1] == (COLLECTION,)

    def test_the_v1_query_is_gone(self):
        assert not hasattr(sb, "CORPUS_STATE_QUERY")
        assert not hasattr(sb, "CATEGORY_MAP_QUERY")


class TestTheStartGuard:
    """``check_collection`` runs before the arm's first question (#570)."""

    def _pool(self, monkeypatch, row):
        from src.utils import benchmark_provenance

        pool = _install_pool(monkeypatch, _FakePool())
        monkeypatch.setattr(
            benchmark_provenance, "readiness_counts", lambda cursor, collection: row
        )
        return pool

    def test_a_ready_collection_returns_the_seven_field_record(self, monkeypatch):
        self._pool(monkeypatch, (5, 5, 0, ["m"]))
        record = ResultHandler.check_collection(RUNNING_CONFIG)
        assert record == {
            "collection": COLLECTION,
            "embedding_name": "HuggingFaceEmbeddings",
            "embedding_model": "m",
            "chunk_count": 5,
            "usable_chunk_count": 5,
            "untagged_chunk_count": 0,
            "embedding_model_source": "chunks",
        }

    def test_an_empty_collection_stops_the_run(self, monkeypatch):
        from src.utils.benchmark_provenance import CollectionNotReadyError

        self._pool(monkeypatch, (0, 0, 0, []))
        with pytest.raises(CollectionNotReadyError):
            ResultHandler.check_collection(RUNNING_CONFIG)

    def test_the_arm_record_carries_the_identity(self, monkeypatch, tmp_path):
        _install_pool(monkeypatch, _FakePool(rows=[]))
        config_path = tmp_path / "arm.yaml"
        config_path.write_text(yaml.safe_dump({"services": {"benchmarking": {}}}))
        monkeypatch.setattr(ResultHandler, "results", [])
        monkeypatch.setattr(ResultHandler, "category_map_records_by_arm", [])
        identity = {"collection": COLLECTION, "embedding_model": "m"}

        ResultHandler.handle_results(
            config_path,
            {},
            {},
            running_config=RUNNING_CONFIG,
            retrieval_identity=identity,
        )

        assert ResultHandler.results[-1]["retrieval_identity"] == identity


class TestTheHarnessEndTagReading:
    """handle_results writes the two embedding-tag keys after each arm (#573)."""

    def _call(
        self,
        monkeypatch,
        tmp_path,
        *,
        identity,
        tag_end,
        results_arg=None,
        corpus_before=None,
    ):
        _install_pool(monkeypatch, _FakePool(rows=[]))
        config_path = tmp_path / "arm.yaml"
        config_path.write_text(yaml.safe_dump({"services": {"benchmarking": {}}}))
        monkeypatch.setattr(ResultHandler, "results", [])
        monkeypatch.setattr(ResultHandler, "category_map_records_by_arm", [])
        monkeypatch.setattr(
            ResultHandler,
            "get_embedding_tag_state",
            staticmethod(lambda config: tag_end),
        )
        ResultHandler.handle_results(
            config_path,
            results_arg or {},
            {},
            running_config=RUNNING_CONFIG,
            corpus_before=corpus_before,
            retrieval_identity=identity,
        )
        return ResultHandler.results[-1]

    def test_an_unchanged_state_records_true_and_the_end_dict(
        self, monkeypatch, tmp_path
    ):
        end = {"embedding_model_tags": ["m1"], "untagged_chunk_count": 0}
        record = self._call(
            monkeypatch,
            tmp_path,
            identity={"embedding_model": "m1", "untagged_chunk_count": 0},
            tag_end=end,
        )
        assert record["embedding_tags_unchanged_at_endpoints"] is True
        assert record["embedding_tags_end"] == end

    def test_a_foreign_tag_with_equal_corpus_records_false_and_logs_warning(
        self, monkeypatch, tmp_path, caplog
    ):
        end = {"embedding_model_tags": ["m1", "m2"], "untagged_chunk_count": 0}
        with caplog.at_level("WARNING", logger="src.bin.service_benchmark"):
            record = self._call(
                monkeypatch,
                tmp_path,
                identity={"embedding_model": "m1", "untagged_chunk_count": 0},
                tag_end=end,
                corpus_before=_v2([]),
            )
        assert record["corpus_unchanged_at_endpoints"] is True
        assert record["embedding_tags_unchanged_at_endpoints"] is False
        assert any(
            "embedding model tags" in r.getMessage()
            for r in caplog.records
            if r.levelname == "WARNING"
        )

    def test_a_failed_end_reading_records_the_marker_and_none_and_keeps_scores(
        self, monkeypatch, tmp_path
    ):
        marker = f"{ResultHandler.CORPUS_UNAVAILABLE} boom>"
        record = self._call(
            monkeypatch,
            tmp_path,
            identity={"embedding_model": "m1", "untagged_chunk_count": 0},
            tag_end=marker,
            results_arg={"q1": {"score": 0.9}},
        )
        assert record["embedding_tags_end"].startswith("<unavailable:")
        assert record["embedding_tags_unchanged_at_endpoints"] is None
        assert record["single_question_results"]["q1"]["score"] == 0.9

    def test_retrieval_identity_none_records_none(self, monkeypatch, tmp_path):
        end = {"embedding_model_tags": ["m1"], "untagged_chunk_count": 0}
        record = self._call(
            monkeypatch,
            tmp_path,
            identity=None,
            tag_end=end,
        )
        assert record["embedding_tags_unchanged_at_endpoints"] is None
