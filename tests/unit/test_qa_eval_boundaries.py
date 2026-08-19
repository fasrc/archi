"""Boundary tests for the ported QA evaluation: what the tested agent must not see.

The evaluator resolves a live row's truth over MCP before the run, and the
comparator legitimately reads that truth. The agent under test must not: it answers
from the question alone, or the benchmark measures leaked truth instead of
retrieval. These tests pin the two seams the truth could cross — the runtime's call
into the agent pipeline and the agent's own model request — and pin that the tool
trace the runtime collects reaches the persisted run artifacts unchanged.

The evaluator-model traffic is out of scope on purpose: it carries the truth and the
gold atoms by design. Only the tested agent is covered here.
"""

import json
from collections import deque
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional
from uuid import UUID

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from mcp.types import CallToolResult

import src.evaluation.qa.workflow as workflow_module
from src.archi.pipelines.agents import base_react
from src.archi.pipelines.agents.base_react import BaseReActAgent
from src.evaluation.qa.artifacts import read_jsonl
from src.evaluation.qa.oracle import OracleCallEvidence
from src.evaluation.qa.workflow import QAWorkflow

QUESTION = "How much capacity is free?"

# Every distinguishable part of the live row's oracle. None of it may reach the
# tested agent: the registry alias, the tool name, the recipe fields, the resolved
# truth, the provenance the evaluator recorded, and the gold-atom text built from it.
REGISTRY_ALIAS = "oracle-alias-x9"
ORACLE_TOOL = "oracle_tool_x9"
CALL_ID = "recipe-call-x9"
CALL_ARGUMENT = "argument-value-x9"
ANSWER_FIELD = "truth_x9"
METADATA_FIELD = "provenance_x9"
RESOLVED_TRUTH = "73141590000 sentinel"
PROVENANCE = "provenance-value-x9"
ORACLE_CONTENT = (
    REGISTRY_ALIAS,
    ORACLE_TOOL,
    CALL_ID,
    CALL_ARGUMENT,
    ANSWER_FIELD,
    METADATA_FIELD,
    RESOLVED_TRUTH,
    PROVENANCE,
)

AGENT_TOOL = "search_knowledge_base"
AGENT_TOOL_PAYLOAD = {"query": "free capacity"}
AGENT_TOOL_RESPONSE = "one node is free"


class _Invoker:
    """Evaluator MCP double: answers every oracle call with the same payload."""

    def __init__(self, payload: Dict[str, Any], count: int) -> None:
        self._values = deque([payload] * count)
        self.calls: List[str] = []

    def invoke(self, call: Any):
        self.calls.append(call.id)
        return (
            CallToolResult(content=[], structuredContent=self._values.popleft()),
            OracleCallEvidence(call.id, 2, True),
        )


class _Evaluator:
    """Deterministic evaluator: infers one gold atom from the resolved truth."""

    def extract_gold(self, question: str, answer: str) -> Dict[str, Any]:
        return {"atoms": [{"id": "truth", "text": answer, "required": True}]}

    def compare(self, question: str, atoms: Any, answer: str) -> Dict[str, Any]:
        return {
            "judgments": [
                {
                    "atom_id": atom.id,
                    "outcome": "entailed",
                    "rationale": "deterministic",
                }
                for atom in atoms
            ]
        }


class _RecordingModel(BaseChatModel):
    """Fake chat model: records each request instead of calling a provider."""

    sink: Any = None

    @property
    def _llm_type(self) -> str:
        return "recording-fake"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.sink.append({"messages": list(messages), "stop": stop, "kwargs": kwargs})
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="fake answer"))]
        )


