## Why

The chat app's "Process Documents" action (`POST /api/upload/embed`) writes the vectorstore
from the chat process: `trigger_embedding` calls `self.chat.vector_manager.update_vectorstore()`
directly (`src/interfaces/chat_app/app.py:6420` on `origin/dev` `bbc52059`). That write
does not go through the data manager's tracked upload path (`trigger_update` →
`run_upload_update`, `src/bin/service_data_manager.py:76-77`), so:

- it does not take `ingestion_lock`, which is a process-local lock in the data-manager
  container. It can run at the same time as a data-manager refresh or ingest.
- `GET /api/ingestion/status` keeps `completed` while it changes the corpus, so a benchmark
  that waits on the endpoint can start against a corpus that is being changed.
- a sync that returns `"failed"` (documents not added) is not an error. The action answers
  200, which is a silent success.

Origin: Codex review thread on PR #645
(https://github.com/fasrc/archi/pull/645#discussion_r4229995745), deferred out of #560.
Issue #652, armed by the operator on 2026-10-20 on the `ultracode` tier.

## What Changes

- **New data-manager endpoint** `POST /document_index/embed` on the uploader app
  (`src/interfaces/uploader_app/app.py`), behind the same `require_admin` guard as the other
  `/document_index/*` endpoints. It calls `post_update_hook` (the data manager's
  `trigger_update`) **directly and synchronously**, not through `_notify_update`, which
  logs and swallows a failure. Answers: 200 `{"status": "ok"}`; 500
  `{"error": "embed_failed", "detail": <message>}` when the update raises (this includes a
  sync that returned `"failed"`); 503 `{"error": "embed_unavailable"}` when no hook is set.
- **New chat-side helper** `src/interfaces/chat_app/embed_proxy.py` with
  `request_embed(data_manager_url, headers)`. It posts to the new endpoint and maps the
  outcome: auth redirect → 502, unreachable → 503 `data_manager_unavailable`, read timeout
  → 504 (the update can still be running), data-manager error → 500 with its message,
  non-JSON or a 2xx without `status == "ok"` → 502. It returns `None` on success.
- **`trigger_embedding`** calls the helper instead of
  `self.chat.vector_manager.update_vectorstore()`. On a failure it answers with the
  helper's body and status. On success it keeps the existing failed-documents query and
  the existing 200 response shapes (`success`, `partial`, `failed`, `message`).
- **Docs:** `docs/docs/api_reference.md` documents `POST /api/upload/embed` (it now reports
  on `GET /api/ingestion/status` as `step == "upload"`) and `POST /document_index/embed`.
- **Accepted behavior change (from the issue's acceptance criteria):** a sync that returns
  `"failed"` now gives a non-2xx answer and `state == "error"` on the status endpoint.
  Before, the chat action answered 200. The UI already shows `data.error` in a toast for a
  non-OK answer (`src/interfaces/chat_app/static/modules/upload.js:141-155`), so no UI
  change is needed.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `ingestion-refresh-status` (added by the unarchived change
  `fix-issue-560-report-refresh-ingest-status`; it is not yet in `openspec/specs/`, so this
  change uses ADDED requirements): the chat app's embed action becomes an upload-triggered
  update that the data manager tracks.

## Impact

- `src/interfaces/uploader_app/app.py` — one new handler method and one route registration.
- `src/interfaces/chat_app/embed_proxy.py` — new module.
- `src/interfaces/chat_app/app.py` — one import and a 4-line call site. Unit tests do
  import this file (`tests/unit/test_require_auth.py:35`), so `trigger_embedding` is tested
  directly; patch coverage measured on a scratch copy is 100 %.
- `docs/docs/api_reference.md`.
- Tests: `tests/unit/test_uploader_embed_endpoint.py`, `tests/unit/test_chat_embed_proxy.py`
  (both new).
- Deploy: the chat app and the data manager ship as separate images. Deploy both; a new
  chat app with an old data manager answers 502 on Process Documents until the data
  manager is redeployed.
- Out of scope: the collection phase of uploads (#648), the uploader's manual-ingest status
  writes and `_notify_update`'s swallowed failure on the other handlers (#699),
  `upload.js`, `GET /api/upload/status`, and the chat process's `vector_manager` object.
