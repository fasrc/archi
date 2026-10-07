# Design — the 16 findings deferred from PR #507 (issue #519)

Read `openspec/changes/fix-issue-492-tar-forcing-guard/design.md` first: decisions D9–D21
define the guard this change edits. The decisions below continue that numbering.

All anchors are `tests/unit/test_service_template_downloads.py` on `origin/dev` `db701852`.

## Baseline (measured 2026-10-07)

- `python -m pytest tests/unit/test_service_template_downloads.py -q` → `104 passed, 18 skipped`.
- The issue's probe (the `cat > /tmp/probe.py` block in issue #519) prints
  `16 of 18 cases give the wrong verdict`. Rows 13 (`LT referer`) and the `FP &&` control
  are already `OK`.
- `_shell_tokens` already drops `#` comments: `_simple_commands('RUN tar -xzf p.tgz # https://download.mozilla.org/')`
  is `[['RUN', 'tar', '-xzf', 'p.tgz']]`. Only the raw `_MOVING_DOWNLOAD.search(command)` at
  `:793` sees the comment.
- `_simple_commands('RUN tar -xzf - </tmp/a')` is `[['RUN', 'tar', '-xzf', '-']]`: the `<`
  target is dropped at `:395-397`.

## D22 — `is_moving` is read from the command's words, not its raw text (row 1)

`is_moving` at `:793` becomes true when any word of any simple command of the command
(`_simple_commands(command)`, so comments are already gone) matches `_MOVING_DOWNLOAD`.
This is a superset of "a parsed `_Download` is moving": it keeps a moving URL fetched by a
program the guard cannot name (`$CURL <moving> | tar -xz`, today indicted by branch 2) in
the net. Reading only `_download_invocations` would drop that case, a regression. The word
reading is the issue's intent (no comment text) without that loss.

## D23 — Write-mode tar has no forcing options and no archive (row 2)

`_parse_tar_span` keeps its 2-tuple return, because tests call it directly (`:1347`). When
the invocation's mode is create, append, update, or concatenate — short `c`, `r`, `u`, `A`
in a cluster before the first option that takes an argument, or `--create`, `--append`,
`--update`, `--catenate`, `--concatenate` — it returns `([], None)`. The `-f` operand of
such a tar is an output, and `-z` on a write compresses; it forces nothing on a read. An
empty `forcing` list makes `_offenders` skip the invocation (`:801`).

Only letters before the first argument-taking option count, so `-xf c` and `-xfc` are
still extractions of an archive named `c`.

## D24 — Traditional option style: a first word of letters is a cluster (row 5)

When the first word of the tar span matches `^[A-Za-z]+$` and contains one of `x`, `c`,
`t`, `r`, `u`, it is an old-style option word. Old style is NOT `-` plus the word: each
letter in `_SHORT_WITH_ARGUMENT` takes the next unconsumed span word, in letter order, so
`tar xzCf /opt /tmp/a` gives `C=/opt` and `f=/tmp/a`, and `tar xzfC /tmp/a /opt` gives
`f=/tmp/a`. The word given to `f` is the archive. Forcing and write-mode letters (D23) are
read from the whole word. Only the first word: a later bare word is an operand. This is the
one-place rule the operator chose on 2026-09-26.

The rule applies only when the command name is literally `tar`. `_parse_tar_span` gains a
keyword `traditional: bool = True`; `_invocations` and `_tar_invocations` pass
`traditional=(name == "tar")`. Reason (refuter, 2026-10-07): for `_UNRESOLVED_PROGRAM`
(`$SUDO tar -xzf /tmp/a`) the span starts with the word `tar`, which matches the pattern
and holds `r`, so D23 would read it as append mode and the reported `['-xzf']` of today
would become `[]`.

## D25 — Shell reserved words are stepped over (row 6)

`_command_name_position` steps over `if then else elif fi do done while until for case
esac { } ( ) !` at the command position, the same way it steps over assignments and
wrappers. The wrapper rule of D19 is unchanged. `time` is NOT in the set: it is already in
`_TRANSPARENT_WRAPPERS`, whose branch also skips its `-p` flag; a reserved-word check that
ran first would make `-p` the command in `time -p tar -xzf /tmp/a` (refuter, 2026-10-07,
measured `['-xzf']` → `[]`). Stepping over `for` or `case` makes the loop variable or the subject the
"command name"; it is not a known program, so nothing is reported for it.

## D26 — `sh -c --` and stdin redirection (rows 15 and 16)

Row 15: after the `-c` slice at `:548`, `if script and script[0] == "--": script = script[1:]`.

Row 16: `_simple_commands` keeps the target of a plain `<` or `0<` redirection as a tagged
word — a `str` subclass, like `_Operator` — at its place in the argv. Only tar reads it:
`_named_commands` removes tagged words from the arguments of every command that is not
`tar` or `_UNRESOLVED_PROGRAM`, so the download pairing (`_parse_download`) and the command
position never see one. `_parse_tar_span` ignores a tagged word during its scan; at the end,
when the archive is `None` or `-`, the stdin target becomes the archive. Other redirections
(`>`, `2>`, `<<`, `<&`) are dropped as today.

