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

It reuses, and never reimplements, four library pieces:

* ``benchmark_schema.normalize_bank`` -- legacy ``question``/``answer`` rows onto
  ragas 0.3.5's ``user_input``/``reference``, exactly as the harness loads a bank;
* ``catalog._normalize_import_dialect`` -- the dialect mapping, the
  ``time_sensitive`` default, the content-derived ids, the alias+native refusal
  and the duplicate-row refusal;
* ``dataset.iter_dataset_items`` -- the reader that validates every normalized row;
* ``dataset.v2_json_document`` -- the ``qa-dataset-v2`` envelope the CLI reads.

The question set is the harness's own: bank rows plus the staged anchor file,
deduped on exact ``user_input`` with the bank row winning
(``service_benchmark.Benchmarker._merge_anchor_questions``). That is what makes
the ids here recomputable from a RAGAS artifact's ``question`` +
``reference_answer`` later, and what keeps the two runs comparable question for
question. On the FASRC bank it is 105 + 5 - 1 = 109 items.

Refusals are loud on purpose. A bank that cannot be converted honestly -- a row
spelling one concept twice, two rows that are the same question and answer, a row
with no reference, a file that is already a QA dataset -- is refused by name and
row number rather than mapped into a dataset that would score something nobody
authored.

Usage:
    python scripts/benchmarking/ragas_bank_to_qa_dataset.py <bank.json> \\
        --out fasrc.qa-v2.json [--anchors examples/benchmarking/anchor_questions.json]
    python scripts/benchmarking/ragas_bank_to_qa_dataset.py <bank.json> \\
        --no-anchors --status locked --out locked.qa-v2.json --json

    archi eval qa --dataset fasrc.qa-v2.json --agent-config <agent.yaml> ...

Exit codes: 0 converted, 1 usage or unreadable input, 2 refused (not a RAGAS
bank; a row carrying both dialect spellings; duplicate rows; a row the dataset
reader rejects).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.evaluation.qa.catalog import (  # noqa: E402  # isort: skip
    _normalize_import_dialect,
)
from src.evaluation.qa.dataset import (  # noqa: E402  # isort: skip
    DatasetItem,
    iter_dataset_items,
    v2_json_document,
)
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

# Mandatory on every native dataset row (``dataset._common_fields`` rejects a row
# without it) and carried by no bank row. A legacy bank and a headerless native
# dataset are otherwise spelled identically, so this is what tells them apart.
NATIVE_ONLY_FIELD = "time_sensitive"

DEFAULT_ANCHORS = Path(REPO_ROOT) / DEFAULT_ANCHOR_PATH


class UsageError(Exception):
    """The run cannot start: a missing, unreadable or misnamed file (exit 1)."""


class BankRefused(Exception):
    """The input cannot be converted honestly (exit 2)."""


