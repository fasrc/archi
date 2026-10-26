## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-652-route-embed-through-data-manager` exists, cut from
      `origin/dev` at `bbc52059` on 2026-10-26. You are on it. Do not re-branch and do not
      rebase.
- [x] 0.2 `openspec validate fix-issue-652-route-embed-through-data-manager --strict` passed
      on the host. Do not add a task that runs it again.
- [x] 0.3 Every design decision is made in `design.md` (D1–D6). Follow the design; where the
      issue body and the design differ, the design wins.
- [x] 0.4 The test files in the Appendix ran on a scratch copy of `bbc52059`, red and then
      green, with the exact counts written in each task below. Copy them **verbatim**. Do
      not rewrite, rename, or "improve" them.

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed. Write the failing test,
  watch it fail, make it pass, then commit — all in one task.
- **Every task ends with exactly one commit**, and you tick its checkbox in this file in the
  same commit. Do not add a task that changes no file.
- Do not implement a later task's behaviour early.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After each
  commit, `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer. Commit
  messages: short, lowercase.
- Edit only these files: `src/interfaces/uploader_app/app.py`,
  `src/interfaces/chat_app/embed_proxy.py` (new), `src/interfaces/chat_app/app.py`,
  `tests/unit/test_uploader_embed_endpoint.py` (new), `tests/unit/test_chat_embed_proxy.py`
  (new), `docs/docs/api_reference.md`, and this `tasks.md`. Do not edit
  `src/bin/service_data_manager.py`, `src/utils/ingestion_status.py`, `upload.js`, any
  other uploader handler, or any control-plane file. The PR body goes in `/tmp`, never in
  the repo.

## 1. The data-manager endpoint

- [x] 1.1 Add `POST /document_index/embed` to the uploader app (design D1). One commit
      (`feat(#652): data-manager embed endpoint runs the tracked upload update`).
      (a) Create `tests/unit/test_uploader_embed_endpoint.py` with the body of **Appendix
      A**, verbatim. Run
      `python -m pytest -q tests/unit/test_uploader_embed_endpoint.py`: expect
      `5 failed` — four with `AttributeError: 'FlaskAppWrapper' object has no attribute
      'embed'` and `test_embed_route_is_registered_post_only_and_admin_protected` with
      `assert 404 == 302`.
      (b) In `src/interfaces/uploader_app/app.py`, add the `embed` method immediately above
      `def _notify_update` and the `/document_index/embed` registration immediately above
      the `/document_index/update_schedule` registration, both exactly as in design D1. Do
      not call `_notify_update` from `embed` (it swallows failures).
      (c) Run the same file: expect `5 passed`. Then run
      `python -m pytest -q tests/unit/test_catalog_document_limits.py tests/unit/test_ingestion_status_lock.py tests/unit/test_ingest_persistence_wiring.py`:
      all pass unchanged.

## 2. The chat-side proxy and the thin call site

- [x] 2.1 Add `src/interfaces/chat_app/embed_proxy.py` and route `trigger_embedding` through
      it (design D2, D4). One commit
      (`fix(#652): route /api/upload/embed through the data manager`).
      (a) Create `tests/unit/test_chat_embed_proxy.py` with the body of **Appendix B**,
      verbatim. Run `python -m pytest -q tests/unit/test_chat_embed_proxy.py`: expect a
      collection error, `ImportError: cannot import name 'embed_proxy'`.
      (b) Create `src/interfaces/chat_app/embed_proxy.py` with the body of **Appendix C**,
      verbatim. Run the same file: expect `4 failed, 9 passed`, because `app.py` is not
      changed yet: `test_chat_app_has_no_direct_vectorstore_write` (assert) and the three
      `test_trigger_embedding_*` tests (`AttributeError: ... does not have the attribute
      'request_embed'`).
      (c) In `src/interfaces/chat_app/app.py`, add
      `from src.interfaces.chat_app.embed_proxy import request_embed` on the line after
      `from src.interfaces.chat_app.document_utils import *`, and in `trigger_embedding`
      replace the single line `self.chat.vector_manager.update_vectorstore()` with the four
      lines in design D4. Change nothing else in `app.py`.
      (d) Run the same file: expect `13 passed`.
      `grep -n "update_vectorstore" src/interfaces/chat_app/app.py` must print nothing.
      Then run `python -m pytest -q tests/unit/test_openai_compat_local_only.py tests/unit/test_data_manager_embedding_progress.py`:
      all pass unchanged.

## 3. Docs

- [x] 3.1 Update `docs/docs/api_reference.md` as design D6 says, and no other file under
      `docs/`. One commit (`docs(#652): document the embed endpoints`).
      Write the `POST /api/upload/embed` section with its answers: 200 `success` (and
      `partial` + `failed`), 500 (the data manager's message), 502 (auth redirect, or a
      non-JSON or not-ok answer), 503 `data_manager_unavailable`, 504 (read timeout; the
      update can still be running, and the data manager's `GET /api/ingestion/status` shows
      when it ends). Write the `POST /document_index/embed` section with its guard (auth
      off: open; auth on: an admin session or `Authorization: Bearer <DM_API_TOKEN>`, else a
      302 redirect to `/login`), POST only, and the answers 200 `{"status": "ok"}`, 500
      `embed_failed` + `detail`, 503 `embed_unavailable`. If you
      link to the status section, use the existing anchor `#get-apiingestionstatus`; add no
      other link. Check:
      `grep -n '^### `POST /document_index/embed`\|^### `POST /api/upload/embed`\|^## Uploads' docs/docs/api_reference.md`
      prints 3 lines.

## 4. Publish

- [x] 4.1 Tick this box, commit the tick through the hook (the gate runs on that commit;
      `chore(#652): mark the publish task done`), then push and open the PR — in that order,
      so the tick rides the push. Before the push, confirm
      `git diff origin/dev --stat -- docs/` lists only `docs/docs/api_reference.md`, and
      `git status --porcelain` is empty. Push with
      `git push -u origin fix/issue-652-route-embed-through-data-manager`. Write the PR body
      to `/tmp/pr-652.md` with `Closes #652` in the body (never only in the title), the
      per-task summary, the behaviour change (a sync that returns `"failed"` now answers
      non-2xx), the deploy note (the chat app and the data manager ship as separate images:
      deploy both), and the patch coverage the gate printed. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-652-route-embed-through-data-manager --title "fix(#652): route /api/upload/embed through the data manager's tracked upload update" --body-file /tmp/pr-652.md`.
      The task is complete when `gh pr view --repo fasrc/archi fix/issue-652-route-embed-through-data-manager`
      prints the PR.

## 5. Post-PR verification finding (added at wrap-up, 2026-10-26)

- [x] 5.1 Fix the `message` row of the `POST /api/upload/embed` 200 table, and pin the
      partial `message` in its test. One commit
      (`docs(#652): the partial embed answer also carries message`).
      The finding: `docs/docs/api_reference.md:687` says `message` is
      `"Embedding complete. Documents are now searchable."` and is present only when all
      files embedded. But `trigger_embedding` (`src/interfaces/chat_app/app.py:6451-6459`)
      also sends `message` on the partial answer, as
      `f"{len(failed_docs)} document(s) failed to process."`, and the query has
      `LIMIT 20` (`app.py:6431-6437`).
      (a) In `tests/unit/test_chat_embed_proxy.py`, in
      `test_trigger_embedding_keeps_the_partial_answer`, add the line
      `    assert body["message"] == "2 document(s) failed to process."` directly after the
      line `    assert body["partial"] is True`. Do not change or rename any other test. Run
      `python -m pytest -q tests/unit/test_chat_embed_proxy.py`: expect `13 passed`.
      (b) In `docs/docs/api_reference.md`, replace the whole `message` row (the line that
      starts with ``| `message` |``) with exactly this line:
      ``| `message` | `"Embedding complete. Documents are now searchable."`, or `"<n> document(s) failed to process."` | Always present. The second form comes with `partial`; `<n>` is the number of entries in `failed`. |``
      and replace the whole `failed` row (the line that starts with ``| `failed` |``)
      with exactly this line:
      ``| `failed` | `[{"file": "<name>", "error": "<reason>"}, ...]` | Present with `partial`; lists up to 20 failed files, newest first. |``
      Change no other line under `docs/`.
      (c) Tick this box, commit through the hook (the gate runs on that commit), then push
      with `git push origin fix/issue-652-route-embed-through-data-manager`. After the push,
      `git status --porcelain` must be empty and `git rev-parse HEAD` must equal
      `git rev-parse origin/fix/issue-652-route-embed-through-data-manager`. Do not open a
      new PR: PR #784 is already open.

## Appendix — exact file bodies (no checkboxes; copy verbatim)

These ran on a scratch copy of `origin/dev` `bbc52059` and are black- and isort-clean.

### Appendix A — `tests/unit/test_uploader_embed_endpoint.py`

```python
"""POST /document_index/embed runs the tracked upload update (issue #652)."""

import threading
from unittest.mock import patch

from flask import Flask

from src.interfaces.uploader_app import app as uploader_app_module
from src.utils.ingestion_status import build_ingestion_helpers


def _tracked_hook(update_vectorstore):
    ing = build_ingestion_helpers(lambda: None, threading.RLock())
    return ing, (lambda: ing["run_upload_update"](update_vectorstore))


def _embed_client(hook):
    wrapper = object.__new__(uploader_app_module.FlaskAppWrapper)
    wrapper.post_update_hook = hook
    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/document_index/embed", "embed", wrapper.embed, methods=["POST"]
    )
    return flask_app.test_client()


def test_embed_endpoint_reports_upload_while_the_sync_runs():
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def update_vectorstore(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(5)
        return "updated"

    ing, hook = _tracked_hook(update_vectorstore)
    client = _embed_client(hook)
    result = {}
    worker = threading.Thread(
        target=lambda: result.update(resp=client.post("/document_index/embed"))
    )
    worker.start()
    assert entered.wait(5)
    during = ing["get_ingestion_status"]()
    release.set()
    worker.join(5)

    assert during["state"] == "running"
    assert during["step"] == "upload"
    assert result["resp"].status_code == 200
    assert result["resp"].get_json() == {"status": "ok"}
    assert calls == [{"force": True}]
    assert ing["get_ingestion_status"]()["state"] == "completed"


def test_embed_endpoint_answers_non_2xx_when_the_sync_failed():
    ing, hook = _tracked_hook(lambda **kwargs: "failed")

    resp = _embed_client(hook).post("/document_index/embed")

    assert resp.status_code == 500
    body = resp.get_json()
    assert body["error"] == "embed_failed"
    assert "documents not added" in body["detail"]
    status = ing["get_ingestion_status"]()
    assert status["state"] == "error"
    assert status["step"] == "failed"


def test_embed_endpoint_answers_non_2xx_when_the_sync_raises():
    def update_vectorstore(**kwargs):
        raise RuntimeError("boom")

    ing, hook = _tracked_hook(update_vectorstore)

    resp = _embed_client(hook).post("/document_index/embed")

    assert resp.status_code == 500
    assert resp.get_json() == {"error": "embed_failed", "detail": "boom"}
    assert ing["get_ingestion_status"]()["error"] == "boom"


def test_embed_endpoint_without_a_hook_is_not_a_success():
    resp = _embed_client(None).post("/document_index/embed")

    assert resp.status_code == 503
    assert resp.get_json()["error"] == "embed_unavailable"


def _constructed_client(tmp_path, hook):
    config = {
        "global": {"DATA_PATH": str(tmp_path)},
        "services": {"postgres": {}, "data_manager": {"auth": {"enabled": True}}},
        "data_manager": {},
    }
    secret_values = {"DM_API_TOKEN": "dm-token"}
    m = uploader_app_module
    with (
        patch.object(m, "get_full_config", return_value=config),
        patch.object(m, "read_secret", side_effect=lambda k: secret_values.get(k, "")),
        patch.object(m, "build_persistence"),
        patch.object(m, "PostgresCatalogService"),
        patch.object(m, "ScraperManager"),
        patch.object(m, "TicketManager"),
        patch.object(m, "LocalFileManager"),
    ):
        flask_app = Flask(__name__)
        m.FlaskAppWrapper(flask_app, post_update_hook=hook)
    return flask_app.test_client()


def test_embed_route_is_registered_post_only_and_admin_protected(tmp_path):
    calls = []
    client = _constructed_client(tmp_path, lambda: calls.append("hook"))

    anonymous = client.post("/document_index/embed")
    wrong_method = client.get(
        "/document_index/embed", headers={"Authorization": "Bearer dm-token"}
    )
    service = client.post(
        "/document_index/embed", headers={"Authorization": "Bearer dm-token"}
    )

    assert anonymous.status_code == 302
    assert anonymous.headers["Location"].endswith("/login")
    assert wrong_method.status_code == 405
    assert service.status_code == 200
    assert calls == ["hook"]
```

### Appendix B — `tests/unit/test_chat_embed_proxy.py`

```python
"""The chat app's embed proxy to the data manager (issue #652)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from flask import Flask

import src.interfaces.chat_app.app as chat_app_module
from src.interfaces.chat_app import embed_proxy

BASE = "http://data-manager:7871"
HEADERS = {"Authorization": "Bearer dm-token"}


def _response(status_code, body=None, *, is_redirect=False):
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_redirect = is_redirect
    if body is None:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = body
    return resp


def _call(resp=None, *, raises=None):
    with patch.object(embed_proxy.requests, "post") as post:
        if raises is not None:
            post.side_effect = raises
        else:
            post.return_value = resp
        result = embed_proxy.request_embed(BASE, HEADERS)
    return result, post


def test_success_returns_none_and_posts_to_the_embed_endpoint():
    result, post = _call(_response(200, {"status": "ok"}))

    assert result is None
    post.assert_called_once_with(
        f"{BASE}/document_index/embed",
        headers=HEADERS,
        timeout=(10, 3600),
        allow_redirects=False,
    )


def test_auth_redirect_maps_to_502():
    result, _ = _call(_response(302, None, is_redirect=True))

    assert result == ({"error": "Data manager authentication failed"}, 502)


def test_unreachable_data_manager_maps_to_503():
    result, _ = _call(raises=requests.exceptions.ConnectionError("refused"))

    assert result == ({"error": "data_manager_unavailable"}, 503)


def test_connect_timeout_maps_to_503():
    result, _ = _call(raises=requests.exceptions.ConnectTimeout("slow connect"))

    assert result == ({"error": "data_manager_unavailable"}, 503)


def test_read_timeout_maps_to_504():
    result, _ = _call(raises=requests.exceptions.ReadTimeout("slow answer"))

    body, status = result
    assert status == 504
    assert "still be running" in body["error"]


def test_data_manager_error_forwards_its_message_as_500():
    resp = _response(500, {"error": "embed_failed", "detail": "boom"})

    result, _ = _call(resp)

    assert result == ({"error": "boom"}, 500)


def test_data_manager_error_without_detail_forwards_its_error_code():
    result, _ = _call(_response(503, {"error": "embed_unavailable"}))

    assert result == ({"error": "embed_unavailable"}, 500)


def test_non_json_answer_maps_to_502():
    result, _ = _call(_response(404, None))

    assert result == ({"error": "Data manager error (HTTP 404)"}, 502)


def test_2xx_without_status_ok_is_not_a_success():
    result, _ = _call(_response(200, {"status": "queued"}))

    assert result == ({"error": "Data manager error (HTTP 200)"}, 502)


def test_chat_app_has_no_direct_vectorstore_write():
    source = Path("src/interfaces/chat_app/app.py").read_text()

    assert "update_vectorstore" not in source
    assert "from src.interfaces.chat_app.embed_proxy import request_embed" in source


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.sql = sql

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def cursor(self):
        return _Cursor(self.rows)

    def close(self):
        self.closed = True


def _trigger_embedding(proxy_result, rows=()):
    wrapper = object.__new__(chat_app_module.FlaskAppWrapper)
    wrapper.data_manager_url = BASE
    wrapper._dm_headers = HEADERS
    wrapper.chat = SimpleNamespace(pg_config={"host": "pg"})
    conn = _Connection(list(rows))
    with (
        patch.object(
            chat_app_module, "request_embed", return_value=proxy_result
        ) as proxy,
        patch.object(chat_app_module.psycopg2, "connect", return_value=conn) as connect,
        Flask(__name__).test_request_context(),
    ):
        resp, status = wrapper.trigger_embedding()
        body = resp.get_json()
    return body, status, proxy, connect


def test_trigger_embedding_answers_with_the_proxy_failure():
    body, status, proxy, connect = _trigger_embedding(({"error": "boom"}, 500))

    assert (body, status) == ({"error": "boom"}, 500)
    proxy.assert_called_once_with(BASE, HEADERS)
    connect.assert_not_called()


def test_trigger_embedding_keeps_the_success_answer():
    body, status, proxy, _ = _trigger_embedding(None)

    assert status == 200
    assert body == {
        "success": True,
        "message": "Embedding complete. Documents are now searchable.",
    }
    proxy.assert_called_once_with(BASE, HEADERS)


def test_trigger_embedding_keeps_the_partial_answer():
    rows = [("a.pdf", "bad pdf"), ("b.txt", None)]

    body, status, _, connect = _trigger_embedding(None, rows)

    assert status == 200
    assert body["success"] is True
    assert body["partial"] is True
    assert body["failed"] == [
        {"file": "a.pdf", "error": "bad pdf"},
        {"file": "b.txt", "error": "Unknown error"},
    ]
    connect.assert_called_once_with(host="pg")
```

### Appendix C — `src/interfaces/chat_app/embed_proxy.py`

```python
"""Chat-side proxy for the data manager's tracked embed update (issue #652)."""

from typing import Optional, Tuple

import requests

from src.utils.logging import get_logger

logger = get_logger(__name__)

# The data manager answers only when its update ends, and the update can wait
# for the ingestion lock behind an ingest that runs for up to about an hour.
EMBED_CONNECT_TIMEOUT_SECONDS = 10
EMBED_READ_TIMEOUT_SECONDS = 3600

_REDIRECT_CODES = (301, 302, 303, 307, 308)


def request_embed(data_manager_url: str, headers: dict) -> Optional[Tuple[dict, int]]:
    """Ask the data manager to run its tracked embed update.

    Returns None on success, else the (body, status) the chat app answers with.
    """
    try:
        resp = requests.post(
            f"{data_manager_url}/document_index/embed",
            headers=headers,
            timeout=(EMBED_CONNECT_TIMEOUT_SECONDS, EMBED_READ_TIMEOUT_SECONDS),
            allow_redirects=False,
        )
    except requests.exceptions.ConnectionError:
        logger.error("Data manager service unavailable")
        return {"error": "data_manager_unavailable"}, 503
    except requests.exceptions.Timeout:
        logger.error(
            "Data manager did not answer the embed request in time; "
            "its GET /api/ingestion/status shows whether the update still runs"
        )
        return (
            {
                "error": "The data manager did not answer in time. The update can "
                "still be running: reload this page later to see the document status."
            },
            504,
        )

    if resp.is_redirect or resp.status_code in _REDIRECT_CODES:
        logger.error("Data-manager rejected embed (auth redirect)")
        return {"error": "Data manager authentication failed"}, 502

    try:
        body = resp.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return {"error": f"Data manager error (HTTP {resp.status_code})"}, 502

    if 200 <= resp.status_code < 300:
        if body.get("status") == "ok":
            return None
        return {"error": f"Data manager error (HTTP {resp.status_code})"}, 502

    message = body.get("detail") or body.get("error") or "embed_failed"
    return {"error": message}, 500
```
