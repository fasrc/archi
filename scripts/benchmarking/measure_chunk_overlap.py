#!/usr/bin/env python
"""Measure the overlap the ingest's chunking ACTUALLY carries across chunk boundaries.

``chunk_overlap`` is a *budget*, not a guarantee. ``SentenceSplitter`` copies back
only whole sentences that fit inside it, so a budget smaller than a typical
sentence (15-30 tokens on the FASRC corpus) frequently carries nothing at all.
The configured number therefore says very little about the continuity a chunking
setting really provides — you have to measure it on real text.

This script does that: for each candidate overlap it reports how many boundaries
carry nothing, how many tokens the typical boundary actually repeats, and what
the setting costs in duplicated embedded tokens (index inflation).

Written for the v2026.10.0 feature-matrix campaign (#396) and the configurable
`chunking.chunk_overlap` key (#403), so a value gets picked from numbers rather
than from convention.

What is measured — the production chunking, not a stand-in. Each loader
document runs through the same two-level ``HierarchicalNodeParser`` the
ingest's ``sentence`` strategy builds (``src/data_manager/vectorstore/
node_parsing.py``, ``_parse_sentence``): parents of ``--parent-chunk-size``
tokens, children of ``--chunk-size`` tokens, one overlap budget applied at both
levels, clamped to the smaller size exactly like ``_clamped_overlap``. The
document's metadata is replayed too, because the splitter subtracts the
metadata string's tokens from every level's budget — with the loader's metadata
(``source``, plus the PDF keys for a PDF page) the re-chunk reproduces the
stored children byte for byte; with the enriched metadata stored on the parent
rows it produces a third more, smaller chunks. The boundaries counted are those
between consecutive *child* chunks of one loader document — the rows that get
embedded — including the boundary where one parent ends and the next begins.
When the dump carries the stored children, the run also reports how many of
them the re-chunk reproduced at the ingest's own overlap.

How the carried text is found — from source offsets, not string inference. The
splitter emits every chunk as a verbatim substring of its document whose end
never moves backwards, so each chunk is located by a forward search that
accepts only occurrences ending no earlier than the previous chunk, and the
text carried across a boundary is exactly ``previous.end - following.start``
characters. This matters: a scraped page
often repeats a block verbatim (a navigation menu rendered twice, a page stored
several times), and a plain suffix/prefix string match then reports a long
"overlap" the splitter never copied. The search rejects any occurrence that
would imply more carried tokens than the budget allows, which is why
``chunk_overlap=0`` stays in the default sweep as the control row: it must read
0 at every boundary.

The whitespace-normalized, character-level string matcher remains as the
fallback for the rare chunk the splitter did not emit verbatim (when it must
cut a sentence longer than the chunk budget, its regex pass drops repeated
punctuation). Characters rather than token ids, because a boundary that falls
mid-token — ``https://github.`` | ``com/fasrc/...`` — re-tokenizes differently
on each side and a token-sequence comparison would report zero. Boundaries that
needed the fallback are counted per row (``fallback_boundaries``).

Corpus source: the ``documents`` table stores no content, so
``dump_chunk_overlap_corpus.sql`` (next to this script) emits one JSON record
per loader document from the parents the ingest stored — only parents
referenced by a live chunk of the target collection, because
``document_parent_nodes`` keeps the parents of every past ingest run. Each
record carries the file's path under the data directory, the children the
ingest stored, and a reconstruction of the text and metadata: parents that
share a document and metadata belong to one loader document (a PDF page, a
file), their text is re-joined in ``parent_index`` order, and the metadata is
projected down to what the loader attached.

Prefer ``--data-root``: with a copy of the data manager's data directory, every
record's file is re-read with the ingest's own loader, so text and metadata are
exactly what production chunked — on the claw KB the re-chunk then reproduces
all 6088 stored children byte for byte. The reconstruction is the fallback and
is biased: a parent boundary that fell inside a sentence gains a break the
original never had, so the re-chunk copies overlap more often than the ingest
did (claw KB, overlap 20: 42.9% empty boundaries reconstructed, 39.6% from the
files) and reproduces only 76% of the stored children. Every run prints that
share at the ingest's own overlap, so the fidelity is measured, not assumed.

Usage:
    # one JSON record per loader document: {"text", "metadata", "path", "children"}
    docker exec -i postgres-dev psql -U archi -d archi-db -t -A \\
      -v collection=default_collection_with_HuggingFaceEmbeddings \\
      < scripts/benchmarking/dump_chunk_overlap_corpus.sql > corpus.jsonl
    docker cp data-manager-dev:/root/data ./corpus-data

    python scripts/benchmarking/measure_chunk_overlap.py corpus.jsonl \\
      --data-root ./corpus-data
    python scripts/benchmarking/measure_chunk_overlap.py corpus.jsonl \\
      --data-root ./corpus-data --chunk-size 512 --parent-chunk-size 2048 \\
      --overlap 20 --overlap 64 --overlap 128 --json out.json

The collection name is the one the data manager logs at startup
(``VectorStoreManager initialized: collection=...``). A plain text file is
accepted as well: one document per NUL-terminated record (``psql -0``), or the
whole file as one document, with no metadata and no parity check.

Exit code 0 on success; 1 when no usable text was supplied.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_WHITESPACE = re.compile(r"\s+")

# NUL cannot appear in corpus text (PostgreSQL rejects it, and the ingest strips
# it at manager.py), so it serves two jobs: the record separator between
# documents in a ``psql -0`` dump, and the sentinel of the fallback string
# matcher, which it can therefore never be matched across.
DOCUMENT_SEPARATOR = "\x00"
_SEPARATOR = DOCUMENT_SEPARATOR

# Mirror DEFAULT_PARENT_CHUNK_SIZE / DEFAULT_CHILD_CHUNK_SIZE / CHILD_CHUNK_OVERLAP
# in src/data_manager/vectorstore/node_parsing.py; a unit test pins them equal.
DEFAULT_PARENT_CHUNK_SIZE = 2048
DEFAULT_CHUNK_SIZE = 512
PRODUCTION_OVERLAP = 20
# 0 is the control row: the offsets must read nothing carried at every boundary.
DEFAULT_OVERLAPS = (0, 20, 64, 128)


@dataclass(frozen=True)
class Chunk:
    """One embedded child chunk and its character span in its document.

    ``start``/``end`` are ``None`` when the chunk was not found verbatim in its
    document (see :func:`place_chunks`).
    """

    text: str
    document: int
    start: Optional[int]
    end: Optional[int]


@dataclass(frozen=True)
class Record:
    """One loader document as the ingest saw it.

    ``metadata`` is what the loader attached (the splitter subtracts its token
    length from every budget); ``children`` are the chunks the ingest stored for
    it, when the dump carries them, for the parity check; ``path`` is the file's
    location under the data root, so :func:`attach_source_text` can re-read it.
    """

    text: str
    metadata: Dict[str, object] = field(default_factory=dict)
    children: Optional[List[str]] = None
    path: Optional[str] = None


def normalize_whitespace(text: str) -> str:
    """Collapse every whitespace run to a single space and strip the ends.

    Chunkers re-join text with their own spacing, so raw whitespace differs on
    the two sides of a boundary even when the words repeat exactly.
    """
    return _WHITESPACE.sub(" ", text).strip()


def longest_overlap_chars(a: str, b: str) -> int:
    """Length of the longest suffix of ``a`` that is also a prefix of ``b``.

    Both sides are whitespace-normalized first. Uses the KMP prefix function
    over ``b + SEP + a`` so the answer is exact in linear time; the sentinel
    stops a match from running through the join.
    """
    a_norm = normalize_whitespace(a)
    b_norm = normalize_whitespace(b)
    if not a_norm or not b_norm:
        return 0

    combined = b_norm + _SEPARATOR + a_norm
    failure = [0] * len(combined)
    candidate = 0
    for index in range(1, len(combined)):
        while candidate and combined[index] != combined[candidate]:
            candidate = failure[candidate - 1]
        if combined[index] == combined[candidate]:
            candidate += 1
        failure[index] = candidate
    return failure[-1]


def overlap_text(a: str, b: str) -> str:
    """The shared span between the end of ``a`` and the start of ``b``."""
    shared = longest_overlap_chars(a, b)
    return normalize_whitespace(b)[:shared] if shared else ""


def split_records(text: str) -> List[str]:
    """Split a ``psql -0`` dump into documents; text without NUL is one document."""
    return [record for record in text.split(DOCUMENT_SEPARATOR) if record.strip()]


def load_records(text: str) -> List[Record]:
    """Parse a corpus file: JSON lines with metadata, or plain NUL-separated text."""
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and all(line.lstrip().startswith("{") for line in lines):
        records = []
        for line in lines:
            payload = json.loads(line)
            body = payload.get("text") or ""
            if not body.strip():
                continue
            children = payload.get("children")
            records.append(
                Record(
                    text=body,
                    metadata=dict(payload.get("metadata") or {}),
                    children=list(children) if children is not None else None,
                    path=payload.get("path") or None,
                )
            )
        return records
    return [Record(text=record) for record in split_records(text)]


def _ingest_loader():
    """The ingest's loader selection, importable from a plain ``python`` run."""
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from src.data_manager.vectorstore.loader_utils import select_loader
    except ImportError as exc:  # pragma: no cover - environment, not logic
        raise SystemExit(
            f"error: --data-root needs the project's loaders importable: {exc}"
        ) from exc

    def load(path: Path) -> List[object]:
        loader = select_loader(path)
        return list(loader.load()) if loader is not None else []

    return load


