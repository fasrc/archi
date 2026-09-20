"""Service templates must not hard-code a compression format for a moving download.

Three service templates fetched Firefox ESR from Mozilla's "latest" endpoint and
extracted it with ``tar -xjf``, which forces bzip2. Mozilla now serves that endpoint
as xz, so bzip2 rejected the file and the build died::

    tar: Child returned status 2
    tar: Error is not recoverable: exiting now

The endpoint has no version in it — ``?product=firefox-esr-latest-ssl`` — so what
arrives can change format without anything in this repository changing. Forcing a
decompressor on that is the defect; ``tar -xf`` reads the format from the file.

Found on 2026-09-16 by building every service image from scratch while verifying #472,
which is unrelated to it. Three of the six templates that fetch Firefox had already
been moved to xz and three had not, so this is also a half-finished fix: the guard
below covers the class rather than the three instances, because the next template
added by copy-paste would otherwise reintroduce it.

A pre-merge job builds only the chatbot slice — ``Dockerfile-chat``,
``Dockerfile-postgres``, ``Dockerfile-data-manager`` — and none of the six templates
that fetch this download is in that slice; they are ``Dockerfile-grader``,
``Dockerfile-grader-gpu``, ``Dockerfile-chat-gpu``, ``Dockerfile-data-manager-gpu``,
``Dockerfile-mattermost-gpu``, and ``Dockerfile-benchmarks-gpu``. These templates
had been unbuildable on ``dev`` for as long as Mozilla has served xz.
"""

import re

import pytest

from src.cli.managers.base_image_preflight import service_templates

# EVERY tar option that forces a compression program, not just the one this defect
# happened to involve. Two-step token scan: walk the command's tokens to find each
# ``tar`` invocation bounded by shell separators (``&&``, ``||``, ``;``, ``|``); then
# inspect that invocation's option tokens (those beginning with ``-``). Long forms are
# matched whole after stripping ``=PROG`` — so ``--no-auto-compress`` and
# ``--exclude=*.gz`` stay clean. Short clusters are matched case-sensitively — ``-i``
# (``--ignore-zeros``) differs from ``-I`` (``--use-compress-program``) by case alone.
_FORCING_LONG = frozenset(
    {
        "--gzip",
        "--gunzip",
        "--ungzip",
        "--bzip2",
        "--xz",
        "--lzma",
        "--lzop",
        "--zstd",
        "--lzip",
        "--compress",
        "--uncompress",
        "--use-compress-program",
    }
)
_FORCING_SHORT = frozenset("zjJZI")
_STDOUT_SINKS = frozenset({"-", "/dev/stdout", "/dev/null"})


class _Operator(str):
    """A token the shell reads as an operator, as opposed to a word that spells one.

    ``echo ';'`` passes a word to echo; the ``;`` in ``a;b`` ends a command. Both are
    the string ``;`` once the quotes are resolved, so the lexer marks the operator.
    """


# A redirection operator: ``>``, ``>>``, ``<``, ``>&``, ``&>`` and the rest, with an
# optional descriptor in front (``2>``) and, for the dup forms, a descriptor or ``-``
# behind (``2>&1``, ``>&-``). A dup form has no target word; every other form consumes
# the word that follows it.
_REDIRECTION = re.compile(r"^(?:\d*(?:>>|>&|>\||<<<|<<|<&|<>|>|<)(?:\d+|-)?|&>>?)$")
_REDIRECTION_DUP = re.compile(r"&(?:\d+|-)$")

# tar's short options that consume an argument: the rest of their own cluster when one
# is attached, otherwise the next token. ``-f`` names the archive; ``-I`` names a
# compression program and is a forcing option as well. Review on 2026-09-19: without
# this set, ``tar -xf/tmp/firefox.tar.xz`` read the ``z`` in the FILENAME as a forcing
# option, so a correct auto-detecting extraction was reported as forcing xz.
_SHORT_WITH_ARGUMENT = frozenset("bCfFgHIKLNTVX")

# The long options that name the archive. ``--file=PATH`` and ``--file PATH`` both.
_ARCHIVE_LONG = frozenset({"--file"})

# tar's long options whose argument is REQUIRED, so it is the next token when no ``=``
# is attached. Measured on GNU tar 1.35 (``tar --help``, every ``--name=ARG`` entry).
# The six ``[=ARG]`` entries — ``--atime-preserve``, ``--backup``, ``--checkpoint``,
# ``--occurrence``, ``--one-top-level``, ``--totals`` — take a value only when it is
# attached, so they are deliberately absent. Review on 2026-09-20: without this set,
# ``tar --exclude --gzip -xf /tmp/moving`` read the exclusion PATTERN as a forcing
# option and rejected a correct template.
_LONG_WITH_ARGUMENT = frozenset(
    {
        "--add-file",
        "--after-date",
        "--blocking-factor",
        "--checkpoint-action",
        "--directory",
        "--exclude",
        "--exclude-from",
        "--exclude-ignore",
        "--exclude-ignore-recursive",
        "--exclude-tag",
        "--exclude-tag-all",
        "--exclude-tag-under",
        "--file",
        "--files-from",
        "--format",
        "--group",
        "--group-map",
        "--hole-detection",
        "--index-file",
        "--info-script",
        "--label",
        "--level",
        "--listed-incremental",
        "--mode",
        "--mtime",
        "--newer",
        "--newer-mtime",
        "--new-volume-script",
        "--no-quote-chars",
        "--owner",
        "--owner-map",
        "--quote-chars",
        "--quoting-style",
        "--record-size",
        "--rmt-command",
        "--rsh-command",
        "--sort",
        "--sparse-version",
        "--starting-file",
        "--strip-components",
        "--suffix",
        "--tape-length",
        "--to-command",
        "--transform",
        "--use-compress-program",
        "--volno-file",
        "--warning",
        "--xattrs-exclude",
        "--xattrs-include",
        "--xform",
    }
)

# The tools whose saved-output option tells the guard where a download landed, with the
# short LETTER and the long option that name that destination. Both accept the value
# attached to the short form: ``wget -O/tmp/x`` and ``curl -o/tmp/x`` are valid, and
# curl's own manual says a short option may be used "with or without a space between
# it and its value".
_DOWNLOAD_OUTPUT_OPTIONS = {
    "wget": ("O", "--output-document"),
    "curl": ("o", "--output"),
}

