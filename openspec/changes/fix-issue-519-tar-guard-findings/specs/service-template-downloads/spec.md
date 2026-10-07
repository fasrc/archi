## ADDED Requirements

### Requirement: The guard reads only the shell a Dockerfile runs

The download guard SHALL decide whether a command is moving, and which tar it runs, from the words the shell runs, never from comments, heredoc bodies, or Dockerfile instructions that run no shell.

A URL in a `#` comment does not make a command moving. Heredoc body lines are not parsed as
shell. `FROM` starts a new build stage with no provenance, and `COPY` or `ADD` to a path
replaces what a download wrote there.

#### Scenario: A URL in a comment does not mark the command moving

- **WHEN** the guard reads `RUN tar -xzf pinned-v1.tar.gz # https://download.mozilla.org/`
- **THEN** it reports nothing

#### Scenario: A heredoc body is not parsed as shell

- **WHEN** the guard reads `RUN <<EOF`, a prose line `This archive isn't forced.`, and `EOF`
- **THEN** it reports nothing
- **WHEN** a moving download saves `/tmp/a` and a later `RUN cat <<EOF > /tmp/notes` has the body line `tar -xzf /tmp/a`
- **THEN** it reports nothing

#### Scenario: A later build stage starts with no provenance

- **WHEN** stage one saves a moving download to `/tmp/a`, a later `FROM` starts stage two, `COPY pinned.tar.gz /tmp/a` runs, and `RUN tar -xzf /tmp/a` follows
- **THEN** the guard reports nothing

#### Scenario: COPY --from an earlier stage carries that stage's provenance

- **WHEN** stage `one` saves a moving download to `/tmp/a`, a later stage runs `COPY --from=one /tmp/a /tmp/a`, and `RUN tar -xzf /tmp/a` follows
- **THEN** the guard reports `-xzf`

#### Scenario: An unterminated heredoc fails closed

- **WHEN** the guard reads `RUN cat <<EOF` and no later line is `EOF`
- **THEN** it reports an `unparseable command` entry that names the unterminated heredoc

#### Scenario: COPY to a moving path clears it within one stage

- **WHEN** a moving download saves `/tmp/a`, then `COPY pinned.tar.gz /tmp/a`, then `RUN tar -xzf /tmp/a`
- **THEN** the guard reports nothing

### Requirement: A tar that writes its archive is not an extraction

The download guard SHALL NOT report a tar invocation in create, append, update, or concatenate mode, because its `-f` archive is an output.

#### Scenario: tar -c on a moving path is clean

- **WHEN** a moving download saves `/tmp/a` and a later `RUN tar -czf /tmp/a /opt/data` runs
- **THEN** the guard reports nothing

#### Scenario: An extraction of an archive named c is still an extraction

- **WHEN** a moving download saves `c` and `tar -xzf c` follows in the same RUN
- **THEN** the guard reports `-xzf`

### Requirement: The guard finds tar where the shell runs it

The download guard SHALL find a tar invocation behind a shell reserved word, behind `sh -c --`, in GNU traditional option style, and with its archive on stdin through `<`.

#### Scenario: tar after then is found

- **WHEN** a moving download saves `/tmp/a` and `RUN if test -f /tmp/a; then tar -xzf /tmp/a; fi` follows
- **THEN** the guard reports `-xzf`

#### Scenario: Traditional option style is read as a cluster

- **WHEN** a moving download saves `/tmp/a` and `RUN tar xzf /tmp/a` follows
- **THEN** the guard reports `xzf`

#### Scenario: Old-style option words give arguments in letter order

- **WHEN** a moving download saves `/tmp/a` and `RUN tar xzCf /opt /tmp/a` follows
- **THEN** the guard reports `xzCf`

#### Scenario: The script after sh -c -- is read

- **WHEN** a moving download saves `/tmp/a` and `RUN sh -c -- 'tar -xzf /tmp/a'` follows
- **THEN** the guard reports `-xzf`

#### Scenario: An archive on stdin is read

- **WHEN** a moving download saves `/tmp/a` and `RUN tar -xzf - </tmp/a` follows
- **THEN** the guard reports `-xzf`

### Requirement: The guard's accepted limits are pinned

The download guard SHALL keep a test for each accepted limit that asserts today's verdict and carries an `accepted limit` comment, so a change that closes a limit is a visible diff.

The accepted limits are the rows 7–14 of issue #519, plus two that this change creates.
"Once moving, stays moving" holds inside one wget invocation, whose `-O` file is moving when
any of its URLs is moving; across download invocations the last write to a path wins.

#### Scenario: The accepted limits keep today's verdict

- **WHEN** the guard reads each of these, after a moving download to `/tmp/a` where the row needs one
- **THEN** it reports nothing for: row 7, exec form `RUN ["tar", "-xzf", "/tmp/a"]`; row 8, the GNU abbreviation `tar --gzi -xf /tmp/a`; row 9, `TAR_OPTIONS=-z tar -xf /tmp/a`; row 10, `sh -ec 'tar -xzf /tmp/a'`; row 11, `cd /tmp && wget -O a <moving> && tar -xzf /tmp/a`; row 12, `wget -O /tmp/a <moving> || wget -O /tmp/a <pinned>` then `tar -xzf /tmp/a`; row 14, a moving URL carried by `ENV` into `wget -O /tmp/a "$FIREFOX_URL"`; and a `RUN <<EOF` script body that downloads and extracts
- **THEN** it reports `-xzf` for row 13, `curl -o /tmp/a --referer <url> <moving>` then `tar -xzf /tmp/a`, through the whole-command fallback
- **THEN** it reports `-xzf` for `tar -czf /tmp/a /opt` followed by `tar -xzf /tmp/a` after a moving save, because a create-mode tar does not clear a moving path

#### Scenario: The live templates stay clean

- **WHEN** the guard reads each of the six templates that fetch the moving Mozilla URL
- **THEN** it reports nothing for each
