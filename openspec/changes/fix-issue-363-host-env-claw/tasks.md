# Tasks — host.env overrides and the claw identity

Every checkbox is one loop turn and ends **green and committed**. Failing test first, watch
it fail for the right reason, smallest fix, `bash scripts/gate.sh`, commit. Never
`--no-verify`.

Standing notes for every task:

- Coverage: `scripts/gate.sh` runs `--cov=src`, so nothing under `deploy/**` reports to
  `diff-cover`, and black/isort do not touch shell. A green gate is necessary but carries no
  signal about this change — the shell self-tests are the evidence, run by hand
  (they are not wired into CI; verified against `origin/dev`).
- The gate runs in the `localhost/archi-loop:latest` container, not a login shell.
- Scope: change no default (`DEPLOYMENT` resolves to `dev`, `GPU_IDS` stays `${GPU_IDS-}`),
  add no `.gitignore` rule, run no deploy, touch nothing outside
  `deploy/fasrc-dev/scripts/`.

## 1. Host identity

- [x] 1.1 `model: opus` — RED: add `deploy/fasrc-dev/scripts/test_host_env.sh`, modeled on
      `test_gpu_flag.sh` (fake `archi` on `PATH`, temp `SCRIPT_DIR` fixture, TAP-ish
      `ok -`/`not ok -`, non-zero exit on failure), covering seven cases: defaults survive a
      missing `host.env`; `host.env` `DEPLOYMENT` reaches `archi create --name`; `CONFIG`
      honored; command-line env beats a `:=`-style `host.env`; a plain assignment beats the
      command line (documented tradeoff, pinned); `GPU_IDS=""` from `host.env` still passes
      no flag; missing `host.env` is not an error. Watch it fail because `lib.sh` never
      sources `host.env`. Then implement in `lib.sh`: the sourcing block after `REPO_ROOT`,
      and `DEPLOYMENT`/`CONFIG` as `${VAR:-default}`. All four self-tests green. Gate;
      commit.
- [x] 1.2 `model: sonnet` — Add `host.env.example` (tracked): the `: "${VAR:=value}"` idiom,
      the plain-assignment warning, both host blocks (`dev` reserved for the GPU host,
      `claw` for the no-GPU / no-local-vLLM workstation). Add the README "Per-host
      configuration" section. Verify `git check-ignore`: `host.env` ignored, the example
      not. Gate; commit.
- [x] 1.3 `model: haiku` — Correct the false premise in `lib.sh:21-29` and
      `test_gpu_flag.sh:8-13`; keep every default and the `${GPU_IDS-}` form.
      `grep -rn 'neither the nvidia container runtime' deploy/fasrc-dev/scripts/` returns
      nothing. Self-tests green. Gate; commit.

## 2. Close-out

- [x] 2.1 `model: sonnet` — Run all four shell self-tests and the issue's no-deploy verify
      snippet (fake `archi`, `DEPLOYMENT=claw`); record the observed output here.

      **Measured** (after the review-round fixes below): `test_host_env` 15 passed,
      0 failed;
      `test_gpu_flag` 3 passed; `test_ensure_config` 10 passed; `test_firewall` 8 passed.
      Verify snippet with a fake `archi` on `PATH`: `DEPLOYMENT=claw bash -c 'source
      deploy/fasrc-dev/scripts/lib.sh; …'` printed `DEPLOYMENT=claw
      CONFIG=deploy/fasrc-dev/config.yaml`. Nothing deployed; no container or volume
      touched.
- [ ] 2.2 `model: haiku` — Gate in the container on the finished branch; push
      `fix/issue-363-host-env-claw`; open the PR (`gh pr create --repo fasrc/archi --base
      dev`, `closes #363` in the body, Findings block from the pre-PR review). Do not merge.

## 3. Pre-PR adversarial review

Rounds recorded here because the loop runs before any PR exists to comment on. Surviving
findings become the PR body's Findings block.

