# Design - fix-issue-326-crash-safe-overwrite-manifest

Line anchors are as of `origin/dev` at `7145888d` (re-verified 2026-10-21, and on `e2978a1d` 2026-10-22). Re-derive before
citing any of them.

The operator decided D1–D3 on 2026-09-27 and D0 on 2026-10-19. This design records them and
the traps an implementation must avoid; it does not reopen them.

## The shape of the defect

A manifest write is a publish. Everything else in this workspace already knows that:
`AtomicJsonlWriter` commits with `os.replace`, `write_summary` streams to a temp file, the
digest map is rewritten in the same statement as the artifacts it describes. The overwrite
paths break the rule in two places — they delete before a step that can refuse, and they
publish a document mid-edit.

| Stage | Location in `run()` | State of the on-disk manifest |
|---|---|---|
| delete owned files | `workflow.py:362` | old manifest, digests of deleted files |
| prune phases, attempts, agent, provenance, digests | `:363-373` | in memory only |
| rewrite the resolved agent inputs | `:374-377` | (unchanged) |
| start guard + first reading (can raise) | `:385` | (unchanged) — **a refusal strands it** |
| set `runtime_phase` | `:387-391` | in memory only |
| **publish** | `:392` | **`from_dict` refuses it** |
| pause path re-adds phase + digests | `:478-513` | in memory only |
| publish | `:514` | valid again |
| continue-through path re-adds | `:590-619` | in memory only |
| publish | `:620` | valid again |

The window between `:392` and `:514`/`:620` is the attempts phase: every live pre-check, every
agent execution, every post-run check. `score()` has the same shape between `:823` and `:908`,
spanning the scoring phase.

Two more facts make this worse than a cosmetic status lie. First, the pruned digest map no
longer records `live_checks.jsonl`, so the manifest and the disk disagree even about which
files are real. Second, `:426` deletes the file the continue path deliberately kept. A crash in
the window therefore loses the evidence *and* the record of it.

## D0 — Run the start guard before the delete, but merge its readings after the prune

`start_readings` (`provenance.py:78`) is the #570 start guard plus the first corpus reading. It
raises `CollectionNotReadyError` by design and can raise a Postgres connect error. Today it is
called at `:385`, after `_remove_owned` at `:362` has deleted the run and score files, so a
refusal leaves the old manifest on disk naming digests of files that no longer exist.

The fix moves the **call** above the `if overwrite:` block, directly after
`config = redact_agent_config(config)` at `:357` (the guard must see the same redacted config
it sees today), and keeps the **merge** where it is:

```python
config = redact_agent_config(config)
readings = corpus_provenance.start_readings(config, spec)   # may raise; nothing touched yet
if overwrite:
    ...prune (pops PROVENANCE_KEYS and TAG_KEYS)...
write_yaml(...)
...
manifest.update(readings)                                    # where :385 is today
```

**The trap.** `start_readings` returns `retrieval_identity` and `corpus_fingerprint_before`,
both members of `corpus_provenance.PROVENANCE_KEYS`, and the prune pops every one of those keys
(`:368-369`). Moving the whole `manifest.update(corpus_provenance.start_readings(...))` line
above the block makes the prune erase the fresh readings on every overwrite. Nothing would
raise: `end_readings` (`:598-605`) would read `corpus_fingerprint_before` and
`retrieval_identity` as `None`, and the run would publish `corpus_unchanged_at_endpoints: None`
and a lost identity. Task 1 pins this with a test that fails on the literal move.

`load_agent_inputs` is already above the block (`:350-354`), so it needs no move. Nothing else
between `:357` and `:385` can refuse: `LazyVectorstore` (`src/evaluation/qa/runtime.py:345`)
only stores its config, and the rest is local file writes and hashing.

The `start_readings` docstring says "no attempt has run, so nothing is lost". That is false on
today's overwrite path and becomes true only because of the move, so it is corrected to state
the contract the callers must keep: run the guard before you delete or write anything.

