## Why

Two grounded defects degrade retrieval quality on the FASRC Knowledge Base
(`docs.rc.fas.harvard.edu`, 211 KB articles, the primary dev corpus):

1. **The whole page is embedded, not the article.** `HtmlToMarkdownProcessor` runs
   `markdownify()` on the entire scraped page (`processing.py:165` →
   `_markdownify_deep_safe` at `:204`) — there is no content scoping. Each Echo-KB (EPKB)
   page wraps the article in a category-filter nav, a bottom "Bookmarkable Links" section
   list, tags, and a footer. So every article is chunked and ranked together with that
   chrome, and the identical nav on all 211 pages produces near-duplicate boilerplate
   chunks (there is no cross-document chunk dedup) that crowd top-k and dilute embeddings.
   Verified: the body isn't a single container (`eckb-article-content` ×34,
   `eckb-article-body` ×17 per section) — but every page carries three unique text
   landmarks that bound the article exactly.

2. **The site's real category taxonomy is thrown away.** The KB renders a server-side
   breadcrumb (`Home › <Category> › <Article>`, `span.eckb-breadcrumb-link`, verified
   present in a no-JS fetch) whose middle term is the article's category from the site's
   19-item taxonomy (Affiliates, AI, Applications, Cluster Usage, Data Security, Data
   Transfer, FASSE, Getting Started, Informational, IQSS, Languages, Manage Account, OOD,
   Parallel Computing, Research Data Management, Software, Storage, Training, VPN). The
   generic web scraper keeps only `title`/`content_type`/`encoding`, so this category
   never enters the KB and cannot inform retrieval or citations.

## What Changes

- **Boilerplate strip via a deterministic landmark slice.** `HtmlToMarkdownProcessor`
  converts the page to Markdown (existing `markdownify`), then slices the article body
  between the page's own landmarks: drop everything up to and including **`Table of
  Contents`**, and everything from **`Bookmarkable Links`** onward — or, when
  `Bookmarkable Links` is absent, from **`Last Updated`** (always present) onward. The
  slice **only activates when both a start and an end landmark are found**, so non-KB
  pages keep today's full-page conversion unchanged. If a slice would yield blank, the
  unsliced Markdown is kept. **No new dependency.**
- **Source category capture.** A new `HtmlCategoryProcessor` — mirroring the existing
  `HtmlTitleProcessor` — runs **before** conversion (while `resource.content` is still
  raw HTML), reads the breadcrumb category, and writes it to `metadata["category"]` (the
  established source-provided key, distinct from `llm_category`). It never overwrites a
  category already set by a scraper and no-ops on pages without the breadcrumb. The value
  propagates to `document_chunks.metadata` via the existing merge (`manager.py:561`) — no
  downstream plumbing.
- **Re-ingest.** Both fixes only take effect after re-ingesting the 211 KB articles; they
  ship together with one re-ingest of the dev deployment + the `archi-dev-deploy-verify`
  smoke test.

## Capabilities

### Modified Capabilities

- `ingest-processing`: HTML→Markdown conversion now slices the article body out of KB
  pages between fixed text landmarks (with a full-page fallback when the landmarks are
  absent); a new pre-conversion step captures the **source category** from the page into
  `metadata["category"]`, which propagates to chunk metadata alongside
  `converted_from`/`llm_category`.

## Impact

- **Code:** `src/data_manager/collectors/processing.py` only — add a landmark-slice step to
  `HtmlToMarkdownProcessor.process`; add `_extract_kb_category` + `HtmlCategoryProcessor`;
  register it before `HtmlToMarkdownProcessor` in `build_persistence`. **No `scraper.py`
  change** (its `reap()` HTML branch is untested — a diff-coverage liability we avoid), and
  **no new dependency** (`markdownify`/`bs4` are already declared).
- **Data / deploy:** requires a re-ingest of `docs.rc.fas.harvard.edu` (211 articles);
  content changes under an unchanged hash, relying on the existing stale-chunk refresh
  (`ingest-processing` spec, "Re-ingest refreshes chunks"). Redeploy fasrc-dev +
  `archi-dev-deploy-verify`.
- **Out of scope (deferred to a follow-up change):** retrieval-side **soft-boost** by
  category and the **query-side category source** design — still under discussion.
