## ADDED Requirements

### Requirement: A forcing option is recognised wherever it sits in the tar invocation

The download guard SHALL treat a `tar` invocation as forcing a decompressor when any of its option tokens names a compression program, whatever position that token holds.

The invariant this guard exists to protect is format **auto-detection** on a moving
download. A moving download is a URL that names no version, so what arrives can change
format without anything in this repository changing; forcing a decompressor on one is the
defect whichever format is forced. That is a property of the whole `tar` invocation, not of
its first option token.

Today `_FORCED_DECOMPRESSOR` (`tests/unit/test_service_template_downloads.py:37-40`)
anchors the option immediately after `tar\s+`, so `tar -x --gzip -f f` and `tar -x -z -f f`
are both unreachable. Four forcing forms are also absent from its alternation: `-I`,
`--use-compress-program`, `--lzip`, and `--uncompress`. The guard's own comment (`:33-36`)
claims it covers "EVERY tar option that forces a compression program", so the gap is a
false statement in the file as well as a hole.

The forcing set is what GNU tar 1.35 `--help` lists under "Compression options", less the
two that force nothing, measured on the host on 2026-09-18. Short forms, matched
case-sensitively inside a cluster: `-z`, `-j`, `-J`, `-Z`, `-I`. Long forms, matched as
whole tokens: `--gzip`, `--gunzip`, `--ungzip`, `--bzip2`, `--xz`, `--lzma`, `--lzop`,
`--zstd`, `--lzip`, `--compress`, `--uncompress`, `--use-compress-program`.

An option token is a token beginning with `-`. An operand is not, which is why
`tar -xf f.tar.xz` stays clean even though its operand ends in `.xz`. The scan SHALL stop
at the end of the `tar` invocation — a shell separator such as `&&`, `;`, or `|` — so an
unrelated command later on the same line cannot supply a forcing option to it. Two limits
on "option token" were added after review on 2026-09-19 and are specified below: the
argument attached to an option such as `-f` is not a flag cluster, and nothing after
`--` is an option at all.

#### Scenario: A forcing option separated from the tar token is flagged

- **WHEN** the guard reads `tar -x --gzip -f /tmp/f.tar.gz -C /opt/` or `tar -x -z -f /tmp/f.tar.gz -C /opt/`
- **THEN** each is reported as forcing a decompressor

#### Scenario: The four missing forcing forms are flagged

- **WHEN** the guard reads `tar --lzip -xf f.tar.lz`, `tar --uncompress -xf f.tar.Z`, `tar -I zstd -xf f.tar.zst`, or `tar --use-compress-program=zstd -xf f.tar.zst`
- **THEN** each is reported as forcing a decompressor

All four are compression options in GNU tar 1.35, and none appears in the guard's
alternation today. `-I` and `--use-compress-program` are the same option in two spellings,
and the long spelling takes its program with an `=`, so both spellings need matching.

#### Scenario: The forcing options already covered stay flagged

- **WHEN** the guard reads any of `tar -xjf f`, `tar -xzf f`, `tar -xJf f`, `tar -xZf f`, `tar --gzip -xf f`, `tar --bzip2 -xf f`, `tar --xz -xf f`, `tar --zstd -xf f`, or `tar --lzma -xf f`
- **THEN** each is reported as forcing a decompressor

These are the cases `TestTheGuardRejectsEveryForcedDecompressor`
(`tests/unit/test_service_template_downloads.py:111`) holds today. Widening the scan must
not drop one.

#### Scenario: A forcing option belonging to a later command is not borrowed

- **WHEN** the guard reads `tar -xf /tmp/f.tar.xz -C /opt/ && gzip -d /tmp/other.gz`
- **THEN** the `tar` invocation is not reported as forcing a decompressor

The scan stops at the shell separator. Reading to the end of the line instead would let any
neighbouring command decide the verdict on a `tar` that is correct.

### Requirement: Format auto-detection never trips the guard

The download guard SHALL NOT report a `tar` invocation whose option tokens carry no program-forcing compression option.

A false positive here is worse than the hole it closes. The guard runs over the live
templates, so a false positive turns a correct template red and the only repair available
is to make the template wrong. That is how a file-wide scan condemned the legitimate
geckodriver line the first time the decompressor check was widened.

Three near-misses are the ones a careless widening breaks, and each SHALL stay clean:
`-xvf` and `--extract` contain no forcing letter but sit next to one in a cluster;
`-a`/`--auto-compress` is a compression option that *selects by suffix* rather than forcing,
and `--no-auto-compress` only switches that selection off; and `-i` is `--ignore-zeros`,
which differs from the forcing `-I` by case alone.

