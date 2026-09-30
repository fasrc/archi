import pytest

from src.utils.evaluations_config import (
    AGENT_CONFIG_STAGED_FILENAME,
    LIVE_AGENT_CONFIG_PATH,
    resolve_agent_config_source,
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


@pytest.mark.parametrize(
    "spelling",
    [
        "//root/archi/configs/config.yaml",
        "///root/archi/configs/config.yaml",
        "/root/archi//configs/config.yaml",
    ],
)
def test_validate_evaluations_config_raises_for_repeated_slashes(spelling):
    """``posixpath.normpath`` keeps exactly two leading slashes; POSIX allows it.

    ``//root/archi/configs/config.yaml`` and the live config are one file, so the
    runtime seam refuses it. The validator has to collapse the doubled root
    itself, because normalization alone will not.
    """
    with pytest.raises(ValueError, match=_DOTTED_KEY):
        validate_evaluations_config(
            {"evaluations": {"enabled": True, "agent_config_path": spelling}}
        )


# Absolute spellings only. ``_is_live_agent_config`` answers a relative path by
# resolving it against the process working directory, which inside the chatbot is
# ``/root/archi`` (``Dockerfile-chat:4``) and in this test process is the pytest
# rootdir. A host test cannot adopt the container's workdir, so a relative
# spelling has no comparable runtime verdict to assert parity against.
_ABSOLUTE_SPELLINGS = [
    ("/root/archi/configs/config.yaml", True),
    ("//root/archi/configs/config.yaml", True),
    ("///root/archi/configs/config.yaml", True),
    ("/root/archi//configs/config.yaml", True),
    ("/root/archi/configs/../configs/config.yaml", True),
    ("/root/archi/./configs/config.yaml", True),
    ("/root/archi/configs/config.eval.yaml", False),
    ("/opt/redacted/config.yaml", False),
]


@pytest.mark.parametrize("spelling,is_live", _ABSOLUTE_SPELLINGS)
def test_preflight_verdict_matches_the_runtime_seam(spelling, is_live):
    """Create time and runtime must agree on every absolute spelling.

    The preflight exists only to report early what ``build_evaluation_service``
    would decide later. A spelling the two disagree on is a deployment that
    passes ``archi create`` and comes up with the console switched off.
    """
    from pathlib import Path

    from src.interfaces.chat_app.evaluation_console import _is_live_agent_config

    assert _is_live_agent_config(Path(spelling)) is is_live

    config = {"evaluations": {"enabled": True, "agent_config_path": spelling}}
    if is_live:
        with pytest.raises(ValueError, match=_DOTTED_KEY):
            validate_evaluations_config(config)
    else:
        assert validate_evaluations_config(config) is None


# --- resolve_agent_config_source and AGENT_CONFIG_STAGED_FILENAME ---


def _make_config(tmp_path, evaluations):
    yaml_path = tmp_path / "archi.yaml"
    yaml_path.write_text("# deployment config")
    return {
        "_config_path": str(yaml_path),
        "services": {"chat_app": {"evaluations": evaluations}},
    }


@pytest.mark.parametrize(
    "evaluations",
    [
        {},
        {"enabled": False},
        {"enabled": "true", "agent_config_path": "missing.yaml"},
    ],
)
def test_resolve_agent_config_source_returns_none_when_not_enabled(
    tmp_path, evaluations
):
    config = _make_config(tmp_path, evaluations)
    assert resolve_agent_config_source(config) is None


def test_resolve_agent_config_source_existing_absolute_file(tmp_path):
    src = tmp_path / "agent.yaml"
    src.write_text("agent config")
    config = _make_config(tmp_path, {"enabled": True, "agent_config_path": str(src)})
    assert resolve_agent_config_source(config) == src.resolve()


def test_resolve_agent_config_source_relative_resolves_against_yaml_dir(
    tmp_path, monkeypatch
):
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    src = eval_dir / "agent.yaml"
    src.write_text("agent config")
    config = _make_config(
        tmp_path, {"enabled": True, "agent_config_path": "eval/agent.yaml"}
    )
    monkeypatch.chdir("/tmp")
    assert resolve_agent_config_source(config) == src.resolve()


def test_resolve_agent_config_source_missing_file_raises(tmp_path):
    missing = tmp_path / "missing.yaml"
    config = _make_config(
        tmp_path, {"enabled": True, "agent_config_path": str(missing)}
    )
    with pytest.raises(ValueError) as exc_info:
        resolve_agent_config_source(config)
    msg = str(exc_info.value)
    assert _DOTTED_KEY in msg
    assert "not found" in msg
    assert str(missing.resolve()) in msg


def test_resolve_agent_config_source_directory_raises(tmp_path):
    d = tmp_path / "agent_dir"
    d.mkdir()
    config = _make_config(tmp_path, {"enabled": True, "agent_config_path": str(d)})
    with pytest.raises(ValueError, match="must be a file"):
        resolve_agent_config_source(config)


def test_resolve_agent_config_source_unreadable_file_raises(tmp_path):
    import os

    if os.geteuid() == 0:
        pytest.skip("running as root; file permissions are not enforced")
    src = tmp_path / "agent.yaml"
    src.write_text("agent config")
    src.chmod(0o000)
    config = _make_config(tmp_path, {"enabled": True, "agent_config_path": str(src)})
    with pytest.raises(ValueError, match="not readable"):
        resolve_agent_config_source(config)


def test_resolve_agent_config_source_relative_self_reference_raises(tmp_path):
    config = _make_config(
        tmp_path, {"enabled": True, "agent_config_path": "archi.yaml"}
    )
    with pytest.raises(ValueError, match="live deployment config"):
        resolve_agent_config_source(config)


def test_resolve_agent_config_source_absolute_self_reference_raises(tmp_path):
    yaml_path = tmp_path / "archi.yaml"
    yaml_path.write_text("deployment config")
    config = {
        "_config_path": str(yaml_path),
        "services": {
            "chat_app": {
                "evaluations": {"enabled": True, "agent_config_path": str(yaml_path)}
            }
        },
    }
    with pytest.raises(ValueError, match="live deployment config"):
        resolve_agent_config_source(config)


def test_resolve_agent_config_source_live_config_path_raises(tmp_path):
    config = _make_config(
        tmp_path,
        {"enabled": True, "agent_config_path": "/root/archi/configs/config.yaml"},
    )
    with pytest.raises(ValueError, match="live deployment config"):
        resolve_agent_config_source(config)


def test_resolve_agent_config_source_relative_without_config_path_raises():
    config = {
        "services": {
            "chat_app": {
                "evaluations": {"enabled": True, "agent_config_path": "eval/agent.yaml"}
            }
        }
    }
    with pytest.raises(ValueError, match=_DOTTED_KEY):
        resolve_agent_config_source(config)


def test_resolve_agent_config_source_blank_path_raises(tmp_path):
    config = _make_config(tmp_path, {"enabled": True, "agent_config_path": ""})
    with pytest.raises(ValueError, match="is required"):
        resolve_agent_config_source(config)


def test_resolve_agent_config_source_missing_path_key_raises(tmp_path):
    config = _make_config(tmp_path, {"enabled": True})
    with pytest.raises(ValueError, match="is required"):
        resolve_agent_config_source(config)


def test_agent_config_staged_filename():
    assert AGENT_CONFIG_STAGED_FILENAME == "qa_agent_config.yaml"
