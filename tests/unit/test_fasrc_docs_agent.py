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


def _agent_with_selected(tool_names):
    """An uninitialized FASRCDocsAgent with just enough wired to BUILD its static
    tools. The tool factories only capture ``catalog_service`` (they call it at tool
    *invocation*, not at build), so a stub object suffices and no live deployment is
    needed."""
    agent = pipelines.FASRCDocsAgent.__new__(pipelines.FASRCDocsAgent)
    agent.catalog_service = object()
    agent._store_documents = None
    agent._store_tool_input = None
    agent.enable_vector_tools = False
    agent.selected_tool_names = list(tool_names)
    agent._static_tools = None
    return agent


def _built_tool_names(agent):
    return {getattr(t, "name", None) for t in agent.rebuild_static_tools()}


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


def test_catalog_search_auto_includes_fetch_companion():
    """A spec that enables a catalog *search* tool (which returns only hashes) but
    omits its companion *read* tool must still build `fetch_catalog_document`, so the
    agent can read the documents it finds (issue #45)."""
    agent = _agent_with_selected(["search_local_files", "search_metadata_index"])
    assert "fetch_catalog_document" in _built_tool_names(agent)


def test_built_tool_descriptions_reference_only_present_tools():
    """No built tool description may instruct the model to use a tool that is not in
    the built set — the machine-checkable form of 'never told to use a tool it does
    not have' (issue #45)."""
    agent = _agent_with_selected(["search_local_files", "search_metadata_index"])
    built = agent.rebuild_static_tools()
    built_names = {getattr(t, "name", None) for t in built}
    all_tool_names = set(_agent_with_selected([]).get_tool_registry())
    for tool in built:
        desc = getattr(tool, "description", "") or ""
        for other in all_tool_names:
            if other in desc:
                assert (
                    other in built_names
                ), f"{getattr(tool, 'name', '?')} description names absent tool {other}"


def test_fetch_companion_not_added_without_a_catalog_search_tool():
    """The companion is a dependency of the catalog *search* tools only — it is not
    force-added when no catalog search tool is selected."""
    agent = _agent_with_selected(["search_vectorstore_hybrid"])
    assert "fetch_catalog_document" not in _built_tool_names(agent)


def test_explicit_fetch_is_not_duplicated():
    """When the spec already lists the companion, it appears exactly once."""
    agent = _agent_with_selected(["search_local_files", "fetch_catalog_document"])
    names = [getattr(t, "name", None) for t in agent.rebuild_static_tools()]
    assert names.count("fetch_catalog_document") == 1


def test_cms_agent_retains_its_builders():
    # The shared tools.py factories and CMSCompOpsAgent are untouched.
    assert hasattr(pipelines.CMSCompOpsAgent, "_init_monit")
    assert hasattr(pipelines.CMSCompOpsAgent, "_build_monit_opensearch_search_tool")
