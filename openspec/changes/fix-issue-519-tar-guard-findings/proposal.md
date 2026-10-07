# Close the 16 download-guard findings deferred from PR #507

## Why

`tests/unit/test_service_template_downloads.py` guards the 15 service Dockerfile templates
against a moving download (a URL that names no version, matched by `_MOVING_DOWNLOAD` at
`:576`) extracted with a forced decompressor. PR #507 (squash `1d6beb94`, 2026-09-21)
merged the guard with 16 verified review findings unfixed. The issue's probe still prints
`16 of 18 cases give the wrong verdict` on `origin/dev` `db701852` (measured 2026-10-07;
the guard file is byte-identical to `0ddc96e1`, 1703 lines).

The operator chose **option 3, best-effort net** (2026-09-21, re-confirmed 2026-09-26): fix
the false positives and the reachable bypasses, and pin the rest as accepted limits. Row 6
is the one bypass whose shape is already in templates: `if test -f /tmp/a; then tar -xzf
/tmp/a; fi` returns `[]` because `then` is read as the command name. `if …; then <command>`
is at `Dockerfile-chat:27`, `-data-manager:22`, `-data-manager-gpu:59` (a moving template)
and `-postgres:17`; the command after `then` is an assignment today, so no tar is missed
now, but a tar added there would go unseen. All six moving templates report `[]` today.

## What Changes

The change is tests-only: every edit is inside `tests/unit/test_service_template_downloads.py`.

- **Fix the false positives** (rows 1–4 of issue #519):
  - Row 1: `is_moving` comes from the parsed words of the command, so a URL in a shell
    comment no longer marks the command moving.
  - Row 2: a `tar` in create, append, update, or concatenate mode writes its `-f` archive,
    so it is not read as an extraction.
  - Row 3: `_commands` skips heredoc body lines, so prose and documentation in a heredoc
    are not parsed as shell.
  - Row 4: provenance resets at each `FROM`, and a `COPY` or `ADD` to a path clears that
    path.
- **Fix the bypasses** rows 5, 6, 15 and 16:
  - Row 5: `tar xzf /tmp/a` (traditional option style) is read as `tar -xzf /tmp/a`.
  - Row 6: shell reserved words (`then`, `do`, `if`, …) are stepped over to find the command.
  - Row 15: `sh -c -- 'script'` reads the script after `--`.
  - Row 16: `tar -xzf - </tmp/a` reads `/tmp/a` as the archive.
- **Pin the accepted limits** rows 7–14: one test per row pins today's verdict with an
  `accepted limit` comment, and one spec scenario names each row.
- Two consequences of the fixes are pinned as accepted limits too (design D27): a
  `RUN <<EOF` script body is not read, and a create-mode tar does not clear a moving path.

## Capabilities

### New Capabilities
- `service-template-downloads` (delta only): `openspec/changes/fix-issue-492-tar-forcing-guard/`
  is unarchived and `openspec/specs/` has no `service-template-downloads`, so this delta
  uses `## ADDED Requirements` with requirement names distinct from the #492 change.

### Modified Capabilities
- (none)

## Impact

- `tests/unit/test_service_template_downloads.py` only. No `src/` line changes, so
  diff-cover reports "No lines with coverage information" and the gate passes on tests.
- No template under `src/cli/templates/dockerfiles/` changes. No new dependency.
- The six moving templates keep `_offenders(text) == []`.
- Out of scope: a real shell or Dockerfile parser, `unreadable …` verdicts for rows 7–14,
  and posting the in-thread replies on PR #507 from the loop. The replies need a token that
  can write to `fasrc/archi`, which the loop container does not have. The PR body carries an
  unchecked item "16 thread replies on #507 pending (acceptance criterion of #519)" and the
  row table, so the review stage or a human posts them before merge.
- #508 was closed on 2026-09-26 as a duplicate of #519 (row 5). The PR body mentions it.