#### Scenario: Plain extraction is left alone

- **WHEN** the guard reads `tar -xf /tmp/f.tar.xz -C /opt/`, `tar -xvf /tmp/f.tar -C /opt/`, or `tar --extract --file /tmp/f -C /opt/`
- **THEN** none is reported as forcing a decompressor

#### Scenario: Suffix-based selection is not forcing

- **WHEN** the guard reads `tar -a -xf /tmp/f.tar.gz`, `tar --auto-compress -xf /tmp/f`, or `tar --no-auto-compress -xf /tmp/f`
- **THEN** none is reported as forcing a decompressor

`--auto-compress` reads the suffix and `--no-auto-compress` stops it doing so. Neither names
a program, so neither pins the format the way this guard forbids. `--no-auto-compress` is
also the near-miss for a long-form alternation containing `compress`: matching it would be
a match on the wrong token boundary.

#### Scenario: A lowercase -i is not the forcing -I

- **WHEN** the guard reads `tar -xif /tmp/f.tar`
- **THEN** it is not reported as forcing a decompressor

`-i` is `--ignore-zeros`. Only the uppercase `-I` is `--use-compress-program`, so the short
cluster SHALL be matched case-sensitively.

#### Scenario: Every live service template reports no offender

- **WHEN** the guard runs over every template `service_templates()` discovers
- **THEN** no template reports a forced decompressor on a moving download
- **AND** no file under `src/cli/templates/dockerfiles/` is edited by this change

All 15 templates are correct as they stand, measured at `4b253e26`. Six of them fetch a
moving download; all six use `wget -O` and `tar -xf`. If closing these holes needs a
template edited, the guard is wrong, not the template.

### Requirement: A moving download is indicted wherever its saved file is force-extracted

The download guard SHALL record the path each moving download is saved to, and SHALL report a forced decompressor applied to that path anywhere in the same template, including in a later `RUN` instruction.

`_commands` (`tests/unit/test_service_template_downloads.py:51-63`) joins backslash
continuations and then splits on newline, so two `RUN` instructions are two logical
commands and the per-command pairing cannot see across them. Docker carries the downloaded
file into the next layer, so splitting the download from the extraction is an ordinary
build shape — and today it is a silent one. Measured at `4b253e26`, the split form below
reports an empty offender list while the identical single-`RUN` form reports
`['tar -xjf']`.

The saved path is read from `wget -O <path>`, `curl -o <path>`, or `curl --output <path>`.
A stdout sink — `-`, `/dev/stdout`, `/dev/null` — SHALL NOT be recorded as a saved path. The
guard SHALL NOT guess a path it cannot read, because a guessed one is what produces a false
positive on a file the download never wrote.

Each forcing `tar` invocation SHALL therefore be decided by one of three branches:

1. A known saved path appears in the invocation's **own token span** → report it, wherever
   in the template the invocation sits.
2. The invocation shares a command with a moving download **and** the guard cannot tell
   which file the invocation reads — no saved path was recorded, or the span holds a shell
   variable or a stdout sink → report it, as conservatively as the guard is today.
3. Otherwise → do not report it.

The span, not the operand list, is what branch 1 SHALL search. An operand list holds only
tokens that do not begin with `-`, so an archive glued to its flag — `--file=<path>`, or a
short cluster written `-xjf<path>` — is hidden inside an option token. Today's whole-command
rule reports both, so matching operands alone would **lose** coverage the guard already has.

A basename SHALL match only a token that carries no `/`. `cd /tmp && tar -xjf ff.tar.xz`
names the saved file with no directory and must be reported; comparing basenames freely
would also report `tar -xJf /opt/vendor/pinned-9.9.9/firefox-esr.tar.xz`, a different and
explicitly version-pinned file that merely shares a basename.

Branch 2 SHALL be kept. `wget -O - "<moving url>" | tar -xzf -` writes no file, and
`tar -xjf "$FF"` names its archive through a variable, so neither can be bound by path.

Branch 1 **replaces** the whole-command pairing wherever a saved path is known, and this is
a deliberate narrowing. Today the check runs over the whole command, so a forcing `tar`
sharing a `RUN` with a moving download is reported even when it extracts a different,
version-pinned file. That over-reach and binding-by-operand cannot both hold: they disagree
about the same line. Where the guard can name the file, the operand decides.

#### Scenario: A download and its extraction in separate RUN instructions are paired

