"""Unit tests for `scripts/dev/update_service_base_images.py` (openspec change
`fix-issue-334-digest-pinned-base-refs`).

The script rewrites the `FROM` line of every service template under
`DOCKERFILES_DIR`. Issue #333 pins those templates to `@sha256:` digest
references, so the script has to read a digest, write a digest, and keep the
`# base-image-pin:` annotation above it honest.

The annotation sits on its own line because a Dockerfile recognises `#` as a
comment only at the start of a line. A trailing `# tag` on a `FROM` line
becomes a second `FROM` argument, and both docker and podman reject the file
with "FROM requires either one or three arguments".

`scripts/gate.sh` measures coverage with `--cov=src`, so nothing under
`scripts/` reports to diff-cover. These tests are the only evidence the script
works. Every scenario in the spec delta appears here as a named test.

The tests never touch the real templates: each one writes fixture Dockerfiles
into `tmp_path` and repoints the module-level `DOCKERFILES_DIR` at it.
"""

from __future__ import annotations

import importlib.util
import re
import shlex
import sys
from pathlib import Path

import pytest
import yaml

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "dev"
    / "update_service_base_images.py"
)

# Real digest of the pinned python base, from issue #334.
_PY_DIGEST = "sha256:c068f17b8cba96682e7007c9dd5511f43fea86c796f3cbeee44e2766c5a9b8e8"
# A second well-formed digest, for "pin to a different build" cases.
_NEW_DIGEST = "sha256:" + "a1" * 32

# The managed annotation wording, from the script's own constants.
_ANN = "# base-image-pin: "
_ANN_END = " (managed by update_service_base_images.py)"


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
    templates.mkdir(parents=True)
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
                f"{_ANN}dev-4314ac4{_ANN_END}\nFROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
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

    A build stage name sits after the reference and belongs to the template.
    The script writes nothing onto the FROM line but the reference itself, so
    everything after it comes through untouched.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                f"{_ANN}dev-4314ac4{_ANN_END}\n"
                f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST} AS builder\n"
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


def test_a_comment_the_script_did_not_write_is_left_alone(tmp_path, monkeypatch):
    """The script removes only the annotation wording it writes itself.

    A template's own comment can sit directly above a FROM line. Removing it
    because of its position would delete somebody else's words.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                "# hand-written note about this service\n"
                f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
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
        "# hand-written note about this service\n"
        "FROM ghcr.io/fasrc/a2rchi-python-base:pr-7\n"
    )


def test_a_digest_is_written_with_the_tag_beside_it(tmp_path, monkeypatch):
    """Spec: a digest is written with the tag beside it.

    This is the maintenance path for the pin issue #333 puts in the templates.
    Without it an operator moves that pin by hand across 15 files.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{"Dockerfile-chat": "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"},
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        [
            "--digest",
            f"python={_NEW_DIGEST}",
            "--tag",
            "dev-abc1234",
            "--switch-source",
            "ghcr",
            "--orig-tag",
            "all",
        ],
    )

    assert target.read_text() == (
        f"{_ANN}dev-abc1234{_ANN_END}\nFROM ghcr.io/fasrc/a2rchi-python-base@{_NEW_DIGEST}\n"
    )
    assert ":dev-abc1234" not in target.read_text()


def test_a_digest_without_a_tag_is_refused(tmp_path, monkeypatch):
    """A digest names no build, so `--digest` needs `--tag` to name one.

    Without it the script would write a pin that records nothing about which
    build it is — and `test_service_templates_pin_one_explicit_base_tag`
    rejects exactly that, so the command would leave the repository failing
    CI. Refusing at the command says so where the operator can act on it.
    """
    original = "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"
    module, templates = _write_fixtures(
        tmp_path, monkeypatch, **{"Dockerfile-chat": original}
    )

    with pytest.raises(SystemExit) as excinfo:
        _run(
            module,
            monkeypatch,
            ["--digest", f"python={_PY_DIGEST}", "--orig-tag", "all"],
        )

    message = str(excinfo.value)
    assert "--tag" in message
    assert (templates / "Dockerfile-chat").read_text() == original


