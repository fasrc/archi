## Context

- **Design doc (the one file to change):**
  `docs/superpowers/specs/2026-10-10-vector-store-rebuild-cutover-design.md`, called "the
  doc" below. It is unchanged since #711 (`82850b3b`). The line numbers below are for
  `origin/dev` @ `e83160e6`, where the doc has 487 lines.
- **Issue:** #712 (P2, `auto-ok`, default tier). Its "Decision" section (operator,
  2026-10-20) accepts all four findings with the fixes in its plan, and fixes the
  `sync_pending` phase for finding 2.
- **Style:** the doc uses ASD-STE100 short sentences, section numbers, and file:line links
  that pin a commit. Keep that style. Pin each new code link to `e83160e6`.
- **Out of scope:** the 2 round-4 findings that the operator's #671 comment of
  2026-10-15 lists for the implementation plan. Do not change the 4.10 table row
  `building | gate | no`, and do not change the first-install condition of the baseline
  row (4.2, line 124).

The text in each decision below is the required content. Copy it as written, unless a
sentence does not fit the doc around it. Then keep the meaning and the identifiers.

## Goals / Non-Goals

**Goals:** each of the 4 findings is fixed in every section that states the rule, and no
section of the doc contradicts another one.

**Non-Goals:** code, tests, config, the implementation plan of #671, and any edit that
the four findings do not need.

## Decisions

### D1. Finding 1 (P1): the owner process runs its own retries

**Rule.** The process that holds the lifecycle connection is the only process that can
run a retry, because it holds both locks. So the recovery job in that process uses that
connection. An in-process **step lock** stops the job from using the connection while a
step of the lifecycle runs.

Edit sites:

1. 4.10, recovery steps 1 and 2 (lines 396-397). Replace them with:

   > 1. If this process already has a lifecycle connection, the job uses that connection. It does not open a second connection, because that connection already holds both locks. Each step of the lifecycle (the build, a cutover and its 3 tries, the probe, or a rollback) holds an in-process **step lock** while it runs. The job tries the step lock with no wait:
   >    - If a step holds it, the job returns. That step continues the lifecycle.
   >    - If the job gets it, the lifecycle waits for a retry: a cutover after 3 lock timeouts (4.5), or a rollback after a lock timeout (4.6). The job goes to step 3 on the lifecycle connection, and keeps the step lock until that step ends.
   > 2. If this process has no lifecycle connection, the job opens one, and tries to take both locks. If another process holds them, it returns.