def _match_loader_document(documents: Sequence[object], page: object):
    """The loader document a record stands for: by page for multi-page files."""
    if page is None:
        return documents[0] if len(documents) == 1 else None
    for document in documents:
        if str(getattr(document, "metadata", {}).get("page")) == str(page):
            return document
    return None


def attach_source_text(
    records: Sequence[Record], data_root, *, load=None
) -> Tuple[List[Record], int]:
    """Replace reconstructed text with the loader document read from ``data_root``.

    Runs each record's file through the ingest's own loader, so text and
    metadata are exactly what production chunked; a PDF page is matched by its
    ``page`` metadata. The loader stamps the path it read from, so the stored
    ``source`` (the path production read from) is restored — the metadata's
    token count must match what production subtracted from the budget. Records
    whose file is missing, fails to load, or has no matching page keep their
    reconstructed text. Returns the records and how many were replaced.
    """
    load = load or _ingest_loader()
    root = Path(data_root)
    attached: List[Record] = []
    replaced = 0
    for record in records:
        document = None
        file = root / record.path if record.path else None
        if file is not None and file.is_file():
            try:
                document = _match_loader_document(
                    load(file), record.metadata.get("page")
                )
            except Exception as exc:  # a loader failure is not a reason to abort
                print(f"warning: could not load {file}: {exc}", file=sys.stderr)
        if document is None:
            attached.append(record)
            continue
        metadata = dict(getattr(document, "metadata", {}) or {})
        if "source" in record.metadata:
            metadata["source"] = record.metadata["source"]
        attached.append(
            Record(
                text=getattr(document, "page_content", "") or "",
                metadata=metadata,
                children=record.children,
                path=record.path,
            )
        )
        replaced += 1
    return attached, replaced


