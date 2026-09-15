## Context

The FASRC KB is the primary dev corpus (211 articles under `docs.rc.fas.harvard.edu`,
sitemap-driven per `deploy/fasrc-dev/sources.manifest.yaml`). Two ingestion defects were
confirmed against the live site and the code:

- `HtmlToMarkdownProcessor.process` (`processing.py:153-187`) converts **the whole page**
  via `_markdownify_deep_safe(content)` (`:165` → `:190`). No content scoping, so the
  Echo-KB (EPKB) category-filter nav, the bottom "Bookmarkable Links" section list, tags,
  and footer are embedded around the article across all 211 pages.
- The generic scraper (`scrapers/scraper.py`) records only `title`/`content_type`/
  `encoding` (`:111-121`); the server-side breadcrumb category is dropped.

Grounded findings on a real page (`/kb/running-jobs/`, 150 KB HTML → 60 KB full-page
Markdown): the article body is **not** a single container (`eckb-article-content` ×34,
`eckb-article-body` ×17 per section), so a CSS-container or generic extractor would guess
boundaries. But three **unique text landmarks** bound the article exactly (each appears
once): `Table of Contents` (start), `Bookmarkable Links` (end), `Last Updated` (fallback
end — the literal footer text; "Last Modified" does **not** appear). A slice between them
was validated on the real `markdownify` output and yields title + breadcrumb + full body,
dropping the pre-TOC nav and the bookmark/tags/date footer.

Pipeline order today (`build_persistence`, `processing.py:476-559`): **HtmlTitleProcessor
→ HtmlToMarkdownProcessor → CategorizationProcessor** (`ResourcePipeline.run`, `:81-91`).
`HtmlTitleProcessor` exists precisely because the title must be read from raw HTML before
conversion strips it — the same constraint applies to the breadcrumb category.

Both fixes only change persisted content/metadata, so they require a **re-ingest**; the
resource hash is unchanged, so the existing "Re-ingest refreshes chunks" guarantee governs
stale-chunk replacement.

## Goals / Non-Goals

**Goals:**
- Embed the article body, not the KB page chrome — improve retrieval precision.
- Capture the site's real category into `metadata["category"]` so it reaches
  `document_chunks.metadata`, enabling later category-aware retrieval and citation grouping.
- Fallback-safe: no article is ever dropped (no landmarks → full page; blank slice → full).
- No new dependency; stay inside the ≥80% diff-coverage gate and avoid black-churn.

**Non-Goals:**
- Retrieval-side **soft-boost** by category and the **query-side category source** — deferred.
- A generic main-content extractor (trafilatura) — superseded for the KB by the exact
  landmark slice; non-KB HTML keeps today's full-page conversion. (Reconsider only if a
  future non-KB source needs boilerplate stripping.)
- Editing `scrapers/scraper.py` (its `reap()` HTML branch is untested; see D2).

## Decisions

### D1 — Boilerplate strip via a deterministic landmark slice on the converted Markdown
In `HtmlToMarkdownProcessor.process`, after producing full-page Markdown with the existing
`_markdownify_deep_safe`, apply `_slice_kb_article(markdown)`:

- **start** = first occurrence of `Table of Contents`; keep from the end of that line.
- **end** = first occurrence of `Bookmarkable Links`; if absent, first occurrence of
  `Last Updated`.
- Return the stripped text between them **only when both a start and an end are found and
  the result is non-blank**; otherwise return the input Markdown unchanged.

Operating on the converted Markdown (not the HTML) matches the confirmed page shape and the
user's text-landmark description, and was validated end-to-end. The existing raise/blank
guards around conversion are untouched (the slice is a pure post-step that can only shrink
or pass through). Keep the marker strings as module-level constants for testability.

Rationale for text-slice over CSS/extractor: the body is fragmented across 17 section divs
(no clean container), and the three landmarks are each unique per page — a two-marker slice
is exact where a heuristic would approximate.

### D2 — Category capture as a new `HtmlCategoryProcessor`, not a scraper edit
The scraper's `reap()` HTML branch (`:100-121`) is **entirely untested** and large, so
editing it fights the 80% diff-coverage floor. Instead add an `HtmlCategoryProcessor` that
mirrors `HtmlTitleProcessor` (`:115-140`): reads `resource.content` (raw HTML at that stage),
guards on the `html`/`htm` suffix, never raises, calls
`resource.set_metadata_field("category", ...)`. Register it between `HtmlTitleProcessor` and
`HtmlToMarkdownProcessor` in `build_persistence`. Whole change stays in `processing.py`,
where the pipeline-order test infra already exists (`test_html_title_processor.py:83`);
touches **zero** scraper code.

`_extract_kb_category(html)` mirrors `_extract_html_title` (`:94-112`): wrap
`BeautifulSoup(html, "html.parser")` in try/except, return `None` on failure,
`logger.debug(exc_info=True)`. Selector: `soup.select("span.eckb-breadcrumb-link")`; return
`crumbs[-2]` when there are ≥3 non-empty crumbs (`[Home, Category, Article]`), taking the
immediate parent for nested categories; otherwise `None`.

Guards (spec-driven): skip when a non-empty `category` is already set (don't clobber a
scraper-provided one, e.g. Indico), and write nothing when no breadcrumb is found.

### D3 — `category` key reuse
Reuse the established `category` metadata key (already used by `indico_scraper.py`,
called out as source-provided and distinct from `llm_category` in `processing.py:292` and
the spec). Propagates to chunk metadata via the existing merge (`manager.py:561`) with no
plumbing changes.

### D4 — Black-churn-safe seams (from the scout)
Both files are black-clean today. Insertion points chosen to avoid reflow:
`_slice_kb_article` and its landmark constants after `_markdownify_deep_safe` (~`:238`);
`_extract_kb_category` next to `_extract_html_title`; `HtmlCategoryProcessor` next to
`HtmlTitleProcessor`. The conversion change inside `process` adds one call after the
existing `markdown = _markdownify_deep_safe(content)` line — low churn.

## Risks / Trade-offs

- **Landmark drift.** If FASRC re-themes the KB and renames "Table of Contents" /
  "Bookmarkable Links" / "Last Updated", the slice silently no-ops (keeps full page) —
  safe degradation, not an error; caught by the deploy-verify spot check. Marker strings
  are module-level constants for a one-line fix.
- **Marker appearing in body text.** `Bookmarkable Links` / `Table of Contents` could in
  principle appear inside article prose; we take the **first** occurrence of each, so the
  top-nav start and footer end are matched. Low risk for these control phrases.
- **Full-page markdownify still runs before slicing.** We convert the whole 150 KB page
  then trim — simple and validated; conversion cost is unchanged from today and acceptable
  for a batch ingest. (A future optimization could slice the HTML first.)
- **Re-ingest cost & stale chunks.** Both fixes need a re-ingest of 211 articles under an
  unchanged hash; correctness depends on the existing stale-chunk refresh. Verified by the
  deploy-verify smoke test after redeploy.
- **Non-KB sources unchanged.** slurm/wiki HTML keeps full-page conversion (their
  boilerplate is not addressed here) — an accepted, explicit non-goal.
