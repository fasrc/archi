# Tasks — pin the two qa-v2 manifest artifact rules in the validator fence (#742)

Every checkbox below is one loop turn and ends **green and committed**. Never end a task with
the suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope.** Files this change may edit: `tests/unit/evaluation/qa/test_overwrite_crash_safety.py`
  and this change's `tasks.md`. Do NOT edit `src/evaluation/qa/schema.py` or any other file
  under `src/`, the #326 change folder
  (`openspec/changes/fix-issue-326-crash-safe-overwrite-manifest/`), `docs/**`, `deploy/**`,
  `.github/**`, or any control-plane file.
- **Read `design.md` first.** Loop 1 ran the Appendix A tests and the Appendix B script on
  `origin/dev` `73104693` (results in task 1.1 and design D4). **Copy the Appendix code
  exactly. Do not rewrite it.**
- **Fresh tiktoken cache.** Before any pytest or gate run, run
  `export TIKTOKEN_CACHE_DIR=$(mktemp -d)`. A stale cache on a full-swap host gives false EIO
  errors.
- **Stage before you gate.** Run `black` and `isort` on the test file, `git add` it, THEN run
  `bash scripts/gate.sh`, then commit, and confirm `git status --porcelain` is empty.

## 1. Pin the two artifact rules

- [x] 1.1 Insert the Appendix A code into `class TestValidatorFence` in `tests/unit/evaluation/qa/test_overwrite_crash_safety.py`, directly after the last line of `test_score_prune_refused_with_wrong_status` (`        RunManifest.from_dict(pruned)`, after the comment `# Demoted to run_completed is accepted.`) and before the two blank lines and `class TestHappyPathAuditSweep:`. Keep one blank line between the old last line and the new decorator. Do not add an import: the file already imports everything the code uses. Then check the insert: `git diff -- tests/unit/evaluation/qa/test_overwrite_crash_safety.py | grep -c '^-[^-]'` prints `0` (no old line removed or changed), and `grep -c -E 'def (test_scored_manifest_refused_without_artifact|test_live_check_rule_covers_status|test_paused_manifest_refused_without_live_checks)\b' tests/unit/evaluation/qa/test_overwrite_crash_safety.py` prints `3` (no duplicate test name). Run `python -m pytest -q -p no:cacheprovider tests/unit/evaluation/qa/test_overwrite_crash_safety.py -k TestValidatorFence`: exactly `10 passed, 15 deselected` (4 old cases + 6 new). Run the whole file: exactly `25 passed`. This is the TDD red step for tests that pin existing behaviour: save the Appendix B script to `/tmp/742-mutation.sh` and run `bash /tmp/742-mutation.sh` from the repository root. It must print `4 failed, 6 passed, 15 deselected` for the `live` stage (the 4 failures are `test_scored_manifest_refused_without_artifact[live_checks.jsonl-...]`, `test_live_check_rule_covers_status[run_completed]`, `test_live_check_rule_covers_status[attention_required]` and `test_paused_manifest_refused_without_live_checks`, each `DID NOT RAISE ValueError`), and `2 failed, 8 passed, 15 deselected` for the `scored` stage (the `summary.json` and `report.md` cases). If a stage prints `10 passed`, the run imported the real checkout and not the copy: stop and write the problem to `docs/questions.md`. Save the full script output to `/tmp/742-mutation.out` for the PR body. Run `git grep -n -E 'missing live-check artifacts|missing result artifacts' -- tests` and confirm it prints at least one line for each message. Run `git diff origin/dev -- src/evaluation/qa/schema.py` and confirm it prints nothing. Format, `git add` the test file and this `tasks.md` with this box ticked, run `bash scripts/gate.sh` green, then commit as `test(qa): pin the manifest artifact rules in the validator fence`. Put both mutation-stage results (the `FAILED` lines and the summary line of each stage) in the commit body.

## 2. Publish

- [ ] 2.1 Run `bash scripts/gate.sh` one more time on the branch tip and confirm it is green. Tick this box (2.1) in this `tasks.md`, commit the tick through the hook as `chore(#742): mark the publish task done`, and only then push: `git push -u origin fix/issue-742-pin-manifest-artifact-rules` (`-u` sets the upstream to the branch's own remote name). Confirm `git rev-parse HEAD` equals `git rev-parse origin/fix/issue-742-pin-manifest-artifact-rules`. Open the PR with `gh pr create --repo fasrc/archi --base dev --head fix/issue-742-pin-manifest-artifact-rules --title "test(qa): pin the manifest artifact rules in the validator fence (#742)" --body-file <file>`. The PR body must contain the line `closes #742` (in the body, not only the title), and must state: (a) the 6 new cases in `TestValidatorFence` and what each one breaks (3 on a real scored manifest, 2 on the edited `run_completed` and `attention_required` documents, 1 on the real first-run pause manifest), each with a positive control and an exact-message assertion; (b) the mutation evidence from `/tmp/742-mutation.out`: both stages, with their `FAILED` lines and summary lines, in a fenced code block; (c) `git diff origin/dev -- src/evaluation/qa/schema.py` is empty: the change is tests only, so no source line, no patch-coverage line and no docs change (the validator's behaviour does not change); (d) the origin: finding 1 of the 2026-10-22 post-PR review of PR #741 (#326). No `Co-Authored-By` trailer. Do not merge. Confirm the PR links the issue: `gh pr view <n> --repo fasrc/archi --json closingIssuesReferences` lists `742`.

