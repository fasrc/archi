#!/usr/bin/env python
"""Measure the overlap a sentence splitter ACTUALLY carries across chunk boundaries.

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

Measurement note — why this compares CHARACTERS, not token ids: a boundary that
falls mid-token (very common, e.g. inside a URL: ``https://github.`` ends one
chunk and ``com/fasrc/...`` opens the next) re-tokenizes differently on each
side. Comparing token-id sequences reports zero overlap for those boundaries even
though the text plainly repeats, which understates real overlap badly. The
comparison here is whitespace-normalized and character-level; only the final
reported size is converted to tokens.

Scope caveat: the input is chunked as ONE text, while the ingest chunks per
document, so a handful of boundaries here straddle two documents. That is
harmless for the tool's purpose — comparing settings against each other under
identical treatment — but it means the absolute numbers are not literally "what
production emits". Quote them as a comparison, not as a production census.

Usage:
    # Dump real corpus text from a running deployment first:
    docker exec postgres-dev psql -U archi -d archi-db -t -A -c \\
      "SELECT string_agg(parent_text, E'\\n\\n' ORDER BY parent_index)
       FROM document_parent_nodes GROUP BY document_id;" > corpus.txt

    python scripts/benchmarking/measure_chunk_overlap.py corpus.txt
    python scripts/benchmarking/measure_chunk_overlap.py corpus.txt \\
      --chunk-size 512 --overlap 20 --overlap 64 --overlap 128 --json out.json

Exit code 0 on success; 1 when no usable text was supplied.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

_WHITESPACE = re.compile(r"\s+")

# Sentinel for the suffix/prefix matcher. NUL cannot appear in the corpus text
# (PostgreSQL rejects it, and the ingest strips it at manager.py), so it can
# never be matched across accidentally.
_SEPARATOR = "\x00"

DEFAULT_CHUNK_SIZE = 512
DEFAULT_OVERLAPS = (20, 64, 128)


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


def measure(
    text: str,
    *,
    chunk_size: int,
    overlap: int,
) -> Dict[str, float]:
    """Split ``text`` at one setting and describe the boundaries it produces."""
    from llama_index.core.node_parser import SentenceSplitter
    from llama_index.core.utils import get_tokenizer

    tokenizer = get_tokenizer()
    splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=overlap)
    chunks = splitter.split_text(text)

    carried = [
        len(tokenizer(overlap_text(first, second)))
        for first, second in zip(chunks, chunks[1:])
    ]
    base_tokens = len(tokenizer(text))
    chunked_tokens = sum(len(tokenizer(chunk)) for chunk in chunks)

    boundaries = len(carried)
    ordered = sorted(carried)
    return {
        "overlap": overlap,
        "chunk_size": chunk_size,
        "chunks": len(chunks),
        "boundaries": boundaries,
        "empty_boundaries": sum(1 for value in carried if value == 0),
        "empty_pct": (
            100.0 * sum(1 for value in carried if value == 0) / boundaries
            if boundaries
            else 0.0
        ),
        "mean_tokens": statistics.mean(carried) if carried else 0.0,
        "median_tokens": statistics.median(carried) if carried else 0.0,
        "p90_tokens": ordered[int(0.9 * len(ordered))] if ordered else 0,
        "index_inflation_pct": (
            100.0 * (chunked_tokens - base_tokens) / base_tokens if base_tokens else 0.0
        ),
    }


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
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure the overlap a sentence splitter actually carries across "
            "chunk boundaries, for a sweep of chunk_overlap values."
        )
    )
    parser.add_argument(
        "text_files",
        nargs="+",
        type=Path,
        help="Text file(s) of real corpus content (see the module docstring).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Child chunk size in tokens (default: {DEFAULT_CHUNK_SIZE}).",
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
        "--json",
        type=Path,
        default=None,
        help="Also write the rows to this path as JSON.",
    )
    args = parser.parse_args(argv)

    texts: List[str] = []
    for path in args.text_files:
        if not path.is_file():
            print(f"error: not a file: {path}", file=sys.stderr)
            return 1
        texts.append(path.read_text(encoding="utf-8", errors="replace"))
    text = "\n\n".join(chunk for chunk in texts if chunk.strip())
    if not text.strip():
        print("error: no usable text in the given file(s)", file=sys.stderr)
        return 1

    overlaps = args.overlaps or list(DEFAULT_OVERLAPS)
    rows = [
        measure(text, chunk_size=args.chunk_size, overlap=overlap)
        for overlap in sorted(set(overlaps))
    ]

    print(f"corpus: {len(text)} chars, chunk_size={args.chunk_size}\n")
    print(format_table(rows))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
