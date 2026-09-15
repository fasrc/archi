# Spec delta — qa-evaluation-trial

## ADDED Requirements

### Requirement: Dataset rows carry unrecognized fields through unchanged

A dataset row MAY carry fields outside the known set, and those fields SHALL
survive the full round trip: parsed into the item model, and re-emitted unchanged
whenever the console writes a dataset.

Carrying is required rather than ignoring because the console writes datasets as
well as reading them — approving generated atoms saves an immutable child dataset,
built from the item model rather than from the imported bytes. A field that is
accepted at validation but absent from the model is therefore silently dropped
from every derived dataset. For a shared question bank that is worse than a
refusal: the operator imports one file, reviews atoms, saves, and holds a child the
benchmark can no longer run, with no error raised anywhere.

Carried fields SHALL NOT influence evaluation. Nothing in preparation, running or
scoring may read them. They are data in transit on behalf of another consumer, and
the console's own contract stays exactly the gold-atom scoring it already
performs.

Carried fields SHALL contribute to the dataset's canonical serialization and
therefore to its content hash. The catalog dedupes imports by that hash, so two
banks whose only difference lives in carried fields — the normal result of a
maintenance edit to sources or notes — MUST NOT address as the same dataset. Were
they excluded, such an edit would import as an existing dataset and the operator's
change would appear to succeed while doing nothing.

#### Scenario: An unrecognized field survives a round trip

- **WHEN** a dataset row carrying fields outside the known set is imported, and the
  console later writes a dataset derived from it
- **THEN** those fields appear in the written dataset with their original values

#### Scenario: Carried fields do not affect scoring

- **WHEN** two datasets are identical except for their carried fields
- **THEN** preparation, running and scoring behave identically for both

#### Scenario: Carried fields change the content hash

- **WHEN** two datasets differ only in a carried field
- **THEN** they produce different canonical serializations, and importing the second
  creates a new dataset rather than resolving to the first

### Requirement: Near-miss field names are refused rather than carried

A row key that is not a known field but is within edit distance 1 of one SHALL be
rejected with an error naming the probable intended field, instead of being carried
as an unrecognized field.

Carrying every unknown key would reopen the typo hole that strict validation
closed, and the consequences are not uniform across the allowlist. Most known
fields are optional, and for `expected_atoms` absence is not neutral — it is the
switch that hands atom authorship to the extractor model. A reviewer who writes
`expectd_atoms` would otherwise get a run that completes and reports success while
scoring against LLM-inferred obligations rather than the reviewed ones they
supplied. The failure is invisible in the result.

The genuine extras a shared question bank carries are not near-misses of any known
field, so this rule refuses typos without refusing the bank.

#### Scenario: A misspelled known field is refused

- **WHEN** a row carries a key within edit distance 1 of a known field
- **THEN** the import fails with an error naming both the offending key and the
  probable intended field

#### Scenario: A genuinely unrelated field is carried

- **WHEN** a row carries a key that is not close to any known field
- **THEN** it is carried as an unrecognized field and the import succeeds

### Requirement: The importer accepts the RAGAS question-bank dialect

Dataset import SHALL accept a question bank written in the RAGAS dialect the
benchmark harness already consumes, without the operator converting the file.

The importer SHALL detect that dialect by the presence of `user_input`, which the
harness treats as the one mandatory field and which no native dataset row carries.
On detection it SHALL map `user_input` to the question and `reference` to the
canonical answer, synthesize a stable identifier for rows lacking one, and treat
rows as static unless declared otherwise. Every remaining field is carried under
the requirement above.

The mapping SHALL live at the import boundary and not in the row parser. The parser
keeps exactly one name per concept; teaching it a second spelling would make the
canonical schema claim false and oblige every future reader to handle both. This
also mirrors the normalize-on-read shim the benchmark harness already uses for the
mirror-image case.

The import result SHALL report which dialect was detected and which field names
were carried rather than interpreted. An operator importing a file the console
partly misunderstood must be able to see that from the result, not infer it from a
later scoring anomaly.

#### Scenario: A RAGAS-dialect bank imports unconverted

- **WHEN** a question bank whose rows carry `user_input` and `reference` is imported
- **THEN** it is accepted, each row's question and canonical answer are taken from
  those fields, and the remaining fields are carried

#### Scenario: The import result names what it did

- **WHEN** a bank is imported in a recognized non-native dialect
- **THEN** the result identifies the dialect and lists the carried field names

#### Scenario: A native dataset is unaffected

- **WHEN** a dataset already in the native schema is imported
- **THEN** no dialect mapping is applied and it behaves exactly as before
