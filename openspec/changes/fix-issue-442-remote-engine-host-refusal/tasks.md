# Tasks — record no host when the container engine is not provably local

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail for the right reason, write the smallest code that passes it, run
`bash scripts/gate.sh`, commit. Never end a task with the suite red, and never use
`--no-verify`.

Standing notes for every task:

- **Append carefully.** When you add a test to an existing file, put it after the last
  test's final line, not before it. Check the diff's trailing context line: an insertion
  placed above the file's last line silently steals that line from the previous test, and
  the gate stays green while an assertion disappears.
- **No duplicate test names.** The gate runs no linter, so a reused `def test_...` deletes
  the older test in silence. Before each commit, run
  `grep '^def test_' <file> | sort | uniq -d` on every test file you touched and confirm
  it prints nothing.
- **Run tests as `python -m pytest`.** A bare `pytest` resolves the main checkout, not
  this branch. In a bare shell, prefix commands with
  `PATH=/home/austin/miniforge3/envs/archi/bin:$PATH`.
- **Coverage.** `scripts/gate.sh` measures `--cov=src`, and every production file in this
  change is under `src/`, so `diff-cover` scores all of it. Both files edited in place are
  black-clean under black 24.10.0 at `c6112167`, so an in-place edit reformats nothing
  around it.
