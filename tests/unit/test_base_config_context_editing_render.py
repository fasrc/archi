"""Regression: the deployed base-config template must carry the in-loop
context-editing block through to the rendered runtime config.

``services.chat_app.context_editing`` is read by ``read_settings`` when the agent
builds its middleware. The CLI renders ``base-config.yaml`` with Jinja and **only
emitted keys survive** into ``/root/archi/configs/*.yaml``, which is what seeds
Postgres and therefore what the running app sees. If the template drops the block,
an operator who declares it in their source config loses it at render time.

That matters more here than for a tuning knob. ``context_window`` is the only way
to declare a window for a model named in the deployment config —
``resolve_configured_model_window`` deliberately refuses the provider's fabricated
``ModelInfo`` default for those — so losing the block leaves exactly the
self-hosted deployments this bound exists to protect with no bound installed at
all.

These tests pin that the block renders when set and stays absent when unset, so
the defaults keep living in one place (``context_budget.py``) rather than being
duplicated into the template.
"""

import yaml
from jinja2 import ChainableUndefined, Environment, PackageLoader, select_autoescape


def _render(chat_app):
    # Mirror the env in src/cli/cli_main.py (PackageLoader + ChainableUndefined).
    env = Environment(
        loader=PackageLoader("src.cli"),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    template = env.get_template("base-config.yaml")
    rendered = template.render(verbosity=0, services={"chat_app": chat_app})
    return yaml.safe_load(rendered)


def test_context_editing_rendered_when_set():
    # The shape a 32768-token self-hosted deployment declares: keep=1 because the
    # exemption does not survive the irreducible-floor guard at the stock keep=3.
    cfg = _render({"context_editing": {"context_window": 32768, "keep": 1}})

    context_editing = cfg["services"]["chat_app"]["context_editing"]
    assert context_editing["context_window"] == 32768
    assert context_editing["keep"] == 1


def test_every_declared_knob_survives_the_render():
    # An unknown-key-tolerant passthrough, so adding a knob to
    # ContextEditingSettings does not need a second edit here to reach the app.
    declared = {
        "enabled": True,
        "context_window": 65536,
        "reserve_fraction": 0.2,
        "margin_fraction": 0.3,
        "keep": 2,
        "per_result_tokens": 1800,
        "exemption_fraction": 0.25,
    }

    cfg = _render({"context_editing": declared})

    assert cfg["services"]["chat_app"]["context_editing"] == declared


def test_disabling_the_bound_survives_the_render():
    # Only a real YAML boolean disables it, so the false must arrive as one.
    cfg = _render({"context_editing": {"enabled": False}})

    assert cfg["services"]["chat_app"]["context_editing"]["enabled"] is False


def test_context_editing_absent_when_unset_preserves_defaults():
    cfg = _render({"recursion_limit": 50})

    # Key omitted → read_settings applies its protective built-in defaults.
    assert "context_editing" not in cfg["services"]["chat_app"]
