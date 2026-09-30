"""Tests for evaluations root path validation."""

from pathlib import Path

import pytest
import yaml

from src.utils.evaluations_root import EVALUATIONS_MOUNT_PATH, validate_evaluations_root


def _config_with_root(root):
    return {"evaluations": {"enabled": True, "root": root}}


def test_outside_root_raises():
    with pytest.raises(ValueError) as exc_info:
        validate_evaluations_root(_config_with_root("/data/evaluations"))
    msg = str(exc_info.value)
    assert "/data/evaluations" in msg
    assert "/root/archi/evaluations" in msg


def test_exact_mount_returns_none():
    result = validate_evaluations_root(_config_with_root(EVALUATIONS_MOUNT_PATH))
    assert result is None


def test_child_of_mount_returns_none():
    result = validate_evaluations_root(
        _config_with_root("/root/archi/evaluations/trial-a")
    )
    assert result is None


def test_prefix_sibling_raises():
    with pytest.raises(ValueError):
        validate_evaluations_root(_config_with_root("/root/archi/evaluations-backup"))


def test_traversal_raises():
    with pytest.raises(ValueError):
        validate_evaluations_root(
            _config_with_root("/root/archi/evaluations/../elsewhere")
        )


def test_relative_root_raises():
    with pytest.raises(ValueError) as exc_info:
        validate_evaluations_root(_config_with_root("evaluations"))
    msg = str(exc_info.value)
    assert "evaluations" in msg
    assert "absolute" in msg


# Task 1.4 — enabled gate and non-string cases


def test_disabled_console_skips_validation():
    config = {"evaluations": {"enabled": False, "root": "/data/evaluations"}}
    assert validate_evaluations_root(config) is None


def test_enabled_absent_skips_validation():
    config = {"evaluations": {"root": "/data/evaluations"}}
    assert validate_evaluations_root(config) is None


def test_enabled_string_true_skips_validation():
    config = {"evaluations": {"enabled": "true", "root": "/data/evaluations"}}
    assert validate_evaluations_root(config) is None


def test_no_evaluations_block_returns_none():
    assert validate_evaluations_root({}) is None


def test_none_root_returns_none():
    config = {"evaluations": {"enabled": True, "root": None}}
    assert validate_evaluations_root(config) is None


def test_integer_root_raises_naming_field_path():
    with pytest.raises(ValueError) as exc_info:
        validate_evaluations_root({"evaluations": {"enabled": True, "root": 123}})
    msg = str(exc_info.value)
    assert "services.chat_app.evaluations.root" in msg


def test_empty_string_root_raises_naming_field_path():
    with pytest.raises(ValueError) as exc_info:
        validate_evaluations_root({"evaluations": {"enabled": True, "root": ""}})
    msg = str(exc_info.value)
    assert "services.chat_app.evaluations.root" in msg


# Task 2.1 — _validate_chat_app_config wires in the evaluations root check


from src.cli.managers.config_manager import ConfigurationManager  # noqa: E402


def _chat_app_config(root):
    """A config whose only questionable field is the evaluations root.

    `agent_config_path` is present and deliberately not the live deployment
    config: `_validate_chat_app_config` also runs `validate_evaluations_config`
    (#330), which refuses an enabled console that names no path or names the live
    one, and it runs *before* the root check. Without a valid path here these
    tests would trip that sibling validator and never exercise the root
    validation they exist for.
    """
    return {
        "services": {
            "chat_app": {
                "agent_class": "MyAgent",
                "default_provider": "openai",
                "default_model": "gpt-4",
                "evaluations": {
                    "enabled": True,
                    "root": root,
                    "agent_config_path": "/root/archi/configs/agent_config.eval.yaml",
                },
            }
        }
    }


def _manager():
    mgr = object.__new__(ConfigurationManager)
    return mgr


def test_validate_chat_app_config_outside_root_raises():
    mgr = _manager()
    with pytest.raises(ValueError) as exc_info:
        mgr._validate_chat_app_config(
            _chat_app_config("/data/evaluations"), ["chatbot"]
        )
    msg = str(exc_info.value)
    assert "/data/evaluations" in msg
    assert "/root/archi/evaluations" in msg


def test_validate_chat_app_config_mounted_root_does_not_raise():
    mgr = _manager()
    mgr._validate_chat_app_config(
        _chat_app_config("/root/archi/evaluations"), ["chatbot"]
    )


def test_validate_chat_app_config_non_chatbot_service_skips():
    mgr = _manager()
    # data_manager service: should not raise even with an outside root
    mgr._validate_chat_app_config(
        _chat_app_config("/data/evaluations"), ["data_manager"]
    )


@pytest.mark.parametrize("block", [None, [], "", 0, {}])
def test_falsy_evaluations_block_is_treated_as_disabled(block):
    """``evaluations: null`` was an accepted, inert shape and must stay one.

    The runtime normalizes with ``chat_app_config.get("evaluations") or {}``
    (``evaluation_console.py:91``) and treats the result as disabled. A validator
    that raises ``AttributeError`` on the same config would refuse a deployment
    the runtime is happy to serve.
    """
    assert validate_evaluations_root({"evaluations": block}) is None


@pytest.mark.parametrize("block", ["/root/archi/evaluations", ["enabled"], 3])
def test_truthy_non_mapping_evaluations_block_raises_naming_field_path(block):
    """A malformed block earns an actionable error, never an ``AttributeError``."""
    with pytest.raises(ValueError) as exc_info:
        validate_evaluations_root({"evaluations": block})
    assert "services.chat_app.evaluations" in str(exc_info.value)


def test_outside_root_error_says_a_path_beneath_the_mount_is_allowed():
    """Spec scenario "A root outside the mounted path refuses the deployment".

    The scenario requires the error to name both paths AND to say a path beneath
    the mount is allowed. Without the third clause an operator reads "only
    /root/archi/evaluations is mounted" as "collapse your catalogs into that one
    directory", which defeats the knob's remaining purpose.
    """
    with pytest.raises(ValueError) as exc_info:
        validate_evaluations_root(_config_with_root("/data/evaluations"))
    msg = str(exc_info.value)
    assert "/data/evaluations" in msg
    assert EVALUATIONS_MOUNT_PATH in msg
    assert "beneath" in msg


def test_the_rendered_root_is_the_value_the_validator_checked():
    """The validator's verdict is void if rendering changes the value.

    The deployment config is loaded once, validated, then re-emitted through
    ``base-config.yaml`` and loaded again by the config seed. Interpolating the
    scalar raw inside double quotes makes that second load decode escape
    sequences the first load kept literal, so a root the validator read as a
    descendant of the mount reaches the runtime as an escape from it.
    """
    from jinja2 import ChainableUndefined, Environment, FileSystemLoader

    repository = Path(__file__).resolve().parents[2]
    environment = Environment(
        loader=FileSystemLoader(str(repository / "src/cli/templates")),
        undefined=ChainableUndefined,
    )

    escaped = "/root/archi/evaluations/" + "\\x2e\\x2e/" * 2 + "trial-a"
    chat_app = {"evaluations": {"enabled": True, "root": escaped}}

    assert validate_evaluations_root(chat_app) is None

    rendered = environment.get_template("base-config.yaml").render(
        services={"chat_app": chat_app}
    )
    reloaded = yaml.safe_load(rendered)["services"]["chat_app"]["evaluations"]["root"]

    assert reloaded == escaped
    assert (
        validate_evaluations_root({"evaluations": {"enabled": True, "root": reloaded}})
        is None
    )
