# Tasks — wait for the vllm stub before the readiness probe answers (#797)

Every checkbox below is one loop turn and ends **green and committed**. Never end a task with
the suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope.** Files this change may edit: `tests/unit/test_judge_slurm.py` and this change's
  `tasks.md`. Do NOT edit `scripts/benchmarking/slurm/archi_judge.sbatch` or any other file
  under `scripts/`, any file under `src/`, `docs/**`, `deploy/**`, `.github/**`, or any
  control-plane file. Do not commit a PR-body file or a `docs/questions.md` entry.
- **Read `design.md` first.** Loop 1 ran the Appendix A patch and the Appendix B script on
  `origin/dev` `5f3d80e9` (results in design D5). **Copy the Appendix text exactly with the
  Write tool. Do not rewrite it.**
- **Fresh tiktoken cache.** Before any pytest or gate run, run
  `export TIKTOKEN_CACHE_DIR=$(mktemp -d)`. A stale cache on a full-swap host gives false EIO
  errors.
- **Foreground only.** Run `bash scripts/gate.sh` and `git commit` in the foreground and wait
  for the exit status in the same turn. Never put a command in the background, and never end
  a turn to "wait" for the gate, for a commit, or for CI.
- **Stage before you gate.** `git add` the files, THEN run `bash scripts/gate.sh`, then
  commit, then confirm `git status --porcelain` is empty.

## 1. Make the curl stub wait for the vllm serve stub

- [x] 1.1 Red and green in one task (do not commit a red state). Write Appendix A to `/tmp/797-fix.patch` with the Write tool. From the repository root run `git apply --check -p1 /tmp/797-fix.patch` and then `git apply -p1 /tmp/797-fix.patch`. Confirm `sha256sum tests/unit/test_judge_slurm.py` prints `36f8f9c90c77ad62d9ecd25787dd562dc12e2d64ca4c0100ddb0c6c81c663349` (if `git apply` refuses the patch, make the three hunks by hand and use this hash as the check; if the hash still differs, STOP and write what differs under "## Measurement" in `design.md`). Confirm `git diff --name-only` prints only `tests/unit/test_judge_slurm.py`, and `git diff -- tests/unit/test_judge_slurm.py | grep -c '^-[^-]'` prints `0` (no old line removed). Run `python -m pytest -q -p no:cacheprovider tests/unit/test_judge_slurm.py`: exactly `56 passed`. Then the red and green proof: write Appendix B to `/tmp/797-proof.sh` with the Write tool, confirm `sha256sum /tmp/797-proof.sh` prints `577d719a3de9fc8e37dc424615e5072ea56e526357043cd2c74e8923017873d4`, run `bash /tmp/797-proof.sh 2>&1 | tee /tmp/797-proof.out` from the repository root (about 2.5 min, it writes only under `/tmp/797-proof/`), and confirm these summary lines in this order: `red` `2 failed, 54 passed` (`test_a_rebuild_during_the_job_keeps_the_pinned_pair` and `test_a_judge_env_change_during_the_job_does_not_reach_the_scorer`); `red-top` `4 failed, 52 passed` (the 2 above plus `test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm` and `test_a_server_that_never_gets_ready_fails_the_job_and_is_stopped`); `green` `56 passed`; `green-top` `56 passed`; `marker` `1 failed, 55 deselected` (`test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm`, `AssertionError`). If a `green` stage fails, or a stage summary differs, STOP and write what you saw under "## Measurement" in `design.md`. Then run the file 20 times without a delay: `for i in $(seq 1 20); do python -m pytest -q -p no:cacheprovider tests/unit/test_judge_slurm.py 2>&1 | tail -1 | sed 's/ in .*//'; done | sort | uniq -c` must print exactly `     20 56 passed`. Run `grep -n 'curl.count").read_text()) == 3' tests/unit/test_judge_slurm.py` and confirm it prints 1 line. Run `black --check tests/unit/test_judge_slurm.py` and `isort --check-only tests/unit/test_judge_slurm.py` (both exit 0). Tick this box. `git add tests/unit/test_judge_slurm.py` and this `tasks.md`, run `bash scripts/gate.sh` in the foreground and wait for exit 0, then commit both together as `test: wait for the vllm stub before the readiness probe answers`. Put the five stage summary lines from `/tmp/797-proof.out` (with their `FAILED` test names) and the `20 56 passed` line in the commit body.

## 2. Publish

- [x] 2.1 Run `bash scripts/gate.sh` in the foreground on the branch tip and confirm exit 0. Tick this box (2.1) in this `tasks.md`, commit the tick through the hook as `chore(#797): mark the publish task done`, and only then push: `git push -u origin fix/issue-797-judge-slurm-stub-race` (`-u` sets the upstream to the branch's own remote name). Confirm `git rev-parse HEAD` equals `git rev-parse origin/fix/issue-797-judge-slurm-stub-race`. Open the PR with `gh pr create --repo fasrc/archi --base dev --head fix/issue-797-judge-slurm-stub-race --title "test: wait for the vllm stub before the readiness probe answers (#797)" --body-file /tmp/797-pr-body.md` (write the body file under `/tmp` with the Write tool; do NOT commit it). The PR body must contain the line `Closes #797` (in the body, not only in the title) and must state: (a) the race (the job probes, scores and stops the server before the `vllm serve` stub's side effects) and the fix (the stub writes `vllm.ready` last; the `curl` stub waits for it, at most 1000 x 0.01 s, before it records the call, so `curl.count` does not change; one new assertion pins the marker); (b) the proof: the five stage summary lines and `FAILED` names from the commit body of task 1.1, in a fenced code block, and the `20 56 passed` line; (c) `git diff --name-only origin/dev...HEAD -- tests scripts src` prints only `tests/unit/test_judge_slurm.py`: `archi_judge.sbatch` does not change, and a tests-only diff scores no patch-coverage lines; (d) the origin: the host gate failed on the `edit-env` and `rebuild` tests on 2026-11-02 and 2026-11-03, up to 3 times in a row. No `Co-Authored-By` trailer. Do not merge. Confirm the PR links the issue: `gh pr view fix/issue-797-judge-slurm-stub-race --repo fasrc/archi --json closingIssuesReferences` lists `797`. STOP at the open PR.