# Each tool's short options that take an argument: the rest of their own cluster when
# one is attached, otherwise the next token. Value-less flags may precede them in the
# same cluster — ``curl -sLo/tmp/x`` is ``-s -L -o/tmp/x`` and ``wget -qO /tmp/x`` is
# ``-q -O /tmp/x``. Measured from ``curl --help all`` (curl 8.21.0; ``-h`` omitted, its
# subject is optional) and ``wget --help`` (GNU Wget 1.25.0). Review on 2026-09-20:
# matching the token prefix ``-o`` missed every clustered spelling, and the saved path
# was lost silently.
_DOWNLOAD_SHORT_WITH_ARGUMENT = {
    "wget": frozenset("aABDeiIloOPQRtTUwX"),
    "curl": frozenset("AbcCdDeEFHKmoPQrtTuUwxXyYz"),
}


def _shell_tokens(command: str) -> list[str]:
    """``command`` split into words and operators the way the shell splits it.

    Words are delimited by unquoted whitespace, with quotes resolved: ``"/tmp/my
    moving.tar"``, ``'/tmp/my moving.tar'`` and ``/tmp/my\\ moving.tar`` are all the
    one word ``/tmp/my moving.tar``. Control operators (``&&``, ``||``, ``;``, ``|``,
    ``&``, ``(``, ``)``) and redirection operators (``>``, ``>>``, ``<``, ``2>&1``,
    ``&>`` …) are isolated whether or not whitespace surrounds them, and returned as
    :class:`_Operator` so a quoted word that spells one is not mistaken for one. An
    unquoted ``#`` at the start of a word begins a comment.

    Review on 2026-09-19: ``wget …&&tar …`` hid both the URL and the tar token, so a
    forced extraction of a moving download read as clean. Review on 2026-09-20:
    ``str.split`` broke a quoted path with a space into two words, and
    ``tar -xzf /tmp/moving>/dev/null`` kept the redirection glued to the archive, so
    the saved path stopped matching and the forced decompressor escaped.

    Raises ``ValueError`` on an unterminated quote. The guard cannot say which file
    such a command reads, and :func:`_offenders` reports that rather than passing it.
    """
    tokens: list[str] = []
    word: list[str] = []
    in_word = False
    i = 0
    n = len(command)

    def flush() -> None:
        nonlocal in_word
        if in_word:
            tokens.append("".join(word))
        word.clear()
        in_word = False

    while i < n:
        c = command[i]
        if c in " \t\n":
            flush()
            i += 1
        elif c == "#" and not in_word:
            break
        elif c == "'":
            end = command.find("'", i + 1)
            if end < 0:
                raise ValueError("unterminated single quote")
            word.append(command[i + 1 : end])
            in_word = True
            i = end + 1
        elif c == '"':
            i += 1
            while i < n and command[i] != '"':
                if command[i] == "\\" and i + 1 < n and command[i + 1] in '"$`\\':
                    i += 1
                word.append(command[i])
                i += 1
            if i >= n:
                raise ValueError("unterminated double quote")
            in_word = True
            i += 1
        elif c == "\\":
            if i + 1 < n:
                word.append(command[i + 1])
                in_word = True
            i += 2
        elif c in "<>":
            # Digits immediately in front of the operator are its descriptor: ``2>``.
            descriptor = ""
            if in_word and "".join(word).isdigit():
                descriptor = "".join(word)
                word.clear()
                in_word = False
            flush()
            operator = c
            j = i + 1
            if j < n and command[j] in (">&|" if c == ">" else "<&>"):
                operator += command[j]
                j += 1
                if operator == "<<" and j < n and command[j] == "<":
                    operator += "<"
                    j += 1
            if operator.endswith("&"):
                dup = re.match(r"(\d+|-)(?=$|[\s;&|<>()])", command[j:])
                if dup:
                    operator += dup.group(1)
                    j += dup.end()
            tokens.append(_Operator(descriptor + operator))
            i = j
        elif c in "&|;":
            flush()
            if command.startswith("&>>", i):
                operator = "&>>"
            elif command[i : i + 2] in ("&&", "&>", "||", "|&", ";;"):
                operator = command[i : i + 2]
            else:
                operator = c
            tokens.append(_Operator(operator))
            i += len(operator)
        elif c in "()" and not (c == "(" and word and word[-1] == "$"):
            flush()
            tokens.append(_Operator(c))
            i += 1
        else:
            word.append(c)
            in_word = True
            i += 1
    flush()
    return tokens


def _simple_commands(command: str) -> list[list[str]]:
    """``command`` as the shell's simple commands: one argv each, redirections removed.

    Control operators bound the commands. A redirection operator and its target word
    are the shell's business and never reach the program's argv, so
    ``tar -xzf /tmp/moving>/dev/null`` hands tar exactly ``-xzf /tmp/moving``.
    """
    commands: list[list[str]] = []
    argv: list[str] = []
    tokens = _shell_tokens(command)
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not isinstance(token, _Operator):
            argv.append(token)
        elif _REDIRECTION.match(token):
            if not _REDIRECTION_DUP.search(token):
                i += 1  # the target word
        else:
            if argv:
                commands.append(argv)
            argv = []
        i += 1
    if argv:
        commands.append(argv)
    return commands


def _basename(token: str) -> str:
    """The command name of ``token``, so ``/bin/tar`` is recognised as ``tar``."""
    return token.strip("\"'").rsplit("/", 1)[-1]


