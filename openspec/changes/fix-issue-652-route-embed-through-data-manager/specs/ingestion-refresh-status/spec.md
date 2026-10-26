## ADDED Requirements

### Requirement: The data-manager embed endpoint runs the tracked upload update
The data manager SHALL serve `POST /document_index/embed`, which runs the upload-triggered update through `run_upload_update` synchronously and answers 200 `{"status": "ok"}` only after the update completed.
While the update runs, the ingestion status SHALL show `state == "running"` and `step == "upload"`. The endpoint SHALL accept only POST and SHALL use the same admin guard as the other `/document_index/*` endpoints.

#### Scenario: Status during and after an embed request
- **WHEN** `POST /document_index/embed` runs and `update_vectorstore` blocks until the test releases it
- **THEN** while it blocks, the status has `state == "running"` and `step == "upload"`
- **AND** `update_vectorstore` was called once with `force=True`
- **AND** the answer is 200 `{"status": "ok"}` and the status then has `state == "completed"`

#### Scenario: An unauthenticated request is refused
- **WHEN** auth is enabled and `POST /document_index/embed` arrives with no session and no `Authorization: Bearer <DM_API_TOKEN>` header
- **THEN** the answer is a redirect to `/login` and the update does not run
- **AND** the same request with the bearer token answers 200, and a GET answers 405

### Requirement: A failed embed update never answers 2xx
The data-manager embed endpoint SHALL answer 500 `{"error": "embed_failed", "detail": <message>}` when the update raises, including a sync that returned `"failed"`, and the ingestion status SHALL show `state == "error"`.
When no update hook is configured, the endpoint SHALL answer 503 `{"error": "embed_unavailable"}`.

#### Scenario: The sync returns failed
- **WHEN** `update_vectorstore` returns `"failed"` during `POST /document_index/embed`
- **THEN** the answer is 500 with `error == "embed_failed"` and a `detail` that contains `documents not added`
- **AND** the status has `state == "error"` and `step == "failed"`

#### Scenario: The sync raises
- **WHEN** `update_vectorstore` raises `RuntimeError("boom")`
- **THEN** the answer is 500 `{"error": "embed_failed", "detail": "boom"}` and the status `error` is `"boom"`

#### Scenario: No hook
- **WHEN** the wrapper has no `post_update_hook`
- **THEN** the answer is 503 with `error == "embed_unavailable"`

### Requirement: The chat embed action runs in the data manager
The chat app's `POST /api/upload/embed` SHALL NOT write the vectorstore from the chat process; it SHALL request `POST /document_index/embed` from the data manager through `request_embed`.
When `request_embed` returns a `(body, status)` pair, the chat action SHALL answer with that body and status and SHALL NOT query the database. On success it SHALL keep its existing 200 answers (`success`, and `partial` with `failed` when failed documents exist).

#### Scenario: No direct vectorstore write in the chat app
- **WHEN** `src/interfaces/chat_app/app.py` is read as text
- **THEN** it does not contain `update_vectorstore`
- **AND** it imports `request_embed` from `src.interfaces.chat_app.embed_proxy`

#### Scenario: The proxy reports a failure
- **WHEN** `trigger_embedding` runs and `request_embed` returns `({"error": "boom"}, 500)`
- **THEN** the answer is 500 `{"error": "boom"}`
- **AND** `request_embed` was called once with the data-manager URL and headers, and no database connection was opened

#### Scenario: The proxy reports success
- **WHEN** `trigger_embedding` runs, `request_embed` returns `None`, and the failed-documents query returns no rows
- **THEN** the answer is 200 `{"success": true, "message": "Embedding complete. Documents are now searchable."}`
- **AND** when the query returns rows `("a.pdf", "bad pdf")` and `("b.txt", None)`, the answer is 200 with `partial` true and `failed` `[{"file": "a.pdf", "error": "bad pdf"}, {"file": "b.txt", "error": "Unknown error"}]`

### Requirement: The chat embed proxy maps every data-manager failure to a non-2xx answer
`request_embed` SHALL return `None` only for a 2xx answer whose JSON body has `status == "ok"`, and SHALL otherwise return the chat app's error body and status.
The mapping SHALL be: an auth redirect is 502, a data manager that cannot be reached (including a connect timeout) is 503 `data_manager_unavailable`, a read timeout is 504 with a message that says the update can still be running, a data-manager error with a JSON body is 500 with the data manager's `detail` (else its `error`), and a non-JSON body or a 2xx without `status == "ok"` is 502.

#### Scenario: Success
- **WHEN** the data manager answers 200 `{"status": "ok"}`
- **THEN** `request_embed` returns `None` and posted once to `<data_manager_url>/document_index/embed` with the given headers, `allow_redirects=False` and the `(10, 3600)` timeout

#### Scenario: Auth redirect
- **WHEN** the data manager answers 302
- **THEN** the result is `({"error": "Data manager authentication failed"}, 502)`

#### Scenario: Unreachable or slow to connect
- **WHEN** `requests.post` raises `ConnectionError` or `ConnectTimeout`
- **THEN** the result is `({"error": "data_manager_unavailable"}, 503)`

#### Scenario: Read timeout
- **WHEN** `requests.post` raises `ReadTimeout`
- **THEN** the status is 504 and the error message contains `still be running`

#### Scenario: Data-manager error
- **WHEN** the data manager answers 500 `{"error": "embed_failed", "detail": "boom"}`
- **THEN** the result is `({"error": "boom"}, 500)`
- **AND** an answer of 503 `{"error": "embed_unavailable"}` gives `({"error": "embed_unavailable"}, 500)`

#### Scenario: Not a JSON object, or not ok
- **WHEN** the data manager answers 404 with a non-JSON body, or 200 `{"status": "queued"}`
- **THEN** the result is a 502 with `Data manager error (HTTP 404)` or `Data manager error (HTTP 200)`
