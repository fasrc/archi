"""Each arm records which URL -> category map it ran against (#524, #538).

The corpus fingerprint cannot prove this: it never hashes ``extra_json``, where
``category`` lives. So the harness reads the map beside the two corpus readings,
records both digests three-state, keeps the end records out of the JSON, and
``dump_artifacts`` writes them as one ``_category_map_<N>.tsv`` sibling per arm
whose sha256 equals that arm's end digest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import ResultHandler
from src.utils.benchmark_provenance import category_map_digest, category_map_records
from src.utils.postgres_service_factory import PostgresServiceFactory

KB = "https://docs.rc.fas.harvard.edu/kb"
MAP_ROWS = [(f"{KB}/storage/", "Storage"), (f"{KB}/running-jobs", "Cluster Usage")]
RECORDS = category_map_records(MAP_ROWS)
DIGEST = category_map_digest(RECORDS)


class _FakePool:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def execute(self, query, params=None, *, fetch=True):
        self.queries.append(query)
        return self.rows


class _BrokenPool:
    def execute(self, *a, **k):
        raise RuntimeError("connection refused")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.setattr(ResultHandler, "category_map_records_by_arm", [])
    monkeypatch.setattr(PostgresServiceFactory, "_instance", None)
    monkeypatch.setattr(
        ResultHandler, "get_corpus_fingerprint", staticmethod(lambda: "sha256:corpus")
    )


def _install_pool(pool):
    PostgresServiceFactory.set_instance(PostgresServiceFactory(connection_pool=pool))
    return pool


def _pin_map(monkeypatch, records, digest):
    monkeypatch.setattr(
        ResultHandler, "get_category_map", staticmethod(lambda: (records, digest))
    )


def _write(tmp_path, name="arm.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump({"services": {"benchmarking": {}}}))
    return path


def _handle(tmp_path, **kwargs):
    ResultHandler.handle_results(
        _write(tmp_path), {}, {}, running_config={}, corpus_before="sha256:corpus", **kwargs
    )
    return ResultHandler.results[-1]


# --- get_category_map ------------------------------------------------------------


def test_reads_the_map_through_the_initialized_pool():
    pool = _install_pool(_FakePool(MAP_ROWS))

    records, digest = ResultHandler.get_category_map()

    assert records == RECORDS and digest == DIGEST
    [query] = pool.queries
    assert "extra_json->>'category'" in query
    assert "NOT is_deleted" in query and "url IS NOT NULL" in query


def test_no_factory_is_a_marker_not_a_crash():
    records, digest = ResultHandler.get_category_map()

    assert records is None
    assert digest.startswith(ResultHandler.CORPUS_UNAVAILABLE)


def test_a_failed_query_is_a_marker_and_is_logged(caplog):
    _install_pool(_BrokenPool())

    with caplog.at_level("WARNING", logger="src.bin.service_benchmark"):
        records, digest = ResultHandler.get_category_map()

    assert records is None
    assert "connection refused" in digest
    assert any("Category-map provenance unavailable" in r.message for r in caplog.records)


# --- handle_results: three-state endpoints ---------------------------------------


def test_equal_digests_are_unchanged(tmp_path, monkeypatch):
    _pin_map(monkeypatch, RECORDS, DIGEST)

    record = _handle(tmp_path, category_map_before=DIGEST)

    assert record["category_map_sha256_start"] == DIGEST
    assert record["category_map_sha256_end"] == DIGEST
    assert record["category_map_unchanged_at_endpoints"] is True


def test_different_digests_are_changed_and_warn(tmp_path, monkeypatch, caplog):
    _pin_map(monkeypatch, RECORDS, DIGEST)

    with caplog.at_level("WARNING"):
        record = _handle(tmp_path, category_map_before="sha256:before")

    assert record["category_map_unchanged_at_endpoints"] is False
    assert any(
        "category map changed" in r.message and DIGEST in r.message
        for r in caplog.records
    )


def test_a_failed_start_reading_is_null(tmp_path, monkeypatch):
    _pin_map(monkeypatch, RECORDS, DIGEST)

    record = _handle(tmp_path, category_map_before="<unavailable: boom>")

    assert record["category_map_unchanged_at_endpoints"] is None


def test_two_identical_failures_are_null_not_true(tmp_path, monkeypatch):
    _pin_map(monkeypatch, None, "<unavailable: boom>")

    record = _handle(tmp_path, category_map_before="<unavailable: boom>")

    assert record["category_map_unchanged_at_endpoints"] is None


def test_a_missing_start_reading_is_null(tmp_path, monkeypatch):
    _pin_map(monkeypatch, RECORDS, DIGEST)

    record = _handle(tmp_path)

    assert record["category_map_sha256_start"] is None
    assert record["category_map_unchanged_at_endpoints"] is None


def test_end_records_stay_out_of_the_json(tmp_path, monkeypatch):
    _pin_map(monkeypatch, RECORDS, DIGEST)

    record = _handle(tmp_path, category_map_before=DIGEST)

    assert ResultHandler.category_map_records_by_arm == [RECORDS]
    assert RECORDS[0] not in json.dumps(record, default=str)


def test_corpus_keys_keep_their_meaning(tmp_path, monkeypatch):
    _pin_map(monkeypatch, RECORDS, DIGEST)

    record = _handle(tmp_path, category_map_before=DIGEST)

    assert record["corpus_fingerprint_before"] == "sha256:corpus"
    assert record["corpus_fingerprint"] == "sha256:corpus"
    assert record["corpus_unchanged_at_endpoints"] is True


def test_records_the_prompt_digest_it_was_given(tmp_path, monkeypatch):
    _pin_map(monkeypatch, RECORDS, DIGEST)

    record = _handle(tmp_path, category_map_before=DIGEST, agent_md_sha256="abc")

    assert record["agent_md_sha256"] == "abc"


# --- dump_artifacts: one snapshot per arm ----------------------------------------


@pytest.fixture()
def dump_state(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(ResultHandler, "metadata", {"time": "2026-09-24"})
    monkeypatch.setattr(ResultHandler, "ab_comparison", None)
    monkeypatch.setattr(ResultHandler, "ab_comparisons", [])
    monkeypatch.setattr(ResultHandler, "leaderboard", None)
    monkeypatch.setattr(ResultHandler, "dump_report", staticmethod(lambda *a: None))
    return tmp_path


def _arm(monkeypatch, records, digest, tmp_path, name):
    """One arm whose start and end readings are *digest*, ending with *records*."""
    _pin_map(monkeypatch, records, digest)
    ResultHandler.handle_results(
        _write(tmp_path, name),
        {},
        {},
        running_config={},
        corpus_before="sha256:corpus",
        category_map_before=digest,
    )


def test_writes_one_snapshot_per_arm_bound_to_its_end_digest(dump_state, monkeypatch):
    maps = [
        category_map_records([(f"{KB}/a", "A")]),
        category_map_records([(f"{KB}/b", "B")]),
        category_map_records([(f"{KB}/c", "C")]),
    ]
    for i, records in enumerate(maps, 1):
        _arm(
            monkeypatch, records, category_map_digest(records), dump_state, f"arm{i}.yaml"
        )

    ResultHandler.dump_artifacts(Path("bench"))

    [artifact] = dump_state.glob("*.json")
    entries = json.loads(artifact.read_text())["benchmarking_results"]
    for i, entry in enumerate(entries, 1):
        name = entry["category_map_file"]
        assert name == f"{artifact.stem}_category_map_{i}.tsv"
        body = (dump_state / name).read_bytes()
        assert f"sha256:{hashlib.sha256(body).hexdigest()}" == entry[
            "category_map_sha256_end"
        ]


def test_a_failed_end_reading_writes_no_file(dump_state, monkeypatch):
    _arm(monkeypatch, None, "<unavailable: boom>", dump_state, "arm1.yaml")

    ResultHandler.dump_artifacts(Path("bench"))

    [artifact] = dump_state.glob("*.json")
    [entry] = json.loads(artifact.read_text())["benchmarking_results"]
    assert entry["category_map_file"] is None
    assert list(dump_state.glob("*.tsv")) == []


def test_legacy_results_are_left_untouched(dump_state, monkeypatch):
    monkeypatch.setattr(ResultHandler, "results", [{"configuration_file": "x.yaml"}])

    ResultHandler.dump_artifacts(Path("bench"))

    [artifact] = dump_state.glob("*.json")
    [entry] = json.loads(artifact.read_text())["benchmarking_results"]
    assert "category_map_file" not in entry
    assert list(dump_state.glob("*.tsv")) == []
