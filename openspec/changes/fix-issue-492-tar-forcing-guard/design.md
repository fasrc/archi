# Design — scanning every tar option and every RUN

## The two-step tar scan (hole A)

One regex cannot do this job cleanly. `_FORCED_DECOMPRESSOR`
(`tests/unit/test_service_template_downloads.py:37-40`) fails because it anchors the option
to the position right after `tar\s+`, and the obvious repair — allowing anything between
`tar` and the option — is what turns the check into a file-wide scan that condemned the
geckodriver line once already.

Split it in two instead:

1. **Find each `tar` invocation.** Walk the command's tokens. A `tar` token starts an
   invocation; the invocation ends at the next shell separator (`&&`, `||`, `;`, `|`) or at
   the end of the command.
2. **Inspect that invocation's option tokens.** A token beginning with `-` is an option
   token. Anything else is an operand and is never consulted, which is what keeps
   `tar -xf f.tar.xz` clean despite its `.xz` operand.

An option token forces a decompressor when it is a long form in the forcing set, or a short
cluster containing `z`, `j`, `J`, `Z`, or `I`. Long forms are matched whole — `--gzip`,
not "a token containing gzip" — which is what keeps `--no-auto-compress` and `--exclude=*.gz`
clean. Short clusters are matched **case-sensitively**, because `-i` is `--ignore-zeros`
and `-I` is `--use-compress-program`; a case-insensitive class silently condemns
`tar -xif`.

The forcing set comes from GNU tar 1.35 `--help`, measured on the host on 2026-09-18:

| forcing | not forcing |
|---|---|
| `-z` `--gzip` `--gunzip` `--ungzip` | `-a` `--auto-compress` |
| `-j` `--bzip2` | `--no-auto-compress` |
| `-J` `--xz` | |
| `-Z` `--compress` `--uncompress` | |
| `-I` `--use-compress-program[=PROG]` | |
| `--lzip` `--lzma` `--lzop` `--zstd` | |

`--auto-compress` picks a program from the file's suffix and `--no-auto-compress` switches
that off. Neither pins a format, so neither is the defect this guard exists to stop.

The invocation boundary matters more than it looks. `tar -xf a.tar && gzip -d b.gz` must
stay clean; a scan that ran to the end of the line would let the neighbouring `gzip` decide
the verdict on a correct `tar`. The six live moving-download commands all run
`… && tar -xf … && ln -s … && rm …` in one `RUN`, so the boundary rule is exercised by the
real templates as well as the fixtures.

## Association by saved path (hole B)

Read the **saved path** of each moving download — `wget -O <path>`, `curl -o <path>`, or
`curl --output <path>` — and then decide each forcing `tar` invocation by one of three
branches:

1. A known saved path appears in the `tar` invocation's **own token span** → **indict**,
   wherever in the template that invocation sits. This is what closes hole B.
2. The invocation shares a command with a moving download, **and** the guard cannot tell
   which file the invocation reads — the download named no saved path, or the invocation's
   span holds a shell variable or a stdout sink → **indict**, conservatively.
3. Otherwise → **clean**.

Three details in branch 1 are each there because the simpler version was measured wrong on
2026-09-18:

- **Match the whole token span, not the operand list.** An operand list holds only tokens
  that do not start with `-`, so `tar --bzip2 --file=/tmp/ff.tar.xz` and
  `tar -xjf/tmp/ff.tar.xz` — both of which glue the archive to its flag — hide the path
  inside an option token and escape entirely. Today's whole-command rule catches both, so
  operand-only matching would be a **regression**, not just an unclosed hole. The span is
  still bounded by the invocation, so the mixed-`RUN` case below stays clean.
- **A bare basename counts only when it is bare.** `cd /tmp && tar -xjf ff.tar.xz` names
  the saved file with no directory and must be caught. But comparing basenames freely
  indicts `tar -xJf /opt/vendor/pinned-9.9.9/firefox-esr.tar.xz` — a different, explicitly
  version-pinned file that merely shares a basename with `/tmp/firefox-esr.tar.xz`. So a
  basename matches only a token carrying no `/`.
