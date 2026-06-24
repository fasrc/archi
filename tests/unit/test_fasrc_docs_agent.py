"""FASRCDocsAgent is a retrieval-only, FASRC-native ReAct agent.

These tests pin two contracts:
1. The chat app resolves `agent_class: FASRCDocsAgent` via
   `getattr(src.archi.pipelines, ...)` to a BaseReActAgent subclass, without
   disturbing the existing CMSCompOpsAgent export.
2. v2 removed the CMS compute-ops surface (MONIT/Rucio/HTCondor): the agent
   exposes only document-retrieval tools, structurally — not merely dormant
   because a secret is unset.

The tool registry is introspected on an uninitialized instance built via
`__new__`, so these tests need no live deployment/LLM wiring.
"""

import src.archi.pipelines as pipelines
from src.archi.pipelines.agents.base_react import BaseReActAgent

RETRIEVAL_TOOLS = {
    "search_vectorstore_hybrid",
    "search_local_files",
    "search_metadata_index",
    "list_metadata_schema",
    "fetch_catalog_document",
    "mcp",
}

CMS_TOOLS = {
    "monit_opensearch_search",
    "monit_opensearch_aggregation",
    "condor_opensearch_search",
    "condor_opensearch_aggregation",
}

CMS_BUILDERS = {
    "_init_monit",
    "_build_monit_opensearch_search_tool",
    "_build_monit_opensearch_aggregation_tool",
    "_build_condor_opensearch_search_tool",
    "_build_condor_opensearch_aggregation_tool",
}


def _uninitialized_agent():
    # get_tool_registry()/get_tool_descriptions() are introspection-safe on an
    # instance built without __init__ (no catalog/LLM wiring required).
    return pipelines.FASRCDocsAgent.__new__(pipelines.FASRCDocsAgent)


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


def test_registry_exposes_only_retrieval_tools():
    registry = _uninitialized_agent().get_tool_registry()
    assert set(registry) == RETRIEVAL_TOOLS


def test_registry_has_no_cms_tools_even_with_monit_secret(monkeypatch):
    # The tool set must be absent-by-design, not dormant-by-config: even with the
    # CMS secret present and tools.monit configured, no MONIT/condor tool appears.
    monkeypatch.setenv("MONIT_GRAFANA_TOKEN", "fake-token-for-test")
    agent = _uninitialized_agent()
    agent.config = {
        "services": {
            "chat_app": {
                "tools": {
                    "monit": {
                        "rucio": {"url": "https://example.invalid/9269/_msearch"},
                        "condor": {"url": "https://example.invalid/8787/_msearch"},
                    }
                }
            }
        }
    }
    registry = agent.get_tool_registry()
    assert CMS_TOOLS.isdisjoint(registry)
    assert set(registry) == RETRIEVAL_TOOLS


def test_no_cms_builder_methods_on_class():
    for name in CMS_BUILDERS:
        assert not hasattr(pipelines.FASRCDocsAgent, name), name


def test_cms_agent_retains_its_builders():
    # The shared tools.py factories and CMSCompOpsAgent are untouched.
    assert hasattr(pipelines.CMSCompOpsAgent, "_init_monit")
    assert hasattr(pipelines.CMSCompOpsAgent, "_build_monit_opensearch_search_tool")
