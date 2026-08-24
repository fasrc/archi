# Design - fix-issue-326-crash-safe-overwrite-manifest

Line anchors are as of `origin/dev` at `3de206bc`. Re-derive before citing any of them.

## The shape of the defect

A manifest write is a publish. Everything else in this workspace already knows that:
`AtomicJsonlWriter` commits with `os.replace`, `write_summary` streams to a temp file, the
digest map is rewritten in the same statement as the artifacts it describes. The overwrite
paths break the rule in one place — they publish a document mid-edit.

| Stage | Location | State of the on-disk manifest |
|---|---|---|
| prune phases, attempts, agent, digests | `workflow.py:352-364` | in memory only |
| set `runtime_phase` | `:375-379` | in memory only |
| **publish** | `:380` | **`from_dict` refuses it** |
| pause path re-adds phase + digests | `:474-501` | in memory only |
| publish | `:506` | valid again |
| continue-through path re-adds | `:586-597` | in memory only |
| publish | `:600` | valid again |

The window between `:380` and `:506`/`:600` is the attempts phase: every live pre-check, every
agent execution, every post-run check. `score()` has the same shape between `:803` and `:881`,
spanning the scoring phase.

Two facts make this worse than a cosmetic status lie. First, the pruned digest map no longer
records `live_checks.jsonl`, so the manifest and the disk disagree even about which files are
real. Second, `:414` deletes the file the continue path deliberately kept. A crash in the
window therefore loses the evidence *and* the record of it.

## D1 — Lower the status, do not relax the validator

The issue's constraint is explicit: do not loosen `RunManifest.from_dict`. That is also the
right call on the merits — the validator's three rules are each true statements about the
pruned document. `phases["run"]` really is gone; `attempts` really is absent;
`live_checks.jsonl` really is not in the digest map. The document is not misjudged. It is
wrong.

`RunStatus` has exactly four values (`schema.py:14-18`) and `prepared` is the only one that is
not terminal. The required-phases table maps it to `("prepare",)` — precisely the phase the
prune leaves standing — and both the qa-v2 live-check rule (`:300-311`) and the scored-artifact
rule (`:312-315`) exclude it. `attempts` is optional for `prepared` alone (`:269-273`), which
settles the choice: the prune pops `attempts`, so no other status can accept the result.

Measured on `3de206bc`, applying the `:352-364` prune to a `qa-v2` manifest:

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
The terminal assignments at `:500`, `:597` and `:862` are unchanged and still publish the real
outcome.

## D2 — Delete the unlink; do not add a sidecar

The obvious fix for `:414` is to rename the staged file aside and delete it once the
replacement seals. It is also unnecessary, and the unnecessary version is expensive.

`AtomicJsonlWriter.__exit__` (`artifacts.py:113-129`) commits with
`os.replace(self._temp_name, self.path)`, and `__enter__` puts the temp file in
`self.path.parent` — the same directory, so the rename is atomic on one filesystem. The old
`live_checks.jsonl` is therefore already protected until the exact instant the new one becomes
visible. Removing the `unlink()` line is the whole fix. There is no window in which neither
file exists.

Nothing in between reads the stale copy. Inside `run()`, `live_checks.jsonl` appears at `:394`
(the path binding), `:463` and `:565` (the two seals), and `:480` (a digest recomputation after
the first seal). Every in-window read is of `pre-run.jsonl` in the staging `TemporaryDirectory`.

A sidecar would have cost three things this does not: a new file name to add to `OWNED_FILES`
(and so to the "artifacts already exist; use `--overwrite`" gate at `:192`/`:921`, changing an
unrelated refusal), a restore step at `run()` entry ahead of `verify_hashes`, and a rule for
what `prepare(overwrite=True)`'s full reset at `:217` does with a leftover sidecar — which is
how stale evidence from an abandoned run gets resurrected into a freshly prepared workspace.
Deleting one line has none of those questions.

A `TemporaryDirectory` is not a candidate either: it is cleaned up on the way out of the
`with` block, including when an exception unwinds it. Evidence moved there would be deleted by
exactly the failure it is meant to survive.

