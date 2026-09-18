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

No pre-merge job builds service images (#473), so nothing caught this. These templates
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
