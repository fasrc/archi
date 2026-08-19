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


def _context(base_dir: Path, config: dict, needs_agent_specs: bool):
    class _CM:
        pass

    cm = _CM()
    cm.config = config
    return SimpleNamespace(
        config_manager=cm,
        base_dir=base_dir,
        benchmarking=False,
        needs_agent_specs=needs_agent_specs,
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
    mgr._stage_agents(_context(base_dir, config, needs_agent_specs=False))

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
        mgr._stage_agents(_context(base_dir, config, needs_agent_specs=True))


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
    # agents_dir validation lives in _validate_agent_specs_config, which keys on
    # "does any enabled service consume agent specs" rather than on chatbot.
    mgr._validate_agent_specs_config(cfg, ["chatbot"])


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


def test_validation_rejects_an_agents_dir_that_is_a_file(tmp_path):
    """agents_dir must be a directory; a regular file is refused up front."""
    not_a_dir = tmp_path / "agents.md"
    not_a_dir.write_text("# not a directory\n")

    with pytest.raises(ValueError, match="must be a directory"):
        _validate(not_a_dir)


def test_agent_consuming_services_are_declared_in_the_registry():
    """The set is derived from actual select_agent_spec() call sites.

    chat_app/app.py, piazza.py and redmine_mailer_integration/redmine.py call
    select_agent_spec(); grader_app and mattermost.py do not. Several services
    bind-mount data/agents without reading specs from it, so the mount is not
    the predicate — consuming the specs is.
    """
    from src.cli.service_registry import service_registry

    consuming = {
        name
        for name, svc in service_registry.get_all_services().items()
        if getattr(svc, "consumes_agent_specs", False)
    }
    assert consuming == {
        "chatbot",
        "piazza",
        "redmine-mailer",
    }, f"unexpected agent-spec consumers: {sorted(consuming)}"


def test_context_needs_agent_specs_covers_more_than_the_chatbot(tmp_path):
    """Keying this on chatbot alone strands every other agent-backed service.

    redmine-mailer reads the staged directory via select_agent_spec() and raises
    when it holds no specs, so an integration-only deployment must still stage.
    """
    from src.cli.managers.templates_manager import TemplateContext

    def _ctx(services):
        return TemplateContext(
            plan=SimpleNamespace(
                base_dir=tmp_path, get_enabled_services=lambda: list(services)
            ),
            config_manager=SimpleNamespace(config={}),
            secrets_manager=SimpleNamespace(),
            options={},
        )

    assert _ctx(["chatbot", "postgres"]).needs_agent_specs is True
    assert _ctx(["redmine-mailer", "postgres"]).needs_agent_specs is True
    assert _ctx(["piazza", "postgres"]).needs_agent_specs is True
    assert _ctx(["grader", "postgres"]).needs_agent_specs is False


def test_stage_agents_still_stages_for_an_integration_without_a_chatbot(tmp_path):
    """The regression guard: redmine-mailer without chatbot must still get agents."""
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()
    agents_src = tmp_path / "agents"
    agents_src.mkdir()
    (agents_src / "triage.md").write_text("# triage\n")

    config = {"services": {"chat_app": {"agents_dir": str(agents_src)}}}

    mgr = object.__new__(TemplateManager)
    mgr._stage_agents(_context(base_dir, config, needs_agent_specs=True))

    staged = sorted(p.name for p in (base_dir / "data" / "agents").iterdir())
    assert staged == ["triage.md"], (
        "an agent-backed integration without a chatbot was left with an empty "
        "bind mount"
    )


def test_input_list_directory_is_skipped_rather_than_crashing(tmp_path):
    """A directory in input_lists must not crash staging after the teardown.

    os.path.exists() is true for a directory, so staging reached
    shutil.copyfile() and raised IsADirectoryError -- inside
    prepare_deployment_files(), i.e. after a --force teardown. Staging already
    tolerates a missing input list by warning and skipping; a path that is not a
    regular file is no more usable, so it is treated the same way.
    """
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()

    a_directory = tmp_path / "lists-dir"
    a_directory.mkdir()
    a_real_list = tmp_path / "urls.txt"
    a_real_list.write_text("https://example.org\n")

    class _CM:
        config = {}

        def get_input_lists(self):
            return [str(a_directory), str(a_real_list)]

    context = SimpleNamespace(config_manager=_CM(), base_dir=base_dir)

    mgr = object.__new__(TemplateManager)
    mgr._copy_web_input_lists(context)

    staged = sorted(p.name for p in (base_dir / "weblists").iterdir())
    assert staged == ["urls.txt"], (
        f"the directory should be skipped and the real list still staged, "
        f"got {staged}"
    )


def _validate_agents_for(services, agents_dir=None, extra=None):
    """Run the agent-input validation for a given set of enabled services."""
    chat_cfg = dict(extra or {})
    if agents_dir is not None:
        chat_cfg["agents_dir"] = str(agents_dir)
    cfg = {"services": {"chat_app": chat_cfg}}
    mgr = object.__new__(ConfigurationManager)
    mgr._validate_agent_specs_config(cfg, services)


def test_agent_inputs_are_validated_for_integrations_without_a_chatbot(tmp_path):
    """Validation must follow the same predicate as staging, or they disagree.

    Round 3 widened *staging* to any agent-spec consumer but left *validation*
    keyed on chatbot, so a redmine-mailer deployment with a bad agents_dir
    passed validation and then failed in staging -- after the teardown. That is
    the same two-predicates-that-must-agree defect this change keeps hitting.
    """
    missing = tmp_path / "no-such-dir"

    with pytest.raises(ValueError, match="agents_dir"):
        _validate_agents_for(["redmine-mailer"], agents_dir=missing)

    with pytest.raises(ValueError, match="agents_dir"):
        _validate_agents_for(["piazza"], agents_dir=missing)


def test_agent_inputs_are_not_validated_when_nothing_consumes_them(tmp_path):
    """A grader-only deployment must not be refused for chat-app config."""
    _validate_agents_for(["grader"], agents_dir=tmp_path / "no-such-dir")
    _validate_agents_for(["grader"])


def test_agent_inputs_require_agents_dir_to_be_present_for_a_consumer():
    """A consumer with no agents_dir at all is refused up front, not in staging."""
    with pytest.raises(ValueError, match="agents_dir"):
        _validate_agents_for(["redmine-mailer"])


def test_chat_app_only_fields_are_not_required_for_an_integration(tmp_path):
    """redmine-mailer needs agent files, not a default_provider or agent_class.

    Widening the agents_dir requirement must not drag the rest of the chat-app
    schema along with it.
    """
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "triage.md").write_text("# triage\n")

    _validate_agents_for(["redmine-mailer"], agents_dir=agents)


def test_agent_dir_typo_still_gets_its_hint_for_a_consumer():
    """The 'did you mean agents_dir?' hint moved with the rest of the check."""
    cfg = {"services": {"chat_app": {"agent_dir": "/somewhere"}}}
    mgr = object.__new__(ConfigurationManager)

    with pytest.raises(ValueError, match="did you mean 'agent_dir'"):
        mgr._validate_agent_specs_config(cfg, ["redmine-mailer"])
