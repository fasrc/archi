# Tasks — scan every tar option and every RUN

Every checkbox below is **one loop turn** and ends **green and committed**. Write the
failing test, watch it fail, write the smallest fix, run `bash scripts/gate.sh`, commit.
Never end a task with the suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope is test files only.** Do not edit `src/cli/templates/dockerfiles/**` — all 15
  templates are already correct, and a diff there means the guard produced a false positive.
  Do not edit `.github/workflows/**`, `deploy/**`, or anything under `src/`.
- **Coverage.** `diff-cover` measures `src/` only, so this diff prints *"No lines with
  coverage information"* and clears `--fail-under=80`. That is expected, not a failure.
  Black and isort **do** enforce `tests/` — keep both clean, and format before `git add`.
- **Do not run `openspec validate` in a task.** The CLI is not installed in the loop
  container. Loop 1 already validated this change on the host.
- **Appending a test is where this file bites.** The last line of
  `tests/unit/test_service_template_downloads.py` is the closing paren of the final
  `assert` in `test_the_two_are_not_paired_across_separate_commands`. Insert new tests
  **after** that line, and re-read the diff's trailing context to confirm you did not
  absorb it. Never reuse an existing `def test_…` name — pytest keeps only the last
  definition and the older test disappears silently, with the gate still green.
- **Three tests are frozen by the issue's acceptance criteria** and must stay green **and
  byte-identical**: `test_auto_detection_is_left_alone` (`:145`),
  `test_the_two_are_not_paired_across_separate_commands` (`:180`), and
  `TestAVersionedDownloadMayForceItsFormat` (`:153`). New negative cases go in **new** test
  methods, never by extending those parametrize lists.
- **Test-count floor.** The baseline for this file alone at `4b253e26` is
  **26 passed, 18 skipped**. Passed may only rise. The 18 skips are the 9 templates that
  fetch no versionless archive, counted twice; that number may rise, never fall.

## 1. Hole A — every forcing option, wherever it sits

- [x] 1.1 `model: opus` — Replace the regex with a two-step scan. RED first: add these six
      commands to the parametrize list of `test_a_forced_format_is_detected` inside
      `TestTheGuardRejectsEveryForcedDecompressor` (`:111`) — `tar -x --gzip -f /tmp/f`,
      `tar -x -z -f /tmp/f`, `tar --lzip -xf /tmp/f`, `tar --uncompress -xf /tmp/f`,
      `tar -I zstd -xf /tmp/f`, `tar --use-compress-program=zstd -xf /tmp/f`. Run the file
      and **watch all six fail**. Then implement a module-level
      `_forced_decompressors(command: str) -> list[str]` that: splits the command into
      tokens; for each `tar` token, reads the following tokens until a shell separator
      (`&&`, `||`, `;`, `|`) or the end; and collects any option token (one starting with
      `-`) that forces a program. Long forms matched **whole**: `--gzip`, `--gunzip`,
      `--ungzip`, `--bzip2`, `--xz`, `--lzma`, `--lzop`, `--zstd`, `--lzip`, `--compress`,
      `--uncompress`, `--use-compress-program` (with or without `=PROG`). Short forms
      matched inside a cluster and **case-sensitively**: `z`, `j`, `J`, `Z`, `I`.
      **Keep the module-level name `_FORCED_DECOMPRESSOR`, bound to an object exposing
      `.findall(command) -> list[str]`** that runs the new scan — a four-line class with a
      single method is enough. Do **not** delete the name and rewrite the call sites. All
      four call sites (`:72-77`, `:118`, `:141`, `:186`) already call
      `_FORCED_DECOMPRESSOR.findall(command)`, and **two of them are inside frozen tests**
      that acceptance criterion 5 requires to stay byte-identical: `:141` is in
      `test_auto_detection_is_left_alone` and `:186` is in
      `test_the_two_are_not_paired_across_separate_commands`. Rewriting those call sites
      would fail the issue. Keeping the name also keeps the issue's own probe
      (`t._FORCED_DECOMPRESSOR.findall(c)`) working verbatim, which acceptance criteria 1
      and 2 depend on. Update the comment at `:33-36`, which claims the old pattern covered
      every option, to describe the new scan. Gate green; commit.
