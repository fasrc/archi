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


def _forced_decompressors(command: str) -> list[str]:
    tokens = command.split()
    result = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "tar":
            i += 1
            while i < len(tokens) and tokens[i] not in _SHELL_SEP:
                token = tokens[i]
                if token.startswith("--"):
                    if token.split("=")[0] in _FORCING_LONG:
                        result.append(token)
                elif token.startswith("-"):
                    if any(ch in _FORCING_SHORT for ch in token[1:]):
                        result.append(token)
                i += 1
        else:
            i += 1
    return result


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


def _wget_curl_paths(command: str) -> set:
    """Extract wget -O / curl -o saved paths from one command; exclude stdout sinks."""
    result = set()
    tokens = command.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _SHELL_SEP:
            i += 1
            continue
        if tok == "wget":
            i += 1
            while i < len(tokens) and tokens[i] not in _SHELL_SEP:
                if tokens[i] == "-O":
                    if i + 1 < len(tokens) and tokens[i + 1] not in _SHELL_SEP:
                        path = tokens[i + 1].strip("\"'")
                        if path not in _STDOUT_SINKS:
                            result.add(path)
                    i += 2
                elif tokens[i].startswith("-O") and len(tokens[i]) > 2:
                    path = tokens[i][2:].strip("\"'")
                    if path not in _STDOUT_SINKS:
                        result.add(path)
                    i += 1
                else:
                    i += 1
        elif tok == "curl":
            i += 1
            while i < len(tokens) and tokens[i] not in _SHELL_SEP:
                if tokens[i] in ("-o", "--output"):
                    if i + 1 < len(tokens) and tokens[i + 1] not in _SHELL_SEP:
                        path = tokens[i + 1].strip("\"'")
                        if path not in _STDOUT_SINKS:
                            result.add(path)
                    i += 2
                else:
                    i += 1
        else:
            i += 1
    return result


def _saved_paths(text: str) -> set:
    """Collect wget/curl saved paths from every moving-download command in text."""
    result = set()
    for command in _commands(text):
        if _MOVING_DOWNLOAD.search(command):
            result |= _wget_curl_paths(command)
    return result


def _span_contains_path(span: list, saved: set) -> bool:
    """True when any span token matches a saved path (full path or bare basename)."""
    for path in saved:
        basename = path.rsplit("/", 1)[-1] if "/" in path else path
        for tok in span:
            clean = tok.strip("\"'")
            if path in clean:
                return True
            if "/" not in clean and basename and basename in clean:
                return True
    return False


def _span_is_unresolvable(span: list) -> bool:
    """True when the archive reference is a shell variable or a stdout sink."""
    for tok in span:
        if "$" in tok:
            return True
        if tok.strip("\"'") in _STDOUT_SINKS:
            return True
    return False


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
        tokens = command.split()
        i = 0
        while i < len(tokens):
            if tokens[i] == "tar":
                i += 1
                span = []
                while i < len(tokens) and tokens[i] not in _SHELL_SEP:
                    span.append(tokens[i])
                    i += 1
                forcing = _forced_decompressors("tar " + " ".join(span))
                if not forcing:
                    continue
                if saved and _span_contains_path(span, saved):
                    result.extend(forcing)
                    continue
                if _MOVING_DOWNLOAD.search(command):
                    cmd_saved = _wget_curl_paths(command)
                    if not cmd_saved or _span_is_unresolvable(span):
                        result.extend(forcing)
            else:
                i += 1
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
