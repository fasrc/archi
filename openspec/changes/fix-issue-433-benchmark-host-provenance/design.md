# Design — the host as run provenance

## Context

A benchmark artifact is read months after the run that wrote it. Everything a reader needs
to judge the run must be inside the file. Every other run-identity field already obeys that
rule: `code_version` digests the image's `src` package, `config_version` digests the
settings, `corpus_fingerprint` digests the documents. The machine is the one factor the
campaign plan pins (`docs/docs/proposals/feature-matrix-campaign-2026.md` §2) that the file
does not record.

The capture channel already exists and needs no new plumbing. `archi create` runs on the
host, `get_git_information()` (`src/cli/managers/templates_manager.py:79`) builds a dict,
the call site at `:750-756` writes it to `<base_dir>/git_info.yaml`, and the harness reads
that file at `EXTRA_METADATA_PATH = "/root/archi/git_info.yaml"`
(`src/bin/service_benchmark.py:66`, read at `:449-455`). The host block rides that channel.

## Goals / Non-Goals

**Goals**

- An artifact states which machine produced it, or states honestly that it does not know.
- A reader can tell the three cases apart: the artifact predates the field, the deploy
  predates the field or capture failed, and the host is recorded.
- `compare_runs.py` shows the reader a cross-host comparison instead of hiding it.

**Non-Goals**

- A refusal rule. Making a host difference *visible* is this change. Making it *void a
  verdict* changes the campaign plan and the pre-registration, and that is an operator
  decision.
- The embedder device. `KEY_SETTING_PATHS` records `embedding_name` only, so a change from
  `cpu` to `cuda` stays invisible. That is a second defect in the same family, and it needs
  its own issue.
- A backfill of `bench_out/`. Those runs' hosts are unrecoverable.

## Decisions

### Capture inside the container is worthless, so capture at deploy

A hostname read inside the benchmark container returns the container id, which is different
on every run. The host must be read where `archi create` runs, which is the host itself.

### Deploy-time capture is correct for a host, although it is wrong for a commit

`docs/docs/interpreting_benchmark_results.md:701-712` warns that `git_info.last_commit`
freezes at deploy and so misreports the code a later run executed. A reader who has learned
that lesson will distrust any deploy-time field. The host does not share the defect:
containers cannot move to another machine, so a `--rerun` against an existing stack runs on
the host that deployed it. The artifact and the docs must both say so, or the field gets
discounted for the wrong reason.

### Three states, three texts, and never a placeholder

- Key **absent** — the artifact predates the field.
- **`null`** — the deploy predates the field, or capture failed.
- **Object** — the host.

An empty string or the word `"unknown"` collapses two of those facts into one, which is the
exact failure the `_INGEST_NOT_RECORDED` sentinel (`src/utils/generate_benchmark_report.py:58`)
was added to prevent. This change copies that pattern rather than inventing a second one.

### The lift happens before the metadata literal is built

`add_metadata` assigns `"git_info": additional_info`. That stores a **reference** to the
loaded dict, not a copy. A `pop("host")` performed after the literal is built would remove
the key from the dict the literal already points at, and the result would look correct by
accident while depending on evaluation order. The helper pops the block first and then
builds the literal, so "one copy" holds by construction. The pop guards on
`isinstance(additional_info, dict)`, because `yaml.safe_load` of an empty file returns
`None` and the existing `OSError` branch sets `additional_info = None` too.

### A helper does the capture, so tests do not shell out to git

`get_git_information` runs two `git` subprocesses. Testing the host block through it would
couple every host test to a git checkout. A module-level `collect_host_information()` holds
the capture, and `get_git_information` calls it. The tests then pin the capture directly and
pin the one line that attaches it.

### Capture never raises, per field

`socket.getfqdn()` performs a reverse DNS lookup and can fail. `/proc/cpuinfo` does not
exist on a machine that is not Linux. Each read sits in its own `try`, and a failure gives
`None` for that field only. A deploy must never fail over provenance, and a machine that
hides its processor model must still report its hostname. The processor model falls back to
`platform.processor()` when `/proc/cpuinfo` yields nothing.

### The renderers' early return widens

Both `format_version_html` (`:222`) and `format_version_markdown` (`:1001`) return `""` when
`not code and not config`. A host recorded on an artifact that carries no version digests
would disappear through that guard. The guard widens to return `""` only when the host is
the absent-key sentinel as well. An artifact that records nothing still renders nothing.

### `provenance_rows` stays a pure row builder

The text renderer at `scripts/benchmarking/compare_runs.py:1703-1710` reads only `field` and
`values` from each row, and the report dict at `:2110` has no channel for a warning. Adding
a `note` key to a row would need renderer surgery and would change a shared contract for one
caller. Instead a separate `host_mismatch_note(arms)` returns the warning or `None`, the
report dict gains a `host_mismatch` key, and the renderer prints that one line under the
provenance table. The note is then testable on its own, without building a whole report.

### The row shows the hostname, because that is the identity

`cpu_model` explains *why* two hosts differ; `hostname` is *which* host it was. The
provenance row keys on the hostname and shows the processor model beside it. The mismatch
note fires on distinct recorded hostnames only. An arm that recorded no host is unknowable,
not mismatched — the same reading `corpus_gate` already gives an unrecorded fingerprint at
`scripts/benchmarking/compare_runs.py:486-487`, which splits `unrecorded` (`value is None`)
from `distinct` (`value is not None`). The file's `_recorded()` helper at `:170` is the
existing idiom for that filter.

## Risks / Trade-offs

- **`socket.getfqdn()` can be slow.** It performs a reverse lookup, and on a host with a bad
  resolver it can block for seconds. It runs once per `archi create`, which already takes
  minutes, so the cost is not observable. The alternative, `socket.gethostname()`, returns a
  short name that two clusters can share, which defeats the purpose.
- **A hostname is weak evidence of identical hardware.** One name can be reassigned to new
  silicon. `cpu_model` beside it is what makes the pair useful, and neither field is claimed
  to be a proof — the requirement is that the comparison becomes *checkable*, not that it
  becomes automatic.
- **A note that never refuses can be ignored.** That is deliberate. A refusal is out of
  scope, and shipping a visible note now is worth more than shipping nothing while the
  refusal rule waits for an operator decision.