def clamp_overlap(overlap: int, chunk_size: int, parent_chunk_size: int) -> int:
    """The budget the splitters really get: production clamps like this too."""
    return max(0, min(overlap, chunk_size, parent_chunk_size))


def sweep_budgets(
    requested: Sequence[int], *, chunk_size: int, parent_chunk_size: int
) -> List[Tuple[int, int]]:
    """``(requested, effective)`` pairs, one per distinct effective budget.

    Two requests that clamp to the same budget would measure the same
    chunking twice and present it as two settings; only the first is kept.
    """
    pairs: List[Tuple[int, int]] = []
    seen = set()
    for value in sorted(set(requested)):
        effective = clamp_overlap(value, chunk_size, parent_chunk_size)
        if effective in seen:
            continue
        seen.add(effective)
        pairs.append((value, effective))
    return pairs


def place_chunks(
    text: str,
    chunk_texts: Sequence[str],
    *,
    document: int,
    budget: int,
    tokenizer,
) -> List[Chunk]:
    """Locate each emitted chunk in its document, in order.

    The splitter emits chunks as verbatim substrings whose ends never move
    backwards (a child made only of text copied from the previous parent ends
    exactly where the previous chunk ends), so each chunk is the first
    occurrence that ends no earlier than the previous chunk — except where a
    page repeats a block longer than a chunk: that occurrence can then sit
    inside the previous chunk and imply an overlap the splitter never copied.
    An occurrence implying more than ``budget`` carried tokens is skipped in
    favour of a later one; when none fits (tokenizing the joined overlap can
    count differently from the splitter's per-sentence sizes) the earliest
    verbatim occurrence is kept. A chunk not found verbatim gets no offsets.
    """
    chunks: List[Chunk] = []
    previous_end: Optional[int] = None
    for chunk_text in chunk_texts:
        cursor = 0
        if previous_end is not None:
            cursor = max(0, previous_end - len(chunk_text))
        earliest = start = text.find(chunk_text, cursor)
        while (
            start >= 0
            and previous_end is not None
            and start < previous_end
            and len(tokenizer(chunk_text[: previous_end - start])) > budget
        ):
            start = text.find(chunk_text, start + 1)
        if start < 0:
            start = earliest
        if start < 0:
            chunks.append(
                Chunk(text=chunk_text, document=document, start=None, end=None)
            )
            continue
        end = start + len(chunk_text)
        chunks.append(Chunk(text=chunk_text, document=document, start=start, end=end))
        previous_end = end
    return chunks


