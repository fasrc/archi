## ADDED Requirements

### Requirement: Confirmation standard for locking

A bank row that cites one or more `sources` SHALL count as confirmed only when every discrete fact in its `reference` is found in the ingested chunks of a page that the row cites.
This requirement defines the word "confirmed" that *Machine-queryable confirmation state* and
*Human-gated mutation* use; an operator MUST NOT lock a row that cites `sources` unless the row
is confirmed by this standard. A discrete fact is one claim in the `reference` that can be true
or false by itself. The standard has seven parts:

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
6. **Evidence and lock hash come from the same content: re-ingest, then lock.** Before a lock
   batch, the operator MUST re-ingest each page that a row in the batch cites. The batch MUST
   take its per-fact evidence (part 5) from the chunks of that re-ingest. The lock step of
   *Machine-queryable confirmation state* stores `source_hashes` from its own fresh fetch of
   each page, while the evidence comes from the ingested chunks. If a page changed after an
   older ingest, the stored hash would describe the new page while the evidence describes the
   old one, so drift detection would never flag a fact that left the page before the lock. The
   re-ingest makes the two describe the same content.
7. **A cited page outside the ingested corpus.** A row whose cited page is not in the ingested
   corpus cannot be confirmed, and stays unlocked, until that page is ingested. There is no
   second evidence path: the live page, an external authority the KB never ingests, or a
   reviewer's own reading of the page does not confirm a fact.

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

#### Scenario: A lock batch re-ingests each cited page before it confirms

- **WHEN** an operator prepares a lock batch, and a row in the batch cites a page
- **THEN** the operator re-ingests that page before the batch confirms any fact against it
- **AND** the per-fact evidence of the batch (source URL, chunk index, quoted span) comes from the
  chunks of that re-ingest, so the content that the evidence describes is the content that the
  lock hashes

#### Scenario: A row that cites an uningested page is not confirmed

- **WHEN** a row cites a page that is not in the ingested corpus (for example an external
  authority that the KB never ingests)
- **THEN** the row cannot be confirmed, and stays unlocked
- **AND** the row can be confirmed only after that page is ingested; no other evidence path
  applies
