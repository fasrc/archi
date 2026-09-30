"""Regression: the deployed base-config template must carry the benchmark SUT's
``provider_mode`` override through to the rendered runtime config.

The CLI renders ``base-config.yaml`` with Jinja and only emitted keys survive into
``/root/archi/configs/*.yaml``. ``benchmark_sut.resolve_local_mode`` honours an
explicit ``services.benchmarking.provider_mode`` to force ``openai_compat`` for an
OpenAI-compatible endpoint whose base URL does not literally end in ``/v1`` — but
that override is useless if the template drops it at render time (issue #73 review).
These tests pin that the key renders when set and stays absent when unset (so the
``/v1`` auto-detect remains the default).
"""

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, PackageLoader, select_autoescape

from src.bin.benchmark_sut import resolve_local_mode


def _render(services):
    # Mirror the env in src/cli/cli_main.py (PackageLoader + ChainableUndefined).
    env = Environment(
        loader=PackageLoader("src.cli"),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    template = env.get_template("base-config.yaml")
    rendered = template.render(verbosity=0, services=services)
    return yaml.safe_load(rendered)


def test_provider_mode_rendered_when_set():
    cfg = _render(
        {"benchmarking": {"provider": "local", "provider_mode": "openai_compat"}}
    )
    assert cfg["services"]["benchmarking"]["provider_mode"] == "openai_compat"


def test_provider_mode_absent_when_unset_preserves_autodetect():
    cfg = _render({"benchmarking": {"provider": "local"}})
    # Key omitted → resolve_local_mode falls back to /v1 auto-detection.
    assert "provider_mode" not in cfg["services"]["benchmarking"]


# === PR #467 review: the render must select the key by presence, not truthiness ===


def test_false_provider_mode_survives_the_render():
    """``provider_mode: false`` must reach the validator instead of vanishing.

    YAML decodes ``provider_mode: false`` to ``False``. A truthiness guard in the
    template dropped the key, so ``apply_sut_local_provider`` read ``None`` and
    auto-detected a dialect the operator never asked for, and
    ``resolve_local_mode``'s refusal of a non-string could never fire on the CLI path.
    """
    cfg = _render({"benchmarking": {"provider": "local", "provider_mode": False}})
    assert cfg["services"]["benchmarking"]["provider_mode"] is False


def test_zero_provider_mode_survives_the_render():
    """``provider_mode: 0`` is the other falsy YAML spelling the guard swallowed.

    The type is asserted, not only the value: ``False == 0`` in Python, so a render
    that collapsed every falsy value to ``false`` would satisfy a bare equality check
    and the neighbouring ``is False`` test at the same time.
    """
    value = _render({"benchmarking": {"provider": "local", "provider_mode": 0}})[
        "services"
    ]["benchmarking"]["provider_mode"]
    assert type(value) is int
    assert value == 0


def test_rendered_false_provider_mode_is_refused_downstream():
    """The end the render serves: an unusable value reaches canonical_local_mode."""
    cfg = _render({"benchmarking": {"provider": "local", "provider_mode": False}})
    with pytest.raises(ValueError):
        resolve_local_mode(
            "http://gpu-vllm:8000", cfg["services"]["benchmarking"]["provider_mode"]
        )


def test_null_provider_mode_stays_absent_preserving_autodetect():
    """A key written with no value still means "not configured"."""
    cfg = _render({"benchmarking": {"provider": "local", "provider_mode": None}})
    assert "provider_mode" not in cfg["services"]["benchmarking"]


# === PR #467 review round 2: the render must not retype the operator's scalar ===

# YAML 1.1 re-reads these bare words as null or as a boolean, so a raw interpolation
# changes the type of a string the operator wrote before any validator sees it.
YAML_SPECIAL_MODE_STRINGS = ["null", "Null", "NULL", "~", "on", "off", "yes", "no"]


@pytest.mark.parametrize("raw", YAML_SPECIAL_MODE_STRINGS)
def test_a_yaml_special_mode_string_keeps_its_string_type(raw):
    """A configured string must reach the validator as the string that was written."""
    cfg = _render({"benchmarking": {"provider": "local", "provider_mode": raw}})
    assert cfg["services"]["benchmarking"]["provider_mode"] == raw


@pytest.mark.parametrize("raw", YAML_SPECIAL_MODE_STRINGS)
def test_a_yaml_special_mode_string_is_refused_not_auto_detected(raw):
    """``provider_mode: "null"`` is an unusable mode, not an absent one.

    Interpolated bare, it round-tripped to ``None`` and auto-detected a dialect the
    operator never asked for — the same silent substitution the presence guard fixed
    for ``false`` and ``0``.
    """
    cfg = _render({"benchmarking": {"provider": "local", "provider_mode": raw}})
    with pytest.raises(ValueError):
        resolve_local_mode(
            "http://gpu-vllm:8000", cfg["services"]["benchmarking"]["provider_mode"]
        )


def test_an_empty_mode_string_still_auto_detects():
    """The empty string stays the one configured value that means "not configured"."""
    cfg = _render({"benchmarking": {"provider": "local", "provider_mode": ""}})
    value = cfg["services"]["benchmarking"]["provider_mode"]
    assert resolve_local_mode("http://gpu-vllm:8000/v1", value) == "openai_compat"
