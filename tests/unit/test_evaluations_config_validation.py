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
