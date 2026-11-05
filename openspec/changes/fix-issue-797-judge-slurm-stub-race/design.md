## Context

The judge job `scripts/benchmarking/slurm/archi_judge.sbatch` (on `origin/dev` `5f3d80e9`)
starts vLLM in the background at `:342-343` and polls it at `:345-355`:

```bash
while :; do
  kill -0 "$VLLM_PID" 2>/dev/null || die "vLLM stopped before it was ready (see the job log)"
  CODE="$("${PROBE[@]}" 2>/dev/null || true)"
  [ "$CODE" = 200 ] && break
  ...
done
```

`PROBE` is `curl ... --unix-socket "$sock" .../v1/models` (`:231`). The trap `cleanup`
(`:98-106`) kills `VLLM_PID` and removes the socket folder on every exit.

`tests/unit/test_judge_slurm.py` replaces `apptainer` and `curl` with stubs on `PATH`. The
`vllm serve` branch of the `apptainer` stub (`:101-117`) records `sockdir.mode` (`:104`) and
`vllm.pid` (`:105`), edits `judge.env` when `$STUB/edit-env` exists (`:106-109`), replaces the
scorer image and sidecar when `$STUB/rebuild` exists (`:110-116`), and then runs
`exec sleep 300` (`:117`). The `curl` stub (`:127-132`) answers `200` when its call count is at
least `curl.ready_after` (default `0`). The tests pass `JUDGE_READY_POLL_SECONDS=0` (`:454`).

Four tests read a side effect of the background stub:

| Test | Reads | Needs |
|---|---|---|
| `test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm` (`:475`) | `sockdir.mode` (`:487`), `vllm.pid` (`:498`) | `:104-105` |
| `test_a_server_that_never_gets_ready_fails_the_job_and_is_stopped` (`:516`) | `vllm.pid` (`:523`) | `:105` |
| `test_a_rebuild_during_the_job_keeps_the_pinned_pair` (`:558`) | `_image_digest(tmp_path) != digest` | `:110-116` |
| `test_a_judge_env_change_during_the_job_does_not_reach_the_scorer` (`:665`) | `JUDGE_PRECISION=fp8` in `judge.env` | `:106-109` |

## Goals / Non-Goals

**Goals:** the four tests pass under any host load; a test proves that the sync point exists.

**Non-Goals:**
- Any change to `scripts/benchmarking/slurm/archi_judge.sbatch`. The job is correct.
- Any change to an existing assertion. `int((stub / "curl.count").read_text()) == 3`
  (`:492`) stays.
- The time-window assertion in `test_the_job_hands_the_scorer_a_slurm_snapshot`
  (`7190 < deadline - time.time() <= 7200`, `:627`). It is a different shape. See D4.

## Decisions

### D1. A marker file is the sync point

The `vllm serve` stub writes `$STUB/vllm.ready` as its last step, after the `edit-env` and
`rebuild` branches and just before `exec sleep 300`. The marker means "every side effect of
this stub is on disk". A marker written at the start of the branch would not cover the later
side effects.

### D2. The curl stub blocks; it does not answer 503

The `curl` stub waits for the marker before anything else, then runs as now. It does not
answer `503` while it waits:

- A `503` adds a probe, so `curl.count` changes and the assertion `== 3` at `:492` fails.
- With `JUDGE_READY_TIMEOUT=0` (`test_a_server_that_never_gets_ready_...`), one `503` ends the
  job before the stub runs, which is the race again.

The wait comes before the `curl.calls`, `curl.header` and `curl.count` writes, so every
recorded value is the same as before.

### D3. The wait has a bound, and then the probe fails closed

The loop is `while [ ! -e "$STUB/vllm.ready" ] && [ "$i" -lt "$ticks" ]; do sleep 0.01; ...; done`,
with `ticks` from `$STUB/curl.wait_ticks` (default `1000`). It counts steps, not seconds: under
load each `sleep 0.01` takes longer, so the bound gets longer, which is the safe direction.

If the marker is still absent after the bound, the stub prints `000` and exits 7 before it
records the call, as `curl` does when nothing listens on the socket. The job then probes
again (`JUDGE_READY_POLL_SECONDS=0`), so no probe answers `200` before the marker. A future
test whose server never writes the marker does not hang: it fails on the `_run` timeout
(60 s).

