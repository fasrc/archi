## ADDED Requirements

### Requirement: Retriever scores reaching the citation layer are similarities

The citation layer SHALL treat a retriever score as a similarity in the range `0..1` where a higher value means more relevant, and SHALL state that convention in the docstring of `format_citations` and wherever the `retriever_scores` metadata key is described.

Every producer that reaches the citation layer under the default `cosine` metric returns
`1.0 - distance` — a similarity. `hybrid_search` likewise orders by `combined_score`
descending, and the `hybrid-search-scoring` capability requires a normalized, higher-is-better
score. The consumers are the only place the opposite convention survives, and it survives as
prose in a docstring as much as in code, so the docstring is part of the contract rather than
a comment on it.

This holds under every supported distance metric, not just `cosine`. `1.0 - distance` is
monotonically decreasing in distance for `<=>`, `<->` and `<#>` alike, so every producer
returns a higher-is-better score and the consumers need no metric-aware branch.

What the score is *measured in* still varies — only `cosine` yields a bounded range, and the
hybrid path returns a weighted blend including an unbounded BM25 term — so the ordering is
metric-independent while the threshold's calibration is not. That is why the floor ships
disabled.

#### Scenario: The stated convention matches the code

- **WHEN** the `format_citations` docstring is read
- **THEN** it describes scores as similarities where higher is more relevant
- **AND** it does not describe them as distances

### Requirement: Cited sources are ordered most relevant first

`format_citations` and `get_top_sources` SHALL order sources by descending score, so that the most relevant source appears first in a citation block and first in the list of top sources.

Both currently sort ascending, which puts the least relevant source at the top of what the
user reads. In `format_citations` the user also sees a `(relevance: N.NN)` figure beside each
source, so the ordering and the number visibly disagree.

#### Scenario: A higher-scoring source is cited before a lower-scoring one

- **WHEN** two sources with distinct scores are formatted into a citation block
- **THEN** the source with the higher score appears first

#### Scenario: The top-sources list leads with the best source

- **WHEN** `get_top_sources` is given documents and their scores
- **THEN** the returned list leads with the highest-scoring document
- **AND** the remaining entries follow in descending score order

### Requirement: Deduplicating a repeated source keeps its highest score

When one display name appears more than once, `format_citations` SHALL keep the highest of its scores, and SHALL continue to prefer any real score over the no-score sentinel.

The dedup comparison keeps the lowest score today, so a source retrieved once strongly and
once weakly is represented by its weakest chunk — both in the figure shown to the user and in
the position it sorts to.

#### Scenario: The best of several chunks represents a source

- **WHEN** the same display name is supplied with several different real scores
- **THEN** the citation for that name shows the highest of them

#### Scenario: A real score still beats the sentinel

- **WHEN** the same display name is supplied once with a real score and once with `-1.0`
- **THEN** the citation for that name shows the real score, regardless of the order supplied

### Requirement: The no-score sentinel is never ranked as a relevance score

A score of `-1.0` SHALL mean "no score available", SHALL sort after every real score, SHALL render without a `(relevance: …)` suffix, and SHALL never be compared against the relevance threshold.

Reversing the sort direction is exactly the change that could promote `-1.0` from worst to
best, because it is numerically the lowest value present. In `format_citations` a tuple sort
key already partitions the sentinel from real scores and only the second element's direction
changes. In `get_top_sources` no such partition exists — the sentinel's position is a side
effect of the sort — so it needs pinning by test rather than by inspection.

#### Scenario: Sentinel entries follow real scores after the sort reverses

- **WHEN** sources are formatted from a mix of real scores and `-1.0` sentinels
- **THEN** every real-scored source appears before every sentinel source
- **AND** the real-scored sources are themselves ordered highest first

#### Scenario: A sentinel renders no relevance figure

- **WHEN** a source's only score is `-1.0`
- **THEN** its citation line carries no `(relevance: …)` suffix

#### Scenario: A sentinel is not filtered by the threshold

- **WHEN** `get_top_sources` encounters a document scored `-1.0`
- **THEN** the relevance threshold is not applied to it
- **AND** it is not used as grounds to stop reading further documents

### Requirement: The relevance threshold is a similarity floor

`get_top_sources` SHALL treat `similarity_score_reference` as a minimum similarity, stopping at the first source scoring below it, and a configured value at or below `0.0` SHALL disable the floor entirely so that no source is filtered unless an operator opts in.

The shipped default is `0.0`, and "no floor" has to mean *no comparison*, not a comparison
against zero. A cosine similarity is `1.0 - distance` over a 0..2 distance, so it runs down to
-1.0: applied literally, a `0.0` floor would drop an anti-correlated source and — because the
list is ordered best-first — every source after it, which is filtering that no operator asked
for. A negative floor is therefore not expressible; that is deliberate, since the values below
zero are exactly the ones an operator cannot calibrate across the metric and hybrid scales.

#### Scenario: A source scoring below zero is still cited under the default

- **WHEN** the shipped default is in effect and a retrieved source scores below `0.0`
- **THEN** that source is cited
- **AND** so is every source retrieved after it

The comparison is a ceiling today (`score > threshold`), which is the distance reading. Its
default of `10` made it inert, because a cosine similarity never approaches `10` — so the
guard has never actually filtered anything, and the default must change in the same breath as
the comparison to keep it that way.

Stopping at the first source below the floor, rather than skipping it and continuing, is
correct because the list is ordered best-first: everything after it scores no higher.

#### Scenario: The default filters nothing

- **WHEN** sources are retrieved on a deployment using the shipped default threshold
- **THEN** no source is dropped for scoring too low

#### Scenario: An operator-set floor drops weak sources

- **WHEN** the threshold is set to a value inside `0..1` and a retrieved source scores below it
- **THEN** that source is not cited
- **AND** no lower-scoring source is cited either

### Requirement: A threshold that cannot be a similarity is ignored rather than obeyed

A configured `similarity_score_reference` greater than `1.0` SHALL be treated as no floor at all, and SHALL cause a warning naming the configured value to be logged once at startup.

A similarity cannot exceed `1.0`, so such a value can only be a distance ceiling left over
from the convention this change retires. Obeyed as a floor it filters every source and the
response cites nothing — a strictly worse outcome than the inert guard it replaces, and one
that appears as silently missing citations rather than as an error.

This matters because deployments read a configuration fetched at deploy time from outside this
repository. Updating the shipped template cannot reach them, so a live distance-era threshold
will outlive this change; the writer is the only place that can defend against it. A value of
exactly `1.0` is left alone: it is a degenerate but coherent floor meaning "cite only an exact
match", and refusing it would override a legal operator choice.

#### Scenario: A distance-era threshold does not silence citations

- **WHEN** a deployment's configuration still sets the threshold to a distance-era value such as `10`
- **THEN** sources are cited exactly as they would be with no floor configured
- **AND** a warning naming the configured value is logged

#### Scenario: A coherent strict floor is still honoured

- **WHEN** the threshold is configured as exactly `1.0`
- **THEN** it is applied as a floor and not discarded
