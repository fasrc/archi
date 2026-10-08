"""Cover `archi restart`'s source-flag normalization (fasrc/archi#461).

`restart` and `create` both resolve `enabled_sources` from the new config, but
only `create` writes the result back with `set_sources_enabled` before
comparing against the deployed config. Without that call, a raw re-render of
an unmodified config carries the template default (`true`) for `git`, `sso`,
`jira`, and `redmine`, while the deployed config (written by `create`) carries
an explicit `false` -- so `restart` refuses a config it never actually
changed.
"""

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from src.cli import cli_main
from src.cli.managers.config_manager import ConfigurationManager
from src.cli.source_registry import source_registry
from src.cli.utils.helpers import _render_config_for_compare

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = REPO_ROOT / "examples" / "deployments" / "basic-openai" / "config.yaml"
DEPLOYMENT_NAME = "my_archi"

# Marker used to halt `restart` right after the restricted-section comparison,
# before it would try to build a real compose plan.
SENTINEL = "stopped-before-compose-build"


@pytest.fixture
def archi_home(tmp_path, monkeypatch):
    """Point the CLI at a throwaway ARCHI_DIR.

    cli_main resolves ARCHI_DIR into a module-level constant at import time, so
    setting the env var alone only works for whichever test imports the module
    first; the attribute has to be patched too (see test_cli_create_dev_smoke.py).
    """
    home = tmp_path / "archi-home"
    monkeypatch.setenv("ARCHI_DIR", str(home))
    monkeypatch.setattr(cli_main, "ARCHI_DIR", str(home))
    return home


@pytest.fixture(autouse=True)
def docker_available(monkeypatch):
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)


@pytest.fixture(autouse=True)
def stop_before_compose_build(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(
        cli_main.ServiceBuilder, "build_compose_config", staticmethod(_raise)
    )


def _build_deployed_config():
    """Render the deployed config the way `create` does: load, normalize, render."""
    config_manager = ConfigurationManager([str(EXAMPLE_CONFIG)], cli_main.env)
    config_defined_sources = config_manager.get_enabled_sources()
    config_disabled_sources = config_manager.get_disabled_sources()
    enabled_sources = list(dict.fromkeys(["links"] + config_defined_sources))
    enabled_sources = [
        src for src in enabled_sources if src not in config_disabled_sources
    ]
    enabled_sources = source_registry.resolve_dependencies(enabled_sources)
    config_manager.set_sources_enabled(enabled_sources)
    return _render_config_for_compare(config_manager.config, False, 3, cli_main.env)


def _write_deployment(archi_home, deployed_config):
    deployment_dir = archi_home / f"archi-{DEPLOYMENT_NAME}"
    (deployment_dir / "configs").mkdir(parents=True)
    compose = {"services": {"chatbot": {}, "postgres": {}}}
    (deployment_dir / "compose.yaml").write_text(yaml.safe_dump(compose))
    (deployment_dir / "configs" / f"{DEPLOYMENT_NAME}.yaml").write_text(
        yaml.safe_dump(deployed_config)
    )
    return deployment_dir


def test_omitted_enabled_key_matches_deployed_false(archi_home):
    """The new config omits git.enabled; the deployed config has it `false`."""
    _write_deployment(archi_home, _build_deployed_config())

    runner = CliRunner()
    result = runner.invoke(
        cli_main.restart,
        ["--name", DEPLOYMENT_NAME, "--config", str(EXAMPLE_CONFIG)],
    )

    assert result.exception is not None and SENTINEL in str(result.exception), (
        f"expected restart to reach the compose build. exit_code={result.exit_code}\n"
        f"output:\n{result.output}\nexception:\n{result.exception}\n"
    )
    assert (
        "Restart config changes are restricted" not in result.output
    ), f"an unmodified config should not be refused. output:\n{result.output}\n"


def test_set_sources_enabled_runs_before_validation(archi_home, monkeypatch):
    _write_deployment(archi_home, _build_deployed_config())

    calls = []

    original_set_sources_enabled = ConfigurationManager.set_sources_enabled
    original_validate_configs = ConfigurationManager.validate_configs
    original_validate_non_chatbot = cli_main._validate_non_chatbot_sections

    def recording_set_sources_enabled(self, *args, **kwargs):
        calls.append("set_sources_enabled")
        return original_set_sources_enabled(self, *args, **kwargs)

    def recording_validate_configs(self, *args, **kwargs):
        calls.append("validate_configs")
        return original_validate_configs(self, *args, **kwargs)

    def recording_validate_non_chatbot(*args, **kwargs):
        calls.append("_validate_non_chatbot_sections")
        return original_validate_non_chatbot(*args, **kwargs)

    monkeypatch.setattr(
        ConfigurationManager, "set_sources_enabled", recording_set_sources_enabled
    )
    monkeypatch.setattr(
        ConfigurationManager, "validate_configs", recording_validate_configs
    )
    monkeypatch.setattr(
        cli_main, "_validate_non_chatbot_sections", recording_validate_non_chatbot
    )

    runner = CliRunner()
    runner.invoke(
        cli_main.restart,
        ["--name", DEPLOYMENT_NAME, "--config", str(EXAMPLE_CONFIG)],
    )

    assert "set_sources_enabled" in calls, f"recorded calls: {calls}"
    assert calls.index("set_sources_enabled") < calls.index("validate_configs")
    assert calls.index("set_sources_enabled") < calls.index(
        "_validate_non_chatbot_sections"
    )


def test_real_data_manager_change_still_refused(archi_home, tmp_path):
    """A new config that genuinely flips `git.enabled` is still refused.

    The example's input list carries a `git-` entry, so whether the deployed
    config has `git` enabled depends on input-list inference (#460); flip
    whatever value was deployed rather than assume one.
    """
    deployed_config = _build_deployed_config()
    _write_deployment(archi_home, deployed_config)
    deployed_git = deployed_config["data_manager"]["sources"]["git"]["enabled"]

    new_config = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    new_config["data_manager"]["sources"]["git"] = {"enabled": not deployed_git}
    new_config_path = tmp_path / "new_config.yaml"
    new_config_path.write_text(yaml.safe_dump(new_config))

    runner = CliRunner()
    result = runner.invoke(
        cli_main.restart,
        ["--name", DEPLOYMENT_NAME, "--config", str(new_config_path)],
    )

    assert "Restart config changes are restricted" in result.output, (
        f"a real data_manager change should still be refused. "
        f"exit_code={result.exit_code}\noutput:\n{result.output}\n"
    )
