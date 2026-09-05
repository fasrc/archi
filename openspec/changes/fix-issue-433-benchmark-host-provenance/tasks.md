# Tasks — record the host that ran a benchmark

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail for the right reason, write the smallest code that passes it, run
`bash scripts/gate.sh`, commit. Never end a task with the suite red, and never use
`--no-verify`.

Standing notes for every task:

- **Append carefully.** When you add a test to an existing file, put it after the last
  test's final line, not before it. Check the diff's trailing context line: an insertion
  placed above the file's last line silently steals that line from the previous test, and
  the gate stays green while an assertion disappears.
- **Run tests as `python -m pytest`.** A bare `pytest` resolves the main checkout, not this
  branch.
- **Coverage.** `scripts/gate.sh` measures `--cov=src`, so nothing in `scripts/` reports to
  `diff-cover`. Do not chase a coverage number on `compare_runs.py`; its unit tests are the
  evidence. The four production files are black-clean under black 24.10.0, so an in-place
  edit reformats nothing around it.
- **Scope.** Do not touch `.github/workflows/**`, `deploy/**`, `config/**`, `ralph.conf`,
  `PROMPT.md`, `Makefile`, `Containerfile`, `scripts/gate.sh`, or `hooks/**`. Do not edit
  any file under `bench_out/` — #426 owns artifact migration.
- **No `Co-Authored-By`** and no session trailers. Short lowercase commit messages.

## 1. Capture the host at deploy

- [x] 1.1 `model: opus` — Create `tests/unit/test_benchmark_host_provenance.py`. RED test
      `test_git_info_yaml_carries_the_host_block`: call
      `src.cli.managers.templates_manager.get_git_information()` and assert the returned
      mapping has a `host` entry that is a mapping holding the keys `hostname` and
      `cpu_model`, with a non-empty `hostname`. Watch it fail. Then add a module-level
      `collect_host_information()` to `src/cli/managers/templates_manager.py` that returns
      `{"hostname": ..., "cpu_model": ...}` — `socket.getfqdn()` for the hostname, the first
      `model name` line of `/proc/cpuinfo` for the processor model with
      `platform.processor()` as the fallback — and call it from `get_git_information()`
      (`:79`) so the block is attached in **both** branches of the existing
      `git branch` check. Add the `platform` import only: `socket` is already imported at
      `src/cli/managers/templates_manager.py:5`. Leave the `-> Dict[str, str]` return
      annotation alone — the existing "not a git repository" branch already stores a dict
      value under `git_info`, so the block adds no new violation, and rewriting the
      annotation would widen the diff for nothing. Gate green; commit.
- [x] 1.2 `model: sonnet` — Pin the two failure shapes, which are **not** symmetric. RED
      tests in the same file: monkeypatch the `/proc/cpuinfo` read to raise **and**
      `platform.processor` to return `""`, and assert `collect_host_information()` returns a
      mapping whose `hostname` is intact and whose `cpu_model is None`; then monkeypatch
      `socket.getfqdn` to raise and assert `collect_host_information() is None` — the whole
      block, not a mapping with a null hostname. Watch both fail. Then wrap each read in its
      own `try`, so an unreadable processor model never costs the hostname, and return
      `None` for the whole block when the hostname is unreadable. Gate green; commit.

      **Why the asymmetry.** The hostname is the identity; the processor model only explains
      why two hosts differ. A block carrying no hostname identifies no machine, so it must
      land in the existing `null` state rather than invent a fourth state that every
      downstream consumer would read as a recorded host named `None`. The issue's constraint
      "a machine that hides its processor model gives `null` for that key" is about
      `cpu_model` and is honoured by the first test. Note that this branch is close to
      unreachable in production — CPython's `socket.getfqdn` swallows a reverse-DNS failure
      itself and falls back to `gethostname()`, so it raises only if the machine cannot name
      itself at all — but the state must still be defined, because the renderers and
      `compare_runs.py` all branch on it.

## 2. Lift the host into the artifact's metadata

