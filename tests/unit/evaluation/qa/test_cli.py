import sys
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import src.cli.qa_eval as qa_cli_module
import src.evaluation.qa.workflow as workflow_module
from src.cli.qa_eval import DEPRECATION_NOTICE, eval_cli, qa_cli
from src.evaluation.qa.artifacts import read_json


class _Workflow:
    def __init__(self, calls):
        self.calls = calls

    def composite(self, **kwargs):
        self.calls.append(kwargs)
        return {"run_id": "run-1", "status": "scored"}


def test_composite_cli_uses_prd_option_shape(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(qa_cli_module, "QAWorkflow", lambda: _Workflow(calls))

    result = CliRunner().invoke(
        qa_cli,
        [
            "--dataset",
            str(tmp_path / "data.json"),
            "--agent-config",
            str(tmp_path / "agent.yaml"),
            "--agent-spec",
            str(tmp_path / "agent.md"),
            "--output-dir",
            str(tmp_path / "run"),
            "-n",
            "4",
            "--run-workers",
            "3",
            "--score-workers",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output == "QA evaluation completed: run-1 (scored)\n"
    assert calls[0]["attempts"] == 4
    assert calls[0]["run_workers"] == 3
    assert calls[0]["score_workers"] == 2
    assert calls[0]["dataset"] == tmp_path / "data.json"


def test_composite_cli_rejects_worker_counts_above_the_supported_limit():
    result = CliRunner().invoke(qa_cli, ["--run-workers", "17"])

    assert result.exit_code == 2
    assert "17 is not in the range 1<=x<=16" in result.output


def test_staged_cli_passes_each_worker_count_only_to_its_phase(monkeypatch, tmp_path):
    calls = []

    class Workflow:
        def run(self, *args, **kwargs):
            calls.append(("run", args, kwargs))
            return {"run_id": "run-1", "status": "run_completed"}

        def score(self, *args, **kwargs):
            calls.append(("score", args, kwargs))
            return {"run_id": "run-1", "status": "scored"}

    monkeypatch.setattr(qa_cli_module, "QAWorkflow", Workflow)
    run_result = CliRunner().invoke(
        qa_cli,
        [
            "run",
            str(tmp_path / "run"),
            "--agent-config",
            str(tmp_path / "agent.yaml"),
            "--agent-spec",
            str(tmp_path / "agent.md"),
            "--run-workers",
            "5",
        ],
    )
    score_result = CliRunner().invoke(
        qa_cli,
        [
            "score",
            str(tmp_path / "run"),
            "--score-workers",
            "6",
        ],
    )

    assert run_result.exit_code == 0, run_result.output
    assert score_result.exit_code == 0, score_result.output
    assert calls[0][2] == {"run_workers": 5}
    assert calls[1][2] == {"score_workers": 6}


def test_composite_cli_reports_missing_required_flags():
    result = CliRunner().invoke(qa_cli, [])

    assert result.exit_code == 2
    assert (
        "requires --dataset, --agent-config, --agent-spec, --output-dir"
        in result.output
    )


def test_composite_cli_runs_local_dataset_to_report_with_four_attempts(
    monkeypatch, tmp_path
):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        '[{"id":"item","question":"Q","answer":"A",'
        '"time_sensitive":false,"expected_atoms":[{"id":"g1",'
        '"text":"A","required":true}]}]'
    )

    class Evaluator:
        def compare(self, question, gold_atoms, answer):
            return {
                "judgments": [
                    {
                        "atom_id": "g1",
                        "outcome": "entailed",
                        "rationale": "match",
                    }
                ]
            }

    class Agent:
        tool_calls = []

        def run(self, question):
            return "A"

    config = {
        "services": {
            "chat_app": {
                "agent_class": "FakeAgent",
                "default_provider": "fake",
                "default_model": "fake-model",
            }
        }
    }
    monkeypatch.setattr(
        workflow_module,
        "load_agent_inputs",
        lambda config_path, spec_path: (
            config,
            SimpleNamespace(tools=["fake"]),
            "---\nname: Fake\ntools: [fake]\n---\nPrompt\n",
            object,
        ),
    )
    monkeypatch.setattr(
        workflow_module, "LangChainEvaluatorRuntime", lambda profile: Evaluator()
    )
    monkeypatch.setattr(workflow_module, "ArchiAgentRuntime", lambda *args: Agent())
    run_dir = tmp_path / "run"

    result = CliRunner().invoke(
        qa_cli,
        [
            "--dataset",
            str(dataset),
            "--agent-config",
            str(tmp_path / "agent.yaml"),
            "--agent-spec",
            str(tmp_path / "agent.md"),
            "--output-dir",
            str(run_dir),
            "-n",
            "4",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (run_dir / "report.md").exists()
    summary = read_json(run_dir / "summary.json")
    assert summary["quality_accounted_attempts"] == 4
    assert summary["overall_attempt_pass_rate"] == 1.0


def test_qa_group_is_named_qa():
    assert qa_cli.name == "qa"


def test_qa_subcommands_answer_help():
    runner = CliRunner(mix_stderr=False)
    for args in (["run", "--help"], ["score", "--help"]):
        result = runner.invoke(qa_cli, args)
        assert result.exit_code == 0, result.output
        assert result.stderr == ""


def test_eval_alias_runs_qa_and_prints_the_notice_once(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(qa_cli_module, "QAWorkflow", lambda: _Workflow(calls))
    options = [
        "--dataset",
        str(tmp_path / "data.json"),
        "--agent-config",
        str(tmp_path / "agent.yaml"),
        "--agent-spec",
        str(tmp_path / "agent.md"),
        "--output-dir",
        str(tmp_path / "run"),
    ]
    runner = CliRunner(mix_stderr=False)
    alias = runner.invoke(eval_cli, ["qa"] + options)
    direct = runner.invoke(qa_cli, options)

    assert alias.exit_code == 0, alias.output
    assert direct.exit_code == 0, direct.output
    assert alias.stdout == direct.stdout
    assert alias.stderr.count(DEPRECATION_NOTICE) == 1
    assert direct.stderr == ""


def test_eval_alias_prints_the_notice_on_subcommand_help():
    result = CliRunner(mix_stderr=False).invoke(eval_cli, ["qa", "run", "--help"])

    assert result.exit_code == 0, result.output
    assert DEPRECATION_NOTICE in result.stderr


def test_top_level_help_lists_qa_and_hides_eval(monkeypatch, capsys):
    import src.cli.cli_main as cli_main

    monkeypatch.setattr(sys, "argv", ["archi", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        cli_main.main()

    assert exc_info.value.code == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("  qa ") for line in lines)
    assert any(line.startswith("  evaluate ") for line in lines)
    assert not any(line.startswith("  eval ") for line in lines)
