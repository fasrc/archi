"""Pins in the base-image requirement sets must be installable together.

``scripts/dev/build_docker_images.sh`` builds each base image's requirement set by
concatenating a header with ``requirements/requirements-base.txt``. Neither file
knows what the other pins, so two independently reasonable pins can produce a set
``pip`` cannot resolve — and the only build path that assembles the GPU set is the
release workflow, so the failure surfaces during a release rather than on a PR.

That is not hypothetical. It happened on 2026-09-15 (#472): PR #453 added
``opentelemetry-sdk==1.44.0`` to the shared base while
``gpu-requirementsHEADER.txt`` pinned ``vllm==0.8.5``, whose metadata requires
``opentelemetry-sdk<1.27.0``. The first ``v2026.08.0`` release dispatch died with
``ResolutionImpossible`` in the build step.

These guards encode the pairwise constraints that a reader of either file alone
cannot see. They are deliberately offline and data-driven: each constraint carries
the date it was measured from PyPI metadata, because a guard asserting a
dependency fact without saying where the fact came from is unmaintainable. Re-measure
before changing a table here.

Known boundary, and it is a large one: **these guards do not prove the set resolves.**
Each encodes one pairwise constraint somebody already knew to look for, so the module
is green exactly when the known traps are absent — not when ``pip`` succeeds. Fixing
#472 demonstrated the gap: the vllm guard below went green while the real GPU set
still failed ``ResolutionImpossible``, because ``torch==2.7.0`` also needs
``sympy>=1.13.3`` and the shared base pinned ``1.13.1``. Almost every constraint here
was added *after* a resolver found it; ``VLLM_TORCH_SPEC`` is the one exception, added
on 2026-09-16 from a review reading of the GPU header, which had asserted the vllm-torch
coupling in prose for a day with no table behind it.

So this module is a regression net, not a gate. The gate is resolving both generated
files, which catches transitive conflicts nobody predicted and costs seconds:

    pip install --dry-run -r src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt
    pip install --dry-run -r src/cli/templates/dockerfiles/base-python-image/requirements.txt

Neither the net nor the resolve proves the packages *import*, and that is not a
theoretical caveat either. Building the GPU image on 2026-09-15 — the first successful
build of it since PR #453 — produced an image that resolved cleanly and then died on
``import vllm``:

    ValueError: 'aimv2' is already used by a Transformers config, pick another name.

``transformers`` was unpinned, vllm 0.9.0 declares no ceiling on it, and every
transformers release from 4.54.0 on collides with vllm's own ``aimv2`` shim. **No
resolver can reach that conclusion**, because every colliding version satisfies the
declared range. ``VLLM_TRANSFORMERS_SPEC`` below is therefore the one table here NOT
measured from ``requires_dist`` — it was measured by importing vllm in a built image.

So the tiers are: this module catches known pairwise traps, a resolve catches
unpredicted version conflicts, and only a real image build catches import-time
collisions. No pre-merge job covers those last two. The release dispatch is
the first thing that builds the GPU image.
"""

import re
from pathlib import Path

import pytest
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parents[2]

CPU_HEADER = REPO_ROOT / "requirements" / "cpu-requirementsHEADER.txt"
GPU_HEADER = REPO_ROOT / "requirements" / "gpu-requirementsHEADER.txt"
BASE_REQUIREMENTS = REPO_ROOT / "requirements" / "requirements-base.txt"

# The two sets ``pip`` actually installs. ``build_docker_images.sh`` writes each one by
# concatenating a header with the shared base, so these are the only files where a pin
# duplicated ACROSS the header/base boundary is visible at all.
_DOCKERFILE_TEMPLATES = REPO_ROOT / "src" / "cli" / "templates" / "dockerfiles"
GENERATED_CPU_REQUIREMENTS = (
    _DOCKERFILE_TEMPLATES / "base-python-image" / "requirements.txt"
)
GENERATED_GPU_REQUIREMENTS = (
    _DOCKERFILE_TEMPLATES / "base-pytorch-image" / "requirements.txt"
)
PYTORCH_BASE_DOCKERFILE = (
    REPO_ROOT
    / "src"
    / "cli"
    / "templates"
    / "dockerfiles"
    / "base-pytorch-image"
    / "Dockerfile"
)

# ``FROM docker.io/pytorch/pytorch:2.7.0-cuda12.6-cudnn9-devel`` -> ("2.7.0", "12.6")
_PYTORCH_FROM_PATTERN = re.compile(
    r"^FROM\s+\S*pytorch/pytorch:(\d+(?:\.\d+)*)-cuda(\d+(?:\.\d+)*)", re.MULTILINE
)

# Every table below maps a measured version STRING to the specifier that version
# actually DECLARES, verbatim as PyPI reports it — so a row can be diffed against
# ``requires_dist`` by eye, and ``packaging`` rather than this module decides what it
# means. ``None`` means the version declares no dependency on that package at all, which
# is different from the version being unmeasured — see ``_measured``.
#
# The keys are full version strings, post identifier included, because a post release is
# a separate distribution with its own metadata. Review on 2026-09-16 found the keys were
# numeric release tuples, which merged ``0.0.29``, ``0.0.29.post1``, ``0.0.29.post2`` and
# ``0.0.29.post3`` into one row holding whichever constraint was measured last — and the
# constraint it held, ``torch==2.6.0``, was wrong for two of the four.
#
# The uniform shape is deliberate. The first version of this module used one-sided
# comparisons — "vllm at least 0.9.0", "sympy at least the floor" — and review on
# 2026-09-15 found three separate holes in that shortcut, because dependency
# constraints are neither monotonic across releases nor always one-sided:
#
#   * vllm 0.19.0 RAISED its floor to 1.27.0, so "newer vllm is always fine" is false.
#   * vllm 0.9.0 still has a floor of 1.26.0, so an SDK DOWNGRADE breaks it.
#   * torch 2.6.0 pins sympy EXACTLY, so a higher sympy is as wrong as a lower one.
#
# Unknown releases fail rather than pass. A guard that silently accepts an unmeasured
# version is worse than no guard: it reports confidence it does not have.

# vllm -> its ``opentelemetry-sdk`` specifier. Measured from PyPI ``requires_dist``
# on 2026-09-15.
#
# The trap behind #472 is that the only range vllm 0.8.5 accepts is itself
# uninstallable: that suite's ``opentelemetry-instrumentation`` imports
# ``pkg_resources`` at module scope, which setuptools 82 removed (see
# ``requirements-base.txt``). So no SDK pin satisfies both vllm 0.8.5 and a working
# exporter, and the fix had to be the vllm bump.
# Re-measured on 2026-09-16 when the keys moved to full version strings. The 0.9.x line
# is why the keys matter here too: 0.9.0 and 0.9.1 declare a floor, 0.9.2 declares no
# opentelemetry-sdk dependency at all, and a release-tuple key could not tell 0.9.0 from
# 0.9.0.1.
VLLM_OTEL_SPEC = {
    "0.8.5": ">=1.26.0,<1.27.0",
    "0.8.5.post1": ">=1.26.0,<1.27.0",
    "0.9.0": ">=1.26.0",
    "0.9.0.1": ">=1.26.0",
    "0.9.1": ">=1.26.0",
    "0.9.2": None,
    "0.10.0": None,
    "0.11.0": None,
    "0.12.0": None,
    "0.15.0": None,
    "0.19.0": ">=1.27.0",
    "0.22.0": ">=1.27.0",
    "0.25.0": ">=1.27.0",
    "0.27.0": ">=1.27.0",
    "0.29.0": ">=1.27.0",
}

# ``xformers`` -> its ``torch`` specifier. One exact torch per distribution, so a torch
# bump that leaves xformers behind conflicts rather than falling back.
#
# Re-measured from PyPI ``requires_dist`` on 2026-09-16, which CORRECTED this table. It
# previously read ``(0, 0, 29) -> torch==2.6.0``; that is the 0.0.29.post2 and post3
# constraint, while 0.0.29 and 0.0.29.post1 require ``torch==2.5.1``. xformers rebuilds a
# release against a newer torch under a post tag, so the post identifier is exactly the
# part that carries the constraint — 0.0.33 wants torch 2.9.0 and 0.0.33.post2 wants
# 2.9.1. Note 0.0.32 has no bare release on PyPI; post1 is the first.
XFORMERS_TORCH_SPEC = {
    "0.0.29": "==2.5.1",
    "0.0.29.post1": "==2.5.1",
    "0.0.29.post2": "==2.6.0",
    "0.0.29.post3": "==2.6.0",
    "0.0.30": "==2.7.0",
    "0.0.31": "==2.7.1",
    "0.0.31.post1": "==2.7.1",
    "0.0.32.post1": "==2.8.0",
    "0.0.32.post2": "==2.8.0",
    "0.0.33": "==2.9.0",
    "0.0.33.post1": "==2.9.0",
    "0.0.33.post2": "==2.9.1",
}

# vllm -> its ``torch`` specifier. Always exact, and every release so far moved it, so
# vllm and torch can never be bumped apart. The GPU header already said so in prose;
# review on 2026-09-16 pointed out that no table enforced it, which left the header
# asserting a coupling the guards ignored. Measured from PyPI ``requires_dist`` on
# 2026-09-16.
#
# Note how little of this a version comparison could express: the pin moves on nearly
# every release (0.9.x -> 2.7.0, 0.10.0 -> 2.7.1, 0.11.0 -> 2.8.0) and then sits still
# across two (0.22.0 and 0.25.0 both want 2.11.0; 0.27.0 and 0.29.0 both want 2.13.0).
VLLM_TORCH_SPEC = {
    "0.8.5": "==2.6.0",
    "0.8.5.post1": "==2.6.0",
    "0.9.0": "==2.7.0",
    "0.9.0.1": "==2.7.0",
    "0.9.1": "==2.7.0",
    "0.9.2": "==2.7.0",
    "0.10.0": "==2.7.1",
    "0.11.0": "==2.8.0",
    "0.12.0": "==2.9.0",
    "0.15.0": "==2.9.1",
    "0.19.0": "==2.10.0",
    "0.22.0": "==2.11.0",
    "0.25.0": "==2.11.0",
    "0.27.0": "==2.13.0",
    "0.29.0": "==2.13.0",
}

# vllm -> the ``transformers`` range it actually WORKS with, which is narrower than the
# range it declares. This table is the one exception to "measured from requires_dist":
# vllm 0.9.0 declares only ``transformers>=4.51.1``, with no ceiling, but transformers
# 4.54.0 added a native ``aimv2`` config and vllm 0.9.0 registers its own shim under
# that name, so ``import vllm`` raises at module scope:
#
#   ValueError: 'aimv2' is already used by a Transformers config, pick another name.
#
# Measured by importing vllm inside the built GPU image on 2026-09-15: 4.52.4 and
# 4.53.3 import, 4.54.1 / 4.55.4 / 4.56.2 all carry native aimv2. **No resolver can
# find this** — every one of those versions satisfies the declared range. It took a
# real image build, which is why an unpinned transformers is a latent break rather
# than a convenience.
VLLM_TRANSFORMERS_SPEC = {
    "0.9.0": ">=4.51.1,<4.54.0",
}

# ``torch`` -> its ``sympy`` specifier. This coupling crosses the header/base boundary
# (torch is pinned in the headers, sympy in the shared base), so neither file shows it
# alone — which is how the first pass at #472 missed it. Re-measured 2026-09-16; torch
# 2.6.0's declaration carries a ``python_version >= "3.9"`` marker, which every image
# here satisfies, so the clause is recorded unconditionally.
TORCH_SYMPY_SPEC = {
    "2.6.0": "==1.13.1",
    "2.7.0": ">=1.13.3",
    "2.7.1": ">=1.13.3",
    "2.8.0": ">=1.13.3",
}


_MISSING = object()


def _normalize_version(version: str) -> str:
    """Casefold and strip a version string, so it can be a table key or compared."""
    return version.strip().lower()


def _measured(table: dict, version: str):
    """The specifier ``table`` records for ``version``, or ``_MISSING``.

    ``_MISSING`` means nobody measured this version, and every caller turns that into
    a failure. It is deliberately distinct from a recorded ``None``, which means the
    version was measured and declares no dependency on that package at all. Collapsing
    the two would let an unmeasured release read as "declares nothing" and pass.
    """
    return table.get(_normalize_version(version), _MISSING)


def _satisfies(candidate: str, specifier: str) -> bool:
    """Does version ``candidate`` satisfy the declared ``specifier``?

    ``packaging`` decides, because PEP 440 is not what a numeric comparison does.
    Zero-padding makes ``1.27`` equal ``1.27.0``; an exclusive ordered bound excludes
    a pre-release of its own version, so ``<1.27.0`` rejects ``1.27.0rc1``; and ``==``
    rejects a post release of its version. A hand-rolled comparison got all three
    wrong in this module before 2026-09-16.

    ``prereleases=True`` because every candidate here comes from an explicit ``==``
    pin in a requirement file, which is the case where pip also considers a
    pre-release. An unparseable specifier raises ``InvalidSpecifier`` rather than
    silently passing, so a typo in a measured table cannot weaken a guard.
    """
    return SpecifierSet(specifier).contains(Version(candidate), prereleases=True)


# ``markitdown[pdf,pptx]==0.1.5`` -> name "markitdown". An extras marker belongs to the
# requirement, not to the project name, so both patterns step over it. The trailing ``$``
# on the pin pattern is deliberate: it makes a COMPOUND specifier such as
# ``vllm==0.9.0,<0.10`` fail to read as an exact pin rather than reading as an exact pin
# of 0.9.0 that the second clause silently contradicts.
_NAME = r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?"
_PIN_PATTERN = re.compile(rf"^{_NAME}==([^\s;#,]+)$")
_REQUIREMENT_PATTERN = re.compile(rf"^{_NAME}\s*(.*)$")

