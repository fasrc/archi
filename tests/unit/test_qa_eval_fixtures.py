"""Fixture validation for the QA eval trial assets under examples/qa_eval/."""

from pathlib import Path

from src.evaluation.qa.oracle_config import EvaluatorMCPRegistry, MCPTransport
from src.evaluation.qa.profile import load_profile
from src.evaluation.qa.validation import DatasetItemState, iter_dataset_items

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "examples" / "qa_eval"


class TestQAEvalTrialDataset:
    def test_dataset_has_three_static_and_two_live_rows(self):
        items = list(iter_dataset_items(FIXTURES_DIR / "dataset.json"))

        assert len(items) == 5
        live_items = [item for item in items if item.is_live]
        static_items = [item for item in items if not item.is_live]
        assert len(live_items) == 2
        assert len(static_items) == 3
        assert all(
            item.state is DatasetItemState.UNRESOLVED_LIVE for item in live_items
        )
        assert all(item.oracle is not None for item in live_items)
        oracle_calls = [
            call
            for item in live_items
            for call in (item.oracle.calls if item.oracle is not None else ())
        ]
        assert {call.server for call in oracle_calls} == {"capacity"}
        assert {call.tool for call in oracle_calls} == {"current_capacity"}

    def test_dataset_has_a_forced_tool_row(self):
        items = list(iter_dataset_items(FIXTURES_DIR / "dataset.json"))

        forced_tool_rows = [
            item
            for item in items
            if not item.is_live and "FASRC knowledge base" in item.question
        ]

        assert len(forced_tool_rows) == 1
        assert forced_tool_rows[0].id == "qa-static-storage-quota"


class TestQAEvalTrialMCPRegistries:
    def test_cli_registry_configures_the_capacity_alias_over_stdio(self):
        registry = EvaluatorMCPRegistry.load(
            FIXTURES_DIR / "qa_evaluation_mcp.cli.yaml"
        )

        assert registry.aliases == ("capacity",)
        server = registry._servers["capacity"]
        assert server.transport is MCPTransport.STDIO
        assert server.command == "python3"
        assert server.args == ("tests/unit/evaluation/qa/fake_mcp_server.py",)

    def test_console_registry_configures_the_container_path(self):
        registry = EvaluatorMCPRegistry.load(
            FIXTURES_DIR / "qa_evaluation_mcp.console.yaml"
        )

        assert registry.aliases == ("capacity",)
        server = registry._servers["capacity"]
        assert server.transport is MCPTransport.STDIO
        assert server.args == ("/root/archi/evaluations/fake_mcp_server.py",)


class TestQAEvalTrialEvaluatorProfiles:
    def test_anthropic_profile_matches_the_dev_standby_model(self):
        profile = load_profile(FIXTURES_DIR / "evaluator-profile.yaml")

        assert profile.atoms_extractor.provider == "anthropic"
        assert profile.atoms_extractor.model == "claude-sonnet-4-6"
        assert profile.atoms_extractor.timeout == 120
        assert profile.evaluator.provider == "anthropic"
        assert profile.evaluator.model == "claude-sonnet-4-6"
        assert profile.evaluator.timeout == 120

    def test_vllm_profile_uses_the_openai_provider(self):
        profile = load_profile(FIXTURES_DIR / "evaluator-profile.vllm.yaml")

        assert profile.atoms_extractor.provider == "openai"
        assert profile.evaluator.provider == "openai"
        assert profile.atoms_extractor.timeout == 120
        assert profile.evaluator.timeout == 120
