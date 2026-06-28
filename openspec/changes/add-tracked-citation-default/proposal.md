# Tracked default for inline `[title](url)` citation guidance

## Why

`add-hyperlink-citations` (#53) made the RAG **code** expose `title` + `url` to the model and
overlay them at retrieval, and updated the **prompt** that tells the model to cite inline as
`[title](url)` and avoid bare `[1]` indices. But that prompt lives in
`deploy/fasrc-dev/agents/fasrc-docs.md`, which is **gitignored per-deployment config**. The
system prompt is assembled as just `agent_prompt + role_context`
(`base_react._build_system_prompt`) — there is **no committed citation guidance anywhere**.

So a fresh checkout / a new deployment whose agent prompt doesn't happen to include the
guidance gets the new context fields (`title`/`url` in every snippet) but **no instruction to
use them**, and can regress to bare numeric indices or omit links entirely. The behavior the
code now supports is not guaranteed by anything committed. (Codex flagged this on #53; tracked
as issue #54.)

The only citation guidance in the tracked tree is stale: `examples/agents/cms-comp-ops.md`
still says "results are numbered `[1]`/`[2]`… result indices".

## What Changes

1. **Tracked default (code):** add a committed `DEFAULT_CITATION_GUIDANCE` string and append it
   to the system prompt in `base_react._build_system_prompt()` **for agents wired with the
   vectorstore retriever tool** (detected via `self.selected_tool_names` ∩
   `{search_knowledge_base, search_vectorstore_hybrid}` — the tool whose output presents
   `[i] <title> <url>` for citation). Other search tools (`search_local_files`,
   `search_metadata_index`) are NOT triggers — their output isn't a clean url+title citation
   surface — and non-retrieval agents are unaffected. The guidance: cite inline as `[title](url)`
   using the title and url shown for each search result; never emit bare `[n]` indices; never
   fabricate a URL (name the source in plain text if it has none).
2. **Refresh tracked examples:** update `examples/agents/cms-comp-ops.md` (and any sibling) to
   model the `[title](url)` style and drop the "numbered result indices" wording.

Per-deployment prompts may still add their own citation specifics; the tracked default is the
guaranteed baseline (duplication is benign — both say the same thing). The gitignored
`deploy/fasrc-dev/agents/fasrc-docs.md` can later drop its now-redundant block (deploy config,
not in this change).

## Impact

- Affected: `src/archi/pipelines/agents/base_react.py` (system-prompt assembly + the tracked
  constant), `examples/agents/*.md` (refresh). No config or schema change; no new dependency.
- Capability: `source-citations` (adds one requirement).
- Behavior change: every retrieval agent's system prompt now carries the citation baseline from
  committed code, independent of its (possibly gitignored) prompt file.
