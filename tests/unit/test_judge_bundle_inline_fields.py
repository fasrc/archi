"""The manifest's ``judge_mode`` and the per-mode arm fields (add-inline-bundle-judge-mode, D5, D6)."""

from __future__ import annotations

import json

import pytest

from src.utils.judge_bundle import (
    MODE_DEFERRED,
    MODE_INLINE,
    MODE_INLINE_BUNDLE,
    judge_arm_fields,
    judge_status_of,
    pending_arm_fields,
    write_judge_bundle,
)

_BUNDLE = {"folder": "bench-20260101_000000-arm0", "digest": "sha256:" + "c" * 64}
_SETTINGS = {
    "timeout": 90,
    "max_workers": 4,
    "batch_size": False,
    "embedding_model": "HuggingFace",
    "evaluator_model": "sonnet/judge",
}


def _write(root, **overrides):
    kwargs = dict(
        result_basename="bench-20260101_000000",
        arm_index=0,
        arm_count=1,
        rows=[
            {
                "user_input": "q",
                "retrieved_contexts": ["c"],
                "response": "a",
                "reference": "r",
            }
        ],
        keys=["q1"],
        enabled_metrics=["faithfulness"],
        ragas_settings=_SETTINGS,
        verbosity=0,
        code_version={"digest": "sha256:" + "a" * 64},
        package_digest="sha256:" + "b" * 64,
    )
    kwargs.update(overrides)
    write_judge_bundle(root, **kwargs)
    return json.loads(
        (root / "bench-20260101_000000-arm0" / "manifest.json").read_text()
    )


def test_the_manifest_records_the_mode_that_wrote_it(tmp_path):
    manifest = _write(tmp_path, judge_mode=MODE_INLINE_BUNDLE)
    assert manifest["judge_mode"] == "inline+bundle"
    # The inline judge has no revision: the writer records null, not a refusal.
    assert manifest["judge_model"] == {"id": "sonnet/judge", "revision": None}


def test_the_manifest_mode_defaults_to_deferred_for_existing_callers(tmp_path):
    assert _write(tmp_path)["judge_mode"] == "deferred"


def test_deferred_arm_fields_are_the_pending_fields():
    assert judge_arm_fields(MODE_DEFERRED, _BUNDLE) == pending_arm_fields(_BUNDLE)


def test_inline_bundle_arm_fields_carry_the_bundle_and_no_status():
    fields = judge_arm_fields(MODE_INLINE_BUNDLE, _BUNDLE)
    assert fields == {"judge_bundle": dict(_BUNDLE)}
    assert judge_status_of(fields) == "inline"


def test_inline_arm_fields_are_empty_even_with_a_bundle():
    assert judge_arm_fields(MODE_INLINE, _BUNDLE) == {}


@pytest.mark.parametrize("mode", [MODE_INLINE, MODE_DEFERRED, MODE_INLINE_BUNDLE])
def test_no_bundle_gives_no_fields(mode):
    assert judge_arm_fields(mode, None) == {}


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError) as exc_info:
        judge_arm_fields("both", _BUNDLE)
    assert "'both'" in str(exc_info.value)
