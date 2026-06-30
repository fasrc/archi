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

import yaml
from jinja2 import ChainableUndefined, Environment, PackageLoader, select_autoescape


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
