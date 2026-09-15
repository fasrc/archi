# Design — benchmark bank preflight

## Context

`archi evaluate` deploys an isolated benchmark stack, the data-manager re-ingests the
corpus (~50 min), then `benchmarking-<name>` answers + scores each question. Bank schema
is validated in `Benchmarker._process_config` (`src/bin/service_benchmark.py`) **per item,
during grading** — the last step. A bank/mode mismatch therefore costs a full ingest before
surfacing as all-`nan` aggregates.

The schema contract already lives in one pure, ragas-free module,
`src/utils/benchmark_schema.py`:
- `normalize_bank` maps the legacy authoring dialect onto the modern ragas schema.
- `required_fields_for_modes(benchmarking_configs)` → `["user_input"]` (+`"sources"` when
  `SOURCES` ∈ `modes`).
- `metric_required_column(metric)` → the column a RAGAS metric additionally needs
  (`reference` for the context metrics; `None` for the answer metrics).

## Goals / non-goals

- **Goal:** fail before the ingest, with a message that names the offending items and the
  missing fields; reuse the existing schema functions so the check cannot drift.
- **Goal:** a standalone validator usable while authoring a bank (sub-second, no deploy).
- **Non-goal:** changing what the modes require, or migrating the banks. This is a guard.

## Decisions

### 1. Validation logic lives in `benchmark_schema.py`, not the CLI
The pure module is already the single source of truth and is unit-testable without the
benchmark-only ragas dependency. Adding `validate_bank` / `bank_eligibility_warnings` /
`preflight_bank_file` there keeps the check co-located with `required_fields_for_modes` (so
a change to one is visible next to the other) and fully diff-coverable. The CLI and the
standalone script are thin callers.

- `validate_bank(bank, benchmarking_configs) -> List[str]`: normalize, then for each item
  assert it is a dict containing every field in `required_fields_for_modes`. Returns a list
  of `"item[i]: missing [...] (has [...])"` strings; empty ⇒ valid. Pure, no I/O.
- `bank_eligibility_warnings(bank, benchmarking_configs) -> List[str]`: only when `RAGAS` ∈
  modes; for each enabled metric with a required column, count rows whose column is empty
  and warn with the scored denominator. Non-fatal.
- `preflight_bank_file(queries_path, benchmarking_configs) -> Tuple[List[str], List[str]]`:
  read JSON at `queries_path`, `normalize_bank`, return `(validate_bank(...),
  bank_eligibility_warnings(...))`. A missing/unparseable/non-list file is returned as a
  single hard error (not an exception) so callers uniformly branch on `errors`.

### 2. Inject the preflight at the pre-deploy chokepoint in `evaluate`
In `src/cli/cli_main.py::evaluate`, immediately after
`benchmarking_configs = config_manager.get_interface_config("benchmarking")` and **before**
`ServiceBuilder.build_compose_config` / `VolumeManager.create_required_volumes` /
`prepare_deployment_files`. At that point both `modes` and `queries_path` are resolved and
nothing expensive has run. Call `preflight_bank_file(queries_path, benchmarking_configs)`;
`logger.warning` each warning; if `errors`, `raise click.ClickException("\n".join(...))`.

`--config-dir` sweeps share one `queries_path` (enforced elsewhere as shared-context
drift), so validating the resolved benchmarking config once is sufficient.

### 3. Standalone script + bench.sh guard
`scripts/benchmarking/validate_queries.py`: argparse (`-c` config, optional `-q` override),
load config YAML → `services.benchmarking`, call `preflight_bank_file`, print
errors/warnings, `sys.exit(1)` on errors. `bench.sh` gains a precondition line that runs it
and aborts on non-zero. The script stays thin; a unit test drives its `main()` so its lines
are covered.

### 4. CI drift guard
`tests/unit/fixtures/` holds a committed **canonical modern-dialect bank** (with `sources`)
that MUST pass `validate_bank` under `modes: [RAGAS, SOURCES]`. If a future harness change
alters `required_fields_for_modes` such that the canonical shape no longer validates, this
test fails — catching the exact class of drift that broke the last run, despite the real
banks being git-ignored.

## Coverage strategy (gate: diff-cover ≥ 80% patch)

The substantive, testable logic (the three `benchmark_schema.py` functions + the script
`main()`) is exercised by `tests/unit/test_benchmark_bank_preflight.py`. The only untested
diff lines are the ~4-line thin call site in `cli_main.py::evaluate` (not imported by unit
tests, per the project's app.py pattern) — a small fraction of the patch, keeping aggregate
patch coverage well above 80%.

## Risks

- **False fail on a valid bank** → operators can't run. Mitigated: `validate_bank` only
  checks key presence for `required_fields_for_modes` (exactly the harness rule), after the
  same `normalize_bank`; heavily unit-tested against modern + legacy + sources-present banks.
- **Preflight reads a different path than the container** → the container mounts the bank
  from the same resolved `queries_path` (`archi evaluate` copies it into the deploy dir), so
  validating `queries_path` on the host matches what the benchmarker loads.
