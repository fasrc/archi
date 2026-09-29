# isort: skip_file
from types import SimpleNamespace

import pytest

from src.evaluation.qa import runtime
from src.evaluation.qa.runtime import (
    ArchiAgentRuntime,
    LangChainEvaluatorRuntime,
    LazyVectorstore,
    ToolTimingCallback,
)
from src.evaluation.qa.profile import load_profile
from src.evaluation.qa.validation import Atom


class _FakePipeline:
    pass


def _files(tmp_path, config_extra="", tools="search"):
    config = tmp_path / "agent.yaml"
    config.write_text(
        "services:\n"
        "  chat_app:\n"
        "    agent_class: FakePipeline\n"
        "    default_provider: openai\n"
        "    default_model: gpt-test\n"
        f"{config_extra}"
    )
    spec = tmp_path / "agent.md"
    spec.write_text(f"---\nname: Test\ntools: [{tools}]\n---\nAnswer directly.\n")
    return config, spec


def test_load_agent_inputs_uses_selected_config_and_spec(monkeypatch, tmp_path):
    config, spec = _files(tmp_path)
    monkeypatch.setattr(
        runtime,
        "import_module",
        lambda name: SimpleNamespace(FakePipeline=_FakePipeline),
    )

    loaded_config, loaded_spec, spec_text, pipeline_class = runtime.load_agent_inputs(
        config, spec
    )

    assert loaded_config["services"]["chat_app"]["default_model"] == "gpt-test"
    assert loaded_spec.tools == ["search"]
    assert "Answer directly." in spec_text
    assert pipeline_class is _FakePipeline


def test_load_agent_inputs_does_not_police_admin_config_content(monkeypatch, tmp_path):
    config, spec = _files(
        tmp_path,
        "    api_key: sk-production-secret\n" "    agents_dir: /tmp/agents\n",
    )
    spec.write_text(
        "---\nname: Test\ntools: [search]\n---\nUse sk-production-secret.\n"
    )
    monkeypatch.setattr(
        runtime,
        "import_module",
        lambda name: SimpleNamespace(FakePipeline=_FakePipeline),
    )

    loaded_config, _, spec_text, _ = runtime.load_agent_inputs(config, spec)

    assert loaded_config["services"]["chat_app"]["api_key"] == "sk-production-secret"
    assert loaded_config["services"]["chat_app"]["agents_dir"] == "/tmp/agents"
    assert "sk-production-secret" in spec_text


def test_evaluator_uses_structured_output_schema():
    observed = {}

    class Runnable:
        def invoke(self, messages, config=None):
            observed["messages"] = messages
            return {"atoms": []}

    class Model:
        def with_structured_output(self, schema):
            observed["schema"] = schema
            return Runnable()

    result = LangChainEvaluatorRuntime._structured(
        Model(), {"type": "object"}, "system prompt", {"question": "q"}
    )

    assert result == {"atoms": []}
    assert observed["schema"] == {"type": "object"}


def test_evaluator_compares_complete_answer_to_gold_atoms():
    invocations = []

    class Runnable:
        def invoke(self, messages, config=None):
            invocations.append(messages)
            return {
                "judgments": [
                    {"atom_id": "g1", "outcome": "entailed", "rationale": "match"}
                ]
            }

    class Model:
        def with_structured_output(self, schema):
            assert set(schema["properties"]["judgments"]["items"]["properties"]) == {
                "atom_id",
                "outcome",
                "rationale",
            }
            return Runnable()

    models = []

    def model_factory(provider, model, provider_config, **kwargs):
        instance = Model()
        models.append(instance)
        return instance

    evaluator = LangChainEvaluatorRuntime(load_profile(None), model_factory)
    result = evaluator.compare(
        "question",
        [Atom(id="g1", text="expected", required=True)],
        "complete answer",
    )

    assert result["judgments"][0]["outcome"] == "entailed"
    assert '"answer": "complete answer"' in invocations[0][1][1]
    assert "response_atoms" not in invocations[0][1][1]


def test_evaluator_fails_when_model_rejects_temperature():
    calls = []

    def model_factory(provider, model, provider_config, **kwargs):
        calls.append(kwargs)
        raise TypeError("unexpected keyword argument 'temperature'")

    with pytest.raises(TypeError, match="unexpected keyword argument 'temperature'"):
        LangChainEvaluatorRuntime(load_profile(None), model_factory)

    assert calls == [{"temperature": 0}]


