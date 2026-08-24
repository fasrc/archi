"""Unit tests for `scripts/dev/update_service_base_images.py` (openspec change
`fix-issue-334-digest-pinned-base-refs`).

The script rewrites the `FROM` line of every service template under
`DOCKERFILES_DIR`. Issue #333 pins those templates to `@sha256:` digest
references, so the script has to read a digest, write a digest, and keep the
trailing `# <tag>` annotation honest.

`scripts/gate.sh` measures coverage with `--cov=src`, so nothing under
`scripts/` reports to diff-cover. These tests are the only evidence the script
works. Every scenario in the spec delta appears here as a named test.

The tests never touch the real templates: each one writes fixture Dockerfiles
into `tmp_path` and repoints the module-level `DOCKERFILES_DIR` at it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "dev"
    / "update_service_base_images.py"
)

# Real digest of the pinned python base, from issue #334.
_PY_DIGEST = "sha256:c068f17b8cba96682e7007c9dd5511f43fea86c796f3cbeee44e2766c5a9b8e8"


def _load_script():
    """Import the script by path — `scripts/dev` is not a package.

    The module is registered in `sys.modules` before it executes because the
    script defines a `@dataclass`, and `dataclasses` resolves a field's type by
    looking the defining module up there.
    """
    name = "update_service_base_images"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_fixtures(tmp_path, monkeypatch, **dockerfiles):
    """Write named fixture Dockerfiles and point the script at them.

    `tmp_path` stands in for the project root and holds a `dockerfiles`
    directory, mirroring the real layout, so the script's `Updated <path>`
    line resolves the same way it does in the repository.
    """
    module = _load_script()
    templates = tmp_path / "dockerfiles"
    templates.mkdir()
    for name, text in dockerfiles.items():
        (templates / name).write_text(text)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "DOCKERFILES_DIR", templates)
    return module, templates


def _run(module, monkeypatch, argv):
    """Drive the script's CLI with `argv`, as CI does."""
    monkeypatch.setattr(sys, "argv", ["update_service_base_images.py", *argv])
    module.main()


def test_digest_pinned_line_rewrites_to_a_tag(tmp_path, monkeypatch):
    """Spec: a digest-pinned reference is rewritten to a tag.

    This is the CI path at `.github/workflows/pr-preview.yml:274`. Before the
    fix the digest reference parsed as the image `...-base@sha256`, missed the
    `image != base_name` guard, and the line was silently left alone.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
                "RUN echo build\n"
            )
        },
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        ["--tag", "pr-7", "--switch-source", "ghcr", "--orig-tag", "all"],
    )

    assert target.read_text() == (
        "FROM ghcr.io/fasrc/a2rchi-python-base:pr-7\nRUN echo build\n"
    )
    assert _PY_DIGEST not in target.read_text()


def test_specific_orig_tag_leaves_a_digest_pinned_line_alone(
    tmp_path, monkeypatch, capsys
):
    """Spec: a specific --orig-tag leaves a digest-pinned line alone.

    A digest reference carries no tag, so no literal `--orig-tag` value can
    match it. Only `--orig-tag all` reaches it.
    """
    original = f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\nRUN echo build\n"
    module, templates = _write_fixtures(
        tmp_path, monkeypatch, **{"Dockerfile-chat": original}
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        ["--tag", "pr-7", "--switch-source", "ghcr", "--orig-tag", "dev-4314ac4"],
    )

    assert target.read_text() == original
    assert "Updated" not in capsys.readouterr().out


def test_orig_tag_all_still_matches_a_tag_pinned_line(tmp_path, monkeypatch):
    """Guard: teaching the parser about digests did not break the tag path."""
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{"Dockerfile-chat": "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"},
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        ["--tag", "pr-7", "--switch-source", "ghcr", "--orig-tag", "all"],
    )

    assert target.read_text() == "FROM ghcr.io/fasrc/a2rchi-python-base:pr-7\n"


def test_stale_annotation_is_dropped_on_the_way_back_to_a_tag(tmp_path, monkeypatch):
    """Spec: a digest annotation never outlives the digest it names.

    The `# <tag>` comment says in words which build the digest is. Once the
    digest is gone the comment names a build the line no longer references.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}  # dev-4314ac4\n"
            )
        },
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        ["--tag", "pr-7", "--switch-source", "ghcr", "--orig-tag", "all"],
    )

    assert target.read_text() == "FROM ghcr.io/fasrc/a2rchi-python-base:pr-7\n"
    assert "dev-4314ac4" not in target.read_text()


def test_other_trailing_content_survives_the_rewrite(tmp_path, monkeypatch):
    """Spec: other trailing content survives the rewrite.

    Only the comment is an annotation. A build stage name is not, and the
    comment split must not be greedy enough to swallow it.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}"
                " AS builder  # dev-4314ac4\n"
            )
        },
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        ["--tag", "pr-7", "--switch-source", "ghcr", "--orig-tag", "all"],
    )

    assert target.read_text() == (
        "FROM ghcr.io/fasrc/a2rchi-python-base:pr-7 AS builder\n"
    )


def test_a_comment_on_a_tag_line_is_left_alone(tmp_path, monkeypatch):
    """Design: a tag-to-tag rewrite passes the trailing text through.

    An annotation is only ever written beside a digest, so a comment on a
    tag-pinned line belongs to somebody else and is not this script's to
    delete.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4  # hand-written\n"
            )
        },
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        ["--tag", "pr-7", "--switch-source", "ghcr", "--orig-tag", "all"],
    )

    assert target.read_text() == (
        "FROM ghcr.io/fasrc/a2rchi-python-base:pr-7  # hand-written\n"
    )
