from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from src.archi.pipelines.agents.base_react import BaseReActAgent
from src.archi.pipelines.agents.tools import (
    RemoteCatalogClient,
    create_document_fetch_tool,
    create_file_search_tool,
    create_metadata_schema_tool,
    create_metadata_search_tool,
    create_retriever_tool,
)
from src.data_manager.vectorstore.retrievers import build_vector_retriever
from src.utils.logging import get_logger

logger = get_logger(__name__)


class FASRCDocsAgent(BaseReActAgent):
    """FASRC documentation/RAG support agent.

    A ReAct agent whose tool set is restricted to document retrieval over the
    archi knowledge base (hybrid vectorstore search, file/metadata search,
    catalog document fetch, and configured MCP tools). It carries no
    compute-operations (MONIT/Rucio/HTCondor) tooling. Deployed with the FASRC
    agent spec (deploy/fasrc-dev/agents/fasrc-docs.md).
    """

    # Catalog search tools return matched lines + resource *hashes* only; reading a
    # document's full text requires fetch_catalog_document, and their tool descriptions
    # instruct the model to call it. So the companion is a hard dependency of the search
    # tools — selecting either pulls it in even if the agent spec omitted it (issue #45).
    _CATALOG_SEARCH_TOOLS = ("search_local_files", "search_metadata_index")
    _CATALOG_COMPANION_TOOL = "fetch_catalog_document"

    def __init__(
        self,
        config: Dict[str, Any],
        *args,
        **kwargs,
    ) -> None:
        super().__init__(config, *args, **kwargs)

        self.catalog_service = RemoteCatalogClient.from_deployment_config(self.config)
        self._vector_retrievers = None
        self._vector_tool = None
        self.enable_vector_tools = (
            "search_vectorstore_hybrid" in self.selected_tool_names
        )

        self.rebuild_static_tools()
        self.rebuild_static_middleware()
        self.refresh_agent()

    @property
    def _chat_app_config(self) -> Dict[str, Any]:
        """Return the services.chat_app config section."""
        return self.config.get("services", {}).get("chat_app", {})

    def get_tool_registry(self) -> Dict[str, Callable[[], Any]]:
        return {
            name: entry["builder"] for name, entry in self._tool_definitions().items()
        }

    def get_tool_descriptions(self) -> Dict[str, str]:
        return {
            name: entry["description"]
            for name, entry in self._tool_definitions().items()
        }

    def _tool_definitions(self) -> Dict[str, Dict[str, Any]]:
        defs = {
            "search_local_files": {
                "builder": self._build_file_search_tool,
                "description": (
                    "Grep-like search over file contents. Provide a distinctive phrase or regex; optionally use "
                    "regex=true, case_sensitive=true, and context (before/after). Returns matching lines with hashes; "
                    "use fetch_catalog_document for full text."
                ),
            },
            "search_metadata_index": {
                "builder": self._build_metadata_search_tool,
                "description": (
                    "Query the files' metadata catalog (ticket IDs, source URLs, resource types, etc.). "
                    "Supports key:value filters and OR (e.g., source_type:git OR url:https://... ticket_id:RC-123). "
                    "Returns matching files with metadata; use fetch_catalog_document to pull full text."
                ),
            },
            "list_metadata_schema": {
                "builder": self._build_metadata_schema_tool,
                "description": (
                    "List metadata schema hints: supported keys, distinct source_type values, and suffixes. "
                    "Use this to learn which key:value filters are available before searching."
                ),
            },
            "fetch_catalog_document": {
                "builder": self._build_fetch_tool,
                "description": (
                    "Fetch full document text by resource hash after a search hit. "
                    "Use this sparingly to pull only the most relevant files."
                ),
            },
            "search_vectorstore_hybrid": {
                "builder": self._build_vector_tool_placeholder,
                "description": (
                    "Hybrid search over the knowledge base that combines lexical (BM25) and semantic (vector) matching.\n"
                    "Input must be a plain text query string.\n"
                    "Query writing guidance:\n"
                    "- Use one short, specific question or request (not a long keyword dump).\n"
                    "- Keep only the most informative terms (about 3-8 keywords or a short sentence).\n"
                    "- Do not repeat terms unless repetition is intentional for emphasis.\n"
                    "- Avoid partial/trailing fragments (e.g., ending with a single character).\n"
                    "- Include exact identifiers when known (component names, APIs, error strings), using quotes for multi-word phrases.\n"
                    "- If results are weak, run a second query that is narrower (add identifiers) or broader (remove overly specific terms)."
                ),
            },
            "mcp": {
                "builder": self._build_mcp_tools,
                "description": "Access tools served via configured MCP servers.",
            },
        }

        return defs

    def _build_file_search_tool(self) -> Callable:
        description = self._tool_definitions()["search_local_files"]["description"]
        return create_file_search_tool(
            self.catalog_service,
            description=description,
            store_docs=self._store_documents,
            store_tool_input=getattr(self, "_store_tool_input", None),
        )

    def _build_metadata_search_tool(self) -> Callable:
        description = self._tool_definitions()["search_metadata_index"]["description"]
        return create_metadata_search_tool(
            self.catalog_service,
            description=description,
            store_docs=self._store_documents,
            store_tool_input=getattr(self, "_store_tool_input", None),
        )

    def _build_metadata_schema_tool(self) -> Callable:
        description = self._tool_definitions()["list_metadata_schema"]["description"]
        return create_metadata_schema_tool(
            self.catalog_service,
            description=description,
        )

    def _build_fetch_tool(self) -> Callable:
        description = self._tool_definitions()["fetch_catalog_document"]["description"]
        return create_document_fetch_tool(
            self.catalog_service,
            description=description,
            store_tool_input=getattr(self, "_store_tool_input", None),
        )

    def _build_vector_tool_placeholder(self) -> List[Callable]:
        return []

    def _build_static_tools(self) -> List[Callable]:
        """Build static tools, auto-including the catalog companion read tool.

        Overrides the base to enforce a dependency: a spec that enables a catalog
        *search* tool but omits ``fetch_catalog_document`` would otherwise build an
        agent whose own tool descriptions tell the model to call a tool it does not
        have (issue #45). Selecting either catalog search tool pulls the companion in.
        """
        selected = list(self.selected_tool_names or [])
        if (
            any(name in selected for name in self._CATALOG_SEARCH_TOOLS)
            and self._CATALOG_COMPANION_TOOL not in selected
        ):
            logger.info(
                "Auto-including %s: a catalog search tool is selected but the agent "
                "spec did not list its companion read tool.",
                self._CATALOG_COMPANION_TOOL,
            )
            selected.append(self._CATALOG_COMPANION_TOOL)
        static_names = [name for name in selected if name != "mcp"]
        return self._select_tools_from_registry(static_names)

    # def _build_static_middleware(self) -> List[Callable]:
    #     """
    #     Initialize middleware: currently, testing what works best.
    #     This is static.
    #     """
    #     todolist_middleware = TodoListMiddleware()
    #     llmtoolselector_middleware = LLMToolSelectorMiddleware(
    #         model=self.agent_llm,
    #         max_tools=3,
    #     )
    #     return [todolist_middleware, llmtoolselector_middleware]

    def _update_vector_retrievers(self, vectorstore: Any) -> None:
        """Instantiate or refresh the vectorstore retriever tool.

        Retriever selection is delegated to ``build_vector_retriever`` (the
        single config seam): when
        ``data_manager.retrievers.hierarchical_rerank.enabled`` is true it
        returns the ``LlamaIndexHierarchicalRetriever``; otherwise it falls back
        to ``HybridRetriever``. Either way the retriever is wired into the same
        ``search_vectorstore_hybrid`` tool with an unchanged name/contract.
        """
        if not self.enable_vector_tools:
            self._vector_retrievers = None
            self._vector_tools = None
            return
        retrievers_cfg = self.dm_config.get("retrievers", {})

        retriever = build_vector_retriever(vectorstore, retrievers_cfg)

        hybrid_description = self._tool_definitions()["search_vectorstore_hybrid"][
            "description"
        ]

        self._vector_retrievers = [retriever]
        self._vector_tools = []
        self._vector_tools.append(
            create_retriever_tool(
                retriever,
                name="search_vectorstore_hybrid",
                description=hybrid_description,
                store_docs=self._store_documents,
                store_tool_input=getattr(self, "_store_tool_input", None),
                enforce_budget=lambda: self._consume_tool_budget(
                    "search_vectorstore_hybrid"
                ),
            )
        )

    def _inject_forced_retrieval(
        self, messages: List[BaseMessage]
    ) -> List[BaseMessage]:
        """Force one ``search_vectorstore_hybrid`` call before the model answers.

        The model can ignore an "always search first" prompt and answer from its
        own weights, leaving ``source_documents`` empty (chat UI shows "Link
        unavailable"). To enforce retrieval, prefill a completed tool round —
        an ``AIMessage`` carrying the tool call plus the matching ``ToolMessage``
        result — so the ReAct loop starts with real chunks already in context.
        Invoking the existing retriever tool also runs its ``store_docs``
        callback, so retrieved documents flow into ``source_documents``/links
        exactly as a model-initiated search would. The model may still search
        again. Gated by ``services.chat_app.force_initial_retrieval`` (default
        on) so prompt-vs-enforcement variants can be A/B'd in the sweep.
        """
        if not getattr(self, "enable_vector_tools", False):
            logger.debug("Forced retrieval skipped: vector tools disabled")
            return messages
        if not self._chat_app_config.get("force_initial_retrieval", True):
            logger.debug("Forced retrieval skipped: force_initial_retrieval=false")
            return messages
        tools = getattr(self, "_vector_tools", None)
        if not tools:
            logger.debug("Forced retrieval skipped: no vector tools built")
            return messages
        # Only force on a fresh user turn (the latest message is the question).
        if not messages or not isinstance(messages[-1], HumanMessage):
            last_type = type(messages[-1]).__name__ if messages else "none"
            logger.debug("Forced retrieval skipped: last message is %s", last_type)
            return messages
        query = (self._message_content(messages[-1]) or "").strip()
        if not query:
            logger.debug("Forced retrieval skipped: empty query")
            return messages

        try:
            result = tools[0].invoke({"query": query})
        except Exception:
            # Fail open: a retrieval error must not break the chat turn.
            logger.warning("Forced initial retrieval failed", exc_info=True)
            return messages

        logger.info(
            "Forced initial retrieval ran: query=%r -> %d chars",
            query,
            len(str(result)),
        )

        call_id = f"forced_search_{uuid.uuid4().hex}"
        ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_vectorstore_hybrid",
                    "args": {"query": query},
                    "id": call_id,
                }
            ],
        )
        tool_msg = ToolMessage(
            content=result if isinstance(result, str) else str(result),
            tool_call_id=call_id,
            name="search_vectorstore_hybrid",
        )
        return list(messages) + [ai, tool_msg]