def test_evaluator_requires_zero_temperature():
    calls = []

    def model_factory(provider, model, provider_config, **kwargs):
        calls.append(kwargs)
        return object()

    LangChainEvaluatorRuntime(load_profile(None), model_factory)

    assert calls == [{"temperature": 0}, {"temperature": 0}]


def _usage_firing_factory():
    """Return a model_factory whose invoke fires on_llm_end with token usage."""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    class _Runnable:
        def __init__(self, return_value):
            self._ret = return_value

        def invoke(self, messages, config=None):
            if config and "callbacks" in config:
                msg = AIMessage(
                    content="r",
                    usage_metadata={
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                    },
                )
                result = LLMResult(generations=[[ChatGeneration(message=msg)]])
                for cb in config["callbacks"]:
                    cb.on_llm_end(result)
            return self._ret

    class _Model:
        def __init__(self, return_value):
            self._ret = return_value

        def with_structured_output(self, schema):
            return _Runnable(self._ret)

    def factory(return_value):
        def model_factory(provider, model, provider_config, **kwargs):
            return _Model(return_value)

        return model_factory

    return factory


def test_evaluator_records_atoms_extractor_usage_after_extract_gold():
    from src.evaluation.qa.profile import EvaluatorProfile, ModelDescriptor

    profile = EvaluatorProfile(
        version=1,
        atoms_extractor=ModelDescriptor(provider="extractor-co", model="ext-model"),
        evaluator=ModelDescriptor(provider="eval-co", model="eval-model"),
    )
    factory = _usage_firing_factory()({"atoms": []})
    ev = LangChainEvaluatorRuntime(profile, factory)
    ev.extract_gold("q", "a")

    assert ev.last_usage is not None
    entry = ev.last_usage["by_model"][0]
    assert entry["provider"] == "extractor-co"
    assert entry["model"] == "ext-model"
    assert entry["input_tokens"] == 10
    assert entry["output_tokens"] == 5


def test_evaluator_records_evaluator_usage_after_compare():
    from src.evaluation.qa.profile import EvaluatorProfile, ModelDescriptor

    profile = EvaluatorProfile(
        version=1,
        atoms_extractor=ModelDescriptor(provider="extractor-co", model="ext-model"),
        evaluator=ModelDescriptor(provider="eval-co", model="eval-model"),
    )
    factory = _usage_firing_factory()({"judgments": []})
    ev = LangChainEvaluatorRuntime(profile, factory)
    ev.compare("q", [Atom(id="a1", text="fact", required=True)], "answer")

    assert ev.last_usage is not None
    entry = ev.last_usage["by_model"][0]
    assert entry["provider"] == "eval-co"
    assert entry["model"] == "eval-model"


def test_evaluator_last_usage_resets_between_calls():
    """A second call replaces last_usage rather than accumulating."""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    tokens = iter([10, 99])

    class _Runnable:
        def invoke(self, messages, config=None):
            t = next(tokens)
            if config and "callbacks" in config:
                msg = AIMessage(
                    content="r",
                    usage_metadata={
                        "input_tokens": t,
                        "output_tokens": 1,
                        "total_tokens": t + 1,
                    },
                )
                result = LLMResult(generations=[[ChatGeneration(message=msg)]])
                for cb in config["callbacks"]:
                    cb.on_llm_end(result)
            return {"atoms": []}

    class _Model:
        def with_structured_output(self, schema):
            return _Runnable()

    def model_factory(provider, model, provider_config, **kwargs):
        return _Model()

    ev = LangChainEvaluatorRuntime(load_profile(None), model_factory)
    ev.extract_gold("q1", "a1")
    ev.extract_gold("q2", "a2")

    assert ev.last_usage["input_tokens"] == 99


def test_evaluator_last_usage_set_even_when_structured_raises():
    """last_usage is populated in finally, so it survives a parsing failure."""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    class _Runnable:
        def invoke(self, messages, config=None):
            if config and "callbacks" in config:
                msg = AIMessage(
                    content="r",
                    usage_metadata={
                        "input_tokens": 50,
                        "output_tokens": 3,
                        "total_tokens": 53,
                    },
                )
                result = LLMResult(generations=[[ChatGeneration(message=msg)]])
                for cb in config["callbacks"]:
                    cb.on_llm_end(result)
            return "not-a-dict"

    class _Model:
        def with_structured_output(self, schema):
            return _Runnable()

    def model_factory(provider, model, provider_config, **kwargs):
        return _Model()

    ev = LangChainEvaluatorRuntime(load_profile(None), model_factory)
    with pytest.raises(ValueError, match="non-object"):
        ev.extract_gold("q", "a")

    assert ev.last_usage is not None
    assert ev.last_usage["input_tokens"] == 50