- [x] 2.1 `model: opus` — RED tests in `tests/unit/test_benchmark_host_provenance.py`, using
      the loader shape the existing benchmark tests use for
      `src.bin.service_benchmark`. `test_metadata_records_the_host`: with an
      `EXTRA_METADATA_PATH` YAML carrying `host: {hostname: h1, cpu_model: c1}`,
      `ResultHandler.add_metadata()` puts that block at `metadata["host"]` **and**
      `metadata["git_info"]` carries no `host` key. `test_metadata_records_null_when_the_deploy_predates_the_field`:
      a `git_info.yaml` with no `host` key gives `metadata["host"] is None`. Add a third
      case for an unreadable file, where `additional_info` is `None`, asserting
      `metadata["host"] is None` and no raise. Watch them fail. Then in `add_metadata`
      (`src/bin/service_benchmark.py:448`) pop the block **before** the metadata literal is
      built — `host = additional_info.pop("host", None) if isinstance(additional_info, dict)
      else None` — and add `"host": host` to the literal. A pop after the literal would
      mutate the same dict `"git_info"` already references and would depend on evaluation
      order.

      In the **same** literal add a `host_captured_at` string beside `host`, exactly as
      `git_info_captured_at` sits beside `git_info` at `src/bin/service_benchmark.py:464`.
      Wording: `"deploy (\`archi create\`), on the machine this stack runs on — a container
      cannot move hosts, so a --rerun ran here too"`. Assert it in
      `test_metadata_records_the_host`. Issue #433 says of the deploy-time caveat "State
      that in the artifact and in the docs", and the precedent this change copies makes the
      same demand in its own comment: "Say so in the artifact rather than in a comment"
      (`src/bin/service_benchmark.py:459-464`). A source comment alone does not reach a
      reader holding only the JSON. Write the code comment too, but the field is what
      satisfies the issue. Gate green; commit.

## 3. Render the host in both reports

      **CAUTION — do not reuse an existing test name.** Both
      `tests/unit/test_benchmark_report_markdown.py:509` and
      `tests/unit/test_benchmark_report_html_provenance.py:207` already define
      `test_provenance_says_not_recorded_for_an_older_artifact` (the #417 ingest
      regressions). A second module-level `def` of that name rebinds it, pytest collects
      only the last one, and the ingest regression silently disappears. `scripts/gate.sh`
      runs black, isort, pytest and diff-cover and **no** linter, so no `F811` fires and the
      only symptom is a test count that stops growing. Every test name added below is
      host-specific for exactly this reason.

- [x] 3.1 `model: opus` — RED tests in `tests/unit/test_benchmark_report_markdown.py`:
      `test_provenance_shows_the_host` (metadata with a host object → the markdown
      provenance block names the hostname) and
      `test_provenance_says_the_host_is_not_recorded_for_an_older_artifact` (metadata with
      no `host` key → the block renders the absent-key text, and that text differs from the
      text rendered for `metadata["host"] = None`). Assert all three texts are pairwise
      distinct. Watch them fail. Then add a module-level `_HOST_NOT_RECORDED = object()`
      sentinel beside `_INGEST_NOT_RECORDED` (`src/utils/generate_benchmark_report.py:58`),
      read `provenance["host"] = metadata.get("host", _HOST_NOT_RECORDED)` in
      `parse_benchmark_results` (`:78`), and add one host row to
      `format_version_markdown` (`:1001`) beside the code row, with a distinct
      `_MD_HOST_*` text for each of the three states. The recorded-host row names the
      hostname, appends the processor model only when `cpu_model` is not `None`, and carries
      the deploy-capture caveat from `metadata["host_captured_at"]` the way the
      deploy-commit row carries its own caveat at
      `src/utils/generate_benchmark_report.py:1022`. Assert the caveat text, and add a third
      test `test_a_host_without_a_processor_model_renders_no_none` asserting that a host
      whose `cpu_model` is `None` renders the hostname with no literal `None` beside it.
      Gate green; commit.
- [x] 3.2 `model: sonnet` — RED tests in
      `tests/unit/test_benchmark_report_html_provenance.py` mirroring 3.1's two tests
      against the HTML renderer, named
      `test_provenance_shows_the_host` and
      `test_html_provenance_says_the_host_is_not_recorded_for_an_older_artifact`, plus the
      same three-way distinctness assertion and the same caveat assertion. That file's
      `_results(**overrides)` helper (`:36-59`) routes every override into the **record**
      and hard-codes the metadata, so it cannot express a host: build the provenance dict
      and call `format_version_html` directly, the way the file already calls
      `format_html_output` with hand-built arguments at `:175-184`. Include a
      `code_version` in the fixture so the existing guard does not swallow the block —
      widening that guard is 3.3's job, not this task's. Watch them fail. Then add the
      matching host row to `format_version_html` (`:222`), escaping the hostname and the
      processor model with `html.escape` the way the code and config rows do, and applying
      the same "omit the processor model when it is `None`" rule as 3.1. Add the HTML twin
      of 3.1's `cpu_model is None` test, named
      `test_an_html_host_without_a_processor_model_renders_no_none`. Gate green; commit.
