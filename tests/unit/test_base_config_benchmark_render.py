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


# --- primary_metric and enabled_metrics -------------------------------------
# Benchmarker.run ranks the sweep leaderboard by
# services.benchmarking.primary_metric of the RENDERED config, and judges only
# mode_settings.ragas_settings.enabled_metrics; a key the template drops is gone.

DEFAULT_METRICS = [
    "answer_relevancy",
    "faithfulness",
    "context_precision",
    "context_recall",
]


def _ragas(cfg):
    return cfg["services"]["benchmarking"]["mode_settings"]["ragas_settings"]


def test_primary_metric_renders_when_set():
    cfg = _render({"benchmarking": {"primary_metric": "noise_sensitivity"}})
    assert cfg["services"]["benchmarking"]["primary_metric"] == "noise_sensitivity"


def test_primary_metric_defaults_to_faithfulness():
    cfg = _render({"benchmarking": {}})
    assert cfg["services"]["benchmarking"]["primary_metric"] == "faithfulness"


def test_enabled_metrics_read_from_the_documented_mode_settings_key():
    metrics = ["faithfulness", "noise_sensitivity"]
    cfg = _render(
        {
            "benchmarking": {
                "mode_settings": {"ragas_settings": {"enabled_metrics": metrics}}
            }
        }
    )
    assert _ragas(cfg)["enabled_metrics"] == metrics


def test_enabled_metrics_still_read_from_the_legacy_key():
    """Configs written against the old template spelling keep working."""
    metrics = ["faithfulness", "answer_correctness"]
    cfg = _render({"benchmarking": {"ragas_settings": {"enabled_metrics": metrics}}})
    assert _ragas(cfg)["enabled_metrics"] == metrics


def test_enabled_metrics_documented_key_wins_over_the_legacy_key():
    cfg = _render(
        {
            "benchmarking": {
                "mode_settings": {
                    "ragas_settings": {"enabled_metrics": ["noise_sensitivity"]}
                },
                "ragas_settings": {"enabled_metrics": ["faithfulness"]},
            }
        }
    )
    assert _ragas(cfg)["enabled_metrics"] == ["noise_sensitivity"]


def test_enabled_metrics_default_when_unset():
    cfg = _render({"benchmarking": {}})
    assert _ragas(cfg)["enabled_metrics"] == DEFAULT_METRICS


def test_sweep_generator_primary_metric_survives_rendering(tmp_path):
    """Source config → generate_prompt_sweep → template render → runtime value."""
    from scripts.benchmarking.generate_prompt_sweep import generate_sweep_configs

    base = {"services": {"benchmarking": {"modes": ["RAGAS"]}}}
    (tmp_path / "base.yaml").write_text(yaml.safe_dump(base))
    prompts = []
    for stem in ("arm-a", "arm-b"):
        prompt = tmp_path / f"{stem}.md"
        prompt.write_text(f"# {stem}\n")
        prompts.append(str(prompt))
    manifest = {
        "base_config": str(tmp_path / "base.yaml"),
        "out_dir": str(tmp_path / "out"),
        "prompts": prompts,
        "primary_metric": "noise_sensitivity",
    }
    (tmp_path / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    for path in generate_sweep_configs(tmp_path / "manifest.yaml"):
        source = yaml.safe_load(path.read_text())
        cfg = _render(source["services"])
        assert cfg["services"]["benchmarking"]["primary_metric"] == (
            "noise_sensitivity"
        )