class _Output:
    def __init__(self, answer):
        self.answer = answer


def test_tool_timing_callback_records_complete_success_and_error(monkeypatch):
    from uuid import UUID

    clock = {"now": 10.0}
    monkeypatch.setattr(runtime, "perf_counter", lambda: clock["now"])
    callback = ToolTimingCallback()
    first_run = UUID("00000000-0000-0000-0000-000000000001")
    second_run = UUID("00000000-0000-0000-0000-000000000002")

    callback.on_tool_start(
        {"name": "search"},
        "{'query': 'fallback'}",
        run_id=first_run,
        inputs={"query": "complete query"},
    )
    clock["now"] = 10.125
    callback.on_tool_end({"matches": ["first", "second"]}, run_id=first_run)
    callback.on_tool_start({"name": "lookup"}, "id", run_id=second_run)
    clock["now"] = 10.5
    callback.on_tool_error(RuntimeError("failed"), run_id=second_run)

    assert callback.traces == [
        {
            "ordinal": 1,
            "name": "search",
            "status": "success",
            "query": '{"query": "complete query"}',
            "response": '{"matches": ["first", "second"]}',
            "duration_ms": 125,
        },
        {
            "ordinal": 2,
            "name": "lookup",
            "status": "error",
            "query": "id",
            "error": "failed",
            "duration_ms": 375,
        },
    ]


def test_tool_timing_callback_retains_unfinished_call_without_invented_fields():
    from uuid import UUID

    callback = ToolTimingCallback()
    callback.on_tool_start(
        {"name": "slow-search"},
        "complete untruncated query",
        run_id=UUID("00000000-0000-0000-0000-000000000004"),
    )

    assert callback.traces == [
        {
            "ordinal": 1,
            "name": "slow-search",
            "status": "incomplete",
            "query": "complete untruncated query",
        }
    ]


def test_tool_timing_callback_does_not_truncate_query_or_response():
    from uuid import UUID

    callback = ToolTimingCallback()
    query = "query-" + ("q" * 20_000)
    response = "response-" + ("r" * 20_000)
    run_id = UUID("00000000-0000-0000-0000-000000000005")

    callback.on_tool_start({"name": "complete"}, query, run_id=run_id)
    callback.on_tool_end(response, run_id=run_id)

    assert callback.traces[0]["query"] == query
    assert callback.traces[0]["response"] == response


def test_tool_timing_callback_integrates_with_langchain_tool():
    from langchain_core.tools import tool

    @tool
    def double(value: int) -> int:
        """Double one integer."""
        return value * 2

    callback = ToolTimingCallback()

    assert double.invoke({"value": 4}, config={"callbacks": [callback]}) == 8
    assert callback.traces[0]["ordinal"] == 1
    assert callback.traces[0]["name"] == "double"
    assert callback.traces[0]["status"] == "success"
    assert callback.traces[0]["query"] == '{"value": 4}'
    assert callback.traces[0]["response"] == "8"
    assert isinstance(callback.traces[0]["duration_ms"], int)
    assert callback.traces[0]["duration_ms"] >= 0


def _config():
    return {
        "services": {
            "chat_app": {"default_provider": "fake", "default_model": "fake-model"}
        }
    }


def test_archi_runtime_reuses_pipeline_with_fresh_attempt_history():
    class Pipeline:
        instances = 0
        histories = []

        def __init__(self, **kwargs):
            Pipeline.instances += 1

        def invoke(self, **kwargs):
            Pipeline.histories.append(kwargs["history"])
            return _Output("final answer")

    agent = ArchiAgentRuntime(_config(), SimpleNamespace(tools=[]), Pipeline)

    assert agent.run("first question") == "final answer"
    assert agent.run("second question") == "final answer"
    assert Pipeline.instances == 1
    assert Pipeline.histories == [
        [("User", "first question")],
        [("User", "second question")],
    ]


def test_lazy_vectorstore_initializes_once(monkeypatch):
    vectorstore = object()
    loads = []
    shared = LazyVectorstore(_config())
    monkeypatch.setattr(
        shared,
        "_load",
        lambda: loads.append(True) or vectorstore,
    )

    assert shared.get() is vectorstore
    assert shared.get() is vectorstore
    assert loads == [True]