def test_an_empty_tag_is_refused(tmp_path, monkeypatch):
    """`--tag ""` is a shell variable that did not expand, not a tag.

    Both call sites pass `--tag "${{ ... }}"` from a workflow output, so an
    empty value is one unset output away. Accepting it unpins the base: with no
    `--digest`, `_build_image_spec` sees a falsy tag and writes a bare
    `FROM <repo>` that resolves to `latest` at build time. With `--digest` it
    writes an annotation naming no build, which the script no longer recognises
    as its own and the repository guard rejects.
    """
    original = "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"

    # The bare-tag path, which is `pr-preview.yml:274`'s exact shape.
    module, templates = _write_fixtures(
        tmp_path / "tag", monkeypatch, **{"Dockerfile-chat": original}
    )
    with pytest.raises(SystemExit) as excinfo:
        _run(
            module,
            monkeypatch,
            ["--tag", "", "--switch-source", "ghcr", "--orig-tag", "all"],
        )
    assert "--tag" in str(excinfo.value)
    assert (templates / "Dockerfile-chat").read_text() == original

    # And the digest path, where whitespace is just as empty.
    module, templates = _write_fixtures(
        tmp_path / "digest", monkeypatch, **{"Dockerfile-chat": original}
    )
    with pytest.raises(SystemExit) as excinfo:
        _run(
            module,
            monkeypatch,
            ["--digest", f"python={_PY_DIGEST}", "--tag", "   ", "--orig-tag", "all"],
        )
    assert "--tag" in str(excinfo.value)
    assert (templates / "Dockerfile-chat").read_text() == original


def test_a_tag_that_is_not_a_tag_is_refused(tmp_path, monkeypatch):
    """A value that is not a valid Docker tag breaks both output forms.

    Docker tags match `[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}`, so a space is not a
    tag. Writing one produces `FROM <repo>:dev bad` — two `FROM` arguments and
    the same parse error as an inline comment — and on the digest path an
    annotation the script's own pattern no longer matches, which orphans it.
    """
    original = "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"

    # A leading "-" is not in this list: argparse claims it as an option name
    # and rejects it before this validation ever sees it.
    for index, bad in enumerate(["dev bad", "dev-4314ac4 ", ".dot-start", "a" * 200]):
        module, templates = _write_fixtures(
            tmp_path / str(index), monkeypatch, **{"Dockerfile-chat": original}
        )
        with pytest.raises(SystemExit) as excinfo:
            _run(
                module,
                monkeypatch,
                ["--tag", bad, "--switch-source", "ghcr", "--orig-tag", "all"],
            )
        assert "--tag" in str(excinfo.value), bad
        assert (templates / "Dockerfile-chat").read_text() == original, bad


def test_the_tags_the_call_sites_use_are_accepted(tmp_path, monkeypatch):
    """The validation must not reject what CI and releases actually pass."""
    for index, good in enumerate(["pr-7", "dev-4314ac4", "v2026.8.0", "latest", "a"]):
        module, templates = _write_fixtures(
            tmp_path / str(index),
            monkeypatch,
            **{"Dockerfile-chat": "FROM ghcr.io/fasrc/a2rchi-python-base:old\n"},
        )
        _run(
            module,
            monkeypatch,
            ["--tag", good, "--switch-source", "ghcr", "--orig-tag", "all"],
        )
        assert (templates / "Dockerfile-chat").read_text() == (
            f"FROM ghcr.io/fasrc/a2rchi-python-base:{good}\n"
        ), good


def test_a_base_image_with_no_digest_keeps_its_tag(tmp_path, monkeypatch):
    """Spec: a base image with no `--digest` keeps its tag.

    The pytorch line here is named by `--tag` but by no `--digest`, so it moves
    to the new tag and gains neither a digest nor an annotation.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n",
            "Dockerfile-gpu": "FROM ghcr.io/fasrc/a2rchi-pytorch-base:dev-4314ac4\n",
        },
    )

    _run(
        module,
        monkeypatch,
        [
            "--digest",
            f"python={_NEW_DIGEST}",
            "--tag",
            "dev-abc1234",
            "--orig-tag",
            "all",
        ],
    )

    assert (templates / "Dockerfile-chat").read_text() == (
        f"{_ANN}dev-abc1234{_ANN_END}\n"
        f"FROM ghcr.io/fasrc/a2rchi-python-base@{_NEW_DIGEST}\n"
    )

    pytorch = (templates / "Dockerfile-gpu").read_text()
    assert pytorch == "FROM ghcr.io/fasrc/a2rchi-pytorch-base:dev-abc1234\n"
    assert "@sha256:" not in pytorch
    assert "base-image-pin" not in pytorch


def test_a_comment_only_rewrite_is_still_written(tmp_path, monkeypatch, capsys):
    """Spec: a line whose only change is its comment is still written.

    Re-pinning the same digest under a new tag changes nothing but the
    annotation. Comparing only the image reference called that "unchanged" and
    dropped it, leaving the comment naming the build before last.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                f"{_ANN}dev-4314ac4{_ANN_END}\nFROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
            )
        },
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        [
            "--digest",
            f"python={_PY_DIGEST}",
            "--tag",
            "dev-abc1234",
            "--switch-source",
            "ghcr",
            "--orig-tag",
            "all",
        ],
    )

    assert target.read_text() == (
        f"{_ANN}dev-abc1234{_ANN_END}\nFROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
    )
    assert "Updated dockerfiles/Dockerfile-chat" in capsys.readouterr().out


