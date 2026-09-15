# Fix HtmlToMarkdownProcessor RecursionError on deeply-nested HTML

## Why

The HTML→Markdown ingest stage (PR #38) converts scraped HTML to Markdown at the
persistence seam via a plain `markdownify` call. `markdownify` recurses over the
BeautifulSoup parse tree, so deeply-nested real-world HTML (FASRC WordPress KB pages)
exceeds Python's default recursion limit (1000) and raises `RecursionError`. The broad
`except` then keeps the **original raw HTML**, which is embedded with structure flattened
— so the conversion feature silently no-ops for those pages.

Measured on a fresh `dev` ingest (2026-06-25): **6 of 331** scraped resources failed,
all with `maximum recursion depth exceeded`. Ingest is not blocked (the guard holds), but
those pages are a silent quality degradation.

## What Changes

- `HtmlToMarkdownProcessor.process` runs the `markdownify` conversion **segfault-safely**
  with an enlarged recursion budget (bounded `sys.setrecursionlimit` and/or a worker
  thread with a larger `threading.stack_size`), restoring any global change in `finally`.
- The existing graceful fallbacks are unchanged: a genuine conversion exception OR
  blank/whitespace output still returns the original resource (never blocks ingest).
- No new third-party dependency (stdlib `sys`/`threading` only).

## Impact

- Affected: `src/data_manager/collectors/processing.py` (the conversion path only).
- Capability: `ingest-processing`.
- Closes fasrc/archi#40.