## D3 — Keep the digest for the file you keep

`:355` already says the continue path must not delete `live_checks.jsonl`:

```python
owned = RUN_FILES | SCORE_FILES
if authorize_staged_invalid:
    owned = owned - {"live_checks.jsonl"}
```

Nine lines later the digest loop pops that same name from `manifest["artifacts"]`
unconditionally. The two statements contradict each other, and `:380` publishes the
contradiction.

D2 alone leaves the file on disk and the record of it gone, which is not recoverable: the
continue precondition needs both. `required_inputs` adds `live_checks.jsonl` when
`authorize_staged_invalid` (`:308-309`), and `verify_hashes(run_dir, manifest["artifacts"],
required_inputs)` at `:315-319` reads the digest out of the map. So the artifact retention has
to mirror the file retention, keyed off the same flag.

The retained digest stays honest for the whole window, because the file it describes does not
change until a seal — and each seal is followed by a digest recomputation (`:480`, `:586`)
before the next publish (`:506`, `:600`). Verified against the publish sites: the only
`write_json(run_dir / "manifest.json", ...)` calls are `:380`, `:502`, `:506`, `:600`, `:803`,
`:881`, and none of them falls between a seal and its recomputation.

An extra entry in the digest map is not a validity risk. `from_dict` checks the shape of every
entry (`:275-288`) and requires certain names to be present for certain statuses; it never
forbids a name.

## D4 — What the tests have to pin, and why the obvious test is too weak

Asserting on the manifest left behind after a run is not the property. The property is about
*every* document that was ever on disk, and a successful run ends valid today.

So the test wraps the publish itself: monkeypatch `write_json` in the `workflow` module's
namespace, and for each call whose path is `manifest.json`, deep-copy the payload, then run
every copy through `RunManifest.from_dict`. A deep copy is required — the workflow keeps
mutating the same dict, so a stored reference would be re-read at its final value and the test
would pass against the unfixed code.

The evidence test needs the exception injected after the former unlink point and before the
seal at `:463`/`:565`. `observe_live_item` is the first call after it and is already faked in
this suite, so raising from that fake is the narrowest injection available. The assertion is
two-part, matching D3: the file is on disk **and** its digest is in the manifest that was last
published.

The counter-test matters as much. A run whose live checks were never staged
(`authorize_staged_invalid` false) must still have `live_checks.jsonl` removed by `:356`,
because a plain `--overwrite` means discard. Without that case, "never delete the staged
checks" is an easy over-fix that silently changes what `--overwrite` means.

## D5 — Scope fences

Stop and reduce the change if you find yourself editing any of these:

- `src/evaluation/qa/schema.py` — the issue forbids it, and D1 explains why it is right.
- The terminal publishes at `:502`, `:506`, `:600`, `:881` and the status assignments at
  `:500`, `:597`, `:862` — they are already correct.
- `_remove_owned` (`:84-97`) and `OWNED_FILES` — D2 exists to avoid touching them.
- `history.py` and `console.py` — they are readers. History's `invalid` row (`:614`) is a
  symptom that disappears when the writer stops publishing invalid documents; the row itself
  stays, for manifests that really are corrupt.
- `prepare()` (`:192-217`) — out of scope, recorded in the proposal.

## D6 — The audit sweep, and what it is allowed to conclude

The issue asks for every `write_json(run_dir / "manifest.json", ...)` site to be checked
against the same invariant. That is `:380`, `:502`, `:506`, `:600`, `:803`, `:881`. Two are
fixed here; the other four publish a manifest whose phase set and digests were just re-added,
so they are valid by construction.

One finding the sweep should record rather than fix: `:862` sets `status = "scored"` in memory
and `write_report` at `:864-870` receives that manifest before `phases["score"]` exists at
`:870`. Nothing writes it to disk, so the on-disk invariant holds. If `write_report` is ever
changed to validate its argument, that becomes a real defect — worth a note in the PR body and
a follow-up issue, not a change here.