def _parse_tar_span(span: list[str]) -> tuple[list[str], str | None]:
    """One tar invocation's forcing options and the archive it reads.

    The archive comes from ``-f``/``--file`` only. An operand is never read as the
    archive: without ``-f`` tar reads its default device or stdin, and the operands
    are member names.
    """
    forcing = []
    archive = None
    end_of_options = False
    i = 0
    while i < len(span):
        token = span[i]
        if end_of_options or not token.startswith("-") or token == "-":
            i += 1
            continue
        if token == "--":
            # Everything after tar's end-of-options marker is an operand, including a
            # member literally named ``--gzip``.
            end_of_options = True
            i += 1
            continue
        if token.startswith("--"):
            name, separator, attached = token.partition("=")
            if name in _FORCING_LONG:
                forcing.append(token)
            if name in _ARCHIVE_LONG:
                if separator:
                    archive = attached
                elif i + 1 < len(span):
                    i += 1
                    archive = span[i]
            elif name in _LONG_WITH_ARGUMENT and not separator and i + 1 < len(span):
                # The next token is this option's argument, not another option.
                i += 1
            i += 1
            continue
        cluster = token[1:]
        forces = False
        for position, character in enumerate(cluster):
            if character in _FORCING_SHORT:
                forces = True
            if character in _SHORT_WITH_ARGUMENT:
                attached = cluster[position + 1 :]
                if character == "f":
                    if attached:
                        archive = attached
                    elif i + 1 < len(span):
                        i += 1
                        archive = span[i]
                elif not attached and i + 1 < len(span):
                    i += 1
                # The rest of the cluster is this option's argument, not more flags.
                break
        if forces:
            forcing.append(token)
        i += 1
    return forcing, archive


def _tar_invocations(command: str) -> list:
    """Every tar invocation in ``command``, one per simple command that runs tar."""
    invocations = []
    for argv in _simple_commands(command):
        for position, token in enumerate(argv):
            if _basename(token) == "tar":
                invocations.append(_parse_tar_span(argv[position + 1 :]))
                break
    return invocations


def _forced_decompressors(command: str) -> list[str]:
    """Every forcing option of every tar invocation in ``command``."""
    return [option for forcing, _ in _tar_invocations(command) for option in forcing]


class _ForcedDecompressorScanner:
    def findall(self, command: str) -> list[str]:
        return _forced_decompressors(command)


_FORCED_DECOMPRESSOR = _ForcedDecompressorScanner()
# A download URL that names no version, so its payload can change under us.
_MOVING_DOWNLOAD = re.compile(r"download\.mozilla\.org|[?&]product=[^\s\"']*latest")


def _templates():
    found = service_templates()
    assert found, "no service templates found; base_image_preflight may have moved"
    return sorted(found)


def _commands(text: str) -> list:
    """``text`` split into shell commands, with backslash continuations joined.

    The pairing matters and is why this is not a whole-file scan. Forcing a
    decompressor is only wrong on a MOVING download: these templates also fetch
    geckodriver from a URL that names ``v0.36.0``, where ``tar -xzf`` is correct
    because the payload cannot change format underneath it. A file-wide regex flagged
    that legitimate line as soon as the decompressor check was widened past bzip2, so
    the check has to see which download each extraction belongs to.
    """
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    return [line for line in joined.splitlines() if line.strip()]


def _download_invocations(command: str) -> list:
    """Every wget or curl invocation in ``command`` as ``(saved paths, its tokens)``.

    Every spelling of the destination option is read: spaced (``-O /tmp/x``), attached
    short (``-O/tmp/x``), spaced long (``--output-document /tmp/x``) and attached long
    (``--output-document=/tmp/x``). Round 2 on 2026-09-19 found the attached long form
    missing, which loses the saved path silently — and branch 2 only reaches within the
    download's own command, so a forced extraction in a LATER RUN then read as clean.

    Pairing each destination with the invocation that wrote it is what keeps a pinned
    download out of the moving set. Review on 2026-09-19: every destination in a
    command containing any moving URL was recorded as moving, so
    ``wget -O /tmp/moving <latest> && wget -O /tmp/pinned <versioned>`` made a correct
    ``tar -xzf /tmp/pinned`` an offender.
    """
    invocations = []
    for argv in _simple_commands(command):
        for position, token in enumerate(argv):
            name = _basename(token)
            options = _DOWNLOAD_OUTPUT_OPTIONS.get(name)
            if options is None:
                continue
            output_letter, long_form = options
            with_argument = _DOWNLOAD_SHORT_WITH_ARGUMENT[name]
            span = argv[position + 1 :]
            paths = set()
            i = 0
            while i < len(span):
                word = span[i]
                if word == long_form:
                    if i + 1 < len(span):
                        i += 1
                        paths.add(span[i])
                elif word.startswith(f"{long_form}="):
                    paths.add(word[len(long_form) + 1 :])
                elif word.startswith("-") and word != "-" and not word.startswith("--"):
                    # A short-option cluster: value-less flags until the first option
                    # that takes an argument, which consumes the rest of the cluster
                    # or, when nothing is attached, the next word.
                    cluster = word[1:]
                    for offset, character in enumerate(cluster):
                        if character not in with_argument:
                            continue
                        attached = cluster[offset + 1 :]
                        if character == output_letter:
                            if attached:
                                paths.add(attached)
                            elif i + 1 < len(span):
                                i += 1
                                paths.add(span[i])
                        elif not attached and i + 1 < len(span):
                            i += 1
                        break
                i += 1
            invocations.append((paths - _STDOUT_SINKS, " ".join(span)))
            break
    return invocations


def _moving_saved_paths(command: str) -> set:
    """Paths saved by the MOVING downloads of ``command``, and by no other download."""
    result = set()
    for paths, text in _download_invocations(command):
        if _MOVING_DOWNLOAD.search(text):
            result |= paths
    return result


def _archive_matches_saved(archive, saved: set) -> bool:
    """True when the archive reference IS a saved path, by full path or by basename.

    Whole-value comparison, not containment. Review on 2026-09-19: ``path in token``
    made the saved ``/tmp/a`` match a pinned ``/tmp/archive-v1.tar.gz``, and the
    basename branch made ``a`` match ``a.tar.old``.
    """
    if archive is None:
        return False
    reference = archive.strip("\"'")
    for path in saved:
        if reference == path:
            return True
        if "/" not in reference and reference == path.rsplit("/", 1)[-1]:
            return True
    return False


