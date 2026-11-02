"""Pin that rendered judge_mode, judge_bundle_dir and evaluator_model_revision
keep their string types (no YAML 1.1 coercion), and that the type guards can
be detected by tests that can actually fail."""

import yaml
from jinja2 import ChainableUndefined, Environment, PackageLoader, select_autoescape

from src.utils.judge_bundle import parse_judge_mode


def _render(services):
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


def test_deferred_renders_and_parses_to_deferred():
    ragas = _render_ragas({"judge_mode": "deferred"})
    assert parse_judge_mode(ragas["judge_mode"]) == "deferred"


def test_judge_bundle_dir_2026_renders_as_string():
    ragas = _render_ragas({"judge_bundle_dir": "2026"})
    assert type(ragas["judge_bundle_dir"]) is str
    assert ragas["judge_bundle_dir"] == "2026"


def test_judge_bundle_dir_null_renders_as_string():
    ragas = _render_ragas({"judge_bundle_dir": "null"})
    assert type(ragas["judge_bundle_dir"]) is str
    assert ragas["judge_bundle_dir"] == "null"


def test_judge_mode_null_renders_as_string_and_parse_refuses():
    import pytest

    ragas = _render_ragas({"judge_mode": "null"})
    assert type(ragas["judge_mode"]) is str
    assert ragas["judge_mode"] == "null"
    with pytest.raises(ValueError):
        parse_judge_mode(ragas["judge_mode"])


def test_evaluator_model_revision_all_digit_sha_renders_as_string():
    ragas = _render_ragas({"evaluator_model_revision": "0123456"})
    assert type(ragas["evaluator_model_revision"]) is str
    assert ragas["evaluator_model_revision"] == "0123456"
