## ADDED Requirements

### Requirement: The download guard reads only the forms its contract names
The download guard SHALL read a Dockerfile only through the written contract in design D31: comment lines dropped, continuations joined, shell-form `RUN` instructions that name `tar`, `wget`, or `curl` (or hold a moving URL, `TAR_OPTIONS`, or `TAPE`) split on `&&`, `;`, and `|`, plain `tar` with dash, long, or traditional options, and plain `wget`/`curl` arguments.

A URL in a `#` comment is not a word, so it does not make a command moving. Paths are
normalised, and a relative path matches a saved path by basename.

#### Scenario: A URL in a comment does not mark the command moving
- **WHEN** the guard reads `RUN tar -xzf pinned-v1.tar.gz # https://download.mozilla.org/`
- **THEN** it reports nothing

#### Scenario: Traditional option style is read
- **WHEN** a moving download saves `/tmp/a` and `RUN tar xzf /tmp/a` follows
- **THEN** the guard reports `xzf`

#### Scenario: Modern options after a traditional word are read
- **WHEN** a moving download saves `/tmp/a` and `RUN tar xf /tmp/a -z` follows
- **THEN** the guard reports `-z`

#### Scenario: Compare mode is a read mode
- **WHEN** a moving download saves `/tmp/a` and `RUN tar dzf /tmp/a` follows
- **THEN** the guard reports `dzf`

#### Scenario: A relative save matches an absolute extraction by basename
- **WHEN** the guard reads `RUN cd /tmp && wget -O a <moving> && tar -xzf /tmp/a`
- **THEN** it reports `-xzf`

### Requirement: A tar that writes its archive is not an extraction
The download guard SHALL NOT report a tar invocation in create, append, update, concatenate, or delete mode, because its `-f` archive is an output.

#### Scenario: tar -c on a moving path is clean
- **WHEN** a moving download saves `/tmp/a` and a later `RUN tar -czf /tmp/a /opt/data` runs
- **THEN** the guard reports nothing

#### Scenario: A traditional word with the mode after f is a write
- **WHEN** a moving download saves `/tmp/a` and `RUN tar fcz /tmp/a /opt` follows
- **THEN** the guard reports nothing

### Requirement: Every form outside the contract fails closed
The download guard SHALL report each instruction it does not read under the contract as `unparseable command '<instruction>': <reason> — needs review`, and SHALL NOT read it further.

The unparseable forms are listed in design D31: heredocs and here-strings (the scan stops
there), exec-form `RUN`, a second `FROM`, `SHELL`, `ADD` of a URL, `ENV`/`ARG` with a moving
URL, `TAR_OPTIONS`, or `TAPE`, an unterminated quote, `||`, `&`, `|&`, `;;`, parentheses,
command substitution, input redirection, output redirection on `wget`/`curl`, an assignment
in front of `tar`/`wget`/`curl`, a command word holding `$`, a reserved word or brace as the
command, a shell or `eval`/`source`/`.`, any other command that carries `tar`, `wget`,
`curl`, or a shell as a word (except `echo`, `printf`, and package installers), a moving URL
outside `wget`/`curl`, an unknown or abbreviated tar option, a tar with no mode or two modes
or two `-f`, a forcing tar whose archive holds `$`, and a `wget`/`curl` word holding `$`.

#### Scenario: Each finding from issue 519 and PR 626 outside the contract fails closed
- **WHEN** the guard reads, after a moving save to `/tmp/a` where the form needs one, any of: `RUN <<EOF` with a body, `RUN cat <<- EOF`, a second `FROM`, `RUN if test -f /tmp/a; then tar -xzf /tmp/a; fi`, `RUN ["tar", "-xzf", "/tmp/a"]`, `RUN tar --gzi -xf /tmp/a`, `RUN TAR_OPTIONS=-z tar -xf /tmp/a`, `RUN sh -ec 'tar -xzf /tmp/a'`, `RUN wget -O /tmp/a <moving> || wget -O /tmp/a <pinned>`, `ENV FIREFOX_URL=<moving>`, `RUN wget -O /tmp/a "$FIREFOX_URL"`, `RUN sh -c -- 'tar -xzf /tmp/a'`, `RUN tar -xzf - </tmp/a`, `RUN sh -- -c -- 'tar -xzf /tmp/a'`, `RUN 'then' tar -xzf /tmp/a`, `RUN TAPE=/tmp/p tar -xz`, `RUN for tar in -xzf /tmp/a; do :; done`, `RUN f() { tar -xzf /tmp/a; }`, `RUN bash -c 'coproc tar -xzf /tmp/a; wait'`, or `RUN sudo tar -xzf /tmp/a`
- **THEN** it reports one entry that starts with `unparseable command` and ends with `needs review`, and no forcing option for that instruction

#### Scenario: A heredoc stops the scan
- **WHEN** the guard reads `RUN cat <<EOF` followed by more lines
- **THEN** it reports the heredoc instruction as unparseable and reads nothing after it

### Requirement: A reviewed instruction is allowed by name
The download guard SHALL skip an unparseable instruction only when its exact text is listed for its template in the commented allow list `_REVIEWED_UNPARSEABLE`, and SHALL fail when an allow-list entry is stale or holds a moving URL.

#### Scenario: A listed instruction is skipped
- **WHEN** a template holds an unparseable instruction whose exact text is listed for that template
- **THEN** the guard reports nothing for it

#### Scenario: A stale entry fails
- **WHEN** an allow-list entry names no unparseable instruction of its template
- **THEN** the allow-list test fails

#### Scenario: The live templates pass
- **WHEN** the guard reads each service template with its allow-list entries
- **THEN** it reports nothing for each