2. 4.5, line 298. Replace the last two sentences ("After 3 failures, ... the build set
   stays current.") with:

   > After 3 failures, the build stays `validating` with its tables kept (4.3 step 2). The lifecycle connection keeps both locks during the wait, so no write reaches the live set, and the build set stays current. The recovery job of this process tries the cutover again on that connection (4.10, step 1). No other process can take the locks, so no other process tries it.

3. 4.6, line 316. Replace "A rollback that times out on its lock keeps that phase, and
   the recovery job (4.10) tries it again until it succeeds." with:

   > A rollback that times out on its lock keeps that phase. The lifecycle connection keeps both locks, and the recovery job of this process tries the rollback again on that connection (4.10, step 1) until it succeeds.

4. Section 5, two rows. In "The cutover lock times out", replace "Then the recovery job
   tries again every 5 minutes" with "Then the recovery job of the process that holds the
   locks tries again every 5 minutes". In "The rollback lock times out", replace "the
   recovery job tries the rollback again" with "the recovery job of the process that
   holds the locks tries the rollback again".

5. Section 6, the "Recovery job (4.10)" line (line 434). Replace "A run returns at once if
   this process has a lifecycle connection, or if another process holds the locks." with:

   > In the process that holds the lifecycle connection, a run after 3 cutover lock timeouts does the cutover again on that connection, and a run after a rollback lock timeout does the rollback again. Neither run opens a second connection. A run returns at once while a step of this lifecycle holds the step lock, or if another process holds the locks.

Check: `grep -n "Returns at once if this process already has a lifecycle connection" "$DOC"`
prints nothing.

### D2. Finding 2 (P2): the final sync is a durable step (`sync_pending`)

**Rule (operator decision).** The transaction that moves the build to an end state also
sets `index_build.phase = 'sync_pending'` on that row. The code clears it after
`update_vectorstore()` returns. Recovery (4.10 step 3) runs the sync for a row in that
phase. `sync_pending` does not defer writes (4.3 step 1), or the sync defers itself.

A sync that defers also returns, and the code clears the phase. That is safe: a deferral
occurs only while another lifecycle holds the write lock or has a row that is not in an
end state, and the final sync of that lifecycle embeds the documents and clears each
`sync_pending` row.

Edit sites:

1. 4.2, line 84. The `phase` comment becomes
   `-- NULL | probe_pending | rollback_pending | sync_pending; see 4.10`.

2. 4.3 step 1, line 153. Replace "or has a non-NULL `phase`" with "or has
   `phase = 'probe_pending'` or `phase = 'rollback_pending'`". Replace "The sync at the
   end of the lifecycle embeds the document (4.10)." with "The final sync (4.10) embeds
   the document." After the sentence that ends "and the end of its lifecycle.", add
   (before "The existing in-process `RLock`"):

   > A `sync_pending` phase (4.10) does not defer a write: it comes after the end of the lifecycle, and the final sync is itself an incremental write, so a deferral in that phase defers the sync itself.

   Do not put this sentence before "The document stays `pending`". There it reads as if
   the `sync_pending` case leaves the document `pending`.

3. 4.6, line 314. Replace "If the probe passes, the code sets `phase = NULL`, and the
   lifecycle ends (4.10)." with "If the probe passes, the code sets
   `phase = 'sync_pending'`, and the lifecycle ends (4.10)."

4. 4.6, line 318. Replace "the `live` row becomes `previous` with `rolled_back_at = NOW()`
   and `phase = NULL`" with "the `live` row becomes `previous` with
   `rolled_back_at = NOW()` and `phase = 'sync_pending'`".

5. 4.10 table (lines 383-390). Keep the `building` row as it is. Change the last row to
   "`previous` after a rollback, `rejected`, `failed`, `superseded`, with `phase = NULL`".
   Change the `live`, `phase = NULL` row only if the new row needs it to read clearly.
   Add this row after the `rollback_pending` row:

   > | any end status, `phase = 'sync_pending'` | final sync, then `phase = NULL` | yes; the locks are released, but the final sync is still due |

6. 4.10, the "Locks" paragraph (line 392). Replace it with:

   > **Locks and the final sync:** The lifecycle connection (4.3 step 1) holds the build lock and the write lock from step 1 until an end state. The transaction that moves the build to an end state also sets `phase = 'sync_pending'` on its row. Then the code releases both locks, and runs the **final sync**: one incremental sync (`update_vectorstore()`) on the live set. The sync embeds each document that is still `pending`: an upload that the build deferred, a new document from after the build read the catalog, and a document that changed during the build. After `update_vectorstore()` returns, the code sets `phase = NULL` on each row with `phase = 'sync_pending'`. If the sync raises (for example, a connection error that the caller catches and logs), the phase stays, and the recovery job runs the sync again (step 3). A sync that defers (4.3 step 1) also returns. A deferral occurs only while another lifecycle holds the write lock or has a row that is not in an end state, and the final sync of that lifecycle embeds the documents.

7. 4.10, recovery step 3 (line 398). After "A `validating` row first gets the rule of 4.3
   step 2a.", add:

   > If no such row exists, but a row has `phase = 'sync_pending'`, the job releases both locks and runs the final sync (above).

8. Section 5. Add this row after "The rollback lock times out":

   > | The final sync raises after the end state (for example, a connection error) | The row keeps `phase = 'sync_pending'`, and writes are not deferred. The next run of the recovery job runs the sync again and clears the phase. |

   In the row "A user uploads a file during a build or its recovery", replace "The sync at
   the end of the lifecycle embeds it." with "The final sync (4.10) embeds it."

9. Section 6. Replace the line "An incremental write defers while a row is `building` or
   `validating` or has a non-NULL `phase`, also when no process holds the locks." with:

   > An incremental write defers while a row is `building` or `validating`, or has `phase = 'probe_pending'` or `phase = 'rollback_pending'`, also when no process holds the locks. A row with `phase = 'sync_pending'` does not defer a write.

   Add this line after the "Recovery job (4.10)" line:

   > Final sync (4.10): the transaction that reaches an end state sets `phase = 'sync_pending'`, and a sync that returns clears it. If the final sync raises, the row keeps `sync_pending`, and the next recovery run runs the sync again and clears the phase.

Check: `grep -n "sync_pending" "$DOC"` prints the `phase` comment (4.2), the table row
(4.10), and the section 6 line.

### D3. Finding 3 (P2): only a removed or changed source skips the shrink check

**Rule.** Replace the one `source_digest` with `source_entries`, one hash for each
source input. Skip the ratio check only when an entry of the live row is absent from the
build, or when the live value is `unknown`. A build that only adds entries keeps the
check.

This rule covers the issue's two cases, "a strict subset of the live set" and "one
source's inputs were narrowed". A hash cannot show if a changed input is narrower or
wider, so each changed entry skips the check. The rule also skips the check for a build
that removes one source and adds another. A literal "strict subset" rule would reject
that build on every redeploy, and only a person could then clear it, which breaks
success criterion 5. Keep the `unknown` waiver for the first build after an upgrade
(no record shows which sources built the live set).

Edit sites:

1. 4.2, line 101. Replace the `source_digest` bullet with:

   > - `source_entries`: a sorted list with one hash for each enabled source input. A hash covers the source type, one input (for example one seed URL from the `links` input lists, one git repository, or one Jira project), and the settings of that type that select documents (for example `base_source_depth` for `links`). The identity holds the hashes only, so no URL or credential is in it.

2. 4.2, line 133 (baseline). Replace "`chunking` and `source_digest` are `unknown`. No
   table records the chunk settings or the sources that built the live set." with
   "`chunking`, `source_entries`, and `embedding_options` are `unknown`. No table records
   the chunk settings, the sources, or the embedder options that built the live set."
   Keep the sentence about the last `ingest_run`. (This edit also does the baseline part
   of D4.)

3. 4.4, lines 204-209. Keep line 204 and the first-install bullet (line 206). Replace the
   line 207 bullet and the line 209 paragraph with:

   > - The build removes or changes a source of the live set: one or more hashes in the `source_entries` of the live row are absent from the build identity. A removed source, or a source whose inputs changed (for example, a lower crawl depth), is an intended change of the corpus, so a smaller corpus is expected. A hash cannot show if a change makes a source narrower or wider, so each changed entry skips the check.
   > - The `source_entries` of the live row is `unknown` (the first build after an upgrade, 4.2). No record shows which sources built the live set.
   >
   > The other checks still apply in each of these conditions. A build that keeps every entry of the live set, also with new entries, keeps the ratio check. An added source does not make the corpus smaller, so a large drop in chunks means that an existing source failed. For example, a source auth outage gives no documents, not failed documents, so only the ratio check catches it.

4. 4.7, line 345. Replace "A change of `source_digest` alone does not start a full build"
   with "A change of `source_entries` alone does not start a full build".

5. Section 6, the gate line (line 428). Replace "A changed or `unknown` live
   `source_digest` skips the chunk ratio only: with it, a dimension error, an empty build,
   or a high failed ratio still rejects the build. With the same source digest, a drop
   below the ratio rejects the build." with:

   > A live entry that is absent from the build (a removed source, or a source whose inputs changed), or an `unknown` live `source_entries`, skips the chunk ratio only: with it, a dimension error, an empty build, or a high failed ratio still rejects the build. With the same entries, a drop below the ratio rejects the build. A source addition plus an existing source that returns no documents, with a drop below the ratio, rejects the build.

6. Section 6, the baseline line (line 442). Replace "`chunking` and `source_digest` are
   always `unknown`." with "`chunking`, `source_entries`, and `embedding_options` are
   always `unknown`."

7. Section 6, the change detection line (line 443). Replace "A change of `source_digest`
   alone does not." with "A change of `source_entries` alone does not."

Checks: `grep -n "differs from the \`source_digest\` of the live row" "$DOC"` prints
nothing, and `grep -c "source_digest" "$DOC"` prints `0`.

### D4. Finding 4 (P2): the build identity records the embedder options

**Rule.** A new identity field, `embedding_options`, holds the allowlisted constructor
kwargs that change the stored vectors, with their values. The allowlist starts with
`encode_kwargs.normalize_embeddings`. It names no credential. Change detection, the
`validating` check, and the reader use the field. The baseline row records `unknown`.

**Why values, not a digest.** The issue plan says "a digest". The digest was there to
keep credentials out of the identity, and an allowlist of non-secret keys does that too.
Chat also needs the values: the finding's result is that chat embeds queries for the old
live set with the new setting. With a digest, a change of the option starts a rebuild,
but chat still uses the new setting for the old set until the cutover. With the values,
chat builds the embedder of the live row with the options of that row (4.8), so success
criterion 1 holds during that build too. State this reason in the PR body.

**Why `encode_kwargs.normalize_embeddings`, not all of `encode_kwargs`.** The plan says
"start with `encode_kwargs`". The key path is the part that changes vectors. A whole-dict
rule would also start a full rebuild for a key that does not change vectors.

Edit sites:

1. 4.2, the field list (after the `embedding_class` / `embedding_model` /
   `embedding_dimensions` bullet, line 99). Add:

   > - `embedding_options`: the constructor kwargs that change the stored vectors, with their values, from one allowlist in `build_identity`. The allowlist starts with `encode_kwargs.normalize_embeddings` of `HuggingFaceEmbeddings`, which the config renders ([base-config.yaml:332-334](https://github.com/fasrc/archi/blob/e83160e6/src/cli/templates/base-config.yaml#L332-L334)). With the `l2` or `inner_product` distance ([postgres_vectorstore.py:131-135](https://github.com/fasrc/archi/blob/e83160e6/src/data_manager/vectorstore/postgres_vectorstore.py#L131-L135)), a change of normalization changes the scores and the threshold filter. The allowlist names no credential.

2. 4.2, line 103. Replace the first two sentences ("The identity never holds constructor
   kwargs, ... for that class.") with:

   > The identity holds no constructor kwarg outside the `embedding_options` allowlist, so it never holds an API key or a token. To build the embedder of a row, chat takes the class, the model, and `embedding_options` from the identity. It takes all other constructor kwargs, credentials included, from the current `embedding_class_map` entry for that class. If `embedding_options` is `unknown` (a baseline row), chat takes those options from the class-map entry too, as today.

   Keep the sentences after them.

3. 4.2, line 133 (baseline): done in D3 edit site 2.

4. 4.3 step 2a, line 161. Replace "compare its embedding model, dimension, and chunk
   configuration with the current config:" with "compare its identity with
   `build_identity(current config)` on the fields of change detection (4.7):".

5. 4.7, line 345. Replace "Compare `embedding_class`, `embedding_model`,
   `embedding_dimensions`, and `chunking`." with "Compare `embedding_class`,
   `embedding_model`, `embedding_dimensions`, `embedding_options`, and `chunking`." Then,
   after the sentence "An `unknown` field is a mismatch, so the first boot after an
   upgrade starts one build (4.2).", add:

   > The comparison does not read the distance metric, so a change of `embedding_options` with `cosine` also starts a build. That build does not change the results, but it is safe.

6. 4.8, line 357. Replace "It takes the other constructor kwargs, credentials included,
   from the current class-map entry (4.2, build identity)." with "It takes
   `embedding_options` from the same row, and the other constructor kwargs, credentials
   included, from the current class-map entry (4.2, build identity)."

7. Section 6, the `build_identity` line (line 437). Replace it with:

   > `build_identity`: records only the allowlisted fields, and no constructor kwarg outside the `embedding_options` allowlist. A config with an API key in the kwargs gives an identity without the key. A `HuggingFaceEmbeddings` config records `encode_kwargs.normalize_embeddings` in `embedding_options`.

8. Section 6, the change detection line (line 443). After "The same config does not.",
   add "A change of `encode_kwargs.normalize_embeddings` with `reset_collection: false`
   starts a cutover build."

Check: `grep -n "normalize_embeddings\|encode_kwargs" "$DOC"` prints the 4.2 identity rule
and the section 6 lines.

### D5. Revision line

Line 4. Append:

> A fifth revision on 2026-11-03 (#712) fixed the 4 other findings of the fourth round: the owner process runs its own retries (4.10), the final sync is a durable step (`sync_pending`, 4.10), a source addition keeps the shrink check (4.4), and the build identity records the embedder options that change vectors (4.2, 4.7).

## Risks / Trade-offs

- **The doc grows contradictions from partial edits.** Each rule appears in 2 to 6
  places. Mitigation: each decision lists all of its sites, and the publish task greps
  the doc for the old terms (`source_digest`, "non-NULL `phase`", "Returns at once") and
  reads the changed sections once end to end.
- **D3 skips the check for a build that removes one source and adds another.** In that
  build, an outage of a third source is not caught by the ratio. The other checks still
  apply. A stricter rule needs a person to clear a legitimate removal, which breaks
  success criterion 5.
- **D4 differs from the plan's word "digest".** The reason is in D4, and the PR body
  states it. If the operator wants a digest, the change is one bullet and one sentence
  of 4.8.
- **The PR also adds this folder.** Issue #712 says the PR changes only the design doc.
  The claim comment on #712 explains the exception. Removing the folder is one commit.
- **Gate flake.** Each commit runs the full unit suite. `tests/unit/test_judge_slurm.py`
  fails at random under load (#797). The diff touches no code, so a failure in that
  file is the flake: run the commit again. Never use `--no-verify`.
