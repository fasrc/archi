"""Paired exact McNemar tests for the rung-0 verdicts (plan §5).

r0a's primary pairs per-question relative source hits; r0b's pairs completion
(status ``ok`` versus not). Both are read as b = baseline succeeds and the arm
fails, c = the reverse, so a significant result has a direction.
"""

import math

import pytest

from scripts.benchmarking.paired_tests import (
    completion_test,
    holm_adjust,
    mcnemar_exact,
    paired_binary,
    source_test,
)

KB = "https://docs.rc.fas.harvard.edu/kb"


def _reference(b, c):
    """archi-bench-out feature_matrix/figures/extract_figure_data.py:67-74."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2**n
    return min(1.0, 2 * p)


# --- mcnemar_exact -------------------------------------------------------------------


def test_no_discordant_pairs_is_p_one():
    assert mcnemar_exact(0, 0) == 1.0


@pytest.mark.parametrize("b", range(21))
def test_matches_the_reference_over_a_grid(b):
    for c in range(21):
        assert mcnemar_exact(b, c) == _reference(b, c)


def test_is_symmetric():
    assert mcnemar_exact(2, 12) == mcnemar_exact(12, 2)


# --- holm_adjust ---------------------------------------------------------------------


def test_holm_single_value_is_unchanged():
    assert holm_adjust([0.03]) == [0.03]


def test_holm_family_of_two():
    assert holm_adjust([0.04, 0.01]) == pytest.approx([0.04, 0.02])


def test_holm_is_monotone_and_capped():
    assert holm_adjust([0.6, 0.02, 0.4]) == pytest.approx([0.8, 0.06, 0.8])


def test_holm_empty():
    assert holm_adjust([]) == []


# --- paired_binary -------------------------------------------------------------------


def test_discordant_counts_and_direction():
    base = {f"q{i}": i < 2 for i in range(14)}  # baseline succeeds on q0, q1
    arm = {f"q{i}": i >= 2 for i in range(14)}  # arm succeeds on q2..q13
    result = paired_binary(base, arm)
    assert (result["b"], result["c"], result["n"]) == (2, 12, 14)
    assert result["p"] == mcnemar_exact(2, 12)
    assert result["direction"] == "arm better"
    assert result["pairs"] == 14


def test_worse_and_no_difference():
    assert paired_binary({"q": True}, {"q": False})["direction"] == "arm worse"
    assert paired_binary({"q": True}, {"q": True})["direction"] == "no difference"


def test_only_common_questions_pair():
    result = paired_binary({"a": True, "b": True}, {"a": False, "c": False})
    assert result["pairs"] == 1 and result["b"] == 1


# --- source_test and completion_test ----------------------------------------------------


def _row(status="ok", sources=(), matched=()):
    refs = [{"url": url} for url in sources]
    for ref, hit in zip(refs, matched):
        ref["matched"] = hit
    return {"status": status, "reference_sources_metadata": refs}


def test_source_test_uses_relative_hits():
    base = {"q": _row(sources=[f"{KB}/a", f"{KB}/b"], matched=[False, False])}
    arm = {"q": _row(sources=[f"{KB}/a", f"{KB}/b"], matched=[False, True])}
    result = source_test(base, arm)
    assert (result["b"], result["c"]) == (0, 1)


def test_source_test_skips_rows_without_sources_or_not_clean():
    base = {"none": _row(), "bad": _row(sources=[f"{KB}/a"], matched=[True])}
    arm = {"none": _row(), "bad": _row(status="degraded", sources=[f"{KB}/a"])}
    assert source_test(base, arm)["pairs"] == 0


def test_source_test_lists_questions_whose_gold_sources_differ():
    base = {"q": _row(sources=[f"{KB}/a/"], matched=[True])}
    arm = {"q": _row(sources=[f"{KB}/b"], matched=[False])}
    result = source_test(base, arm)
    assert result["pairs"] == 0
    assert result["sources_differ"] == ["q"]


def test_source_lists_equal_after_canonicalization():
    base = {"q": _row(sources=[f"{KB}/a/"], matched=[True])}
    arm = {"q": _row(sources=[f"{KB}/a"], matched=[False])}
    result = source_test(base, arm)
    assert result["pairs"] == 1 and result["sources_differ"] == []


def test_degraded_row_counts_for_completion():
    base = {"q": _row(), "r": _row()}
    arm = {"q": _row(status="degraded"), "r": _row()}
    result = completion_test(base, arm)
    assert (result["b"], result["c"], result["pairs"]) == (1, 0, 2)
