# Design

## Context

Three layers sit between "the model retrieves a doc" and "the answer shows a link":

```
LAYER 3  PROMPT      cite instruction (already says "use url" — but ineffective)
   ▲
LAYER 2  CONTEXT     _format_documents_for_llm: [idx] {filename} (hash=…)  ← strips url/title
   ▲
LAYER 1  DATA        metadata.url (100%), metadata.title (~7%, PDFs/selenium only)
```

The defect is the Layer 2 ↔ Layer 3 mismatch: the prompt references `url`/`title` the
formatter doesn't emit. Layer 1 limits how clean the link *text* can be.

## Decisions

### Layer 2 — `_format_documents_for_llm` (the critical change)
Change the per-snippet header to surface the citation fields. Keep `hash` (the
`fetch_catalog_document` companion tool needs it), drop nothing the agent depends on.

- `title = metadata.get("title") or metadata.get("display_name") or filename`
- `url = metadata.get("url")`
- Header becomes (illustrative): `[{idx}] {title}` + (` <{url}>` when url present) + ` (hash={hash})`

The model now sees a title and a URL for every snippet and can construct `[title](url)`.

### Layer 3 — FASRC Docs prompt
Replace the numeric-index citation guidance with:
- "When you reference a source, cite it inline as a Markdown link `[title](url)`, using the
  title and url shown for that search result, placed where you would otherwise put a bracketed
  number."
- "Do not emit bare `[1]`/`[2]` indices in the final answer. Never fabricate a URL; if a
  result has no url, name the source in plain text instead."

### Layer 1 — HTML `<title>` capture at the processing seam (link-text quality)
Implemented as a new `HtmlTitleProcessor` in `src/data_manager/collectors/processing.py`,
prepended **before** `HtmlToMarkdownProcessor` in `build_persistence()` (markdown conversion
strips `<title>`, so title capture must run first). It parses `resource.content` with
BeautifulSoup and sets `metadata["title"]` from `<title>` → `<h1>` → `og:title`, trimmed;
it never overwrites a non-empty title already set by the selenium/SSO or PDF paths. A
re-ingest backfills existing docs.

> Seam note (changed during implementation): the original plan put this in `scraper.py`'s
> plain-HTTP branch. Moved to the processing pipeline — the same well-factored seam that
> already attaches content-derived metadata (`converted_from`, `llm_category`) — because it
> is unit-testable in isolation and avoids reformatting the large, not-black-clean
> `scraper.py` (which would drag untested `crawl_iter` lines into the diff and fail the
> patch-coverage gate). Same metadata outcome, cleaner change.

## Goals / Non-goals
- **Goal:** inline `[title](url)` citations rendered as clickable links, clean title text.
- **Non-goal:** changing the chat UI (no post-processor exists; Markdown renders natively).
- **Non-goal:** a new citation data model — reuse existing `url`/`title`/`display_name` metadata.

## Risks / tradeoffs
- **Model compliance:** even with title+url in context, the model must place the link inline
  reliably. Mitigate with an explicit prompt instruction + an example; verify on dev.
- **Title quality:** some `<title>`s are generic ("Slurm Workload Manager"); acceptable — still
  better than a number. `display_name` fallback covers missing titles pre-re-ingest.
- **Re-ingest cost:** full scope requires a dev re-ingest (one pass, as just run for
  categorization). Minimal scope (Layers 2+3 only) ships clickable links immediately with
  URL-slug text for HTML, no re-ingest — documented as the fallback path.
