## ADDED Requirements

### Requirement: Overlapping writes to the per-source status file keep both updates
The data manager SHALL make every read-modify-write of `ingestion_status.json` hold one process-wide lock from the read to the write, so a write that overlaps another write keeps the other write's change.
The scheduled refresh (`set_source_status`), the start-up seed, `reload_schedules`, and the uploader's `FlaskAppWrapper._update_source_status` all write through `src/utils/source_status_file.py`.

#### Scenario: A reload overlaps a refresh that writes error
- **GIVEN** the file holds `{"git": {"state": "running", "schedule": "0 1 * * *"}}`
- **WHEN** `set_source_schedules(path, {"git": "0 4 * * *"})` has read the file and not yet written it, and `update_source_status(path, "git", state="error")` runs in another thread
- **THEN** after both return the file holds `{"git": {"state": "error", "schedule": "0 4 * * *"}}`

#### Scenario: An uploader write overlaps a refresh that writes error
- **GIVEN** the file holds `{"git": {"state": "running"}}`
- **WHEN** `FlaskAppWrapper._update_source_status("git", schedule="0 4 * * *")` has read the file and not yet written it, and `update_source_status(path, "git", state="error")` runs in another thread
- **THEN** after both return the file holds `{"git": {"state": "error", "schedule": "0 4 * * *"}}`

#### Scenario: Two uploader writes overlap
- **GIVEN** the file holds `{"git": {"state": "running"}}`
- **WHEN** `_update_source_status("git", schedule="0 4 * * *")` has read the file and not yet written it, and `_update_source_status("git", state="error")` runs in another thread
- **THEN** after both return the file holds `{"git": {"state": "error", "schedule": "0 4 * * *"}}`

#### Scenario: The data manager's writers use the file
- **GIVEN** the file holds `{"git": {"state": "idle", "last_run": "t0"}}` and the config schedules `git` and `jira`
- **WHEN** `service_data_manager.main()` starts, a scheduled refresh of `git` fails, and `POST /api/reload-schedules` returns `{"git": "0 5 * * *", "links": "0 6 * * *"}`
- **THEN** after the start `jira` is `{"schedule": "0 4 * * *", "state": "idle"}` and `git` keeps `last_run == "t0"`
- **AND** after the failed refresh `git` has `state == "error"` and `last_run == "t0"`
- **AND** after the reload `git` has `schedule == "0 5 * * *"` and `links` has no entry

#### Scenario: No writer touches the file directly
- **WHEN** the source of `src/bin/service_data_manager.py` and `src/interfaces/uploader_app/app.py` is searched
- **THEN** neither contains `write_text`, `json.loads(self.status_file` or `status_file.read_text`
- **AND** both import from `src.utils.source_status_file`

### Requirement: A reader never sees a partly written status file
The data manager SHALL write `ingestion_status.json` to a temporary file in the same directory and then put it in place with one `os.replace`, so the path always names a whole file.

#### Scenario: The write replaces the whole file in one step
- **GIVEN** the file holds `{"git": {"state": "idle", "last_run": "2026-11-01T00:00:00+00:00"}}`
- **WHEN** `update_source_status(path, "git", state="error")` runs
- **THEN** `os.replace` is called once, with a source in the same directory as the file
- **AND** at that call the file still holds the whole old content and the source holds the whole new content
- **AND** afterwards the directory holds only `ingestion_status.json`

#### Scenario: A failed write keeps the old file
- **GIVEN** the file holds `{"git": {"state": "idle"}}`
- **WHEN** `update_source_status(path, "git", state="error")` runs and `os.fsync` raises `OSError`
- **THEN** the call raises `OSError`
- **AND** the file still holds `{"git": {"state": "idle"}}` and the directory holds no temporary file

### Requirement: The status writers keep their field rules and error behaviour
A status write SHALL change only the fields it is given, and the uploader's `_update_source_status` SHALL log a failed write and not raise.

#### Scenario: An update keeps the other fields and sources
- **GIVEN** `git` has `state`, `last_run` and `schedule`, and `jira` has `state`
- **WHEN** `update_source_status(path, "git", state="running")` runs
- **THEN** `git` keeps its `last_run` and `schedule`, and `jira` is unchanged

#### Scenario: An empty schedule removes the schedule
- **WHEN** `update_source_status(path, "git", schedule="")` runs on an entry that has a schedule
- **THEN** the entry has no `schedule` key

#### Scenario: The seed keeps existing values
- **GIVEN** `git` has `state == "error"` and `schedule == "0 1 * * *"`
- **WHEN** `seed_source_schedules(path, {"git": "0 2 * * *", "jira": "0 3 * * *", "links": ""})` runs
- **THEN** `git` is unchanged, `jira` is `{"schedule": "0 3 * * *", "state": "idle"}`, and `links` has no entry

#### Scenario: A reload sets schedules only on existing entries
- **WHEN** `set_source_schedules(path, {"git": "0 4 * * *", "jira": "0 5 * * *"})` runs and only `git` has an entry
- **THEN** `git` has `schedule == "0 4 * * *"` and `jira` has no entry

#### Scenario: Content that is not an object reads as empty with a warning
- **GIVEN** the file holds `["git"]`
- **WHEN** `load_source_status(path)` runs
- **THEN** it returns `{}` and logs a WARNING that contains `not a JSON object`

#### Scenario: The uploader logs a failed write
- **WHEN** `_update_source_status("git", state="idle")` runs and the file's directory cannot be created
- **THEN** the call returns without raising and logs `Failed to update source status`
