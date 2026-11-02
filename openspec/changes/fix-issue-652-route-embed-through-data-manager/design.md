## Context

Anchors on `origin/dev` `bbc52059` (2026-10-25). They supersede the line numbers in the issue
body (`82850b3b`); only `chat_app/app.py` moved (by 8 lines).

- `src/interfaces/chat_app/app.py`
  - `:77` `from src.interfaces.chat_app.document_utils import *`; `:78` the
    `evaluation_console` import.
  - `:2819` `self.data_manager_url = f"http://{dm_host}:{dm_port}"`; `:2822`
    `self._dm_headers = {"Authorization": f"Bearer {dm_token}"} if dm_token else {}`.
  - `:3204-3209` registers `/api/upload/embed` → `trigger_embedding`, guarded by
    `Permission.Upload.EMBED`. This registration does not change.
  - `:6408` `trigger_embedding`; `:6420` `self.chat.vector_manager.update_vectorstore()`;
    then a Postgres query for `ingestion_status = 'failed'` rows and the 200 answers
    (`partial` with `failed`, or the plain success message); `:6470-6472` the generic
    `except Exception` → 500 `{"error": str(e)}`.
  - The proxy pattern: `upload_jira` (`:6356-6406`) and `upload_file` (`:5928-6001`):
    `requests.post(..., headers=self._dm_headers, timeout=..., allow_redirects=False)`,
    redirect → 502 `"Data manager authentication failed"`, `ConnectionError` → 503
    `{"error": "data_manager_unavailable"}`, and in `upload_file` `Timeout` → 504.