def _archive_is_unresolvable(archive) -> bool:
    """True when the guard cannot tell which file this tar reads.

    Only the archive reference is consulted. Review on 2026-09-19: a ``$`` anywhere in
    the invocation counted, so ``tar -xzf pinned-v1.tar.gz -C "$DEST"`` was called
    unresolvable because its extraction DIRECTORY was a variable. No ``-f`` at all
    means tar reads stdin or its default device, which is equally unresolvable.
    """
    if archive is None:
        return True
    reference = archive.strip("\"'")
    return "$" in reference or reference in _STDOUT_SINKS


def _offenders(text: str) -> list:
    """Forcing options on tar invocations that extract a moving download.

    Three branches decide each forcing invocation:
    1. A known saved path appears in the invocation's token span — indict.
    2. The invocation shares a command with a moving download and the guard
       cannot resolve which file it reads — indict conservatively.
    3. Otherwise — clean.
    """
    result = []
    commands = []
    for command in _commands(text):
        try:
            _shell_tokens(command)
        except ValueError as exc:
            # Fail closed: a command the guard cannot read is reported, never passed.
            result.append(f"unparseable command {command.strip()!r}: {exc}")
        else:
            commands.append(command)
    saved = set()
    for command in commands:
        saved |= _moving_saved_paths(command)
    for command in commands:
        is_moving = bool(_MOVING_DOWNLOAD.search(command))
        command_saved = _moving_saved_paths(command) if is_moving else set()
        for forcing, archive in _tar_invocations(command):
            if not forcing:
                continue
            if saved and _archive_matches_saved(archive, saved):
                result.extend(forcing)
                continue
            if is_moving and (not command_saved or _archive_is_unresolvable(archive)):
                result.extend(forcing)
    return result


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_a_moving_download_is_not_extracted_with_a_forced_decompressor(template):
    """A versionless download must be extracted with format auto-detection."""
    content = template.read_text(encoding="utf-8")
    if not _MOVING_DOWNLOAD.search(content):
        pytest.skip(f"{template.name} fetches no versionless archive")

    forced = _offenders(content)
    assert not forced, (
        f"{template.name} extracts a versionless download with {forced}, which forces "
        f"a compression program. The endpoint carries no version, so its payload can "
        f"change format without anything here changing — pinning the decompressor is "
        f"the defect whichever format is pinned. Mozilla serves xz today, so `-xjf` "
        f"failed with 'tar: Child returned status 2'; `-xzf` would fail the same way. "
        f"Use `tar -xf` and let tar detect the format."
    )


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_the_saved_filename_does_not_claim_a_format_it_cannot_guarantee(template):
    """Saving a versionless download as ``.tar.bz2`` misstates what arrived.

    This is cosmetic on its own — ``tar -xf`` ignores the name — but a wrong extension
    is what invites the next person to add a matching ``-xjf``, which is exactly how
    this defect survived in half the templates.
    """
    content = template.read_text(encoding="utf-8")
    if not _MOVING_DOWNLOAD.search(content):
        pytest.skip(f"{template.name} fetches no versionless archive")

    claimed = [
        command
        for command in _commands(content)
        if _MOVING_DOWNLOAD.search(command) and ".tar.bz2" in command
    ]
    assert not claimed, (
        f"{template.name} saves a versionless download as .tar.bz2, but the endpoint "
        f"serves xz. Name it .tar.xz so the file and the extraction agree."
    )


class TestTheGuardRejectsEveryForcedDecompressor:
    """The invariant is auto-detection, so every forcing option must trip it.

    Review on 2026-09-16: the guard claimed auto-detection but recognised only bzip2,
    so an edit to ``tar -xzf`` on the same moving URL would have stayed green while
    failing the image exactly as ``-xjf`` did.
    """

    @pytest.mark.parametrize(
        "command",
        [
            "tar -xjf /tmp/f.tar.bz2 -C /opt/",
            "tar -xzf /tmp/f.tar.gz -C /opt/",
            "tar -xJf /tmp/f.tar.xz -C /opt/",
            "tar -xZf /tmp/f.tar.Z -C /opt/",
            "tar --gzip -xf /tmp/f -C /opt/",
            "tar --bzip2 -xf /tmp/f -C /opt/",
            "tar --xz -xf /tmp/f -C /opt/",
            "tar --zstd -xf /tmp/f -C /opt/",
            "tar --lzma -xf /tmp/f -C /opt/",
            "tar -x --gzip -f /tmp/f",
            "tar -x -z -f /tmp/f",
            "tar --lzip -xf /tmp/f",
            "tar --uncompress -xf /tmp/f",
            "tar -I zstd -xf /tmp/f",
            "tar --use-compress-program=zstd -xf /tmp/f",
        ],
    )
    def test_a_forced_format_is_detected(self, command):
        assert _FORCED_DECOMPRESSOR.findall(command), (
            f"{command!r} forces a compression program and must be rejected on a "
            f"versionless download; the guard's invariant is auto-detection."
        )

    @pytest.mark.parametrize(
        "command",
        [
            "tar -xf /tmp/f.tar.xz -C /opt/",
            "tar -xvf /tmp/f.tar -C /opt/",
            "tar --extract --file /tmp/f -C /opt/",
        ],
    )
    def test_auto_detection_is_left_alone(self, command):
        assert not _FORCED_DECOMPRESSOR.findall(
            command
        ), f"{command!r} lets tar detect the format and must not trip the guard."

    @pytest.mark.parametrize(
        "command",
        [
            "tar -a -xf /tmp/f.tar.gz",
            "tar --auto-compress -xf /tmp/f",
            "tar --no-auto-compress -xf /tmp/f",
            "tar -xif /tmp/f.tar",
            "tar --exclude=*.gz -xf /tmp/f",
        ],
    )
    def test_near_misses_are_not_flagged(self, command):
        assert not _FORCED_DECOMPRESSOR.findall(
            command
        ), f"{command!r} does not force a decompressor and must not trip the guard."

    def test_separator_bounds_each_tar_invocation(self):
        """A shell separator ends a tar invocation; adjacent commands cannot bleed."""
        # A command after the separator must not contaminate the preceding clean tar.
        assert not _forced_decompressors(
            "tar -xf /tmp/f.tar.xz -C /opt/ && gzip -d /tmp/other.gz"
        ), "gzip after && must not be attributed to the preceding tar scan"
        # A second tar after the separator is scanned independently and indicted on its own.
        offenders = _forced_decompressors(
            "tar -xf /tmp/a.tar && tar -xzf /tmp/b.tar.gz"
        )
        assert len(offenders) == 1, (
            f"exactly one forcing option expected from the second tar invocation; "
            f"got {offenders}"
        )