def test_an_unknown_digest_name_is_refused(tmp_path, monkeypatch):
    """Spec: an unknown --digest name is refused.

    The error names the valid keys, because the alternative is a reference no
    runtime can pull, discovered at build time in CI far from the command that
    wrote it.
    """
    original = "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"
    module, templates = _write_fixtures(
        tmp_path, monkeypatch, **{"Dockerfile-chat": original}
    )

    with pytest.raises(SystemExit) as excinfo:
        _run(
            module,
            monkeypatch,
            ["--digest", f"java={_NEW_DIGEST}", "--orig-tag", "all"],
        )

    message = str(excinfo.value)
    assert "java" in message
    assert "python" in message and "pytorch" in message
    assert (templates / "Dockerfile-chat").read_text() == original


def test_a_malformed_digest_is_refused(tmp_path, monkeypatch):
    """Spec: a malformed digest is refused."""
    original = "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"
    module, templates = _write_fixtures(
        tmp_path, monkeypatch, **{"Dockerfile-chat": original}
    )

    with pytest.raises(SystemExit) as excinfo:
        _run(module, monkeypatch, ["--digest", "python=deadbeef", "--orig-tag", "all"])

    assert "deadbeef" in str(excinfo.value)
    assert (templates / "Dockerfile-chat").read_text() == original


def test_tag_digest_and_back_again_returns_the_original_line(tmp_path, monkeypatch):
    """Spec: tag, digest, and back again returns the original line.

    The reader and the writer have to agree. A round trip that drifted by a
    stray comment or a lost prefix would leave the templates a little
    different after every pin, which is how a diff stops being reviewable.
    """
    original = "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"
    module, templates = _write_fixtures(
        tmp_path, monkeypatch, **{"Dockerfile-chat": original}
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        [
            "--digest",
            f"python={_PY_DIGEST}",
            "--tag",
            "dev-4314ac4",
            "--switch-source",
            "ghcr",
            "--orig-tag",
            "all",
        ],
    )
    assert target.read_text() == (
        f"{_ANN}dev-4314ac4{_ANN_END}\nFROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
    )

    _run(
        module,
        monkeypatch,
        ["--tag", "dev-4314ac4", "--switch-source", "ghcr", "--orig-tag", "all"],
    )

    assert target.read_text() == original


def test_pinning_a_line_that_ends_in_a_space_is_stable(tmp_path, monkeypatch):
    """13 of the 15 real templates end their FROM line with a stray space.

    The annotation goes on its own line, so nothing is ever appended to the
    FROM line and the stray space is not this script's to touch. It survives
    every rewrite, and pinning twice changes nothing.
    """
    # The exact shape at src/cli/templates/dockerfiles/Dockerfile-chat:2.
    original = "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4 \n"
    module, templates = _write_fixtures(
        tmp_path, monkeypatch, **{"Dockerfile-chat": original}
    )
    target = templates / "Dockerfile-chat"

    pin = [
        "--digest",
        f"python={_PY_DIGEST}",
        "--tag",
        "dev-4314ac4",
        "--switch-source",
        "ghcr",
        "--orig-tag",
        "all",
    ]
    _run(module, monkeypatch, pin)

    pinned = (
        f"{_ANN}dev-4314ac4{_ANN_END}\n"
        f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST} \n"
    )
    assert target.read_text() == pinned

    # Pinning again changes nothing — no annotation line accumulates.
    _run(module, monkeypatch, pin)
    assert target.read_text() == pinned

    # And the CI rewrite gets a clean tag line with no stale annotation.
    _run(
        module,
        monkeypatch,
        ["--tag", "pr-7", "--switch-source", "ghcr", "--orig-tag", "all"],
    )
    assert target.read_text() == "FROM ghcr.io/fasrc/a2rchi-python-base:pr-7 \n"


