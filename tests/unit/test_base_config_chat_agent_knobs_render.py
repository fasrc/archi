"""Regression (#670): chat-agent settings declared under ``services.chat_app`` must
survive the base-config render.

The agents read ``services.chat_app.force_initial_retrieval`` (default on) and
``services.chat_app.tool_budgets`` (default ``{"search_vectorstore_hybrid": 2}``),
but only keys the template emits reach the rendered runtime config that seeds
Postgres. A template that drops them leaves every deployment on the code defaults
whatever its YAML says.

Each key is emitted only when declared with a non-null value, so the defaults keep
living in the agents. ``null`` counts as undeclared: rendered as-is, it would reach
``.get("force_initial_retrieval", True)`` as ``None`` and turn forced retrieval off.
"""

import pytest
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
    return yaml.safe_load(rendered)["services"]["chat_app"]


@pytest.mark.parametrize("value", [False, True])
def test_force_initial_retrieval_survives_as_a_boolean(value):
    chat_app = _render({"force_initial_retrieval": value})

    assert chat_app["force_initial_retrieval"] is value


def test_tool_budgets_survive_unchanged():
    declared = {"search_vectorstore_hybrid": 3, "search_local_files": 5}

    chat_app = _render({"tool_budgets": declared})

    assert chat_app["tool_budgets"] == declared


@pytest.mark.parametrize("key", ["force_initial_retrieval", "tool_budgets"])
def test_undeclared_key_is_left_out(key):
    chat_app = _render({"recursion_limit": 50})

    assert key not in chat_app


@pytest.mark.parametrize("key", ["force_initial_retrieval", "tool_budgets"])
def test_null_key_is_left_out(key):
    chat_app = _render({key: None})

    assert key not in chat_app
