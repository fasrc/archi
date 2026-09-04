#!/usr/bin/env python
"""Convert the RAGAS golden-set bank into a ``qa-dataset-v2`` file for ``archi eval qa``.

The #396 feature matrix wants the gold-atoms QA evaluator and the RAGAS harness
to answer the SAME questions on the same stack, so their verdicts can be read
side by side. They cannot today: the QA dataset loader refuses RAGAS-dialect rows
outright (``src/evaluation/qa/dataset.py`` -- ``user_input`` and ``reference``
are a blocklist, not an alias table, because the harness prefers those names and
a carried alias would let one file score as different content in each stack), and
the one adapter that maps the dialect lives behind the browser import path
(``EvaluationCatalog.import_dataset``). This script is the CLI door to that same
adapter.

It reuses, and never reimplements, six library pieces:

* ``benchmark_schema.normalize_bank`` -- legacy ``question``/``answer`` rows onto
  ragas 0.3.5's ``user_input``/``reference``, exactly as the harness loads a bank;
* ``catalog._iter_json_array_rows`` + ``catalog._exact_json_numbers`` -- the
  strict read: a repeated object key and a number binary floats cannot hold are
  refused instead of silently collapsed and rounded;
* ``catalog._normalize_import_dialect`` -- the dialect mapping, the
  ``time_sensitive`` default, the content-derived ids and the duplicate-row
  refusal (the double-spelling refusal is raised here first, because the adapter
  knows only the question and answer aliases);
* ``dataset.iter_dataset_items`` -- the reader that validates every normalized row;
* ``dataset.v2_json_document`` -- the ``qa-dataset-v2`` envelope the CLI reads;
* ``artifacts.AtomicTextWriter`` -- the staged, rename-on-success write every
  other QA artifact already uses.

The question set is the harness's own: bank rows plus the staged anchor file,
deduped on exact ``user_input`` with the bank row winning
(``service_benchmark.Benchmarker._merge_anchor_questions``). That is what makes
the ids here recomputable from a RAGAS artifact's ``question`` +
``reference_answer`` later, and what keeps the two runs comparable question for
question. On the FASRC bank it is 105 + 5 - 1 = 109 items.

Three caveats on that join, none of which bite the FASRC bank today:

* the derivation folds CRLF and bare CR to LF while a RAGAS artifact stores the
  question and reference verbatim, so recompute the id from newline-normalized
  text;
* a bank row that carries its own ``id`` keeps it, so its item id is authored
  rather than derived and the item has to be matched by question text instead --
  the run report counts those rows as ``explicit_ids``, and it is 0 here;
* the anchors are whatever this command was told to use. It does not read the
  deployment configuration, so a run with ``services.benchmarking.anchors``
  disabled or repointed needs ``--no-anchors`` or ``--anchors <path>`` to match.

Refusals are loud on purpose. A bank that cannot be converted honestly -- a row
spelling one concept twice, two rows that are the same question and answer, a row
with no reference, a file that is already a QA dataset container -- is refused by
name and row number rather than mapped into a dataset that would score something
nobody authored. Refusals are also narrow: where a file is genuinely ambiguous
(a headerless array is a legacy bank and a V1 dataset at once) it is converted,
because guessing could only refuse valid input.

Usage:
    python scripts/benchmarking/ragas_bank_to_qa_dataset.py <bank.json> \\
        --out fasrc.qa-v2.json [--anchors examples/benchmarking/anchor_questions.json]
    python scripts/benchmarking/ragas_bank_to_qa_dataset.py <bank.json> \\
        --no-anchors --status locked --out locked.qa-v2.json --json

    archi eval qa --dataset fasrc.qa-v2.json --agent-config <agent.yaml> ...

Exit codes: 0 converted, 1 the run cannot start (bad flags, a file that cannot be
read, an ``--out`` that is not ``.json`` or that would overwrite an input),
2 refused (not a RAGAS bank; malformed JSON; a repeated object key; a number that
cannot be carried exactly; a row carrying both dialect spellings; duplicate rows;
a row the dataset reader rejects).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.evaluation.qa.artifacts import (  # noqa: E402  # isort: skip
    AtomicTextWriter,
    copy_file_atomic,
    sha256_file,
)
from src.evaluation.qa.catalog import (  # noqa: E402  # isort: skip
    _exact_json_numbers,
    _iter_json_array_rows,
    _normalize_import_dialect,
)
from src.evaluation.qa.dataset import (  # noqa: E402  # isort: skip
    DatasetItem,
    _first_non_whitespace,
    iter_dataset_items,
    v2_json_document,
)
from src.evaluation.qa.oracle import validate_json_value  # noqa: E402  # isort: skip
from src.utils.benchmark_schema import (  # noqa: E402  # isort: skip
    DEFAULT_ANCHOR_PATH,
    LEGACY_TO_MODERN,
    REPO_ROOT,
    normalize_bank,
    row_status,
)

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSED = 2

DEFAULT_ANCHORS = Path(REPO_ROOT) / DEFAULT_ANCHOR_PATH


class UsageError(Exception):
    """The run cannot start: a missing, unreadable or misnamed file (exit 1)."""


class BankRefused(Exception):
    """The input cannot be converted honestly (exit 2)."""


def _read_bytes(path: Path, *, what: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read {what} {path}: {exc}") from exc


def _validated(row: Any, context: str) -> Any:
    """One row, with the checks the adapter would apply -- applied earlier.

    ``_exact_json_numbers`` refuses a number binary floats cannot hold, and
    ``validate_json_value`` refuses a NUL or a lone surrogate. Both run in the
    adapter too, but only after this script has re-encoded the merged rows as
    UTF-8 -- and a lone surrogate crashes that encode first, with a codec error
    that names a byte offset and no field. Running them here means the refusal
    names the row and the key instead.
    """
    row = _exact_json_numbers(row, context)
    validate_json_value(row, context)
    return row


def _refuse_double_spellings(rows: List[Any], what: str) -> None:
    """Refuse any row that carries a legacy key AND its modern counterpart.

    ``normalize_record`` pops the legacy key and keeps the modern one, so the
    harness would score the row having silently discarded half of it. The
    dialect adapter refuses the question and answer pair by name, but it knows
    nothing of ``contexts``/``retrieved_contexts`` and would carry both as
    extras -- so the check belongs here, over ``LEGACY_TO_MODERN`` itself rather
    than a second copy of the mapping. Wording mirrors the adapter's.
    """
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        for legacy, modern in LEGACY_TO_MODERN.items():
            if legacy in row and modern in row:
                raise BankRefused(
                    f"{what} row {index} carries both '{legacy}' and "
                    f"'{modern}'; keep exactly one"
                )


def load_bank(path: Path, *, what: str = "bank") -> List[Any]:
    """Read a bank (or anchor) file and put every row in the modern dialect.

    Only an unambiguously native container is refused: a bank is a top-level
    JSON array, so an object with a ``schema_version`` -- the shape this script
    itself writes, and the file an operator is most likely to hand back by
    mistake -- is not one. A headerless array is NOT second-guessed: a legacy
    bank and a V1 dataset are the same bytes (``question``/``answer``, no schema
    version), nothing in the file separates them, and converting reproduces a
    native row exactly, so guessing could only ever refuse valid input.

    Parsed by the import path's own strict reader rather than ``json.loads``: a
    repeated object key and a number no binary float can hold are both refused
    there, while ``json.loads`` would silently keep the last spelling of the key
    and round the number. Reading the bank leniently and re-serializing it would
    hand the adapter a document the author never wrote, and the two entry points
    would stop carrying identical content.
    """
    blob = _read_bytes(path, what=what)
    _refuse_non_array_root(path)
    try:
        document = [
            _validated(row, f"{what} row {index}")
            for index, row in enumerate(_iter_json_array_rows(blob), 1)
        ]
    except ValueError as exc:
        raise BankRefused(f"{path}: {exc}") from exc
    if not document:
        raise BankRefused(f"{path} is not a RAGAS bank: it has no rows")
    _refuse_double_spellings(document, what)
    return normalize_bank(document)


def _refuse_non_array_root(path: Path) -> None:
    """Refuse anything whose root token is not ``[``.

    ``_iter_json_array_rows`` selects rows by the streaming path ``item``, which
    a top-level OBJECT member named ``item`` also produces -- so without this
    check ``{"metadata": ..., "item": {...}}`` would convert as a one-row bank
    and every other field would vanish. The root token is read from the same
    helper the dataset container check uses.
    """
    try:
        root = _first_non_whitespace(path)
    except ValueError as exc:
        raise BankRefused(f"{path} is not a RAGAS bank: {exc}") from exc
    if root != ord("["):
        raise BankRefused(
            f"{path} is not a RAGAS bank: a bank is a top-level JSON array of "
            "rows, and this file's root is not one (a qa-dataset-v1/v2 document "
            "is an object with a schema_version)"
        )


def merge_anchors(
    bank: List[Any], anchors: List[Any]
) -> Tuple[List[Any], int, int, int]:
    """Splice the anchor rows into the bank the way the harness does.

    Mirrors ``Benchmarker._merge_anchor_questions``: dedupe on exact
    ``user_input`` against the bank, bank row wins, anchors keep their file
    order. Returns the merged rows, how many anchors were added, how many were
    already asked by the bank, and how many were unusable (not an object, or
    carrying no question).

    The harness skips an unusable anchor too, so tolerating it keeps the two
    question sets identical -- but the two skip reasons are counted apart,
    because a broken row in the anchor file and a deliberate duplicate look the
    same in a total and mean very different things to whoever is auditing why
    the question count moved.
    """
    existing = {
        row.get("user_input")
        for row in bank
        if isinstance(row, dict) and row.get("user_input")
    }
    merged = list(bank)
    added = 0
    duplicate = 0
    unusable = 0
    for anchor in anchors:
        if not isinstance(anchor, dict) or not anchor.get("user_input"):
            unusable += 1
            continue
        if anchor["user_input"] in existing:
            duplicate += 1
            continue
        merged.append(anchor)
        added += 1
    return merged, added, duplicate, unusable


def filter_status(rows: Iterable[Any], statuses: Sequence[str]) -> List[Any]:
    """Keep rows whose confirmation status is one of ``statuses``.

    ``row_status`` is the bank's own reader: ``locked`` only when the field says
    exactly that, ``draft`` for absent or anything else. An empty selection keeps
    every row.
    """
    if not statuses:
        return list(rows)
    wanted = set(statuses)
    return [row for row in rows if row_status(row) in wanted]


def normalize_rows(rows: List[Any], scratch: Path) -> Tuple[Path, Dict[str, Any]]:
    """Run the rows through the catalog's dialect adapter into ``scratch``.

    Returns the normalized headerless-array file and the adapter's report
    (``import_dialect`` and the carried field names). A ``None`` report means the
    adapter did not recognize the dialect at all.
    """
    destination = Path(scratch) / "normalized.json"
    blob = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    report = _normalize_import_dialect(blob, "json", destination)
    if report is None:
        raise BankRefused(
            "not a RAGAS bank: no row carries 'user_input' (or the legacy "
            "'question') -- there is nothing to convert"
        )
    return destination, report


def write_v2(normalized: Path, out: Path, scratch: Path) -> Tuple[int, str]:
    """Build the envelope in ``scratch``, publish it, return (items, sha256).

    The envelope is written and then re-read *in the scratch directory*, by the
    same reader ``archi eval qa`` uses, from the bytes that were actually
    written. Only a file that reads back cleanly is published, so a bank the
    dataset reader refuses (a live row with no oracle, say) never lands at
    ``--out`` and never replaces a good dataset from an earlier run.

    Both writes go through the project's own atomic artifact helpers, which
    stage under a uniquely named hidden sibling and rename into place only on a
    clean exit: nothing is left behind by a refusal, and two conversions aimed
    at one ``--out`` cannot truncate or publish each other's bytes.
    """
    if out.suffix.lower() != ".json":
        raise UsageError(f"--out must be a .json file, got {out.name}")
    staged = scratch / out.name
    written = 0

    def counted() -> Iterator[DatasetItem]:
        nonlocal written
        for item in iter_dataset_items(normalized):
            written += 1
            yield item

    with AtomicTextWriter(staged) as handle:
        for chunk in v2_json_document(counted()):
            handle.write(chunk)
    reread = sum(1 for _item in iter_dataset_items(staged))
    if reread != written:
        raise RuntimeError(
            f"wrote {written} items to {staged} but read back {reread}; "
            "the dataset was not written cleanly"
        )
    # Digest the staged file, not ``out``: with two conversions aimed at one
    # output, a hash taken after publishing could describe whichever run
    # published last, and the report would pair this run's counts with another
    # run's bytes.
    digest = sha256_file(staged)
    copy_file_atomic(staged, out)
    return written, digest


def _refuse_to_clobber_an_input(
    out: Path, bank_path: Path, anchors_path: Optional[Path]
) -> None:
    """Never let the dataset be written over a file it was converted from.

    The bank is a maintained, human-authored artifact and the anchor file is
    tracked in the repo; both are read into memory before anything is written,
    so ``--out <bank>`` would replace the source with the dataset and lose it.
    Compared resolved, so an indirect spelling of the same file is caught too.
    """
    destination = out.resolve()
    inputs = [("bank", bank_path)]
    if anchors_path is not None:
        inputs.append(("anchor file", anchors_path))
    for what, path in inputs:
        if destination == path.resolve():
            raise UsageError(
                f"--out {out} would overwrite the {what} it reads ({path}); "
                "write the dataset somewhere else"
            )


def convert(
    bank_path: Path,
    anchors_path: Optional[Path],
    out: Path,
    statuses: Sequence[str],
) -> Dict[str, Any]:
    """Bank (+ anchors) -> a ``qa-dataset-v2`` file; returns the run report."""
    _refuse_to_clobber_an_input(out, bank_path, anchors_path)
    bank = load_bank(bank_path)
    merged, added, duplicate, unusable = list(bank), 0, 0, 0
    if anchors_path is not None:
        merged, added, duplicate, unusable = merge_anchors(
            bank, load_bank(anchors_path, what="anchor file")
        )
    selected = filter_status(merged, statuses)
    with tempfile.TemporaryDirectory(prefix="ragas-bank-to-qa-") as scratch:
        try:
            normalized, dialect = normalize_rows(selected, Path(scratch))
            count, digest = write_v2(normalized, out, Path(scratch))
        except ValueError as exc:
            # The adapter and the dataset reader both refuse by raising
            # ValueError with a message that names the row.
            raise BankRefused(str(exc)) from exc
    return {
        "bank": str(bank_path),
        "anchors": str(anchors_path) if anchors_path is not None else None,
        "import_dialect": dialect["import_dialect"],
        "carried_fields": dialect["carried_fields"],
        "bank_rows": len(bank),
        "anchors_added": added,
        "anchors_skipped": duplicate,
        "anchors_unusable": unusable,
        "status_filter": list(statuses),
        "dropped_by_status": len(merged) - len(selected),
        "items": count,
        # Rows whose id was authored, not derived: those items cannot be matched
        # to a RAGAS artifact by recomputing the id from question + reference.
        "explicit_ids": sum(
            1 for row in selected if isinstance(row, dict) and row.get("id")
        ),
        "out": str(out),
        "sha256": digest,
    }


def format_report(report: Dict[str, Any]) -> str:
    lines = [
        f"dialect: {report['import_dialect']}",
        f"carried fields: {', '.join(report['carried_fields']) or '(none)'}",
        f"bank rows: {report['bank_rows']} ({report['bank']})",
    ]
    if report["anchors"] is None:
        lines.append("anchors: none (--no-anchors)")
    else:
        lines.append(
            f"anchors: {report['anchors_added']} added, "
            f"{report['anchors_skipped']} already in the bank, "
            f"{report['anchors_unusable']} unusable "
            f"({report['anchors']})"
        )
    if report["status_filter"]:
        lines.append(
            f"status filter {', '.join(report['status_filter'])}: dropped "
            f"{report['dropped_by_status']} rows"
        )
    lines.append(f"items written: {report['items']}")
    lines.append(
        f"rows carrying an authored id: {report['explicit_ids']} "
        "(the rest join to a RAGAS run by derived id)"
    )
    lines.append(f"out: {report['out']}")
    lines.append(f"sha256: {report['sha256']}")
    return "\n".join(lines)


class _Parser(argparse.ArgumentParser):
    """argparse, with usage errors on this script's own exit-1 contract."""

    def error(self, message: str) -> None:  # pragma: no cover - argparse plumbing
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        description=(
            "Convert a RAGAS golden-set question bank (plus the anchor "
            "questions) into a qa-dataset-v2 file that `archi eval qa` accepts."
        )
    )
    parser.add_argument("bank", type=Path, help="RAGAS question bank (JSON array).")
    anchors = parser.add_mutually_exclusive_group()
    anchors.add_argument(
        "--anchors",
        type=Path,
        default=DEFAULT_ANCHORS,
        help=(
            f"Anchor question file (default: {DEFAULT_ANCHOR_PATH}). Mirror the "
            "run's services.benchmarking.anchors setting; this script does not "
            "read the deployment configuration."
        ),
    )
    anchors.add_argument(
        "--no-anchors",
        action="store_true",
        help="Convert the bank alone, without the anchor questions.",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="Destination .json dataset."
    )
    parser.add_argument(
        "--status",
        action="append",
        dest="statuses",
        choices=["draft", "locked"],
        help="Keep only rows with this confirmation status; repeatable.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the run report as JSON instead of text.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = convert(
            args.bank,
            None if args.no_anchors else args.anchors,
            args.out,
            args.statuses or [],
        )
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except BankRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    print(json.dumps(report, indent=2) if args.as_json else format_report(report))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
