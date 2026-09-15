# Hyperlinked source citations ([title](url) instead of [1])

## Why

When the FASRC Docs agent references a source, the answer shows a bare numeric index
(`… lost its session token [1]`). Users want an inline Markdown hyperlink whose text is the
document title and whose target is the document's web URL:

> … lost its session token [VSCode Remote Development via SSH and Tunnel](https://docs.rc.fas.harvard.edu/kb/vscode-remote-development-via-ssh-or-tunnel/)

**The current prompt already asks for this and silently fails.** It instructs the model to
"cite sources using the `url` field from the search result metadata," but the retriever's
context formatter (`_format_documents_for_llm`) renders each snippet as
`[idx] {filename} (hash={hash})` — it never surfaces `url` (or a title). The model is told
to cite a URL it cannot see, so it falls back to the only handle it has: the index. The
prompt and the context formatter contradict each other.

Investigation of the live dev corpus (4,893 chunks):
- **`url`**: present and clean (`^https?://`) on **100%** of chunks.
- **`title`**: non-empty on only **~7%** (348 — all PDFs; the selenium/SSO path also captures
  it). The plain-HTTP scrape path (slurm.schedmd.com, most docs.rc.fas pages) parses content
  for links but **never extracts `<title>`**, so the human-readable link text is missing for
  the bulk of documents.

## What Changes

1. **Context (retriever):** `_format_documents_for_llm` exposes `title` and `url` per snippet
   so the model can actually cite them. This is the critical fix that makes the citation
   instruction effective.
2. **Prompt (FASRC Docs agent):** replace the "results are numbered `[1]`/`[2]`… for citation"
   guidance with an instruction to cite inline as a Markdown link `[title](url)`, placed where
   a bracketed number would go, and never to fabricate a URL.
3. **Ingest (scraper):** the plain-HTTP branch extracts the page `<title>` (BeautifulSoup,
   already a dependency) into resource metadata, so HTML documents carry a clean title — not
   just PDFs. A re-ingest populates existing documents.

Link-text fallback: when `title` is absent, use `display_name` (the URL slug) so the link is
still readable; never use the resource hash or fabricate a URL.

## Impact

- Affected: `src/archi/pipelines/agents/tools/retriever.py` (formatter),
  `deploy/fasrc-dev/agents/fasrc-docs.md` (prompt), `src/data_manager/collectors/scrapers/scraper.py`
  (title capture). No UI change — there is no inline-citation post-processor; the chat renderer
  shows Markdown links natively.
- Capability: `source-citations`.
- Requires a dev re-ingest for clean titles on existing HTML docs (full scope).
