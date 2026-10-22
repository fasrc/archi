# Keep the on-disk QA-eval manifest valid across the overwrite window, and keep the staged live checks until their replacement is sealed

## Why

`run(overwrite=True)` and `score(overwrite=True)` prune the manifest **before** they lower
its status, so each writes an on-disk `manifest.json` that `RunManifest.from_dict` refuses.
The invalid document then stays on disk for the whole attempts or scoring phase — hours, on a
real run. Since #570, `run()` also deletes the old run's files **before** its start guard
runs, so a refusal from the guard strands a manifest whose digests name deleted files.

Line anchors in this change are as of `origin/dev` at `7145888d` (re-verified 2026-10-21, and on `e2978a1d` 2026-10-22).
The prune in `run()` is `src/evaluation/qa/workflow.py:358-373`:

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
    for key in corpus_provenance.PROVENANCE_KEYS:
        manifest.pop(key, None)
    for key in corpus_provenance.TAG_KEYS:
        manifest.pop(key, None)
    for name in RUN_FILES | SCORE_FILES:
        manifest["artifacts"].pop(name, None)
```

`manifest["status"]` is never touched, so the document written at `:392` still claims the
prior terminal status while the evidence for it has been removed. Measured on an earlier base
(`3de206bc`; the prune's status logic is unchanged since), applying that prune to a `qa-v2`
manifest and calling `RunManifest.from_dict`:

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

Two independent rules in `src/evaluation/qa/schema.py` fire: the required-phases table (`:258-269`)
and the `attempts` check (`:271-275`). Which one you hit depends only on which terminal status the
run was in. The qa-v2 live-check rule (`:300-310`) is next in line but is never reached, because
every pruned status other than `prepared` fails one of the first two, and `prepared` is exempt
from it. Re-measured on `e2978a1d` (2026-10-22) with real `qa-v2` manifests: same table. Task 2
re-measures it on the branch, and task 5 pins it as a test.

**The start guard runs after the delete.** `manifest.update(corpus_provenance.start_readings(
config, spec))` is at `:385`, after `self._remove_owned(run_dir, owned)` at `:362`.
`start_readings` (`src/evaluation/qa/provenance.py:78`) raises `CollectionNotReadyError` by
design when the collection is empty, has no vectors, or uses a different model, and a Postgres
connect error can come from it too. A refusal there leaves the old manifest on disk with its
old terminal status and digests for files that were just deleted. `from_dict` accepts that
document, but `verify_hashes` (in `score()` and in the history detail view) then fails. The
`start_readings` docstring ("no attempt has run, so nothing is lost") is wrong on this path.

The staged live-check evidence is destroyed in the same window.
`staged_checks_path.unlink()` at `:426` removes the paused run's only copy of
`live_checks.jsonl` after it has been read into the authorized-item set and **before** the
replacement is written at `:475` (pause path) or `:577` (continue-through path). The prune
has already dropped that file's digest from `manifest["artifacts"]`, and `:392` has already
persisted the pruned manifest, so after a crash in the window the next continue fails its own
precondition: `required_inputs` adds `live_checks.jsonl` when `authorize_staged_invalid`
(`:311-312`) and `verify_hashes` (`:323-327`) needs both the file and its recorded digest.
Neither exists.

Trigger: the worker dies mid-window (redeploy, OOM, SIGKILL) or Ctrl-C on a CLI `--overwrite`
re-run, or the start guard refuses an overwrite. The console's continue always passes
`overwrite=True` (`src/evaluation/qa/worker.py:113`), so an operator reaches this path with one
click.

Consequence: `run`, `score`, `retry_plan`, and history detail all raise on the leftover
manifest, so the workspace and its paid-for LLM artifacts need hand-editing to recover. During
a healthy continue, history lists the in-flight run as `status: "invalid"` — the fallback row
at `src/evaluation/qa/history.py:614`, reached because `_load_manifest` (`:106`) calls
`from_dict`. This blocks safe console activation (fasrc/archi#320), which exposes continue to
operators. Fixes fasrc/archi#326.

Upstream's design, not ours. `src/evaluation/qa/workflow.py` is a `port-hunks` file from
`archi-physics/archi` pin `bebfbe56` (`openspec/changes/archive/port-live-eval-trial/disposition.md`),
and the defect was found in post-merge review of PR #305 (merge `4314ac4b`).

## What Changes

The operator decided D1–D3 on 2026-09-27 and D0 on 2026-10-19 (issue #326 body). They are
not reopened here.

- **D0 — the start guard runs before anything is deleted.** The `start_readings` **call**
  moves above the `if overwrite:` block in `run()`, after `config = redact_agent_config(config)`
  (`:357`): `readings = corpus_provenance.start_readings(config, spec)`. The
  `manifest.update(readings)` stays **below** the block, where the call is today. The split is
  required, not a style choice: `start_readings` returns `retrieval_identity` and
  `corpus_fingerprint_before`, and the prune pops every `PROVENANCE_KEYS` entry (`:368-369`), so
  a literal move of the whole line would erase the fresh readings on every overwrite and
  `end_readings` (`:599-605`) would compare against `None`. `load_agent_inputs` is already above
  the block (`:350-354`). The `start_readings` docstring is corrected to say that callers run
  it before they delete or write anything.
- **D1 — the prune lowers the status it invalidates.** `run(overwrite=True)` sets
  `manifest["status"] = "prepared"`; `score(overwrite=True)` sets `"run_completed"`. Each is
  exactly the status the surviving phase set proves, so the document written at `:392` and
  `:823` is the same shape a first run writes at that point. `prepared` is the only choice for
  `run()`: the prune pops `attempts`, and every other status requires it.
- **D2 — the staged live checks are kept, not unlinked.** The `unlink()` at `:426` is removed
  rather than replaced with a sidecar, because `AtomicJsonlWriter`
  (`src/evaluation/qa/artifacts.py:94-129`) already writes a temp file in the same directory
  and commits with `os.replace`. The old `live_checks.jsonl` therefore survives untouched
  until the instant its replacement is committed — rename-until-sealed, with no new file name
  and no recovery path to write. Nothing between `:426` and the seal reads that path (the only
  in-window reader is the staging copy `pre-run.jsonl`).
- **D3 — the retained file keeps its digest.** On the `authorize_staged_invalid` path the
  prune stops popping `live_checks.jsonl` from `manifest["artifacts"]`, mirroring the
  `owned - {"live_checks.jsonl"}` exclusion three lines above it (the simplest form is to pop
  `owned` instead of `RUN_FILES | SCORE_FILES`). Without this the file survives a crash but the
  continue still cannot verify it.
- **`RunManifest.from_dict` is not weakened.** No line of `schema.py` changes; the existing
  schema tests stand unmodified. The writer is the thing that was wrong.
- **Behaviour change, deliberately.** History and the console now show a mid-overwrite run as
  `prepared` (or `run_completed`) instead of `invalid`, until the phase seals its first output
  file (`answers.jsonl` in `run()`, `evaluation_results.jsonl` in `score()`). From that seal
  to the next manifest write, history still lists the run as `invalid` (known gap 3 below,
  which exists today on first runs too). `progress.status` changes with it:
  `console.py:82` copies `manifest["status"]` into `job["progress"]["status"]`. The display
  does not change while a phase runs, because `evaluations.js:62-64` shows
  `progress.runtime_phase` first and falls back to `progress.status` only when
  `runtime_phase` is absent.

## The invariant, and its known gaps

What this change makes true: **every `manifest.json` that `run()` and `score()` write is a
document `from_dict` accepts, a start-guard refusal on `run(overwrite=True)` deletes and writes
nothing, and the staged `live_checks.jsonl` plus its digest survive until the replacement is
sealed.** It does not make the on-disk state consistent at every instant. Windows of the
following shapes remain. All are recorded here and none is fixed:

1. **End-of-run provenance window (accepted by the operator, 2026-10-19).** After the new
   `live_checks.jsonl` is sealed (`:577-589`) and before the manifest write at `:620`, the
   end-of-run provenance re-check runs (`end_readings`, `:598-605`; #570, #647). The D3
   digest still describes the old file in that window, so a kill there turns a missing digest
   into a hash mismatch. The pause path has the same shape with no network call in it: after
   the seal at `:475` and before the write at `:514`, a kill leaves the old digest next to
   the new file.
2. **Delete-to-publish windows.** In `run()`, `_remove_owned` (`:362`) deletes files before
   the first manifest write (`:392`); in `score()`, `_remove_owned(run_dir, SCORE_FILES)`
   (`:817`) deletes the score files before the write at `:823`. A kill (not an exception)
   between them leaves the old manifest listing digests of deleted files. D0 takes every
   network call out of the `run()` window; what remains there is two local file writes and
   two hashes (`:374-377`), and in `score()` nothing but the delete itself. Closing them
   means a manifest write before the delete — the operator's option (b) of 2026-09-30, which
   D0 superseded — so it is out of scope here.
3. **Seal-to-publish windows (exist today, on first runs too).** After `answers.jsonl` is
   sealed (the writer at `:562`) and before the manifest write at `:620`, and after
   `evaluation_results.jsonl` is sealed (the writer at `:838`) and before `:908`, the sealed
   file is on disk but not in `manifest["artifacts"]`. History refuses such a run with
   `run contains undeclared artifact(s)` (`history.py:261-269`, reached from `list_runs`
   through `_latency_trend`, `:456-461`) and lists it as the `invalid` row. A failure in
   those windows, for example in the post-run live checks (`:584`), therefore still shows
   `invalid`. This is not a manifest that `from_dict` refuses and not a digest of a deleted
   file, so it is outside the issue's invariant. The spec scenario for a failed overwrite
   names the earlier window only.

**Known limitation, not fixed: recovery after a failed continue.** D2 and D3 make a later
continue pass its input verification at the workflow level. No shipped interface issues that
continue today: a failed continue leaves the job `failed` or `interrupted`
(`src/evaluation/qa/jobs.py:63-79`, `:270-286`), and both `console.continue_evaluation`
(`src/evaluation/qa/console.py:509-511`) and `jobs.continue_process` (`jobs.py:181-186`) refuse
unless the job is `attention_required`. The CLI `run` command has no authorize option
(`src/cli/qa_eval.py:206-233`), and `retry_plan` refuses a run that is not a complete scored
run (`src/evaluation/qa/workspace.py:111`). The operator recovers with a plain `run
--overwrite`, which discards the staged evidence, or by calling the workflow directly. The
issue does not put `console.py` or `jobs.py` in scope; a human decides on a follow-up.

## Out of scope, deliberately

- **`_remove_owned(run_dir, owned)` at `:362` on a non-continue `--overwrite`.** That path
  deletes `live_checks.jsonl` before its replacement exists too, but discarding the prior
  run's artifacts is what a plain `--overwrite` means. The continue path already excludes the
  file from `owned`; this change protects the file the code was already trying to keep.
- **`prepare(overwrite=True)`'s full reset** (`:219-220`, `OWNED_FILES`). Same reasoning,
  larger blast radius, no staged evidence in play.
- **The in-memory manifest passed to `write_report`** (`:891-896`), which carries
  `status: "scored"` (`:889`) before `phases["score"]` is re-added at `:897`. Nothing persists
  it, so the on-disk invariant holds; the audit sweep records the finding rather than changing
  it.
- **The known gaps and the known limitation above.** Recorded, not fixed.
- **A new `RunStatus`, or any change to `schema.py`.**
- **Reporting the defect upstream on `archi-physics/archi`** (the same defect exists on its
  `main`). After this merges, a human decides whether to open that issue.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `qa-evaluation-trial`: adds three requirements — the start guard refuses before the
  overwrite deletes anything, every on-disk manifest the workflow writes is a valid document,
  and staged live-check evidence outlives the window that replaces it. Recorded as **ADDED**
  requirements, not MODIFIED: seven of the eight requirements in
  `openspec/specs/qa-evaluation-trial/spec.md` are the port trial's own acceptance conditions
  (phases run, oracle resolves, console toggle, registry staging, port inventory, trial
  acceptance, RAGAS untouched), and the eighth, "QA runs record corpus provenance" (#570),
  says which readings a manifest records, not when the guard runs relative to the overwrite
  delete. None of them states a durability or crash-safety property, so there is nothing to
  modify, and the new D0 requirement adds an ordering rule without changing what #570 records.

## Impact

- `src/evaluation/qa/workflow.py` — in `run()`: the `start_readings` call moves above the
  `if overwrite:` block (its `manifest.update` stays below it); the block gains a status
  assignment and one conditional artifact retention; the `unlink()` at `:426` is removed. In
  `score()`: the `if overwrite:` block (`:816-820`) gains a status assignment. `schema.py`,
  `history.py`, `console.py`, `worker.py` and the terminal writes at `:514`, `:518`, `:620`,
  `:908` are **not** touched.
- `src/evaluation/qa/provenance.py` — the `start_readings` docstring only.
- Tests — a new module `tests/unit/evaluation/qa/test_overwrite_crash_safety.py` that reuses
  the paused-run and completed-run helpers of `test_live_workflow.py`: a guard-refusal test, a
  provenance-survives-the-overwrite test, a `write_json` recorder that round-trips every
  persisted `manifest.json` through `RunManifest.from_dict` on the continue, plain
  `--overwrite`, re-pause and re-score paths, an exception injected through
  `observe_live_item` after the former unlink point, a later continue that passes input
  verification, a history row that is not `invalid`, and a parametrized test that pins the
  validator's refusals of the pruned, non-demoted documents.
- No change to the schema, the CLI surface, the console API, dependencies, or any deployment.
  No container rebuild.
