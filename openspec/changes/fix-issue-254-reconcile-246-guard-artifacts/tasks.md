## 1. Reconcile the guard-design statements

Every task in this group edits Markdown only. There is no red step to run, and no source or
test file changes: `tests/unit/test_requirements_hygiene.py` on `dev` is already the shipped
guard, and it is the reference the reconciled text must match. Read it first — its
`DUCKDB_PIN_PATHS` list, `requirement_project_name()`, `declares_unreadable_requirement()`
and `test_guard_monitors_every_requirements_file_the_generator_touches()` are the four things
the artifacts must describe. Each task ends in its own commit.

- [x] 1.1 Reconcile decision D3 of
      `openspec/changes/fix-issue-246-remove-dead-duckdb-pin/design.md` (the matching
      rationale, around `:89-104`). Replace the literal-pattern claim — currently "the guard
      matches `^\s*duckdb([=<>!~ ]|$)` per line, which is deliberately the same shape as the
      filter at the loop image's container definition" — with the shipped mechanism: the guard
      reads the project name a requirement line declares, normalizes it per PEP 503 (case
      folded, runs of `-`, `_` and `.` folded to a single `-`) and compares it to `duckdb`.
      Keep both properties D3 already argues for, because both still hold, and say why they
      now hold: a reintroduction at any version or specifier is caught (the version string is
      never consulted), and `duckdb-engine` is not a false positive (it normalizes to a
      different project name). Add the three shapes the literal pattern missed and the
      normalized comparison catches: `DuckDB==1.0`, `duckdb[httpfs]==1.0`, and
      `duckdb; python_version >= "3.11"`. Then replace the closing "agrees character for
      character" paragraph per design D2 of this change: the guard is deliberately stricter
      than the loop image's duckdb-stripping filter — that filter's character class stops at
      `[=<>!~ ]` and would pass `duckdb[httpfs]==1.0` through — and the guard fails the suite
      before such a declaration can reach an image build, so the day the filter is deleted the
      guard is already enforcing the stronger condition. Do **not** edit the filter or the
      container definition, and do not touch D4, which correctly records it as a human
      follow-up. Commit.
- [x] 1.2 Reconcile decision D5 of the same `design.md` (the collection behaviour, around
      `:116-124`). It currently says the guard collects offenders "across all three files" and
      that the red step "must report exactly three hits on `origin/dev`". Restate it over the
      five monitored paths from `DUCKDB_PIN_PATHS`, naming them: `requirements/
      requirements-base.txt`, `requirements/cpu-requirementsHEADER.txt`,
      `requirements/gpu-requirementsHEADER.txt`, and the two
      `src/cli/templates/dockerfiles/base-*-image/requirements.txt`. Keep D5's actual
      argument — assert on the collected list so one red run names every offender instead of
      turning one merge into a sequence of red runs — and keep the red-step evidence claim
      accurate: on `origin/dev` before the pin was deleted, three of the five monitored files
      declared it, so the red run reported three hits out of five monitored paths. Say why the
      two header files are monitored even though they never carried the pin: the generator
      concatenates them ahead of `requirements-base.txt`, so a declaration added to a header
      would leave the tracked outputs green until the next base-image build installed it.
      Commit.
- [x] 1.3 Reconcile task 1.1 of
      `openspec/changes/fix-issue-246-remove-dead-duckdb-pin/tasks.md` (`:3-22`). Replace the
      three-path module-level mapping with the five shipped paths, still resolved from
      `REPO_ROOT = Path(__file__).resolve().parents[2]`. Replace "One test scans every line of
      each file against `^\s*duckdb([=<>!~ ]|$)`" with the shipped shape: a helper resolves
      each line's PEP 503-normalized project name and the scan collects every line whose name
      is `duckdb`. Keep everything that is still correct and load-bearing: the
      write-red-then-green-in-one-task instruction and its reason (the gate runs before every
      commit, so a task that ends red can never be committed), collecting offenders as
      `path:line` strings and asserting on the list, the module and test docstrings explaining
      that upstream still carries the pin, the no-module-level-skip and no-git-call
      constraints, the `sed -i '/^duckdb==0\.8\.1$/d'` deletion over the three files that
      carried the pin, and the do-not-regenerate instruction. Where the old text said the red
      run fails "reporting exactly three hits", say three hits across the five monitored
      paths. Leave tasks 1.2, 1.3, and groups 2 and 3 of that file alone — they are about the
      pin deletion and the three-deleted-lines blast radius, and they are still accurate.
      Commit.