class TestAVersionedDownloadMayForceItsFormat:
    """A pinned URL cannot change format underneath us, so forcing is fine there.

    These templates fetch geckodriver from a URL naming ``v0.36.0`` and extract it with
    ``tar -xzf``. That is correct, and a file-wide scan wrongly condemned it once the
    decompressor check widened past bzip2 — which is what forced the per-command pairing
    in ``_commands``.
    """

    def test_a_pinned_url_with_a_forced_format_is_accepted(self):
        command = (
            'RUN wget -q "https://github.com/mozilla/geckodriver/releases/download/'
            'v0.36.0/geckodriver-v0.36.0-linux64.tar.gz" && '
            'tar -xzf "geckodriver-v0.36.0-linux64.tar.gz" -C /usr/local/bin'
        )
        assert not _MOVING_DOWNLOAD.search(command), (
            "a URL naming v0.36.0 is not a moving download, so the guard must not "
            "reach its forced -xzf at all"
        )

    def test_the_two_are_not_paired_across_separate_commands(self):
        """A moving download in one command must not indict another command's format."""
        text = (
            "RUN wget -O /tmp/f.tar.xz "
            '"https://download.mozilla.org/?product=firefox-esr-latest-ssl" && \\\n'
            "    tar -xf /tmp/f.tar.xz -C /opt/\n"
            'RUN wget -q "https://example.invalid/tool-v1.2.3.tar.gz" && \\\n'
            "    tar -xzf tool-v1.2.3.tar.gz -C /usr/local/bin\n"
        )
        offenders = [
            found
            for command in _commands(text)
            if _MOVING_DOWNLOAD.search(command)
            for found in _FORCED_DECOMPRESSOR.findall(command)
        ]
        assert offenders == [], (
            f"the pinned second command must not be indicted by the moving first one, "
            f"got {offenders}"
        )


def test_forced_tar_in_later_run_is_flagged_via_saved_path():
    """A forcing tar in a separate RUN is indicted when its span contains the saved path."""
    text = (
        "RUN wget -O /tmp/ff.tar "
        '"https://download.mozilla.org/?product=firefox-esr-latest-ssl&os=linux64"\n'
        "RUN tar -xjf /tmp/ff.tar -C /opt\n"
    )
    offenders = _offenders(text)
    assert offenders, (
        f"/tmp/ff.tar is extracted with a forced decompressor in a separate RUN "
        f"instruction; the guard must cross RUN boundaries via the saved path; "
        f"got {offenders!r}"
    )


def test_forced_tar_on_different_file_in_same_run_is_not_flagged():
    """A forcing tar on a pinned file in the same RUN as a correct moving extraction is clean.

    Branch 1 binds the verdict to each tar invocation's own token span, not to the
    whole command — so a second tar that extracts a version-pinned archive is not
    indicted merely because a saved path appears elsewhere in the same command.
    """
    text = (
        'RUN wget -O /tmp/ff.tar.xz "https://download.mozilla.org/?product=firefox-esr-latest-ssl" '
        "&& tar -xf /tmp/ff.tar.xz -C /opt "
        "&& tar -xzf tool-v1.2.3.tar.gz -C /usr/local/bin\n"
    )
    offenders = _offenders(text)
    assert offenders == [], (
        f"tar -xzf operates on tool-v1.2.3.tar.gz, not the saved moving-download path; "
        f"branch 1 must bind to the invocation's own span, not the whole command; "
        f"got {offenders!r}"
    )


def test_stdout_sink_download_is_flagged_via_branch2():
    """A piped download that writes no file is indicted via branch 2 (unresolvable).

    Both the glued form (-O-) and the spaced form (-O -) must not be recorded as
    saved paths — '-' is a stdout sink.  With no saved path, branch 2 fires: the
    invocation shares a command with a moving download and the guard cannot resolve
    which file tar reads.
    """
    glued = (
        'RUN wget -O- "https://download.mozilla.org/?product=firefox-esr-latest-ssl" '
        "| tar -xzf -\n"
    )
    assert _offenders(glued), (
        f"wget -O- pipes to stdout; the guard must not record '-' as a saved path "
        f"and must indict the forcing tar via branch 2; got {_offenders(glued)!r}"
    )
    spaced = (
        'RUN wget -O - "https://download.mozilla.org/?product=firefox-esr-latest-ssl" '
        "| tar -xzf -\n"
    )
    assert _offenders(spaced), (
        f"wget -O - (spaced) also pipes to stdout; the guard must not record '-' as "
        f"a saved path and must indict via branch 2; got {_offenders(spaced)!r}"
    )


def test_archive_path_glued_to_option_is_flagged_via_branch1():
    """A saved path glued to a tar option token is matched in the full span, not operands.

    tar --bzip2 --file=/tmp/ff.tar.xz and tar -xjf/tmp/ff.tar.xz both hide the
    archive path inside an option token.  An operand-only scan would miss them;
    span matching catches both.
    """
    base = 'RUN wget -O /tmp/ff.tar.xz "https://download.mozilla.org/?product=firefox-esr-latest-ssl"\n'
    long_form = base + "RUN tar --bzip2 --file=/tmp/ff.tar.xz\n"
    assert _offenders(long_form), (
        f"--file=/tmp/ff.tar.xz glues the saved path to a long option; span matching "
        f"must find it; got {_offenders(long_form)!r}"
    )
    short_glued = base + "RUN tar -xjf/tmp/ff.tar.xz\n"
    assert _offenders(short_glued), (
        f"-xjf/tmp/ff.tar.xz glues the saved path to the short cluster; span matching "
        f"must find it; got {_offenders(short_glued)!r}"
    )