- [x] 3.3 `model: sonnet` — RED test, one per renderer, named
      `test_a_host_renders_without_any_version_digest`: an artifact whose metadata carries a
      `host` object but **no** `code_version` and whose record carries no `config_version`
      must still render the host. Watch both fail — today
      `if not code and not config: return ""` drops the whole block. That guard is at
      `src/utils/generate_benchmark_report.py:243` (HTML) and `:1008` (markdown); re-derive
      both with `grep -n "if not code and not config"` before editing, and do not trust a
      line number in this file that you have not just re-derived. Then widen both guards to
      return `""` only when the host is `_HOST_NOT_RECORDED` as well. Add a sibling
      asserting an artifact with none of the three still renders the empty string, so the
      widening did not turn the guard off. Gate green; commit.

## 4. Show the host in the comparison

- [x] 4.1 `model: sonnet` — RED test in `tests/unit/test_compare_runs.py`: a comparison of
      two artifacts whose metadata name hosts prints a `host` row in the provenance table
      naming both. Watch it fail. Then add a `host: Optional[dict] = None` field to `Arm`
      (`scripts/benchmarking/compare_runs.py:183`), populate it from
      `metadata.get("host")` where the other metadata fields are read (`:311-312`), and add
      a `host` row to `provenance_rows` (`:441`) that renders
      `<hostname> (<cpu_model>)`, falls back to the hostname alone when `cpu_model` is
      `None`, and renders `not recorded` when the arm has no host. Gate green; commit.
- [x] 4.2 `model: opus` — RED test
      `test_flags_two_arms_that_ran_on_different_hosts`: two arms with different hostnames
      make the report print a host-mismatch warning, and the exit code is unchanged from the
      same comparison with matching hosts. Add a sibling asserting that one arm with a host
      and one arm with no `host` key prints **no** warning. Watch both fail. Then add
      `host_mismatch_note(arms) -> Optional[str]`, returning the warning only when two or
      more **recorded** hostnames are distinct; put its result on the report dict as
      `host_mismatch` (`:2110`); and print that one line under the provenance table in the
      text renderer (`:1703-1710`). Filter unrecorded hostnames out **before** comparing, the
      way `corpus_gate` splits `unrecorded` from `distinct` at `:486-487`; the file's
      `_recorded()` helper at `:170` is the existing idiom. Do not add a key to the
      provenance rows — the renderer reads only `field` and `values`, and that contract has
      other callers. The note must not change the exit code. Gate green; commit.

## 5. Documents and close-out

- [ ] 5.1 `model: sonnet` — Edit `docs/docs/interpreting_benchmark_results.md`: add
      `host` to the results-file tree under `metadata` (`:659-663`), add a
      `metadata.host` row to the per-run provenance table (`:713-722`) answering "Did these
      runs execute on the same machine?", and add one sentence to §5.E saying that the host
      does **not** share the `git_info.last_commit` freeze trap described at `:701-712`,
      because a container cannot move to another machine. Add one line to
      `docs/docs/benchmarking.md` if it lists the metadata fields; skip it if it does not.
      A documents-only diff reports no coverage data and passes the gate on that ground.
      Gate green; commit.
- [ ] 5.2 `model: sonnet` — Verify the acceptance criteria end to end and open the PR.
      Run `git grep -n "getfqdn" -- 'src/*.py'` and confirm it is now non-empty.

      **Check for a shadowed test.** A duplicate top-level test name silently deletes the
      older test, and no linter in the gate catches it. Run:

      ```bash
      for f in tests/unit/test_benchmark_report_markdown.py \
               tests/unit/test_benchmark_report_html_provenance.py \
               tests/unit/test_benchmark_host_provenance.py \
               tests/unit/test_compare_runs.py; do
        echo -n "$f dupes: "; grep '^def test_' "$f" | sort | uniq -d | wc -l
      done
      ```

      Every line must print `0`. All four files have zero duplicates before this change, and
      the pre-change top-level test counts are 27 (markdown), 12 (HTML) and 83
      (compare_runs) — each must have GROWN, never shrunk. Then run
      `python -m pytest tests/unit/test_benchmark_host_provenance.py
      tests/unit/test_benchmark_report_markdown.py
      tests/unit/test_benchmark_report_html_provenance.py tests/unit/test_compare_runs.py -q`
      and confirm every test passes. Run `bash scripts/gate.sh` once more on the finished
      change and confirm it exits 0. Confirm `git status --porcelain` is empty after the
      last commit. Push with
      `git push -u origin fix/issue-433-benchmark-host-provenance` — the branch tracks
      `origin/dev`, so `-u` is required. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, and put `closes #433` in the PR
      **body**; a closing keyword in the title does not link the issue. Then stop. Do not
      merge.
