# Tasks — digest-aware base-reference rewriting

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail, write the smallest fix, run `bash scripts/gate.sh`, commit. Never end a
task with the suite red, and never use `--no-verify`.

Two standing notes for every task:

- Coverage: `scripts/gate.sh:146` runs `--cov=src`, so nothing in `scripts/` reports to
  `diff-cover`. Do not chase a coverage number on this file. Black and isort **do** enforce
  `scripts/` and `tests/` — keep both format-clean.
- Scope: do not touch `.github/workflows/**`, `deploy/**`, the Dockerfile templates, or
  `src/cli/managers/base_image_preflight.py`.

## 1. Parse digest references

- [x] 1.1 `model: opus` — Create `tests/unit/test_update_service_base_images.py`. Load the script with
      `importlib.util.spec_from_file_location`, copying the loader shape at
      `tests/unit/test_validate_queries_script.py:28`. Add a helper that writes a fixture
      Dockerfile into `tmp_path` and monkeypatches the module's `DOCKERFILES_DIR` to that
      directory. RED test: a fixture line reading
      `FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<64 hex>` (no trailing comment), run
      with `--tag pr-7 --switch-source ghcr --orig-tag all`, must become
      `FROM ghcr.io/fasrc/a2rchi-python-base:pr-7`. Watch it fail. Then make
      `_split_image_spec` `rsplit("@", 1)` the digest off before the existing `:` split and
      return it as a fourth component, and update `_update_line` to unpack four values.
      Gate green; commit.
- [x] 1.2 `model: sonnet` — Regression guard for the matching rules: `--orig-tag all` rewrites a digest-pinned
      line, and `--orig-tag dev-4314ac4` leaves that same line byte-identical. **These pass
      once 1.1 lands — that is the point of them. Do not contrive a failure first.** Gate
      green; commit.

## 2. Drop the stale annotation on the way back to a tag

- [x] 2.1 `model: sonnet` — RED test: a fixture line reading
      `FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<64 hex>  # dev-4314ac4` run with
      `--tag pr-7 --switch-source ghcr --orig-tag all` must become
      `FROM ghcr.io/fasrc/a2rchi-python-base:pr-7`, with `dev-4314ac4` gone from the line.
      Watch it fail. Implement: split `_update_line`'s `suffix` group into a leading part
      and a trailing comment at the first `#` that follows whitespace, and drop the comment
      when the source reference carried a digest and the target reference is a tag. Gate
      green; commit.
- [ ] 2.2 `model: sonnet` — RED test: a fixture line reading
      `FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<64 hex> AS builder  # dev-4314ac4`
      rewritten to a tag keeps ` AS builder` and loses the comment. Watch it fail if 2.1's
      split was too greedy; make it green. Gate green; commit.

## 3. Write digest references

- [ ] 3.1 `model: opus` — RED test: `--digest python=sha256:<64 hex> --tag dev-abc1234 --switch-source ghcr
      --orig-tag all` against a tag-pinned python-base fixture must produce
      `FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<64 hex>  # dev-abc1234`. Watch it fail.
      Implement: a repeatable `--digest NAME=sha256:HEX` argparse option parsed into a
      `dict`, a `digests` field on `UpdateOptions` defaulting to empty, a digest output form
      in `_build_image_spec`, and the comment write in `_update_line`. Gate green; commit.
- [ ] 3.2 `model: sonnet` — RED test, two assertions: with `--digest python=sha256:<64 hex>` and no `--tag`,
      the python-base line carries the digest and **no** trailing comment; in the same run a
      pytorch-base fixture line, which has no `--digest` entry, keeps its tag and gains no
      digest. Gate green; commit.
- [ ] 3.3 `model: sonnet` — RED test: re-pinning a line that already carries that exact digest, with a
      different `--tag`, still rewrites the file and still prints `Updated <path>`. Watch it
      fail — today `_update_line` returns unchanged when the image spec matches. Implement:
      compare the rebuilt line against the original line instead of comparing only the image
      spec. Gate green; commit.
- [ ] 3.4 `model: sonnet` — RED test, two cases: `--digest java=sha256:<64 hex>` exits non-zero with a message
      naming `python` and `pytorch`, and `--digest python=deadbeef` exits non-zero; in both
      cases the fixture file on disk is unchanged. Watch them fail. Implement both checks in
      `parse_args`, raising `SystemExit` with an explicit message, mirroring the unknown-base
      idiom at `scripts/dev/update_service_base_images.py:121`. Gate green; commit.

## 4. Round trip and close-out

- [ ] 4.1 `model: sonnet` — RED-or-guard test: take a tag-pinned fixture line, rewrite it to a digest with
      `--digest python=sha256:<64 hex> --tag dev-4314ac4`, then rewrite that result back with
      `--tag dev-4314ac4 --switch-source ghcr --orig-tag all`, and assert the final line
      equals the original line exactly. Gate green; commit.
- [ ] 4.2 `model: haiku` — Run `bash scripts/gate.sh` once more on the finished change and confirm it exits 0.
      Confirm `git status` is empty after the last commit. Push with
      `git push -u origin fix/issue-334-digest-pinned-base-refs` — the branch currently
      tracks `origin/dev`, so `-u` is required. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, put `closes #334` in the **body** (a
      closing keyword in the title does not link the issue), and stop. Do not merge.