def split_documents(
    records: Sequence[Record],
    *,
    chunk_size: int,
    parent_chunk_size: int,
    overlap: int,
) -> List[Chunk]:
    """Chunk each loader document the way the ingest's ``sentence`` strategy does.

    Mirrors ``build_hierarchical_nodes`` + ``_parse_sentence`` in
    node_parsing.py: the document's metadata travels into the LlamaIndex
    ``Document`` (the splitter shrinks every budget by its token length), and a
    two-level ``HierarchicalNodeParser`` applies one overlap budget at both
    levels, clamped to the smaller chunk size. Returns the leaf (child) chunks
    in emission order, each placed in its document by :func:`place_chunks`.
    """
    from llama_index.core import Document
    from llama_index.core.node_parser import HierarchicalNodeParser, get_leaf_nodes
    from llama_index.core.utils import get_tokenizer

    budget = clamp_overlap(overlap, chunk_size, parent_chunk_size)
    parser = HierarchicalNodeParser.from_defaults(
        chunk_sizes=[parent_chunk_size, chunk_size], chunk_overlap=budget
    )
    tokenizer = get_tokenizer()
    chunks: List[Chunk] = []
    for index, record in enumerate(records):
        document = Document(text=record.text, metadata=dict(record.metadata))
        nodes = parser.get_nodes_from_documents([document])
        leaf_texts = [leaf.get_content() for leaf in get_leaf_nodes(nodes)]
        chunks.extend(
            place_chunks(
                record.text,
                [leaf for leaf in leaf_texts if leaf.strip()],
                document=index,
                budget=budget,
                tokenizer=tokenizer,
            )
        )
    return chunks


def reproduced_children(
    records: Sequence[Record], chunks: Sequence[Chunk]
) -> Tuple[int, int]:
    """``(reproduced, stored)``: stored children the split emitted verbatim."""
    from collections import Counter

    produced: Dict[int, Counter] = {}
    for chunk in chunks:
        produced.setdefault(chunk.document, Counter())[chunk.text] += 1
    reproduced = stored = 0
    for index, record in enumerate(records):
        if record.children is None:
            continue
        stored += len(record.children)
        reproduced += sum(
            (produced.get(index, Counter()) & Counter(record.children)).values()
        )
    return reproduced, stored


def has_offsets(previous: Chunk, following: Chunk) -> bool:
    """Whether both sides of a boundary were placed in the document."""
    return previous.end is not None and following.start is not None


def carried_text(previous: Chunk, following: Chunk) -> str:
    """The text the splitter carried from ``previous`` into ``following``.

    Read from the source offsets when both chunks have them; otherwise fall
    back to the longest suffix/prefix string match.
    """
    if has_offsets(previous, following):
        shared = max(0, previous.end - following.start)
        return following.text[:shared]
    return overlap_text(previous.text, following.text)


def carried_chars(previous: Chunk, following: Chunk) -> int:
    """Characters carried across a boundary (see :func:`carried_text`)."""
    return len(carried_text(previous, following))


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile of ``values`` (any order); empty input gives 0."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def summarize_boundaries(carried: Sequence[int]) -> Dict[str, float]:
    """Describe the tokens carried across a set of boundaries."""
    boundaries = len(carried)
    empty = sum(1 for value in carried if value == 0)
    return {
        "boundaries": boundaries,
        "empty_boundaries": empty,
        "empty_pct": 100.0 * empty / boundaries if boundaries else 0.0,
        "mean_tokens": statistics.mean(carried) if carried else 0.0,
        "median_tokens": statistics.median(carried) if carried else 0.0,
        "p90_tokens": percentile(carried, 0.9),
    }


def measure(
    records: Sequence[Record],
    *,
    chunk_size: int,
    parent_chunk_size: int,
    overlap: int,
) -> Dict[str, float]:
    """Chunk ``records`` at one setting and describe the boundaries it produces."""
    from llama_index.core.utils import get_tokenizer

    tokenizer = get_tokenizer()
    chunks = split_documents(
        records,
        chunk_size=chunk_size,
        parent_chunk_size=parent_chunk_size,
        overlap=overlap,
    )
    pairs = [
        (previous, following)
        for previous, following in zip(chunks, chunks[1:])
        if previous.document == following.document
    ]
    carried = [len(tokenizer(carried_text(a, b))) for a, b in pairs]
    base_tokens = sum(len(tokenizer(record.text)) for record in records)
    chunked_tokens = sum(len(tokenizer(chunk.text)) for chunk in chunks)

    row: Dict[str, float] = {
        "overlap": overlap,
        "effective_overlap": clamp_overlap(overlap, chunk_size, parent_chunk_size),
        "chunk_size": chunk_size,
        "parent_chunk_size": parent_chunk_size,
        "documents": len(records),
        "chunks": len(chunks),
    }
    row.update(summarize_boundaries(carried))
    row["fallback_boundaries"] = sum(1 for a, b in pairs if not has_offsets(a, b))
    row["reproduced_children"], row["stored_children"] = reproduced_children(
        records, chunks
    )
    row["index_inflation_pct"] = (
        100.0 * (chunked_tokens - base_tokens) / base_tokens if base_tokens else 0.0
    )
    return row