def test_the_default_orig_tag_only_reaches_a_latest_pinned_line(tmp_path, monkeypatch):
    """`--orig-tag` defaults to `latest`, and only a `latest` line matches it.

    This pins the **script's** default, which issue #339 deliberately left
    alone. That default is a trap for any caller that omits `--orig-tag`: the
    templates carry `dev-4314ac4`, not `latest`, since `5e168b00`, and a digest
    pin carries no tag at all, so such a caller silently matches nothing. The
    release workflow was that caller until #339 gave it `--orig-tag all`.

    The fix stayed at the call site. Changing this default to suit one caller
    repairs that caller and hides the next one, and both current call sites
    pass `--orig-tag all` explicitly. See
    `test_the_release_workflow_argv_rewrites_the_pin_the_templates_carry`,
    which reads the release argv out of the workflow file.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-latest": "FROM ghcr.io/fasrc/a2rchi-python-base:latest\n",
            "Dockerfile-pinned": "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n",
            "Dockerfile-digest": (
                f"{_ANN}dev-4314ac4{_ANN_END}\nFROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
            ),
        },
    )

    # The exact argv from the release workflow: no --orig-tag.
    _run(module, monkeypatch, ["--tag", "v2026.8.0", "--switch-source", "ghcr"])

    assert (templates / "Dockerfile-latest").read_text() == (
        "FROM ghcr.io/fasrc/a2rchi-python-base:v2026.8.0\n"
    )
    assert (templates / "Dockerfile-pinned").read_text() == (
        "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"
    )
    assert (templates / "Dockerfile-digest").read_text() == (
        f"{_ANN}dev-4314ac4{_ANN_END}\nFROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
    )


def test_a_digest_for_a_base_excluded_by_bases_is_refused(tmp_path, monkeypatch):
    """Spec: a digest for a base the run excludes is refused.

    `update_base_tags` only walks the bases in `--bases`, so a digest for any
    other base can never be applied. Accepting it would exit zero having
    written nothing — the same silent partial failure this change exists to
    remove.
    """
    original = "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"
    module, templates = _write_fixtures(
        tmp_path, monkeypatch, **{"Dockerfile-chat": original}
    )

    with pytest.raises(SystemExit) as excinfo:
        _run(
            module,
            monkeypatch,
            [
                "--digest",
                f"python={_PY_DIGEST}",
                "--bases",
                "pytorch",
                "--orig-tag",
                "all",
            ],
        )

    message = str(excinfo.value)
    assert "python" in message
    assert "--bases" in message
    assert (templates / "Dockerfile-chat").read_text() == original


def test_a_source_switch_alone_never_unpins_a_digest(tmp_path, monkeypatch):
    """A rewrite that names no new reference must not remove the old one.

    With no `--tag` and no `--digest` there is nothing to put in the
    reference's place. Building one from the prefix and image alone yields a
    bare `FROM ghcr.io/fasrc/a2rchi-python-base`, which resolves to `latest` at
    build time — an unpinned base, reported as a successful update.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                f"{_ANN}dev-4314ac4{_ANN_END}\nFROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
            )
        },
    )
    target = templates / "Dockerfile-chat"

    # Move the registry only. The pin, and the annotation naming it, survive.
    _run(module, monkeypatch, ["--switch-source", "dockerhub", "--orig-tag", "all"])

    assert target.read_text() == (
        f"{_ANN}dev-4314ac4{_ANN_END}\nFROM docker.io/a2rchi/a2rchi-python-base@{_PY_DIGEST}\n"
    )


def test_a_no_op_run_on_a_digest_line_changes_nothing(tmp_path, monkeypatch):
    """The same guard, with nothing to change at all: the line is untouched."""
    original = f"{_ANN}dev-4314ac4{_ANN_END}\nFROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
    module, templates = _write_fixtures(
        tmp_path, monkeypatch, **{"Dockerfile-chat": original}
    )
    target = templates / "Dockerfile-chat"

    _run(module, monkeypatch, ["--switch-source", "ghcr", "--orig-tag", "all"])

    assert target.read_text() == original


def test_re_pinning_the_same_digest_and_tag_changes_nothing(tmp_path, monkeypatch):
    """Running the documented pin command twice is a no-op.

    Nothing moves, so nothing is written and no file is reported. An operator
    re-running the command after a partial failure should not see the tree
    churn.
    """
    original = (
        f"{_ANN}dev-4314ac4{_ANN_END}\n"
        f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
    )
    module, templates = _write_fixtures(
        tmp_path, monkeypatch, **{"Dockerfile-chat": original}
    )
    target = templates / "Dockerfile-chat"

    pin = [
        "--digest",
        f"python={_PY_DIGEST}",
        "--tag",
        "dev-4314ac4",
        "--switch-source",
        "ghcr",
        "--orig-tag",
        "all",
    ]
    _run(module, monkeypatch, pin)
    assert target.read_text() == original

    _run(module, monkeypatch, pin)
    assert target.read_text() == original


def test_moving_the_digest_replaces_the_annotation(tmp_path, monkeypatch):
    """A moved digest takes the old annotation with it.

    The old annotation named the build the previous digest was. Leaving it
    would put two disagreeing answers in the file; the new `--tag` names the
    build the new digest is.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                f"{_ANN}dev-4314ac4{_ANN_END}\n"
                f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
            )
        },
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        [
            "--digest",
            f"python={_NEW_DIGEST}",
            "--tag",
            "dev-abc1234",
            "--orig-tag",
            "all",
        ],
    )

    assert target.read_text() == (
        f"{_ANN}dev-abc1234{_ANN_END}\n"
        f"FROM ghcr.io/fasrc/a2rchi-python-base@{_NEW_DIGEST}\n"
    )
    assert "dev-4314ac4" not in target.read_text()


def test_a_tag_and_digest_reference_is_read_as_digest_pinned(tmp_path, monkeypatch):
    """`<image>:<tag>@sha256:<hex>` is a valid reference and must not be skipped.

    Docker accepts a tag and a digest on one reference; the digest decides
    which image is pulled and the tag beside it is informational. Read naively
    the tag stays glued to the image name, the name guard rejects it, and the
    line is passed over in silence — the same failure this change exists to
    end. The tag is dropped rather than carried, because a tag and a digest are
    alternatives everywhere else in this script.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                f"FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4@{_PY_DIGEST}\n"
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


