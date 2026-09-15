# Design — digest-pinning the service templates

## Context

Fifteen files, two distinct references, one `FROM` line each:

```
$ grep -hE '^FROM ghcr\.io/fasrc' src/cli/templates/dockerfiles/Dockerfile-* | sort | uniq -c
      1 FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4
      6 FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4␠
      1 FROM ghcr.io/fasrc/a2rchi-pytorch-base:dev-4314ac4
      7 FROM ghcr.io/fasrc/a2rchi-pytorch-base:dev-4314ac4␠
```

(`␠` marks a trailing space; 13 of the 15 carry one.) Measured on `origin/dev` at
`19a3cb31`, 2026-08-27.

Everything this change needs already exists. `scripts/dev/update_service_base_images.py`
writes the digest form (#334, merged at `3bab8aeb`). The preflight reads it:
`_FROM_BASE_RE` (`src/cli/managers/base_image_preflight.py:33`) captures `\S*` after the
image name, so the `@sha256:` suffix rides along and `base_reference()`
(`src/cli/managers/base_image_preflight.py:77`) returns the whole pinned reference. The
repository guard reads it: `_base_pins`
(`tests/unit/test_python_version_declaration.py:320`) takes the build name from the managed
annotation when the reference carries no tag, and four tests below it
(`test_base_pins_reads_the_build_from_a_digest_annotation` and its neighbours) were written
against this change by name. So the design question is not *how* — it is *what must be true
afterwards, and what could quietly stop being true*.

## Goals / Non-Goals

**Goals:**

- No `ghcr.io/fasrc` reference in the templates resolves through a mutable tag.
- Every digest-pinned line still says which build it is, in a form the tooling reads.
- The result is a valid Dockerfile that a real builder parses.
- A guard fails if a later commit puts a tag back.

**Non-Goals:**

- Any change to `scripts/dev/update_service_base_images.py`. #334 owns it and it is done.
- Any workflow edit. The operator owns those, and none is needed (see below).
- Any change to `src/cli/managers/base_image_preflight.py`. It is already digest-ready.
- Moving the build. This pins `dev-4314ac4` as it is; it does not adopt a newer base.
- Resolving digests at build time from a registry. The two digests are literals.

## Decisions

### The script writes the templates; nobody hand-edits them

The command is one line, and it is the same command the developer guide documents at
`docs/docs/developer_guide.md:490`:

```bash
python scripts/dev/update_service_base_images.py \
  --switch-source ghcr --orig-tag all --tag dev-4314ac4 \
  --digest python=sha256:c068f17b8cba96682e7007c9dd5511f43fea86c796f3cbeee44e2766c5a9b8e8 \
  --digest pytorch=sha256:c29c6e8b4262736e3a5d3d47756b0d483db88254a91b16932d37f498bc704b5e
```

`--orig-tag all` is required: the script's default is `latest`, which matches none of the 15
lines and would rewrite nothing while exiting 0 — the exact silent no-op that cost a full
release cycle in #339.

Hand-editing was the alternative and is rejected for a reason beyond effort. The annotation
wording must match `_ANNOTATION_RE` (`scripts/dev/update_service_base_images.py:127`)
character for character or no later run can find it, and the trailing spaces on 13 lines
must survive untouched. The script does both by construction. A human doing this 15 times
gets one of them wrong.

**The result was measured, not assumed.** The command above was run against the working tree
on 2026-08-27 and the output reverted. It rewrote all 15 files and produced, for
`Dockerfile-chat`:

```
 # syntax=docker/dockerfile:1
-FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4␠
+# base-image-pin: dev-4314ac4 (managed by update_service_base_images.py)
+FROM ghcr.io/fasrc/a2rchi-python-base@sha256:c068f17b8cba96682e7007c9dd5511f43fea86c796f3cbeee44e2766c5a9b8e8␠
```

The trailing space survives, as the script's design says it must.

### The annotation carries the managed wording, not the issue's

Issue #335 asks for `# base image: dev-4314ac4`. This change writes
`# base-image-pin: dev-4314ac4 (managed by update_service_base_images.py)`. The reasoning is
in `proposal.md` under "One deviation from issue #335, and why"; the short version is that
the issue's own instruction — use the #334 script — produces the managed wording, and the
existing guard `test_service_templates_pin_one_explicit_base_tag` rejects anything else.

### Proof that the result parses is a real builder, run once, recorded here

The failure this change must not reproduce is the one review caught on #342: a `#` on the
`FROM` line makes every service build fail at parse time, before any image is pulled. A
grep for `#` on a `FROM` line is a cheap guard and this change adds one, but a grep is not
proof that a builder accepts the file. A builder is.

Both were run against the rewritten templates on 2026-08-27:

```
$ docker build --check -f src/cli/templates/dockerfiles/Dockerfile-chat "$empty_ctx"
#5 [internal] load metadata for ghcr.io/fasrc/a2rchi-python-base@sha256:c068f17b…
Check complete, no warnings found.        # exit 0

$ podman build --pull=never -f src/cli/templates/dockerfiles/Dockerfile-chat "$empty_ctx"
STEP 1/16: FROM ghcr.io/fasrc/a2rchi-python-base@sha256:c068f17b…
Error: creating build container: … image not known
```

docker (buildx 0.34.1) resolved the digest's metadata from ghcr and found no warnings.
podman (`--pull=never`, so it never downloads a multi-gigabyte base) parsed the `FROM` and
failed only on the image's local absence — which is the parse succeeding. Both results are
recorded here rather than repeated as a task, because the loop sandbox has no container
runtime and a task that needs one would halt the loop rather than fail it.

### The guards go in the two files that already own this ground

`tests/unit/test_base_image_preflight.py` gets the assertion that the preflight reads a
digest-pinned reference, because that file already globs the real `TEMPLATE_DIR` and asserts
against the real templates (`test_required_references_carry_the_pin_from_the_templates`,
`tests/unit/test_base_image_preflight.py:58`). The issue directs extending this file rather
than adding one.

`tests/unit/test_python_version_declaration.py` needs nothing new. Its
`test_service_templates_pin_one_explicit_base_tag` already accepts a digest that carries an
annotation and rejects one that does not, and already collapses two different digests to
one build name through the annotation. It flips from "passing on tags" to "passing on
digests" with no edit, which is the outcome #334 built it for. Running both files against
the rewritten tree on 2026-08-27 gave **145 passed**, with the one unrelated failure
described below.

### The release flow still round-trips

This is the cross-subsystem risk worth stating plainly, because getting it wrong breaks a
release after the images are published.

`test-and-build-tag.yml` verifies three times that the templates name *this release's* base
tag, and one of those verifies runs on a fresh checkout of the dispatched ref before that
job rewrites anything (the `release` job's first step). If the templates in the tree still
carried a digest at that point, `--verify` would compare a digest against a tag reference
and fail.

They do not, because of the order the workflow already establishes:

1. `smoke-test` checks out the ref and retargets with `--tag <release> --switch-source ghcr
   --orig-tag all`. `--orig-tag all` matches a digest-pinned line, and writing a tag removes
   the annotation — both proven by #334's tests.
2. The same job verifies, smokes, then commits the rewritten Dockerfiles and pushes them to
   the dispatched ref.
3. `release` checks that ref out. It now carries tag references, so its verify passes.

The pre-existing hole — if step 2's push is lost, step 3's verify fails — is unchanged by
this work. A tree stuck on `dev-4314ac4` fails that verify exactly as a tree stuck on a
digest would, because neither names the release tag. This change adds no failure mode there;
the workflow comment at that step already names the gap and defers it.

`pr-preview.yml` calls the same retarget with `--tag pr-<N>`, so PR previews behave
identically.

### What this change deliberately leaves alone

A developer building bases locally uses `--switch-source localhost` with a `--tag`, which
writes a tag reference and drops the annotation. No automated call site passes
`--switch-source localhost`; it is a documented manual option. Nothing here needs to change
for it.

## Risks / Trade-offs

- **A digest is unreadable.** `sha256:c068f17b…` tells a human nothing. That is exactly what
  the annotation line above it repairs, and the guard makes the annotation mandatory: a
  digest with no annotation fails `test_service_templates_pin_one_explicit_base_tag`.
- **Repinning is now a two-value operation.** Moving to a newer base means supplying both
  digests, not editing one tag. The developer guide documents the command
  (`docs/docs/developer_guide.md:490`) and `--verify` proves a rewrite landed.
- **The digests are literals in a commit.** If the operator's verification was wrong, the
  templates build the wrong base. Mitigated by re-verifying both against ghcr on 2026-08-27
  before writing them (`gh api /orgs/fasrc/packages/container/<image>/versions`), and by
  docker resolving the python digest's metadata from ghcr during the parse check above.

## Notes for the implementation loop

- **A dangling `tasks.md` symlink at the repository root reddens an unrelated test.**
  `test_every_page_stating_a_minimum_is_guarded`
  (`tests/unit/test_python_version_declaration.py:236`) reads every documentation page it can
  reach and raises `FileNotFoundError` when the root `tasks.md` symlink points at a change
  directory that does not exist on this branch. The symlink is gitignored
  (`.gitignore:65`) and untracked, so repointing it at this change's `tasks.md` is safe and
  changes nothing in the diff. Loop 1 repointed it.
- Do not add an `openspec validate` step to any task. The loop sandbox has no `openspec`
  binary, and a task that calls it halts the loop just before the pull request.
- Format before staging. The pre-commit hook's black rewrites files after `git add`, so
  `git status` must be empty after the commit; CI's black is an assert, not a writer.