- **WHEN** a template reads `RUN wget -O /tmp/ff.tar "https://download.mozilla.org/?product=firefox-esr-latest-ssl&os=linux64"` followed by `RUN tar -xjf /tmp/ff.tar -C /opt`
- **THEN** the guard reports `/tmp/ff.tar` as force-extracted
- **AND** the offender list is not empty

#### Scenario: The single-RUN pairing keeps working

- **WHEN** a moving download saved to `/tmp/ff.tar` and `tar -xjf /tmp/ff.tar` sit in one `RUN` joined by `&&`
- **THEN** the guard reports it, as it does today

This is the original defect's shape, and branch 1 covers it: the saved path is an operand of
the forcing invocation.

#### Scenario: A moving download with no saved path is still reported

- **WHEN** a moving download carries no `-O`, `-o`, or `--output` and a forced `tar` sits in the same logical command
- **THEN** the guard reports it
- **AND** no `tar` invocation in any other command is reported because of that download

This is branch 2. A download with no saved file cannot be bound by path, so dropping the
whole-command rule for it would open a new hole while closing the old one.

#### Scenario: A download piped to tar is reported in both spellings

- **WHEN** a template reads `wget -O- "<moving url>" | tar -xzf -` or `wget -O - "<moving url>" | tar -xzf -`
- **THEN** the guard reports each of them

The two spellings differ only by a space, and the spaced one is the trap: a reader that took
the next token would record `-` as the saved path. A path would then be "known", so branch 2
could not fire, while branch 1 could not fire either because `-` names no file — and the
case would go clean. Today's guard reports it, so this SHALL NOT become a regression.

#### Scenario: An archive glued to its flag is reported

- **WHEN** a moving download saved to `/tmp/ff.tar.xz` is extracted by `tar --bzip2 --file=/tmp/ff.tar.xz` or by `tar -xjf/tmp/ff.tar.xz`
- **THEN** the guard reports each of them

Both glue the archive into an option token, where an operand scan cannot see it. Today's
whole-command rule reports both.

#### Scenario: An archive named by a shell variable is reported

- **WHEN** a moving download saved to `/tmp/ff.tar.xz` shares a `RUN` with `tar -xjf "$FF" -C /opt`
- **THEN** the guard reports it

The guard cannot resolve `$FF`, so it cannot prove the invocation reads some other file.
Branch 2 covers it. `src/cli/templates/dockerfiles/Dockerfile-data-manager-gpu` already
writes `tar` operands through variables, so this shape is not hypothetical.

#### Scenario: A bare basename after a directory change is reported

- **WHEN** a moving download saved to `/tmp/ff.tar.xz` is extracted in a later `RUN` by `cd /tmp && tar -xjf ff.tar.xz -C /opt`
- **THEN** the guard reports it

### Requirement: The guard indicts only the file the moving download saved

The download guard SHALL bind a forced decompressor to a moving download by the `tar` invocation's own token span whenever that download's saved path is known, and SHALL NOT report an invocation that demonstrably reads some other file.

This is the constraint that makes path association safe where proximity association is not.
Finding `4027485403` proposes associating extraction inputs with *prior* downloads. Read as
proximity, that re-condemns the geckodriver line
(`src/cli/templates/dockerfiles/Dockerfile-grader:62-65`), which forces `-xzf` on a URL
naming `v0.36.0` and is correct because a pinned URL cannot change format. It also breaks
`test_the_two_are_not_paired_across_separate_commands`
(`tests/unit/test_service_template_downloads.py:180-191`), which exists to assert exactly
that.

Binding by the `tar` invocation's own span, rather than by the text of the whole command it
sits in, is the load-bearing half. A single `RUN` can hold a correct extraction of the
moving download and a forced extraction of some pinned archive; a check that asked only
whether the saved path appears *somewhere in the command* would indict the second for the
first's presence.

"Demonstrably reads some other file" is the limit of this clause. Where the invocation names
a file the guard can resolve and that file is not a saved path, it SHALL be left alone; where
the invocation names nothing the guard can resolve, branch 2 of the previous requirement
applies and it SHALL be reported. Silence is never read as innocence.

Today's guard does exactly that, so this requirement narrows it. That narrowing is the
price of binding by operand and is paid only where the guard can name the file; where it
cannot, branch 2 of the previous requirement keeps the whole-command reach unchanged. No
live template is affected either way — all 15 report no offender under both rules, measured
on 2026-09-18.

#### Scenario: A pinned download in another command keeps its forced format