Review round 1 (2026-11-05) changed this. The first version continued as before after the
bound and answered `200`, so a server stub slower than about 10 s let the job score and stop
the server before its side effects: the same race. The new test
`test_a_server_slower_than_the_probe_wait_is_not_ready_before_its_edits` sets
`curl.wait_ticks` to `1` and a 0.5 s `serve.delay` in the `vllm serve` stub. With the first
version it failed on its `JUDGE_PRECISION=fp8` assertion; with the `000` line it passes.

Every probe in the job follows `"${SERVE[@]}" &`, and no other script under
`scripts/benchmarking/slurm/` calls `curl` (`grep -ln curl scripts/benchmarking/slurm/*`
prints only `archi_judge.sbatch`). So no current test reaches the bound: the file takes about
4.5 s with the fix and about 4.2 s without it.

### D4. One new assertion pins the marker

A later edit that removes the marker line does not make any existing test fail: each probe
waits 1000 steps and then continues, so the job tests get about 10 s slower each. Loop 1
measured it: with the marker line removed, the file took 169.8 s. Apart from the new
assertion below, only `test_the_job_hands_the_scorer_a_slurm_snapshot` failed, on its 10 s
time window, which does not name the cause. So `test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm`
gets one more assertion, after the `curl.count` assertion:

```python
    # The server stub did all its side effects before the job stopped it (#797).
    assert (stub / "vllm.ready").exists()
```

With the marker line removed, that test failed with `AssertionError: assert False`. Since
review round 1 (D3), it fails earlier: no probe answers `200`, and `_run` stops the job after
60 s with `subprocess.TimeoutExpired` (measured 2026-11-05). The assertion stays: it still
names the race if a later edit removes the wait.

The snapshot test's 10 s window is unchanged by this fix in normal runs. With the fix, the job
also waits for the stub's start, which takes milliseconds. Making that window independent of
load is out of scope (#797 does not name it).

### D5. Prove the race and the fix with a delay in a throwaway copy

A race fix has no natural red step. The proof (`tasks.md` Appendix B) copies `origin/dev`
into `/tmp/797-proof/` with `git archive` and runs the test file there, so the checkout never
holds a delay. It runs five stages; Loop 1 ran it on `origin/dev` `5f3d80e9` with the
Appendix A patch applied, in 2 min 26 s:

| Stage | Test file | Edit | Result |
|---|---|---|---|
| `red` | `origin/dev` | `sleep 3` before `:108` and `:112` | `2 failed, 54 passed` |
| `red-top` | `origin/dev` | `sleep 3` before `prev=""` (`:102`) | `4 failed, 52 passed` |
| `green` | working tree | as `red` | `56 passed` |
| `green-top` | working tree | as `red-top` | `56 passed` |
| `marker` | working tree | marker line removed, `-k test_a_job_serves_on_a_private_socket_...` | `1 failed, 55 deselected` |

The `red` failures are `test_a_rebuild_during_the_job_keeps_the_pinned_pair` and
`test_a_judge_env_change_during_the_job_does_not_reach_the_scorer`. The `red-top` failures add
`test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm` and
`test_a_server_that_never_gets_ready_fails_the_job_and_is_stopped`, each with a
`FileNotFoundError` on `sockdir.mode` or `vllm.pid`.

A copy of the fix with the marker but without the wait, and the `red` delay, also gave
`2 failed, 54 passed`: the wait, not the marker, removes the race.

Without a delay, the fixed file gave `56 passed` in 20 of 20 runs on the host (swap 100 %
full).

Review round 1 (2026-11-05) ran the `red` and `red-top` delays on the new file (57 tests):
`57 passed` for each.

### D6. Ship the fix as an exact patch

Appendix A of `tasks.md` is a `git apply -p1` patch. Loop 1 checked that it applies to
`origin/dev` `5f3d80e9` and that the result is byte-identical to the file that the proof ran.
`black` 24.10.0 and `isort` 6.0.1 accept the result unchanged.

## Risks / Trade-offs

- **A future test that never starts the server fails on the 60 s `_run` timeout.** Accepted:
  that test is wrong, and a loud failure is better than a pass that hides the race. D3 says
  why no current test does this. An edit that removes the marker line costs about 60 s for
  each job test before it fails.
- **The proof depends on timing.** A 3 s delay against a job that ends in well under 1 s gave
  the same result in every Loop-1 run. If a `red` stage passes on a slow host, the delay was
  too short for that host; it is not evidence against the fix.
