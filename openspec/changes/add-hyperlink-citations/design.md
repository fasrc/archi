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

### Layer 1 — scraper `<title>` capture (link-text quality)
In `scraper.py`'s plain-HTTP branch (`else` at ~line 83), parse the response body with
BeautifulSoup (already used for link extraction) and set `metadata["title"]` from `<title>`
(fallback `<h1>`, then `og:title`), trimmed. The selenium/SSO path and PDF loader already set
a title; this closes the HTML gap. A re-ingest backfills existing docs.

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