- **WHEN** one command fetches a moving download to `/tmp/f.tar.xz` and extracts it with `tar -xf`, and a later command fetches `tool-v1.2.3.tar.gz` and extracts it with `tar -xzf`
- **THEN** the offender list is empty

This is `test_the_two_are_not_paired_across_separate_commands`
(`tests/unit/test_service_template_downloads.py:180`) unchanged. It SHALL stay green and
SHALL NOT be edited.

#### Scenario: A forced tar on another file in the same command is not indicted

- **WHEN** one `RUN` reads `wget -O /tmp/ff.tar.xz "<moving url>" && tar -xf /tmp/ff.tar.xz -C /opt && tar -xzf tool-v1.2.3.tar.gz -C /usr/local/bin`
- **THEN** the offender list is empty

The saved path is extracted correctly. The forced `tar` beside it operates on a different,
version-pinned file. Only the operand distinguishes the two, so only the operand may decide.

#### Scenario: A different file sharing a basename is not reported

- **WHEN** a moving download saved to `/tmp/firefox-esr.tar.xz` appears in one `RUN`, and a later `RUN` reads `tar -xJf /opt/vendor/pinned-9.9.9/firefox-esr.tar.xz -C /opt/vendor`
- **THEN** the offender list is empty

The two files share a basename and nothing else. The second names a version-pinned
directory, so its payload cannot change format, and forcing `xz` on it is correct. This is
the false positive a free basename comparison produces, and it is why a basename SHALL match
only a token carrying no `/`.

#### Scenario: The geckodriver line stays clean

- **WHEN** the guard runs over the templates that fetch geckodriver from a URL naming `v0.36.0` and extract it with `tar -xzf`
- **THEN** no offender is reported for those lines

`TestAVersionedDownloadMayForceItsFormat`
(`tests/unit/test_service_template_downloads.py:153`) holds this line. It SHALL stay green
and SHALL NOT be edited.

### Requirement: The guard SHALL read a tar invocation the way tar reads it

The download guard SHALL parse each `tar` invocation into its forcing options and the archive named by `-f` or `--file`, SHALL recognise the executable by its basename, SHALL bound each invocation on shell control operators whether or not whitespace surrounds them, and SHALL stop recognising options at the `--` end-of-options marker.

Reading the command as whitespace-delimited tokens was wrong in both directions, measured
on 2026-09-19. It produced false positives — `tar -xf/tmp/firefox.tar.xz` was reported as
forcing xz because the FILENAME contains a `z`, and `tar -xf /tmp/moving -- --gzip` was
reported although GNU tar treats `--gzip` there as a member name. It produced false
negatives too — `/bin/tar -xzf …` was skipped by an exact `tar` token match (a regression
against the earlier unanchored regex), and `wget …&&tar …` hid both the URL and the tar
token, because bash does not require whitespace around a control operator. Whitespace
alone must not decide whether a template is broken.

#### Scenario: An attached archive argument is not read as options
- **WHEN** a template contains `tar -xf/tmp/firefox.tar.xz`
- **THEN** the guard reports no forcing option
- **AND** `tar -xjf/tmp/ff.tar.xz` is still reported, because `-j` precedes the attached argument

#### Scenario: An absolute path to the tar executable is recognised
- **WHEN** a moving download saved to `/tmp/ff.tar` is extracted by `/bin/tar -xzf /tmp/ff.tar` in a later RUN
- **THEN** the guard reports the forcing option
- **AND** an unrelated executable named `mytar` is not read as a tar invocation

#### Scenario: A control operator without surrounding whitespace bounds the invocation
- **WHEN** a template contains `wget -O /tmp/ff.tar <moving>&&tar -xzf /tmp/ff.tar`
- **THEN** the guard reports the forcing option
- **AND** in `tar -xf /tmp/moving;tar -xzf pinned-v1.tar.gz` the forcing option belongs to the second invocation only

#### Scenario: Option recognition stops at the end-of-options marker
- **WHEN** a template contains `tar -xf /tmp/moving -- --gzip`
- **THEN** the guard reports no forcing option

### Requirement: The guard SHALL bind each saved path and each archive reference whole

The download guard SHALL record a saved path only from the download invocation that wrote it, SHALL compare an archive reference to a saved path as a whole value rather than by containment, and SHALL judge resolvability from the archive reference alone.