- [x] 1.2 `model: sonnet` — Negative guards, in a **new** test method beside
      `test_auto_detection_is_left_alone` (do not extend that method's parametrize list —
      it is frozen). Assert `_forced_decompressors` returns empty for: `tar -a -xf /tmp/f.tar.gz`,
      `tar --auto-compress -xf /tmp/f`, `tar --no-auto-compress -xf /tmp/f`,
      `tar -xif /tmp/f.tar`, and `tar --exclude=*.gz -xf /tmp/f`. Each is a near-miss a
      careless widening breaks: `--auto-compress` selects by suffix instead of forcing,
      `--no-auto-compress` is the wrong-boundary match for a long form containing
      `compress`, `-i` is `--ignore-zeros` and differs from the forcing `-I` by case alone,
      and `--exclude=*.gz` contains `gz` in a token that is not a compression option.
      **These pass once 1.1 is correct — that is the point of them. Do not contrive a
      failure first.** If any fails, 1.1's matching is too wide; fix 1.1's rule, not the
      test. Gate green; commit.
- [x] 1.3 `model: sonnet` — Invocation-boundary guard, a new test method: assert
      `_forced_decompressors("tar -xf /tmp/f.tar.xz -C /opt/ && gzip -d /tmp/other.gz")`
      is empty, and that
      `_forced_decompressors("tar -xf /tmp/a.tar && tar -xzf /tmp/b.tar.gz")` reports
      exactly one offender. The scan must stop at the separator, so a neighbouring command
      can neither supply a forcing option to a correct `tar` nor mask a second `tar` that
      has one. Gate green; commit.

## 2. Hole B — association by the saved path

- [x] 2.1 `model: opus` — RED first, a new test method: build the split-`RUN` text
      `RUN wget -O /tmp/ff.tar "https://download.mozilla.org/?product=firefox-esr-latest-ssl&os=linux64"`
      followed by `RUN tar -xjf /tmp/ff.tar -C /opt`, and assert the guard reports a
      non-empty offender list naming `/tmp/ff.tar`. **Watch it fail** — today `_commands`
      makes these two separate logical commands and the per-command pairing reports
      nothing. Then implement: a `_saved_paths(text) -> set[str]` reading `wget -O <path>`,
      `curl -o <path>`, and `curl --output <path>` from each command matching
      `_MOVING_DOWNLOAD`; and an `_offenders(text) -> list[str]` deciding each forcing `tar`
      invocation by the **three branches** in `design.md`: (1) a known saved path appears in
      the invocation's **own token span** → report, wherever in the file it sits; (2) the
      invocation shares a command with a moving download **and** the guard cannot tell which
      file it reads — no saved path recorded, or the span holds a `$` variable or a stdout
      sink → report; (3) otherwise → clean. Strip surrounding quotes before comparing. Match
      the saved path as written **anywhere in the span** (so `--file=<path>` and
      `-xjf<path>`, which glue the archive to its flag, are caught), and match its basename
      **only** against a token carrying no `/`. Never record `-`, `/dev/stdout` or
      `/dev/null` as a saved path. Read the "three details in branch 1" list in `design.md`
      first — each of those three was measured to be a **regression** against today's guard
      if done the simpler way. Route **only** the template test at `:66`
      (`test_a_moving_download_is_not_extracted_with_a_forced_decompressor`) through
      `_offenders`. **Do not touch the test at `:89`** — that is
      `test_the_saved_filename_does_not_claim_a_format_it_cannot_guarantee`, which asserts a
      `.tar.bz2` naming claim, never calls the decompressor check at all, and would be
      turned into a duplicate of `:66` by a literal rewrite. Gate green; commit.
- [x] 2.2 `model: opus` — The false-positive guard that makes 2.1 safe. New test method:
      one `RUN` reading
      `wget -O /tmp/ff.tar.xz "<moving url>" && tar -xf /tmp/ff.tar.xz -C /opt && tar -xzf tool-v1.2.3.tar.gz -C /usr/local/bin`
      must report an **empty** offender list. The saved path is extracted correctly; the
      forced `tar` beside it operates on a different, version-pinned file. Only the operand
      distinguishes them, so only the operand may decide. **Watch this fail if 2.1 asked
      whether the saved path appears anywhere in the command rather than binding it to the
      `tar` invocation's own operands** — that is the likely first implementation, and this
      test is what catches it. Note this case is reported by the guard as it stands **today**
      (the whole-command rule reaches it), so branch 1 deliberately narrows the guard here;
      that narrowing is specified, and no live template is affected. Then add four more new
      test methods, one per regression the first prototype of this rule actually had —
      each is reported by the guard **today**, so each must still be reported:
      `wget -O- "<url>" | tar -xzf -` **and** the spaced `wget -O - "<url>" | tar -xzf -`
      (branch 2; the spaced form is the one that breaks a naive saved-path reader);
      `tar --bzip2 --file=/tmp/ff.tar.xz` and `tar -xjf/tmp/ff.tar.xz` (branch 1, glued);
      `tar -xjf "$FF"` sharing a `RUN` with the download (branch 2, unresolvable);
      and `cd /tmp && tar -xjf ff.tar.xz` in a later `RUN` (branch 1, bare basename).
      Add one negative alongside them: a later `RUN` reading
      `tar -xJf /opt/vendor/pinned-9.9.9/firefox-esr.tar.xz` must report **nothing**, even
      though its basename matches the saved `/tmp/firefox-esr.tar.xz`. Gate green; commit.
