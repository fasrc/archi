"""Preflight censuses for the rung-0 category sweep (plan §6.2, W7).

Three gates the sweep must pass before any verdict is read, plus the r0b
exemplar-disjointness check (§5 void checks, §6.3):

1. **Coverage** — share of non-deleted KB documents (``/kb/`` URLs) with a
   non-empty ``category``; below 90 % is an ingest bug to chase first.
2. **Vocabulary drift** — the distinct non-empty categories over those KB
   documents equal the bullet list under ``## Category routing`` in the routing
   prompt (r0a). Non-KB sources such as Indico keep their own taxonomy and are
   out of scope; an empty category counts against coverage only.
3. **Bank coverage and concentration** — per category, owned gold rows and
   coverage (distinct gold articles over every source), by the shared rules in
   ``category_attribution``; fails below 6 covered categories or when any
   article's share of gold rows exceeds 10 %.

Every reading — the map, and the corpus fingerprint that labels the output —
runs on one ``--pg-dsn`` connection inside one read-only repeatable-read
transaction, so the output describes one database state. The JSON records the
corpus fingerprint, the category-map digest (the fingerprint cannot see a
metadata change, #524) and the sha256 of every input, so ``archive_run.sh
--sweep --census`` can bind it to the run.

Usage::

    python scripts/benchmarking/category_census.py --pg-dsn postgresql://... \\
        --bank config/benchmarking/fasrc_ragas_queries.json \\
        --anchors examples/benchmarking/anchor_questions.json \\
        --routing-prompt config/benchmarking/prompt_sweep_r0/fasrc-docs-r0a-category.md \\
        --exemplar-prompt config/benchmarking/prompt_sweep_r0/fasrc-docs-r0b-icl.md \\
        --json bench_out/census.json

Exit codes: 0 every gate passed, 1 usage or unreadable input, 2 a gate failed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.benchmarking.category_attribution import (  # noqa: E402
    attribute,
    row_shares,
    url_categories,
)
from src.utils.benchmark_provenance import (  # noqa: E402
    canonical_source_url,
    category_map_digest,
    category_map_records,
    corpus_fingerprint,
)

HARNESS = REPO_ROOT / "src" / "bin" / "service_benchmark.py"
COVERAGE_THRESHOLD = 0.90
MIN_COVERED_CATEGORIES = 6
MAX_ARTICLE_SHARE = 0.10
MIN_ARTICLES = 3
DEFAULT_SIMILARITY = 0.5

EXIT_OK, EXIT_USAGE, EXIT_GATE = 0, 1, 2

Docs = Sequence[Tuple[Optional[str], Optional[str]]]


def _harness_query(name: str) -> str:
    """A query constant read from the harness source, so the text cannot drift."""
    tree = ast.parse(HARNESS.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == name for target in node.targets
        ):
            return node.value.value  # type: ignore[attr-defined]
    raise RuntimeError(f"{HARNESS} defines no {name}")


def _is_kb(url: Optional[str]) -> bool:
    return bool(url) and "/kb/" in canonical_source_url(url)


# --- 1. coverage -------------------------------------------------------------------


def coverage_census(docs: Docs) -> Dict[str, Any]:
    kb = [(url, category) for url, category in docs if _is_kb(url)]
    categorized = sum(1 for _, category in kb if category)
    share = categorized / len(kb) if kb else 0.0
    return {
        "total": len(kb),
        "categorized": categorized,
        "share": share,
        "threshold": COVERAGE_THRESHOLD,
        "passed": bool(kb) and share >= COVERAGE_THRESHOLD,
    }


# --- 2. vocabulary -----------------------------------------------------------------


def _section(text: str, heading: str) -> List[str]:
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise ValueError(f"prompt has no {heading!r} section") from None
    body: List[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def parse_routing_labels(text: str) -> List[str]:
    """The first bullet list under ``## Category routing``."""
    labels: List[str] = []
    for line in _section(text, "## Category routing"):
        stripped = line.strip()
        if stripped.startswith("- "):
            labels.append(stripped[2:].strip())
        elif labels and stripped:
            break
    if not labels:
        raise ValueError("the '## Category routing' section lists no categories")
    return labels


