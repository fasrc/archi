## Why

The #671 knowledge-base rebuild cutover design
(`docs/superpowers/specs/2026-10-10-vector-store-rebuild-cutover-design.md`) has 4 known
defects. Codex posted them on PR #711 two seconds after #711 merged, so no PR took a fix.
The nightly review run verified each finding against the doc text and the code, and the
operator accepted all four on 2026-10-20 (issue #712, "Decision" section). The
implementation plan for #671 (milestone `v2026.11.0`) waits for a design with no known
contradiction (#671 comment of 2026-10-20).

1. **P1, the lock owner cannot run the scheduled retry** (thread `r4287343449`). After 3
   cutover lock timeouts (4.5, line 298) or a rollback lock timeout (4.6, line 316), the
   design sends the retry to the recovery job. But recovery step 1 (4.10, line 396)
   returns in the process that holds the lifecycle connection, and step 2 (line 397)
   returns in every other process. No process runs the retry, and writes defer with no
   end.
2. **P2, the final sync is not durable** (thread `r4287343452`). The end state commits
   before the final `update_vectorstore()` (4.10, line 392). If that sync raises in the
   same process, recovery finds no non-end row, so deferred uploads stay `pending`.
3. **P2, any source-digest change waives the shrink gate** (thread `r4287343440`). Line
   207 skips the chunk-ratio check when the one `source_digest` differs. A redeploy that
   only adds a source changes the digest, so an auth outage of an existing source can
   make a shrunken corpus live.
4. **P2, the build identity omits embedder options that change vectors** (thread
   `r4287343430`). `build_identity()` (4.2, lines 97-103) and change detection (4.7, line
   345) ignore `encode_kwargs.normalize_embeddings`. With the `l2` or `inner_product`
   distance, a change of that option changes the scores, but no rebuild starts.

**This change is a driver for the nightly executor only.** The design of record is the
design doc above. The nightly supervisor reads its task list from
`openspec/changes/fix-issue-<n>-<slug>/tasks.md`, so this folder exists to hold that list
and the decisions below. Issue #712 says "The PR changes only the design doc". This folder
is the one exception, and the claim comment on #712 says so.

## What Changes

- **Docs only.** One file changes outside this folder:
  `docs/superpowers/specs/2026-10-10-vector-store-rebuild-cutover-design.md`. No code, no
  test, no config.
- Finding 1: recovery step 1 (4.10) makes the owner process do the next step of the
  stored state on its own lifecycle connection, guarded by an in-process step lock.
  Sections 4.5, 4.6, 5 and 6 name the same rule.
- Finding 2: a new `index_build.phase` value, `sync_pending`. The transaction that reaches
  an end state sets it, the code clears it after `update_vectorstore()` returns, and
  recovery runs the sync for a row in that phase. `sync_pending` does not defer writes
  (4.3 step 1).
- Finding 3: the identity field `source_digest` becomes `source_entries`, one hash for
  each source input. The ratio check is skipped only when an entry of the live row is
  absent from the build (a removed or changed source), or when the live value is
  `unknown`. A build that only adds sources keeps the ratio check.
- Finding 4: a new identity field, `embedding_options`, holds the allowlisted
  vector-affecting constructor kwargs (starting with
  `encode_kwargs.normalize_embeddings`) and their values. Change detection (4.7), the
  `validating` check (4.3 step 2a) and the reader (4.8) use it. The baseline row records
  it as `unknown`.
- One section 6 test line for each fix, and a new revision note on line 4.
- **No** change to the 2 round-4 findings that the operator's #671 comment of
  2026-10-15 already routes to the implementation plan: the 4.10 table row for a
  `building` row, and the first-install owner row.

## Capabilities

### New Capabilities
- `rebuild-cutover-design`: what the #671 cutover design of record states about the
  recovery of a waiting lifecycle, the final sync, the shrink check, and the build
  identity.

### Modified Capabilities
<!-- None. -->

## Impact

- `docs/superpowers/specs/2026-10-10-vector-store-rebuild-cutover-design.md` (sections 4.2,
  4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.10, 5, 6, and the revision line).
- This folder.
- Replies on the 4 Codex threads of PR #711 that name the fix commits.
- Unblocks the #671 implementation plan (milestone `v2026.11.0`).