def test_a_tag_and_digest_reference_has_no_tag_to_match(tmp_path, monkeypatch):
    """The other half: it is digest-pinned, so a literal --orig-tag misses it.

    The tag is present in the text but is not the reference's tag — the digest
    is what the line resolves to. Letting `--orig-tag dev-4314ac4` match here
    would contradict the rule that a digest-pinned line carries no tag.
    """
    original = f"FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4@{_PY_DIGEST}\n"
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


def test_no_rewritten_from_line_ever_carries_an_inline_comment(tmp_path, monkeypatch):
    """A Dockerfile has no inline comments, so a FROM line must never gain one.

    `#` opens a comment only at the start of a line. A trailing `# tag` becomes
    a second `FROM` argument, and docker and podman both refuse the file with
    "FROM requires either one or three arguments" — every service build fails
    before an image is pulled. This walks the option combinations that write an
    annotation and asserts none of them puts a `#` on a `FROM` line.
    """
    starts = {
        "Dockerfile-tag": "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n",
        "Dockerfile-space": "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4 \n",
        "Dockerfile-stage": (
            "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4 AS builder\n"
        ),
        "Dockerfile-digest": (
            f"{_ANN}dev-4314ac4{_ANN_END}\n"
            f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
        ),
    }
    runs = [
        ["--digest", f"python={_PY_DIGEST}", "--tag", "dev-abc", "--orig-tag", "all"],
        ["--digest", f"python={_NEW_DIGEST}", "--tag", "dev-xyz", "--orig-tag", "all"],
        ["--tag", "pr-7", "--switch-source", "ghcr", "--orig-tag", "all"],
        ["--switch-source", "dockerhub", "--orig-tag", "all"],
    ]

    for index, argv in enumerate(runs):
        module, templates = _write_fixtures(
            tmp_path / str(index), monkeypatch, **starts
        )
        _run(module, monkeypatch, argv)

        for path in sorted(templates.glob("Dockerfile*")):
            for line in path.read_text().splitlines():
                if line.lstrip().startswith("FROM "):
                    assert "#" not in line, f"{argv} produced {line!r}"


def test_a_blank_line_between_annotation_and_from_does_not_orphan_it(
    tmp_path, monkeypatch
):
    """The annotation belongs to the next FROM line, not to the gap above it.

    A blank line between the two is the template's, but it must not hide the
    annotation from the rewrite. Missing it leaves a comment naming a build the
    file no longer references.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                f"{_ANN}dev-4314ac4{_ANN_END}\n"
                "\n"
                f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
            )
        },
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        ["--tag", "pr-7", "--switch-source", "ghcr", "--orig-tag", "all"],
    )

    assert target.read_text() == "\nFROM ghcr.io/fasrc/a2rchi-python-base:pr-7\n"
    assert "dev-4314ac4" not in target.read_text()


def test_a_blank_line_does_not_produce_two_annotations(tmp_path, monkeypatch):
    """The same gap, re-pinned: one annotation, directly above its FROM."""
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                f"{_ANN}dev-4314ac4{_ANN_END}\n"
                "\n"
                f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
            )
        },
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        [
            "--digest",
            f"python={_NEW_DIGEST}",
            "--tag",
            "dev-abc1234",
            "--orig-tag",
            "all",
        ],
    )

    assert target.read_text() == (
        "\n"
        f"{_ANN}dev-abc1234{_ANN_END}\n"
        f"FROM ghcr.io/fasrc/a2rchi-python-base@{_NEW_DIGEST}\n"
    )
    assert target.read_text().count("base-image-pin") == 1


def test_a_hand_written_comment_of_similar_shape_survives(tmp_path, monkeypatch):
    """Ownership is the full managed wording, not a loose prefix.

    A template may carry its own note above a FROM line. Deleting it because it
    looked roughly like an annotation would be data loss in the template.
    """
    note = "# base image: DO-NOT-EDIT\n"
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                note + f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
            )
        },
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        ["--tag", "pr-7", "--switch-source", "ghcr", "--orig-tag", "all"],
    )

    assert target.read_text() == (note + "FROM ghcr.io/fasrc/a2rchi-python-base:pr-7\n")


def test_a_final_from_line_with_no_newline_still_gets_a_separator(
    tmp_path, monkeypatch
):
    """A file can end without a trailing newline on its FROM line.

    Reusing that empty line ending for the annotation glues the two together as
    `# ...tagFROM ...`, which comments the base instruction out and leaves a
    Dockerfile that cannot build.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        # Deliberately no trailing newline.
        **{"Dockerfile-chat": "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4"},
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        [
            "--digest",
            f"python={_PY_DIGEST}",
            "--tag",
            "dev-abc1234",
            "--orig-tag",
            "all",
        ],
    )

    assert target.read_text() == (
        f"{_ANN}dev-abc1234{_ANN_END}\n"
        f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}"
    )


