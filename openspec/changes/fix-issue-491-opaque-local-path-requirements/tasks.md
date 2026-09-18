## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-491-opaque-local-path-requirements` exists, cut from
      `origin/dev` at `4b253e26`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-491-opaque-local-path-requirements --strict` passed
      on the host. The `openspec` CLI is **not installed in this container** — do not run
      it, and do not add a task that does. It is already green.
- [x] 0.3 The red was measured at `4b253e26`: all five target lines print
      `opaque=False`, and the file's baseline is **133 passed, 0 skipped, in 0.15s**. The
      candidate pattern in design D1 was measured against 9 positives, 9 negatives and all
      five monitored files (327 requirement lines, zero matches). The design is known
      reachable — you are not searching for an approach.

## 1. Close the hole (TDD, red and green in this one task)

- [ ] 1.1 Write the new cases red, then make them green — **both in this one task**,
      because `scripts/gate.sh` runs before every commit and a task that ends with the
      suite red can never be committed. All work is in
      `tests/unit/test_base_image_dependency_compatibility.py`. **No `src/` file changes
      in this change.**

      **Red first.** Add a parametrized test to `TestOpaqueRequirementsFailClosed`
      (`:1448`), beside `test_a_vcs_or_url_requirement_is_reported` (`:1467`). Name it
      something not already used in the file — `grep -n 'def test_' ` the file first,
      because the gate runs no linter and a duplicate `def test_...` silently deletes the
      earlier test while staying green. Cover at least these lines, asserting
      `_opaque_requirements(f"torch==2.7.0\n{line}\n")` is truthy for each:
      `./local_vllm`, `../pkgs/vllm`, `/opt/vllm.whl`,
      `vllm-0.9.0-py3-none-any.whl`, `dist/vllm-0.9.0.tar.gz`, `.\win_vllm`,
      `vllm-0.9.0.tar.bz2`, `vllm-0.9.0.zip`.
      Add a second parametrized test for the dotted-name negatives that must stay clean:
      `backports.tarfile==1.2.0`, `zope.interface==5.4.0`, `ruamel.yaml==0.18.6`,
      `langgraph-prebuilt<1.0.9` (this one is live in
      `requirements/requirements-base.txt`).
      **When you insert, check the diff's trailing context line** — appending inside an
      existing test body instead of after it silently steals that test's last assertion.
      `git diff` the file and confirm the previous test still ends with its own `assert`.
      Run `python -m pytest tests/unit/test_base_image_dependency_compatibility.py -q
      --no-header` and confirm the 8 positive cases FAIL on the assertion — not on an
      import or collection error — and the negative cases already pass.

      **Then green.** Add a module-level compiled sibling pattern next to
      `_VCS_OR_URL_REQUIREMENT` (`:333-335`) per design D1, and have
      `_opaque_requirements` (`:338-347`) report a line matching **either** pattern.
      Do **not** change `_REQUIREMENT_PATTERN` (`:273`) — design D2 explains why widening
      the name reader is the wrong direction. Anchor the archive-suffix clause on the
      right (`(?:\s|;|$)`) or `backports.tarfile==1.2.0` will false-positive.
      Update `_opaque_requirements`' docstring to name every shape it now reports (VCS,
      URL, `file://`, drive letter, local path, archive) and to say it reports a line
      rather than resolving a name from it, dated `2026-09-18`.

      **Prove it end-to-end** (issue acceptance criteria 1, 3 and 4). Run this probe and
      keep its output for the PR body:

      ```
      python - <<'PY'
      import importlib.util
      from pathlib import Path
      spec = importlib.util.spec_from_file_location(
          "g", "tests/unit/test_base_image_dependency_compatibility.py")
      m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
      for line in ("./local_vllm", "../pkgs/vllm", "/opt/vllm.whl",
                   "vllm-0.9.0-py3-none-any.whl", "dist/vllm-0.9.0.tar.gz"):
          print(f"{line!r:32} opaque={bool(m._opaque_requirements(f'torch==2.7.0\n{line}\n'))}")
      gpu = m.GENERATED_GPU_REQUIREMENTS.read_text(encoding="utf-8")
      print("gpu + ./local_vllm ->", m._opaque_requirements(gpu + "./local_vllm\n"))
      for f in ("requirements/cpu-requirementsHEADER.txt",
                "requirements/gpu-requirementsHEADER.txt",
                "requirements/requirements-base.txt",
                "src/cli/templates/dockerfiles/base-python-image/requirements.txt",
                "src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt"):
          print(f, "->", m._opaque_requirements(Path(f).read_text(encoding="utf-8")))
      PY
      ```

      All five lines must print `opaque=True`; the planted GPU text must report a
      non-empty list; all five live files must report `[]`.

      **Then commit.** Run `bash scripts/gate.sh` **bare — no pipe, no redirect** (it
      refuses to run when its output is piped or redirected). Format the file before
      `git add`, and confirm `git status --porcelain` is empty after the commit — the
      pre-commit hook's black is a writer, so a commit can otherwise be pushed
      misformatted and redden CI. The file's suite must end with **more than 133** tests
      passed and 0 skipped. Expect `diff-cover` to print *"No lines with coverage
      information"*: this diff is tests-only plus OpenSpec markdown, so there are no
      measurable `src/` lines. That is a legitimate pass, not a bypassed gate. Never
      `--no-verify`. No `Co-Authored-By` or session trailer on the commit.

## 2. Ship it (no merge)

- [ ] 2.1 Push with `git push -u origin fix/issue-491-opaque-local-path-requirements`.
      The `-u` matters: the branch was cut from `origin/dev` and still tracks the trunk
      until you repoint it. Then confirm the push landed by comparing
      `git rev-parse HEAD` with
      `git ls-remote origin refs/heads/fix/issue-491-opaque-local-path-requirements` —
      "Everything up-to-date" is not proof, and a remote that is behind local HEAD means
      the push failed.

- [ ] 2.2 Open the PR: `gh pr create --repo fasrc/archi --base dev`. The **body** MUST
      contain `Closes #491` — a closing keyword in the *title* does not create the link.
      Verify the link afterwards with the GraphQL `closingIssuesReferences` field; do not
      infer it from the body text. The body must also record:
      the red-then-green evidence from 1.1 (the 8 failing cases, then the probe output);
      the before/after test count for the file (133 → your number);
      that all five monitored files report `[]`, so there is no false positive on a real
      input;
      that `_REQUIREMENT_PATTERN` was deliberately left alone, and why (design D2);
      that PEP 508 direct references (`vllm @ https://…`) are out of scope because
      `_unpinned_protected` already fails them closed (design D4);
      and that `diff-cover` reports no measurable lines because the diff is tests-only.
      **Never merge** — a human merges in daylight.
