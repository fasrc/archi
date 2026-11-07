## Why

An operator who opens the running deployment cannot tell what is deployed. The service
status board at `/ssb/status` shows service alerts and nothing else, so the deployed
`archi-config` pin, the deploy time, the last knowledge-base ingest, and the ingest flags
that produced the current corpus are all invisible from the application. Today the only
way to answer "is what I am looking at actually deployed" is to read deploy output on the
host.

The data needed to answer it either already exists and is never surfaced, or is computed
at deploy and then discarded. `ensure_config` already derives the config `HEAD`, the pin,
the match verdict and the dirty paths (`deploy/scripts/lib.sh:295-303`), but it only logs
them. The `documents` table already carries `ingested_at`, `indexed_at` and
`ingestion_status`, but nothing reads them for display.

## What Changes

- **A Deployment panel on `/ssb/status`** showing the deployed config pin
  (`CONFIG_REF` + `CONFIG_SHA`), the config `HEAD` that actually deployed, the match
  verdict, the application version, and the deploy timestamp.
- **A Knowledge base panel on `/ssb/status`** showing the last ingest window, document
  counts by ingestion status, the chunk count, and the ingest configuration.
- **Deploy provenance becomes persistent.** `config_seed` writes one deployment-record
  row per deploy carrying `config_ref`, `config_sha`, `config_head`, `pin_matched`,
  `dirty_paths`, `app_version` and `deployed_at`. `ensure_config` exports the values it
  already computes so the seed can store them.
- **Ingest runs record the configuration that produced them.** When an ingest run
  finishes, the data manager writes an ingest-run row holding the run window, the
  resulting counts, the status, and a snapshot of the ingest-affecting configuration.
- **Two warning states**, both required:
  - *Live-edited config* — the deployed `HEAD` carries tracked edits, or does not match
    the pin. `ensure_config` permits the live-edit deploy, which is exactly the case
    where the pin constant alone misleads the reader.
  - *Config changed after this ingest* — the current ingest configuration differs from
    the snapshot taken at the last completed ingest. The board names each differing flag
    with its value now and its value at ingest.
- **Two new tables** in `src/cli/templates/init.sql` and `tests/smoke/init-test.sql`,
  plus a migration, because preserved data volumes mean an existing database never
  re-runs `init.sql`.

No existing behaviour is removed. The Active Alerts, Expired Alerts and Post New Alert
sections stay exactly as they are.

## Capabilities

### New Capabilities

- `deployment-status-board`: what `/ssb/status` displays about the deployed stack and the
  knowledge base, including both warning states and how the page behaves when a record is
  missing.
- `ingest-run-provenance`: the ingest-run record — when a run is recorded, what
  configuration snapshot it carries, and the guarantee that the snapshot describes the
  run that produced the corpus rather than current configuration.

### Modified Capabilities

- `deploy-config-provisioning`: the "Config provenance is recorded" requirement currently
  stops at the deploy output. It changes so that provenance is also persisted where the
  running deployment can read it, which is what makes the pin visible in the application.

## Impact

**Schema** — `src/cli/templates/init.sql`, `tests/smoke/init-test.sql`, and a new file
under `src/cli/templates/migrations/` (precedent: `add_documents_last_modified.sql`).

**Deploy path** — `deploy/scripts/lib.sh` (`ensure_config` exports its provenance values),
`src/cli/tools/config_seed.py` (148 lines; `seed()` at line 55 writes the record).

**Ingest path** — `src/data_manager/vectorstore/manager.py` around the completion point at
line 314, writing the run record and the configuration snapshot.

**Chat app** — `src/interfaces/chat_app/service_alerts.py` (`status_board()` at line 104)
and `src/interfaces/chat_app/templates/status.html`. Both are thinly covered by unit
tests, so all logic lands in new separately-tested helper modules following the
`src/interfaces/chat_app/config_fingerprint.py` + `tests/unit/test_config_fingerprint.py`
precedent, and the route and template stay thin call sites.

**Not affected** — the chat pipeline, retrieval, the `/v1` endpoint, and the alert tables.
`static_config.created_at` is deliberately not used as the deploy time: the upsert at
`src/utils/config_service.py:405-440` omits it from its `ON CONFLICT DO UPDATE` list, so
it records the first ever seed on that volume rather than the latest deploy.