# Packages whose pin a guard in this module reads. Each guard SKIPS when its subject is
# absent from the file, which is correct for a genuinely absent package — the CPU header
# carries no vllm. For these names, therefore, "absent" must mean absent and never
# "present, but not as an exact pin"; ``_unpinned_protected`` enforces the difference.
PROTECTED_PACKAGES = frozenset(
    {
        "opentelemetry-sdk",
        "sympy",
        "torch",
        "transformers",
        "vllm",
        "xformers",
    }
)


def _normalize_name(name: str) -> str:
    """PEP 503 name normalization, so ``opentelemetry_sdk`` and ``Torch`` compare."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _joined_lines(text: str):
    """Yield logical lines, joining each physical line that ends in a backslash.

    pip's own rule (``join_lines`` in ``pip._internal.req.req_file``): a trailing ``\\``
    continues the requirement onto the next physical line. Round 5 on 2026-09-20 found
    this module reading the two halves separately, which broke the hash-pinned spelling
    pip documents — ``numpy==2.0.0 \\`` then ``--hash=sha256:...`` — in BOTH directions
    at once. ``_PIN_PATTERN`` is anchored, so ``numpy==2.0.0 \\`` recorded no pin and the
    package read as absent; and the separator branch of ``_PATH_OR_ARCHIVE_REQUIREMENT``
    read the same backslash as a path separator and reported the line. Joining first is
    the only reading that records the pin AND declines the false report.

    pip's comment rule (``COMMENT_RE``, same as ``_COMMENT``): a whole-line comment never
    opens a continuation buffer, even when it ends in ``\\``. Round 6 on 2026-09-22 found
    the module opening a buffer for ``# comment \\`` and joining the archive requirement
    after it onto the comment, hiding the archive from every reader.
    """
    buffered = ""
    for raw_line in text.splitlines():
        if raw_line.endswith("\\") and not _COMMENT.match(raw_line):
            # pip rule: a whole-line comment (COMMENT_RE) never opens a buffer.
            # Review finding 1, 2026-09-22.
            buffered += raw_line[:-1]
            continue
        yield buffered + raw_line
        buffered = ""
    if buffered:
        yield buffered


def _requirement_lines(text: str):
    """Yield the requirement lines of ``text``, without comments or option lines.

    Blanks, ``#`` comments and option lines such as ``--extra-index-url`` carry no
    requirement. A trailing comment — by pip's rule, a ``#`` after whitespace — and an
    environment marker are cut away, so the caller sees the requirement and nothing
    else. A ``#`` with no whitespace before it is part of the requirement, as it is to
    pip: the fragment of ``git+https://host/repo.git#egg=vllm`` or the literal hash in
    ``foo#vllm.tar.gz``.

    Physical lines are joined first, then the per-requirement options a joined line
    carries (``--hash``, ``--config-settings``) are cut away: they qualify the
    requirement rather than name it, and leaving them attached defeats the anchored
    ``_PIN_PATTERN``. A whole line that IS an option still starts with ``-`` after the
    join and is skipped as before.
    """
    for joined_line in _joined_lines(text):
        line = joined_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        line = _COMMENT.sub("", line).split(";", 1)[0].strip()
        line = _PER_REQUIREMENT_OPTION.sub("", line).strip()
        if line:
            yield line


def _parse_pins(text: str) -> dict:
    """Map normalized project name to pinned version for every ``name==version``.

    Only exact pins are read: this module asserts against exact pins, and a range
    would make every constraint below ambiguous. ``_unpinned_protected`` is what keeps
    that ambiguity from turning into a silent skip.
    """
    pins = {}
    for line in _requirement_lines(text):
        match = _PIN_PATTERN.match(line)
        if match:
            pins[_normalize_name(match.group(1))] = match.group(2)
    return pins


# A requirement given as a bare VCS or URL reference rather than a project name:
# ``git+https://host/repo.git#egg=vllm``, ``hg+``, ``https://host/pkg.whl``. Review on
# 2026-09-16: ``_requirement_lines`` strips the ``#egg=vllm`` fragment as a comment and
# the name matcher then recorded the project as ``git``, so every vllm guard read vllm
# as ABSENT and skipped while pip installed an arbitrary checkout. These lines do not
# start with ``-``, so the directive matcher above never saw them either.
#
# ``file:`` with no slashes is a URL to pip too. Round 3 on 2026-09-20:
# ``install_req_from_line("file:foo")`` returns ``name=None, link=file:///foo`` on pip
# 26.1.2, so the scheme alone decides; the guard had demanded ``file://`` and read
# ``file:foo`` as a project named ``file``.
_VCS_OR_URL_REQUIREMENT = re.compile(
    r"^(?:(?:git|hg|bzr|svn)\+|https?://|file:|[A-Za-z]:[\\/])", re.IGNORECASE
)

# pip's own comment rule (``COMMENT_RE`` in ``pip._internal.req.req_file``): a ``#``
# begins a comment only at the start of the line or after whitespace. Round 3 on
# 2026-09-20: ``foo#vllm.tar.gz`` is an archive path to pip — it keeps the ``#`` and
# builds a ``file:///…/foo%23vllm.tar.gz`` link — but ``_requirement_lines`` cut the
# line at the first ``#`` and the suffix matcher saw only ``foo``.
_COMMENT = re.compile(r"(^|\s+)#.*$")

# The per-requirement options pip allows after a requirement on the same logical line:
# ``numpy==2.0.0 --hash=sha256:...``. They qualify the requirement, never name it, so
# they are cut away before matching. Round 5 on 2026-09-20: ``_PIN_PATTERN`` is anchored
# at ``$``, so a hash left attached made an exactly-pinned protected package read as
# absent and every pairwise guard skipped it. Round 7 on 2026-09-22: ``-C`` is the only
# short form pip's ``SUPPORTED_OPTIONS_REQ`` carries (``--hash`` and
# ``-C``/``--config-settings``, measured on pip 26.1.2); the pattern cut only ``--``
# options, so ``-Cfoo=bar`` and ``-C foo=bar`` survived and the anchored
# ``_PIN_PATTERN`` recorded no pin.
#
# A config setting is cut only when its value is ``KEY=VAL``, because that is the only
# shape pip accepts: ``_handle_config_settings`` raises ``Arguments to -C must be of
# the form KEY=VAL`` for ``-Cfoo``, ``-C foo``, ``--config-settings=foo`` and
# ``--config-settings=`` alike (measured on pip 26.1.2). Review finding 2 of
# 2026-09-22 (Codex): cutting ``-C`` plus one following character recorded an exact
# pin from a line pip refuses, so the guard passed where the parent revision failed
# closed and the image build was the first reader to object. The ``--`` branch keeps
# the old breadth for every other long option (``--hash``) but hands
# ``--config-settings`` to the KEY=VAL branch, which closes the same hole the long
# spelling already had. A bare trailing ``-C`` carries no value and is likewise never
# erased.
#
# The value is read the way pip reads it, not as raw text. ``get_line_parser`` runs
# ``shlex.split`` over the option string before ``_handle_config_settings`` partitions
# on ``=``, so quoted or backslash-escaped whitespace inside the KEY is legal:
# ``-C "foo bar=baz"`` records the key ``foo bar`` (measured on pip 26.1.2). Round 3 of
# 2026-09-22 (Codex, comment 4069783454): a raw ``[^\s=]*=`` key stopped at the space
# inside the quotes, left the option attached, and reported an exactly pinned package
# as unpinned — a false report on a line pip installs. A quoted value must therefore
# carry the ``=`` INSIDE the quotes to be cut; ``-C "foo bar"`` still fails closed,
# because pip rejects it.
_CONFIG_SETTING_VALUE = (
    r"(?:\"[^\"]*=[^\"]*\"|'[^']*=[^']*'|(?:[^\s='\"]|\\\s|\"[^\"=]*\"|'[^'=]*')*=)"
)
# The delimiter in front of the option is a literal space, not any whitespace.
# ``break_args_options`` (``pip._internal.req.req_file``) runs ``line.split(" ")`` and
# opens the option string at the first token that starts with ``-``, so a tab or a
# no-break space before ``-C`` leaves the option inside the requirement and
# ``install_req_from_line`` raises. Review round 4 of 2026-09-22 (Codex, comment
# 4069945613): ``\s+`` cut the option away from ``vllm==0.9.0\t-Cfoo=bar``, the
# anchored ``_PIN_PATTERN`` then recorded an exact pin, and every pairwise guard read
# vllm as healthy on a file the image build refuses. Whitespace BEFORE that space stays
# with the requirement, exactly as pip leaves it in ``args``, and ``_requirement_lines``
# strips it. A doubled space is a run of spaces to pip too: the empty token it yields
# names no option.
_PER_REQUIREMENT_OPTION = re.compile(
    rf"[ ]+(?:(?:--config-settings[=\s]|-C)\s*{_CONFIG_SETTING_VALUE}"
    rf"|--(?!config-settings\b)\S+).*$"
)

# A ``${NAME}`` placeholder pip substitutes from the build environment before it reads
# the line (``ENV_VAR_RE``: uppercase letters, digits and underscores only). Round 3 on
# 2026-09-20: with ``VLLM_PATH=./foo`` a whole-line ``${VLLM_PATH}`` installs a local
# tree, and this module — which cannot see that environment — matched nothing and
# recorded no project at all. Such a line is reported, whatever else it contains.
_ENVIRONMENT_SUBSTITUTION = re.compile(r"\$\{[A-Z0-9_]+\}")

# A requirement given as a bare local project path or a bare archive path rather than a
# project name: ``./local_vllm``, ``../pkgs/vllm``, ``/opt/vllm.whl``,
# ``vllm-0.9.0-py3-none-any.whl``, ``dist/vllm-0.9.0.tar.gz``. Added 2026-09-18 (#491):
# pip accepts both shapes, and neither was in ``_VCS_OR_URL_REQUIREMENT``, so a line like
# this read as a project named e.g. ``vllm-0-9-0-py3-none-any-whl`` and every guard
# naming the real package skipped it as absent. Three alternatives, one per clause: a
# leading ``.``, a token containing a path separator, a token ending in an archive
# suffix. The suffix alternation is anchored on the right so a dotted project name such
# as ``backports.tarfile==1.2.0`` does not match on its own ``.tar``-shaped substring.
# Exactly the suffixes in pip's own ``ARCHIVE_EXTENSIONS``
# (``pip._internal.utils.filetypes``, measured on pip 26.1.2). Review on 2026-09-19 found
# ``.tlz``, ``.tar.lz`` and ``.tar.lzma`` missing: pip builds a ``file://`` link for each
# of them from the extension alone, so ``vllm-0.9.0.tlz`` read as a project named
# ``vllm-0-9-0-tlz`` and every vllm guard skipped. Round 2 removed ``.tbz2``, which pip
# does NOT accept — ``is_archive_file("x.tbz2")`` is False and pip reads such a line as
# an ordinary project name, so matching it was a guess, not a free superset.
_ARCHIVE_SUFFIX = (
    r"\.(?:whl|zip|tgz|tbz|txz|tlz|tar\.gz|tar\.bz2|tar\.xz|tar\.lzma|tar\.lz|tar)"
)
# A PEP 508 comparison, so that ``example.zip ==1.0`` — a legal dotted project name
# whose tail looks like an archive — is not read as a filename because of the space
# before its operator. Review on 2026-09-19: the compact spelling ``example.zip==1.0``
# already passed, so whitespace alone decided the verdict. Round 2 added the optional
# ``(``: the grammar also allows the specifier in parentheses, ``example.zip (==1.0)``,
# which pip reads as the project ``example.zip``.
# packaging's tokenizer accepts a space and a tab between tokens and nothing else
# (``WS`` is ``[ \t]+`` in ``packaging._tokenizer``, measured on the copy pip 26.1.2
# vendors). Round 4 of 2026-09-22 (Codex, comment 4069945622): the exemptions below
# spelled their whitespace ``\s``, which also matches a no-break space, so
# ``example.zip [a\u00a0,b] ==1.0`` — a line pip refuses, and one 376b5867 reported —
# bought an exemption and reached the image build unreported.
_WSP = r"[ \t]"
_COMPARISON_AFTER_SUFFIX = rf"\(?{_WSP}*(?:===|==|!=|~=|<=|>=|<|>)"
# An extras list attached to an archive: ``vllm-0.9.0-py3-none-any.whl[foo]``. pip
# accepts it and resolves the wheel; round 2 found the guard reading the line as a
# project named ``vllm-0-9-0-py3-none-any-whl``, so vllm read as absent and every
# protected-vllm guard skipped — the silent-skip shape this change exists to close.
_ATTACHED_EXTRAS = r"(?:\[[^\]]*\])?"
# PEP 508's extras grammar, as packaging enforces it for pip:
# ``'[' wsp* [ identifier (wsp* ',' wsp* identifier)* wsp* ] ']'``, with an identifier
# starting on a letter or a digit. Measured on pip 26.1.2: ``[foo]``, ``[foo,bar]``,
# ``[ foo , bar ]``, ``[]`` and ``[1foo]`` parse; ``[foo bar]`` ("Expected comma
# between extra names"), ``[foo,]``, ``[,foo]`` and ``[-foo]`` raise.
#
# The LAST character is constrained as well as the first, because packaging's
# IDENTIFIER token is ``\b[a-zA-Z0-9][a-zA-Z0-9._-]*\b`` (``packaging._tokenizer``,
# measured on packaging as shipped with pip 26.1.2). The closing ``\b`` cannot sit
# between two non-word characters, so ``[foo.]`` and ``[foo-]`` truncate to ``foo``
# and raise ``Expected matching RIGHT_BRACKET``. Round 2 of 2026-09-22 (Codex
# adversarial pass on 56baa86d) found both exempted here. The final class is
# ``[A-Za-z0-9_]`` and NOT ``[A-Za-z0-9]``: ``_`` is a word character, so pip accepts
# ``[foo_]``, and an alphanumeric-only rule would report a line pip installs.
_EXTRA_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9_])?"
_VALID_EXTRAS_LIST = (
    rf"\[{_WSP}*(?:{_EXTRA_NAME}(?:{_WSP}*,{_WSP}*{_EXTRA_NAME})*{_WSP}*)?\]"
)
# ``example.zip [foo] ==1.0`` is the project ``example.zip[foo]`` with specifier
# ``==1.0`` to pip (PEP 508 ``wsp* extras?`` allows whitespace before ``[``).
# Review finding 2, 2026-09-22: ``_ATTACHED_EXTRAS`` required ``[`` to follow the
# suffix immediately; a space before ``[foo]`` made it match empty, and ``\s`` then
# matched that space — the comparison lookahead never fired. A second negative
# lookahead now also blocks ``\s`` when a detached extras list leads to a comparison.
# Review finding 3 of 2026-09-22 (Codex): that second lookahead first accepted any
# bracket content, so ``example.zip [foo bar] ==1.0`` — which pip refuses — lost the
# report the parent revision made. It reads ``_VALID_EXTRAS_LIST`` instead, so only a
# list pip itself accepts suppresses the report.
# The ``[^ \t\S]`` arm is whitespace OUTSIDE packaging's class — a no-break space and
# its Unicode kin. It carries no lookahead because no exemption can apply: pip's parse
# ends at that character whatever follows it, so the line is reported.
_PATH_OR_ARCHIVE_REQUIREMENT = re.compile(
    rf"^(?:\.|[^;]*[\\/]"
    rf"|[^;]*{_ARCHIVE_SUFFIX}{_ATTACHED_EXTRAS}"
    rf"(?:;|$|[^ \t\S]|{_WSP}(?!{_WSP}*{_COMPARISON_AFTER_SUFFIX})"
    rf"(?!{_WSP}*{_VALID_EXTRAS_LIST}{_WSP}*{_COMPARISON_AFTER_SUFFIX})))",
    re.IGNORECASE,
)

# A PEP 508 direct reference carrying a project name in front of the URL:
# ``numpy@https://host/numpy.whl``, ``vllm @ git+https://host/vllm``. The name reader
# reads ``numpy`` from both spellings, so design D4 leaves the class readable and
# ``_unpinned_protected`` fails a protected name closed. Review on 2026-09-19: the
# separator inside the URL made the compact spelling opaque while the spaced spelling
# passed, so whitespace alone decided the verdict. The scheme set is the one
# ``_VCS_OR_URL_REQUIREMENT`` uses; a target with NO scheme (``evil@../pkgs/vllm``) is a
# local path by another name and stays reported.
# PEP 508's grammar is ``name wsp* extras?``, so the brackets need not touch the name.
# Round 5 on 2026-09-20: ``numpy [foo] @ https://host/numpy.whl`` is the project
# ``numpy[foo]`` to pip, but ``_NAME`` demanded an attached ``[``, so this exemption
# missed and the separator branch reported a line whose project name is readable. The
# compact spelling already passed, so whitespace alone decided the verdict — the same
# asymmetry round 1 fixed for the ``@`` itself. Widened HERE only: ``_PIN_PATTERN`` and
# ``_REQUIREMENT_PATTERN`` keep the strict name so no pin is read across a space.
_NAME_WITH_SPACED_EXTRAS = r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?"
_NAMED_URL_REFERENCE = re.compile(
    rf"^{_NAME_WITH_SPACED_EXTRAS}\s*@\s*(?:(?:git|hg|bzr|svn)\+|https?://|file:)",
    re.IGNORECASE,
)


def _opaque_requirements(text: str) -> list:
    """Requirement lines whose project name this module cannot read.

    Reported rather than parsed. A VCS reference, a URL, a ``file://`` URL, a Windows
    drive letter, a bare local path, or a bare archive path (every suffix in pip's
    ``ARCHIVE_EXTENSIONS``: ``.whl``, ``.zip``, ``.tgz``, ``.tbz``, ``.txz``, ``.tlz``,
    ``.tar``, ``.tar.gz``, ``.tar.bz2``, ``.tar.xz``, ``.tar.lz``, ``.tar.lzma``, with or
    without an attached extras list) supplies a real package with no version this module can check, so each must fail the
    suite instead of resolving to a nonsense name like ``git`` or
    ``vllm-0-9-0-py3-none-any-whl``. A line is reported; no project name is ever resolved
    from a path or an archive filename.

    One shape is deliberately NOT reported: a PEP 508 direct reference carrying a project
    name, ``numpy@https://host/numpy.whl``. The name reader reads it, and
    ``_unpinned_protected`` fails a protected name closed — design D4. That exemption
    needs a URL scheme; ``evil@../pkgs/vllm`` is a local path wearing a name and is
    reported. Class measured 2026-09-18 (#491), widened after review 2026-09-19.

    A line carrying a ``${NAME}`` placeholder is reported whatever else it says: pip
    substitutes it from the build environment before parsing, and this module cannot
    see that environment, so the line may name any package at any version (D14).
    """
    return [
        line
        for line in _requirement_lines(text)
        if _ENVIRONMENT_SUBSTITUTION.search(line)
        or (
            not _NAMED_URL_REFERENCE.match(line)
            and (
                _VCS_OR_URL_REQUIREMENT.match(line)
                or _PATH_OR_ARCHIVE_REQUIREMENT.match(line)
            )
        )
    ]


def _parse_requirements(text: str) -> dict:
    """Map normalized project name to specifier text for EVERY requirement line.

    Operator-agnostic, unlike ``_parse_pins``, so a protected package written as a
    range stays visible instead of vanishing. That difference is the whole point.
    """
    found = {}
    for line in _requirement_lines(text):
        match = _REQUIREMENT_PATTERN.match(line)
        if match:
            found[_normalize_name(match.group(1))] = match.group(2).strip()
    return found


# ``-r other.txt`` / ``-c constraints.txt`` and their long forms. Deliberately NOT
# ``--extra-index-url`` or ``--index-url``, which carry no requirements and which the
# CPU header legitimately uses.
# Options that supply installable content without a version this module can read.
# ``-r``/``-c`` pull in another file; ``-e``/``--editable`` install a path or VCS
# checkout directly. Adversarial review on 2026-09-16 found only the first pair was
# matched, so ``-e git+...#egg=vllm`` was dropped by ``_requirement_lines`` (it starts
# with ``-``) and reported by nothing — every protected-dependency guard then read vllm
# as ABSENT and skipped, admitting an arbitrary unmeasured checkout to the GPU image.
#
# The rule is the class, not the two spellings: an option that can carry a requirement
# must fail the suite rather than read as silence.
# The short forms take a value with NO separator too: pip accepts ``-rother.txt`` and
# ``-cconstraints.txt`` exactly as it accepts ``-r other.txt``. Review on 2026-09-16
# found the pattern demanded whitespace, ``=`` or end-of-line, so the compact spelling
# matched nothing while ``_requirement_lines`` still dropped the line for starting with
# ``-`` — the included file's pins stayed invisible.
_REQUIREMENT_BEARING_PATTERN = re.compile(
    r"^(?:-[rce]|--requirement|--constraint|--editable)(?:[=\s]|$|\S)"
)


def _requirement_bearing_directives(text: str) -> list:
    """Option lines of ``text`` that install something this module cannot version.

    These are the lines ``_requirement_lines`` cannot honour: it skips everything
    starting with ``-``, so the package they supply is invisible to every guard while
    pip installs it regardless. Reporting them is what turns that silence into a
    failure.
    """
    directives = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if _REQUIREMENT_BEARING_PATTERN.match(line):
            directives.append(line)
    return directives


# Kept as the previous name so existing guards keep reading; the behaviour is now the
# wider class above.
_include_directives = _requirement_bearing_directives


def _pytorch_from_stages(content: str) -> list:
    """Every ``(torch, cuda)`` pair named by a pytorch ``FROM`` in ``content``.

    A list, not a first match. ``re.search`` returned only the earliest stage, so in a
    multi-stage Dockerfile a builder stage on the correct torch could mask a later
    stage on the wrong one while ``pip install -r requirements.txt`` ran against the
    mismatched base (adversarial review, 2026-09-16).
    """
    return [
        (match.group(1), match.group(2))
        for match in _PYTORCH_FROM_PATTERN.finditer(content)
    ]


def _declarations(text: str) -> dict:
    """Map normalized project name to EVERY specifier declared for it, in file order.

    ``_parse_pins`` and ``_parse_requirements`` both keep only the last occurrence of a
    name, which is what let a repeated declaration hide a contradiction. This keeps all
    of them, so a duplicate is a fact the guards can see.
    """
    found = {}
    for line in _requirement_lines(text):
        match = _REQUIREMENT_PATTERN.match(line)
        if match:
            found.setdefault(_normalize_name(match.group(1)), []).append(
                match.group(2).strip()
            )
    return found


def _conditional_protected(text: str) -> dict:
    """Protected packages declared with an environment marker, mapped to that marker.

    An empty mapping is the healthy answer. ``_requirement_lines`` cuts a line at
    ``;`` so the guards never see the marker, which means a line pip may skip on this
    image reads here as an unconditional pin.

    Physical lines are joined first (pip rule: ``join_lines``), so a marker on the
    continuation of a backslash-joined line is still detected.
    Review finding 3, 2026-09-22.

    The per-requirement options are then cut away, because pip reads the marker only
    from the requirement half. Its ``break_args_options`` splits the logical line at
    the first token beginning with ``-``, so a semicolon inside an option value —
    ``--config-settings=foo=bar;baz`` — is part of that value and never a marker.
    Review finding 1 of 2026-09-22 (Codex): partitioning the whole line on ``;``
    read that value as a marker and rejected a valid unconditional pin.
    """
    conditional = {}
    for raw_line in _joined_lines(text):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        line = line.split("#", 1)[0].strip()
        line = _PER_REQUIREMENT_OPTION.sub("", line).strip()
        requirement, separator, marker = line.partition(";")
        if not separator:
            continue
        match = _REQUIREMENT_PATTERN.match(requirement.strip())
        if match and _normalize_name(match.group(1)) in PROTECTED_PACKAGES:
            conditional[_normalize_name(match.group(1))] = marker.strip()
    return conditional


def _duplicated_protected(text: str) -> dict:
    """Protected packages that ``text`` declares more than once, with every specifier.

    An empty mapping is the healthy answer. Any entry means pip sees two constraints
    where the guards see one, so the set can be impossible while this module is green.
    """
    return {
        name: specifiers
        for name, specifiers in _declarations(text).items()
        if name in PROTECTED_PACKAGES and len(specifiers) > 1
    }


def _unpinned_protected(text: str) -> dict:
    """Protected packages that ``text`` names without an exact ``==`` pin.

    An empty mapping is the healthy answer. Any entry means a guard below would
    ``skip`` on a package the file genuinely carries.
    """
    pins = _parse_pins(text)
    return {
        name: specifier
        for name, specifier in _parse_requirements(text).items()
        if name in PROTECTED_PACKAGES and name not in pins
    }


def _pins(path: Path) -> dict:
    """``_parse_pins`` over a file, for the fixtures below."""
    return _parse_pins(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cpu_pins():
    return _pins(CPU_HEADER)


@pytest.fixture(scope="module")
def gpu_pins():
    return _pins(GPU_HEADER)


@pytest.fixture(scope="module")
def base_pins():
    return _pins(BASE_REQUIREMENTS)


class TestVllmAcceptsThePinnedOpenTelemetrySdk:
    """The GPU set pins both ``vllm`` and, via the shared base, ``opentelemetry-sdk``.

    Only the GPU header carries ``vllm``, so this conflict can only ever appear in
    the PyTorch image, which no pre-merge job builds.
    """

    def test_vllm_does_not_cap_the_pinned_opentelemetry_sdk(self, gpu_pins, base_pins):
        vllm = gpu_pins.get("vllm")
        sdk = base_pins.get("opentelemetry-sdk")
        if vllm is None or sdk is None:
            pytest.skip(
                "this guard only applies while the GPU header pins vllm and the "
                "shared base pins opentelemetry-sdk"
            )

        specifier = _measured(VLLM_OTEL_SPEC, vllm)
        if specifier is _MISSING:
            pytest.fail(
                f"vllm {vllm} is not in VLLM_OTEL_SPEC. Read its ``requires_dist`` on "
                f"PyPI, add the row with today's date, then re-run. Do not widen this "
                f"to a version comparison: vllm 0.19.0 RAISED its opentelemetry-sdk "
                f"floor to 1.27.0, so 'newer is always safe' is false and an "
                f"unmeasured release can reintroduce #472."
            )

        if specifier is None:
            return  # this vllm declares no opentelemetry-sdk dependency at all

        assert _satisfies(sdk, specifier), (
            f"vllm {vllm} requires opentelemetry-sdk{specifier}, but "
            f"requirements-base.txt pins opentelemetry-sdk=={sdk}. pip cannot resolve "
            f"the PyTorch image's requirement set, so the release build fails at the "
            f"build step (#472). Note the constraint has a FLOOR as well as any "
            f"ceiling, so downgrading the SDK is not a fix — and for vllm 0.8.5 the "
            f"whole accepted range is uninstallable on current setuptools, as "
            f"requirements-base.txt records."
        )


class TestBothBaseImagesAgreeOnTorch:
    """The CPU and GPU images must pin the same ``torch``.

    Decided 2026-09-15 while fixing #472. The conflict lived only in the GPU header,
    so the minimal fix would have moved that one and left the images on different
    torch versions — while the CPU image runs the embedding and reranking work. They
    move together instead, so a reader never has to wonder which image a torch-shaped
    bug came from.
    """

    def test_cpu_and_gpu_headers_pin_the_same_torch(self, cpu_pins, gpu_pins):
        cpu_torch = cpu_pins.get("torch")
        gpu_torch = gpu_pins.get("torch")
        assert cpu_torch is not None, "cpu-requirementsHEADER.txt must pin torch"
        assert gpu_torch is not None, "gpu-requirementsHEADER.txt must pin torch"
        assert cpu_torch == gpu_torch, (
            f"cpu-requirementsHEADER.txt pins torch=={cpu_torch} and "
            f"gpu-requirementsHEADER.txt pins torch=={gpu_torch}. Both base images "
            f"must carry the same torch: the CPU image runs embedding and reranking, "
            f"so a divergence makes a torch-shaped bug depend on which image served "
            f"the request. Move both, or record here why they diverge."
        )


class TestXformersMatchesTheTorchPin:
    """``xformers`` pins one exact ``torch`` per release, so the two move together."""

    def test_xformers_release_matches_the_pinned_torch(self, gpu_pins):
        xformers = gpu_pins.get("xformers")
        torch = gpu_pins.get("torch")
        if xformers is None or torch is None:
            pytest.skip("this guard only applies while the GPU header pins both")

        specifier = _measured(XFORMERS_TORCH_SPEC, xformers)
        if specifier is _MISSING:
            pytest.fail(
                f"xformers {xformers} is not in XFORMERS_TORCH_SPEC. Read its "
                f"``requires_dist`` on PyPI, add the row with today's date, then "
                f"re-run. Do not delete this guard to get past it: xformers pins "
                f"torch exactly, so an unverified pair is a build failure waiting "
                f"for the next release (#472). Add the row under the FULL version, "
                f"post identifier included: 0.0.29 and 0.0.29.post2 are different "
                f"distributions built against different torch versions."
            )

        assert _satisfies(torch, specifier), (
            f"xformers {xformers} requires torch{specifier}, but the GPU "
            f"header pins torch=={torch}. xformers ships one build per torch "
            f"release, so this set cannot resolve."
        )


class TestVllmMatchesTheTorchPin:
    """``vllm`` pins ``torch`` exactly, and until 2026-09-16 nothing checked it.

    The GPU header states the constraint in its own comment — "vllm pins torch exactly,
    and xformers ships one build per torch release" — but only the xformers half of that
    sentence had a table. So the header asserted a coupling the guards did not enforce.

    Review on 2026-09-16 raised it with the right example: vllm 0.10.0 requires
    ``torch==2.7.1``, so bumping vllm to 0.10.0 and leaving ``torch==2.7.0`` produces a
    set pip cannot resolve. Measured from PyPI ``requires_dist`` on 2026-09-16.

    Honest note on how reachable that was before this guard existed: a bump to 0.10.0
    would still have failed, because ``VLLM_TRANSFORMERS_SPEC`` holds only 0.9.0 and
    fails closed on anything else. That protection is incidental, not designed — the
    transformers table exists to catch an import collision, not to gate torch — and it
    dissolves the moment somebody follows that failure message, measures the transformers
    range for the new vllm and adds the row. At that point torch is unguarded again. A
    constraint the header names deserves its own table rather than a side effect of a
    different one.
    """

    def test_vllm_release_matches_the_pinned_torch(self, gpu_pins):
        vllm = gpu_pins.get("vllm")
        torch = gpu_pins.get("torch")
        if vllm is None or torch is None:
            pytest.skip("this guard only applies while the GPU header pins both")

        specifier = _measured(VLLM_TORCH_SPEC, vllm)
        if specifier is _MISSING:
            pytest.fail(
                f"vllm {vllm} is not in VLLM_TORCH_SPEC. Read its ``requires_dist`` on "
                f"PyPI, add the row with today's date, then re-run. vllm pins torch "
                f"exactly and every release so far moved that pin, so an unmeasured "
                f"release is a resolution failure waiting for the next bump (#472)."
            )

        assert _satisfies(torch, specifier), (
            f"vllm {vllm} requires torch{specifier}, but "
            f"gpu-requirementsHEADER.txt pins torch=={torch}. vllm pins torch exactly, "
            f"so this set cannot resolve. torch, vllm and xformers move as one unit: "
            f"changing any one of the three means re-measuring the other two."
        )

    @pytest.mark.parametrize(
        "vllm_version, torch_version",
        [
            ("0.8.5", "2.6.0"),
            ("0.8.5.post1", "2.6.0"),
            ("0.9.0", "2.7.0"),
            ("0.9.0.1", "2.7.0"),
            ("0.9.1", "2.7.0"),
            ("0.9.2", "2.7.0"),
            ("0.10.0", "2.7.1"),
            ("0.11.0", "2.8.0"),
            ("0.12.0", "2.9.0"),
            ("0.15.0", "2.9.1"),
            ("0.19.0", "2.10.0"),
            ("0.22.0", "2.11.0"),
            ("0.25.0", "2.11.0"),
            ("0.27.0", "2.13.0"),
            ("0.29.0", "2.13.0"),
        ],
    )
    def test_each_measured_vllm_release_accepts_its_own_torch(
        self, vllm_version, torch_version
    ):
        specifier = _measured(VLLM_TORCH_SPEC, vllm_version)
        assert specifier is not _MISSING, (
            f"vllm {vllm_version} is measured in this test but missing from "
            f"VLLM_TORCH_SPEC."
        )
        assert _satisfies(torch_version, specifier), (
            f"vllm {vllm_version} requires torch=={torch_version} as measured, but "
            f"the table says torch{specifier}."
        )

    @pytest.mark.parametrize(
        "vllm_version, torch_version, why",
        [
            (
                "0.10.0",
                "2.7.0",
                "the scenario review raised: bumping vllm one release while leaving "
                "torch behind. 0.10.0 requires torch==2.7.1",
            ),
            (
                "0.9.0",
                "2.7.1",
                "and the mirror image: moving torch forward while vllm stays at 0.9.0, "
                "which requires torch==2.7.0 exactly",
            ),
            (
                "0.29.0",
                "2.7.0",
                "a large vllm jump with torch untouched is the same failure, louder",
            ),
        ],
    )
    def test_a_vllm_bump_that_leaves_torch_behind_is_rejected(
        self, vllm_version, torch_version, why
    ):
        specifier = _measured(VLLM_TORCH_SPEC, vllm_version)
        assert specifier is not _MISSING, f"vllm {vllm_version} must be measured"
        assert not _satisfies(torch_version, specifier), why


class TestPytorchBaseImageMatchesTheTorchPin:
    """The ``FROM`` image already ships a torch, and ``pip`` then installs the pin.

    When the two disagree, ``pip`` replaces the base image's torch with a PyPI wheel
    while the image keeps the older CUDA and cuDNN system libraries underneath. The
    build still succeeds, so nothing fails until a GPU import or a kernel launch —
    and ``vllm`` and ``xformers`` both carry native extensions compiled against one
    specific torch and CUDA pair.

    This guard needs no measured table and no network: both values live in this
    repository, so it cannot rot the way ``XFORMERS_TORCH_SPEC`` and
    ``TORCH_SYMPY_SPEC`` can. It compares only the torch version. Whether the
    image's CUDA matches the ``nvidia-*-cu12`` wheels the set resolves to is a
    resolver question, not a text question — see the module docstring.
    """

    def test_from_image_declares_the_pinned_torch(self, gpu_pins):
        torch = gpu_pins.get("torch")
        assert torch is not None, "gpu-requirementsHEADER.txt must pin torch"

        content = PYTORCH_BASE_DOCKERFILE.read_text(encoding="utf-8")
        stages = _pytorch_from_stages(content)
        assert stages, (
            f"{PYTORCH_BASE_DOCKERFILE} has no recognizable "
            f"``FROM .../pytorch/pytorch:<torch>-cuda<ver>`` line. If the base image "
            f"moved to a different publisher, update _PYTORCH_FROM_PATTERN rather "
            f"than deleting this guard."
        )
        assert len(stages) == 1, (
            f"{PYTORCH_BASE_DOCKERFILE} names {len(stages)} pytorch base stages "
            f"{stages}. This guard cannot tell which one runs `pip install -r "
            f"requirements.txt`, so it fails closed rather than checking the first and "
            f"reporting confidence it does not have. If this file became a multi-stage "
            f"build deliberately, teach the guard which stage installs the "
            f"requirements — do not relax it to the first match."
        )
        image_torch, image_cuda = stages[0]

        assert _normalize_version(image_torch) == _normalize_version(torch), (
            f"the PyTorch base image is built FROM pytorch/pytorch:{image_torch}-"
            f"cuda{image_cuda}, but gpu-requirementsHEADER.txt pins torch=={torch}. "
            f"pip would install the pinned wheel over the image's torch and leave "
            f"CUDA {image_cuda} underneath it, so the build succeeds and the failure "
            f"lands at GPU import or kernel launch instead. vllm and xformers carry "
            f"native extensions, so the pair has to agree. Move the FROM tag to a "
            f"torch {torch} image whose CUDA matches the nvidia-*-cu12 wheels the "
            f"requirement set resolves to."
        )


class TestTransformersIsPinnedWithinWhatVllmImportsWith:
    """``transformers`` must be pinned, and pinned below vllm's import-time ceiling.

    Leaving it unpinned is the defect, not a style choice. vllm declares no ceiling, so
    pip takes the newest transformers, and every version from 4.54.0 on breaks
    ``import vllm`` on a name collision that no resolver can see. An unpinned
    transitive dependency here means the GPU image's importability changes with
    whatever PyPI published most recently.
    """

    def test_transformers_is_pinned(self, base_pins):
        assert "transformers" in base_pins, (
            "requirements-base.txt must pin transformers. It is unpinned today, so "
            "pip resolves whatever is newest and vllm 0.9.0 fails to import against "
            "anything from 4.54.0 on — a break that appears with no change to this "
            "repository at all. See VLLM_TRANSFORMERS_SPEC."
        )

    def test_transformers_is_within_vllms_import_range(self, gpu_pins, base_pins):
        vllm = gpu_pins.get("vllm")
        transformers = base_pins.get("transformers")
        if vllm is None:
            pytest.skip("this guard only applies while the GPU header pins vllm")
        assert transformers is not None, "requirements-base.txt must pin transformers"

        specifier = _measured(VLLM_TRANSFORMERS_SPEC, vllm)
        if specifier is _MISSING:
            pytest.fail(
                f"vllm {vllm} is not in VLLM_TRANSFORMERS_SPEC. This range cannot be "
                f"read off PyPI metadata — vllm declares no ceiling. Import vllm "
                f"inside a built GPU image against candidate transformers versions, "
                f"record what actually works with today's date, then re-run."
            )

        assert _satisfies(transformers, specifier), (
            f"vllm {vllm} imports only with transformers{specifier}, but "
            f"requirements-base.txt pins transformers=={transformers}. The image will "
            f"BUILD and then fail at ``import vllm`` with \"'aimv2' is already used by "
            f'a Transformers config". Resolution cannot catch this; only an import in '
            f"a built image can."
        )


class TestSympySatisfiesTheTorchPin:
    """``torch`` constrains ``sympy``, and the two live in different files.

    The GPU header pins torch; the shared base pins sympy. Neither file shows the
    other, so this is the coupling the first attempt at #472 missed — the vllm and
    xformers guards were green while the set still failed to resolve.
    """

    @pytest.mark.parametrize("header_name", ["cpu", "gpu"])
    def test_sympy_meets_the_floor_that_the_pinned_torch_requires(
        self, header_name, cpu_pins, gpu_pins, base_pins
    ):
        pins = cpu_pins if header_name == "cpu" else gpu_pins
        torch = pins.get("torch")
        sympy = base_pins.get("sympy")
        if torch is None or sympy is None:
            pytest.skip(
                "this guard only applies while the header pins torch and the shared "
                "base pins sympy"
            )

        specifier = _measured(TORCH_SYMPY_SPEC, torch)
        if specifier is _MISSING:
            pytest.fail(
                f"torch {torch} is not in TORCH_SYMPY_SPEC. Read its "
                f"``requires_dist`` on PyPI, add the row with today's date, then "
                f"re-run. Do not delete this guard to get past it: torch pinned sympy "
                f"exactly at 2.6.0 and moved to a floor at 2.7.0, so an unverified "
                f"pair is a base-image build failure (#472)."
            )

        assert _satisfies(sympy, specifier), (
            f"{header_name}-requirementsHEADER.txt pins torch=={torch}, which requires "
            f"sympy{specifier}, but requirements-base.txt pins "
            f"sympy=={sympy}. The base-image requirement set cannot resolve. Note "
            f"torch 2.6.0 pins sympy EXACTLY, so on a torch downgrade a higher sympy "
            f"is as wrong as a lower one. sympy is in the SHARED base while torch is "
            f"in the headers, so neither file shows this on its own — move them "
            f"together."
        )


class TestSatisfiesModelsTheWholeDeclaredRange:
    """The comparison helper must honour every clause a release declares.

    Three holes in the first version of this module, all found by review on
    2026-09-15 and all the same mistake: a one-sided comparison standing in for a
    declared range. Each case below is green under the correct helper and was green
    under the broken guards too — which is the point, since the broken guards were
    green while the pair could not resolve.
    """

    @pytest.mark.parametrize(
        "candidate, specifier, expected, why",
        [
            (
                "1.26.0",
                ">=1.26.0,<1.27.0",
                True,
                "the lower bound is inclusive",
            ),
            (
                "1.44.0",
                ">=1.26.0,<1.27.0",
                False,
                "vllm 0.8.5's ceiling excludes 1.44.0 — the original #472 conflict",
            ),
            (
                "1.25.0",
                ">=1.26.0",
                False,
                "a floor-only range still has a floor: vllm 0.9.0 needs >=1.26.0, so "
                "an SDK downgrade must not pass",
            ),
            (
                "1.26.0",
                ">=1.27.0",
                False,
                "vllm 0.19.0 raised the floor to 1.27.0, so constraints are not "
                "monotonic across releases",
            ),
            (
                "1.13.3",
                "==1.13.1",
                False,
                "torch 2.6.0 pins sympy exactly, so a HIGHER sympy is still wrong",
            ),
            (
                "1.13.1",
                "==1.13.1",
                True,
                "the exact pin is satisfied only by itself",
            ),
        ],
    )
    def test_every_clause_is_enforced(self, candidate, specifier, expected, why):
        assert _satisfies(candidate, specifier) is expected, why


class TestVersionComparisonFollowsPep440:
    """Ordered bounds must follow PEP 440, not a truncated numeric tuple.

    Adversarial review on 2026-09-16 found the hand-rolled comparison wrong in both
    directions. ``_release`` kept only leading numeric components, so it stopped at the
    first non-numeric one: ``1.27.0rc1`` compared as ``(1, 27)``, and so did the
    abbreviated ``1.27``. Against ``<1.27.0`` — vllm 0.8.5's ceiling, the exact
    constraint behind #472 — ``(1, 27) < (1, 27, 0)`` held, so both passed.

    Both are wrong. PEP 440 zero-pads, so ``1.27`` IS ``1.27.0`` and the ceiling
    excludes it; and an exclusive ordered bound excludes a pre-release of its own
    version, so ``<1.27.0`` excludes ``1.27.0rc1`` too. The truncation also produced
    false REDS: ``1.26.1rc1`` and ``1.26`` both failed ``>=1.26.0``, which PEP 440
    accepts.

    Hand-rolling PEP 440 was the mistake. ``packaging`` decides this now — it is an
    unconditional pytest dependency (``packaging>=22``), so it is present wherever this
    suite runs, and it adds nothing to either image.

    ``prereleases=True`` is deliberate: every version reaching these helpers comes from
    an explicit ``==`` pin in a requirement file, which is exactly the case where pip
    also considers a pre-release.
    """

    @pytest.mark.parametrize(
        "candidate, specifier, expected, why",
        [
            (
                "1.27.0rc1",
                ">=1.26.0,<1.27.0",
                False,
                "an exclusive ordered bound excludes a pre-release of its own "
                "version, so vllm 0.8.5's ceiling rejects 1.27.0rc1. The truncating "
                "comparison passed it",
            ),
            (
                "1.27",
                ">=1.26.0,<1.27.0",
                False,
                "PEP 440 zero-pads, so 1.27 is 1.27.0 and the ceiling excludes it. "
                "The truncating comparison passed it",
            ),
            (
                "1.26.1rc1",
                ">=1.26.0",
                True,
                "1.26.1rc1 is above the 1.26.0 floor. The truncating comparison "
                "rejected it — a false red on a pin that resolves",
            ),
            (
                "1.26",
                ">=1.26.0",
                True,
                "1.26 is 1.26.0, which meets its own floor. Also a false red before",
            ),
            (
                "2.7.0.post1",
                "==2.7.0",
                False,
                "a post release is a different distribution, so an exact pin rejects "
                "it — the finding-6 behaviour, now enforced by packaging",
            ),
            (
                "2.7.0",
                "==2.7.0",
                True,
                "the exact pin is satisfied by itself",
            ),
            (
                "1.44.0",
                ">=1.26.0,<1.27.0",
                False,
                "the original #472 conflict: the shared base's SDK against vllm "
                "0.8.5's ceiling",
            ),
        ],
    )
    def test_the_comparison_matches_pep_440(self, candidate, specifier, expected, why):
        assert _satisfies(candidate, specifier) is expected, why

    @pytest.mark.parametrize(
        "label, table",
        [
            ("VLLM_OTEL_SPEC", "vllm_otel"),
            ("XFORMERS_TORCH_SPEC", "xformers_torch"),
            ("VLLM_TRANSFORMERS_SPEC", "vllm_transformers"),
            ("TORCH_SYMPY_SPEC", "torch_sympy"),
            ("VLLM_TORCH_SPEC", "vllm_torch"),
        ],
    )
    def test_every_recorded_specifier_parses(self, label, table):
        tables = {
            "vllm_otel": VLLM_OTEL_SPEC,
            "xformers_torch": XFORMERS_TORCH_SPEC,
            "vllm_transformers": VLLM_TRANSFORMERS_SPEC,
            "torch_sympy": TORCH_SYMPY_SPEC,
            "vllm_torch": VLLM_TORCH_SPEC,
        }
        for key, specifier in tables[table].items():
            try:
                Version(key)
            except InvalidVersion:
                pytest.fail(f"{label} key {key!r} is not a PEP 440 version")
            if specifier is None:
                continue
            try:
                SpecifierSet(specifier)
            except InvalidSpecifier:
                pytest.fail(
                    f"{label}[{key!r}] holds {specifier!r}, which is not a PEP 440 "
                    f"specifier. Copy it verbatim from the package's requires_dist."
                )

    def test_an_unsupported_operator_is_rejected_rather_than_ignored(self):
        with pytest.raises(InvalidSpecifier):
            _satisfies("1.0.0", "=>1.0.0")


class TestMeasuredTablesKeyOnTheFullVersion:
    """A table key must identify ONE distribution, post identifier included.

    Review on 2026-09-16 found the table keys were numeric release tuples, so
    ``_release`` mapped ``0.0.29``, ``0.0.29.post1``, ``0.0.29.post2`` and
    ``0.0.29.post3`` all onto ``(0, 0, 29)`` — four distributions, one row. The row
    then held whichever constraint was measured last, and the module reported it for
    the other three.

    That was not hypothetical: the row read ``torch==2.6.0``, which is the post2 and
    post3 constraint, while xformers 0.0.29 and 0.0.29.post1 both require
    ``torch==2.5.1``. So the guard approved xformers 0.0.29 with torch 2.6.0 — a pair
    pip rejects — and rejected xformers 0.0.29 with torch 2.5.1, which is the pair
    upstream actually built. Measured from PyPI ``requires_dist`` on 2026-09-16.
    """

    @pytest.mark.parametrize(
        "xformers_version, torch_version",
        [
            ("0.0.29", "2.5.1"),
            ("0.0.29.post1", "2.5.1"),
            ("0.0.29.post2", "2.6.0"),
            ("0.0.29.post3", "2.6.0"),
            ("0.0.30", "2.7.0"),
            ("0.0.31", "2.7.1"),
            ("0.0.31.post1", "2.7.1"),
            ("0.0.32.post1", "2.8.0"),
            ("0.0.32.post2", "2.8.0"),
            ("0.0.33", "2.9.0"),
            ("0.0.33.post1", "2.9.0"),
            ("0.0.33.post2", "2.9.1"),
        ],
    )
    def test_each_measured_xformers_release_accepts_its_own_torch(
        self, xformers_version, torch_version
    ):
        specifier = _measured(XFORMERS_TORCH_SPEC, xformers_version)
        assert specifier is not _MISSING, (
            f"xformers {xformers_version} is measured in this test but missing from "
            f"XFORMERS_TORCH_SPEC."
        )
        assert _satisfies(torch_version, specifier), (
            f"xformers {xformers_version} requires torch=={torch_version} as "
            f"measured, but the table says torch{specifier}."
        )

    @pytest.mark.parametrize(
        "left, right, why",
        [
            (
                "0.0.29",
                "0.0.29.post2",
                "xformers rebuilt 0.0.29 against a newer torch under a post tag, so "
                "collapsing the two reuses the wrong constraint",
            ),
            (
                "0.0.33",
                "0.0.33.post2",
                "the same split happened again at 0.0.33: 2.9.0 versus 2.9.1",
            ),
        ],
    )
    def test_a_post_release_is_not_read_as_its_base_release(self, left, right, why):
        assert _measured(XFORMERS_TORCH_SPEC, left) != _measured(
            XFORMERS_TORCH_SPEC, right
        ), why

    @pytest.mark.parametrize(
        "candidate, bound, expected, why",
        [
            ("2.7.0", "2.7.0", True, "the exact pin is satisfied by itself"),
            (
                "2.7.0.post1",
                "2.7.0",
                False,
                "a post release is a DIFFERENT distribution, so an exact clause must "
                "reject it rather than truncating it to the release segment",
            ),
            (
                "1.13.1",
                "1.13.1",
                True,
                "torch 2.6.0's exact sympy pin still matches itself",
            ),
        ],
    )
    def test_an_exact_clause_compares_the_whole_version(
        self, candidate, bound, expected, why
    ):
        assert _satisfies(candidate, f"=={bound}") is expected, why

    @pytest.mark.parametrize(
        "label, table",
        [
            ("VLLM_OTEL_SPEC", "vllm_otel"),
            ("XFORMERS_TORCH_SPEC", "xformers_torch"),
            ("VLLM_TRANSFORMERS_SPEC", "vllm_transformers"),
            ("TORCH_SYMPY_SPEC", "torch_sympy"),
            ("VLLM_TORCH_SPEC", "vllm_torch"),
        ],
    )
    def test_every_table_is_keyed_by_version_strings(self, label, table):
        tables = {
            "vllm_otel": VLLM_OTEL_SPEC,
            "xformers_torch": XFORMERS_TORCH_SPEC,
            "vllm_transformers": VLLM_TRANSFORMERS_SPEC,
            "torch_sympy": TORCH_SYMPY_SPEC,
            "vllm_torch": VLLM_TORCH_SPEC,
        }
        offenders = [key for key in tables[table] if not isinstance(key, str)]
        assert not offenders, (
            f"{label} is keyed by {offenders}, not by version strings. A numeric "
            f"tuple key drops the post identifier and merges distinct distributions "
            f"into one row."
        )


class TestProtectedPackagesCarryAnExactPin:
    """A protected package written as a RANGE must fail, not read as absent.

    Every guard above skips when its subject is missing from the file, and for a
    genuinely absent package that is right — the CPU header carries no vllm. But
    ``_parse_pins`` reads only ``==``, so ``vllm>=0.9.0`` is indistinguishable from
    no vllm at all and the skip turns the guard OFF rather than tightening it.

    Found by review on 2026-09-16. A range on any of vllm, opentelemetry-sdk, sympy
    or xformers silently disabled that package's guard while pip stayed free to
    resolve an unmeasured release — the same green-guard-failing-build shape as #472
    itself. An extras marker did it too: ``_PIN_PATTERN`` did not allow ``[...]``, so
    ``torch[opt]==2.7.0`` also read as absent.
    """

    @pytest.mark.parametrize(
        "line, expected",
        [
            ("vllm>=0.9.0", {"vllm": ">=0.9.0"}),
            ("opentelemetry-sdk>=1.26.0", {"opentelemetry-sdk": ">=1.26.0"}),
            ("sympy>1.13.0,<2", {"sympy": ">1.13.0,<2"}),
            ("xformers", {"xformers": ""}),
            ("vllm==0.9.0", {}),
            ("torch[opt]==2.7.0", {}),
            ("langgraph-prebuilt<1.0.9", {}),
            ("# vllm>=0.9.0", {}),
        ],
    )
    def test_a_range_on_a_protected_package_is_not_read_as_absent(self, line, expected):
        assert _unpinned_protected(line) == expected

    @pytest.mark.parametrize(
        "label, path",
        [
            ("cpu-requirementsHEADER.txt", CPU_HEADER),
            ("gpu-requirementsHEADER.txt", GPU_HEADER),
            ("requirements-base.txt", BASE_REQUIREMENTS),
        ],
    )
    def test_every_protected_package_present_is_exactly_pinned(self, label, path):
        unpinned = _unpinned_protected(path.read_text(encoding="utf-8"))
        assert not unpinned, (
            f"{label} names {sorted(unpinned)} without an exact ``==`` pin "
            f"({unpinned}). Every guard in this module skips when its subject is "
            f"absent, and a range is read as absent, so this would disable the "
            f"guard instead of relaxing it. Pin the package exactly and add the "
            f"measured row, or drop it from PROTECTED_PACKAGES with a reason."
        )


class TestNoRequirementFileDefersToAnotherFile:
    """An ``-r``/``-c`` include would make a protected package invisible here.

    Adversarial review on 2026-09-16: ``_requirement_lines`` drops every line starting
    with ``-``, which is right for ``--extra-index-url`` (the CPU header has two) but
    wrong for ``-r other.txt`` and ``-c constraints.txt``. Those pull in real
    requirements that pip installs and this module cannot see, so a protected package
    supplied through an include reads as absent and turns its guard off — the same
    silent skip as a range, by a different route.

    Resolving includes recursively is the other possible answer. Refusing them is
    better here: the three files are assembled by ``cat`` in
    ``build_docker_images.sh``, so an include has no reason to appear, and a guard that
    fails closed on an unsupported construct beats one that half-supports it. No file
    uses an include today, so this costs nothing until somebody adds one.
    """

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("-r other.txt\n", ["-r other.txt"]),
            ("-c constraints.txt\n", ["-c constraints.txt"]),
            ("--requirement other.txt\n", ["--requirement other.txt"]),
            ("--constraint constraints.txt\n", ["--constraint constraints.txt"]),
            ("--extra-index-url https://example.invalid/simple\n", []),
            ("--index-url https://example.invalid/simple\n", []),
            ("torch==2.7.0\n", []),
            ("# -r other.txt\n", []),
        ],
    )
    def test_an_include_directive_is_reported(self, text, expected):
        assert _include_directives(text) == expected

    @pytest.mark.parametrize(
        "label, path",
        [
            ("cpu-requirementsHEADER.txt", CPU_HEADER),
            ("gpu-requirementsHEADER.txt", GPU_HEADER),
            ("requirements-base.txt", BASE_REQUIREMENTS),
            ("base-python-image/requirements.txt", GENERATED_CPU_REQUIREMENTS),
            ("base-pytorch-image/requirements.txt", GENERATED_GPU_REQUIREMENTS),
        ],
    )
    def test_no_file_pulls_requirements_in_from_elsewhere(self, label, path):
        includes = _include_directives(path.read_text(encoding="utf-8"))
        assert not includes, (
            f"{label} uses {includes}. Every guard in this module reads only the file "
            f"in front of it, so a package supplied through an include is invisible "
            f"here and its guard silently skips, while pip installs it anyway. These "
            f"sets are assembled by ``cat``, so inline the pins instead — or teach "
            f"_requirement_lines to resolve includes before relaxing this."
        )


class TestProtectedPinsAreUnconditional:
    """A protected pin carrying an environment marker must fail closed.

    Found by the async reviewer on 2026-09-16, on the parser this round introduced.
    ``_requirement_lines`` cuts a line at ``;`` to drop the marker, so
    ``transformers==4.53.3; sys_platform == "win32"`` reads here as an unconditional
    exact pin: ``_parse_pins`` records 4.53.3 and ``_unpinned_protected`` reports
    healthy. On the Linux image pip ignores that line completely, leaving transformers
    unpinned and free to resolve 4.54+ — which is precisely the ``aimv2`` import break
    this guard exists to prevent.

    Evaluating markers against the image environment is the other option. Failing
    closed is the better answer for these files: they are ``cat``-concatenated into one
    Linux image each, a conditional pin on a protected package has no purpose here, and
    a guard that refuses a construct it cannot model beats one that quietly mismodels
    it. Markers on other packages are untouched. No file uses one today.
    """

    @pytest.mark.parametrize(
        "text, expected",
        [
            (
                'transformers==4.53.3; sys_platform == "win32"',
                {"transformers": 'sys_platform == "win32"'},
            ),
            (
                'torch==2.7.0 ; python_version >= "3.9"',
                {"torch": 'python_version >= "3.9"'},
            ),
            ("transformers==4.53.3", {}),
            ('markitdown[pdf,pptx]==0.1.5; sys_platform == "linux"', {}),
            ('# transformers==4.53.3; sys_platform == "win32"', {}),
        ],
    )
    def test_a_marker_on_a_protected_pin_is_reported(self, text, expected):
        assert _conditional_protected(text) == expected

    @pytest.mark.parametrize(
        "label, path",
        [
            ("cpu-requirementsHEADER.txt", CPU_HEADER),
            ("gpu-requirementsHEADER.txt", GPU_HEADER),
            ("requirements-base.txt", BASE_REQUIREMENTS),
            ("base-python-image/requirements.txt", GENERATED_CPU_REQUIREMENTS),
            ("base-pytorch-image/requirements.txt", GENERATED_GPU_REQUIREMENTS),
        ],
    )
    def test_no_protected_pin_is_conditional(self, label, path):
        conditional = _conditional_protected(path.read_text(encoding="utf-8"))
        assert not conditional, (
            f"{label} declares {sorted(conditional)} with an environment marker "
            f"({conditional}). The marker is stripped before these guards read the "
            f"pin, so a line pip skips on this image still reads here as an "
            f"unconditional pin — the guard passes while the package is effectively "
            f"unpinned. Drop the marker, or evaluate markers against the image "
            f"environment before relaxing this."
        )

    def test_a_continued_environment_marker_is_reported(self):
        """A marker split onto the next physical line via backslash-continuation must
        still be detected.

        pip joins ``vllm==0.9.0 \\`` and ``; python_version < "3.11"`` into one
        logical line before evaluating the marker. Measured: pip 26.1.2's ``join_lines``
        yields a single requirement string containing the semicolon-separated marker.
        ``_conditional_protected`` iterating ``text.splitlines()`` instead sees only
        the first physical half, finds no ``;``, and returns ``{}`` — hiding the
        conditional pin. Review finding 3, 2026-09-22.
        """
        text = 'vllm==0.9.0 \\\n; python_version < "3.11"\n'
        assert _conditional_protected(text) == {"vllm": 'python_version < "3.11"'}
        assert _parse_pins(text) == {"vllm": "0.9.0"}

    @pytest.mark.parametrize(
        "text",
        [
            "vllm==0.9.0 --config-settings=foo=bar;baz\n",
            "vllm==0.9.0 \\\n    --config-settings=foo=bar;baz\n",
            "vllm==0.9.0 -Cfoo=bar;baz\n",
            'vllm==0.9.0 --hash=sha256:aa ; python_version < "3.11"\n',
        ],
    )
    def test_a_semicolon_inside_a_per_requirement_option_is_not_a_marker(self, text):
        """pip separates the options from the requirement BEFORE it reads the marker.

        Measured at 5380d135, pip 26.1.2: ``break_args_options`` cuts the line at the
        first token beginning with ``-``, so
        ``vllm==0.9.0 --config-settings=foo=bar;baz`` parses as the requirement
        ``vllm==0.9.0`` with NO marker and the config setting ``foo`` = ``bar;baz``.
        The same split sends the trailing ``; python_version < "3.11"`` of the last
        spelling into the option string, where it is not a marker either
        (``install_req_from_line`` reports ``markers=None`` for all four).

        Review finding 1 of 2026-09-22 (Codex, comment 4069452136):
        ``_conditional_protected`` partitioned the whole logical line on ``;``, so the
        option value's own semicolon read as an environment marker and the guard
        rejected a valid unconditional pin. Options are cut away first.
        """
        assert _conditional_protected(text) == {}
        assert _parse_pins(text) == {"vllm": "0.9.0"}

    def test_a_marker_in_front_of_an_option_is_still_reported(self):
        """Cutting the options must not cut a marker that precedes them.

        Measured at 5380d135, pip 26.1.2: for
        ``vllm==0.9.0 ; python_version < "3.11" --hash=sha256:aa`` the requirement
        half keeps the marker (``markers=python_version < "3.11"``), so the guard must
        still report the conditional pin — and report the marker alone, without the
        option text trailing it.
        """
        text = 'vllm==0.9.0 ; python_version < "3.11" --hash=sha256:aa\n'
        assert _conditional_protected(text) == {"vllm": 'python_version < "3.11"'}


class TestProtectedPackagesAreDeclaredOnce:
    """A protected package must be declared once, in one place.

    Adversarial review on 2026-09-16 found that both parsers keep only the last
    occurrence of a name, so a repeated declaration hides a contradiction instead of
    reporting it. ``vllm==0.9.0`` followed by ``vllm<0.9.0`` leaves an exact pin in
    ``_parse_pins``, ``_unpinned_protected`` reports healthy, and every pairwise guard
    passes — while pip correctly calls the set impossible.

    The concatenation is why this is not an exotic case. ``build_docker_images.sh``
    builds each image's set by joining a header with ``requirements-base.txt``, so a
    package pinned in BOTH files produces two lines in one file with nothing in this
    module comparing them: ``gpu_pins`` and ``base_pins`` are separate dicts. torch is
    pinned in the headers and sympy and transformers in the shared base today, and
    nothing stops the next edit from adding torch to the base.

    So the duplicate check runs over the GENERATED sets as well — the only files pip
    actually reads, and the only place a cross-file duplicate can be seen at all.
    """

    @pytest.mark.parametrize(
        "text, expected",
        [
            (
                "vllm==0.9.0\nvllm<0.9.0\n",
                {"vllm": ["==0.9.0", "<0.9.0"]},
            ),
            (
                "torch==2.7.0\ntorch==2.6.0\n",
                {"torch": ["==2.7.0", "==2.6.0"]},
            ),
            (
                "vllm==0.9.0\nVLLM==0.9.0\n",
                {"vllm": ["==0.9.0", "==0.9.0"]},
            ),
            ("vllm==0.9.0\n", {}),
            ("numpy==1.0\nnumpy==2.0\n", {}),
        ],
    )
    def test_a_repeated_protected_declaration_is_reported(self, text, expected):
        assert _duplicated_protected(text) == expected

    @pytest.mark.parametrize(
        "label, path",
        [
            ("cpu-requirementsHEADER.txt", CPU_HEADER),
            ("gpu-requirementsHEADER.txt", GPU_HEADER),
            ("requirements-base.txt", BASE_REQUIREMENTS),
            ("base-python-image/requirements.txt", GENERATED_CPU_REQUIREMENTS),
            ("base-pytorch-image/requirements.txt", GENERATED_GPU_REQUIREMENTS),
        ],
    )
    def test_each_protected_package_is_declared_once(self, label, path):
        duplicated = _duplicated_protected(path.read_text(encoding="utf-8"))
        assert not duplicated, (
            f"{label} declares {sorted(duplicated)} more than once ({duplicated}). "
            f"Both parsers keep the last occurrence, so the earlier one is invisible "
            f"to every guard while pip still sees both and can call the set "
            f"impossible. Declare the package in exactly one file."
        )

    @pytest.mark.parametrize(
        "label, header, generated",
        [
            ("base-python-image", CPU_HEADER, GENERATED_CPU_REQUIREMENTS),
            ("base-pytorch-image", GPU_HEADER, GENERATED_GPU_REQUIREMENTS),
        ],
    )
    def test_the_generated_set_matches_its_sources(self, label, header, generated):
        expected = header.read_text(encoding="utf-8") + BASE_REQUIREMENTS.read_text(
            encoding="utf-8"
        )
        assert generated.read_text(encoding="utf-8") == expected, (
            f"{label}/requirements.txt is not "
            f"``cat {header.name} requirements-base.txt``. That generated file is "
            f"what the image installs and what a ``pip install --dry-run`` resolves, "
            f"while every other guard here reads the sources — so a stale generated "
            f"file means this module is green about a requirement set the build does "
            f"not use. Re-run scripts/dev/build_docker_images.sh, or regenerate the "
            f"two files with that same concatenation."
        )


class TestParsingFailsClosedOnRequirementBearingOptions:
    """Adversarial review, 2026-09-16: a shape the parser cannot read was treated as
    a shape that is not there. That is backwards for a guard.
    """

    def test_an_editable_directive_is_reported_as_unreadable(self):
        """``-e``/``--editable`` installs a real project, so it must not vanish.

        ``_requirement_lines`` drops every line starting with ``-``, and the include
        matcher recognised only ``-r``/``-c``. So ``-e git+...#egg=vllm`` was invisible:
        every protected-dependency guard saw vllm as absent and SKIPPED, which would let
        an arbitrary unmeasured checkout into the GPU image.
        """
        text = "\n".join(
            [
                "torch==2.7.0",
                "-e git+https://github.com/vllm-project/vllm@main#egg=vllm",
                "--editable ./local/vllm",
            ]
        )
        reported = _requirement_bearing_directives(text)
        assert len(reported) == 2, (
            f"both editable directives must be reported as unreadable, got {reported}. "
            f"An editable install supplies a package no guard here can version-check, "
            f"so it must fail the suite rather than read as absent."
        )

    def test_include_directives_are_still_reported(self):
        text = "-r other.txt\n--constraint pins.txt\ntorch==2.7.0"
        assert len(_requirement_bearing_directives(text)) == 2

    def test_plain_option_lines_stay_inert(self):
        text = (
            "--extra-index-url https://example.invalid/simple\n"
            "--prefer-binary\n"
            "torch==2.7.0"
        )
        assert _requirement_bearing_directives(text) == []


class TestPytorchBaseStagesAreUnambiguous:
    """A multi-stage Dockerfile can name more than one pytorch base.

    ``re.search`` returned the FIRST match only, so a builder stage on the right torch
    could mask a later stage on the wrong one while ``pip install -r requirements.txt``
    ran against the mismatched base.
    """

    def test_every_pytorch_stage_is_collected(self):
        content = "\n".join(
            [
                "FROM docker.io/pytorch/pytorch:2.7.0-cuda12.6-cudnn9-devel AS builder",
                "RUN echo build",
                "FROM docker.io/pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel",
                "RUN pip install -r requirements.txt",
            ]
        )
        stages = _pytorch_from_stages(content)
        assert stages == [("2.7.0", "12.6"), ("2.6.0", "12.4")], (
            f"both stages must be collected, got {stages}. Taking only the first lets "
            f"a correct builder stage hide a wrong runtime stage."
        )

    def test_a_single_stage_is_still_read(self):
        content = "FROM docker.io/pytorch/pytorch:2.7.0-cuda12.6-cudnn9-devel\nRUN true"
        assert _pytorch_from_stages(content) == [("2.7.0", "12.6")]


class TestOpaqueRequirementsFailClosed:
    """A requirement with no readable project name must fail, not resolve to nonsense.

    Review on 2026-09-16: ``git+https://host/repo.git#egg=vllm`` had its ``#egg=vllm``
    fragment stripped as a comment, and the name matcher then recorded the project as
    ``git``. Every vllm guard read vllm as ABSENT and skipped while pip installed an
    arbitrary checkout. These lines do not begin with ``-``, so the directive matcher
    never saw them either.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "git+https://github.com/vllm-project/vllm@main#egg=vllm",
            "hg+https://host/repo#egg=vllm",
            "https://host/vllm-0.9.0-py3-none-any.whl",
            "file:///opt/wheels/vllm-0.9.0.whl",
        ],
    )
    def test_a_vcs_or_url_requirement_is_reported(self, line):
        assert _opaque_requirements(f"torch==2.7.0\n{line}\n"), (
            f"{line!r} supplies a package with no version this module can check, so it "
            f"must be reported rather than parsed into a bogus project name."
        )

    @pytest.mark.parametrize(
        "line",
        [
            "./local_vllm",
            "../pkgs/vllm",
            "/opt/vllm.whl",
            "vllm-0.9.0-py3-none-any.whl",
            "dist/vllm-0.9.0.tar.gz",
            ".\\win_vllm",
            "vllm-0.9.0.tar.bz2",
            "vllm-0.9.0.zip",
            "vllm-0.9.0.tlz",
            "vllm-0.9.0.tar.lz",
            "vllm-0.9.0.tar.lzma",
            "vendor packages/vllm",
            "vendor packages/vllm-0.9.0.tar.gz",
            "my package.tar.gz",
            "vllm-0.9.0-py3-none-any.whl[foo]",
            "vllm-0.9.0.tar.gz[extra]",
            "evil@../pkgs/vllm",
            "evil@/opt/vllm",
            "evil@C:\\pkgs\\vllm",
        ],
    )
    def test_a_local_path_or_archive_requirement_is_reported(self, line):
        assert _opaque_requirements(f"torch==2.7.0\n{line}\n"), (
            f"{line!r} is a bare local path or archive, which supplies a real package "
            f"with no version this module can check, so it must be reported rather than "
            f"silently skipped."
        )

    @pytest.mark.parametrize(
        "line",
        [
            "backports.tarfile==1.2.0",
            "zope.interface==5.4.0",
            "ruamel.yaml==0.18.6",
            "langgraph-prebuilt<1.0.9",
        ],
    )
    def test_a_dotted_project_name_pin_is_not_reported(self, line):
        assert _opaque_requirements(f"torch==2.7.0\n{line}\n") == [], (
            f"{line!r} is an ordinary dotted project name, not an archive path, and must "
            f"not be swept up by the path/archive class."
        )

    @pytest.mark.parametrize(
        "line",
        [
            "numpy@https://host/numpy-2.0.0-py3-none-any.whl",
            "numpy @ https://host/numpy-2.0.0-py3-none-any.whl",
            "vllm@https://host/vllm-0.9.0-py3-none-any.whl",
            "vllm@git+https://github.com/vllm-project/vllm",
        ],
    )
    def test_a_named_url_direct_reference_is_not_reported(self, line):
        assert _opaque_requirements(f"torch==2.7.0\n{line}\n") == [], (
            f"{line!r} is a PEP 508 direct reference whose project name the name reader "
            f"does read, so design D4 leaves it readable and _unpinned_protected fails "
            f"it closed. Reporting the compact spelling while the spaced spelling "
            f"passes makes the verdict depend on whitespace alone."
        )

    def test_a_protected_direct_reference_still_fails_closed(self):
        unpinned = _unpinned_protected(
            "torch==2.7.0\nvllm@https://host/vllm-0.9.0-py3-none-any.whl\n"
        )
        assert "vllm" in unpinned, (
            f"a direct reference is left readable only because this guard fails it "
            f"closed; got {unpinned}. Without it the vllm guards would skip."
        )

    @pytest.mark.parametrize(
        "line",
        [
            "example.zip ==1.0",
            "example.zip==1.0",
            "example.tar >=1",
            "example.tgz > 1",
            "example.zip (==1.0)",
            "example.tar (>=1)",
            "example.zip[foo] ==1.0",
        ],
    )
    def test_a_spaced_specifier_is_not_read_as_an_archive(self, line):
        assert _opaque_requirements(f"torch==2.7.0\n{line}\n") == [], (
            f"{line!r} is a dotted project name followed by a PEP 508 comparison, which "
            f"pip parses as a named requirement. Only the whitespace before the operator "
            f"separates it from the compact spelling, which is already accepted."
        )

    def test_a_detached_extras_list_before_a_comparison_is_not_an_archive(self):
        """``example.zip [foo] ==1.0`` is a named requirement with extras to pip.

        Measured at 376b5867, pip 26.1.2: ``install_req_from_line("example.zip [foo] ==1.0")``
        returns ``name='example-zip', extras=frozenset({'foo'}), specifier=SpecifierSet('==1.0')``.
        PEP 508 grammar allows ``wsp*`` between the name and the extras list, so the
        space before ``[foo]`` does not make the line an archive path.
        Review finding 2, 2026-09-22: ``_ATTACHED_EXTRAS`` required ``[`` to follow the
        suffix immediately; with a space before ``[foo]``, it matched empty and
        ``\\s`` matched that space — the comparison lookahead never fired.
        """
        # A detached extras list followed by a comparison is not an archive.
        assert _opaque_requirements("example.zip [foo] ==1.0\n") == [], (
            "example.zip [foo] ==1.0 is a named requirement to pip; detached extras "
            "before a comparison must not make the line opaque"
        )
        assert (
            _opaque_requirements("example.tar [foo] (>=1)\n") == []
        ), "parenthesised specifier after detached extras applies the same rule"
        # A detached extras list with no following comparison is still opaque.
        assert _opaque_requirements(
            "example.zip [foo]\n"
        ), "example.zip [foo] has no specifier; the archive suffix still makes it opaque"
        assert _opaque_requirements(
            "example.zip\n"
        ), "a bare archive with no extras is still opaque"

    @pytest.mark.parametrize(
        "extras",
        [
            "[foo bar]",
            "[foo,]",
            "[,foo]",
            "[-foo]",
            "[foo.]",
            "[foo-]",
            "[foo,bar.]",
            "[foo., bar]",
        ],
    )
    def test_a_malformed_detached_extras_list_stays_opaque(self, extras):
        """A detached extras list pip refuses must not buy the line an exemption.

        Measured at 5380d135, pip 26.1.2: ``install_req_from_line`` raises
        ``InvalidRequirement`` for each list below — ``[foo bar]`` wants a comma,
        ``[foo,]`` and ``[,foo]`` want an extra name, ``[-foo]`` wants an identifier —
        so the image build refuses the requirements file.

        Round 2 of 2026-09-22 (Codex adversarial pass on 56baa86d) added the trailing
        forms. ``[foo.]`` and ``[foo-]`` raise ``Expected matching RIGHT_BRACKET``:
        packaging's IDENTIFIER token is ``\b[a-zA-Z0-9][a-zA-Z0-9._-]*\b``, and the
        closing ``\b`` cannot sit between ``.`` and ``]`` — two non-word characters —
        so the token stops at ``foo`` and the bracket never closes.

        Review finding 3 of 2026-09-22 (Codex, comment 4069452153): the lookahead
        added for the detached spelling accepted any bracket content, so
        ``example.zip [foo bar] ==1.0`` read as a named requirement and the guard
        stopped reporting a line the parent revision reported. The lookahead now reads
        pip's extras grammar, so only a list pip accepts suppresses the report.
        """
        line = f"example.zip {extras} ==1.0"
        assert _opaque_requirements(line + "\n") == [line], (
            f"pip rejects {line!r}; a detached extras list it refuses must leave the "
            f"line opaque rather than exempt it"
        )

    @pytest.mark.parametrize(
        "extras",
        [
            "[foo]",
            "[foo,bar]",
            "[ foo , bar ]",
            "[]",
            "[ ]",
            "[foo.bar_1]",
            "[foo_]",
            "[foo..bar]",
            "[foo--bar]",
            "[a.b-c_d]",
        ],
    )
    def test_every_detached_extras_list_pip_accepts_keeps_its_exemption(self, extras):
        """Reading pip's grammar must not turn a valid extras list into a report.

        Measured at 5380d135, pip 26.1.2: every list below parses — PEP 508 allows
        whitespace inside the brackets and an empty list, and ``.`` ``-`` ``_`` inside
        an extra name. Narrowing the lookahead must keep all of them exempt.

        ``[foo_]`` is the case that decides the shape of the fix, and it is why the
        rule is NOT "an extra name ends on an alphanumeric character". Measured at
        56baa86d: pip ACCEPTS ``example.zip [foo_] ==1.0`` while it rejects
        ``[foo.]`` and ``[foo-]``, because ``_`` is a word character and packaging's
        IDENTIFIER token ends on ``\b``. A rule keyed on alphanumerics would report a
        line pip installs. ``[foo..bar]`` and ``[foo--bar]`` parse too: only the
        FIRST and LAST characters are constrained.
        """
        assert _opaque_requirements(f"example.zip {extras} ==1.0\n") == []

    @pytest.mark.parametrize(
        "line",
        [
            "example.zip [a\u00a0,b] ==1.0",
            "example.zip [\u00a0foo] ==1.0",
            "example.zip [foo\u00a0] ==1.0",
            "example.zip\u00a0[foo] ==1.0",
            "example.zip [foo]\u00a0==1.0",
        ],
    )
    def test_whitespace_pip_refuses_leaves_a_detached_extras_line_opaque(self, line):
        """Only a space or a tab separates the tokens of a requirement pip accepts.

        packaging's tokenizer reads whitespace with ``[ \\t]+`` (``WS`` in
        ``packaging._tokenizer``, measured on the copy pip 26.1.2 vendors), so a
        no-break space (U+00A0) between two tokens ends the parse. Measured at
        966411f4, pip 26.1.2: ``install_req_from_line`` raises for every line above,
        and the parent revision at 376b5867 reported all five as opaque.

        Review round 4 of 2026-09-22 (Codex, comment 4069945622): the detached-extras
        exemption spelled its whitespace ``\\s``, which matches every Unicode space, so
        each line bought an exemption pip itself refuses and the image build became
        the first reader to object.
        """
        assert _opaque_requirements(line + "\n") == [line], (
            f"pip rejects {line!r}; whitespace outside packaging's [ \\t] class must "
            f"leave the line opaque rather than exempt it"
        )

    @pytest.mark.parametrize(
        "line",
        [
            "example.zip\t[foo]\t==1.0",
            "example.zip [a\t,b] ==1.0",
            "example.zip\t[ foo , bar ]\t(==1.0)",
        ],
    )
    def test_a_tab_around_a_detached_extras_list_keeps_the_exemption(self, line):
        """A tab is in packaging's whitespace class, so narrowing must keep it.

        Measured at 966411f4, pip 26.1.2: each line above parses as the project
        ``example.zip`` with its extras and its specifier. Reading the whitespace as
        ``[ \\t]`` rather than ``\\s`` must not turn a tab-separated line pip installs
        into a report.
        """
        assert _opaque_requirements(line + "\n") == [], (
            f"pip accepts {line!r}; a tab is packaging's whitespace, so the detached "
            f"extras exemption must still apply"
        )

    @pytest.mark.parametrize("line", ["example.tbz2==1.0", "vllm-0.9.0.tbz2"])
    def test_a_suffix_pip_does_not_accept_is_not_read_as_an_archive(self, line):
        """``.tbz2`` is not in pip's ``ARCHIVE_EXTENSIONS``; ``.tbz`` is.

        Round 2 on 2026-09-19 refused the "a superset costs nothing" claim round 1
        made, and it is right: ``is_archive_file("x.tbz2")`` is False, so pip reads
        the line as an ordinary named requirement and only this guard calls it an
        archive. The suffix set is pip's set, or it is a guess.
        """
        assert _opaque_requirements(f"torch==2.7.0\n{line}\n") == [], (
            f"{line!r} carries a suffix pip does not recognise as an archive, so pip "
            f"reads it as a project name and the guard must too."
        )

    def test_the_supported_bzip2_suffix_is_still_reported(self):
        """Dropping ``.tbz2`` must not drop ``.tbz``, which pip DOES accept."""
        assert _opaque_requirements("torch==2.7.0\nvllm-0.9.0.tbz\n"), (
            "pip builds a file:// link for vllm-0.9.0.tbz from the extension alone; "
            "it must stay reported."
        )

    @pytest.mark.parametrize("line", ["file:foo", "file:vllm", "file:foo/bar"])
    def test_a_relative_file_uri_is_reported(self, line):
        """``file:foo`` is a local project link to pip, slashes or not.

        Round 3 on 2026-09-20: ``install_req_from_line("file:foo")`` returns
        ``name=None, link=file:///foo`` on pip 26.1.2, but the line neither starts
        with ``.`` nor carries a slash or an archive suffix, and the URL matcher wanted
        ``file://``. The guard recorded a project named ``file`` and every protected
        guard read the package inside as absent.
        """
        assert _opaque_requirements(f"torch==2.7.0\n{line}\n"), (
            f"{line!r} is a file: URI pip resolves to a local project; it must be "
            f"reported rather than read as a project named 'file'."
        )

    def test_a_named_file_uri_reference_keeps_its_name(self):
        """``numpy@file:foo`` carries a URL scheme, so D4 leaves it readable."""
        assert _opaque_requirements("torch==2.7.0\nnumpy@file:foo\n") == [], (
            "a direct reference whose target carries the file: scheme is a URL like "
            "any other; the exemption must not depend on the two slashes"
        )
        assert _opaque_requirements(
            "torch==2.7.0\nevil@../pkgs/vllm\n"
        ), "a target with no scheme is still a local path wearing a name"

    @pytest.mark.parametrize(
        "line",
        [
            "${VLLM_PATH}",
            "${VLLM_PATH}==1.0",
            "vllm==${VLLM_VERSION}",
            "vllm @ ${VLLM_URL}",
        ],
    )
    def test_an_environment_substitution_is_reported(self, line):
        """pip expands ``${NAME}`` before parsing; the guard cannot, so it fails closed.

        Round 3 on 2026-09-20: pip's ``ENV_VAR_RE`` is ``\\$\\{[A-Z0-9_]+\\}`` and
        ``expand_env_variables`` substitutes it from the build environment before the
        line is read. With ``VLLM_PATH=./foo`` a whole-line ``${VLLM_PATH}`` installs a
        local tree; this module saw the literal placeholder, matched nothing, and
        recorded no project at all.
        """
        assert _opaque_requirements(f"torch==2.7.0\n{line}\n"), (
            f"{line!r} depends on a build-time environment this module cannot see; it "
            f"must be reported, not silently recorded as nothing."
        )

    def test_a_substitution_inside_a_comment_is_not_a_requirement(self):
        assert (
            _opaque_requirements("torch==2.7.0\nvllm==0.9.0  # export ${VLLM_PATH}\n")
            == []
        ), "a placeholder after the comment marker is comment text"

    def test_a_literal_hash_in_an_archive_name_is_preserved(self):
        """pip's comment rule needs whitespace before ``#``; ``foo#vllm.tar.gz`` has none.

        Round 3 on 2026-09-20: pip's ``COMMENT_RE`` is ``(^|\\s+)#.*$``, so
        ``install_req_from_line("foo#vllm.tar.gz")`` returns an unnamed
        ``file:///…/foo%23vllm.tar.gz`` link. ``_requirement_lines`` cut the line at the
        first ``#`` and the suffix matcher saw only ``foo``.
        """
        assert _opaque_requirements("torch==2.7.0\nfoo#vllm.tar.gz\n"), (
            "pip keeps the '#' and reads an archive; the guard must match pip's "
            "comment rule and report the archive"
        )
        assert list(_requirement_lines("foo #comment\nbar\t# comment\n")) == [
            "foo",
            "bar",
        ], "a '#' preceded by whitespace is still a comment"
        assert _opaque_requirements("torch==2.7.0\nfoo #vllm.tar.gz\n") == []
        # The fragment of a VCS reference survives the comment rule and stays reported.
        assert _opaque_requirements(
            "torch==2.7.0\ngit+https://host/repo.git#egg=vllm\n"
        ), "the VCS class is unchanged by the comment rule"
        # A named direct reference with a fragment keeps its name (D4).
        assert (
            _opaque_requirements(
                "torch==2.7.0\nnumpy@https://host/numpy.whl#sha256=abc\n"
            )
            == []
        )

    def test_a_continued_line_is_joined_before_it_is_classified(self):
        """pip joins a line ending in ``\\`` with the next; this module must too.

        Round 5 on 2026-09-20: pip's standard hash-pinned spelling is
        ``numpy==2.0.0 \\`` followed by ``--hash=sha256:...``. The separator branch of
        ``_PATH_OR_ARCHIVE_REQUIREMENT`` read the trailing continuation backslash as a
        path separator and reported the line, so adding hashes to any monitored file
        turned the whole suite red. Reading the two physical lines separately was no
        better before that: ``_PIN_PATTERN`` is anchored, ``numpy==2.0.0 \\`` never
        matched it, and the pin went unrecorded — the silent skip this module exists to
        prevent, reached by a spelling pip calls ordinary.

        Joining is what pip does (``join_lines`` in ``pip._internal.req.req_file``), and
        it is the only reading that both refuses the false report and records the pin.
        """
        hashed = "torch==2.7.0\nnumpy==2.0.0 \\\n    --hash=sha256:abcdef\n"
        assert _opaque_requirements(hashed) == [], (
            "a hash-pinned requirement is a named pin to pip; the continuation "
            "backslash is not a path separator"
        )
        assert _parse_pins(hashed)["numpy"] == "2.0.0", (
            "the pin must be RECORDED, not merely un-reported: an unrecorded "
            "protected package reads as absent and every pairwise guard skips"
        )
        # The continuation still ends the requirement when nothing follows it.
        assert _parse_pins("numpy==2.0.0 \\\n")["numpy"] == "2.0.0"
        # A real local path is still reported when it arrives through a continuation.
        assert _opaque_requirements(
            "torch==2.7.0\nvllm @ \\\n    ../pkgs/vllm\n"
        ), "joining must not launder a local path into a readable requirement"

    def test_a_comment_line_ending_in_backslash_does_not_continue(self):
        """A whole-line comment never opens a continuation buffer, even when it ends in ``\\``.

        pip's rule (``COMMENT_RE``, same as ``_COMMENT``): ``COMMENT_RE.match(line)``
        prevents a line from opening a buffer regardless of the trailing ``\\``.
        Measured at 376b5867, pip 26.1.2: ``join_lines`` output for ``"# comment \\\\"``
        followed by ``"vllm-0.9.0-py3-none-any.whl"`` is two separate logical lines —
        ``[(1, ' # comment \\\\'), (2, 'vllm-0.9.0-py3-none-any.whl')]`` — not one
        joined line. Review finding 1, 2026-09-22.
        """
        # A whole-line comment ending in backslash must not hide the archive after it.
        assert _opaque_requirements("# comment \\\nvllm-0.9.0-py3-none-any.whl\n") == [
            "vllm-0.9.0-py3-none-any.whl"
        ]
        # Indented spelling (still matches COMMENT_RE.match via the \\s+ branch).
        assert _opaque_requirements(
            "   # comment \\\nvllm-0.9.0-py3-none-any.whl\n"
        ) == ["vllm-0.9.0-py3-none-any.whl"]
        # Bare-hash spelling.
        assert _opaque_requirements("#\\\nvllm-0.9.0-py3-none-any.whl\n") == [
            "vllm-0.9.0-py3-none-any.whl"
        ]
        # An INLINE comment ending in backslash still continues: the line
        # ``vllm==0.9.0  # note \\`` does not match COMMENT_RE.match (starts with 'v'),
        # so the buffer stays open and numpy joins it. Measured: pip yields one logical
        # line ``vllm==0.9.0  # note numpy==2.0.0``; _COMMENT.sub strips the tail.
        assert _parse_pins("vllm==0.9.0  # note \\\nnumpy==2.0.0\n")["vllm"] == "0.9.0"

    def test_whitespace_may_separate_a_name_from_its_extras(self):
        """PEP 508 allows ``wsp*`` between the name and the extras list.

        Round 5 on 2026-09-20: ``numpy [foo] @ https://host/numpy.whl`` is the project
        ``numpy[foo]`` to pip, but the direct-reference exemption required ``[`` to
        follow the name immediately, so the exemption missed and the slash branch
        reported a line whose project name is perfectly readable. The compact spelling
        ``numpy[foo] @ ...`` already passed, so whitespace alone decided the verdict —
        the same asymmetry round 1 fixed for the ``@`` itself.
        """
        assert (
            _opaque_requirements("torch==2.7.0\nnumpy [foo] @ https://host/numpy.whl\n")
            == []
        ), "whitespace before the extras list does not hide the project name"
        assert (
            _opaque_requirements("torch==2.7.0\nnumpy[foo] @ https://host/numpy.whl\n")
            == []
        ), "the compact spelling is unchanged"
        # The exemption still turns on a SCHEME, not on the brackets.
        assert _opaque_requirements(
            "torch==2.7.0\nevil [foo] @ ../pkgs/vllm\n"
        ), "a target with no scheme is a local path whatever the extras look like"

    def test_a_plain_pin_is_not_reported(self):
        assert _opaque_requirements("torch==2.7.0\nvllm==0.9.0\n") == []

    @pytest.mark.parametrize(
        "path",
        [GENERATED_CPU_REQUIREMENTS, GENERATED_GPU_REQUIREMENTS],
        ids=["cpu", "gpu"],
    )
    def test_the_generated_sets_carry_no_opaque_requirement(self, path):
        opaque = _opaque_requirements(path.read_text(encoding="utf-8"))
        assert opaque == [], (
            f"{path} declares {opaque}, whose project name this module cannot read. "
            f"Every compatibility guard would silently skip the package it supplies."
        )


