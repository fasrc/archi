"""FASRCDocsAgent is exported and wired as a tool-using ReAct agent.

v1 is a copy/rename of CMSCompOpsAgent; these tests pin the contract that the
chat app relies on: `agent_class: FASRCDocsAgent` must resolve via
`getattr(src.archi.pipelines, ...)` to a BaseReActAgent subclass, without
disturbing the existing CMSCompOpsAgent export.
"""

import src.archi.pipelines as pipelines
from src.archi.pipelines.agents.base_react import BaseReActAgent


def test_fasrc_docs_agent_is_exported():
    assert hasattr(pipelines, "FASRCDocsAgent")
    assert "FASRCDocsAgent" in pipelines.__all__
    # The chat app resolves agent_class this way (src/archi/archi.py).
    assert getattr(pipelines, "FASRCDocsAgent").__name__ == "FASRCDocsAgent"


def test_fasrc_docs_agent_is_react_subclass():
    # Tool-use is preserved: it remains a ReAct agent.
    assert issubclass(pipelines.FASRCDocsAgent, BaseReActAgent)


def test_fasrc_docs_agent_distinct_from_cms():
    assert pipelines.FASRCDocsAgent is not pipelines.CMSCompOpsAgent
    # The original is untouched and still exported.
    assert hasattr(pipelines, "CMSCompOpsAgent")