Consequence, intended: `tar -xz </tmp/pinned` in a moving command was indicted as
unresolvable; it now resolves to `/tmp/pinned` and is clean when that path is not moving.

## D27 — Heredoc bodies are not parsed as shell (row 3)

`_commands` joins backslash continuations as today, then walks the lines. Heredoc openers
are found from the TOKENS of the line, never from its raw text: each `<<` or `<<-` operator
token of `_shell_tokens(line)` (not `<<<`) and the word token after it, quotes stripped, is
one opener. So a `#` comment line, a quoted `'<<EOF'`, and arithmetic `$((1<<2))` open
nothing (refuter, 2026-10-07: a raw-text regex dropped every later line for those). A line
that does not tokenize opens nothing here; `_offenders` reports it as `unparseable` as
today. The lines after an opener, up to and including the line that is exactly `WORD`
(leading tabs stripped for `<<-`), are dropped. Several openers on one line are closed in
order. The opening line stays a command.

An unterminated heredoc fails closed: `_offenders` reports
`unparseable command '<opening line>': unterminated heredoc` and the lines after the
opener are not read. To carry that verdict, `_commands` may return the unterminated
opener in a form `_offenders` recognises (for example a second return value); the exact
shape is the implementer's choice.

**Accepted limit, new:** a heredoc that IS the `RUN` script (`RUN <<EOF` … `EOF`) is a
shell script in BuildKit, and its body is no longer read. The issue sets the prose body of
`RUN <<EOF` as a false positive to fix, so the body cannot be read as shell. A test pins
that `RUN <<EOF` / `wget -O /tmp/a <moving>` / `tar -xzf /tmp/a` / `EOF` returns `[]` with
an `accepted limit` comment. No template uses a heredoc today.

## D28 — Provenance resets at `FROM`; `COPY` and `ADD` clear their destination (row 4)

In `_offenders`, a command whose first word is `FROM` (case-insensitive) starts a new
stage with an empty `provenance`. The finished stage's dict is kept, keyed by its index
(0, 1, …) and by its `AS <name>` (case-insensitive), for `COPY --from`.

A command whose first word is `COPY` or `ADD` sets each destination path in the current
`provenance`. Destination: the last word that is not a `--flag`, or, for the JSON exec form,
the last element of the array. When the destination ends in `/`, each destination path is
the destination joined with the basename of its source. The moving flag of a destination:

- `COPY --from=<earlier stage>` (by name or index): the flag that stage held for the source
  path (`False` when it held none). Refuter, 2026-10-07: clearing here turned the caught
  `COPY --from=one /tmp/a /tmp/a` of a moving download into `[]`.
- Any other `COPY` or `ADD`, and `--from=` naming an external image: `False` (cleared).

## D29 — Rows 7–14 are pinned at today's verdict (accepted limits)

One test per row, each named for the limit and carrying an `# accepted limit (#519 row N)`
comment, asserts today's `_offenders` value. Rows 7–12 and 14 pin `[]`. Row 13 (curl
`--referer`) pins today's `['-xzf']`: the issue lists it as latent, and the whole-command
fallback already reports it. A later change that closes a limit flips its test, a visible
diff.

**Row 12 reading.** The issue says both "Record rows 7–14 as accepted limits … pins
today's `[]` verdict" (with the acceptance criterion "rows 7–14 still print `got=[]`") and
"Row 12: keep a path marked moving once any write on the command was moving; document
it". A cross-invocation "once moving, stays moving" rule would flip row 12 to forcing and
fail that criterion, and it would also make `wget -O /tmp/a <moving> && wget -O /tmp/a
<pinned>` a new false positive, against option 3's first rule. This change takes the
reading that satisfies every acceptance criterion: across download invocations the last
write wins (D17, unchanged), so row 12's `||` case stays `[]` and is pinned; inside ONE
wget invocation `-O` is already moving when any URL is (D18), which the spec scenario
states as "once moving, stays moving" for that scope. The PR body MUST name this reading
so the reviewer can overturn it.

## D30 — Not changed

- The `unparseable command …` message (rows 3's second symptom) keeps its text.
- A create-mode tar does not clear a moving path it overwrites (D23 returns no archive). A
  test pins `wget -O /tmp/a <moving>` / `tar -czf /tmp/a /opt` / `tar -xzf /tmp/a` as
  reported, with an `accepted limit` comment: conservative, no template does it.

## Ultracode pre-implementation check — 2026-10-07

Three independent refuters (assumptions-vs-code, failure-modes, missed-cases) read this
change against the guard on `db701852` before implementation. Nine findings, all fixable
from the issue body, are folded in: the row-6 "live trigger" wording (proposal), D24
old-style argument order and its restriction to a literal `tar`, D25 without `time`, D27
token-based heredoc openers and a fail-closed unterminated heredoc, D28 `COPY --from`
provenance, task 1.2(d) quoting, and two PR-body checks in task 8.1. None needed a human
decision. The row-12 reading of D29 was checked and not refuted.
