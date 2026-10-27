## ADDED Requirements

### Requirement: Confirmation standard for locking

A bank row that cites one or more `sources` SHALL count as confirmed only when every discrete fact in its `reference` is found in the ingested chunks of a page that the row cites.
This requirement defines the word "confirmed" that *Machine-queryable confirmation state* and
*Human-gated mutation* use; an operator MUST NOT lock a row that cites `sources` unless the row
is confirmed by this standard. A discrete fact is one claim in the `reference` that can be true
or false by itself. The standard has five parts:

1. **Which facts.** Every discrete fact that the `reference` asserts MUST be found, not only the
   facts that the row's `user_input` asks about. RAGAS `answer_correctness` and
   `factual_correctness` compare the system answer against the whole `reference`, so an
   unsupported fact anywhere in it corrupts the score.
2. **Partial support: trim, then lock.** If one or more facts are not found, the reviewed batch
   MUST remove or correct each unsupported fact, and only then lock the row. A row MUST NOT be
   locked while its `reference` still asserts an unsupported fact. The trim is a row edit, so it
   needs the explicit human confirmation that *Human-gated mutation* requires.
3. **Stale `notes`: fix the notes, then lock.** If the chunks support the `reference` but the
   row's `notes` describe a retrieval difficulty that no longer exists, the same batch MUST
   correct the `notes`, and only then lock the row.
4. **Evidence source: the ingested chunks.** A fact is found only if it is in the ingested chunks
   of a cited page (the `document_chunks` rows that the retriever reads and the benchmark scores
   against). A fact that is on the live web page but not in those chunks is NOT confirmed.
5. **Evidence shown to the reviewer: per fact.** A lock batch MUST show the human reviewer, for
   each discrete fact of each row, the source URL, the chunk index (`chunk_index`) of the chunk
   that holds the fact, and a short quoted span from that chunk that supports the fact.

This standard applies only to rows that cite `sources`. A row whose `sources` is empty (for
example a `should_refuse` anchor) stays under the existing rule of *Machine-queryable
confirmation state*: it is lockable with no `source_hashes`, and this requirement adds no
evidence demand for it.

#### Scenario: A fully supported reference can be locked

- **WHEN** a row cites `sources`, and every discrete fact in its `reference` is found in the
  ingested chunks of a cited page, including the facts that its `user_input` does not ask about
- **THEN** the row counts as confirmed, and an operator can lock it
- **AND** if one fact that the `user_input` does not ask about is not found, the row does not
  count as confirmed

#### Scenario: A partially supported reference is trimmed, then locked

- **WHEN** a reviewed batch finds that a row's `reference` asserts a fact that is not in the
  ingested chunks of any cited page
- **THEN** the batch removes or corrects that fact, and only then locks the row
- **AND** the row is never locked while its `reference` still asserts the unsupported fact

#### Scenario: Stale notes are corrected before the lock

- **WHEN** the ingested chunks support every fact in a row's `reference`, but the row's `notes`
  describe a retrieval difficulty that no longer exists
- **THEN** the same batch corrects the `notes`, and only then locks the row
- **AND** the row is never locked with the stale `notes`

#### Scenario: A fact found only on the live page is not confirmed

- **WHEN** a fact in a row's `reference` is on the live web page of a cited source, but is not in
  any ingested chunk of a cited page
- **THEN** that fact is not confirmed
- **AND** the row does not count as confirmed while its `reference` asserts that fact

#### Scenario: A lock batch shows the evidence for each fact

- **WHEN** a lock batch is shown to a human reviewer
- **THEN** for each discrete fact of each row, the batch shows the source URL, the chunk index,
  and a short quoted span from that chunk that supports the fact
