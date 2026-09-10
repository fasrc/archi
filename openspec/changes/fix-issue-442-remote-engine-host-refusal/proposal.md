# Record no host when the container engine is not provably local

## Why

`archi create` records the machine the **CLI process** runs on as the benchmark host.
`collect_host_information()` (`src/cli/managers/templates_manager.py:95`) reads
`socket.getfqdn()` and `/proc/cpuinfo` in that process. `get_git_information()` attaches
the result at `:159`, and the call site at `:805` writes it to `<base_dir>/git_info.yaml`.

Nothing between the capture and the deploy establishes that the engine is on that machine.
`DeploymentManager.start_deployment` (`src/cli/managers/deployment_manager.py:31`) runs
`{self.compose_tool} -f … up`, where `compose_tool` is `podman compose` or `docker compose`
(`:29`). Both tools honour an environment variable that points at another host. On
`origin/dev` at `c6112167`, `git grep -n "DOCKER_HOST\|CONTAINER_HOST\|DOCKER_CONTEXT" --
src docs deploy scripts tests` returns nothing, so no code guards this today.

The artifact states the claim without a hedge. `add_metadata`
(`src/bin/service_benchmark.py:475`) writes:

```
"host_captured_at": "deploy (`archi create`), on the machine this stack runs on"
                    " — a container cannot move hosts, so a --rerun ran here too"
```

Both halves hold only while the client and the engine are the same machine. With a remote
engine the field gives **false matches** (two arms deployed from one laptop to two engines
read as one host) and **false mismatches** (two arms deployed from two laptops to one
engine read as two hosts). `compare_runs.py` then presents a host mismatch as evidence
about the runs.

Issue #442 recorded the decision on 2026-09-09: archi does **not** support a remote
container engine. The fix enforces the invariant the artifact already asserts. It does not
weaken the claim, and it does not capture the engine's own host name — that option is
reserved for the day remote engines become a supported feature.

## What Changes

- A new module `src/utils/container_endpoint.py` answers one question:
  `container_endpoint_is_provably_local()`. It reads `DOCKER_HOST` and `CONTAINER_HOST`,
  resolves `DOCKER_CONTEXT` (or `currentContext` in `~/.docker/config.json`) to an
  endpoint, and classifies every endpoint **by URL scheme, never by presence**.
- `unix://`, `unix:/`, and a bare filesystem path count as local. `tcp://`, `ssh://`,
  `http://`, `https://`, and `npipe://` count as not provably local. An unknown scheme
  counts as not provably local.
- `collect_host_information()` (`src/cli/managers/templates_manager.py:95`) returns `None`
  before it samples anything when the endpoint is not provably local. `git_info.yaml` then
  carries `host: null`, and the artifact carries `metadata.host: null`.
- The refusal becomes a **fourth** named cause in the null-host text.
  `_MD_HOST_NULL` (`src/utils/generate_benchmark_report.py:77`) and `_HTML_HOST_NULL`
  (`:86`) name three causes today. The refusal is not a failure, and the operator needs the
  difference. The `_HOST_NOT_RECORDED` sentinel comment at `:60-74` says "THREE" and is
  corrected in the same edit.
- The check is best-effort and never raises. An unreadable `~/.docker/config.json` means
  "no evidence of a remote engine", not an abort. This keeps the existing clause "Capture
  SHALL never raise and SHALL never fail a deploy".
- `host_captured_at` prose stays exactly as written. Enforcement is what makes the existing
  claim true.
- The SHALL clause at
  `openspec/changes/fix-issue-433-benchmark-host-provenance/specs/benchmark-run-provenance/spec.md:5`
  is amended in the same change, so the spec and the code keep agreeing. That change
  directory is still unarchived on `dev` at `c6112167`.

## Capabilities

### New Capabilities

- `benchmark-run-provenance`: no directory of that name exists under `openspec/specs/` at
  `c6112167`. The capability lives only in the unarchived change
  `fix-issue-433-benchmark-host-provenance`. This change therefore states its requirements
  as **ADDED**, never as MODIFIED — a MODIFIED delta against a capability that the spec
  tree does not hold fails `openspec validate --strict`.

### Modified Capabilities

None.

## Impact

- `src/utils/container_endpoint.py` — new module, the whole classification.
- `src/cli/managers/templates_manager.py` — one import and one guard clause in
  `collect_host_information()`.
- `src/utils/generate_benchmark_report.py` — the fourth cause in two constants and the
  sentinel comment.
- `openspec/changes/fix-issue-433-benchmark-host-provenance/specs/benchmark-run-provenance/spec.md`
  — the amended SHALL clause and one new scenario.
- `docs/docs/interpreting_benchmark_results.md` — one sentence at `:749-751`, where the doc
  states that the recorded host is the host every run in the deployment used.
- New tests: `tests/unit/test_container_endpoint.py`. Extensions to
  `tests/unit/test_benchmark_host_provenance.py`,
  `tests/unit/test_benchmark_report_markdown.py`, and
  `tests/unit/test_benchmark_report_html_provenance.py`.
- Both production files that are edited in place are black-clean under the gate's pinned
  black 24.10.0, measured on 2026-09-10 at `c6112167`, so an in-place edit reformats
  nothing around it and `diff-cover` scores only the new lines.
- **Not** in scope: capturing the engine's own host name, teaching archi to support a
  remote engine, any `docker`/`podman` subprocess call at deploy time, and any backfill of
  the artifacts already in `bench_out/`.