**The red test must use a completed run, not a paused one.** On a paused run with
`authorize_staged_invalid=True`, the only owned files that get deleted are the two resolved
agent inputs, and `:374-375` rewrites them byte-for-byte before the guard raises. A "nothing
changed on disk" assertion would then pass against the unfixed code. A completed (`scored`)
run with a plain `overwrite=True` deletes `answers.jsonl` and the score files, which are not
rewritten, so the red is real.

## D1 — Lower the status, do not relax the validator

The issue's constraint is explicit: do not loosen `RunManifest.from_dict`. That is also the
right call on the merits — the validator's rules are each true statements about the pruned
document. `phases["run"]` really is gone; `attempts` really is absent; `live_checks.jsonl`
really is not in the digest map. The document is not misjudged. It is wrong.

`RunStatus` has exactly four values (`schema.py:14-18`) and `prepared` is the only one that is not terminal. The
required-phases table maps it to `("prepare",)` — precisely the phase the prune leaves
standing — and both the qa-v2 live-check rule (`schema.py:300-310`) and the scored-artifact
rule (`:311-314`) exclude it. `attempts` is optional for `prepared` alone, which settles the
choice: the prune pops `attempts`, so no other status can accept the result.

Measured on `3de206bc`, applying the prune to a `qa-v2` manifest:

| prior status | as written today | status demoted to `prepared` |
|---|---|---|
| `scored` | `manifest phase state is incomplete` | ACCEPTED |
| `run_completed` | `manifest phase state is incomplete` | ACCEPTED |
| `attention_required` | `manifest attempts must be a positive integer` | ACCEPTED |

`score()`'s prune drops only the score phase and `SCORE_FILES`, so `run_completed` is the
status its survivors prove: prepare and run stay `completed`, and `live_checks.jsonl` is a
`RUN_FILE` it never touches. Measured: `scored` → `manifest phase state is incomplete`;
demoted to `run_completed` → ACCEPTED.

The demotion is not a new state to reason about. It makes the overwrite path converge on the
state a first run is already in at that line — `prepare` complete, nothing downstream claimed.
The terminal assignments at `:512`, `:617` and `:889` are unchanged and still publish the real
outcome.

## D2 — Delete the unlink; do not add a sidecar

The obvious fix for `:426` is to rename the staged file aside and delete it once the
replacement seals. It is also unnecessary, and the unnecessary version is expensive.

`AtomicJsonlWriter.__exit__` (`artifacts.py:113-129`) commits with
`os.replace(self._temp_name, self.path)`, and `__enter__` creates the temp file with
`tempfile.mkstemp(dir=str(self.path.parent))` — the same directory, so the rename is atomic on
one filesystem. The old `live_checks.jsonl` is therefore already protected until the exact
instant the new one becomes visible. Removing the `unlink()` line is the whole fix. There is no
window in which neither file exists.

Nothing in between reads the stale copy. Inside `run()`, `live_checks.jsonl` appears at `:406`
(the path binding, read once at `:416-420` before the former unlink), `:475` and `:577` (the
two seals), and `:492` (a digest recomputation after the first seal). Every in-window read is of
`pre-run.jsonl` in the staging `TemporaryDirectory`.

A sidecar would have cost three things this does not: a new file name to add to `OWNED_FILES`
(and so to the "artifacts already exist; use `--overwrite`" gates at `:195`/`:948`, changing an
unrelated refusal), a restore step at `run()` entry ahead of `verify_hashes`, and a rule for
what `prepare(overwrite=True)`'s full reset at `:219-220` does with a leftover sidecar — which
is how stale evidence from an abandoned run gets resurrected into a freshly prepared workspace.
Deleting one line has none of those questions.

A `TemporaryDirectory` is not a candidate either: it is cleaned up on the way out of the
`with` block, including when an exception unwinds it. Evidence moved there would be deleted by
exactly the failure it is meant to survive.

