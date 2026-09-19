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
_SHELL_SEP = frozenset({"&&", "||", ";", "|"})
_STDOUT_SINKS = frozenset({"-", "/dev/stdout", "/dev/null"})

# A shell control operator, whether or not whitespace surrounds it. ``||`` is listed
# before ``|`` so the two-character operator wins.
_SHELL_OPERATOR = re.compile(r"(\|\||&&|;|\|)")

# tar's short options that consume an argument: the rest of their own cluster when one
# is attached, otherwise the next token. ``-f`` names the archive; ``-I`` names a
# compression program and is a forcing option as well. Review on 2026-09-19: without
# this set, ``tar -xf/tmp/firefox.tar.xz`` read the ``z`` in the FILENAME as a forcing
# option, so a correct auto-detecting extraction was reported as forcing xz.
_SHORT_WITH_ARGUMENT = frozenset("bCfFgHIKLNTVX")

# The long options that name the archive. ``--file=PATH`` and ``--file PATH`` both.
_ARCHIVE_LONG = frozenset({"--file"})

# The tools whose saved-output option tells the guard where a download landed, with the
# options that name that destination. Both accept the value attached to the short form:
# ``wget -O/tmp/x`` and ``curl -o/tmp/x`` are valid, and curl's own manual says a short
# option may be used "with or without a space between it and its value".
_DOWNLOAD_OUTPUT_OPTIONS = {
    "wget": ("-O", "--output-document"),
    "curl": ("-o", "--output"),
}


def _shell_tokens(command: str) -> list[str]:
    """``command`` split into tokens, with shell control operators isolated.

    ``str.split`` sees ``&&``, ``||``, ``;`` and ``|`` only when whitespace surrounds
    them, but bash does not require it. Review on 2026-09-19: ``wget …&&tar …`` hid
    both the URL and the tar token, so a forced extraction of a moving download read
    as clean, and ``tar -xf a;tar -xzf b`` read as ONE invocation, so the second tar's
    forcing option was attributed to the first tar's archive.
    """
    return _SHELL_OPERATOR.sub(r" \1 ", command).split()


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
    """Every tar invocation in ``command``, bounded by shell control operators."""
    invocations = []
    tokens = _shell_tokens(command)
    i = 0
    while i < len(tokens):
        if _basename(tokens[i]) != "tar":
            i += 1
            continue
        i += 1
        span = []
        while i < len(tokens) and tokens[i] not in _SHELL_SEP:
            span.append(tokens[i])
            i += 1
        invocations.append(_parse_tar_span(span))
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

    Pairing each destination with the invocation that wrote it is what keeps a pinned
    download out of the moving set. Review on 2026-09-19: every destination in a
    command containing any moving URL was recorded as moving, so
    ``wget -O /tmp/moving <latest> && wget -O /tmp/pinned <versioned>`` made a correct
    ``tar -xzf /tmp/pinned`` an offender.
    """
    invocations = []
    tokens = _shell_tokens(command)
    i = 0
    while i < len(tokens):
        name = _basename(tokens[i])
        options = _DOWNLOAD_OUTPUT_OPTIONS.get(name)
        if options is None:
            i += 1
            continue
        short, long_form = options
        paths = set()
        span = []
        i += 1
        while i < len(tokens) and tokens[i] not in _SHELL_SEP:
            token = tokens[i]
            span.append(token)
            if token in (short, long_form):
                if i + 1 < len(tokens) and tokens[i + 1] not in _SHELL_SEP:
                    i += 1
                    span.append(tokens[i])
                    paths.add(tokens[i].strip("\"'"))
            elif token.startswith(short) and len(token) > len(short):
                paths.add(token[len(short) :].strip("\"'"))
            i += 1
        invocations.append((paths - _STDOUT_SINKS, " ".join(span)))
    return invocations


def _moving_saved_paths(command: str) -> set:
    """Paths saved by the MOVING downloads of ``command``, and by no other download."""
    result = set()
    for paths, text in _download_invocations(command):
        if _MOVING_DOWNLOAD.search(text):
            result |= paths
    return result


def _saved_paths(text: str) -> set:
    """Collect every moving download's saved path across all of ``text``."""
    result = set()
    for command in _commands(text):
        result |= _moving_saved_paths(command)
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
    saved = _saved_paths(text)
    result = []
    for command in _commands(text):
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
