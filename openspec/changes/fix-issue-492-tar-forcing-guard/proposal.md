# Scan every tar option and every RUN in the service-template download guard

## Why

`tests/unit/test_service_template_downloads.py` is the only thing standing between the 15
service Dockerfile templates and the failure that took them down on 2026-09-16: a moving
download — a URL that names no version, so its payload can change format underneath us —
extracted with a forced decompressor. Its docstring claims it guards that whole class. It
does not. Two `chatgpt-codex-connector[bot]` findings on PR #474
([`4027485396`](https://github.com/fasrc/archi/pull/474#discussion_r4027485396),
[`4027485403`](https://github.com/fasrc/archi/pull/474#discussion_r4027485403), both P2,
posted `2026-09-16T14:59:46Z`) named two holes. PR #474 merged 20 minutes later at
`2026-09-16T15:19:20Z` with no reply on either thread, so neither was worked. The
unattended 4AM review on 2026-09-17 verified both post-merge and filed #492.

**Hole A — the pattern reaches only the first option token.** `_FORCED_DECOMPRESSOR`
(`tests/unit/test_service_template_downloads.py:37-40`) anchors the option immediately
after `tar\s+`, so any *separated* option is unreachable, and four forcing forms are absent
from its alternation entirely. Re-measured on this branch at `4b253e26`:

| command | today |
|---|---|
| `tar -xjf f.tar.bz2` | FLAGGED (the original defect) |
| `tar -x --gzip -f f.tar.gz` | **MISSED** — option is not the first token |
| `tar -x -z -f f.tar.gz` | **MISSED** — option is not the first token |
| `tar --lzip -xf f.tar.lz` | **MISSED** — form absent from the alternation |
| `tar --uncompress -xf f.tar.Z` | **MISSED** — form absent |
| `tar -I zstd -xf f.tar.zst` | **MISSED** — form absent |
| `tar --use-compress-program=zstd -xf f.tar.zst` | **MISSED** — form absent |
| `tar -xf f.tar.xz` | not flagged — correct, and must stay that way |

GNU tar 1.35 `--help` lists the complete set of compression options, measured on the host on
2026-09-18: `-a/--auto-compress`, `-I/--use-compress-program=PROG`, `-j/--bzip2`, `-J/--xz`,
`--lzip`, `--lzma`, `--lzop`, `--no-auto-compress`, `--zstd`, `-z/--gzip/--gunzip/--ungzip`,
and `-Z/--compress/--uncompress`. Nine of those eleven entries force a program. Two do not:
`--auto-compress` picks the program from the suffix, and `--no-auto-compress` only switches
that off. Both must stay clean.

**Hole B — the pairing cannot see past one `RUN`.** `_commands`
(`tests/unit/test_service_template_downloads.py:51-63`) joins backslash continuations then
splits on newline, so one `RUN … && …` block is one logical command and two `RUN`
instructions are two. Measured on this branch:

```
split across RUN     moving=True   offenders=[]      <- silent
one RUN (control)    moving=True   offenders=['tar -xjf']
```

Docker carries `/tmp/ff.tar` into the next layer, so the split form is a valid build shape
that reintroduces the exact failure while the guard stays green.

**Both holes are latent, neither is a live break.** Measured across all 15 templates at
`4b253e26`: 6 match `_MOVING_DOWNLOAD`, every one of those 6 keeps the `wget -O` and the
`tar -xf` in the same `RUN` (for example `src/cli/templates/dockerfiles/Dockerfile-grader:56-59`),
and all 15 report zero offenders. The red tests are therefore the whole proof; there is no
template failure to reproduce.

## What Changes

- The forced-decompressor check becomes a two-step scan rather than one regex: find each
  `tar` invocation in a command, then inspect its option tokens up to the next shell
  separator. Position stops mattering, so a separated `-x -z -f` is caught.
- The forcing set gains the four missing forms — `-I`, `--use-compress-program`, `--lzip`,
  `--uncompress` — and short options are matched inside a cluster, case-sensitively.
  `-a/--auto-compress` and `--no-auto-compress` are **not** forcing and stay clean.
- A moving download's **saved path** is tracked (`wget -O <path>`, `curl -o <path>`, or
  `curl --output <path>`), and a forced decompressor naming that path is flagged wherever in
  the template it sits — including a later `RUN`. A stdout sink (`-`, `/dev/stdout`) is not
  a saved path.
- Where a saved path is known, it **replaces** the whole-command pairing for that
  invocation: the guard asks whether *this* `tar` reads *that* file, not whether the two
  merely share a `RUN`. Where no saved path can be read — a piped download, a shell
  variable — the whole-command reach is kept exactly as it is today. `design.md` states the
  three branches and the measured evidence for each.
- The module docstring's `#473` claim is corrected, and the three other stale `#473`
  forward-references in `tests/unit/test_base_image_dependency_compatibility.py` with it.

## Why association is by saved path, not by proximity

Finding `4027485403` proposes associating "extraction inputs with prior downloads rather
than requiring both operations on one logical line". Taken as proximity, that regresses two
tests that exist precisely to hold this line:
`test_the_two_are_not_paired_across_separate_commands`
(`tests/unit/test_service_template_downloads.py:180-191`) asserts a moving download in one
command must not indict a different command's format, and
`TestAVersionedDownloadMayForceItsFormat` (`:153`) protects the legitimate geckodriver line
(`src/cli/templates/dockerfiles/Dockerfile-grader:62-65`: `tar -xzf` on a URL naming
`v0.36.0`, which cannot change format). A file-wide scan condemned that line once before,
which is the documented reason `_commands` pairs per command at all (`:51-63`).

The saved path satisfies both halves. All 6 moving templates save to
`/tmp/firefox-esr.tar.xz` and extract that same path with `tar -xf`; geckodriver's `tar`
operates on `geckodriver-v0.36.0-linux64.tar.gz`, a different path, so it stays clean.

## Capabilities

### New Capabilities

- `service-template-downloads`: what the service Dockerfile templates guarantee about
  fetching and extracting an archive, and what the repository guard that enforces it must
  recognise. No capability under `openspec/specs/` covers it today — the nearest,
  `service-base-images`, is about how the templates reference their base images and is
  itself unarchived (added by `openspec/changes/fix-issue-334-digest-pinned-base-refs/`).
  This change therefore adds requirements rather than modifying any.

### Modified Capabilities

None.

## Impact

- `tests/unit/test_service_template_downloads.py` — the guard itself, plus new cases.
- `tests/unit/test_base_image_dependency_compatibility.py` — docstring and comment text
  only, in its own commit. No assertion changes.
- `src/cli/templates/dockerfiles/**` is **not** edited. All 15 templates are already
  correct and must stay byte-identical; a diff there would mean a false positive.
- No production source changes at all. `diff-cover` measures `src/` only
  (`scripts/gate.sh`), so this diff reports *"No lines with coverage information"* and
  clears `--fail-under=80`. That is expected, not a failure — the tests are the evidence.
- No new dependency. `re` and string handling are enough; a Dockerfile parser is out of
  scope.
