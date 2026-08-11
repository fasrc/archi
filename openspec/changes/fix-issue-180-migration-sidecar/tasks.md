## 1. Rendered compose: the `db-migrate` sidecar

- [x] 1.1 Write a failing test in `tests/unit/test_migration_sidecar_render.py` that renders `base-compose.yaml` with Postgres enabled (follow the rendering precedent in `tests/unit/test_dev_mode_compose_render.py`), parses the YAML, and asserts a `db-migrate` service exists with `restart: "no"` and `depends_on.postgres.condition == "service_healthy"`. Watch it fail.
- [x] 1.2 Add the `db-migrate` service to `src/cli/templates/base-compose.yaml` inside the existing `{% if postgres_enabled %}` block, mirroring the `config-seed` block at `:119–146`: build from `archi_code/cli/templates/dockerfiles/Dockerfile-postgres`, `container_name: {{ name }}-db-migrate`, the same `PGHOST`/`PGPORT`/`PGDATABASE`/`PGUSER`/`PG_PASSWORD` environment as `config-seed`, `env_file: .env`, `volumes: - ./migrations:/migrations:ro`, `restart: "no"`, and the `{%- if host_mode %}network_mode: host{%- endif %}` guard. Command: loop over `/migrations/*.sql` in lexicographic order running `psql -v ON_ERROR_STOP=1 -f` on each. Make 1.1 pass.
- [x] 1.3 Extend the test to assert ordering: `config-seed` and the data-manager service each list `db-migrate` under `depends_on` with `condition: service_completed_successfully`. Watch it fail.
- [x] 1.4 Add those two `depends_on` edges (`config-seed` at `:127–129`, data-manager at `:35–39`). Make 1.3 pass.
- [x] 1.5 Add a test asserting that rendering with Postgres **disabled** produces no `db-migrate` service, and make it pass (it should already, via the existing `{% if postgres_enabled %}` guard — confirm the placement is inside it).

## 2. Ship the migration files with the deployment

- [x] 2.1 Write a failing test asserting `templates_manager` copies `src/cli/templates/migrations/` into the rendered deployment directory, with the same `.sql` file set, and that a second render into the same directory succeeds and leaves the same set. Watch it fail.
- [x] 2.2 Add the copy step to `src/cli/managers/templates_manager.py` next to the `init.sql` write at `:595–629`, using a module-level constant alongside `BASE_INIT_SQL_TEMPLATE` (`:60`). Make 2.1 pass.

## 3. Make every migration re-runnable

- [x] 3.1 Write a failing test that reads every file in `src/cli/templates/migrations/` and asserts no bare `ALTER TABLE ... RENAME COLUMN` remains — each rename must be guarded (an `information_schema.columns` presence check). Watch it fail on `rename_mid_to_message_id.sql`.
- [x] 3.2 Wrap all six renames in `rename_mid_to_message_id.sql` (`feedback.mid`, `timing.mid`, and the three `ab_comparisons.*_mid`, plus the already-guarded `ALTER INDEX`) in `DO $$ ... END $$` blocks conditioned on the old column existing AND the new column not existing, per design Decision 3. Make 3.1 pass.
- [x] 3.3 Add a test that applies every migration file, in lexicographic order, against a schema shaped like the current `init.sql` and asserts each exits successfully with the schema unchanged — so a future non-idempotent migration is caught by the gate rather than by a broken deploy. Prefer an in-process SQL-shape assertion over a live Postgres; if no fixture exists, assert every statement is either idempotent-by-syntax (`IF EXISTS` / `IF NOT EXISTS`) or guarded by a `DO` block.

## 4. Catalog startup schema precondition

- [x] 4.1 Write a failing test in `tests/unit/` asserting that `PostgresCatalogService` initialization raises `RuntimeError` naming `last_modified` when the mocked `documents` table lacks that column (mock the connection the way `tests/unit/test_catalog_postgres_upsert_last_modified.py` does). Watch it fail.
- [x] 4.2 Add `_REQUIRED_DOCUMENT_COLUMNS` as a module-level frozenset next to `_METADATA_COLUMN_MAP` / `_NON_TEXT_COLUMNS` in `src/data_manager/collectors/utils/catalog_postgres.py`, holding the 18 columns named in the `upsert_resource` INSERT (`:225–241`). Add a check in `refresh()` (`:147`) that queries `information_schema.columns` for `documents`, set-differences against the frozenset, and raises `RuntimeError` naming the sorted missing columns. Make 4.1 pass.
- [x] 4.3 Add tests for the two remaining scenarios: a complete schema initializes without raising, and the verification query is issued once during initialization rather than once per `upsert_resource` call.
- [x] 4.4 Add a test asserting `_REQUIRED_DOCUMENT_COLUMNS` matches the column list in the INSERT statement, so the constant cannot drift from the query it guards.

## 5. Verify and ship

- [x] 5.1 Run `bash scripts/gate.sh` from the worktree root and confirm it exits 0 with ≥80% diff coverage. Fix anything red — never bypass.
- [ ] 5.2 Confirm `black --check` still passes on both edited Python files (they were clean at `origin/dev`; a reflow would swamp the diff).
- [ ] 5.3 Push the branch and open a PR into `fasrc/archi:dev` whose body contains `Closes #180`, and note in it that `rename_mid_to_message_id.sql` was made idempotent because the issue's premise that it already was is false — a fresh deploy would otherwise have failed to start.