def test_shell_variable_archive_is_flagged_via_branch2():
    """A tar that names its archive through a shell variable is indicted via branch 2.

    The guard cannot resolve the variable, so the span is unresolvable; branch 2
    fires because the invocation shares a command with a moving download.
    """
    text = (
        'RUN wget -O /tmp/ff.tar.xz "https://download.mozilla.org/?product=firefox-esr-latest-ssl" '
        '&& tar -xjf "$FF"\n'
    )
    offenders = _offenders(text)
    assert (
        offenders
    ), f'tar -xjf "$FF" is unresolvable; branch 2 must indict it; got {offenders!r}'


def test_bare_basename_in_later_run_is_flagged_via_branch1():
    """A tar naming the saved file by basename alone (after cd) is flagged via branch 1.

    A basename token with no '/' matches the saved path's basename, provided the
    candidate token itself carries no '/'.
    """
    text = (
        'RUN wget -O /tmp/ff.tar.xz "https://download.mozilla.org/?product=firefox-esr-latest-ssl"\n'
        "RUN cd /tmp && tar -xjf ff.tar.xz\n"
    )
    offenders = _offenders(text)
    assert offenders, (
        f"ff.tar.xz (bare basename) matches the saved /tmp/ff.tar.xz; "
        f"branch 1 must indict it; got {offenders!r}"
    )


def test_basename_collision_with_pinned_path_is_not_flagged():
    """A later RUN whose archive shares a basename but not the full path is clean.

    /tmp/firefox-esr.tar.xz is saved; /opt/vendor/pinned-9.9.9/firefox-esr.tar.xz
    shares its basename but is a different, explicitly version-pinned file.  Because
    the token carries a '/', basename matching is skipped and only full-path matching
    applies — which does not fire.
    """
    text = (
        'RUN wget -O /tmp/firefox-esr.tar.xz "https://download.mozilla.org/?product=firefox-esr-latest-ssl"\n'
        "RUN tar -xJf /opt/vendor/pinned-9.9.9/firefox-esr.tar.xz\n"
    )
    offenders = _offenders(text)
    assert offenders == [], (
        f"/opt/vendor/pinned-9.9.9/firefox-esr.tar.xz differs from the saved path; "
        f"basename matching must not fire on a token containing '/'; got {offenders!r}"
    )


def test_moving_download_without_saved_path_indicts_own_command_only():
    """A wget with no -O/-o falls to branch 2 (no saved path) within its own command.

    The guard must not guess a saved path from the URL — guessing is how false positives
    land on files the download never wrote.  A forcing tar in the same RUN as the
    wget-without-O is indicted conservatively via branch 2.  A forcing tar in a
    *different* RUN is not indicted, because no saved path was recorded to link them
    and they do not share a command with the moving download.
    """
    # A forcing tar in the same command as wget-without-O: indicted via branch 2.
    same_run = (
        'RUN wget "https://download.mozilla.org/?product=firefox-esr-latest-ssl" '
        "&& tar -xjf firefox-esr.tar.xz -C /opt\n"
    )
    assert _offenders(same_run), (
        f"wget with no -O shares a RUN with a forcing tar; branch 2 must indict it "
        f"because no saved path is known; got {_offenders(same_run)!r}"
    )
    # A forcing tar in a separate RUN: no saved path links it, so it must be clean.
    separate_run = (
        'RUN wget "https://download.mozilla.org/?product=firefox-esr-latest-ssl"\n'
        "RUN tar -xjf firefox-esr.tar.xz -C /opt\n"
    )
    assert _offenders(separate_run) == [], (
        f"wget with no -O in a prior RUN records no saved path; the guard must not "
        f"guess one and indict the later tar; got {_offenders(separate_run)!r}"
    )


class TestTheScannerReadsATarInvocationAsTarDoes:
    """Review on 2026-09-19 (Codex, eight P2 findings): the scanner read a tar
    invocation as whitespace-delimited tokens, so an attached option argument, an
    absolute path to the executable, an attached shell operator and tar's
    end-of-options marker each produced a wrong verdict — in both directions.
    """

    _MOVING = '"https://download.mozilla.org/?product=firefox-esr-latest-ssl"'

    def test_an_attached_archive_argument_is_not_read_as_options(self):
        """``-f`` consumes the remainder of its cluster, so the path is not flags."""
        assert _forced_decompressors("tar -xf/tmp/firefox.tar.xz") == [], (
            "the 'z' in the filename is part of -f's argument, not a forcing option; "
            "GNU tar documents -f as --file=ARCHIVE and extracts the attached form"
        )

    def test_a_forcing_option_before_the_attached_argument_is_still_read(self):
        """The characters before ``f`` are still a flag cluster."""
        assert _forced_decompressors("tar -xjf/tmp/ff.tar.xz"), (
            "-xjf/tmp/ff.tar.xz forces bzip2 before -f consumes the path; narrowing "
            "the cluster scan must not drop the forcing option in front of it"
        )

    def test_an_absolute_path_to_tar_is_recognized(self):
        """``/bin/tar`` is a tar invocation; the exact-token check skipped it."""
        text = (
            f"RUN wget -O /tmp/ff.tar {self._MOVING}\n"
            "RUN /bin/tar -xzf /tmp/ff.tar\n"
        )
        assert _offenders(text), (
            "/bin/tar -xzf on the saved moving download must be indicted; matching "
            "the bare token 'tar' also regressed the earlier unanchored regex"
        )

    def test_an_unrelated_executable_ending_in_tar_is_not_a_tar_invocation(self):
        """Matching the basename must not widen to any name ending in ``tar``."""
        text = (
            f"RUN wget -O /tmp/ff.tar {self._MOVING}\n" "RUN mytar -xzf /tmp/ff.tar\n"
        )
        assert (
            _offenders(text) == []
        ), "mytar is not tar; basename matching must compare the whole basename"

    def test_an_attached_shell_operator_bounds_the_invocation(self):
        """Bash runs ``a;b`` without spaces; the scanner must see two invocations.

        The list of forcing options alone cannot tell the two readings apart, so this
        asserts the attribution: the moving download is extracted correctly by the
        first tar, and the forcing option belongs to the second, which reads a pinned
        archive. Scanned as one invocation, the guard hands the second tar's ``-xzf``
        to the first tar's ``/tmp/moving`` and reports a correct template as broken.
        """
        text = (
            f"RUN wget -O /tmp/moving {self._MOVING} && "
            "tar -xf /tmp/moving;tar -xzf pinned-v1.tar.gz\n"
        )
        assert _offenders(text) == [], (
            f"the ';' bounds two tar invocations; -xzf belongs to the one reading "
            f"pinned-v1.tar.gz, not to the one reading the moving download; "
            f"got {_offenders(text)!r}"
        )
        hidden = f"RUN wget -O /tmp/ff.tar {self._MOVING};tar -xzf /tmp/ff.tar\n"
        assert _offenders(hidden), (
            "the ';' glued to both neighbours hid the tar token altogether, so a "
            "forced extraction of the moving download read as clean"
        )

    def test_an_attached_operator_does_not_hide_a_download_or_a_tar(self):
        """``wget …&&tar …`` must not swallow the tokens it is glued to."""
        text = f"RUN wget -O /tmp/ff.tar {self._MOVING}&&tar -xzf /tmp/ff.tar\n"
        assert _offenders(text), (
            "&& glued to its neighbours hid both the URL and the tar token, so a "
            "forced extraction of the moving download read as clean"
        )

    def test_option_scanning_stops_at_the_end_of_options_marker(self):
        """Tokens after ``--`` are operands, not options."""
        assert _forced_decompressors("tar -xf /tmp/moving -- --gzip") == [], (
            "GNU tar extracts a member literally named --gzip here with format "
            "auto-detection; after -- the scanner must stop recognizing options"
        )


