## 1. Schema

- [ ] 1.1 Add the `deployment_record` table to `src/cli/templates/init.sql` — append-only,
      with `config_ref`, `config_sha`, `config_head`, `pin_matched` (boolean),
      `dirty_paths` (text), `app_version`, `deployed_at` (timestamptz, default now), and a
      descending index on `deployed_at`
- [ ] 1.2 Add the `ingest_run` table to `src/cli/templates/init.sql` — append-only, with
      `started_at`, `completed_at`, `status`, `documents_embedded`, `documents_failed`,
      `documents_pending`, `chunk_count`, `config_snapshot` (jsonb), and a descending index
      on `completed_at`
- [ ] 1.3 Mirror both tables into `tests/smoke/init-test.sql` so the smoke schema keeps
      parity with `init.sql`
- [ ] 1.4 Add the migration file under `src/cli/templates/migrations/` creating both tables
      idempotently (`CREATE TABLE IF NOT EXISTS`), following
      `add_documents_last_modified.sql`

## 2. Ingest-configuration snapshot builder (pure, tested first)

- [ ] 2.1 Write `tests/unit/test_ingest_config_snapshot.py` first and watch it fail —
      cover every flag (HTML-to-Markdown, categorization, chunking strategy, embedding
      model and dimensions, chunk size, chunk overlap, distance metric, sitemap floor),
      each flag's default when the key is absent, and an empty configuration
- [ ] 2.2 Implement the builder so the tests pass — a pure function from the data-manager
      configuration mapping to the flag mapping, reading no environment and opening no
      connection (design D5)
- [ ] 2.3 Add drift-comparison tests: no drift for equal snapshots, one differing flag,
      several differing flags, and a key present on one side only
- [ ] 2.4 Implement the drift comparison returning, per differing flag, its current value
      and its value at ingest (design D6)

## 3. Deployment record — write path

- [ ] 3.1 Export the provenance values `ensure_config` already computes from
      `deploy/scripts/lib.sh` as `ARCHI_CONFIG_REF`, `ARCHI_CONFIG_SHA`,
      `ARCHI_CONFIG_HEAD`, `ARCHI_CONFIG_PIN_MATCHED` and `ARCHI_CONFIG_DIRTY_PATHS`
      (design D3), leaving the existing provenance log lines unchanged
- [ ] 3.2 Pass those five variables through to the `config-seed` service in
      `src/cli/templates/base-compose.yaml`
- [ ] 3.3 Write `tests/unit/test_deployment_record.py` first and watch it fail — cover
      reading the five variables from a supplied environment mapping, the absent and empty
      cases, and `pin_matched` parsing from the shell's `yes`/`no`
- [ ] 3.4 Implement the record builder and its insert, called from
      `src/cli/tools/config_seed.py`, reading `APP_VERSION` from the same environment
- [ ] 3.5 Wrap the write so a failure logs and the deploy continues (design D9), with a
      test that asserts a raising write does not propagate
- [ ] 3.6 Extend `bash deploy/scripts/test_ensure_config.sh` to assert the five variables
      are exported with the values the provenance log reports

## 4. Ingest run — write path

- [ ] 4.1 Write `tests/unit/test_ingest_run_record.py` first and watch it fail — cover a
      run that changed documents, a run that found the store already up to date, the
      counts, and the snapshot being carried
- [ ] 4.2 Record the run start when `update_vectorstore()` begins and write the record when
      it finishes, covering both the "up to date" and the "updated" branches with a status
      that distinguishes them (design D4)
- [ ] 4.3 Populate the counts from the document catalogue and the chunk count, and attach
      the snapshot built by the task-2 function from `self._data_manager_config`
- [ ] 4.4 Wrap the write so a failure logs and the ingest still reports success (design D9),
      with a test that asserts a raising write does not fail the run

## 5. Status-board read path (helpers, tested first)

- [ ] 5.1 Write `tests/unit/test_status_provenance.py` first and watch it fail — cover the
      matched state, the off-pin state, the on-pin-with-dirty-paths state, an absent
      deployment record, an absent ingest run, and a raising query
- [ ] 5.2 Implement the provenance reader returning a view model for the template: the
      deployment panel fields, the knowledge-base fields, the live-edited warning state,
      and the drift list from task 2.4
- [ ] 5.3 Implement the ingest window and duration formatting with tests, including a run
      still in progress and a run with no completion time
- [ ] 5.4 Assert the unavailable path: an absent or unreadable record yields an explicit
      unavailable view model rather than raising or returning zeros

## 6. Status-board render

- [ ] 6.1 Call the reader from `status_board()` in
      `src/interfaces/chat_app/service_alerts.py` and pass the view model to the template,
      keeping the handler a thin call site
- [ ] 6.2 Add the Deployment and Knowledge base sections to
      `src/interfaces/chat_app/templates/status.html` above Active Alerts, reusing the
      existing `.ssb-section-title` and card styling
- [ ] 6.3 Render both warning states — live-edited config, and configuration drift listing
      each differing flag with its value now and its value at ingest
- [ ] 6.4 Confirm the Active Alerts, Expired Alerts and Post New Alert sections are
      unchanged, and that the page renders when both records are absent

## 7. Gate and review

- [ ] 7.1 Run `bash scripts/gate.sh` and confirm it exits 0 with patch coverage at or above
      80 percent
- [ ] 7.2 Run `/codex:adversarial-review` and address the findings before opening the pull
      request
- [ ] 7.3 Open the pull request with `gh pr create --repo fasrc/archi --base dev`, with no
      `Co-Authored-By` trailer
