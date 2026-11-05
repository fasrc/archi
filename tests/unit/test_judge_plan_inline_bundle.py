"""Start rules of an ``inline+bundle`` run (add-inline-bundle-judge-mode, D3).

The plan carries the digests and the bundle folder like ``deferred``; it needs the
inline judge's model id but not a revision; a mixed sweep is refused.
"""

from __future__ import annotations

import re

import pytest
import yaml

from src.utils.judge_bundle import MODE_INLINE_BUNDLE, JudgeRunRefused
from tests.unit.test_judge_start_refusal import package_dir  # noqa: F401  (fixture)
from tests.unit.test_judge_start_refusal import (
    _inline_cfg,
    _plan,
    _stub_package_digest,
)

_UNSET = object()


def _cfg(tmp_path, name, *, evaluator_model=_UNSET, revision=_UNSET, bundle_dir=_UNSET):
    ragas: dict = {"judge_mode": "inline+bundle", "evaluator_model": "sonnet/judge"}
    if evaluator_model is not _UNSET:
        ragas["evaluator_model"] = evaluator_model
    if revision is not _UNSET:
        ragas["evaluator_model_revision"] = revision
    if bundle_dir is not _UNSET:
        ragas["judge_bundle_dir"] = bundle_dir
    cfg = {"services": {"benchmarking": {"mode_settings": {"ragas_settings": ragas}}}}
    path = tmp_path / name
    path.write_text(yaml.safe_dump(cfg))
    return str(path)


def test_the_plan_carries_the_digests_and_the_bundle_folder(tmp_path, package_dir):
    paths = [_cfg(tmp_path, "a.yaml"), _cfg(tmp_path, "b.yaml")]

    plan = _plan(paths, package_dir, tmp_path)

    assert plan.mode == MODE_INLINE_BUNDLE
    assert plan.arm_count == 2
    assert plan.bundle_dir == "judge_bundles"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", plan.code_version["digest"])
    assert plan.package_digest == _stub_package_digest()["digest"]


def test_no_revision_is_fine(tmp_path, package_dir):
    plan = _plan([_cfg(tmp_path, "a.yaml")], package_dir, tmp_path)
    assert plan.mode == MODE_INLINE_BUNDLE


@pytest.mark.parametrize("revision", ["", None])
def test_an_empty_revision_is_also_fine(tmp_path, package_dir, revision):
    plan = _plan([_cfg(tmp_path, "a.yaml", revision=revision)], package_dir, tmp_path)
    assert plan.mode == MODE_INLINE_BUNDLE


@pytest.mark.parametrize("model", ["", None])
def test_a_missing_judge_model_refuses_and_names_the_config(
    tmp_path, package_dir, model
):
    path = _cfg(tmp_path, "a.yaml", evaluator_model=model)
    with pytest.raises(JudgeRunRefused) as exc_info:
        _plan([path], package_dir, tmp_path)
    assert path in str(exc_info.value)
    assert "evaluator_model" in str(exc_info.value)


def test_an_unavailable_package_digest_refuses_and_quotes_the_source(
    tmp_path, package_dir
):
    def _unavailable():
        return {"digest": None, "source": "<unavailable: pip exited 3>"}

    with pytest.raises(JudgeRunRefused) as exc_info:
        _plan(
            [_cfg(tmp_path, "a.yaml")],
            package_dir,
            tmp_path,
            package_digest_fn=_unavailable,
        )
    assert "<unavailable: pip exited 3>" in str(exc_info.value)


def test_an_absolute_bundle_dir_refuses(tmp_path, package_dir):
    with pytest.raises(JudgeRunRefused) as exc_info:
        _plan([_cfg(tmp_path, "a.yaml", bundle_dir="/abs")], package_dir, tmp_path)
    assert "judge_bundle_dir" in str(exc_info.value)


def test_mixed_with_an_inline_config_refuses_and_names_both(tmp_path, package_dir):
    paths = [_cfg(tmp_path, "a.yaml"), _inline_cfg(tmp_path, "b.yaml")]
    with pytest.raises(JudgeRunRefused) as exc_info:
        _plan(paths, package_dir, tmp_path)
    msg = str(exc_info.value)
    assert paths[0] in msg and paths[1] in msg
    assert "inline+bundle" in msg
