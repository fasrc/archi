## Context

Anchors re-verified on `origin/dev` `db701852` (2026-10-07):

- `src/cli/tools/config_seed.py:32-54` `resolve_config_path`; wrong sentence at `:40-42`;
  fallback `:51-53` (`sorted(glob.glob(...))` → `candidates[0]`).
- `src/cli/tools/config_seed.py:96` `services_config=services` — the whole block is seeded.
- `src/cli/tools/config_seed.py:129-140` `seed_entry`: resolve → load → `PostgresServiceFactory.from_env`
  → `seed` → `record_deployment`. `main()` at `:124` calls it; the `__main__` block turns
  any `Exception` into exit 1.
- `src/utils/benchmark_provenance.py:80` `ARM_OVERRIDE_PATHS`, `:94-98`
  `DEPLOY_REWRITTEN_PATHS`, `:107-109` `DIVERGENCE_IGNORED_PATHS`, `:239`
  `asserted_config_divergence(selected, running, *, ignore_paths=...)`. The module imports
  only the standard library, so it imports cleanly in the config-seed container (which
  already imports `src.utils.*`).
- `src/archi/pipelines/agents/fasrc_docs_agent.py:250-251` docstring; read at `:256`.
- `tests/unit/test_config_seed_resolve.py` — 4 tests, 4 passed at `db701852`. Its module
  docstring repeats the "harmless" claim.

## Decisions

**D1 — Reuse `asserted_config_divergence`, both directions.** For each candidate after the
first: `sorted(set(asserted(first, other)) | set(asserted(other, first)))`. Each call reports
only keys its first argument asserts, so the union covers a key present in only one file.
Do not use `config_divergence` (it has no ignore list). Do not write a local walker.

**D2 — Leaf semantics are inherited, not redefined.** `None` matches an empty container
and an absent key (`_leaves_equal`), because every consumer reads with `.get(key)`. So
`foo: null` vs no `foo` is NOT a difference; `foo: true` vs no `foo` IS one. `0` vs `False`
is a difference. Tests pin the "key in only one file with a real value" case only.

**D3 — Compare the whole file, not only `services`.** The seeder writes `data_manager`,
`global`, `archi` and `mcp_servers` too, and the agent reads them from Postgres. The ignore
list is exactly `DIVERGENCE_IGNORED_PATHS` (imported, never copied). An ignored path skips
its whole subtree (the walker's `prefix in ignore` check).

**D4 — Return shape.** `arm_config_divergence(paths: list[str]) -> dict[str, list[str]]`
maps each differing file's path to its sorted dotted paths. Files that agree are absent
from the dict. Fewer than two paths → `{}`. An empty YAML file loads as `None`, which the
walker treats as `{}`.

**D5 — Where the check runs.** Split the fallback decision out of `resolve_config_path`
without changing its return value: add `fallback_candidates(config_path) -> list[str]`
that returns `[]` when `config_path` is an existing file, else the sorted `*.yaml` list of
the directory (the same directory rule as today). `resolve_config_path` uses it (first
candidate, else the original path), so its 4 tests stay green unchanged. `seed_entry`
calls `fallback_candidates` once; when it has two or more entries it calls
`arm_config_divergence` on them **before** `load_config` of the seed file and before
`PostgresServiceFactory.from_env`. A present `config.yaml` → no check at all.

**D6 — How it fails.** On any divergence, print to stderr one header line naming the
reference file (`candidates[0]`) and, per differing file, one line with the file name and
its comma-joined dotted paths, plus one sentence: the agent reads the seeded `services`
block, so arms must agree outside `services.benchmarking`, the deploy-rewritten paths and
`name`; run separate deployments to A/B such a setting. Then `sys.exit(1)`. Use
`SystemExit`, not an exception, so the message is not followed by a traceback from the
`__main__` handler (`SystemExit` is not an `Exception` subclass and passes through).

**D7 — Docstrings.** `resolve_config_path`: the seeder seeds from the first file, and
`seed_entry` refuses the deployment when arms disagree outside the ignored paths, because
the agent reads the seeded configuration for everything else. `fasrc_docs_agent.py`: the
flag is read from the seeded configuration, so it cannot vary per arm inside one
multi-config deployment; to A/B it, run separate deployments (or re-seed) per value.

## Risks

- A sweep that varies a non-`services.benchmarking` setting now fails at deploy. Accepted
  by the operator (#523 body, "Decision").
- `src/cli/tools/config_seed.py` is black-clean at `db701852`, so the edit does not reflow
  unrelated lines; patch coverage comes from the new test file.