- [x] 1.4 Reconcile the **guard** requirement in
      `openspec/changes/fix-issue-246-remove-dead-duckdb-pin/specs/dependency-pin-hygiene/spec.md`,
      in a commit of its own so it can be dropped alone (design D3 of this change). Only the
      requirement "A reintroduced duckdb pin SHALL fail the test suite" and its scenarios are
      in scope: widen "any of the three shared base requirements files" to the guard's five
      monitored files, and restate its second paragraph — "identify a dependency by
      distribution name rather than by an exact version string" — as comparison of PEP 503
      -normalized project names, which is what makes case, extras and marker variants resolve
      to the same distribution while `duckdb-engine` stays distinct. Update the scenario
      "Every offending file is reported in one run" so it no longer says "more than one of the
      three files", and the scenario "A clean tree passes" so it covers the monitored set.
      **Leave alone**: the first requirement ("The shared base requirements SHALL NOT pin
      duckdb", which names the three files that carried it), the requirement about running in
      every environment — except the phrase "the actual contents of the three files" in its
      first scenario, which is guard scope and becomes the monitored set — and the whole
      "SHALL NOT regenerate the derived requirements files" requirement with its exactly-three
      -deleted-lines scenario. Every `### Requirement:` heading and every requirement's lead
      sentence MUST keep its `SHALL` on the first physical line; `openspec validate --strict`
      reads only that line and a wrapped lead sentence fails. Commit.
- [x] 1.5 Close the two gaps the adversarial review of PR #307 named, both raised because
      the reconciliation stopped short of the four shipped facts it set out to record.
      First, task 1.1 of `openspec/changes/fix-issue-246-remove-dead-duckdb-pin/tasks.md`
      described only the single name-comparison scan, so a maintainer replaying it verbatim
      would build a guard materially weaker than the shipped one: add the fail-closed
      unreadable-requirement predicate and its test, and the both-directions equality
      self-check between the monitored path list and the paths
      `scripts/dev/build_docker_images.sh` references, with the note that the latter is a
      drift alarm rather than a proof of coverage. Second, the guard requirement reconciled
      in 1.4 gains a paragraph and a scenario for each of those two behaviours, since the
      spec is the artifact `openspec archive` promotes into `openspec/specs/` and the
      requirement is the guard's durable contract. Provenance was checked before writing:
      `git log -S` confirms the five monitored paths, the unreadable-shape predicate and the
      generator-path self-check all landed in `5fceb8ae` (PR #251), the issue-246 commit, so
      all four facts belong to this change's artifacts and not to a later guard change.
      Commit.

## 2. Verify against the issue's acceptance criteria

- [x] 2.1 Prove the reconciliation is faithful and confined, then record it. Check each of the
      four criteria of issue #254:
      (a) every guard-design statement now in `design.md`, `tasks.md:1.1` and the guard
      requirement of `spec.md` can be pointed at a line of
      `tests/unit/test_requirements_hygiene.py` — five monitored paths, PEP 503 name
      normalization, the fail-closed unreadable-shape check, and the bidirectional
      generator-path discovery;
      (b) the pin-deletion narrative still reads correctly — `git diff` must show no change at
      `design.md:3`, `design.md:33` or `design.md:64`, and none to the first or last
      requirement of `spec.md`;
      (c) `git diff --name-only origin/dev...HEAD` lists only `.md` files under
      `openspec/changes/` — no source, no test, no requirements file, no control-plane path;
      (d) `git grep -n 'duckdb(\[=<>!~' -- openspec/changes/fix-issue-246-remove-dead-duckdb-pin/`
      prints nothing outside the paragraph that deliberately quotes the strip filter's own
      pattern.
      Then run the project gate bare — no pipe, no redirect, it refuses to run when its output
      is piped — and expect diff-cover to report **no lines with coverage information**,
      because a Markdown-only diff has no measurable `src/` lines. That is a legitimate pass;
      never use `--no-verify`. Finally tick the boxes for the tasks that are done and commit
      that tick, so this task has a commit of its own.
- [x] 2.2 Confirm the change still validates:
      `openspec validate fix-issue-254-reconcile-246-guard-artifacts --strict`, and
      `openspec validate fix-issue-246-remove-dead-duckdb-pin --strict` so the edits above did
      not break the change being reconciled. The `openspec` binary exists on the host but
      **not** inside the loop container, so guard the call —
      `command -v openspec >/dev/null || echo 'openspec unavailable here; validated on the
      host in Loop 1'` — and do **not** halt when it is absent. Both changes were validated
      strict on the host when this proposal was written. Fold this check into task 2.1's
      commit if it produces no file change.

## 3. Ship it (no merge)

- [x] 3.1 Push with `git push -u origin fix/issue-254-reconcile-246-guard-artifacts`. The
      branch was created from `origin/dev` and therefore tracks the trunk until `-u` repoints
      it — check `git rev-parse --abbrev-ref @{u}` names the feature branch afterwards.
- [x] 3.2 Open the PR against `dev`: `gh pr create --repo fasrc/archi --base dev`. Write the
      body to a file and pass `--body-file`; the body MUST contain `closes #254`, because a
      closing keyword in the *title* does not link the issue. Verify the link afterwards with
      the GraphQL `closingIssuesReferences` field rather than assuming it. The body must
      record: that this is a documentation-only reconciliation with no behaviour change, and
      that `tests/unit/test_requirements_hygiene.py` is unchanged; the four shipped facts now
      described (five monitored paths, PEP 503 normalization, fail-closed unreadable-shape
      check, bidirectional generator-path discovery); that the pin-deletion narrative's
      "three files" is intentionally preserved and why; that `spec.md` was edited although
      issue #254 names only `design.md` and `tasks.md`, with the reason (archive promotes
      `spec.md` into `openspec/specs/`) and the note that the edit is isolated in commit 1.4
      and can be dropped alone; that diff-cover reported no measurable `src/` lines because
      the diff is Markdown only, not a skipped gate; and that #253 (PR #298) is in flight and
      extends the guard, which is why the reconciled text describes the guard's contract
      instead of enumerating covered shapes. **Never merge** — a human merges in daylight.

## Verification record (2.1 / 2.2)

Run on `8cad6276` by the nightly reviewer-responder, 2026-08-20.

- **(a) four shipped facts anchored.** `tests/unit/test_requirements_hygiene.py:19`
  (`cpu-requirementsHEADER.txt`, one of the five monitored paths), `:42`
  (`_NAME_SEPARATOR_PATTERN = re.compile(r"[-_.]+")`) with `:98`
  (`.sub("-", name).lower()`) for PEP 503 normalization, `:60`
  (`declares_unreadable_requirement`) for the fail-closed check, and `:207`
  (`test_guard_monitors_every_requirements_file_the_generator_touches`) for the
  bidirectional generator-path discovery.
- **(b) pin-deletion narrative intact.** The spec edit produced three hunks, all inside the
  guard requirement and the one in-scope phrase of the every-environment requirement. The
  first requirement ("SHALL NOT pin duckdb", naming the three files that carried the pin) and
  the last ("SHALL NOT regenerate", with its exactly-three-deleted-lines scenario) show zero
  changed lines.
- **(c) diff confined.** `git diff --name-only origin/dev...HEAD` lists eight paths, all under
  `openspec/changes/`. All are Markdown except
  `fix-issue-254-reconcile-246-guard-artifacts/.openspec.yaml`, the new change's own
  descriptor — the one non-Markdown file, disclosed in the PR body. No source, test,
  requirements or control-plane path appears.
- **(d) literal strip-filter pattern.** Two occurrences remain, both deliberate: `design.md:27`
  and `proposal.md:21`, each quoting the loop image's own `grep -ivE` filter. Neither claims
  the guard uses that pattern.
- **2.2 strict validation.** Both `fix-issue-246-remove-dead-duckdb-pin` and
  `fix-issue-254-reconcile-246-guard-artifacts` report valid under
  `openspec validate --strict` on the host.
- **Gate.** Run bare on the host; exits 0. Diff coverage reports "No lines with coverage
  information in this diff", which is the expected pass for a Markdown-only diff.

3.1 and 3.2 were completed on the original run: the branch is pushed and PR #307 is open with
`closes #254` in its body. The body's "What is NOT done" section is superseded by this
record — group 1 and group 2 are now complete.