def test_a_crlf_file_is_rewritten_whole_with_lf_endings(tmp_path, monkeypatch):
    """A rewritten CRLF file comes out entirely LF, annotation included.

    `update_base_tags` reads with `Path.read_text()`, whose universal newlines
    turn every `\\r\\n` into `\\n` before any line is examined. The conversion
    is therefore whole-file and predates this change; what matters here is that
    the result is never *mixed*, which would be a trap for tooling expecting
    one convention. The templates are all LF, so no file is affected today.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                "RUN echo build\r\nFROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\r\n"
            )
        },
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        [
            "--digest",
            f"python={_PY_DIGEST}",
            "--tag",
            "dev-abc1234",
            "--orig-tag",
            "all",
        ],
    )

    raw = target.read_bytes()
    assert b"\r" not in raw
    assert raw.decode() == (
        "RUN echo build\n"
        f"{_ANN}dev-abc1234{_ANN_END}\n"
        f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
    )


def test_an_annotation_never_survives_above_a_tag_reference(tmp_path, monkeypatch):
    """An annotation names a digest, so a tag line must never carry one.

    The source here is already a tag, so the digest-to-tag rule never fires.
    The annotation still has to go: it labels a reference that has no digest
    for it to name.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                f"{_ANN}dev-4314ac4{_ANN_END}\n"
                "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"
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


def test_two_from_lines_each_get_their_own_annotation(tmp_path, monkeypatch):
    """A multi-stage template has more than one managed FROM line."""
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": (
                "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4 AS builder\n"
                "RUN echo build\n"
                "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"
            )
        },
    )
    target = templates / "Dockerfile-chat"

    _run(
        module,
        monkeypatch,
        [
            "--digest",
            f"python={_PY_DIGEST}",
            "--tag",
            "dev-abc1234",
            "--orig-tag",
            "all",
        ],
    )

    assert target.read_text() == (
        f"{_ANN}dev-abc1234{_ANN_END}\n"
        f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST} AS builder\n"
        "RUN echo build\n"
        f"{_ANN}dev-abc1234{_ANN_END}\n"
        f"FROM ghcr.io/fasrc/a2rchi-python-base@{_PY_DIGEST}\n"
    )


# --- Verification ------------------------------------------------------------
#
# `--verify` is the proof that a rewrite happened. It reads the reference on
# the line rather than whether the line moved, because those two tests disagree
# on exactly one input: a template that already carries the target reference,
# which is what a re-dispatch of the same release tag produces.


def test_verify_passes_when_every_base_reference_names_the_target(
    tmp_path, monkeypatch, capsys
):
    """A tree already on the target reference verifies, and is left alone."""
    python_ref = "FROM ghcr.io/fasrc/a2rchi-python-base:v2026.8.0\n"
    pytorch_ref = "FROM ghcr.io/fasrc/a2rchi-pytorch-base:v2026.8.0 \n"
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{"Dockerfile-chat": python_ref, "Dockerfile-chat-gpu": pytorch_ref},
    )

    _run(
        module,
        monkeypatch,
        ["--verify", "--tag", "v2026.8.0", "--switch-source", "ghcr"],
    )

    assert (templates / "Dockerfile-chat").read_text() == python_ref
    assert (templates / "Dockerfile-chat-gpu").read_text() == pytorch_ref
    assert "v2026.8.0" in capsys.readouterr().out


