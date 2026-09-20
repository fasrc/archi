# Tasks — bound the git diff captured into benchmark artifact metadata

Every checkbox below is **one loop turn** and ends **green and committed**. Write the
failing test, watch it fail for the right reason, write the smallest code that passes it,
run `bash scripts/gate.sh`, commit. Never end a task with the suite red, and never use
`--no-verify`.

Standing notes for every task:

- **Read `design.md` first.** D2 gives the two git commands, D3 the bounding rule, D4 the
  four keys, D5 the pathspec. The proposal's table shows why the issue's literal pathspec
  (`-- . ':(exclude)bench_out'`) is wrong from the capture's own directory; do not use it.
- **New files only for tests.** All new tests go in the new file
  `tests/unit/test_benchmark_git_diff_bound.py`. Do not append to
  `tests/unit/test_benchmark_host_provenance.py` or any other existing test file.
- **Run tests as `python -m pytest`.** A bare `pytest` can resolve the main checkout, not
  this branch.
- **Coverage.** The gate measures `--cov=src` and needs 80% patch coverage. The new module
  `src/cli/managers/git_diff_capture.py` is a leaf; its tests exercise every line. All four
  production files touched are black-clean under black 24.10.0 at `4b253e26`, so an
  in-place edit reformats nothing around it. Run `black` and `isort` **before** `git add`,
  and confirm `git status --porcelain` is empty after each commit.
- **Real git in tests.** The loop container carries git 2.47.3. The fixture in 1.1 sets
  `GIT_CONFIG_GLOBAL` to `os.devnull` and `GIT_CONFIG_NOSYSTEM=1` via `monkeypatch.setenv`
  so the git processes spawned by the code under test cannot read any developer config.
  Pass identity on the commit as `git -c user.name=t -c user.email=t@t commit -q -m init`.
- **Do not assert on the `host` block.** It is #433's contract and depends on the container
  endpoint environment. These tests assert only the four diff keys and `last_commit`.
- **Do not run `openspec validate` in a task.** The CLI is not installed in the loop
  container. Loop 1 validated this change on the host on 2026-09-20.
- **Scope.** Do not touch `.github/workflows/**`, `deploy/**`, `config/**`, `ralph.conf`,
  `PROMPT.md`, `Makefile`, `Containerfile`, `scripts/gate.sh`, or `hooks/**`. Do not edit
  `src/utils/benchmark_provenance.py`, `scripts/benchmarking/compare_runs.py`, or
  `src/bin/service_benchmark.py`: the readers are unchanged by design.
- **No `Co-Authored-By`** and no session trailers. Short lowercase commit messages.

## 1. Bound the capture

- [ ] 1.1 `model: opus` — Create `tests/unit/test_benchmark_git_diff_bound.py` with a
      module-level `pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason=...)`
      and a `repo` fixture (design D7): under `tmp_path / "checkout"` write
      `src/pkg/mod.py` (`"x = 1\n"`), `README.md`, and `bench_out/art.json` holding
      `json.dumps({"k": list(range(20000))})` (about 130 KB); `git init -q`, `git add -A`,
      commit. RED tests, in this order, all calling
      `src.cli.managers.templates_manager.get_git_information(wd=repo)`:
      `test_a_clean_tree_records_empty_fields` (spec scenario "A clean tree records empty
      fields": `git_diff == ""`, `git_diff_stat == ""`, `git_diff_truncated is False`,
      `git_diff_original_bytes == 0`);
      `test_a_small_change_is_kept_whole` (append one line to `src/pkg/mod.py`; `+` line
      present in `git_diff`, `git_diff_truncated is False`,
      `git_diff_original_bytes == len(git_diff.encode("utf-8"))`, `"1 file changed"` in
      `git_diff_stat`);
      `test_an_oversized_change_is_cut_and_marked` (write `"x = 1\n"` plus 90 000 lines of
      `f"line_{i} = {i}\n"` to `src/pkg/mod.py`, about 1.3 MB;
      `len(git_diff.encode("utf-8")) <= GIT_DIFF_MAX_BYTES`, `git_diff.endswith("\n")`,
      `git_diff_truncated is True`, `git_diff_original_bytes > 1_000_000`,
      `len(json.dumps(info).encode("utf-8")) < 512_000`). **Watch them fail**: today
      `get_git_information()` takes no `wd` argument, so all three fail with `TypeError`,
      which is the right reason. Then create `src/cli/managers/git_diff_capture.py` with
      the constants, `git_diff_pathspec()`, `bound_text()` and `capture_git_diff()` exactly
      as design D1-D4 (include `--no-ext-diff --no-color` and the `--stat=120,,200` form),
      and edit `get_git_information` in `src/cli/managers/templates_manager.py:138-165` as
      design D6: add `wd: Optional[Path] = None`, resolve it to `Path(__file__).parent`
      when `None`, keep the `git branch` check and `last_commit` as they are, and replace
      lines `160-163` (the bare `git diff`) with `meta_data.update(capture_git_diff(wd))`.
      `Optional` is already imported at `:10`; add the module import beside
      `from src.cli.managers.source_version import write_source_commit` at `:14`. Leave the
      `-> Dict[str, str]` annotation alone. Confirm
      `tests/unit/test_benchmark_host_provenance.py` still passes: its
      `test_git_info_yaml_carries_the_host_block` calls `get_git_information()` with no
      argument, which is the production call shape. Gate green; commit.
