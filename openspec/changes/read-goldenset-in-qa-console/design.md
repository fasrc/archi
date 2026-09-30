# Design — reading the golden set in the QA console

## The decision that shapes everything: carry, don't ignore

The obvious fix is to delete `_strict_row_keys`. It is five lines, it is not a
spec requirement, and removing it makes the error go away.

It also produces the worst available outcome, for a reason that only shows up one
step later. The console writes datasets as well as reading them — reviewing
generated atoms saves an immutable child. `dataset_item_to_dict` builds that child
purely from `DatasetItem`'s fixed fields, so a key that passes a permissive
validator is still gone by the time the child is written. The operator imports one
bank, reviews atoms, saves, and now holds a dataset the benchmark cannot run,
with no error anywhere in the chain.

Demonstrated on the current code:

```
IN : {"id":"q1","question":"…","answer":"…","time_sensitive":false,"category":"storage"}
OUT: {"id":"q1","question":"…","answer":"…","time_sensitive":false,"category":"storage"}
known fields survive: True

with RAGAS fields -> ValueError: dataset row 0 has unknown field(s): notes, sources
```

Known fields round-trip cleanly; the machinery for preservation exists and simply
has no slot for anything else. So the requirement is not permissiveness, it is a
slot: parse extras into `DatasetItem.extra`, write them back out, and hash them.

## Why extras are hashed

The requirement is that two banks differing only in `sources` must not address to
the same dataset — otherwise a maintenance edit imports as `created: False`, and
the operator's change appears to succeed while doing nothing.

**Correction (found during implementation):** an earlier draft of this document
said `canonical_json` (`dataset.py:193`) produces the bytes the dedupe key is
derived from. That is wrong. `import_dataset` hashes the uploaded blob directly —
`digest = _sha256(blob)` (`catalog.py:467`, `_sha256` at `:121`). The requirement
still holds, but by a different route: the dialect adapter re-serializes the bank
canonically (sorted keys, compact separators) with extras included, and the digest
is taken over those normalized bytes. The integrity manifest forces this anyway —
the stored `metadata.sha256` must equal the hash of the stored source file. Pinned
from both sides: a sources-only edit yields `created: True`, and a byte-identical
re-import dedupes.

## Two structural findings from implementation

**There is a second emitter.** Extending `dataset_item_to_dict` is not sufficient.
An imported bank lands as a **legacy (V1)** dataset, and approved children of legacy
parents are published through `_dataset_row` (`catalog.py:125`, called at `:216`),
not through `dataset_item_to_dict`. Miss it and the headline acceptance failure —
the reviewed child losing `sources` — survives every other part of this change.
The spec requirement is written as "whenever the console writes a dataset," which
already covers both paths; this note exists so the next reader does not assume one
emitter.

**The normalize target is the V1 headerless array, not a V2 container.** This is
forced by the harness, not a preference: `_load_bank_file`
(`src/utils/benchmark_schema.py:319`) ends `return bank if isinstance(bank, list)
else None`, so a V2-container child would silently load as `None` and stop being a
valid RAGAS bank. Since the whole proposal rests on "a reviewed child is still a
valid bank," the array shape is load-bearing. Child rows carry `question`/`answer`,
which the harness's existing legacy-to-modern shim maps back on read.

## Why the rename lives at the import boundary, not in the parser

Two dialects, one meaning: `user_input` and `question` are the same concept. The
parser could learn both names, but then every future reader has two spellings to
handle forever, and the "canonical schema" claim quietly becomes false.

`import_dataset` already takes raw bytes before validation and is the only entry
point for operator-supplied files, so it is where dialect knowledge belongs. It
also matches the precedent: `align-ragas-benchmark-dialect` solved the mirror-image
problem with a normalize-on-read shim rather than teaching the harness two
schemas.

Detection keys on `user_input`, because that is the field the RAGAS harness itself
treats as mandatory, and no Dataset V2 row has it.

## Why near-miss keys are refused instead of carried

Carrying unknown keys re-opens the typo hole that strict checking closed, and the
consequences are unequal across fields. Most of the allowlist is optional, and for
`expected_atoms` "absent" is not neutral — it is the switch that hands atom
authorship to the LLM extractor. A reviewer who writes `expectd_atoms` would get a
green run scored against inferred obligations instead of their approved ones.

So: keys within edit distance 1 of a known field are rejected with a "did you
mean" message; everything else is carried. The bank's real extras — `sources`,
`notes`, `status`, `anchor_type`, `source_match_field` — are nowhere near a known
name, so they pass. This keeps the property that made strictness worth having,
while dropping the part that blocked the bank.

## Alternatives considered

- **Convert the bank to Dataset V2 and keep two files.** Simplest, no code change.
  Rejected: it is the status quo the change exists to remove, and a generated file
  still drifts the moment someone edits the wrong copy.
- **Blanket leniency, no `extra` slot.** Rejected above: accepts the bank and then
  loses fields on the first reviewed child, converting a loud failure into a silent
  one inside an evidence rig.
- **Teach the parser both dialects via an alias table.** Rejected: two permanent
  names per concept, spread through the core parser, to serve one external file
  shape. The boundary adapter contains the same knowledge in one place.
- **A reserved container key (`extra: {...}`) the author writes explicitly.**
  Cleaner in the abstract, and it is what a greenfield schema should do. Rejected
  here because it requires editing all 110 bank rows and breaks the external
  `fasrc/ragas-json-editor` tool that authors them — the file has to stay
  drop-in for the editor.
- **Score `sources` in the console.** Out of scope and arguably wrong: the console's
  contract is gold atoms. Making it a second source-accuracy scorer duplicates
  RAGAS and gives two subtly different numbers for one property.