## D3 — Keep the digest for the file you keep

`:359-361` already says the continue path must not delete `live_checks.jsonl`:

```python
owned = RUN_FILES | SCORE_FILES
if authorize_staged_invalid:
    owned = owned - {"live_checks.jsonl"}
```

Twelve lines later the digest loop (`:372-373`) pops that same name from
`manifest["artifacts"]` unconditionally. The two statements contradict each other, and `:392`
publishes the contradiction. The smallest edit is to pop `owned` instead of
`RUN_FILES | SCORE_FILES`, so the digests removed are exactly the files removed.

**The agent-input digests (post-PR review, 2026-10-22).** Popping `owned` also pops the
digests of `agent_config.resolved.yaml` and `agent_spec.resolved.md`, which the prune
rewrites at once (`:374-377`) but records again only at the pause or completion publish. The
resume check at `:318-322` verifies those two files only when their digest is present. Once
D2 and D3 make a later continue possible, that continue would skip the check and re-seal an
edit made after the failure. So on the same flag, `run()` records `config_hash` and
`spec_hash` in `manifest["artifacts"]` right after it computes them, before the first
publish (tasks.md group 4a). An extra entry is not a validity risk (see below).

D2 alone leaves the file on disk and the record of it gone, which is not recoverable: the
continue precondition needs both. `required_inputs` adds `live_checks.jsonl` when
`authorize_staged_invalid` (`:311-312`), and `verify_hashes(run_dir, manifest["artifacts"],
required_inputs)` at `:323-327` reads the digest out of the map.

**Where the retained digest goes stale (known gap 1, accepted).** The file does not change
until a seal. On the pause path the seal at `:475` is followed by the recomputation at
`:486-495` and the publish at `:514`, with no refusable call between them; a kill there still
leaves the old digest next to the new file, the same shape as the window below. On the
continue-through path the seal at `:577-589` is followed by `end_readings` (`:598-605`, a
network call), then the recomputation at `:606`, then the publish at `:620`. A kill after the
seal and before `:620` leaves the old digest next to the new file: a hash mismatch instead of a
missing digest. The operator accepted this gap on 2026-10-19; the proposal records it and does
not fix it.

An extra entry in the digest map is not a validity risk. `from_dict` checks the shape of every
entry (`schema.py:277-288`) and requires certain names for certain statuses; it never forbids
a name.

## D4 — What the tests have to pin, and why the obvious test is too weak

Asserting on the manifest left behind after a run is not the property. The property is about
*every* document that was ever on disk, and a successful run ends valid today.

So the test wraps the publish itself: monkeypatch `write_json` in the `workflow` module's
namespace, and for each call whose path is `manifest.json`, deep-copy the payload, then run
every copy through `RunManifest.from_dict`. A deep copy is required — the workflow keeps
mutating the same dict, so a stored reference would be re-read at its final value and the test
would pass against the unfixed code.

**Fixtures.** There is no paused-run fixture and no `observe_live_item` fake in
`tests/unit/evaluation/qa/test_workflow.py`. The paused-run helper is `_paused_at_live_gate`
(`tests/unit/evaluation/qa/test_live_workflow.py:177`); the completed-run helper is `_run`
(`:158`); both need the `runtimes` fixture (`:135`). Put the new tests in a new module,
`tests/unit/evaluation/qa/test_overwrite_crash_safety.py`, and import those helpers from
`tests.unit.evaluation.qa.test_live_workflow` (precedent: `test_jobs_history_wait_budget.py:12`
imports a sibling test module). A new module avoids reused test names and appended-test
collisions in the two large existing files.

**Paths.** The recorder runs on four overwrite paths, because each publishes a different
document: the continue (`authorize_staged_invalid=True`, prior status `attention_required`),
the plain `--overwrite` re-run (`src/cli/qa_eval.py:222-230`, no authorize flag, prior status
`scored`), the continue that pauses again (the only overwrite path that publishes `:514` and
returns `attention_required`; it needs a dataset with two live items), and the re-score. A
test on the continue path alone passes a D1 line wrapped in `if authorize_staged_invalid:`.

