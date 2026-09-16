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

# ``tar -xjf`` / ``tar -xjvf`` / ``tar --bzip2``: any spelling that forces bzip2.
_FORCED_BZIP2 = re.compile(r"tar\s+(?:-[a-zA-Z]*j[a-zA-Z]*\b|--bzip2\b)")
# A download URL that names no version, so its payload can change under us.
_MOVING_DOWNLOAD = re.compile(r"download\.mozilla\.org|[?&]product=[^\s\"']*latest")


def _templates():
    found = service_templates()
    assert found, "no service templates found; base_image_preflight may have moved"
    return sorted(found)


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_a_moving_download_is_not_extracted_with_a_forced_decompressor(template):
    """A versionless download must be extracted with format auto-detection."""
    content = template.read_text(encoding="utf-8")
    if not _MOVING_DOWNLOAD.search(content):
        pytest.skip(f"{template.name} fetches no versionless archive")

    forced = _FORCED_BZIP2.findall(content)
    assert not forced, (
        f"{template.name} extracts a versionless download with {forced}, which forces "
        f"bzip2. Mozilla's firefox-esr-latest endpoint serves xz, so bzip2 fails with "
        f"'tar: Child returned status 2' and the image cannot be built. Use `tar -xf` "
        f"and let tar detect the format. Three sibling templates already do."
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

    assert ".tar.bz2" not in content, (
        f"{template.name} saves a versionless download as .tar.bz2, but the endpoint "
        f"serves xz. Name it .tar.xz so the file and the extraction agree."
    )