## Appendix (reference only — no checkboxes)

### A. The fix: write this to `/tmp/797-fix.patch` exactly

Every context line of this patch starts with one space, and the empty context line in the
second hunk is a single space. `git apply -p1` accepts it on `origin/dev` `5f3d80e9`.

```diff
--- a/tests/unit/test_judge_slurm.py
+++ b/tests/unit/test_judge_slurm.py
@@ -114,6 +114,8 @@
       printf '{"image_digest": "sha256:rebuilt"}\n' > "$JUDGE_HOME/scorer-identity.json.new"
       mv -f "$JUDGE_HOME/scorer-identity.json.new" "$JUDGE_HOME/scorer-identity.json"
     fi
+    # Last side effect: the curl stub answers only after this file exists (#797).
+    : > "$STUB/vllm.ready"
     exec sleep 300 ;;
   build*)
     out=""
@@ -125,6 +127,10 @@
 """
 
 _CURL = r"""#!/bin/bash
+# Wait for the vllm serve stub's side effects (at most 1000 x 0.01 s), so the job
+# cannot probe, score and stop the server before they happen (#797).
+i=0
+while [ ! -e "$STUB/vllm.ready" ] && [ "$i" -lt 1000 ]; do sleep 0.01; i=$((i + 1)); done
 printf '%s\n' "$*" >> "$STUB/curl.calls"
 for a in "$@"; do case "$a" in @*) cat "${a#@}" >> "$STUB/curl.header" ;; esac; done
 n="$(cat "$STUB/curl.count" 2>/dev/null || echo 0)"; echo $((n + 1)) > "$STUB/curl.count"
@@ -490,6 +496,8 @@
     assert f"--socket {socket}" in run
     # It waited for /v1/models through the socket.
     assert int((stub / "curl.count").read_text()) == 3
+    # The server stub did all its side effects before the job stopped it (#797).
+    assert (stub / "vllm.ready").exists()
     assert all("--unix-socket" in c and "/v1/models" in c for c in _calls(stub, "curl"))
     # The merge job is submitted after this job succeeds.
     (merge,) = _calls(stub, "sbatch")
```

### B. The proof: write this to `/tmp/797-proof.sh` exactly

Run it from the repository root. It copies `origin/dev` into `/tmp/797-proof/` with
`git archive`, edits only those copies, and never writes to the checkout.

```bash
#!/bin/bash
# #797 proof: run tests/unit/test_judge_slurm.py in throwaway copies under /tmp, with a
# temporary `sleep 3` in the vllm serve stub. Run it from the repository root. It never
# writes to the checkout. `red*` stages use the origin/dev test file; `green*` stages and
# `marker` use the working-tree test file (the fix).
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
OUT="${PROOF_DIR:-/tmp/797-proof}"
rm -rf "$OUT"
mkdir -p "$OUT/base"
git -C "$ROOT" archive origin/dev src tests scripts pyproject.toml | tar -x -C "$OUT/base"
export TIKTOKEN_CACHE_DIR="$(mktemp -d)"

edit() { # edit <file> <mode>: insert the delay, or remove the marker line
  python3 - "$1" "$2" <<'PY'
import sys

path, mode = sys.argv[1], sys.argv[2]
text = open(path).read()
pairs = {
    "delay": [
        (
            "      # An operator edits judge.env while the model loads.\n",
            "      # An operator edits judge.env while the model loads.\n      sleep 3\n",
        ),
        (
            "      # build_scorer_image.sh while the job runs: the image moves first, then the sidecar.\n",
            "      # build_scorer_image.sh while the job runs: the image moves first, then the sidecar.\n      sleep 3\n",
        ),
    ],
    "delaytop": [('  *"vllm serve"*)\n    prev=""\n', '  *"vllm serve"*)\n    sleep 3\n    prev=""\n')],
    "nomarker": [('    : > "$STUB/vllm.ready"\n', "")],
}[mode]
for old, new in pairs:
    assert text.count(old) == 1, (mode, old)
    text = text.replace(old, new)
open(path, "w").write(text)
PY
}

stage() { # stage <name> <test-file source: dev|work> <mode> [pytest args...]
  local name="$1" src="$2" mode="$3"
  shift 3
  local dir="$OUT/$name" file
  cp -a "$OUT/base" "$dir"
  file="$dir/tests/unit/test_judge_slurm.py"
  [ "$src" = work ] && cp "$ROOT/tests/unit/test_judge_slurm.py" "$file"
  edit "$file" "$mode"
  echo "=== $name ($src test file, $mode)"
  (cd "$dir" && python -m pytest -q -p no:cacheprovider -o addopts="" "$@" \
    tests/unit/test_judge_slurm.py 2>&1 | grep -E '^(FAILED|ERROR)|passed|failed' | cut -c1-200) || true
}

stage red dev delay
stage red-top dev delaytop
stage green work delay
stage green-top work delaytop
stage marker work nomarker -k test_a_job_serves_on_a_private_socket_scores_merges_and_stops_vllm
```
