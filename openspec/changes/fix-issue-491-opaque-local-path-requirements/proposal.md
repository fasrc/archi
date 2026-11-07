# Reject local-path and archive requirements as opaque in the base-image guards

## Why

`tests/unit/test_base_image_dependency_compatibility.py` guards the base images against a
protected dependency arriving unpinned or version-incompatible. Every guard in the module
**skips** when its subject is absent, which is correct for a genuinely absent package — the
CPU header carries no vllm. That makes "absent" the module's single most dangerous reading,
and `_opaque_requirements` (`:338-347`) exists to make an unreadable requirement fail rather
than read as absent.

It does not cover a requirement given as a bare **local path** or an **archive filename**.
`_VCS_OR_URL_REQUIREMENT` (`:333-335`) matches only `git+ / hg+ / bzr+ / svn+ / https?:// /
file:// / <drive>:`, so the path and archive forms pip accepts are reported by nothing.

Re-measured on branch `fix/issue-491-opaque-local-path-requirements` at `4b253e26`, module
loaded by file path, one requirement line appended to `torch==2.7.0`:

| requirement line | `_opaque_requirements` | name `_parse_requirements` records |
|---|---|---|
| `./local_vllm` | `[]` | none — line dropped |
| `../pkgs/vllm` | `[]` | none — line dropped |
| `/opt/vllm.whl` | `[]` | none — line dropped |
| `vllm-0.9.0-py3-none-any.whl` | `[]` | `vllm-0-9-0-py3-none-any-whl` |
| `dist/vllm-0.9.0.tar.gz` | `[]` | `dist` |

Two distinct mechanisms, one outcome. A line starting with `.` or `/` does not match
`_REQUIREMENT_PATTERN` (`:273`) at all and is dropped unrecorded; an archive filename matches
it whole, because `.` `-` `_` all sit inside the name class, so the module records a project
called `vllm-0-9-0-py3-none-any-whl` or `dist`. Either way vllm reads as **absent**,
`_unpinned_protected` (`:475`) sees nothing to flag, every guard in
`TestVllmAcceptsThePinnedOpenTelemetrySdk` and its siblings skips, and the module reports
green while pip installs an unmeasured build.

pip accepts both forms — measured, not assumed:
`python -m pip install --help` documents `<local project path>` and `<archive url/path>`.

**Latent, not a live break.** Re-measured at `4b253e26`: all five monitored files
(`requirements/cpu-requirementsHEADER.txt`, `requirements/gpu-requirementsHEADER.txt`,
`requirements/requirements-base.txt`, and both generated sets under
`src/cli/templates/dockerfiles/base-*-image/requirements.txt`) report
`_opaque_requirements == []`, across 327 requirement lines, with no unparsable-name line.
This change hardens the guard against a future edit, so the red test written first is the
whole proof — there is no existing failure to reproduce.

The identical class of hole for `-e git+https://…#egg=vllm` was closed on 2026-09-16 by
`_REQUIREMENT_BEARING_PATTERN` (`:381-383`). This change follows that shape: report the
unreadable line rather than trying to parse it.

## What Changes

- Add a sibling pattern that `_opaque_requirements` also consults, so a requirement whose
  first token names a **path** or an **archive** is reported as opaque. The class:
  1. the token starts with `.` (`./local_vllm`, `../pkgs/vllm`, `.\win_vllm`);
  2. the token contains `/` or `\` (`/opt/vllm.whl`, `dist/vllm-0.9.0.tar.gz`);
  3. the token ends in an archive suffix — `.whl`, `.zip`, `.tgz`, `.tbz2`, `.txz`, `.tar`,
     `.tar.gz`, `.tar.bz2`, `.tar.xz` (`vllm-0.9.0-py3-none-any.whl`).
- Leave `_REQUIREMENT_PATTERN` alone. Widening the name reader is the wrong direction: the
  failure mode to avoid is a path quietly parsing into a plausible project name (design D2).
- Update `_opaque_requirements`' docstring to name the widened class, dated.
- New parametrized cases in `TestOpaqueRequirementsFailClosed` (`:1448`), written red first.

The change is **tests-only** (`tests/unit/**` plus this OpenSpec markdown). No `src/` edit,
no runtime behaviour change, no new dependency.

Explicitly **not** done:

- No change to `_REQUIREMENT_PATTERN`, and no attempt to extract a project name from a path
  or a wheel filename. PEP 427 filename parsing in a guard is a second parser to keep
  correct, and a wrong extraction is worse than a reported failure (design D2).
- No handling of PEP 508 direct references (`vllm @ https://host/x.whl`). Measured at
  `4b253e26`: that form records the name `vllm` with a non-pin specifier, so
  `_unpinned_protected` already fails it closed. It is not a hole (design D4).
- No change to which files the module reads.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `dependency-pin-hygiene`: the fail-closed rule for unreadable requirement shapes now
  extends to bare local-path and archive requirements in the base-image compatibility
  guard, so a protected package supplied that way fails the suite instead of reading as
  absent and skipping every pairwise guard. The delta is expressed as `ADDED` requirements
  because this capability's spec has not been archived into `openspec/specs/` — it exists
  only in unarchived changes (see design D3).

## Impact

- **Tests**: `tests/unit/test_base_image_dependency_compatibility.py` only — one new
  module-level pattern, the `_opaque_requirements` reader, new parametrized cases, a
  docstring update. Baseline for that file alone at `4b253e26`: **133 passed, 0 skipped,
  in 0.15s**.
- **Runtime / src**: none.
- **Monitored files**: unchanged and they stay green. The candidate pattern was run against
  all five before authoring — 327 requirement lines, zero matches (design D1).
- **Coverage**: the diff is tests-only, so `diff-cover` reports *"No lines with coverage
  information"* and the `--fail-under=80` patch-coverage gate passes. That is the documented
  behaviour for a tests-only diff, not a bypassed gate.
- **Related**: #492 (tar-option decompression scan in the service Dockerfiles) is a separate
  guard in a separate module and stays independent.