Three collection rules were too broad, measured on 2026-09-19. Every destination in a
command containing any moving URL was recorded as moving, so
`wget -O /tmp/moving <latest> && wget -O /tmp/pinned <versioned>` condemned a correct
`tar -xzf /tmp/pinned`. Containment made the saved `/tmp/a` match a pinned
`/tmp/archive-v1.tar.gz`, and the basename branch made `a` match `a.tar.old`. A `$`
anywhere in the invocation counted as an unresolvable archive, so
`tar -xzf pinned-v1.tar.gz -C "$DEST"` was indicted for the variable naming its
extraction DIRECTORY. Each of the three turns a correct template red, which is how a
guard stops being trusted.

#### Scenario: Every spelling of the output option records the saved path
- **WHEN** a moving download is written as `curl -o/tmp/ff.tar.xz`, `curl --output=/tmp/ff.tar.xz`, `wget -O/tmp/ff.tar.xz` or `wget --output-document=/tmp/ff.tar.xz` and extracted by `tar -xzf /tmp/ff.tar.xz` in a later RUN
- **THEN** the guard reports the forcing option
- **AND** the spaced forms of the same options behave identically

#### Scenario: A saved path does not match a longer path that starts with it
- **WHEN** `/tmp/a` is the saved path and a later RUN extracts `/tmp/archive-v1.tar.gz` with a forced format
- **THEN** the guard reports no offender
- **AND** a saved basename `a` does not match the archive `a.tar.old`

#### Scenario: Each saved path belongs to its own download
- **WHEN** one RUN saves a moving download to `/tmp/moving`, a version-pinned download to `/tmp/pinned`, and extracts `/tmp/pinned` with a forced format
- **THEN** the guard reports no offender
- **AND** extracting `/tmp/moving` with a forced format in that same RUN is still reported

#### Scenario: Only the archive reference decides resolvability
- **WHEN** a RUN carrying a moving download extracts a literal `pinned-v1.tar.gz` with `-C "$DEST"`
- **THEN** the guard reports no offender
- **AND** `tar -xjf "$FF"`, whose archive itself is a variable, is still reported

### Requirement: The guard SHALL read a command as the shell and the tools read it

The download guard SHALL resolve quotes and isolate control and redirection operators before it reads a command, SHALL recognise `tar`, `wget` and `curl` only at a command position, SHALL consume the required argument of a long option and find the output option inside a short-option cluster, and SHALL report a command it cannot parse rather than pass it.

Review on 2026-09-20 (seven findings against `4dc38191`): `str.split` broke a quoted path
with a space into two words; `tar -xzf /tmp/moving>/dev/null` kept the redirection glued
to the archive so the saved path stopped matching; `echo tar -xzf /tmp/moving` counted as
an invocation; `tar --exclude --gzip` read the exclusion pattern as a forcing option; and
`curl -sLo/tmp/x` recorded no saved path. Design D14–D16.

#### Scenario: A long option's separate argument is not an option
- **WHEN** a tar invocation reads `tar --exclude --gzip -xf /tmp/moving`
- **THEN** the guard reports no forcing option
- **AND** `tar --exclude pattern --gzip -xf /tmp/f` still reports `--gzip`
- **AND** `tar --occurrence --gzip -xf /tmp/f`, whose first option takes only an attached value, still reports `--gzip`

#### Scenario: The output option inside a short-option cluster records the saved path
- **WHEN** a moving download is written as `curl -sLo/tmp/ff.tar.xz`, `curl -sLo /tmp/ff.tar.xz`, `wget -qO/tmp/ff.tar.xz` or `wget -qO /tmp/ff.tar.xz` and extracted by `tar -xzf /tmp/ff.tar.xz` in a later RUN
- **THEN** the guard reports the forcing option
- **AND** `curl -do=1 <url>` records no saved path, because `-d` consumes the rest of its cluster

#### Scenario: An attached redirection does not hide the archive
- **WHEN** a saved moving path is extracted by `tar -xzf /tmp/moving>/dev/null`, `tar -xzf /tmp/moving>/dev/null 2>&1`, `tar -xzf >/dev/null /tmp/moving` or `tar 2>&1 -xzf /tmp/moving`
- **THEN** the guard reports the forcing option

#### Scenario: A quoted path with a space is one argument
- **WHEN** a moving download is saved as `"/tmp/my moving.tar"` and a later RUN extracts `"/tmp/my pinned-v1.tar.gz"` with a forced format
- **THEN** the guard reports no offender
- **AND** extracting `"/tmp/my moving.tar"`, `'/tmp/my moving.tar'` or `/tmp/my\ moving.tar` with a forced format is reported
- **AND** a `;` inside a quoted argument does not begin a new command

