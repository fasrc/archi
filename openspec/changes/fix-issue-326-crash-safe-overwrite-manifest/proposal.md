# Keep the on-disk QA-eval manifest valid across the overwrite window, and keep the staged live checks until their replacement is sealed

## Why

`run(overwrite=True)` and `score(overwrite=True)` prune the manifest **before** they lower
its status, so each writes an on-disk `manifest.json` that `RunManifest.from_dict` refuses.
The invalid document then stays on disk for the whole attempts or scoring phase — hours, on a
real run.

The prune in `run()` is `src/evaluation/qa/workflow.py:352-364` at `origin/dev` `3de206bc`:

```python
if overwrite:
    owned = RUN_FILES | SCORE_FILES
    if authorize_staged_invalid:
        owned = owned - {"live_checks.jsonl"}
    self._remove_owned(run_dir, owned)
    manifest["phases"].pop("run", None)
    manifest["phases"].pop("score", None)
    manifest.pop("attempts", None)
    manifest.pop("agent", None)
    manifest.pop("attention_required", None)
    for name in RUN_FILES | SCORE_FILES:
        manifest["artifacts"].pop(name, None)
```

`manifest["status"]` is never touched, so the document written at `:380` still claims the
prior terminal status while the evidence for it has been removed. Measured on this branch's
base (`3de206bc`), applying that exact prune to a `qa-v2` manifest and calling
`RunManifest.from_dict`:

```
run(overwrite=True) prune
  status='scored'             -> ValueError: manifest phase state is incomplete
  status='run_completed'      -> ValueError: manifest phase state is incomplete
  status='attention_required' -> ValueError: manifest attempts must be a positive integer
  same three, status demoted to 'prepared'      -> ACCEPTED

score(overwrite=True) prune
  status='scored'                               -> ValueError: manifest phase state is incomplete
  status='scored' demoted to 'run_completed'    -> ACCEPTED
```

Three independent rules in `src/evaluation/qa/schema.py` fire: the required-phases table
(`:258-267`), the `attempts` check (`:269-273`), and the qa-v2 live-check rule (`:300-311`).
Which one you hit depends only on which terminal status the run was in.

The staged live-check evidence is destroyed in the same window.
`staged_checks_path.unlink()` at `:414` removes the paused run's only copy of
`live_checks.jsonl` after it has been read into the authorized-item set and **before** the
replacement is written at `:463` (pause path) or `:565` (continue-through path). The prune
has already dropped that file's digest from `manifest["artifacts"]`, and `:380` has already
persisted the pruned manifest, so after a crash in the window the next continue fails its own
precondition: `required_inputs` adds `live_checks.jsonl` when `authorize_staged_invalid`
(`:308-309`) and `verify_hashes` (`:318`) needs both the file and its recorded digest. Neither
exists.

Trigger: the worker dies mid-window (redeploy, OOM, SIGKILL) or Ctrl-C on a CLI `--overwrite`
re-run. The console's continue always passes `overwrite=True`
(`src/evaluation/qa/worker.py:109-113`), so an operator reaches this path with one click.