- [ ] 2.3 `model: sonnet` — Confirm the three frozen tests and the live templates. Run
      `python -m pytest tests/unit/test_service_template_downloads.py -q --no-header` and
      confirm: `test_auto_detection_is_left_alone`,
      `test_the_two_are_not_paired_across_separate_commands` and
      `TestAVersionedDownloadMayForceItsFormat` are green, `git diff --stat` shows **no**
      change under `src/cli/templates/dockerfiles/`, passed is **above 26**, and skipped is
      **at least 18**. Also add a new test method asserting a moving download with **no**
      `-O`/`-o` still gets the per-command rule and indicts no other command — the guard
      must not guess a saved path it cannot read. Gate green; commit.

## 3. Correct the stale #473 claims

- [ ] 3.1 `model: sonnet` — Docstring and comment text only, no assertion changes.
      **CAUTION — issue #492's "One more thing" section prescribes wording that is itself
      wrong. Do not copy it.** It says to change the module docstring
      (`tests/unit/test_service_template_downloads.py:20`) to say CI builds service images
      *post-merge only*, citing `publish-base-images.yml`. That workflow builds the **base**
      images, not the service templates, and a **pre-merge** job does build service images:
      `pr-preview.yml` runs `on: pull_request` and its smoke stage brings up a rendered
      stack with `--build`. Measured on 2026-09-18. Write the accurate claim instead: a
      pre-merge job builds only the `chatbot` slice — `Dockerfile-chat`,
      `Dockerfile-postgres`, `Dockerfile-data-manager` — and **none of the six templates
      that fetch this download** is in that slice; they are `Dockerfile-grader`,
      `Dockerfile-grader-gpu`, `Dockerfile-chat-gpu`, `Dockerfile-data-manager-gpu`,
      `Dockerfile-mattermost-gpu`, and `Dockerfile-benchmarks-gpu`. The docstring's
      conclusion — nothing caught this — stands; only its reason was wrong. Then drop the
      stale `#473` forward-references in
      `tests/unit/test_base_image_dependency_compatibility.py` at `:7`, `:53`, `:211` and
      `:513` — #473 is CLOSED as NOT_PLANNED, so "#473 asks CI for the last two" and "the
      one image no pre-merge job builds (#473)" both point at a closed issue. Fix all four
      or none; a half-corrected file is worse than an uncorrected one. Gate green; commit.

## 4. Close out

- [ ] 4.1 `model: sonnet` — Re-run the issue's probe and record the result in the PR body.
      Handle the three probe blocks differently; do **not** rewrite the probe globally.
      - **Hole-A block: run it verbatim.** It calls `t._FORCED_DECOMPRESSOR.findall(c)`,
        and task 1.1 deliberately kept that name bound to an object exposing `.findall`, so
        no edit is needed. Expect `FLAGGED` for all six previously-missed forms and `MISSED`
        for `tar -xf f.tar.xz`.
      - **Hole-B block and the template block: replace the comprehension with
        `_offenders(text)`.** Their comprehension filters commands by
        `_MOVING_DOWNLOAD.search(c)` *before* checking the decompressor, and the split-`RUN`
        line `RUN tar -xjf /tmp/ff.tar -C /opt` does not match that filter. Swapping in a
        different decompressor helper while keeping that filter still returns `[]` and would
        fail acceptance criterion 2 — the very thing this task exists to confirm. Only
        `_offenders` exercises branch 1.
      Confirm all three acceptance blocks: the six forms flagged and `tar -xf f.tar.xz`
      still clean; a **non-empty** offender list for the split-`RUN` input; and an **empty**
      list for all 15 templates. Gate green; commit (a docs-only commit is fine if no code
      changed).
- [ ] 4.2 `model: sonnet` — Run `bash scripts/gate.sh` once more on the finished change and
      confirm it exits 0. Confirm `git status --porcelain` is empty after the last commit.
      Push with `git push -u origin fix/issue-492-tar-forcing-guard` — the branch tracks
      `origin/dev`, so `-u` is required or the push retargets the trunk. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`. Put `Closes #492` in the **body** — a
      closing keyword in the title does not link the issue — and include the probe output
      from 4.1 and a note that the docstring wording in 3.1 deliberately departs from the
      issue's prescribed text, with the reason. Then **stop. Do not merge.**