def test_archi_runtime_requires_shared_vectorstore_for_vector_search():
    with pytest.raises(
        ValueError, match="vector-search runtime requires a shared vector store"
    ):
        ArchiAgentRuntime(
            _config(),
            SimpleNamespace(tools=["search_vectorstore_hybrid"]),
            object,
        )


def test_archi_runtime_reuses_shared_vectorstore_with_cached_pipeline(monkeypatch):
    vectorstore = object()
    loads = []
    received_vectorstores = []

    class Pipeline:
        def __init__(self, **kwargs):
            pass

        def invoke(self, **kwargs):
            received_vectorstores.append(kwargs["vectorstore"])
            return _Output("final answer")

    shared = LazyVectorstore(_config())
    monkeypatch.setattr(shared, "_load", lambda: loads.append(True) or vectorstore)
    agent = ArchiAgentRuntime(
        _config(),
        SimpleNamespace(tools=["search_vectorstore_hybrid"]),
        Pipeline,
        shared,
    )

    agent.run("first question")
    agent.run("second question")

    assert loads == [True]
    assert received_vectorstores == [vectorstore, vectorstore]


def test_archi_runtime_does_not_cache_failed_initialization(monkeypatch):
    vectorstore = object()
    loads = []

    class Pipeline:
        instances = 0

        def __init__(self, **kwargs):
            Pipeline.instances += 1

        def invoke(self, **kwargs):
            return _Output("final answer")

    def load_vectorstore():
        loads.append(True)
        if len(loads) == 1:
            raise RuntimeError("vector store is starting")
        return vectorstore

    shared = LazyVectorstore(_config())
    monkeypatch.setattr(shared, "_load", load_vectorstore)
    agent = ArchiAgentRuntime(
        _config(),
        SimpleNamespace(tools=["search_vectorstore_hybrid"]),
        Pipeline,
        shared,
    )

    with pytest.raises(RuntimeError, match="vector store is starting"):
        agent.run("first question")

    assert agent.run("second question") == "final answer"
    assert loads == [True, True]
    assert Pipeline.instances == 1


def test_archi_runtime_reports_vectorstore_failure_from_attempt(monkeypatch):
    spec = SimpleNamespace(tools=["search_vectorstore_hybrid"])

    def fail_vectorstore():
        raise RuntimeError("vector store is offline")

    shared = LazyVectorstore(_config())
    monkeypatch.setattr(shared, "_load", fail_vectorstore)

    agent = ArchiAgentRuntime(_config(), spec, object, shared)

    with pytest.raises(RuntimeError, match="vector store is offline"):
        agent.run("question")


def test_archi_runtime_uses_normal_pipeline_invocation(monkeypatch):
    vectorstore = object()
    observed = {}

    class Pipeline:
        def __init__(self, **kwargs):
            observed["init"] = kwargs

        def invoke(self, **kwargs):
            observed["invoke"] = kwargs
            return _Output("final answer")

    shared = LazyVectorstore(_config())
    monkeypatch.setattr(shared, "_load", lambda: vectorstore)
    answer = ArchiAgentRuntime(
        _config(),
        SimpleNamespace(tools=["search_vectorstore_hybrid"]),
        Pipeline,
        shared,
    ).run("question")

    from src.utils.llm_usage import UsageRecorder

    assert answer == "final answer"
    assert "strict_tool_loading" not in observed["init"]
    assert observed["invoke"]["history"] == [("User", "question")]
    assert observed["invoke"]["vectorstore"] is vectorstore
    assert len(observed["invoke"]["callbacks"]) == 2
    assert isinstance(observed["invoke"]["callbacks"][0], ToolTimingCallback)
    assert isinstance(observed["invoke"]["callbacks"][1], UsageRecorder)


def test_archi_runtime_collects_tool_timings(monkeypatch):
    from uuid import UUID

    ticks = iter((3.0, 3.125))
    monkeypatch.setattr(runtime, "perf_counter", lambda: next(ticks))

    class Pipeline:
        def __init__(self, **kwargs):
            pass

        def invoke(self, **kwargs):
            callback = kwargs["callbacks"][0]
            run_id = UUID("00000000-0000-0000-0000-000000000003")
            callback.on_tool_start({"name": "search"}, "query", run_id=run_id)
            callback.on_tool_end("result", run_id=run_id)
            return _Output("final answer")

    agent = ArchiAgentRuntime(_config(), SimpleNamespace(tools=[]), Pipeline)

    assert agent.run("question") == "final answer"
    assert agent.tool_calls == [
        {
            "ordinal": 1,
            "name": "search",
            "status": "success",
            "query": "query",
            "response": "result",
            "duration_ms": 125,
        }
    ]