Consequence: `run`, `score`, `retry_plan`, and history detail all raise on the leftover
manifest, so the workspace and its paid-for LLM artifacts need hand-editing to recover. During
a healthy continue, history lists the in-flight run as `status: "invalid"` — the fallback row
at `src/evaluation/qa/history.py:614`, reached because `_load_manifest` (`:106-117`) calls
`from_dict`. This blocks safe console activation (fasrc/archi#320), which exposes continue to
operators. Fixes fasrc/archi#326.

Upstream's design, not ours. `src/evaluation/qa/workflow.py` is a `port-hunks` file from
`archi-physics/archi` pin `bebfbe56` (`openspec/changes/archive/port-live-eval-trial/disposition.md`),
and the defect was found in post-merge review of PR #305 (merge `4314ac4b`).

## What Changes

- **The prune lowers the status it invalidates.** `run(overwrite=True)` sets
  `manifest["status"] = "prepared"`; `score(overwrite=True)` sets `"run_completed"`. Each is
  exactly the status the surviving phase set proves, so the document written at `:380` and
  `:803` is the same shape a first run writes at that point. `prepared` is the only choice for
  `run()`: the prune pops `attempts`, and every other status requires it.
- **The staged live checks are kept, not unlinked.** The `unlink()` at `:414` is removed
  rather than replaced with a sidecar, because `AtomicJsonlWriter`
  (`src/evaluation/qa/artifacts.py:94-129`) already writes a temp file in the same directory
  and commits with `os.replace`. The old `live_checks.jsonl` therefore survives untouched
  until the instant its replacement is committed — rename-until-sealed, with no new file name
  and no recovery path to write. Nothing between `:414` and the seal reads that path
  (verified: the only in-window reader is the staging copy `pre-run.jsonl`).
- **The retained file keeps its digest.** On the `authorize_staged_invalid` path the prune
  stops popping `live_checks.jsonl` from `manifest["artifacts"]`, mirroring the
  `owned - {"live_checks.jsonl"}` exclusion three lines above it. Without this the file
  survives a crash but the continue still cannot verify it. This is what makes the invariant
  statable as one sentence: **the manifest's recorded digests and the files on disk agree at
  every instant, and the manifest is always a document `from_dict` accepts.**
- **`RunManifest.from_dict` is not weakened.** No line of `schema.py` changes; the existing
  schema tests stand unmodified. The writer is the thing that was wrong.
- **Behaviour change, deliberately.** History and the console now show a mid-overwrite run as
  `prepared` (or `run_completed`) with its `runtime_phase` progress, instead of `invalid`.
  Progress display is unaffected either way: `console.py:78-86` reads `runtime_phase` from the
  raw dict and never consults `status`.

## Out of scope, deliberately

- **`_remove_owned(run_dir, owned)` at `:355` on a non-continue `--overwrite`.** That path
  deletes `live_checks.jsonl` before its replacement exists too, but discarding the prior
  run's artifacts is what a plain `--overwrite` means. The continue path already excludes the
  file from `owned`; this change protects the file the code was already trying to keep.
- **`prepare(overwrite=True)`'s full reset** (`:217`, `OWNED_FILES`). Same reasoning, larger
  blast radius, no staged evidence in play.
- **The in-memory manifest passed to `write_report`** (`:864`), which carries
  `status: "scored"` before `phases["score"]` is re-added. Nothing persists it, so the
  on-disk invariant holds; the audit sweep records the finding rather than changing it.
- **Reporting the defect on `archi-physics/archi` PR #608** — the port's recorded upstream
  channel. That happens after this fix merges, not in this change.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `qa-evaluation-trial`: adds two requirements — the on-disk manifest is a valid document at
  every instant, and staged live-check evidence outlives the window that replaces it.
  Recorded as **ADDED** requirements, not MODIFIED: the seven requirements in
  `openspec/specs/qa-evaluation-trial/spec.md` are the port trial's own acceptance conditions
  (phases run, oracle resolves, console toggle, registry staging, port inventory, trial
  acceptance, RAGAS untouched). None of them states a durability or crash-safety property, so
  there is nothing to modify.

## Impact

- `src/evaluation/qa/workflow.py` — the `if overwrite:` block in `run()` (`:352-364`) gains a
  status assignment and one conditional artifact retention; the `unlink()` at `:414` is
  removed; the `if overwrite:` block in `score()` (`:796-800`) gains a status assignment.
  `schema.py`, `history.py`, `console.py`, `worker.py` and the terminal writes at `:502`,
  `:506`, `:600`, `:881` are **not** touched.
- `tests/unit/evaluation/qa/test_workflow.py` — a `write_json` recorder that round-trips every
  persisted `manifest.json` through `RunManifest.from_dict`, applied to both overwrite paths;
  an exception injected after the former unlink point asserting the staged evidence and its
  digest both survive.
- No change to the schema, the CLI surface, the console API, dependencies, or any deployment.
  No container rebuild.