def test_verify_names_every_template_left_on_the_wrong_reference(tmp_path, monkeypatch):
    """Both halves of a reference are checked, and both name their file.

    A wrong tag is the failure this change exists to catch. A wrong prefix is
    the other half: `localhost/a2rchi/...` at the right tag is an image the
    release runner never pulled, and a check that reads only the tag passes it.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n",
            "Dockerfile-chat-gpu": (
                "FROM localhost/a2rchi/a2rchi-pytorch-base:v2026.8.0\n"
            ),
            "Dockerfile-mailbox": "FROM ghcr.io/fasrc/a2rchi-python-base:v2026.8.0\n",
        },
    )

    with pytest.raises(SystemExit) as excinfo:
        _run(
            module,
            monkeypatch,
            ["--verify", "--tag", "v2026.8.0", "--switch-source", "ghcr"],
        )

    message = str(excinfo.value)
    assert "Dockerfile-chat" in message
    assert "Dockerfile-chat-gpu" in message
    assert "Dockerfile-mailbox" not in message
    assert (templates / "Dockerfile-chat").read_text() == (
        "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"
    )


def test_verify_fails_when_no_template_names_a_base_image(tmp_path, monkeypatch):
    """A check that examines nothing must not pass.

    A renamed directory, a renamed base image, or a wrong path all produce an
    empty set, and an empty set satisfies "every reference carries the release
    tag" without reading a single line.
    """
    module, _ = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{"Dockerfile-postgres": "FROM docker.io/pgvector/pgvector:pg17\n"},
    )

    with pytest.raises(SystemExit) as excinfo:
        _run(
            module,
            monkeypatch,
            ["--verify", "--tag", "v2026.8.0", "--switch-source", "ghcr"],
        )

    assert "no service template" in str(excinfo.value).lower()


def test_verify_fails_on_a_base_image_it_does_not_know(tmp_path, monkeypatch):
    """A renamed base must not pass by being invisible to the check.

    Both the rewriter and the check read `BASE_IMAGE_MAP`, so a template moved
    onto `a2rchi-python-base-v2` is skipped by both: the retarget leaves it
    alone and the check never looks at it. As long as one other template
    matches, the run would go green on a service that ships the base image the
    release replaced.

    The check therefore refuses any `a2rchi` base it cannot place, rather than
    passing over it. `--bases` still narrows which known bases are compared, so
    a run limited to `python` does not fail on the pytorch templates.
    """
    module, _ = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": "FROM ghcr.io/fasrc/a2rchi-python-base:v2026.8.0\n",
            "Dockerfile-mailbox": (
                "FROM ghcr.io/fasrc/a2rchi-python-base-v2:v2026.8.0\n"
            ),
        },
    )

    with pytest.raises(SystemExit) as excinfo:
        _run(
            module,
            monkeypatch,
            ["--verify", "--tag", "v2026.8.0", "--switch-source", "ghcr"],
        )

    message = str(excinfo.value)
    assert "Dockerfile-mailbox" in message
    assert "a2rchi-python-base-v2" in message


def test_verify_narrowed_to_one_base_ignores_the_other(tmp_path, monkeypatch):
    """`--bases python` compares the python templates and skips the pytorch ones.

    The refusal above is about a base the script cannot place at all, not about
    a known base this run was told to leave alone.
    """
    module, _ = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": "FROM ghcr.io/fasrc/a2rchi-python-base:v2026.8.0\n",
            "Dockerfile-chat-gpu": (
                "FROM ghcr.io/fasrc/a2rchi-pytorch-base:dev-4314ac4\n"
            ),
        },
    )

    _run(
        module,
        monkeypatch,
        [
            "--verify",
            "--tag",
            "v2026.8.0",
            "--switch-source",
            "ghcr",
            "--bases",
            "python",
        ],
    )


def test_verify_requires_a_tag(tmp_path, monkeypatch):
    """A verification with no expected tag has nothing to check."""
    module, _ = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{"Dockerfile-chat": "FROM ghcr.io/fasrc/a2rchi-python-base:v2026.8.0\n"},
    )

    with pytest.raises(SystemExit) as excinfo:
        _run(module, monkeypatch, ["--verify", "--switch-source", "ghcr"])

    assert "--tag" in str(excinfo.value)


def test_verify_refuses_a_digest(tmp_path, monkeypatch):
    """`--verify` checks a tag reference, so a digest has no meaning here.

    The release retarget writes a tag: `--tag … --switch-source ghcr
    --orig-tag all` drops any digest the template carried. Accepting
    `--digest` here would check a reference the release never writes, which is
    the silent-wrong-answer failure this mode exists to end.
    """
    module, _ = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{"Dockerfile-chat": "FROM ghcr.io/fasrc/a2rchi-python-base:v2026.8.0\n"},
    )

    with pytest.raises(SystemExit) as excinfo:
        _run(
            module,
            monkeypatch,
            ["--verify", "--tag", "v2026.8.0", "--digest", f"python={_PY_DIGEST}"],
        )

    assert "--digest" in str(excinfo.value)


# --- The CI call sites -------------------------------------------------------
#
# These tests read the argv out of the workflow file instead of restating it.
# Issue #339 is a call site that drifted from what the script needs, and a test
# that restates the argv stays green through exactly that drift.

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
_RELEASE_WORKFLOW = _WORKFLOWS / "test-and-build-tag.yml"
_RETARGET_STEP = "Point Dockerfiles to versioned base images"
_VERIFY_STEP = "Verify the service templates point at this release's base images"
_SMOKE_STEP = "Run smoke deployment"

# A GitHub Actions expression, e.g. `${{ needs.build-images.outputs.tag }}`.
_EXPRESSION_RE = re.compile(r"\$\{\{[^}]*\}\}")

_INVOCATION = ["python", "scripts/dev/update_service_base_images.py"]


def _workflow_step(workflow_path, step_name):
    """The step named `step_name`, from any job of the workflow."""
    document = yaml.safe_load(workflow_path.read_text())
    for job in document["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == step_name:
                return step
    raise AssertionError(f"{workflow_path.name} has no step named {step_name!r}")


def _script_argv(step, tag):
    """The script arguments the step passes, with every CI expression resolved.

    Every `${{ ... }}` in the step becomes `tag`. The release steps interpolate
    exactly one expression, the release tag, so one substitution value is
    enough. `re.sub` takes a function, not the string itself, because a
    replacement string reads a backslash as an escape.
    """
    command = _EXPRESSION_RE.sub(lambda _: tag, step["run"]).strip()
    argv = shlex.split(command)
    assert argv[: len(_INVOCATION)] == _INVOCATION, f"unexpected command: {command}"
    return argv[len(_INVOCATION) :]


def test_the_release_workflow_argv_rewrites_the_pin_the_templates_carry(
    tmp_path, monkeypatch
):
    """Issue #339: the release retarget must reach the templates' current tag.

    The step passed no `--orig-tag`, so it took the script's `latest` default
    and matched none of the 15 service templates -- they carry `dev-4314ac4`,
    not `latest`, since `5e168b00`. Measured against a copy of the real
    templates on `5a26b5a3`: the old release argv rewrote 0 of 15, and the
    PR-preview argv rewrote 15 of 15.

    Both fixtures below end as the real templates do. Several carry a trailing
    space after the reference, and that space has to survive the rewrite.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n",
            "Dockerfile-chat-gpu": (
                "FROM ghcr.io/fasrc/a2rchi-pytorch-base:dev-4314ac4 \n"
            ),
        },
    )
    argv = _script_argv(_workflow_step(_RELEASE_WORKFLOW, _RETARGET_STEP), "v2026.8.0")

    _run(module, monkeypatch, argv)

    assert (templates / "Dockerfile-chat").read_text() == (
        "FROM ghcr.io/fasrc/a2rchi-python-base:v2026.8.0\n"
    )
    assert (templates / "Dockerfile-chat-gpu").read_text() == (
        "FROM ghcr.io/fasrc/a2rchi-pytorch-base:v2026.8.0 \n"
    )


