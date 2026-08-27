import pytest

from src.utils.evaluations_config import (
    LIVE_AGENT_CONFIG_PATH,
    validate_evaluations_config,
)

_DOTTED_KEY = "services.chat_app.evaluations.agent_config_path"


@pytest.mark.parametrize(
    "chat_app_config",
    [
        None,
        {},
        {"evaluations": {}},
        {"evaluations": {"enabled": False}},
        {"evaluations": {"enabled": 1}},
        {"evaluations": {"enabled": "true"}},
    ],
)
def test_validate_evaluations_config_no_raise_when_not_enabled(chat_app_config):
    assert validate_evaluations_config(chat_app_config) is None


@pytest.mark.parametrize(
    "evaluations_block",
    [
        {"enabled": True},
        {"enabled": True, "agent_config_path": None},
        {"enabled": True, "agent_config_path": ""},
        {"enabled": True, "agent_config_path": "   "},
        {"enabled": True, "agent_config_path": 42},
    ],
)
def test_validate_evaluations_config_raises_missing_or_blank(evaluations_block):
    with pytest.raises(ValueError, match=_DOTTED_KEY):
        validate_evaluations_config({"evaluations": evaluations_block})


def test_validate_evaluations_config_raises_for_live_path():
    with pytest.raises(ValueError, match=_DOTTED_KEY):
        validate_evaluations_config(
            {
                "evaluations": {
                    "enabled": True,
                    "agent_config_path": LIVE_AGENT_CONFIG_PATH,
                }
            }
        )


def test_validate_evaluations_config_raises_for_live_path_dotdot():
    dotdot = LIVE_AGENT_CONFIG_PATH.replace(
        "configs/config.yaml", "configs/../configs/config.yaml"
    )
    with pytest.raises(ValueError, match=_DOTTED_KEY):
        validate_evaluations_config(
            {"evaluations": {"enabled": True, "agent_config_path": dotdot}}
        )


def test_validate_evaluations_config_accepts_redacted_copy():
    assert (
        validate_evaluations_config(
            {
                "evaluations": {
                    "enabled": True,
                    "agent_config_path": "/root/archi/configs/config.eval.yaml",
                }
            }
        )
        is None
    )


def test_validate_evaluations_config_raises_for_relative_live_path():
    """A relative path is what the container resolves, not what the host does.

    The chatbot image sets ``WORKDIR /root/archi``
    (``src/cli/templates/dockerfiles/Dockerfile-chat:4``), so ``configs/config.yaml``
    names the live deployment config at runtime and ``build_evaluation_service``
    refuses it. Create-time preflight has to agree.
    """
    with pytest.raises(ValueError, match=_DOTTED_KEY):
        validate_evaluations_config(
            {
                "evaluations": {
                    "enabled": True,
                    "agent_config_path": "configs/config.yaml",
                }
            }
        )


def test_validate_evaluations_config_raises_for_relative_live_path_dotdot():
    with pytest.raises(ValueError, match=_DOTTED_KEY):
        validate_evaluations_config(
            {
                "evaluations": {
                    "enabled": True,
                    "agent_config_path": "configs/../configs/config.yaml",
                }
            }
        )


def test_validate_evaluations_config_verdict_ignores_host_cwd(monkeypatch, tmp_path):
    """The verdict must not depend on where the operator ran ``archi create``."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match=_DOTTED_KEY):
        validate_evaluations_config(
            {
                "evaluations": {
                    "enabled": True,
                    "agent_config_path": "configs/config.yaml",
                }
            }
        )


def test_validate_evaluations_config_accepts_relative_redacted_copy():
    assert (
        validate_evaluations_config(
            {
                "evaluations": {
                    "enabled": True,
                    "agent_config_path": "configs/config.eval.yaml",
                }
            }
        )
        is None
    )
