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


def _record(name, corpus_fingerprint, corpus_stable=True):
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
        "corpus_stable": corpus_stable,
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
        _record("a", "sha256:same", corpus_stable=False),
        _record("b", "sha256:same"),
    ]

    warnings = _warnings()
    assert any("changed" in w.lower() and "a" in w for w in warnings)


def test_warns_when_an_arm_has_unknown_corpus_stability():
    ResultHandler.results = [
        _record("a", "sha256:same", corpus_stable=None),
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
    del legacy["corpus_stable"]
    del legacy["corpus_fingerprint"]
    ResultHandler.results = [legacy, _record("b", "sha256:same")]

    assert _warnings() == []


def test_the_leaderboard_is_still_produced_so_the_operator_can_judge():
    """Warn, do not silently withhold: suppression is the operator's call."""
    ResultHandler.results = [
        _record("a", "sha256:aaa", corpus_stable=False),
        _record("b", "sha256:bbb"),
    ]

    leaderboard = ResultHandler.build_leaderboard()

    assert len(leaderboard["rows"]) == 2
    assert leaderboard["shared_context"]["warnings"]
