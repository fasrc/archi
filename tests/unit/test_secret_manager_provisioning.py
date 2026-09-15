"""Characterization tests for ``src.cli.managers.secrets_manager.SecretsManager``.

These pin the behaviour of the four members a whole-file black reflow (#291)
touches, so the reflow has something to be measured against. They are
expected to pass **before** the reformat -- a failure here before the
reformat means the test encodes the wrong behaviour, not that it found a
regression:

- ``_get_model_based_secrets``
- ``write_secrets_to_files``
- ``write_env_file``
- ``get_env_file_path``
"""

import logging
from types import SimpleNamespace

import pytest

from src.cli.managers.secrets_manager import SecretsManager

MODULE_LOGGER = "src.cli.managers.secrets_manager"


def _write_env(tmp_path, **pairs):
    env_path = tmp_path / ".env"
    env_path.write_text("\n".join(f"{k}={v}" for k, v in pairs.items()) + "\n")
    return env_path


def _manager(tmp_path, config_manager=None, **env_pairs):
    env_pairs.setdefault("PG_PASSWORD", "pw")
    env_path = _write_env(tmp_path, **env_pairs)
    return SecretsManager(env_file_path=str(env_path), config_manager=config_manager)


def _config_manager(models_configs=None, configs=None):
    return SimpleNamespace(
        get_models_configs=lambda: models_configs if models_configs is not None else [],
        get_configs=lambda: configs if configs is not None else [],
    )


class TestGetModelBasedSecrets:
    def test_openai_and_anthropic_models_yield_both_keys(self, tmp_path):
        config_manager = _config_manager(
            models_configs=[
                {
                    "sut": {"model": "OpenAI:gpt-4o"},
                    "evaluator": {"model": "Anthropic:claude-3-haiku"},
                }
            ]
        )
        manager = _manager(tmp_path, config_manager=config_manager)

        secrets = manager._get_model_based_secrets()

        assert secrets == {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}

    def test_section_value_that_is_not_a_mapping_is_skipped_without_raising(
        self, tmp_path
    ):
        config_manager = _config_manager(
            models_configs=[{"not_a_section": "OpenAI:gpt-4o"}]
        )
        manager = _manager(tmp_path, config_manager=config_manager)

        secrets = manager._get_model_based_secrets()

        assert secrets == set()

    def test_open_source_model_name_adds_no_key_and_logs_not_enforced_warning(
        self, tmp_path, caplog
    ):
        config_manager = _config_manager(
            models_configs=[{"sut": {"model": "HuggingFace:meta-llama/Llama-3"}}]
        )
        manager = _manager(tmp_path, config_manager=config_manager)

        with caplog.at_level(logging.WARNING, logger=MODULE_LOGGER):
            secrets = manager._get_model_based_secrets()

        assert secrets == set()
        assert any("won't be explicitly enforced" in r.message for r in caplog.records)

    def test_a_non_mapping_section_does_not_stop_the_remaining_sections(self, tmp_path):
        """The bad section is skipped, not treated as the end of the scan.

        A single-section config cannot tell ``continue`` apart from ``break``.
        This one puts a valid section *after* the bad one, so a ``break`` here
        would drop ``OPENAI_API_KEY`` and turn the test red.
        """
        config_manager = _config_manager(
            models_configs=[
                {
                    "not_a_section": "Anthropic:claude-3-haiku",
                    "sut": {"model": "OpenAI:gpt-4o"},
                }
            ]
        )
        manager = _manager(tmp_path, config_manager=config_manager)

        secrets = manager._get_model_based_secrets()

        assert secrets == {"OPENAI_API_KEY"}

    def test_huit_bedrock_provider_requires_huit_api_key(self, tmp_path):
        config_manager = _config_manager(
            configs=[
                {"services": {"benchmarking": {"provider": "HUIT_Bedrock"}}},
            ]
        )
        manager = _manager(tmp_path, config_manager=config_manager)

        secrets = manager._get_model_based_secrets()

        assert secrets == {"HUIT_API_KEY"}

    def test_huit_bedrock_as_the_ragas_evaluator_requires_huit_api_key(self, tmp_path):
        """The evaluator arm of the provider check, not just the SUT arm.

        ``benchmarking.mode_settings.ragas_settings.evaluator_provider`` is the
        second half of the ``in (sut_provider, evaluator_provider)`` test, and
        black re-wrapped that expression, so the reflow touched it.
        """
        config_manager = _config_manager(
            configs=[
                {
                    "services": {
                        "benchmarking": {
                            "provider": "openai",
                            "mode_settings": {
                                "ragas_settings": {"evaluator_provider": "HUIT_Bedrock"}
                            },
                        }
                    }
                },
            ]
        )
        manager = _manager(tmp_path, config_manager=config_manager)

        secrets = manager._get_model_based_secrets()

        assert secrets == {"HUIT_API_KEY"}


class TestWriteSecretsToFiles:
    def test_each_secret_lands_in_its_own_lowercased_file_and_env_alongside(
        self, tmp_path
    ):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        manager = _manager(tmp_path, PG_PASSWORD="pw", OPENAI_API_KEY="sk-abc")

        manager.write_secrets_to_files(target_dir, {"PG_PASSWORD", "OPENAI_API_KEY"})

        secrets_dir = target_dir / "secrets"
        assert (secrets_dir / "pg_password.txt").read_text() == "pw"
        assert (secrets_dir / "openai_api_key.txt").read_text() == "sk-abc"
        env_lines = (target_dir / ".env").read_text().splitlines()
        assert set(env_lines) == {"PG_PASSWORD=pw", "OPENAI_API_KEY=sk-abc"}

    def test_secret_absent_from_env_raises_value_error_naming_it(self, tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        manager = _manager(tmp_path)

        with pytest.raises(ValueError, match="MISSING_SECRET"):
            manager.write_secrets_to_files(target_dir, {"MISSING_SECRET"})

        # The refusal must not leave an empty file behind for the caller to
        # mount as if it held a real secret.
        assert not (target_dir / "secrets" / "missing_secret.txt").exists()


class TestWriteEnvFile:
    def test_writes_one_line_per_resolvable_secret_and_skips_the_rest(self, tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        manager = _manager(tmp_path, PG_PASSWORD="pw")

        manager.write_env_file(target_dir, {"PG_PASSWORD", "MISSING_SECRET"})

        assert (target_dir / ".env").read_text() == "PG_PASSWORD=pw\n"


class TestGetEnvFilePath:
    def test_returns_the_loaded_path(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("PG_PASSWORD=pw\n")
        manager = SecretsManager(env_file_path=str(env_path), config_manager=None)

        assert manager.get_env_file_path() == env_path