def vocabulary_census(docs: Docs, labels: Sequence[str]) -> Dict[str, Any]:
    corpus = {category for url, category in docs if _is_kb(url) and category}
    prompt = set(labels)
    prompt_only, corpus_only = sorted(prompt - corpus), sorted(corpus - prompt)
    return {
        "prompt_only": prompt_only,
        "corpus_only": corpus_only,
        "passed": not prompt_only and not corpus_only,
    }


# --- 3. bank coverage and concentration --------------------------------------------


def bank_census(rows: Dict[str, List[str]], docs: Docs) -> Dict[str, Any]:
    category_of = url_categories(
        (url, category) for url, category in docs if url is not None
    )
    result = attribute(rows, category_of)
    table = [
        {
            "category": category,
            "gold_rows": len(result.gold_rows.get(category, [])),
            "coverage": len(articles),
            "underpowered": len(articles) < MIN_ARTICLES,
        }
        for category, articles in sorted(result.coverage.items())
    ]
    shares = row_shares(rows)
    failures: List[str] = []
    if len(result.coverage) < MIN_COVERED_CATEGORIES:
        failures.append(
            f"the bank covers {len(result.coverage)} categories; "
            f"at least {MIN_COVERED_CATEGORIES} are required"
        )
    for url, share in sorted(shares.items(), key=lambda item: -item[1]):
        if share > MAX_ARTICLE_SHARE:
            failures.append(
                f"{url} supplies {share:.1%} of gold rows "
                f"(limit {MAX_ARTICLE_SHARE:.0%})"
            )
    return {
        "rows": len(rows),
        "gold_rows": result.gold_row_count,
        "categories_with_coverage": len(result.coverage),
        "table": table,
        "max_article_share": max(shares.values(), default=0.0),
        "cross_category": result.cross_category,
        "uncategorized": result.uncategorized,
        "unresolved": [list(pair) for pair in result.unresolved],
        "failures": failures,
        "passed": not failures,
    }


# --- exemplar disjointness ---------------------------------------------------------

_URL = re.compile(r"https?://[^\s)\]>\"']+")


def parse_exemplars(text: str) -> List[Dict[str, Any]]:
    """``Question:`` / ``Answer:`` pairs under ``## Worked examples``."""
    exemplars: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    target = ""
    for line in _section(text, "## Worked examples"):
        stripped = line.strip()
        if stripped.startswith("Question:"):
            current = {"question": stripped[len("Question:") :].strip(), "answer": ""}
            exemplars.append(current)
            target = "question"
        elif stripped.startswith("Answer:") and current is not None:
            current["answer"] = stripped[len("Answer:") :].strip()
            target = "answer"
        elif current is not None and stripped:
            current[target] = f"{current[target]} {stripped}".strip()
        elif current is not None and target == "question":
            target = ""
    for exemplar in exemplars:
        exemplar["urls"] = [
            canonical_source_url(url.rstrip(".,;"))
            for url in _URL.findall(exemplar["answer"])
        ]
    return exemplars


def _words(text: str) -> set:
    return set(re.sub(r"[^\w]+", " ", text.casefold()).split())


def exemplar_disjointness(
    exemplars: Sequence[Dict[str, Any]],
    bank: Sequence[Dict[str, Any]],
    threshold: float,
) -> Dict[str, Any]:
    """Fail when an exemplar question or cited URL appears in the bank or anchors."""
    collisions: List[Dict[str, Any]] = []
    bank_urls = {
        canonical_source_url(url) for row in bank for url in (row.get("sources") or [])
    }
    for index, exemplar in enumerate(exemplars, 1):
        words = _words(exemplar["question"])
        for row in bank:
            other = _words(str(row.get("user_input", "")))
            union = words | other
            similarity = len(words & other) / len(union) if union else 0.0
            if words == other or similarity >= threshold:
                collisions.append(
                    {
                        "kind": "question",
                        "exemplar": index,
                        "question": row.get("user_input"),
                        "similarity": round(similarity, 3),
                    }
                )
        for url in exemplar["urls"]:
            if url in bank_urls:
                collisions.append({"kind": "url", "exemplar": index, "url": url})
    return {
        "passed": not collisions,
        "exemplars": len(exemplars),
        "threshold": threshold,
        "collisions": collisions,
    }


# --- database readings ---------------------------------------------------------------