- [ ] 1.2 `model: opus` — The exclusion, RED first, same test file. Add
      `test_a_bench_out_only_deletion_records_a_clean_tree` (delete `bench_out/art.json`;
      `git_diff == ""`, `git_diff_stat == ""`, `git_diff_original_bytes == 0`) and
      `test_a_mixed_change_records_only_the_code_path` (edit `src/pkg/mod.py` **and**
      delete `bench_out/art.json`; `"src/pkg/mod.py" in git_diff`,
      `"bench_out" not in git_diff`, `"bench_out" not in git_diff_stat`). **Watch both
      fail** if 1.1 shipped without the pathspec, then add `-- :(top,exclude)bench_out` to
      both commands. If 1.1 already carried the pathspec, both pass at once; that is
      acceptable, say so in the commit message, and go on to the guard test. Guard test:
      `test_capture_from_a_subdirectory_covers_the_whole_tree` — edit `src/pkg/mod.py`,
      edit `README.md`, delete `bench_out/art.json`, then call
      `get_git_information(wd=repo / "src" / "pkg")`; assert `"src/pkg/mod.py" in git_diff`,
      `"README.md" in git_diff`, and `"bench_out"` in neither `git_diff` nor
      `git_diff_stat`. **This test is the one that goes red if the issue's literal
      `-- . ':(exclude)bench_out'` was used**: from `src/pkg` that form drops `README.md`.
      If it fails, fix the pathspec to the `top` form, never the test. Gate green; commit.
- [ ] 1.3 `model: sonnet` — Pin `bound_text` and the stat bound, same test file, importing
      `bound_text`, `GIT_DIFF_MAX_BYTES` and `GIT_DIFF_STAT_MAX_FILES` from
      `src.cli.managers.git_diff_capture`. Tests: `test_bound_text_keeps_empty_input_empty`
      (`bound_text("", 10) == ("", False, 0)`);
      `test_bound_text_keeps_text_under_the_cap_whole` (`bound_text("a\nb\n", 100) ==
      ("a\nb\n", False, 4)`); `test_bound_text_cuts_at_a_line_boundary`
      (`bound_text("aaaa\nbbbb\ncccc\n", 11)` keeps `"aaaa\nbbbb\n"`, `True`, `15`);
      `test_bound_text_keeps_a_prefix_when_the_first_line_exceeds_the_cap`
      (`bound_text("x" * 50, 10)` returns a non-empty string of at most 10 bytes, `True`,
      `50`); `test_bound_text_never_splits_a_multibyte_character` (`bound_text("é" * 20, 7)`:
      `é` is 2 bytes, so the kept text is `"é" * 3` with 6 bytes, `True`, `40`; assert
      `len(kept.encode("utf-8")) <= 7` and `kept.encode("utf-8").decode("utf-8") == kept`);
      and `test_the_stat_is_bounded_by_file_count` using the `repo` fixture: create 300
      tracked files under `src/many/part<i>/file<i>.py`, commit, append a line to each,
      capture, assert `git_diff_stat.count("\n") <= GIT_DIFF_STAT_MAX_FILES + 3` and that
      the last non-empty line contains `"300 files changed"`. **These pass once 1.1 is
      correct — that is the point of them. Do not contrive a failure first.** If any fails,
      1.1's helper is wrong; fix the helper, not the test. Gate green; commit.

## 2. Say so in the docs

- [ ] 2.1 `model: sonnet` — Docs only. In `docs/docs/interpreting_benchmark_results.md`, in
      the artifact tree (about `:702`), directly under the line
      `│   ├── git_info.last_commit   # the DEPLOY's commit, NOT this run's code (§5.E)`, add
      two lines aligned the same way:
      `│   ├── git_info.git_diff      # uncommitted changes at DEPLOY; capped at 256 KB, top-level bench_out/ excluded`
      and
      `│   ├── git_info.git_diff_stat # git diff --stat of the same changes, at most 200 files`.
      In Procedure E (about `:744-748`), after the sentence ending "even though they ran
      different code.", add one sentence: "Since #514 the diff is bounded: `git_diff` holds
      at most 256 KB, `git_diff_truncated` says whether it was cut,
      `git_diff_original_bytes` gives the full size, and top-level `bench_out/` is
      excluded." No other edits; do not touch the table of fields. `diff-cover` prints
      "No lines with coverage information" for a docs-only diff, which is expected. Gate
      green; commit.

## 3. Close out

- [ ] 3.1 `model: sonnet` — Run `bash scripts/gate.sh` once more on the finished change and
      confirm it exits 0. Run `grep -n 'git_diff' src/utils/benchmark_provenance.py` and
      confirm the three matches (`:499`, `:524`, `:609`) are unchanged and all read the key
      `git_diff`. Confirm `git status --porcelain` is empty after the last commit. Push
      with `git push -u origin fix/issue-514-bound-git-diff` — the branch tracks
      `origin/dev`, so `-u` is required or the push retargets the trunk. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`. Put `Closes #514` in the **body** — a
      closing keyword in the title does not link the issue. The body must say: (a) existing
      oversized artifacts in `fasrc/archi-bench-out` are left as-is by design; (b) the
      pathspec departs from the issue's literal `-- . ':(exclude)bench_out'` because that
      form, run from the capture's own directory, hides every change outside
      `src/cli/managers/` (measured 2026-09-20, table in `proposal.md`); (c) the fix takes
      effect at the next `archi create` from a release that carries it, so the FASRC dev
      stack at `v2026.08.0` keeps writing oversized artifacts until redeployed. If the push
      or `gh pr create` returns HTTP 403 under the ambient `GH_TOKEN`, retry the same
      command prefixed with `env -u GH_TOKEN` (the keyring token holds `repo` scope); if
      that also fails, leave the branch committed and stop — the wrap-up pushes it. Then
      **stop. Do not merge.**
