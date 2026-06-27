## 1. Layer 2 — retriever context formatter (the critical fix, TDD)

- [ ] 1.1 In `tests/unit/` for the retriever tool, add a failing test that builds Documents
  with `metadata = {url, title, resource_hash, filename}` and asserts
  `_format_documents_for_llm` output contains the `title` and the `url` for each snippet
  (and still the hash). Confirm it fails on current code (header is `[idx] {filename} (hash=…)`).
- [ ] 1.2 Add a test for the fallback: a Document with `url` but no `title` → the snippet uses
  `display_name` (else filename) as the title text; the hash is never used as citation text.
- [ ] 1.3 Implement: change the header in `_format_documents_for_llm`
  (`src/archi/pipelines/agents/tools/retriever.py`) to surface
  `title = title or display_name or filename` and the `url`, retaining `hash`.

## 2. Layer 1 — scraper `<title>` capture (TDD)

- [ ] 2.1 Add a failing test for the plain-HTTP scrape path: given an HTTP response whose body
  has `<title>Foo</title>`, the produced `ScrapedResource` metadata has `title == "Foo"`.
  Confirm it fails today (the HTTP branch sets no title).
- [ ] 2.2 Add a test: a page with no `<title>` (fallback to `<h1>`, then `og:title`, then "")
  does not raise and yields the best-available title or empty.
- [ ] 2.3 Implement: in `scraper.py`'s plain-HTTP branch, parse the body with BeautifulSoup
  (already a dep) and set `metadata["title"]` from `<title>` → `<h1>` → `og:title`, trimmed.

## 3. Layer 3 — FASRC Docs prompt

- [ ] 3.1 Update `deploy/fasrc-dev/agents/fasrc-docs.md`: replace the "results are numbered
  `[1]`/`[2]`… for citation" guidance with "cite inline as a Markdown link `[title](url)`
  using the title and url shown for the result, placed where a number would go; no bare
  indices in the answer; never fabricate a URL." Include one worked example.
  (Gitignored dev config — not in the PR diff; note the change in the PR body.)

## 4. Verify

- [ ] 4.1 `bash scripts/gate.sh` green (diff-cover ≥ 80% on changed lines); no new dependency.
- [ ] 4.2 Redeploy fasrc-dev + re-ingest (one pass) so existing HTML docs get titles; confirm
  `metadata.title` non-empty coverage rises well above the current ~7% (PDF-only) baseline.
- [ ] 4.3 Live check: ask the dev chat a question whose answer cites a doc; confirm the answer
  contains an inline `[title](url)` Markdown link (clickable) and no bare `[n]` index.
