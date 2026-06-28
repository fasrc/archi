# Design

## Context

```
agent prompt file (per-deployment, gitignored)  ──┐
                                                   ├─► _build_system_prompt() ─► system_prompt
role_context (SSO roles)  ─────────────────────────┘        (agent_prompt + role_context)
```

Nothing committed instructs the model on citation style. The `[title](url)` behavior is only as
good as whatever prompt a deployment happens to ship. We want a committed baseline.

## Decisions

### Injection point — `base_react._build_system_prompt()`
Append a tracked `DEFAULT_CITATION_GUIDANCE` to the assembled prompt. Current body:
```python
base_prompt = self.agent_prompt or ""
role_context = get_role_context()
return base_prompt + role_context
```
becomes (illustrative):
```python
parts = [self.agent_prompt or ""]
if self._has_retrieval_tool():
    parts.append(DEFAULT_CITATION_GUIDANCE)
parts.append(get_role_context())
return "\n\n".join(p for p in parts if p)
```

### Targeting — only agents with a catalog/vectorstore retrieval tool
`base_react` already resolves `self.selected_tool_names`. Define a module constant
`RETRIEVAL_TOOL_NAMES = frozenset({"search_knowledge_base", "search_vectorstore_hybrid",
"search_local_files", "search_metadata_index"})` — the tools whose results carry `url`/`title`.
`_has_retrieval_tool()` = `bool(set(self.selected_tool_names or []) & RETRIEVAL_TOOL_NAMES)`.
This keeps the guidance off agents that don't retrieve citable docs (e.g. image-processing,
`search_opensearch` monit agents whose hits aren't url/title docs).

### The guidance text (single source of truth)
A short, deployment-neutral block: cite inline as a Markdown link `[title](url)` using the
title and url shown for each search result, placed where a bracketed number would go; never
emit bare `[1]`/`[2]` indices in the final answer; never fabricate a URL — if a result has no
url, name the source in plain text. (No FASRC-specific example, so it suits any deployment.)

## Goals / Non-goals
- **Goal:** the `[title](url)` behavior is guaranteed by committed code for every retrieval
  agent, independent of the gitignored per-deployment prompt.
- **Non-goal:** a full tracked-base-prompt + per-deployment-override layering system in
  `agent_spec` (heavier; not needed — appending a default covers the requirement).
- **Non-goal:** removing the (redundant) citation block from `deploy/fasrc-dev/agents/fasrc-docs.md`
  — that's gitignored deploy config; it can be trimmed separately.

## Risks / tradeoffs
- **Duplication:** a deployment whose prompt already has citation guidance gets it twice.
  Benign (identical intent); reinforcement, not conflict.
- **Over/under-targeting:** the retrieval-tool set is explicit; if a new retrieval tool is added
  it must be added to `RETRIEVAL_TOOL_NAMES`. A unit test pins the membership so the omission is
  visible. Alternative (always append) was rejected as too broad.
- **Prompt drift:** keeping the guidance as one constant means future wording changes happen in
  one tracked place rather than N deployment files.

## Alternatives considered
- **Examples-only (refresh `examples/agents/*` and stop):** satisfies "tracked guidance exists"
  literally but does NOT make a fresh deployment emit correct citations — it only provides a
  template to copy. Rejected as the primary fix; kept as a secondary cleanup.