class TestTheGuardBindsEachArchiveToItsOwnDownload:
    """The other half of the same review: which file a tar reads, and where it came
    from. Substring containment, whole-span variable detection and whole-command
    download collection each turned a correct template red.
    """

    _MOVING = '"https://download.mozilla.org/?product=firefox-esr-latest-ssl"'
    _PINNED = '"https://example.invalid/tool-v1.2.3.tar.gz"'

    def test_curl_saves_to_an_attached_short_argument(self):
        """``curl -o/tmp/x`` is valid curl and must record the saved path."""
        text = (
            f"RUN curl -o/tmp/ff.tar.xz {self._MOVING}\n"
            "RUN tar -xzf /tmp/ff.tar.xz\n"
        )
        assert _offenders(text), (
            "curl's short options take an attached value exactly as wget's -OFILE "
            "does; an unrecorded saved path leaves the later forced tar undetected"
        )

    @pytest.mark.parametrize(
        "download",
        [
            "curl --output=/tmp/ff.tar.xz",
            "wget --output-document=/tmp/ff.tar.xz",
            "curl --output /tmp/ff.tar.xz",
            "wget --output-document /tmp/ff.tar.xz",
        ],
    )
    def test_a_long_output_option_records_the_saved_path(self, download):
        """The attached long form saves a file exactly as the spaced form does."""
        text = (
            f"RUN {download} {self._MOVING}\n" "RUN tar -xzf /tmp/ff.tar.xz -C /opt\n"
        )
        assert _offenders(text), (
            f"{download!r} saves the moving download to /tmp/ff.tar.xz; losing that "
            f"association leaves the forced extraction in the later RUN undetected, "
            f"because branch 2 only reaches within the download's own command"
        )

    def test_a_saved_path_does_not_match_a_longer_path_that_starts_with_it(self):
        """``/tmp/a`` is not ``/tmp/archive-v1.tar.gz``."""
        text = (
            f"RUN wget -O /tmp/a {self._MOVING}\n"
            "RUN tar -xzf /tmp/archive-v1.tar.gz\n"
        )
        assert _offenders(text) == [], (
            "substring containment made every path with the saved path as a prefix "
            "look like the moving download; the archive reference must match whole"
        )

    def test_a_saved_basename_does_not_match_a_longer_basename(self):
        """``a`` is not ``a.tar.old``."""
        text = (
            f"RUN wget -O /tmp/a {self._MOVING}\n" "RUN cd /tmp && tar -xzf a.tar.old\n"
        )
        assert (
            _offenders(text) == []
        ), "the basename branch had the same containment defect as the path branch"

    def test_each_saved_path_belongs_to_its_own_download(self):
        """One RUN, two downloads: only the moving one contributes a saved path."""
        text = (
            f"RUN wget -O /tmp/moving {self._MOVING} && "
            f"wget -O /tmp/pinned {self._PINNED} && tar -xzf /tmp/pinned\n"
        )
        assert _offenders(text) == [], (
            "/tmp/pinned came from a version-pinned URL, so forcing its format is "
            "correct; collecting every destination in a command that happens to "
            "contain a moving URL condemns it"
        )

    def test_a_moving_download_in_the_same_run_still_records_its_path(self):
        """The pairing must not lose the moving download's own destination."""
        text = (
            f"RUN wget -O /tmp/moving {self._MOVING} && "
            f"wget -O /tmp/pinned {self._PINNED} && tar -xzf /tmp/moving\n"
        )
        assert _offenders(text), (
            "the moving download's destination must still be indicted when a "
            "pinned download shares the RUN with it"
        )

    def test_a_variable_outside_the_archive_reference_is_not_unresolvable(self):
        """``-C "$DEST"`` says nothing about which archive tar reads."""
        text = (
            f"RUN wget -O /tmp/ff.tar.xz {self._MOVING} && "
            "tar -xf /tmp/ff.tar.xz -C /opt && "
            'tar -xzf pinned-v1.tar.gz -C "$DEST"\n'
        )
        assert _offenders(text) == [], (
            "the second tar reads a pinned literal archive; a variable extraction "
            "directory must not make it unresolvable"
        )

    def test_a_variable_archive_reference_is_still_unresolvable(self):
        """Narrowing the check must keep the case it exists for."""
        text = f"RUN wget -O /tmp/ff.tar.xz {self._MOVING} && " 'tar -xjf "$FF"\n'
        assert _offenders(text), (
            'tar -xjf "$FF" names its archive through a variable; branch 2 must '
            "still indict it"
        )