def test_archi_runtime_fails_before_model_when_selected_mcp_tools_did_not_load():
    class Pipeline:
        def __init__(self, **kwargs):
            self.loaded_mcp_tools = []

        def invoke(self, **kwargs):
            raise AssertionError("model must not run without selected MCP tools")

    with pytest.raises(RuntimeError, match="selected 'mcp'.*no MCP tools"):
        ArchiAgentRuntime(_config(), SimpleNamespace(tools=["mcp"]), Pipeline).run(
            "question"
        )


def test_archi_runtime_invokes_model_when_selected_mcp_tools_loaded():
    class Pipeline:
        instances = 0

        def __init__(self, **kwargs):
            Pipeline.instances += 1
            self.loaded_mcp_tools = [SimpleNamespace(name="search")]

        def invoke(self, **kwargs):
            return _Output("grounded answer")

    agent = ArchiAgentRuntime(_config(), SimpleNamespace(tools=["mcp"]), Pipeline)

    assert agent.run("first question") == "grounded answer"
    assert agent.run("second question") == "grounded answer"
    assert Pipeline.instances == 1


def test_archi_runtime_does_not_cache_pipeline_with_missing_mcp_tools():
    class Pipeline:
        instances = 0

        def __init__(self, **kwargs):
            Pipeline.instances += 1
            self.loaded_mcp_tools = []

        def invoke(self, **kwargs):
            raise AssertionError("model must not run without selected MCP tools")

    agent = ArchiAgentRuntime(_config(), SimpleNamespace(tools=["mcp"]), Pipeline)

    with pytest.raises(RuntimeError, match="selected 'mcp'.*no MCP tools"):
        agent.run("first question")
    with pytest.raises(RuntimeError, match="selected 'mcp'.*no MCP tools"):
        agent.run("second question")

    assert Pipeline.instances == 2


def test_archi_runtime_rejects_empty_answer():
    class Pipeline:
        def __init__(self, **kwargs):
            pass

        def invoke(self, **kwargs):
            return _Output("")

    with pytest.raises(ValueError, match="no usable terminal answer"):
        ArchiAgentRuntime(_config(), SimpleNamespace(tools=[]), Pipeline).run(
            "question"
        )


# --- #582: ArchiAgentRuntime records usage ---


def test_archi_runtime_records_usage_after_successful_run():
    """run() sets self.usage with default_provider/default_model after a successful invoke."""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    class Pipeline:
        def __init__(self, **kwargs):
            pass

        def invoke(self, **kwargs):
            msg = AIMessage(
                content="r",
                usage_metadata={
                    "input_tokens": 30,
                    "output_tokens": 12,
                    "total_tokens": 42,
                },
            )
            result = LLMResult(generations=[[ChatGeneration(message=msg)]])
            for cb in kwargs["callbacks"]:
                if "on_llm_end" in type(cb).__dict__:
                    cb.on_llm_end(result)
            return _Output("final answer")

    agent = ArchiAgentRuntime(_config(), SimpleNamespace(tools=[]), Pipeline)
    agent.run("question")

    assert agent.usage is not None
    assert agent.usage["input_tokens"] == 30
    assert agent.usage["output_tokens"] == 12
    entry = agent.usage["by_model"][0]
    assert entry["provider"] == "fake"
    assert entry["model"] == "fake-model"


def test_archi_runtime_records_usage_when_invoke_raises():
    """Usage is still captured in finally even when pipeline.invoke raises."""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    class Pipeline:
        def __init__(self, **kwargs):
            pass

        def invoke(self, **kwargs):
            msg = AIMessage(
                content="r",
                usage_metadata={
                    "input_tokens": 20,
                    "output_tokens": 7,
                    "total_tokens": 27,
                },
            )
            result = LLMResult(generations=[[ChatGeneration(message=msg)]])
            for cb in kwargs["callbacks"]:
                if "on_llm_end" in type(cb).__dict__:
                    cb.on_llm_end(result)
            raise RuntimeError("pipeline error")

    agent = ArchiAgentRuntime(_config(), SimpleNamespace(tools=[]), Pipeline)

    with pytest.raises(RuntimeError, match="pipeline error"):
        agent.run("question")

    assert agent.usage is not None
    assert agent.usage["input_tokens"] == 20
    assert agent.usage["output_tokens"] == 7
