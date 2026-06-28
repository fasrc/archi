"""Tests for the committed default citation guidance on retrieval agent specs.

`load_agent_spec` appends a tracked `DEFAULT_CITATION_GUIDANCE` (cite inline as `[title](url)`,
never bare `[n]`) to the resolved prompt of any agent that declares the vectorstore retriever
tool — so the behavior does not depend on a per-deployment (gitignored) prompt file. The
resolved prompt becomes `agent_prompt` in `BaseReActAgent`, so it reaches the system prompt.
Non-retrieval agents are unaffected.
"""

from src.archi.pipelines.agents.agent_spec import (
    DEFAULT_CITATION_GUIDANCE,
    RETRIEVAL_TOOL_NAMES,
    load_agent_spec_from_text,
)

_HEADER = "---\nname: {name}\ntools:\n{tools}\n---\n{body}"


def _spec(tool_names, body="You are a helper.", name="Test Agent"):
    tools = "\n".join(f"  - {t}" for t in tool_names)
    return load_agent_spec_from_text(_HEADER.format(name=name, tools=tools, body=body))


class TestRetrievalAgentGetsGuidance:

    def test_guidance_appended_for_vectorstore_retriever(self):
        spec = _spec(["search_vectorstore_hybrid"], body="Answer questions.")
        assert "Answer questions." in spec.prompt  # original body preserved
        assert DEFAULT_CITATION_GUIDANCE in spec.prompt
        assert "[title](url)" in spec.prompt

    def test_guidance_present_even_with_minimal_body(self):
        spec = _spec(["search_knowledge_base"], body="hi")
        assert DEFAULT_CITATION_GUIDANCE in spec.prompt

    def test_guidance_forbids_bare_indices_and_fabrication(self):
        low = DEFAULT_CITATION_GUIDANCE.lower()
        assert "[1]" in DEFAULT_CITATION_GUIDANCE or "bare" in low
        assert "fabricat" in low


class TestNonRetrievalAgentUnaffected:

    def test_no_guidance_for_local_files_only(self):
        # search_local_files / search_metadata_index are intentionally NOT triggers
        spec = _spec(["search_local_files", "search_metadata_index"])
        assert DEFAULT_CITATION_GUIDANCE not in spec.prompt

    def test_no_guidance_for_non_retrieval_tool(self):
        spec = _spec(["some_other_tool"], body="Answer questions.")
        assert spec.prompt == "Answer questions."


class TestTriggerSetMembership:

    def test_retrieval_tool_names_is_exactly_the_vectorstore_retriever(self):
        # pin membership so a new retriever tool / accidental broadening is visible
        assert RETRIEVAL_TOOL_NAMES == {
            "search_knowledge_base",
            "search_vectorstore_hybrid",
        }
