## Context

All anchors are on `origin/dev` `db701852`. `lib.sh:14-20` holds the environment knobs;
`lib.sh:24` is `fm_die` (stderr, exit 2) and `:25` is `fm_log`. The self-test
`test_feature_matrix_wrappers.sh` points knobs (`FM_DOCKER`, `FM_ARCHI`, `FM_GIT`) at stubs
under `$T/bin`, and its stub `archi` makes the corpus drift during `eval qa` when
`$T/drift-after-qa` exists (case 42, around line 505).

## Decisions

1. **Knobs.** `FM_PAGE_MAIL_TO="${FM_PAGE_MAIL_TO:-}"` and `FM_MAIL="${FM_MAIL:-mail}"`
   beside the other knobs, each with a header-comment line. Empty recipient = off.
2. **`fm_page <subject> <body>`.** Return 0 at once when `FM_PAGE_MAIL_TO` is empty.
   Else `printf '%s\n' "$body" | "$FM_MAIL" -s "feature_matrix: $subject" "$FM_PAGE_MAIL_TO"`.
   A failure (non-zero exit, or a missing binary) logs `page failed` to stderr and
   returns 0. It must not trip `set -e` or `pipefail` in the caller.
3. **`fm_die_paged <context> <msg>`.** Calls `fm_page "<context>: <msg>"`-style subject and
   the message (plus any captured reason) as body, then `fm_die "<msg>"`. The context is
   `stack <STACK> arm <ARM>` (arm mode) or `stack <STACK> sweep` / `stack <STACK> arm
   <STEM>` (sweep modes). Exit code stays 2 and the stderr line stays exactly what
   `fm_die` prints today, so existing test assertions keep passing.
4. **Refusal reason in the body (archive_run.sh).** The checks (`fm_sweep_tools archive`
   and the embedded Python heredoc) print the reason to stderr. Capture that stderr to a
   temp file while it still goes to the terminal (for example
   `2> >(tee "$errf" >&2)`, or write to the file and `cat` it to stderr after), then put
   its content in the page body. The terminal output must not change. Remove the temp
   file on exit.
5. **No page on preconditions.** Only the four sites named in the proposal change. Usage
   errors, `fm_require_*`, "still running", "no artifact", and lock mismatches stay
   `fm_die`.
6. **Tests.** Add `export FM_MAIL="$T/bin/mail"` and a stub that appends
   `ARGS: $*` and stdin to `$T/mail.calls`. Keep `FM_PAGE_MAIL_TO` unset for all existing
   cases; set it only inside the new cases (subshell or explicit unset after), so the
   existing 59 cases prove the "off" path. A failing-mail case uses a second stub that
   exits 1 (or `FM_MAIL=$T/bin/nonexistent`).

## Risks

- `set -euo pipefail`: a failing `mail` inside a pipe aborts the caller unless guarded
  with `|| ...`. The failing-mail test is the guard.
- Process substitution `>(...)` can finish after the main shell reads the file. If used,
  read the file only after the command returns, or use the write-then-`cat` form.