- **Flaky test.** `tests/unit/evaluation/qa/test_jobs_history.py::test_job_manager_terminates_running_evaluation_process`
  is flaky (fasrc/archi#435). If only that test fails, re-run the gate. Do not bypass it
  and do not fix it here.
- **Scope.** Do not touch `.github/workflows/**`, `deploy/**`, `config/**`, `ralph.conf`,
  `PROMPT.md`, `Makefile`, `Containerfile`, `scripts/gate.sh`, or `hooks/**`. Do not
  change `host_captured_at`. Do not add a `docker` or `podman` subprocess call.
- **No `Co-Authored-By`** and no session trailers. Short lowercase commit messages.

## 1. Classify a container endpoint

- [x] 1.1 Create `tests/unit/test_container_endpoint.py` with RED tests for a new pure
      function `endpoint_is_local(endpoint)` in a new module
      `src/utils/container_endpoint.py`. Cases, one test each:
      `unix:///var/run/docker.sock` → `True`;
      `unix:/run/user/1000/podman/podman.sock` (single slash, the documented FASRC form)
      → `True`; `/var/run/docker.sock` (bare path, no scheme) → `True`; `""` → `True`;
      `None` → `True`; `"  "` (whitespace only) → `True`;
      `tcp://engine.example.edu:2376` → `False`; `ssh://user@box/run/podman.sock` →
      `False`; `http://h:2375` → `False`; `https://h:2376` → `False`;
      `npipe:////./pipe/docker_engine` → `False`; `weirdscheme://h` (unknown scheme) →
      `False`. Watch them fail on the missing module.

      Then write the module. The rule is one sentence, and the table above is derived from
      it: strip the value; an empty value is local; a value beginning `unix:` is local; a
      value carrying any other URI scheme (match `^[A-Za-z][A-Za-z0-9+.-]*:`) is not
      provably local; a value with no scheme is a filesystem path and is local. Put that
      sentence in the module docstring, with the reason a presence check is wrong (the
      FASRC `export DOCKER_HOST=unix:/$(podman info …)` line). Gate green; commit.

- [x] 1.2 Extend `tests/unit/test_container_endpoint.py` with RED tests for
      `container_endpoint_is_provably_local()` reading the environment. Use
      `monkeypatch.setenv` and `monkeypatch.delenv(..., raising=False)` so no test leaks a
      variable. Cases: no variable set → `True`; `DOCKER_HOST=unix:///var/run/docker.sock`
      → `True`; `DOCKER_HOST=tcp://h:2376` → `False`;
      `CONTAINER_HOST=ssh://user@box/run/podman.sock` → `False`;
      `CONTAINER_HOST=unix:/run/user/1000/podman/podman.sock` → `True`; both set and both
      local → `True`; `DOCKER_HOST` local and `CONTAINER_HOST` remote → `False` (either
      variable refuses). Every test in this task must also `delenv` `DOCKER_CONTEXT` and
      point `HOME` at `tmp_path`, so no developer's real Docker configuration reaches the
      assertion.

      Then implement the environment half. Gate green; commit.

- [x] 1.3 Extend `tests/unit/test_container_endpoint.py` with RED tests for the Docker
      context. Build a fake store under `tmp_path` and set `HOME` to it with
      `monkeypatch.setenv`. Layout, taken from `docker context inspect`'s own
      `MetadataPath` output: `.docker/contexts/meta/<any-dir-name>/meta.json`, each file
      holding `{"Name": "<ctx>", "Endpoints": {"docker": {"Host": "<endpoint>"}}}`.
      Cases: `DOCKER_CONTEXT` naming a context whose `Host` is `unix://…` → `True`;
      naming one whose `Host` is `tcp://…` → `False`; `DOCKER_CONTEXT=default` → `True`
      (the name alone is not evidence); no `DOCKER_CONTEXT` but
      `.docker/config.json` holding `{"currentContext": "<remote ctx>"}` → `False`;
      `DOCKER_HOST=unix://…` set together with a `tcp://` context → `True` (`DOCKER_HOST`
      wins); a `config.json` holding malformed JSON → `True` and raises nothing; a
      `~/.docker` directory that does not exist → `True`; a `meta.json` naming no matching
      context → `True`.

      Then implement context resolution. Read `DOCKER_CONTEXT`; if it is unset or empty,
      read `currentContext` from `~/.docker/config.json`. An empty name, or the name
      `default`, resolves to no endpoint. Otherwise **glob**
      `~/.docker/contexts/meta/*/meta.json`, parse each, and take the one whose `Name`
      field equals the context name — do not compute a digest of the name, because
      matching on the field the file states itself cannot be wrong about the digest
      algorithm. Read `Endpoints.docker.Host` from the match.

      Wrap the whole function body so that **any** exception returns "provably local".
      An unreadable Docker configuration is not evidence of a remote engine, and the
      capture clause forbids raising. Gate green; commit.

## 2. Refuse the capture

- [x] 2.1 Add a RED test to `tests/unit/test_benchmark_host_provenance.py` (append after
      the file's last line) named
      `test_collect_host_information_returns_none_for_a_remote_endpoint`: set
      `DOCKER_HOST=tcp://engine.example.edu:2376` with `monkeypatch.setenv`, call
      `collect_host_information()`, and assert it returns `None`. Add a second,
      `test_collect_host_information_still_records_a_local_endpoint`: set
      `DOCKER_HOST=unix:///var/run/docker.sock`, and assert the result is a mapping with a
      non-empty `hostname`. Watch the first fail.

      In the **same task**, fix the existing ambient-environment test. Add a
      `monkeypatch` fixture argument to `test_git_info_yaml_carries_the_host_block`
      (`tests/unit/test_benchmark_host_provenance.py:13`) and
      `monkeypatch.delenv("DOCKER_HOST", raising=False)`,
      `monkeypatch.delenv("CONTAINER_HOST", raising=False)`,
      `monkeypatch.delenv("DOCKER_CONTEXT", raising=False)` before the call. Without this
      the test reads the developer's or the runner's environment and fails for a correct
      reason. Do the same for any other test in that file that calls
      `get_git_information()` or `collect_host_information()` without setting the
      variables itself.

      Then add the guard: import `container_endpoint_is_provably_local` from
      `src.utils.container_endpoint` into
      `src/cli/managers/templates_manager.py` (the module already imports from
      `src.utils`, see `:18` and `:23`), and return `None` from
      `collect_host_information()` (`:95`) **before** the `socket.getfqdn()` call when the
      check is false. Add a short comment stating that this is a refusal, not a failure,
      and that `host_captured_at` asserts the CLI host and the engine host are the same
      machine. Gate green; commit.

## 3. Name the fourth cause in both reports

- [x] 3.1 Add RED tests for the report text. Append to
      `tests/unit/test_benchmark_report_markdown.py` (38 tests before this change) and to
      `tests/unit/test_benchmark_report_html_provenance.py` (20 before). Each new test
      renders an artifact whose `metadata.host` is `None` and asserts the null text names
      the refusal — assert on a stable substring such as
      `"container engine"` — **and** still names the three existing causes
      (`"capture failed"`, `"metadata could not be read"`, and the predates-the-field
      wording). Reuse the helpers those files already use for the null case (see
      `tests/unit/test_benchmark_report_markdown.py:685-686` and
      `tests/unit/test_benchmark_report_html_provenance.py:340-341`). Watch them fail.

      Then edit `src/utils/generate_benchmark_report.py`: extend `_MD_HOST_NULL` (`:77`)
      and `_HTML_HOST_NULL` (`:86`) with the fourth cause, worded as a refusal rather
      than a failure — for example "or the container engine was not provably local, so no
      host was recorded". Use `&mdash;`-style HTML entities in the HTML constant to match
      its neighbour. Correct the `_HOST_NOT_RECORDED` sentinel comment at `:60-74`: it
      says the value has "THREE" causes and enumerates three. Make it four and add the
      refusal, keeping the existing note that the lead clause claims only the artifact.
      Gate green; commit.

## 4. Keep the spec and the documents agreeing

- [x] 4.1 Amend the requirement at
      `openspec/changes/fix-issue-433-benchmark-host-provenance/specs/benchmark-run-provenance/spec.md:5`.
      It reads "`archi create` SHALL record the machine it runs on in `git_info.yaml`, as
      a `host` block holding a `hostname` and a `cpu_model`." Rewrite it so the obligation
      is conditional on a provably-local container endpoint, and cross-reference this
      change for the classification rule. **Keep the whole SHALL sentence on one physical
      line** — `openspec validate --strict` reads only a requirement's first physical
      line, so a wrapped lead sentence fails validation. Add one scenario to that same
      requirement for the refusal.

      Then run **both** validations and confirm both pass:

      ```bash
      openspec validate fix-issue-433-benchmark-host-provenance --strict
      openspec validate fix-issue-442-remote-engine-host-refusal --strict
      ```

      If the `openspec` CLI is not on `PATH` in this environment, say so in the commit
      message and continue — do not halt the loop on a missing tool.

      In the same task, edit `docs/docs/interpreting_benchmark_results.md:749-751`. That
      passage says the host recorded at deploy is the host every run in that deployment
      used. Add one sentence: the recorded host is written only when the container
      endpoint is provably local, and `archi create` records `null` rather than a guess
      when it is not. A documents-and-spec diff reports no coverage data and passes the
      gate on that ground. Gate green; commit.

## 5. Close out

- [x] 5.1 Verify the acceptance criteria end to end, then push and open the PR.

      Confirm each criterion from issue #442:

      ```bash
      # detection reads both variables and classifies by scheme
      git grep -n "DOCKER_HOST\|CONTAINER_HOST\|DOCKER_CONTEXT" -- src | head
      # no duplicate test names anywhere this change touched
      for f in tests/unit/test_container_endpoint.py \
               tests/unit/test_benchmark_host_provenance.py \
               tests/unit/test_benchmark_report_markdown.py \
               tests/unit/test_benchmark_report_html_provenance.py; do
        echo -n "$f dupes: "; grep '^def test_' "$f" | sort | uniq -d | wc -l
      done
      ```

      Every dupes line must print `0`. The pre-change top-level test counts are 9
      (`test_benchmark_host_provenance.py`), 38 (`test_benchmark_report_markdown.py`) and
      20 (`test_benchmark_report_html_provenance.py`) — each must have **grown**, never
      shrunk.

      Run
      `python -m pytest tests/unit/test_container_endpoint.py tests/unit/test_benchmark_host_provenance.py tests/unit/test_benchmark_report_markdown.py tests/unit/test_benchmark_report_html_provenance.py -q`
      and confirm every test passes. Run `bash scripts/gate.sh` once more on the finished
      change and confirm it exits 0. Confirm `git status --porcelain` is empty after the
      last commit.

      Push with `git push -u origin fix/issue-442-remote-engine-host-refusal` — the branch
      tracks `origin/dev`, so `-u` is required or the push retargets the trunk. Open the
      PR with `gh pr create --repo fasrc/archi --base dev`, and put `closes #442` in the
      PR **body**; a closing keyword in the title does not link the issue. Then stop. Do
      not merge.
