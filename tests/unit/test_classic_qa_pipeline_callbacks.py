"""The classic QAPipeline must forward invoke callbacks to its LLM chains.

The QA evaluation runtime passes a UsageRecorder through
``pipeline.invoke(callbacks=[...])``. ``ChainWrapper.invoke`` used to call its
chain with ``config={}``, so a QAPipeline under evaluation recorded
``"usage": null`` for calls the provider did report (#582 review).
"""

from types import SimpleNamespace

import pytest

from src.archi.pipelines.classic_pipelines.qa import QAPipeline
from src.archi.pipelines.classic_pipelines.utils.chain_wrappers import ChainWrapper


@pytest.fixture(autouse=True)
def _no_global_config(monkeypatch):
    # _prepare_payload reads the global config (unused); it needs Postgres.
    monkeypatch.setattr(
        "src.archi.pipelines.classic_pipelines.utils.chain_wrappers.get_global_config",
        lambda: {},
    )


class _RecordingChain:
    def __init__(self):
        self.configs = []

    def invoke(self, inputs, config=None):
        self.configs.append(config)
        return "answer"


def _wrapper(chain):
    wrapper = ChainWrapper.__new__(ChainWrapper)
    wrapper.chain = chain
    wrapper.unprunable_input_variables = []
    wrapper.prompt = SimpleNamespace(input_variables=["question"])
    wrapper.token_limiter = SimpleNamespace(
        check_input_size=lambda value: True,
        prune_inputs_to_token_limit=lambda **inputs: inputs,
    )
    return wrapper


def test_chain_wrapper_forwards_the_runnable_config():
    chain = _RecordingChain()
    marker = object()

    _wrapper(chain).invoke({"question": "q"}, config={"callbacks": [marker]})

    assert chain.configs == [{"callbacks": [marker]}]


def test_chain_wrapper_keeps_an_empty_config_by_default():
    chain = _RecordingChain()

    _wrapper(chain).invoke({"question": "q"})

    assert chain.configs == [{}]


def test_qa_pipeline_passes_callbacks_to_both_chains():
    condense, chat = _RecordingChain(), _RecordingChain()
    pipeline = QAPipeline.__new__(QAPipeline)
    pipeline.condense_chain = _wrapper(condense)
    pipeline.chat_chain = _wrapper(chat)
    pipeline.retriever = SimpleNamespace(invoke=lambda query: [])
    pipeline.llms = {}
    marker = object()

    pipeline.invoke(history=[("User", "q")], callbacks=[marker])

    assert condense.configs == [{"callbacks": [marker]}]
    assert chat.configs == [{"callbacks": [marker]}]


def test_qa_pipeline_without_callbacks_keeps_an_empty_config():
    condense, chat = _RecordingChain(), _RecordingChain()
    pipeline = QAPipeline.__new__(QAPipeline)
    pipeline.condense_chain = _wrapper(condense)
    pipeline.chat_chain = _wrapper(chat)
    pipeline.retriever = SimpleNamespace(invoke=lambda query: [])
    pipeline.llms = {}

    pipeline.invoke(history=[("User", "q")])

    assert condense.configs == [{}]
    assert chat.configs == [{}]