def _job_step_names(workflow_path, job_name):
    """Every step name in one job, in file order."""
    document = yaml.safe_load(workflow_path.read_text())
    return [step.get("name") for step in document["jobs"][job_name]["steps"]]


def _flag_value(argv, flag):
    """The value that follows `flag` in `argv`."""
    assert flag in argv, f"{flag} missing from {argv}"
    return argv[argv.index(flag) + 1]


def test_the_release_run_verifies_the_retarget_before_it_smoke_tests():
    """The proof runs between the rewrite and the thing that depends on it.

    A smoke test against the wrong base proves nothing, so the run has to stop
    before it rather than after. The verification also has to name the same
    reference the retarget writes; a check on a different tag or a different
    registry passes a tree the release cannot ship.
    """
    names = _job_step_names(_RELEASE_WORKFLOW, "smoke-test")
    assert names.index(_RETARGET_STEP) < names.index(_VERIFY_STEP)
    assert names.index(_VERIFY_STEP) < names.index(_SMOKE_STEP)

    retarget = _script_argv(
        _workflow_step(_RELEASE_WORKFLOW, _RETARGET_STEP), "v2026.8.0"
    )
    verify = _script_argv(_workflow_step(_RELEASE_WORKFLOW, _VERIFY_STEP), "v2026.8.0")

    assert "--verify" in verify
    assert _flag_value(verify, "--tag") == _flag_value(retarget, "--tag")
    assert _flag_value(verify, "--switch-source") == _flag_value(
        retarget, "--switch-source"
    )


def test_the_release_steps_compose_on_the_pin_the_templates_carry(
    tmp_path, monkeypatch
):
    """The whole of issue #339, driven by the argv the workflow really passes.

    Verification fails on the templates as they sit in the tree, passes after
    the retarget step runs, and both argvs are read from the workflow file. A
    release that reintroduces the defect — by losing `--orig-tag all`, by
    verifying a different tag, or by dropping either step — turns this red.
    """
    module, templates = _write_fixtures(
        tmp_path,
        monkeypatch,
        **{
            "Dockerfile-chat": "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n",
            "Dockerfile-chat-gpu": (
                "FROM ghcr.io/fasrc/a2rchi-pytorch-base:dev-4314ac4 \n"
            ),
        },
    )
    retarget = _script_argv(
        _workflow_step(_RELEASE_WORKFLOW, _RETARGET_STEP), "v2026.8.0"
    )
    verify = _script_argv(_workflow_step(_RELEASE_WORKFLOW, _VERIFY_STEP), "v2026.8.0")

    with pytest.raises(SystemExit) as excinfo:
        _run(module, monkeypatch, verify)
    assert "dev-4314ac4" in str(excinfo.value)

    _run(module, monkeypatch, retarget)
    _run(module, monkeypatch, verify)

    assert (templates / "Dockerfile-chat").read_text() == (
        "FROM ghcr.io/fasrc/a2rchi-python-base:v2026.8.0\n"
    )
