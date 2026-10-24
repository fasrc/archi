"""Pin that judge_mode, judge_bundle_dir and evaluator_model_revision render
when set and stay absent when unset, and that the rendered values keep their
type (no YAML 1.1 coercion via bare interpolation)."""

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, PackageLoader, select_autoescape

from src.utils.judge_bundle import parse_judge_mode


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


def _ragas(cfg):
    return cfg["services"]["benchmarking"]["mode_settings"]["ragas_settings"]


def _render_ragas(ragas_settings):
    return _ragas(
        _render({"benchmarking": {"mode_settings": {"ragas_settings": ragas_settings}}})
    )


# --- keys absent when unset -------------------------------------------------


def test_judge_keys_absent_when_none_set():
    ragas = _render_ragas({})
    assert "judge_mode" not in ragas
    assert "judge_bundle_dir" not in ragas
    assert "evaluator_model_revision" not in ragas


# --- each key renders when set ----------------------------------------------


def test_judge_mode_renders_when_set():
    ragas = _render_ragas({"judge_mode": "deferred"})
    assert ragas["judge_mode"] == "deferred"


def test_judge_bundle_dir_renders_when_set():
    ragas = _render_ragas({"judge_bundle_dir": "bundles/x"})
    assert ragas["judge_bundle_dir"] == "bundles/x"


def test_evaluator_model_revision_renders_when_set():
    ragas = _render_ragas({"evaluator_model_revision": "0123abc"})
    assert ragas["evaluator_model_revision"] == "0123abc"


# --- absent when None -------------------------------------------------------


def test_judge_mode_absent_when_none():
    ragas = _render_ragas({"judge_mode": None})
    assert "judge_mode" not in ragas


def test_judge_bundle_dir_absent_when_none():
    ragas = _render_ragas({"judge_bundle_dir": None})
    assert "judge_bundle_dir" not in ragas


def test_evaluator_model_revision_absent_when_none():
    ragas = _render_ragas({"evaluator_model_revision": None})
    assert "evaluator_model_revision" not in ragas


# --- judge_mode: false survives the render and parse_judge_mode refuses it --


def test_judge_mode_false_survives_the_render():
    ragas = _render_ragas({"judge_mode": False})
    assert ragas["judge_mode"] is False


def test_rendered_false_judge_mode_is_refused_by_parse_judge_mode():
    ragas = _render_ragas({"judge_mode": False})
    with pytest.raises(ValueError):
        parse_judge_mode(ragas["judge_mode"])


# --- YAML 1.1 retype guard on evaluator_model_revision ----------------------
# "1e3" and "null" are examples of strings YAML 1.1 would silently retype
# (to float 1000.0 and None respectively) if interpolated bare.


def test_revision_1e3_keeps_string_type():
    ragas = _render_ragas({"evaluator_model_revision": "1e3"})
    assert ragas["evaluator_model_revision"] == "1e3"
    assert type(ragas["evaluator_model_revision"]) is str


def test_revision_null_keeps_string_type():
    ragas = _render_ragas({"evaluator_model_revision": "null"})
    assert ragas["evaluator_model_revision"] == "null"
    assert type(ragas["evaluator_model_revision"]) is str
