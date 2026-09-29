# Design — server-side clamp for `api_catalog_document`

## Context

Anchors verified on `origin/dev` `ec878b07`:

- `src/interfaces/uploader_app/app.py:761-770` — `api_catalog_document`. Line 762 reads
  `max_chars = request.args.get("max_chars", default=4000, type=int)`. Lines 769-770 are
  `if max_chars and len(text) > max_chars:` / `text = text[:max_chars]`.
- `src/interfaces/uploader_app/app.py:168-170` — the route is registered as
  `protected(self.api_catalog_document)`.
- `src/archi/pipelines/agents/tools/result_limits.py:26` — `resolve_requested_chars(requested,
  ceiling)`. It returns the ceiling for a bool, a value that `int()` cannot convert, or a value
  below `len(TRUNCATION_MARKER)` (50). Otherwise it returns `min(value, ceiling)`.
- `src/archi/pipelines/agents/tools/local_files.py:485` — `DEFAULT_FETCH_RESULT_CHARS = 6000`.
- `app.py` is not imported by any unit test (`tests/unit/test_ingest_persistence_wiring.py`
  reads it as text), so new lines in `app.py` score as uncovered in diff-cover.
- `black --check src/interfaces/uploader_app/app.py` passes at `ec878b07`, so an edit there
  does not reflow untouched code.

## Decisions

### D1 — One helper module, one call site

`src/interfaces/uploader_app/document_limits.py`:

```python
MAX_CATALOG_DOCUMENT_CHARS = 6000
DEFAULT_CATALOG_DOCUMENT_CHARS = 4000

def clamp_document_chars(requested) -> int:
    if requested is None:
        return DEFAULT_CATALOG_DOCUMENT_CHARS
    return resolve_requested_chars(requested, MAX_CATALOG_DOCUMENT_CHARS)
```

`request.args.get("max_chars")` gives `None` when the parameter is absent and a `str`
otherwise. `resolve_requested_chars` already calls `int()` on its input, so a string such as
`"100"` resolves to `100` and `"abc"` resolves to the ceiling.

The endpoint becomes:

```python
limit = clamp_document_chars(request.args.get("max_chars"))
...
text = (load_text_from_path(path) or "")[:limit]
```

This keeps the `app.py` diff to about three changed lines. All logic is in the tested module.

### D2 — Absent means 4000, not 6000

`resolve_requested_chars(None, 6000)` returns 6000. The issue requires that a request with no
`max_chars` keeps today's 4000 default, so the helper checks `None` first. 4000 is below the
ceiling, so the ceiling holds for every input.

### D3 — Reuse the client rule as it is

The helper does not change `resolve_requested_chars`. As a result, values below 50 (for
example `max_chars=10`) resolve to the 6000 ceiling, the same as on the agent side. This is
the documented rule in `result_limits.py` (a value too small to hold the truncation marker is
treated as malformed). No test asserts a value below 50 as "honoured".

A malformed value (`"abc"`, `""`) today falls back to 4000 through Flask's `type=int`. After
the change it resolves to 6000, because the issue's decision is to reuse the shared rule. Both
values are within the ceiling. The spec states this.

### D4 — Import cost

The first version of `document_limits.py` imported `src.archi.pipelines.agents.tools.result_limits`, which runs the
`tools` package `__init__` (langchain, mcp adapters). The data-manager image is built from the
same `a2rchi-python-base` image as the chat image, and its `requirements.txt` includes
`langchain-mcp-adapters` and `langchain`. As a result, the import is safe in the data-manager
container. A unit test imports the helper module, so an import failure fails the gate.

Review round 2 replaced that import. `src.archi.pipelines/__init__.py` imports every agent
class and the MCP adapters, and the uploader app loaded none of that before this change. A
lazy import only moved the cost from startup to each request that gives `max_chars`. As a
result, `TRUNCATION_MARKER` and `resolve_requested_chars` moved unchanged to
`src/utils/requested_chars.py`, which imports only `typing`. `result_limits.py` re-exports
both names, so the agent tools and their tests keep their imports. The rule text is moved byte-for-byte, so
task 1.1's "do not change `result_limits.py`" still holds for the rule itself. A unit test runs a clean
interpreter, calls `clamp_document_chars`, and asserts that `src.archi.pipelines` is not loaded.

### D5 — A source-text guard for the call site

Because `app.py` is not importable in unit tests, a test reads `app.py` as text (the same
pattern as `test_ingest_persistence_wiring.py`) and asserts that the body of
`api_catalog_document` calls `clamp_document_chars(` and no longer contains
`if max_chars and`. This makes sure the helper is wired into the real entry point, not only
defined.

## Risks

- A caller that relied on `max_chars=0` for a whole document now gets 6000 characters. That
  is the intended fix (the agent tool already treats `0` as the ceiling).