def format_table(rows: Sequence[Dict[str, float]]) -> str:
    """Render the sweep as a fixed-width table."""
    header = (
        f"{'overlap':>7} {'chunks':>7} {'empty boundaries':>18} "
        f"{'mean tok':>9} {'median':>7} {'p90':>5} {'index inflation':>16}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row['overlap']:>7} {row['chunks']:>7} "
            f"{row['empty_boundaries']:>6}/{row['boundaries']:<5} "
            f"({row['empty_pct']:4.1f}%) {row['mean_tokens']:>9.1f} "
            f"{row['median_tokens']:>7.0f} {row['p90_tokens']:>5} "
            f"{row['index_inflation_pct']:>15.1f}%"
        )
    for row in rows:
        if row["effective_overlap"] != row["overlap"]:
            lines.append(
                f"overlap {row['overlap']}: measured at the clamped budget "
                f"{row['effective_overlap']} (production clamps the same way)"
            )
        if row["fallback_boundaries"]:
            lines.append(
                f"overlap {row['overlap']}: {row['fallback_boundaries']} boundaries "
                "measured by string matching (chunk not emitted verbatim)"
            )
        if row["stored_children"] and row["effective_overlap"] == PRODUCTION_OVERLAP:
            share = 100.0 * row["reproduced_children"] / row["stored_children"]
            lines.append(
                f"overlap {row['overlap']}: reproduces {row['reproduced_children']}/"
                f"{row['stored_children']} ({share:.1f}%) of the children the ingest "
                "stored"
            )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure the overlap the ingest's sentence chunking actually carries "
            "across chunk boundaries, for a sweep of chunk_overlap values."
        )
    )
    parser.add_argument(
        "text_files",
        nargs="+",
        type=Path,
        help=(
            "Corpus file(s): JSON lines from dump_chunk_overlap_corpus.sql, or "
            "plain text with one NUL-terminated document per record."
        ),
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Child chunk size in tokens (default: {DEFAULT_CHUNK_SIZE}).",
    )
    parser.add_argument(
        "--parent-chunk-size",
        type=int,
        default=DEFAULT_PARENT_CHUNK_SIZE,
        help=f"Parent chunk size in tokens (default: {DEFAULT_PARENT_CHUNK_SIZE}).",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        action="append",
        dest="overlaps",
        help=(
            "chunk_overlap value to measure; repeat to sweep "
            f"(default: {' '.join(str(o) for o in DEFAULT_OVERLAPS)})."
        ),
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help=(
            "Copy of the data manager's data directory; each record's file is "
            "re-read with the ingest's loader so text and metadata are exact."
        ),
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Also write the rows to this path as JSON.",
    )
    args = parser.parse_args(argv)

    records: List[Record] = []
    for path in args.text_files:
        if not path.is_file():
            print(f"error: not a file: {path}", file=sys.stderr)
            return 1
        records.extend(load_records(path.read_text(encoding="utf-8", errors="replace")))
    if not records:
        print("error: no usable text in the given file(s)", file=sys.stderr)
        return 1
    loaded = 0
    if args.data_root is not None:
        records, loaded = attach_source_text(records, args.data_root)

    budgets = sweep_budgets(
        args.overlaps or list(DEFAULT_OVERLAPS),
        chunk_size=args.chunk_size,
        parent_chunk_size=args.parent_chunk_size,
    )
    rows = [
        measure(
            records,
            chunk_size=args.chunk_size,
            parent_chunk_size=args.parent_chunk_size,
            overlap=requested,
        )
        for requested, _ in budgets
    ]

    chars = sum(len(record.text) for record in records)
    source = (
        f"{loaded} read with the ingest loaders, {len(records) - loaded} "
        "reconstructed from parents"
        if args.data_root is not None
        else "reconstructed from parents"
    )
    print(
        f"corpus: {len(records)} documents ({source}), {chars} chars, "
        f"chunk_size={args.chunk_size}, parent_chunk_size={args.parent_chunk_size}\n"
    )
    print(format_table(rows))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
