"""``handle_results`` must record the configuration the agent actually read.

The agent reads Postgres; ``handle_results`` read the YAML file from disk at
report time. When those diverged the report labelled the run with settings it
never used -- an 8192-token run was written up as 32768, which is undetectable
from the artifact alone.

The file is still recorded (it is what the operator selected, and what a sweep
varies), but it is no longer the only account of the run.
"""

import pytest
import yaml

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import ResultHandler
from src.utils.benchmark_provenance import config_fingerprint


@pytest.fixture(autouse=True)
def _reset_results(monkeypatch):
    monkeypatch.setattr(ResultHandler, "results", [])


def _write(tmp_path, config):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


FILE_CONFIG = {
    "services": {"chat_app": {"context_editing": {"context_window": 32768, "keep": 1}}}
}
RUNNING_CONFIG = {
    "services": {"chat_app": {"context_editing": {"context_window": 8192, "keep": 1}}}
}


def test_records_the_running_configuration_not_only_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "get_full_config", lambda: RUNNING_CONFIG)

    ResultHandler.handle_results(_write(tmp_path, FILE_CONFIG), {}, {})

    record = ResultHandler.results[0]
    assert record["configuration"] == FILE_CONFIG
    assert record["running_configuration"] == RUNNING_CONFIG


def test_names_the_setting_the_report_would_have_misattributed(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "get_full_config", lambda: RUNNING_CONFIG)

    ResultHandler.handle_results(_write(tmp_path, FILE_CONFIG), {}, {})

    assert ResultHandler.results[0]["configuration_divergence"] == [
        "services.chat_app.context_editing.context_window"
    ]


def test_divergence_is_empty_when_the_file_describes_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "get_full_config", lambda: FILE_CONFIG)

    ResultHandler.handle_results(_write(tmp_path, FILE_CONFIG), {}, {})

    assert ResultHandler.results[0]["configuration_divergence"] == []


def test_warns_when_the_run_did_not_use_the_selected_configuration(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(sb, "get_full_config", lambda: RUNNING_CONFIG)

    with caplog.at_level("WARNING"):
        ResultHandler.handle_results(_write(tmp_path, FILE_CONFIG), {}, {})

    assert "context_window" in caplog.text


def test_no_warning_when_the_configurations_agree(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(sb, "get_full_config", lambda: FILE_CONFIG)

    with caplog.at_level("WARNING"):
        ResultHandler.handle_results(_write(tmp_path, FILE_CONFIG), {}, {})

    assert caplog.text == ""


def test_an_unreadable_running_config_does_not_discard_the_results(
    tmp_path, monkeypatch
):
    """A finished benchmark must not lose its scores because Postgres was down."""

    def _boom():
        raise RuntimeError("Static config not initialized in Postgres.")

    monkeypatch.setattr(sb, "get_full_config", _boom)

    ResultHandler.handle_results(_write(tmp_path, FILE_CONFIG), {"q": 1}, {"total": 2})

    record = ResultHandler.results[0]
    assert record["single_question_results"] == {"q": 1}
    assert record["running_configuration"] is None
    assert "Postgres" in record["configuration_divergence"][0]


def test_each_config_record_carries_its_own_version_block(tmp_path, monkeypatch):
    """A sweep runs N configs in one invocation, so one stamp per file is a lie.

    ``benchmarking-bench-sweep-20260610_015120.json`` holds three arms
    (fasrc-cannon v1/v2/v3). A single version on the metadata block would label
    all three with whichever ran last -- the same misattribution as the
    8192-recorded-as-32768 failure, one level up.
    """
    monkeypatch.setattr(sb, "get_full_config", lambda: RUNNING_CONFIG)

    ResultHandler.handle_results(_write(tmp_path, FILE_CONFIG), {}, {})

    stamp = ResultHandler.results[0]["config_version"]
    assert stamp["digest"] == config_fingerprint(RUNNING_CONFIG)
    assert stamp["divergence_from_selected_file"] == [
        "services.chat_app.context_editing.context_window"
    ]


def test_two_arms_in_one_invocation_get_their_own_digests(tmp_path, monkeypatch):
    arm_a = {"services": {"chat_app": {"context_editing": {"context_window": 8192}}}}
    arm_b = {"services": {"chat_app": {"context_editing": {"context_window": 32768}}}}

    monkeypatch.setattr(sb, "get_full_config", lambda: arm_a)
    ResultHandler.handle_results(_write(tmp_path, arm_a), {}, {})
    monkeypatch.setattr(sb, "get_full_config", lambda: arm_b)
    ResultHandler.handle_results(_write(tmp_path, arm_b), {}, {})

    digests = [r["config_version"]["digest"] for r in ResultHandler.results]
    assert digests[0] != digests[1]


def test_prompts_are_mapped_in_the_file_config_as_before(tmp_path, monkeypatch):
    """The existing prompt-inlining behaviour is unchanged by the new fields."""
    prompt = tmp_path / "p.txt"
    prompt.write_text("PROMPT BODY")
    config = {
        "services": {"benchmarking": {"prompts": {"main": {"greet": str(prompt)}}}}
    }
    monkeypatch.setattr(sb, "get_full_config", lambda: config)

    ResultHandler.handle_results(_write(tmp_path, config), {}, {})

    recorded = ResultHandler.results[0]["configuration"]
    assert (
        recorded["services"]["benchmarking"]["prompts"]["main"]["greet"]
        == "PROMPT BODY"
    )
