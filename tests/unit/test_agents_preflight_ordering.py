"""Agent staging must not raise after the deployment has been torn down.

`archi create --force` now performs its destructive teardown only after every
step that can refuse the deployment (fasrc/archi#287). `TemplateManager._stage_agents()`
runs *after* that point — it is part of writing the new deployment — so anything it
can reject has to be either rejected earlier or not rejected at all.

Two routes reached it after the teardown:

1. A deployment with no chatbot (the grader-only flow,
   `examples/deployments/grading/config.yaml`, whose services are `grader_app`
   only) has no `services.chat_app` section, so config validation skips the
   chat-app checks entirely — yet agent staging ran unconditionally and raised
   "Missing required services.chat_app.agents_dir in config."
2. The validator's `glob("*.md")` and the stager's `is_file() and suffix.lower()`
   are different predicates, so inputs existed that passed one and failed the other.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.cli.managers.config_manager import ConfigurationManager
from src.cli.managers.templates_manager import TemplateManager


def _context(base_dir: Path, config: dict, chatbot: bool):
    class _CM:
        pass

    cm = _CM()
    cm.config = config
    return SimpleNamespace(
        config_manager=cm,
        base_dir=base_dir,
        benchmarking=False,
        chatbot=chatbot,
    )


def test_stage_agents_skips_a_deployment_without_a_chatbot(tmp_path):
    """The grader-only flow has no chat_app section and must not be refused for it.

    Agents exist to be served by the chat app; a deployment without one has
    nothing to serve them. Raising here cost the operator their deployment,
    because this runs after the forced teardown.
    """
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    config = {"services": {"grader_app": {"port": 8080}}}

    mgr = object.__new__(TemplateManager)
    mgr._stage_agents(_context(base_dir, config, chatbot=False))

    assert not (
        base_dir / "data" / "agents"
    ).exists(), "no agents should be staged for a deployment with no chat app"


def test_stage_agents_still_requires_agents_dir_when_chatbot_is_enabled(tmp_path):
    """Do not over-skip: with a chatbot enabled the requirement still holds."""
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    config = {"services": {"chat_app": {}}}

    mgr = object.__new__(TemplateManager)
    with pytest.raises(ValueError, match="agents_dir"):
        mgr._stage_agents(_context(base_dir, config, chatbot=True))


def _validate(agents_dir: Path):
    cfg = {
        "services": {
            "chat_app": {
                "agent_class": "ArchiAgent",
                "agents_dir": str(agents_dir),
                "default_provider": "openai",
                "default_model": "gpt-4o",
            }
        }
    }
    mgr = object.__new__(ConfigurationManager)
    mgr._validate_chat_app_config(cfg, ["chatbot"])


def test_validation_rejects_a_directory_named_like_a_markdown_file(tmp_path):
    """glob('*.md') matches directories; the stager requires is_file().

    A directory named `notes.md` satisfied the validator and then failed staging
    with "No agent markdown files found" — after the teardown.
    """
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "notes.md").mkdir()

    with pytest.raises(ValueError, match="at least one .md file"):
        _validate(agents_dir)


def test_validation_accepts_an_uppercase_markdown_suffix(tmp_path):
    """The stager accepts .MD via suffix.lower(); the validator's glob did not.

    This is the same predicate mismatch in the opposite direction: a file the
    deployment would happily stage was rejected up front.
    """
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "AGENT.MD").write_text("# agent\n")

    _validate(agents_dir)


def test_validation_accepts_a_normal_markdown_file(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "agent.md").write_text("# agent\n")

    _validate(agents_dir)


def test_context_chatbot_property_reads_the_deployment_plan(tmp_path):
    """The stub context above short-circuits the property; exercise the real one."""
    from src.cli.managers.templates_manager import TemplateContext

    def _plan(services):
        return SimpleNamespace(
            base_dir=tmp_path, get_enabled_services=lambda: list(services)
        )

    def _ctx(services):
        return TemplateContext(
            plan=_plan(services),
            config_manager=SimpleNamespace(config={}),
            secrets_manager=SimpleNamespace(),
            options={},
        )

    assert _ctx(["chatbot", "postgres"]).chatbot is True
    assert _ctx(["grader_app", "postgres"]).chatbot is False


def test_validation_rejects_an_agents_dir_that_is_a_file(tmp_path):
    """agents_dir must be a directory; a regular file is refused up front."""
    not_a_dir = tmp_path / "agents.md"
    not_a_dir.write_text("# not a directory\n")

    with pytest.raises(ValueError, match="must be a directory"):
        _validate(not_a_dir)