class TestTheScannerReadsTheCommandAsTheShellDoes:
    """Review round 3 on 2026-09-20 (Codex, seven P2 findings): the parser still
    read the command as whitespace-delimited tokens where the shell and the tools
    read it otherwise — a long option's separate argument, an output option inside
    a short-option cluster, an attached redirection, a quoted path with a space, and
    a ``tar`` that is only an argument to another command.
    """

    _MOVING = '"https://download.mozilla.org/?product=firefox-esr-latest-ssl"'
    _PINNED = '"https://example.invalid/tool-v1.2.3.tar.gz"'

    def test_a_long_option_argument_is_not_read_as_an_option(self):
        """``--exclude --gzip`` excludes a pattern named ``--gzip``; nothing is forced."""
        assert _forced_decompressors("tar --exclude --gzip -xf /tmp/moving") == [], (
            "GNU tar declares --exclude=PATTERN, so the next token is its argument; "
            "scanning that argument as an option reports a correct template as broken"
        )
        # Once the argument is consumed, a forcing option AFTER it is still read.
        assert _forced_decompressors("tar --exclude pattern --gzip -xf /tmp/f") == [
            "--gzip"
        ], "consuming the argument must stop at one token"
        # An option whose argument is OPTIONAL takes a value only when attached, so
        # the token after it is another option, exactly as getopt_long reads it.
        assert _forced_decompressors("tar --occurrence --gzip -xf /tmp/f") == [
            "--gzip"
        ], "--occurrence[=N] must not swallow the --gzip that follows it"
        # The archive is still found after a consumed long argument.
        assert _parse_tar_span(["--directory", "/opt", "-xzf", "/tmp/moving"]) == (
            ["-xzf"],
            "/tmp/moving",
        )

    @pytest.mark.parametrize(
        "download",
        [
            "curl -sLo/tmp/ff.tar.xz",
            "curl -sLo /tmp/ff.tar.xz",
            "wget -qO/tmp/ff.tar.xz",
            "wget -qO /tmp/ff.tar.xz",
        ],
    )
    def test_an_output_option_inside_a_short_cluster_records_the_saved_path(
        self, download
    ):
        """``-sLo`` is ``-s -L -o``; the value-less flags in front hide nothing."""
        text = (
            f"RUN {download} {self._MOVING}\n" "RUN tar -xzf /tmp/ff.tar.xz -C /opt\n"
        )
        assert _offenders(text), (
            f"{download!r} saves the moving download to /tmp/ff.tar.xz; curl and "
            f"wget both let value-less short options precede the output option in "
            f"one cluster, and losing the path leaves the later forced tar undetected"
        )

    def test_a_short_option_argument_is_not_read_as_an_output_option(self):
        """``-do=1`` posts the data ``o=1``; ``-d`` consumes the rest of its cluster."""
        recorded = [
            path
            for paths, _ in _download_invocations(f"curl -do=1 {self._MOVING}")
            for path in paths
        ]
        assert recorded == [], (
            f"curl's -d takes an argument, so the 'o' after it is data, not the "
            f"output option; got {recorded!r}"
        )
        # The stdout sink is still recognised when it is glued inside the cluster.
        text = f"RUN wget -qO- {self._MOVING} | tar -xzf -\n"
        assert _offenders(text), (
            "wget -qO- pipes to stdout; the guard must not record '-' as a saved "
            "path and must indict the forcing tar via branch 2"
        )

    @pytest.mark.parametrize(
        "extraction",
        [
            "tar -xzf /tmp/moving>/dev/null",
            "tar -xzf /tmp/moving>/dev/null 2>&1",
            "tar -xzf /tmp/moving 2>/dev/null",
            "tar -xzf >/dev/null /tmp/moving",
            "tar 2>&1 -xzf /tmp/moving",
            "tar -xzf /tmp/moving &>/dev/null",
        ],
    )
    def test_an_attached_redirection_does_not_hide_the_archive(self, extraction):
        """A redirection belongs to the shell; tar's argv never contains it."""
        text = f"RUN wget -O /tmp/moving {self._MOVING}\n" f"RUN {extraction}\n"
        assert _offenders(text), (
            f"{extraction!r} hands tar the archive /tmp/moving and applies the "
            f"redirection itself, so the saved moving path must still match; left "
            f"joined, the archive reads as '/tmp/moving>/dev/null' and the forced "
            f"decompressor escapes"
        )

    def test_a_quoted_path_with_a_space_is_one_argument(self):
        """The shell passes ``"/tmp/my moving.tar"`` as ONE word."""
        text = (
            f'RUN wget -O "/tmp/my moving.tar" {self._MOVING}\n'
            'RUN tar -xzf "/tmp/my pinned-v1.tar.gz"\n'
        )
        assert _offenders(text) == [], (
            f"both paths truncated to /tmp/my by str.split, so a pinned archive was "
            f"reported as the moving file; got {_offenders(text)!r}"
        )
        for spelling in (
            'tar -xzf "/tmp/my moving.tar"',
            "tar -xzf '/tmp/my moving.tar'",
            "tar -xzf /tmp/my\\ moving.tar",
        ):
            hit = (
                f'RUN wget -O "/tmp/my moving.tar" {self._MOVING}\n' f"RUN {spelling}\n"
            )
            assert _offenders(hit), (
                f"{spelling!r} extracts the saved moving file; every quoting of the "
                f"same word must still match"
            )

    def test_a_quoted_operator_is_not_a_separator(self):
        """A ``;`` inside quotes is data, not a command boundary."""
        text = (
            f"RUN wget -O /tmp/moving {self._MOVING} && "
            "echo 'note: a;tar -xzf /tmp/moving is wrong' > /opt/README\n"
        )
        assert _offenders(text) == [], (
            f"the quoted text is one argument to echo; splitting on the ';' inside "
            f"it invented a tar invocation; got {_offenders(text)!r}"
        )

    def test_an_unterminated_quote_fails_closed(self):
        """A command the guard cannot parse is reported, never silently passed."""
        text = f"RUN wget -O /tmp/moving {self._MOVING}\n" 'RUN tar -xf "/tmp/moving\n'
        found = _offenders(text)
        assert found and "unparseable" in found[0], (
            f"an unterminated quote leaves the guard unable to say which file tar "
            f"reads; silence here is the failure mode this file exists to prevent; "
            f"got {found!r}"
        )
