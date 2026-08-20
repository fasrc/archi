## 1. Guard the pin, then delete it (TDD)

- [x] 1.1 Write the guard red, then make it green — **in this one task**, because the gate
      runs before every commit and a task that ends with the suite red can never be
      committed. First create `tests/unit/test_requirements_hygiene.py` with a module-level
      mapping of the five paths, resolved from
      `REPO_ROOT = Path(__file__).resolve().parents[2]`:
      `requirements/requirements-base.txt`,
      `requirements/cpu-requirementsHEADER.txt`,
      `requirements/gpu-requirementsHEADER.txt`,
      `src/cli/templates/dockerfiles/base-python-image/requirements.txt`,
      `src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt`. A helper resolves
      each line's PEP 503-normalized project name (case folded, runs of `-`, `_` and `.`
      folded to a single `-`); the scan collects every line whose normalized name is `duckdb`
      as `path:line` strings, and asserts the collected **list** is empty so the failure
      names all of them at once (design D5). The name comparison alone would fail open, so
      the module needs two more protections beside it — both shipped, both required:
      (a) a companion predicate that reports a line whose project name cannot be read at
      all — a bare wheel URL, a `git+https://…#egg=duckdb`, a local path — with its own test
      asserting no monitored line is of that shape, so such a line fails the suite instead of
      passing as "not duckdb"; and (b) a self-check that the monitored path list and the
      requirements paths `scripts/dev/build_docker_images.sh` references are **equal in both
      directions**, not merely a subset, because a subset check would let coverage evaporate
      silently if the generator were restructured beyond the discovery regex. Give the module
      and the test docstrings stating why the guard exists: upstream still carries this pin,
      so an upstream merge would otherwise reintroduce it silently, and state that (b) is a
      drift alarm rather than a proof of coverage since it cannot see paths built from
      variables or command substitution. **No module-level `pytest.mark.skipif`, and no git
      call** — that is the whole reason this is not in `test_repo_hygiene.py` (design D2).
      Run `python -m pytest tests/unit/test_requirements_hygiene.py -x -q` and confirm it
      **FAILS reporting exactly three hits across the five monitored paths** — one per file
      that declared the pin, on the assertion, not on an import or a missing path. Then
      delete the pin, one line per file, with `sed -i '/^duckdb==0\.8\.1$/d'` over the
      three paths (design D1) — do **not** run `scripts/dev/build_docker_images.sh` and do
      **not** otherwise regenerate the two derived files. Re-run the guard: green.
- [x] 1.2 Prove the blast radius mechanically before committing, because "I was careful" is
      not the check — regeneration is (design D1):
      `git diff --name-only` lists exactly the three requirements files;
      `test "$(git diff -U0 | grep -c '^-[^-]')" = 3` passes;
      `git diff -U0 | grep -c '^+[^+]'` is 0 (no added lines);
      `git diff -- Containerfile` is empty (design D4).
      Any deleted-line count above three means the derived files were regenerated and #247's
      drift got swept in — revert and redo surgically.
- [x] 1.3 Confirm the removal is complete and nothing else referenced the pin:
      `git grep -n duckdb -- requirements/ src/cli/templates/dockerfiles/` prints **nothing**,
      and repo-wide `git grep -ln duckdb` now lists only the container definition.

## 2. Verify against the issue's acceptance criteria

- [ ] 2.1 Run `bash scripts/gate.sh` **bare — no pipe, no redirect** (it refuses to run when
      its output is piped or redirected). Format, lint, tests, and the ≥80% diff-coverage
      threshold must all pass. Never `--no-verify`. Expect diff-cover to report **no lines
      with coverage information**: the `src/` paths in this diff are `.txt` files with no
      executable lines, and the remainder is the new test. That is a legitimate pass, not a
      bypassed gate.
- [ ] 2.2 Run `openspec validate fix-issue-246-remove-dead-duckdb-pin --strict` and confirm
      it passes.
- [ ] 2.3 Evidence the red-then-green claim for the PR body: check the three requirements
      files out from `origin/dev` (`git checkout origin/dev -- requirements/
      src/cli/templates/dockerfiles/`), run the guard and capture the **failing** output with
      its three named hits, then restore the branch versions (`git checkout HEAD --` the same
      paths) and capture the **passing** output. Both runs get quoted in the PR body. Confirm
      `git status --porcelain` is clean afterwards.

## 3. Ship it (no merge)

- [ ] 3.1 Push with `git push -u origin fix/issue-246-remove-dead-duckdb-pin` — the branch was
      created from `origin/dev` and so tracks the trunk until `-u` repoints it.
- [ ] 3.2 Open the PR against `dev`: `gh pr create --repo fasrc/archi --base dev`. The body
      MUST contain `closes #246` — a closes-keyword in the *title* does not link the issue.
      The body must also record: the red-then-green evidence from 2.3; that diff-cover
      reports no measurable `src/` lines because this is a requirements + test change, not a
      skipped gate; that **merging republishes the base images**, because
      `requirements/requirements-base.txt` matches the change-detection `PATTERN` in the
      base-image publish workflow; that the duckdb-stripping filter in the container
      definition is now redundant and is deliberately left for a human (design D4); and that
      upstream still carries the pin, which is what the new guard defends against.
      **Never merge** — a human merges in daylight.