**Injection.** Inject the mid-window failure with
`monkeypatch.setattr(workflow_module, "observe_live_item", raising_fake)` — the name is
imported into the workflow module at `workflow.py:38` and called at `:440` (pre-run) and
`:584` (post-run). Raise on the first call, which is the pre-run check: a failure after
`answers.jsonl` is sealed hits known gap 3 (an undeclared artifact) and shows the `invalid`
history row for a reason this change does not touch. Do **not** raise from the MCP invoker:
`live_checks.py` catches that error and turns it into a check row, so the run would not unwind.
For the same reason, an exhausted `SequenceInvoker` does not raise in the test: its
`IndexError` becomes an `oracle_failed` check row, so every test passes enough values.

**History row.** Select the row by `EvaluationHistory.id_for_path(run_dir)`. The `invalid`
fallback row is named after the directory and a valid row after the run, so a selector by
name finds a row before the fix and none after it.

The evidence assertion is two-part, matching D3: the file is on disk with its original bytes
**and** its digest is in the manifest that was last published. Two follow-on assertions cover
the spec scenarios that had no test: removing the raising fake and calling
`run(overwrite=True, authorize_staged_invalid=True)` again passes `verify_hashes` (it pauses or
completes instead of raising `workspace artifact ...`), and `EvaluationHistory(runs_dir).
list_runs()` shows the interrupted run under a real status, not the `invalid` row
(`history.py:614`).

The counter-test matters as much. A run whose live checks were never staged
(`authorize_staged_invalid` false) must still have `live_checks.jsonl` removed by `:362`,
because a plain `--overwrite` means discard. Without that case, "never delete the staged
checks" is an easy over-fix that silently changes what `--overwrite` means.

## D5 — Scope fences

Stop and reduce the change if you find yourself editing any of these:

- `src/evaluation/qa/schema.py` — the issue forbids it, and D1 explains why it is right.
- The terminal publishes at `:514`, `:518`, `:620`, `:908` and the status assignments at
  `:512`, `:617`, `:889` — they are already correct.
- `_remove_owned` (`:86-100`) and `OWNED_FILES` — D2 exists to avoid touching them.
- `history.py`, `console.py` and `evaluations.js` — they are readers. History's `invalid` row
  (`:614`) is a symptom that disappears when the writer stops publishing invalid documents;
  the row itself stays, for manifests that really are corrupt.
- `prepare()` (`:181-286`) — out of scope, recorded in the proposal.
- `provenance.py` beyond the `start_readings` docstring — the guard's behaviour is #570's and
  is correct.
- The `end_readings` window and the delete-to-publish window — recorded as known gaps, not
  fixed.

## D6 — The audit sweep, and what it is allowed to conclude

The issue asks for every `write_json(run_dir / "manifest.json", ...)` site in `run()` and
`score()` to be checked against the invariant. That is `:392`, `:514`, `:518`, `:620`, `:823`,
`:908`. Two are fixed here (`:392`, `:823`); the other four publish a manifest whose phase set
and digests were just re-added, or (`:518`) one that `:392` already made valid, so they are
valid by construction. The regression test in task 5 proves it for `:285`, `:392`, `:518`,
`:620`, `:823` and `:908` and for a first-run pause at `:514`; the task 3 re-pause test proves
it for `:514` on the overwrite path.

One finding the sweep should record rather than fix: `:889` sets `status = "scored"` in memory
and `write_report` at `:891-896` receives that manifest before `phases["score"]` exists at
`:897`. Nothing writes it to disk, so the on-disk invariant holds. If `write_report` is ever
changed to validate its argument, that becomes a real defect — worth a note in the PR body,
not a change here.
