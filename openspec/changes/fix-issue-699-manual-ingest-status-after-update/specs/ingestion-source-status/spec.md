## ADDED Requirements

### Requirement: A manual ingest writes its source status after its vector update
The uploader SHALL write the per-source status of a manual ingest only after the vector update that follows it returns, and on success SHALL write `state == "idle"` with a new `last_run`.
This applies to `add_git_repo` and `remove_git_repo` (key `git`), `add_jira_project` (key `jira`), `upload` (key `local_files`), and `upload_url` (key `web`). Each handler keeps its existing 200 answer body.

#### Scenario: The update sees the old status
- **WHEN** a manual ingest through any of the five handlers runs and `post_update_hook` reads the status file during its call
- **THEN** the hook sees the old entry for that key, with the old `last_run`
- **AND** after the answer the entry has `state == "idle"` and a `last_run` that is not earlier than the request start

#### Scenario: The 200 answer is unchanged
- **WHEN** `add_git_repo` succeeds
- **THEN** the answer is 200 `{"status": "ok"}`
- **AND** `remove_git_repo` of one matching resource answers 200 `{"status": "ok", "repo": <name>, "deleted": 1}`, and `upload_url` answers 200 with `resources_scraped`

#### Scenario: No update hook
- **WHEN** the wrapper has no `post_update_hook` and `add_git_repo` succeeds
- **THEN** the answer is 200 `{"status": "ok"}` and the `git` entry has `state == "idle"` with a new `last_run`

### Requirement: A failed manual-ingest update is recorded and is not a success
The uploader SHALL write `state == "error"` and keep the old `last_run` for the key of a manual ingest whose vector update raises, and SHALL answer HTTP 500 `{"error": "update_failed", "detail": <message>}` instead of a 2xx answer.

#### Scenario: The update raises
- **WHEN** a manual ingest through any of the five handlers runs and `post_update_hook` raises `RuntimeError("sync down")`
- **THEN** the answer is 500 `{"error": "update_failed", "detail": "sync down"}`
- **AND** the entry for that key is exactly `{"state": "error", "last_run": <old last_run>}`

#### Scenario: A collection failure is unchanged
- **WHEN** the git collector raises `RuntimeError("clone down")` in `add_git_repo`
- **THEN** the answer is 500 `{"error": "ingest_failed", "detail": "clone down"}`
- **AND** `post_update_hook` is not called and the `git` entry is unchanged