class TestCompactOptionFormsAreRecognized:
    """pip accepts ``-rother.txt`` with no separator, and so must the matcher.

    Review on 2026-09-16: the pattern required whitespace, ``=`` or end-of-line after
    the short option, so the compact spelling matched nothing — while
    ``_requirement_lines`` still dropped the line for starting with ``-``, leaving the
    included file's pins invisible to every guard.
    """

    @pytest.mark.parametrize(
        "line",
        ["-rother.txt", "-cconstraints.txt", "-e./local/vllm", "-r other.txt"],
    )
    def test_a_compact_directive_is_reported(self, line):
        assert _requirement_bearing_directives(line) == [line]

    @pytest.mark.parametrize(
        "line",
        ["--extra-index-url https://example.invalid/simple", "--prefer-binary", "-v"],
    )
    def test_an_inert_option_is_not_reported(self, line):
        assert _requirement_bearing_directives(line) == []

    @pytest.mark.parametrize(
        "text",
        [
            "vllm==0.9.0 -Cfoo=bar",
            "vllm==0.9.0 -C foo=bar",
            "vllm==0.9.0 \\\n    -Cfoo=bar\n",
            "vllm==0.9.0 --config-settings=foo=bar",
        ],
    )
    def test_config_settings_short_option_is_cut_from_pin(self, text):
        """pip 26.1.2 SUPPORTED_OPTIONS_REQ carries exactly --hash and
        -C/--config-settings; measured, -C is its only short per-requirement option.

        Round 7 on 2026-09-22: _PER_REQUIREMENT_OPTION matched only long (``--``)
        options, so ``-Cfoo=bar`` and ``-C foo=bar`` survived into the line both
        readers matched. ``_parse_pins`` recorded nothing (the anchored _PIN_PATTERN
        failed at ``$``); ``_unpinned_protected`` reported vllm as unguarded.
        """
        assert _unpinned_protected(text) == {}
        assert _parse_pins(text) == {"vllm": "0.9.0"}

    def test_bare_trailing_config_settings_flag_is_not_cut(self):
        """A bare ``-C`` with no value character is not silently dropped.

        pip requires a value after ``-C``, so a naked ``-C`` at end of line is not a
        valid per-requirement option and must not be erased by the pattern.
        """
        assert list(_requirement_lines("vllm==0.9.0 -C")) == ["vllm==0.9.0 -C"]

    @pytest.mark.parametrize(
        "text",
        [
            "vllm==0.9.0 -Cfoo",
            "vllm==0.9.0 -C foo",
            "vllm==0.9.0 -C foo bar=baz",
            "vllm==0.9.0 --config-settings=foo",
            "vllm==0.9.0 --config-settings foo",
            "vllm==0.9.0 --config-settings=",
            'vllm==0.9.0 -C "foo bar"',
        ],
    )
    def test_a_config_settings_value_that_is_not_key_equals_val_fails_closed(
        self, text
    ):
        """pip rejects a config setting whose value carries no ``=``; so does the guard.

        Measured at 5380d135, pip 26.1.2: ``_handle_config_settings`` raises
        ``Arguments to -C must be of the form KEY=VAL`` for every line above, so the
        image build refuses the requirements file.

        Review finding 2 of 2026-09-22 (Codex, comment 4069452147): the pattern erased
        ``-C`` plus any single following character, so ``-Cfoo`` was cut away, an exact
        vllm pin was recorded and the guard reported nothing — the parent revision
        failed closed on the same line. The long spellings carried the same hole,
        which ``--\\S+`` had opened before this change. The value must contain ``=``
        before the option is cut.
        """
        assert _parse_pins(text) == {}
        assert "vllm" in _unpinned_protected(text)

    @pytest.mark.parametrize(
        "text",
        [
            "vllm==0.9.0 -C=",
            "vllm==0.9.0 -Cfoo=",
            "vllm==0.9.0 -C foo=bar=baz",
            "vllm==0.9.0 --config-settings foo=bar",
            'vllm==0.9.0 -C "foo bar=baz"',
            "vllm==0.9.0 -C 'foo bar=baz'",
            "vllm==0.9.0 -C foo\\ bar=baz",
            'vllm==0.9.0 --config-settings "foo bar=baz"',
        ],
    )
    def test_every_config_settings_value_pip_accepts_is_still_cut(self, text):
        """The KEY=VAL rule must not turn a valid spelling into a false report.

        Measured at 5380d135, pip 26.1.2: each line above parses, because the value
        after the flag contains an ``=`` (``-C=`` records the empty key). Narrowing the
        pattern must keep cutting all four, or the guard reports a pinned package as
        unpinned.
        """
        assert _parse_pins(text) == {"vllm": "0.9.0"}
        assert _unpinned_protected(text) == {}

    @pytest.mark.parametrize(
        "text",
        [
            "vllm==0.9.0\t-Cfoo=bar",
            "vllm==0.9.0\u00a0-Cfoo=bar",
            "vllm==0.9.0\t--hash=sha256:aa",
            "vllm==0.9.0\t--config-settings=foo=bar",
            "vllm==0.9.0 \t--hash=sha256:aa",
        ],
    )
    def test_an_option_not_preceded_by_a_space_fails_closed(self, text):
        """pip breaks a line into requirement and options on literal spaces only.

        ``break_args_options`` (``pip._internal.req.req_file``, pip 26.1.2) runs
        ``line.split(" ")`` and treats a token as an option only when that token
        starts with ``-``. A tab or a no-break space before ``-C`` therefore keeps the
        option inside the requirement, and ``install_req_from_line`` raises on every
        line above — the image build refuses the file.

        Review round 4 of 2026-09-22 (Codex, comment 4069945613): the pattern opened
        on ``\\s+``, so it cut the option away, ``_parse_pins`` recorded an exact pin
        from a line pip rejects and every pairwise guard read vllm as healthy. For the
        ``-C`` arm that is a regression against 376b5867, which matched no short
        option at all and failed closed.
        """
        assert _parse_pins(text) == {}
        assert "vllm" in _unpinned_protected(text)

    @pytest.mark.parametrize(
        "text",
        [
            "vllm==0.9.0\t -Cfoo=bar",
            "vllm==0.9.0\t --hash=sha256:aa",
            "vllm==0.9.0  --hash=sha256:aa",
        ],
    )
    def test_an_option_preceded_by_a_space_is_still_cut(self, text):
        """Whitespace before the space stays with the requirement, as pip leaves it.

        Measured at 966411f4, pip 26.1.2: each line above parses. ``break_args_options``
        splits on the space, so ``vllm==0.9.0\\t`` is the requirement — packaging skips
        the trailing tab — and the option is parsed separately. An empty token from a
        doubled space names no option either. Narrowing the delimiter to a space must
        keep cutting all three, or the guard reports a pinned package as unpinned.
        """
        assert _parse_pins(text) == {"vllm": "0.9.0"}
        assert _unpinned_protected(text) == {}
