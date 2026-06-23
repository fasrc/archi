# 0002 — MarkdownNodeParser (not MarkdownElementNodeParser) for markdown sources

**Status:** Accepted (branch-scoped: `feat/hierarchical-rerank-impl`)
**Task:** `2.1 Add a LlamaIndex node-parsing helper that converts a LangChain Document → LlamaIndex Document → hierarchical nodes (parents + children), defaulting to SentenceSplitter, with MarkdownElementNodeParser selected for markdown sources.`
**Change:** `openspec/changes/add-hierarchical-rerank-retrieval`

## Context

Task 2.1 names `MarkdownElementNodeParser` for the markdown chunking strategy.
In practice, `llama_index.core.node_parser.MarkdownElementNodeParser` calls
`extract_table_summaries()` on every parse, which resolves an LLM
(`Settings.llm` → `llama-index-llms-openai`). That package is **not installed**
and an LLM in the ingestion path directly contradicts the change's CPU-only,
no-extra-LLM design (design D4/D6: "No GPU reranker", "embeddings already run on
CPU"; the data-manager image carries no OpenAI LLM client). With no markdown
tables present it still raises:

```
ImportError: `llama-index-llms-openai` package not found
```

so `MarkdownElementNodeParser` is unusable on this path without adding an LLM
dependency the design explicitly avoids.

The authoritative requirement (`specs/hierarchical-rerank-retrieval/spec.md`)
states ingestion *MAY* use "markdown-element parsing for markdown sources" and
that child text must be "segmented on sentence/structural boundaries". It does
not mandate the specific class.

## Decision

For the `markdown` strategy, use `MarkdownNodeParser` to carve the document into
header-delimited sections (the parent context nodes), then split each section
into child leaves with `SentenceSplitter`. This:

- needs **no LLM** and runs CPU-only, matching the design;
- segments parents on real markdown structure (headers) and children on
  sentence boundaries, satisfying the spec's "Child boundaries respect
  structure" scenario;
- keeps the default `sentence` strategy (`HierarchicalNodeParser` +
  `SentenceSplitter`) unchanged for the HTML-derived FASRC corpus.

### Rejected alternatives

- **`MarkdownElementNodeParser` (as literally named in task 2.1)** — requires
  `llama-index-llms-openai` + a configured LLM for table summarisation;
  rejected because it breaks the CPU-only/no-extra-LLM constraint and is not
  importable in the current images. The spec's "MAY" wording for markdown
  parsing makes this an implementation detail, not a hard requirement.
- **Installing `llama-index-llms-openai` and wiring an LLM** — adds an LLM call
  (latency, cost, network) to a batch CPU ingestion path purely to summarise
  tables; out of scope and contrary to the design's non-goals.