def _write_dataset(path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "qa-dataset-v2",
                "items": [
                    {
                        "id": "live",
                        "question": QUESTION,
                        "time_sensitive": True,
                        "oracle": {
                            "kind": "mcp",
                            "calls": [
                                {
                                    "id": CALL_ID,
                                    "server": REGISTRY_ALIAS,
                                    "tool": ORACLE_TOOL,
                                    "arguments": {"scope": CALL_ARGUMENT},
                                    "answer_fields": {ANSWER_FIELD: f"/{ANSWER_FIELD}"},
                                    "metadata_fields": {
                                        METADATA_FIELD: f"/{METADATA_FIELD}"
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def live_run(monkeypatch, tmp_path):
    """Run the complete workflow over one live row against a given pipeline class."""

    def run(pipeline_class: type):
        dataset = tmp_path / "dataset.json"
        run_dir = tmp_path / "run"
        _write_dataset(dataset)
        payload = {ANSWER_FIELD: RESOLVED_TRUTH, METADATA_FIELD: PROVENANCE}
        # One resolution for preparation, one for each live check around the run.
        invoker = _Invoker(payload, 3)
        config = {
            "services": {
                "chat_app": {
                    "agent_class": "TestedPipeline",
                    "default_provider": "fake",
                    "default_model": "fake-model",
                }
            }
        }
        spec = SimpleNamespace(tools=[], prompt="Answer the question directly.")
        monkeypatch.setattr(
            workflow_module.EvaluatorMCPRegistry,
            "load",
            classmethod(lambda cls, path=None: invoker),
        )
        monkeypatch.setattr(
            workflow_module,
            "LangChainEvaluatorRuntime",
            lambda _profile: _Evaluator(),
        )
        monkeypatch.setattr(
            workflow_module,
            "load_agent_inputs",
            lambda *_args: (
                config,
                spec,
                "---\nname: tested\ntools: []\n---\n",
                pipeline_class,
            ),
        )
        manifest = QAWorkflow().composite(
            dataset,
            tmp_path / "agent.yaml",
            tmp_path / "agent.md",
            run_dir,
        )
        # The attempt really ran: an empty run would pass every absence assertion.
        assert manifest["status"] == "scored"
        return run_dir

    return run


def _strings(value: Any) -> Iterator[str]:
    """Yield every string reachable in a recorded argument structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item)
    else:
        yield repr(value)


def _assert_carries_question_only(recorded: Any) -> None:
    observed = "\n".join(_strings(recorded))
    assert QUESTION in observed
    assert [term for term in ORACLE_CONTENT if term in observed] == []


def test_runtime_hands_the_agent_pipeline_no_oracle_content(live_run):
    recorded: Dict[str, List[Dict[str, Any]]] = {"init": [], "invoke": []}

    class Pipeline:
        def __init__(self, **kwargs):
            recorded["init"].append(kwargs)

        def invoke(self, **kwargs):
            recorded["invoke"].append(kwargs)
            return SimpleNamespace(answer="agent answer")

    live_run(Pipeline)

    assert len(recorded["invoke"]) == 1
    _assert_carries_question_only(recorded)


def test_tested_agent_model_request_carries_no_oracle_content(live_run, monkeypatch):
    requests: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        base_react,
        "get_model",
        lambda *args, **kwargs: _RecordingModel(sink=requests),
    )

    live_run(BaseReActAgent)

    assert len(requests) == 1
    assert [message.type for message in requests[0]["messages"]] == ["system", "human"]
    _assert_carries_question_only(requests)


def test_persisted_tool_trace_keeps_the_observed_call_name_and_payload(live_run):
    class Pipeline:
        def __init__(self, **kwargs):
            pass

        def invoke(self, **kwargs):
            callback = kwargs["callbacks"][0]
            run_id = UUID("00000000-0000-0000-0000-000000000009")
            callback.on_tool_start(
                {"name": AGENT_TOOL},
                json.dumps(AGENT_TOOL_PAYLOAD),
                run_id=run_id,
                inputs=AGENT_TOOL_PAYLOAD,
            )
            callback.on_tool_end(AGENT_TOOL_RESPONSE, run_id=run_id)
            return SimpleNamespace(answer="agent answer")

    run_dir = live_run(Pipeline)

    [answer] = read_jsonl(run_dir / "answers.jsonl")
    [trace] = answer["tool_calls"]
    assert trace["name"] == AGENT_TOOL
    assert trace["query"] == json.dumps(AGENT_TOOL_PAYLOAD, sort_keys=True)
    assert trace["response"] == AGENT_TOOL_RESPONSE
    assert trace["status"] == "success"
