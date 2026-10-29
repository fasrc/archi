"""The third judge mode, ``inline+bundle`` (add-inline-bundle-judge-mode, D1 and D2).

``parse_judge_mode`` accepts it and names all three values when it refuses;
``writes_bundle`` and ``scores_inline`` say what a plan does, beside ``is_deferred``.
"""

from __future__ import annotations

import pytest

from src.utils.judge_bundle import (
    MODE_DEFERRED,
    MODE_INLINE,
    MODE_INLINE_BUNDLE,
    JudgePlan,
    is_deferred,
    parse_judge_mode,
    scores_inline,
    writes_bundle,
)


def test_the_three_mode_constants_are_the_config_values():
    assert (MODE_INLINE, MODE_DEFERRED, MODE_INLINE_BUNDLE) == (
        "inline",
        "deferred",
        "inline+bundle",
    )


def test_string_inline_bundle_gives_inline_bundle():
    assert parse_judge_mode("inline+bundle") == MODE_INLINE_BUNDLE


@pytest.mark.parametrize("bad", ["Inline+Bundle", "inline-bundle", "both", "bundle"])
def test_invalid_names_the_value_and_all_three_allowed_values(bad):
    with pytest.raises(ValueError) as exc_info:
        parse_judge_mode(bad)
    msg = str(exc_info.value)
    assert repr(bad) in msg
    for allowed in ("'inline'", "'deferred'", "'inline+bundle'"):
        assert allowed in msg


def _plan(mode):
    return JudgePlan(mode=mode, arm_count=1, bundle_dir="judge_bundles")


@pytest.mark.parametrize(
    "mode, deferred, bundle, inline",
    [
        (MODE_INLINE, False, False, True),
        (MODE_DEFERRED, True, True, False),
        (MODE_INLINE_BUNDLE, False, True, True),
    ],
)
def test_the_predicates_describe_each_mode(mode, deferred, bundle, inline):
    plan = _plan(mode)
    assert is_deferred(plan) is deferred
    assert writes_bundle(plan) is bundle
    assert scores_inline(plan) is inline


def test_no_plan_reads_as_inline_for_every_predicate():
    assert is_deferred(None) is False
    assert writes_bundle(None) is False
    assert scores_inline(None) is True