- **A stdout sink is not a saved path.** `wget -O - "<moving url>" | tar -xzf -` records
  `-` as its target under a naive reader. That would make a path "known", switching branch 2
  off, while branch 1 cannot fire because `-` is not a path — and the case would silently
  go clean, again a regression against today. `-`, `/dev/stdout` and `/dev/null` are
  therefore never recorded.

Branch 2 is not a leftover; it is load-bearing. `wget -O- "<moving url>" | tar -xzf -`
writes no file at all, and `tar -xjf "$FF"` names its archive through a variable the guard
cannot resolve. Both are the same defect, and both are reported.

**The per-command rule becomes operand-aware, and that is a deliberate change from the
current guard.** Today `_FORCED_DECOMPRESSOR.findall(command)` runs over the whole command,
so any forcing `tar` sharing a `RUN` with a moving download is indicted — including one that
extracts a completely different, version-pinned file. Keeping that as-is is incompatible
with binding by operand: the two rules would disagree about the same line. Branch 1 replaces
it wherever a saved path is known, and branch 2 preserves it exactly where it is not.

Binding to the operand, not to the command text, is the load-bearing part. A single `RUN`
can hold a correct extraction of the moving download beside a forced extraction of a pinned
archive:

```dockerfile
RUN wget -O /tmp/ff.tar.xz "<moving>" && tar -xf /tmp/ff.tar.xz -C /opt \
    && tar -xzf tool-v1.2.3.tar.gz -C /usr/local/bin
```

Asking "does the saved path appear somewhere in this command?" indicts the second `tar` for
the first's presence. Asking "does the saved path appear in *this* `tar` invocation's own
span?" does not.

Operand comparison SHALL tolerate the quoting the templates actually use — the geckodriver
lines write `tar -xzf "geckodriver-…tar.gz"` — so strip surrounding quotes before comparing.
Compare the path as written and by basename, since a template may `cd` and then name the
file bare.

**When no saved path can be read, do not guess one.** A `wget <url>` with no `-O` saves to a
name derived from the URL, and for `https://download.mozilla.org/?product=firefox-esr-latest-ssl`
that derivation is unreliable. A guessed path is how a false positive lands on a file the
download never wrote. Such a download falls to branch 2 — the whole-command rule, unchanged
from today — and nothing more. The resulting gap is narrower than the gap a false positive
would open, because a false positive can only be repaired by making a correct template wrong.

## Measured before it was written

The rule above was prototyped and run on 2026-09-18 against this branch at `4b253e26`,
before any task was worked. The first prototype was **wrong in four ways** — it kept the
whole-command rule alongside branch 1 and so failed the mixed-`RUN` case, matched operands
instead of spans, compared basenames freely, and recorded `-` as a saved path. Each is
called out above because each measured as a regression against today's guard, not merely as
a gap. Results of the corrected rule:

| case | result |
|---|---|
| all 15 forcing forms (9 already frozen plus the six hole-A misses) | each flagged |
| `-xf`, `-xvf`, `--extract`, `-a`, `--auto-compress`, `--no-auto-compress`, `-xif`, `--exclude=*.gz` | each clean |
| `tar -xf a.tar && gzip -d b.gz` | clean (separator boundary holds) |
| hole B, download and extraction in separate `RUN` instructions | flagged |
| the frozen cross-command test's input | empty |
| one `RUN` mixing a correct moving extraction with a forced pinned one | empty |
| `wget -O- \| tar -xzf -` and `wget -O - \| tar -xzf -` | both flagged via branch 2 |
| `wget` with no `-O`, forced `tar` in the same `RUN` | flagged via branch 2 |
| `tar --bzip2 --file=/tmp/ff.tar.xz` and `tar -xjf/tmp/ff.tar.xz` (glued) | both flagged via branch 1 |
| `tar -xjf "$FF"` (archive named by a variable) | flagged via branch 2 |
| `cd /tmp && tar -xjf ff.tar.xz` (bare basename) | flagged via branch 1 |
| `tar -xJf /opt/vendor/pinned-9.9.9/firefox-esr.tar.xz` (basename collision) | empty |
| all 15 live service templates | **0 offenders** |