- `src/interfaces/uploader_app/app.py`
  - `:113` `protected = self.require_admin`; the `/document_index/*` registrations follow;
    `:150-155` registers `/document_index/update_schedule`.
  - `:192-206` `require_admin`: with auth disabled, it passes; with auth enabled, it passes a
    logged-in session or `Authorization: Bearer <DM_API_TOKEN>`, else it redirects to
    `/login`.
  - `:487-493` `_notify_update`: calls `post_update_hook()` and **catches every exception**
    (logs a warning). The other handlers use it; their failures stay hidden (#699).
- `src/bin/service_data_manager.py:76-77` `trigger_update()` →
  `_ing["run_upload_update"](data_manager.update_vectorstore)`; `:197-199` passes it as
  `post_update_hook`.
- `src/utils/ingestion_status.py:112-113` `run_upload_update` →
  `run_tracked("upload", ...)`: counts the run in (`_join`, which publishes
  `running`/`upload` at once when no run is shown running), takes `ingestion_lock`,
  publishes `running`/`upload`, calls `update_vectorstore(force=True)`, raises
  `RuntimeError("vectorstore update failed: documents not added")` when it returns
  `"failed"`, publishes `error`/`failed` with the message on a raise (and re-raises), else
  `completed`/`done` when no other run is queued.
- `src/data_manager/vectorstore/manager.py:359-394`: the sync returns `"up_to_date"`,
  `"updated"`, or `"failed"` (when `_add_to_postgres` raised).
- `src/interfaces/chat_app/static/modules/upload.js:136-155`: the UI posts to
  `/api/upload/embed`; on `response.ok && data.success` it shows success or the `partial`
  warning; otherwise it shows `data.error` in an error toast.
- Unit tests import `src/interfaces/uploader_app/app` (for example
  `tests/unit/test_catalog_document_limits.py:102`, which builds the wrapper with
  `object.__new__`). They also import `src/interfaces/chat_app/app.py`
  (`tests/unit/test_require_auth.py:35-48` builds that wrapper with `object.__new__`), so
  `trigger_embedding` can be tested directly. The project CLAUDE.md says the opposite; it
  is out of date on this point.
- `src/cli/templates/base-compose.yaml:25-30` and `:225-230`: the data manager and the chat
  app are built from different Dockerfiles, with separate image names and tags.

## Goals / Non-Goals

Goals:
- The chat app's embed action runs in the data manager, takes `ingestion_lock`, and reports
  `state == "running"`, `step == "upload"` while it runs.
- A failed update gives a non-2xx answer at both hops and `state == "error"` on the status.
- `grep -n "update_vectorstore" src/interfaces/chat_app/app.py` prints nothing.

Non-goals: the collection phase of uploads (#648); the other uploader handlers and
`_notify_update`'s swallowed failure (#699); `upload.js`; `GET /api/upload/status`; the
chat process's `vector_manager` object; the failed-documents query (it lists the newest 20
failed documents of any run, as today).

## Decisions

### D1 — The data-manager endpoint calls the hook directly

Add a handler method `embed` on `FlaskAppWrapper`, immediately above `_notify_update`:

```python
    def embed(self):
        """Run the tracked vectorstore update now and answer with its outcome.

        Unlike _notify_update, a failed update is not swallowed: the caller
        gets a non-2xx answer, never a silent success.
        """
        if not self.post_update_hook:
            return jsonify({"error": "embed_unavailable"}), 503
        try:
            self.post_update_hook()
        except Exception as exc:
            logger.error("Embed update failed: %s", exc)
            return jsonify({"error": "embed_failed", "detail": str(exc)}), 500
        return jsonify({"status": "ok"})
```

Register it in `__init__` immediately above the `/document_index/update_schedule`
registration:

```python
        self.add_endpoint(
            "/document_index/embed",
            "embed",
            protected(self.embed),
            methods=["POST"],
        )
```

- **Why not `_notify_update`:** it catches every exception, so the endpoint would answer
  200 after a failed sync. That is the silent success the issue forbids.
- **Why 503 for a missing hook:** without a hook the endpoint cannot run an update, and a
  2xx would claim one ran. In the data-manager service the hook is always
  `trigger_update`, so this branch is defensive.
- **Why `detail` carries `str(exc)`:** the other uploader handlers answer
  `{"error": <code>, "detail": str(exc)}`, and the chat action already answered
  `{"error": str(e)}` before this change, so no new text reaches the user.
- **Synchronous by design:** the request holds until the update ends, so the chat app can
  report the outcome. It waits for `ingestion_lock` like the existing upload, git and Jira
  requests, which already reach `trigger_update` through `_notify_update` inside the
  request. If the chat side stops waiting (D3), the data manager still finishes the update
  and publishes its terminal state.

### D2 — The chat-side helper

New module `src/interfaces/chat_app/embed_proxy.py`:

```python
EMBED_CONNECT_TIMEOUT_SECONDS = 10
EMBED_READ_TIMEOUT_SECONDS = 3600


def request_embed(data_manager_url: str, headers: dict) -> Optional[Tuple[dict, int]]:
    """Return None on success, else the (body, status) the chat app answers with."""
```

It calls `requests.post(f"{data_manager_url}/document_index/embed", headers=headers,
timeout=(EMBED_CONNECT_TIMEOUT_SECONDS, EMBED_READ_TIMEOUT_SECONDS),
allow_redirects=False)` and maps:

| Outcome | Answer |
|---|---|
| `requests.exceptions.ConnectionError` (this includes `ConnectTimeout`) | 503 `{"error": "data_manager_unavailable"}` |
| `requests.exceptions.Timeout` (a `ReadTimeout`) | 504 `{"error": "The data manager did not answer in time. The update can still be running: reload this page later to see the document status."}` |
| `resp.is_redirect` or status in `(301, 302, 303, 307, 308)` | 502 `{"error": "Data manager authentication failed"}` |
| body is not a JSON object | 502 `{"error": "Data manager error (HTTP <code>)"}` |
| 2xx and `body["status"] == "ok"` | `None` (success) |
| 2xx without `status == "ok"` | 502 `{"error": "Data manager error (HTTP <code>)"}` |
| any other status with a JSON object | 500 `{"error": body["detail"] or body["error"] or "embed_failed"}` |

- **`except` order matters:** `ConnectTimeout` subclasses both `ConnectionError` and
  `Timeout`. Catch `ConnectionError` first, so a data manager that cannot be reached is 503,
  and only a `ReadTimeout` is 504.
- **Success needs `status == "ok"`**, like `upload_jira` (`resp.status_code == 200 and
  dm_data.get("status") == "ok"`). A 2xx with any other body is not proof that an update
  ran.
- **Data-manager errors are 500**, as the issue's plan says, with the data manager's
  message (`detail`, else `error`). A non-JSON answer (for example an HTML 404 from a data
  manager without the endpoint) is 502, like the existing proxies.
- Other `requests` exceptions are not caught. They reach `trigger_embedding`'s existing
  `except Exception` → 500, as today.
- Log with `src.utils.logging.get_logger(__name__)`. Do not log headers (they carry the
  data-manager token).

### D3 — Timeouts

The data manager answers only when its update ends, and the update can first wait for
`ingestion_lock` behind an initial ingest that runs for 22–64 minutes
(`src/utils/ingestion_status.py:1-7`). A 10-second connect timeout reports a data manager
that cannot be reached quickly. A 3600-second read timeout lets a queued update finish in
most cases. On a read timeout the chat app answers 504 and tells the user that the update
can still be running, and to reload the page later. The message does not name
`GET /api/ingestion/status`: `upload.js:153` shows it in a toast, and the chat app does not
serve that path (only the data manager does, on its own port). The log line names the data
manager's endpoint for the operator, and the docs describe it.
The chat app serves requests from gunicorn `gthread` threads (`src/bin/service_chat.py:28-34`);
before this change the same request already blocked for the whole sync in the chat
process.

### D4 — `trigger_embedding` is a thin call site

Add `from src.interfaces.chat_app.embed_proxy import request_embed` immediately after the
`document_utils` import (`:77`). In `trigger_embedding` replace the one line
`self.chat.vector_manager.update_vectorstore()` with:

```python
            failure = request_embed(self.data_manager_url, self._dm_headers)
            if failure is not None:
                body, status = failure
                return jsonify(body), status
```

Keep the two log lines around it, the failed-documents query, both 200 answers, and the
generic `except`. Change nothing else in `app.py`. Unit tests call `trigger_embedding`
directly (D5), so these lines are covered.

### D5 — Tests

- `tests/unit/test_uploader_embed_endpoint.py` (new): the handler is tested with
  `object.__new__(FlaskAppWrapper)` and a real `build_ingestion_helpers(...)` hook, so the
  status assertions exercise the real `run_upload_update`. The registration test builds the
  real wrapper with its 7 constructor dependencies patched (`get_full_config`,
  `read_secret`, `build_persistence`, `PostgresCatalogService`, `ScraperManager`,
  `TicketManager`, `LocalFileManager`) and auth enabled, and proves the route is POST-only
  and admin-protected.
- `tests/unit/test_chat_embed_proxy.py` (new): patches `embed_proxy.requests.post` and
  covers every row of the D2 table, with the literal `(10, 3600)` timeout. One test reads
  `src/interfaces/chat_app/app.py` as text and asserts that `update_vectorstore` is absent
  (the acceptance grep as a unit test). Three tests call `trigger_embedding` on an
  `object.__new__(FlaskAppWrapper)` (the `test_require_auth.py` pattern) with
  `chat_app.app.request_embed` and `psycopg2.connect` patched: a proxy failure is answered
  with the proxy's body and status and no database query; a success keeps the plain 200
  answer; a success with failed documents keeps the `partial` 200 answer.
- Both files ran on a scratch copy of `origin/dev` `bbc52059`: before the change, 5 failed
  (4 `AttributeError: ... no attribute 'embed'`, 1 `assert 404 == 302`) and the proxy file
  failed at collection (`ImportError: cannot import name 'embed_proxy'`); with only
  `embed_proxy.py` added, the proxy file gave `4 failed, 9 passed`; after the change,
  18 passed (5 + 13). A mutant `app.py` that calls `request_embed` and ignores its result
  fails `test_trigger_embedding_answers_with_the_proxy_failure`. Neighbours
  `test_catalog_document_limits.py`, `test_ingestion_status_lock.py`,
  `test_ingest_persistence_wiring.py`, `test_data_manager_embedding_progress.py` and
  `test_openai_compat_local_only.py` passed unchanged. Patch coverage: 100 % (46 lines,
  0 missing). The exact bodies are in the `tasks.md` appendix.

### D6 — Docs

In `docs/docs/api_reference.md`:
- Add a `### POST /document_index/embed` section in "Data Manager", after
  `GET /api/ingestion/status`. Spell out the guard (the page documents no other
  `/document_index/*` endpoint): with `services.data_manager.auth.enabled` false the
  endpoint is open; with it true, the request needs an admin session or the header
  `Authorization: Bearer <DM_API_TOKEN>`, else the answer is a 302 redirect to `/login`.
  It accepts POST only, runs the tracked upload update synchronously, and reports
  `step == "upload"` while it runs. List the three answers from D1.
- Add a `## Uploads` section after "Data Viewer" (before "Analytics") with a
  `### POST /api/upload/embed` section: needs the `Upload.EMBED` permission; runs the update
  in the data manager (it waits for any running ingest or refresh, and reports on the data
  manager's `GET /api/ingestion/status` as `step == "upload"`); the 200 answers (`success`,
  `partial` + `failed`) and the error answers from D2 (500, 502, 503, 504). For 504, say
  that the update can still be running and that the data manager's
  `GET /api/ingestion/status` shows when it ends.
- In the `GET /api/ingestion/status` section, change "While a file upload is being
  ingested, `step` is `upload`." so it also names the chat app's Process Documents action
  (`POST /api/upload/embed`).

## Risks / Trade-offs

- **A failed sync is now a visible error.** Before, the chat action answered 200 after a
  sync that returned `"failed"`. This is the intended change (issue acceptance criteria).
- **The action now waits behind a running ingest.** That is the point of taking
  `ingestion_lock`: the action must not change the corpus while a data-manager run does.
  The UI keeps its "Processing..." state until the answer arrives.
- **Version skew:** a chat app with this change and a data manager without the endpoint
  answers 502 (`Data manager error (HTTP 404)`), not a silent success. The two services
  ship as separate images (`base-compose.yaml:25-30`, `:225-230`), so the 502 lasts until
  the data manager is redeployed too. Deploy both together.
- **Merge order:** #648 and #699 edit other handlers in `uploader_app/app.py`. This change
  adds one method and one registration and edits no existing handler, so a later rebase is
  small.
