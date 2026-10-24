# Clamp the catalog document endpoint's max_chars on the server

## Why

`api_catalog_document` in `src/interfaces/uploader_app/app.py:761-770` reads
`max_chars = request.args.get("max_chars", default=4000, type=int)` and truncates only
through `if max_chars and len(text) > max_chars: text = text[:max_chars]`. That is the only
guard. As a result:

- `max_chars=999999` returns the whole document (no ceiling).
- `max_chars=0` is falsy, so the guard is skipped and the whole document is returned.
- `max_chars=-5` slices `text[:-5]`, which returns all of the document except its tail.

The route is wrapped in `protected(...)` (`app.py:168-170`, admin auth), which limits who can
call it but does not bound what a caller gets. The agent tool already clamps on the client
side (#235, PR #265), but `RemoteCatalogClient.get_document` (`local_files.py:124-136`)
forwards `max_chars` as given, so any other caller of the endpoint is unbounded.

This is issue #260 (milestone v2026.11.0). The decision recorded on the issue with the
operator (2026-09-26, re-confirmed 2026-09-27) is: reuse the tested client-side rule on the
server, with a module constant ceiling of 6000, and clamp rather than reject. The issue body
is the authoritative work order.

## What Changes

- New module `src/interfaces/uploader_app/document_limits.py` with
  `MAX_CATALOG_DOCUMENT_CHARS = 6000`, `DEFAULT_CATALOG_DOCUMENT_CHARS = 4000`, and
  `clamp_document_chars(requested) -> int`. An absent value (`None`) gives the 4000 default.
  Any other value goes through the existing
  `resolve_requested_chars(requested, MAX_CATALOG_DOCUMENT_CHARS)`
  (`src/archi/pipelines/agents/tools/result_limits.py:26`).
- `api_catalog_document` calls the helper once and always slices `text[:limit]`. The raw
  `type=int` read and the `if max_chars and …` guard are removed.
- One sentence in `docs/docs/agents_tools.md` (the `fetch_catalog_document` entry) states
  that the server caps `max_chars` at 6000 characters and that `0` or a negative value
  means the cap, not "no limit".

## Out of scope

- A configurable ceiling (no `uploader_app:` config key is added).
- The agent fetch tool's own clamp (#235, already landed).
- Other uploader endpoints.
- Returning HTTP 400 for a bad value (rejected with the operator: existing callers pass
  `max_chars` through today).

## Impact

- Code: `src/interfaces/uploader_app/app.py` (thin call site only),
  new `src/interfaces/uploader_app/document_limits.py`.
- Tests: new `tests/unit/test_catalog_document_limits.py`.
- Docs: `docs/docs/agents_tools.md`.
- Behavior change for callers: a request above 6000, `0`, or a negative value now returns
  at most 6000 characters. A request with no `max_chars` still returns at most 4000.