def sha256_file(path: Path) -> str:
    """Digest of the written dataset, so a run can be pinned to its input."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path, *, what: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read {what} {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(f"{what} {path} is not valid JSON: {exc}") from exc


def _spells_one_concept_twice(row: Any) -> bool:
    """True when a row carries a legacy key AND its modern counterpart.

    ``normalize_record`` pops the legacy key and keeps the modern one, so this
    collision would vanish on the way to the adapter whose job is to refuse it
    by name. Detected from ``LEGACY_TO_MODERN`` itself, not from a second copy
    of the mapping.
    """
    return isinstance(row, dict) and any(
        legacy in row and modern in row for legacy, modern in LEGACY_TO_MODERN.items()
    )


def _modernized(rows: List[Any]) -> List[Any]:
    """``normalize_bank``, minus its silent drop of a colliding legacy key.

    A row spelling one concept twice is handed on verbatim for the dialect
    adapter to judge, instead of being quietly repaired here.
    """
    normalized = normalize_bank(rows)
    return [
        row if _spells_one_concept_twice(row) else new
        for row, new in zip(rows, normalized)
    ]


def load_bank(path: Path, *, what: str = "bank") -> List[Any]:
    """Read a bank (or anchor) file and put every row in the modern dialect."""
    document = _read_json(path, what=what)
    if not isinstance(document, list):
        raise BankRefused(
            f"{path} is not a RAGAS bank: a bank is a top-level JSON array of "
            "rows, and this file is not one (a qa-dataset-v1/v2 document is an "
            "object with a schema_version)"
        )
    for index, row in enumerate(document, 1):
        if isinstance(row, dict) and NATIVE_ONLY_FIELD in row:
            raise BankRefused(
                f"{path} is not a RAGAS bank: row {index} carries "
                f"'{NATIVE_ONLY_FIELD}', which every native dataset row must "
                "have and no bank row has -- this file is already a QA dataset"
            )
    return _modernized(document)


def merge_anchors(bank: List[Any], anchors: List[Any]) -> Tuple[List[Any], int, int]:
    """Splice the anchor rows into the bank the way the harness does.

    Mirrors ``Benchmarker._merge_anchor_questions``: dedupe on exact
    ``user_input`` against the bank, bank row wins, anchors keep their file
    order. Returns the merged rows, how many anchors were added, and how many
    were skipped (already in the bank, or carrying no question).
    """
    existing = {
        row.get("user_input")
        for row in bank
        if isinstance(row, dict) and row.get("user_input")
    }
    merged = list(bank)
    added = 0
    skipped = 0
    for anchor in anchors:
        if not isinstance(anchor, dict) or not anchor.get("user_input"):
            skipped += 1
            continue
        if anchor["user_input"] in existing:
            skipped += 1
            continue
        merged.append(anchor)
        added += 1
    return merged, added, skipped


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


def write_v2(normalized: Path, out: Path) -> int:
    """Write the ``qa-dataset-v2`` envelope, then re-read it and count.

    The re-read is the check that the file on disk is one the CLI will accept:
    every item is validated a second time by the same reader ``archi eval qa``
    uses, from the bytes that were actually written. A refusal part-way through
    leaves no partial file behind.
    """
    if out.suffix.lower() != ".json":
        raise UsageError(f"--out must be a .json file, got {out.name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    written = 0

    def counted() -> Iterator[DatasetItem]:
        nonlocal written
        for item in iter_dataset_items(normalized):
            written += 1
            yield item

    try:
        with partial.open("w", encoding="utf-8") as handle:
            for chunk in v2_json_document(counted()):
                handle.write(chunk)
        partial.replace(out)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    reread = sum(1 for _item in iter_dataset_items(out))
    if reread != written:
        raise RuntimeError(
            f"wrote {written} items to {out} but read back {reread}; "
            "the dataset was not written cleanly"
        )
    return written


def convert(
    bank_path: Path,
    anchors_path: Optional[Path],
    out: Path,
    statuses: Sequence[str],
) -> Dict[str, Any]:
    """Bank (+ anchors) -> a ``qa-dataset-v2`` file; returns the run report."""
    bank = load_bank(bank_path)
    merged, added, skipped = list(bank), 0, 0
    if anchors_path is not None:
        merged, added, skipped = merge_anchors(
            bank, load_bank(anchors_path, what="anchor file")
        )
    selected = filter_status(merged, statuses)
    with tempfile.TemporaryDirectory(prefix="ragas-bank-to-qa-") as scratch:
        try:
            normalized, dialect = normalize_rows(selected, Path(scratch))
            count = write_v2(normalized, out)
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
        "anchors_skipped": skipped,
        "status_filter": list(statuses),
        "dropped_by_status": len(merged) - len(selected),
        "items": count,
        "out": str(out),
        "sha256": sha256_file(out),
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
            f"{report['anchors_skipped']} already in the bank "
            f"({report['anchors']})"
        )
    if report["status_filter"]:
        lines.append(
            f"status filter {', '.join(report['status_filter'])}: dropped "
            f"{report['dropped_by_status']} rows"
        )
    lines.append(f"items written: {report['items']}")
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
        help=f"Anchor question file (default: {DEFAULT_ANCHOR_PATH}).",
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