- [x] 3.1 Round 1 — three findings; the first two **held** and overturned the issue's
      sketched design, the third held in part.
      `[high]` a sourced `host.env` could override every later `${VAR:-}` knob, including
      the config pin (`CONFIG_REF`/`CONFIG_SHA`) the tracked file exists to protect —
      **held**; fixed by parsing an explicit allowlist (`DEPLOYMENT`, `CONFIG`, `GPU_IDS`)
      instead of sourcing.
      `[high]` `source` executes arbitrary shell from a git-excluded file before
      `require_files` and before `nuke.sh`'s confirmation, on the production host —
      **held**; the parser executes nothing, and a non-assignment line aborts (canary test
      proves no execution).
      `[medium]` the self-test pinned only happy-path argv — **held in part**: negative
      cases added (unsupported key aborts; non-assignment aborts unexecuted; comments
      parse), and the command-line-wins case is now unconditional. Pushed back on
      entrypoint-level wrapper tests: the suite's established pattern (`test_gpu_flag`,
      `test_ensure_config`) stubs the deploy layer, and the parser removes the
      executed-code risk class those wrapper tests would have hunted.
      The redesign superseded tasks 1.1/1.2 as written: `host.env` is data (`KEY=VALUE`,
      allowlist, command line always wins), not a sourced file with a `:=` idiom. TDD held
      for the pivot: the rewritten test failed 3/9 against the sourcing implementation
      (cases 5, 7, 8), then passed 9/9 after the parser landed.
- [x] 3.2 Round 2 — three findings on the parser.
      `[medium]` CRLF endings poisoned values (`--name claw\r`) — **held**; each line now
      strips a trailing CR (RED case 10 caught the poisoned argv, GREEN after).
      `[medium]` the parser was stricter than the docs: indented comments and
      whitespace-only lines aborted — **held**; edge whitespace is trimmed before
      classification, docs and spec state it (RED cases 9/11, GREEN after).
      `[high]` a malformed host.env blocks `status.sh`/`nuke.sh`, not just deploys —
      **held on the docs, pushed back on the policy**: fail-closed is the point precisely
      for `nuke.sh` (an unparsable host.env makes identity ambiguous, and a teardown on an
      ambiguous identity destroys the wrong deployment; the error names the offending line
      of the operator's own file). Kept fail-closed; code comment, example, README, and
      spec now state the every-wrapper scope explicitly, and case 7 proves the abort lands
      before `archi` is ever invoked. Self-test now 11 cases.
- [x] 3.3 Round 3 — one finding, **held**, verified by running the shell semantics.
      `[high]` an ambient EMPTY `DEPLOYMENT=''`/`CONFIG=''` bypassed host.env (`+x` sees
      "set") and then fell through `:-` to the reserved `dev` — a claw host silently
      retargeting production's name, reachable from a stray profile export. Fixed: the
      identity keys treat empty as unset on both sides (`[ -n "${VAR:-}" ]`), while
      `GPU_IDS` keeps set-wins semantics because there empty is the documented explicit
      disable. RED cases 12/13 reproduced `--name dev` on a claw-pinned fixture, GREEN
      after. Self-test now 13 cases.
- [x] 3.4 Round 4 — two findings, both **held**.
      `[high]` an empty identity value IN host.env (`DEPLOYMENT=`) was accepted, assigned
      empty, and re-defaulted by `:-` to the reserved `dev` — the same wrong-target
      failure one hop over. Fixed: an empty `DEPLOYMENT`/`CONFIG` value in the file aborts
      (`GPU_IDS=` stays valid as the documented disable). RED case 14 reproduced
      `--name dev`, GREEN after.
      `[medium]` duplicate keys were silently first-wins, so an appended correction never
      took effect. Fixed: a duplicate key aborts. RED case 15 reproduced the stale
      `--name dev`, GREEN after. Self-test now 15 cases.