## Appendix (reference only — no checkboxes)

### A. Test code to insert into `class TestValidatorFence`

Insert this block exactly. It starts with one blank line and is indented for the class body.

```python

    @pytest.mark.parametrize(
        ("artifact", "message"),
        [
            ("live_checks.jsonl", "qa-v2 manifest is missing live-check artifacts"),
            ("summary.json", "scored manifest is missing result artifacts"),
            ("report.md", "scored manifest is missing result artifacts"),
        ],
    )
    def test_scored_manifest_refused_without_artifact(
        self, monkeypatch, tmp_path, runtimes, artifact, message
    ):
        value = {"value": 7, "revision": "r1"}
        run_dir, _invoker, _rt = _run(monkeypatch, tmp_path, [value] * 3, runtimes)
        manifest = read_json(run_dir / "manifest.json")
        assert manifest["schema_version"] == "qa-v2"
        assert manifest["status"] == "scored"

        # Positive control: the untouched scored manifest is accepted.
        RunManifest.from_dict(copy.deepcopy(manifest))

        broken = copy.deepcopy(manifest)
        broken["artifacts"].pop(artifact)
        with pytest.raises(ValueError) as caught:
            RunManifest.from_dict(broken)
        assert str(caught.value) == message

    @pytest.mark.parametrize("status", ["run_completed", "attention_required"])
    def test_live_check_rule_covers_status(
        self, monkeypatch, tmp_path, runtimes, status
    ):
        value = {"value": 7, "revision": "r1"}
        run_dir, _invoker, _rt = _run(monkeypatch, tmp_path, [value] * 3, runtimes)
        manifest = copy.deepcopy(read_json(run_dir / "manifest.json"))
        manifest["status"] = status
        if status == "run_completed":
            manifest["phases"].pop("score")
            for name in SCORE_FILES:
                manifest["artifacts"].pop(name, None)

        # Positive control: the edited manifest passes every earlier rule.
        RunManifest.from_dict(copy.deepcopy(manifest))

        manifest["artifacts"].pop("live_checks.jsonl")
        with pytest.raises(ValueError) as caught:
            RunManifest.from_dict(manifest)
        assert str(caught.value) == "qa-v2 manifest is missing live-check artifacts"

    def test_paused_manifest_refused_without_live_checks(
        self, monkeypatch, tmp_path, runtimes
    ):
        _workflow, run_dir = _paused_at_live_gate(monkeypatch, tmp_path)
        manifest = read_json(run_dir / "manifest.json")
        assert manifest["schema_version"] == "qa-v2"
        assert manifest["status"] == "attention_required"

        # Positive control: the real first-run pause manifest is accepted.
        RunManifest.from_dict(copy.deepcopy(manifest))

        broken = copy.deepcopy(manifest)
        broken["artifacts"].pop("live_checks.jsonl")
        with pytest.raises(ValueError) as caught:
            RunManifest.from_dict(broken)
        assert str(caught.value) == "qa-v2 manifest is missing live-check artifacts"
```

### B. Mutation script (`/tmp/742-mutation.sh`)

Run it from the repository root, after Appendix A is in the working tree. Each stage builds a
throwaway copy from `HEAD` plus the working-tree test file, disables one rule in the copy's
`schema.py`, and runs the fence there. It never changes the real checkout.

```bash
#!/usr/bin/env bash
set -u
repo=$(git rev-parse --show-toplevel)
test_file=tests/unit/evaluation/qa/test_overwrite_crash_safety.py
for stage in live scored; do
  d=$(mktemp -d)
  git -C "$repo" archive HEAD | tar -x -C "$d"
  cp "$repo/$test_file" "$d/$test_file"
  python - "$d/src/evaluation/qa/schema.py" "$stage" <<'PY'
import pathlib
import sys

path, stage = pathlib.Path(sys.argv[1]), sys.argv[2]
code = path.read_text()
if stage == "live":
    old = '        and "live_checks.jsonl" not in artifacts\n'
    new = old + "        and False\n"
else:
    old = "    if status is RunStatus.SCORED and not {"
    new = "    if False and status is RunStatus.SCORED and not {"
assert code.count(old) == 1, f"{stage}: anchor not found exactly once"
path.write_text(code.replace(old, new))
PY
  echo "=== $stage rule disabled"
  (
    cd "$d" &&
      PYTHONPATH="$d" TIKTOKEN_CACHE_DIR=$(mktemp -d) python -m pytest -q \
        -p no:cacheprovider "$test_file" -k TestValidatorFence 2>&1 |
      grep -E '^FAILED|passed|failed'
  )
done
```
