"""A leaderboard must not rank variants it cannot show shared conditions for.

``build_leaderboard`` already warns when a swept field differs across configs --
model, provider, evaluator, query set. The corpus belongs in that same check: a
ranking computed across arms scored on different documents, or on an arm whose
corpus shifted mid-run, asserts controlled conditions the run cannot support.

This reuses the existing shared-context warning mechanism rather than blocking
the leaderboard. Suppressing output entirely is an operator policy decision;
recording the defect where the comparison is presented is the provenance fix.
"""

import pytest

from src.bin.service_benchmark import ResultHandler

BENCH = {
    "name": "variant",
    "agent_md_file": "/agents/a.md",
    "model": "m",
    "provider": "p",
    "queries_path": "q.json",
    "mode_settings": {"ragas_settings": {"evaluator_model": "judge"}},
}


def _record(name, corpus_fingerprint, corpus_unchanged_at_endpoints=True):
    bench = dict(BENCH, name=name)
    return {
        "configuration": {"services": {"benchmarking": bench}},
        "total_results": {
            "aggregate_faithfulness": 0.5,
            "aggregate_answer_relevancy": 0.5,
            "aggregate_context_precision": 0.5,
            "aggregate_context_recall": 0.5,
        },
        "single_question_results": {},
        "corpus_fingerprint": corpus_fingerprint,
        "corpus_unchanged_at_endpoints": corpus_unchanged_at_endpoints,
    }


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.setattr(ResultHandler, "leaderboard", {})
    monkeypatch.setattr(ResultHandler, "_corpus_snapshot_id", "pinned")


def _warnings():
    return ResultHandler.build_leaderboard()["shared_context"]["warnings"]


def test_no_corpus_warning_when_every_arm_saw_the_same_stable_corpus():
    ResultHandler.results = [
        _record("a", "sha256:same"),
        _record("b", "sha256:same"),
    ]

    assert _warnings() == []


def test_warns_when_the_arms_were_scored_against_different_corpora():
    ResultHandler.results = [
        _record("a", "sha256:aaa"),
        _record("b", "sha256:bbb"),
    ]

    assert any("corpus_fingerprint" in w for w in _warnings())


def test_warns_when_an_arm_had_an_unstable_corpus():
    ResultHandler.results = [
        _record("a", "sha256:same", corpus_unchanged_at_endpoints=False),
        _record("b", "sha256:same"),
    ]

    warnings = _warnings()
    assert any("changed" in w.lower() and "a" in w for w in warnings)


def test_warns_when_an_arm_has_unknown_corpus_stability():
    ResultHandler.results = [
        _record("a", "sha256:same", corpus_unchanged_at_endpoints=None),
        _record("b", "sha256:same"),
    ]

    assert any("unknown" in w.lower() for w in _warnings())


def test_a_record_predating_provenance_says_nothing_rather_than_unknown():
    """An absent field is not a finding.

    Result files written before provenance existed carry no corpus keys at all.
    Treating that as "unknown" would put a warning on every historical sweep,
    which is noise, not information.
    """
    legacy = _record("a", "sha256:same")
    del legacy["corpus_unchanged_at_endpoints"]
    del legacy["corpus_fingerprint"]
    ResultHandler.results = [legacy, _record("b", "sha256:same")]

    assert _warnings() == []


def test_ranks_are_assigned_when_the_arms_are_comparable():
    ResultHandler.results = [
        _record("a", "sha256:same"),
        _record("b", "sha256:same"),
    ]

    leaderboard = ResultHandler.build_leaderboard()

    assert leaderboard["comparable"] is True
    assert all(row["rank"] is not None for row in leaderboard["rows"])


def test_no_rank_is_manufactured_when_the_arms_are_not_comparable():
    """A rank is a machine-readable claim of ordering.

    Emitting one when the corpus provenance says the arms were not scored under
    the same conditions asserts exactly what the run cannot support -- and a
    consumer reading rows[*].rank never sees the separate warning list. The
    metrics are preserved so an operator can still inspect them.
    """
    ResultHandler.results = [
        _record("a", "sha256:aaa"),
        _record("b", "sha256:bbb"),
    ]

    leaderboard = ResultHandler.build_leaderboard()

    assert leaderboard["comparable"] is False
    assert all(row["rank"] is None for row in leaderboard["rows"])
    assert all(row["metrics"]["faithfulness"] == 0.5 for row in leaderboard["rows"])


def test_an_unstable_arm_also_withholds_ranks():
    ResultHandler.results = [
        _record("a", "sha256:same", corpus_unchanged_at_endpoints=False),
        _record("b", "sha256:same"),
    ]

    leaderboard = ResultHandler.build_leaderboard()

    assert leaderboard["comparable"] is False
    assert all(row["rank"] is None for row in leaderboard["rows"])


def test_the_leaderboard_is_still_produced_so_the_operator_can_judge():
    """Withhold the ranking, not the data."""
    ResultHandler.results = [
        _record("a", "sha256:aaa", corpus_unchanged_at_endpoints=False),
        _record("b", "sha256:bbb"),
    ]

    leaderboard = ResultHandler.build_leaderboard()

    assert len(leaderboard["rows"]) == 2
    assert leaderboard["shared_context"]["warnings"]


def test_records_predating_provenance_stay_comparable():
    """Historical sweeps are not retroactively invalidated."""
    legacy_a, legacy_b = _record("a", "sha256:x"), _record("b", "sha256:y")
    for rec in (legacy_a, legacy_b):
        del rec["corpus_unchanged_at_endpoints"]
        del rec["corpus_fingerprint"]
    ResultHandler.results = [legacy_a, legacy_b]

    leaderboard = ResultHandler.build_leaderboard()

    assert leaderboard["comparable"] is True
    assert all(row["rank"] is not None for row in leaderboard["rows"])
