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

- [ ] 1.1 `model: opus` — RED: add `deploy/fasrc-dev/scripts/test_host_env.sh`, modeled on
      `test_gpu_flag.sh` (fake `archi` on `PATH`, temp `SCRIPT_DIR` fixture, TAP-ish
      `ok -`/`not ok -`, non-zero exit on failure), covering seven cases: defaults survive a
      missing `host.env`; `host.env` `DEPLOYMENT` reaches `archi create --name`; `CONFIG`
      honored; command-line env beats a `:=`-style `host.env`; a plain assignment beats the
      command line (documented tradeoff, pinned); `GPU_IDS=""` from `host.env` still passes
      no flag; missing `host.env` is not an error. Watch it fail because `lib.sh` never
      sources `host.env`. Then implement in `lib.sh`: the sourcing block after `REPO_ROOT`,
      and `DEPLOYMENT`/`CONFIG` as `${VAR:-default}`. All four self-tests green. Gate;
      commit.
- [ ] 1.2 `model: sonnet` — Add `host.env.example` (tracked): the `: "${VAR:=value}"` idiom,
      the plain-assignment warning, both host blocks (`dev` reserved for the GPU host,
      `claw` for the no-GPU / no-local-vLLM workstation). Add the README "Per-host
      configuration" section. Verify `git check-ignore`: `host.env` ignored, the example
      not. Gate; commit.
- [ ] 1.3 `model: haiku` — Correct the false premise in `lib.sh:21-29` and
      `test_gpu_flag.sh:8-13`; keep every default and the `${GPU_IDS-}` form.
      `grep -rn 'neither the nvidia container runtime' deploy/fasrc-dev/scripts/` returns
      nothing. Self-tests green. Gate; commit.

## 2. Close-out

- [ ] 2.1 `model: sonnet` — Run all four shell self-tests and the issue's no-deploy verify
      snippet (fake `archi`, `DEPLOYMENT=claw`); record the observed output here.
- [ ] 2.2 `model: haiku` — Gate in the container on the finished branch; push
      `fix/issue-363-host-env-claw`; open the PR (`gh pr create --repo fasrc/archi --base
      dev`, `closes #363` in the body, Findings block from the pre-PR review). Do not merge.

## 3. Pre-PR adversarial review

Rounds recorded here because the loop runs before any PR exists to comment on. Surviving
findings become the PR body's Findings block.