#### Scenario: A tar named as an argument to another command is not an invocation
- **WHEN** a RUN after a moving download runs `echo tar -xzf /tmp/moving`
- **THEN** the guard reports no offender
- **AND** `tar` after `&&`, `||`, `;` or `(`, behind a `NAME=value` assignment or a wrapper such as `sudo` or `env`, inside a `sh -c '…'` string, or after the RUN instruction's own `--mount=` flag is still an invocation and is still reported
- **AND** `mytar -xzf <saved moving path>` is still not reported

#### Scenario: An unparseable command is reported
- **WHEN** a RUN after a moving download carries an unterminated quote
- **THEN** the guard reports the command as unparseable
- **AND** every command of every live service template parses

#### Scenario: A wrapper option with a separate argument does not hide tar
- **WHEN** a RUN after a moving download runs `sudo -u root tar -xzf <saved>`, `nice -n 10 tar -xzf <saved>`, `env --chdir /tmp tar -xzf <saved>` or `sudo -u root sh -c 'tar -xzf <saved>'`
- **THEN** the guard reports the forcing option, because behind a wrapper a known program anywhere after the wrapper's options is the command
- **AND** `echo root tar -xzf <saved>` without a wrapper still reports no offender

#### Scenario: A URL-shaped argument to a curl option does not break the pairing
- **WHEN** a curl invocation reads `--header`, `--proxy`, `--referer` or `--user-agent` with a URL-shaped value before `-o /tmp/moving <moving>`, and a later RUN extracts `/tmp/moving` with a forced format
- **THEN** the guard reports the forcing option, because the option's argument is consumed by arity and never pairs with `-o`
- **AND** `--url <url>` names a transfer and pairs like a bare URL
- **AND** a long option in neither arity table disables the pairing, so every destination of that invocation takes the invocation's moving-ness

#### Scenario: A command word the guard cannot name is read as a possible tar
- **WHEN** a RUN after a moving download runs `$(echo tar) -xzf <saved>`, `` `which tar` -xzf <saved> `` or `$TAR -xzf <saved>`
- **THEN** the guard reports the forcing option
- **AND** `$(which ls) -la <saved>` reports no offender, because it carries no forcing option
- **AND** a command substitution is one word, so `tar -xzf $(ls /tmp/*.tar)` names an unresolvable archive as before

### Requirement: The guard SHALL follow provenance in Dockerfile order

The download guard SHALL record each saved path with the moving-ness of the transfer that wrote it, in the order the shell runs the writes, SHALL let a later write to the same path replace the earlier provenance, and SHALL pair each curl output option with the URL in the same position.

Review on 2026-09-20: one file-wide set collected before any command was scanned let a
LATER moving write indict an EARLIER extraction and never let a pinned write clear a
moving path; one curl with a moving and a pinned transfer marked both destinations from
the invocation's text. Design D17–D18.

#### Scenario: A later moving write does not indict an earlier extraction
- **WHEN** RUN 1 saves a pinned download to `/tmp/a` and extracts it with a forced format, and RUN 2 saves a moving download to `/tmp/a`
- **THEN** the guard reports no offender
- **AND** a moving write to `/tmp/a` BEFORE the extraction is still reported
- **AND** `tar -xzf /tmp/a && wget -O /tmp/a <moving>` inside one RUN reports no offender

#### Scenario: A later pinned write clears the moving provenance
- **WHEN** a moving download is saved to `/tmp/a`, then a pinned download is saved to `/tmp/a` and extracted with a forced format, in separate RUNs or in one
- **THEN** the guard reports no offender

#### Scenario: Each curl transfer pairs with its own output
- **WHEN** one invocation reads `curl -o /tmp/moving <moving> -o /tmp/pinned <pinned>` and `/tmp/pinned` is extracted with a forced format, in the same RUN or a later one
- **THEN** the guard reports no offender
- **AND** extracting `/tmp/moving` with a forced format is reported, whichever transfer comes first
- **AND** `wget -O /tmp/all <pinned> <moving>` marks `/tmp/all` moving, because wget writes every URL to that one file

#### Scenario: An unpaired curl invocation is read conservatively
- **WHEN** a curl invocation carries a bare word that is not a URL, such as the argument of `--header "Accept: x"`, together with `-o /tmp/moving <moving>`
- **THEN** `/tmp/moving` is recorded as moving from the invocation as a whole
- **AND** a forced extraction of it in a later RUN is reported