The last row is the one that matters most: it is the false-positive backstop, and it is what
lets this change stay test-only.

### Why not proximity

Finding `4027485403` says to "associate extraction inputs with prior downloads rather than
requiring both operations on one logical line". Taken as proximity — the nearest prior
download indicts the next extraction — it breaks two tests that exist to hold this exact
line:

- `test_the_two_are_not_paired_across_separate_commands`
  (`tests/unit/test_service_template_downloads.py:180-191`).
- `TestAVersionedDownloadMayForceItsFormat` (`:153`), protecting
  `src/cli/templates/dockerfiles/Dockerfile-grader:62-65`.

The saved path is the discriminator the finding was reaching for. All six moving templates
save to `/tmp/firefox-esr.tar.xz`; geckodriver's `tar` operates on
`geckodriver-v0.36.0-linux64.tar.gz`. Different paths, so the correct line stays clean
without any special case.

## CAUTION — the issue's docstring instruction is wrong; do not copy it

Issue #492's "One more thing" section says to correct the module docstring
(`tests/unit/test_service_template_downloads.py:20`) from *"No pre-merge job builds service
images (#473)"* to say **post-merge-only**, on the grounds that
`.github/workflows/publish-base-images.yml:91` builds on every push to `dev` and `main`.

That premise conflates two different things and was measured wrong. Verified on this branch
on 2026-09-18:

- `publish-base-images.yml` builds the **base** images, not the service templates.
  `scripts/dev/build_docker_images.sh` says so in its own usage text: *"Builds the base
  Docker images locally."*
- A **pre-merge** job does build service images. `pr-preview.yml` runs
  `on: pull_request` (`:3-5`) and its smoke stage (`:304-321`) brings up a rendered stack
  with `--build`, which compiles `Dockerfile-chat`, `Dockerfile-postgres`, and
  `Dockerfile-data-manager`.
- That slice is `services: chatbot` only. **None of the six templates carrying the moving
  download is in it** — they are `Dockerfile-grader`, `Dockerfile-grader-gpu`,
  `Dockerfile-chat-gpu`, `Dockerfile-data-manager-gpu`, `Dockerfile-mattermost-gpu`, and
  `Dockerfile-benchmarks-gpu`. The `-gpu` variants are distinct files from the ones the
  smoke stack builds.

So the docstring's conclusion — *"nothing caught this"* — is true, and its stated reason is
false in two directions at once. Writing the issue's proposed correction would replace one
wrong claim with another. The accurate statement is that CI builds only a narrow slice of
the service templates pre-merge, and none of the templates that fetch this download. Task
3.1 carries the required wording.

## Scope

- Test files only. `src/cli/templates/dockerfiles/**` stays byte-identical — a diff there
  would mean the guard produced a false positive.
- `.github/workflows/**` is not edited. The nightly drain's scoped token and the local
  deny-hook both block that path.
- No new dependency. `re` plus `shlex`-free token splitting is enough; a Dockerfile parser
  is out of scope and would be a dependency.
- `diff-cover` measures `src/` only, so this diff prints *"No lines with coverage
  information"* and clears `--fail-under=80`. Expected. Black and isort **do** cover
  `tests/`, so keep both clean.

## Review round 1 — 2026-09-19 (Codex, eight P2 findings, all verified valid)

Every finding was reproduced against the PR head at `072dac42` before any code moved, and
each fix was written test-first. The eight share one root cause, so they were closed by one
rewrite of the helper layer rather than eight patches: the scanner read the command as
whitespace-delimited TOKENS, where it needed to read it as INVOCATIONS.

### D9 — One parser: `(forcing options, archive reference)` per tar invocation

`_parse_tar_span` replaces the character-scan. It knows three things the token scan did
not:

- **An option that takes an argument consumes the rest of its cluster.**
  `tar -xf/tmp/firefox.tar.xz` was reported as forcing xz because the FILENAME contains a
  `z`. The short options that take an argument are `bCfFgHIKLNTVX`; `-I` is both forcing
  and argument-taking, so it is recorded before the cluster ends.
- **`--` ends option recognition.** `tar -xf /tmp/moving -- --gzip` extracts a member
  literally named `--gzip` with auto-detection; the guard called it forcing.
- **The archive comes from `-f`/`--file` only.** An operand is never read as the archive:
  without `-f`, tar reads stdin or its default device, and the operands are member names.

The archive reference is what branch 1 and branch 2 now consult, which is what makes D11
and D12 possible at all.

### D10 — The executable is matched by basename, and operators are isolated

`tokens[i] == "tar"` skipped `/bin/tar -xzf …` entirely — a regression against the
unanchored `tar\s+` regex this change replaced. `_basename` fixes it, and a test fences
the over-widening: `mytar` is not tar.

`str.split()` exposes `&&`, `||`, `;` and `|` only when whitespace surrounds them, but
bash does not require it. Measured: `wget …&&tar …` hid both the URL and the tar token, so
a forced extraction of a moving download read as clean; `tar -xf a;tar -xzf b` read as ONE
invocation, so the second tar's forcing option was attributed to the first tar's archive.
`_shell_tokens` isolates the operators first, `||` before `|`.

The trade-off, stated rather than implied: an operator inside a quoted string is also
isolated. That ends a tar span early, which can only lose a forcing option the guard would
otherwise attribute to a file it cannot prove tar reads — it cannot invent an offender.

### D11 — A saved path belongs to the download invocation that wrote it

`_saved_paths` collected every wget/curl destination in any command containing a moving
URL. So `wget -O /tmp/moving <latest> && wget -O /tmp/pinned <versioned>` marked
`/tmp/pinned` as moving and condemned a correct `tar -xzf /tmp/pinned`.
`_download_invocations` now pairs each destination with its own invocation's tokens, and
only an invocation whose own text matches `_MOVING_DOWNLOAD` contributes. curl's attached
short form `-o/tmp/x` is read too — it was handled for wget's `-OFILE` and not for curl's,
so a moving download written that way recorded no path and left the later forced tar
undetected.

### D12 — Whole-value matching, and resolvability judged on the archive alone

`path in token` made the saved `/tmp/a` match a pinned `/tmp/archive-v1.tar.gz`, and the
basename branch made `a` match `a.tar.old`. `_archive_matches_saved` compares whole
values, keeping the basename branch only for an archive reference carrying no `/`.

`_span_is_unresolvable` scanned the whole span for `$`, so
`tar -xzf pinned-v1.tar.gz -C "$DEST"` was indicted for a variable naming its extraction
DIRECTORY. `_archive_is_unresolvable` reads the archive reference only, and keeps the
conservative default: no `-f` at all means tar reads stdin or its default device, which is
unresolvable.

### Deliberately not fixed here

Old-style tar syntax (`tar xzf /tmp/f.tar.gz`, no leading dash) is still invisible to the
scanner: the first operand is the option cluster and nothing treats it as one. No template
in the repository uses that spelling — all ten dash-prefix their options, measured — so
this is a widening rather than a fix to a live hole, and it is filed as a follow-up issue
rather than grown into this diff.

**After the round:** 60 passed, 18 skipped in the file (46 passed, 18 skipped at
`072dac42`), 16 of 16 cases in the review matrix at their expected verdict, including the
original `-xjf` defect still indicted and the pinned geckodriver line still clean.

## Review round 2 — 2026-09-19 (adversarial pass over round 1's own fix)

### D13 — Every spelling of the destination option, or the saved path is lost silently

Round 1's `_download_invocations` read the spaced forms and the attached SHORT form, and
missed the attached LONG form: `curl --output=/tmp/ff.tar` and
`wget --output-document=/tmp/ff.tar` recorded no saved path at all. The failure is quiet
and one-directional — branch 2 only reaches within the download's own command, so a forced
extraction in a LATER RUN read as clean, which is the exact silent-skip shape this change
exists to close. Fixed with a parametrized test over all four spellings.

This is round 1's own regression, not a pre-existing hole: the spelling was unreachable
before, because the previous code matched `-O`/`-o` literally and never looked at long
options at all.

**After the round:** 64 passed, 18 skipped in the file.