def read_database(dsn: str, connect: Callable[[str], Any]) -> Dict[str, Any]:
    """Map rows, map digest and corpus fingerprint from one database state."""
    conn = connect(dsn)
    try:
        conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
        with conn.cursor() as cur:
            cur.execute(_harness_query("CATEGORY_MAP_QUERY"))
            docs = [tuple(row) for row in cur.fetchall()]
            cur.execute(_harness_query("CORPUS_STATE_QUERY"))
            fingerprint = corpus_fingerprint(cur.fetchall())
        conn.rollback()
    finally:
        conn.close()
    return {
        "docs": docs,
        "category_map_digest": category_map_digest(category_map_records(docs)),
        "corpus_fingerprint": fingerprint,
    }


# --- report --------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_bank(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path} is not a JSON list of bank rows")
    return data


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "## Category preflight census",
        "",
        f"- corpus fingerprint: `{report.get('corpus_fingerprint')}`",
        f"- category-map digest: `{report.get('category_map_digest')}`",
        f"- result: **{'pass' if report['passed'] else 'FAIL'}**",
    ]
    for gate in report.get("failures", []):
        lines.append(f"  - {gate}")
    bank = report.get("bank")
    if bank:
        lines += [
            "",
            "| category | gold rows | coverage | underpowered |",
            "|---|---|---|---|",
        ]
        for row in bank["table"]:
            lines.append(
                f"| {row['category']} | {row['gold_rows']} | {row['coverage']} | "
                f"{'yes' if row['underpowered'] else 'no'} |"
            )
        lines += [
            "",
            f"cross-category rows: {len(bank['cross_category'])}; "
            f"uncategorized: {len(bank['uncategorized'])}; "
            f"unresolved sources: {len(bank['unresolved'])}; "
            f"max article share: {bank['max_article_share']:.1%}",
        ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pg-dsn", required=True)
    parser.add_argument("--bank", required=True, type=Path)
    parser.add_argument("--anchors", required=True, type=Path)
    parser.add_argument("--routing-prompt", required=True, type=Path)
    parser.add_argument("--exemplar-prompt", required=True, type=Path)
    parser.add_argument(
        "--similarity-threshold", type=float, default=DEFAULT_SIMILARITY
    )
    parser.add_argument("--json", type=Path)
    return parser


def _default_connect(dsn: str) -> Any:
    import psycopg2

    return psycopg2.connect(dsn)


def main(
    argv: Optional[Sequence[str]] = None,
    connect: Callable[[str], Any] = _default_connect,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        bank = _load_bank(args.bank)
        anchors = _load_bank(args.anchors)
        labels = parse_routing_labels(args.routing_prompt.read_text())
        exemplars = parse_exemplars(args.exemplar_prompt.read_text())
        inputs = {
            "bank_sha256": _sha256(args.bank),
            "anchors_sha256": _sha256(args.anchors),
            "routing_prompt_sha256": _sha256(args.routing_prompt),
            "exemplar_prompt_sha256": _sha256(args.exemplar_prompt),
            "similarity_threshold": args.similarity_threshold,
        }
    except (OSError, ValueError) as exc:
        print(f"category_census: {exc}", file=sys.stderr)
        return EXIT_USAGE

    report: Dict[str, Any] = {"inputs": inputs, "failures": []}
    try:
        reading = read_database(args.pg_dsn, connect)
    except Exception as exc:  # noqa: BLE001 - any failed reading fails the census
        report.update(passed=False, failures=[f"database reading failed: {exc}"])
    else:
        docs = reading.pop("docs")
        rows = {
            str(row.get("user_input")): list(row.get("sources") or []) for row in bank
        }
        report.update(reading)
        report["coverage"] = coverage_census(docs)
        report["vocabulary"] = vocabulary_census(docs, labels)
        report["bank"] = bank_census(rows, docs)
        report["exemplars"] = exemplar_disjointness(
            exemplars, bank + anchors, args.similarity_threshold
        )
        for name in ("coverage", "vocabulary", "bank", "exemplars"):
            if not report[name]["passed"]:
                report["failures"].append(f"{name} gate failed")
        report["passed"] = not report["failures"]

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True, default=list))
    print(render_markdown(report))
    return EXIT_OK if report["passed"] else EXIT_GATE


if __name__ == "__main__":
    sys.exit(main())
