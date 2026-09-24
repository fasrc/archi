"""Attribute golden-set rows to documentation categories — one rule, two callers.

The preflight census (``category_census.py``) and the per-category slice in
``compare_runs.py`` both call this module, so the preflight and the scored result
can never disagree about which category a row belongs to (plan
``docs/docs/proposals/categories-action-plan.md`` §6.1, §6.2).

The rules, as decided on #525 and #538 rule 4:

- **Ownership.** A row belongs to the category of its **first** declared source.
  A row whose first source has no category is on the *uncategorized* line and is
  not moved to a later source; a first source missing from the map (or mapped to
  two categories) leaves the row unowned and is reported as *unresolved*.
- **Cross-category rows** — sources resolving to more than one category — keep
  their owner for every row-level figure but are left out of per-category source
  accuracy, because under ``any(matches)`` the hit may have come from the other
  category's source.
- **Coverage** is the distinct gold articles in a category over **every** source.
- **Concentration** counts an article on every row that cites it, in any
  position, over the gold rows (rows that declare at least one source).
- **Power** for a metric counts the category's own articles cited by the rows
  that metric attributes to the category.

Pure: no I/O, so both callers pass the same inputs and get the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from src.utils.benchmark_provenance import canonical_source_url

#: A URL the map assigns to two different categories. It is reported as
#: unresolved rather than silently given one of them.
AMBIGUOUS = "<ambiguous>"

#: Metrics whose rows exclude cross-category rows (see module docstring).
_SOURCE_METRICS = {"source"}
_ROW_METRICS = {"completion", "item_pass"}


def url_categories(
    pairs: Iterable[Tuple[str, Optional[str]]],
) -> Dict[str, Optional[str]]:
    """Canonical URL -> category, ``None`` for no category, ``AMBIGUOUS`` for two."""
    out: Dict[str, Optional[str]] = {}
    for url, category in pairs:
        key = canonical_source_url(url)
        value = category or None
        if key in out and out[key] != value:
            out[key] = AMBIGUOUS
        elif key not in out:
            out[key] = value
    return out


@dataclass
class Attribution:
    """Who owns each row, and the lines and counts the rules produce."""

    rows: Dict[str, List[str]]
    category_of: Mapping[str, Optional[str]]
    owner: Dict[str, Optional[str]] = field(default_factory=dict)
    gold_rows: Dict[str, List[str]] = field(default_factory=dict)
    cross_category: List[str] = field(default_factory=list)
    uncategorized: List[str] = field(default_factory=list)
    unresolved: List[Tuple[str, str]] = field(default_factory=list)
    coverage: Dict[str, Set[str]] = field(default_factory=dict)

    @property
    def gold_row_count(self) -> int:
        return sum(1 for sources in self.rows.values() if sources)


def _resolve(category_of: Mapping[str, Optional[str]], url: str) -> Tuple[bool, Optional[str]]:
    """``(resolved, category)``; unresolved when absent or ambiguous."""
    if url not in category_of or category_of[url] == AMBIGUOUS:
        return False, None
    return True, category_of[url]


def attribute(
    rows: Mapping[str, Sequence[str]],
    category_of: Mapping[str, Optional[str]],
) -> Attribution:
    """Apply the ownership, side-line and coverage rules to *rows*.

    *rows* maps a row key (the question text) to its declared source URLs in
    declared order; *category_of* is :func:`url_categories` output.
    """
    canonical = {
        key: [canonical_source_url(url) for url in sources]
        for key, sources in rows.items()
    }
    result = Attribution(rows=canonical, category_of=category_of)
    for key, sources in canonical.items():
        categories: Set[str] = set()
        for url in sources:
            resolved, category = _resolve(category_of, url)
            if not resolved:
                result.unresolved.append((key, url))
            elif category is not None:
                categories.add(category)
                result.coverage.setdefault(category, set()).add(url)

        owner: Optional[str] = None
        if sources:
            resolved, category = _resolve(category_of, sources[0])
            if resolved and category is None:
                result.uncategorized.append(key)
            elif resolved:
                owner = category
        result.owner[key] = owner
        if owner is not None:
            result.gold_rows.setdefault(owner, []).append(key)
        if len(categories) > 1:
            result.cross_category.append(key)
    return result


def metric_rows(result: Attribution, category: str, metric: str) -> List[str]:
    """The rows *metric* attributes to *category*."""
    owned = result.gold_rows.get(category, [])
    if metric in _SOURCE_METRICS:
        crossing = set(result.cross_category)
        return [key for key in owned if key not in crossing]
    if metric in _ROW_METRICS:
        return list(owned)
    raise ValueError(
        f"unknown metric {metric!r}; expected one of "
        f"{sorted(_SOURCE_METRICS | _ROW_METRICS)}"
    )


def metric_power(result: Attribution, category: str, metric: str) -> int:
    """Distinct *category* articles cited by the rows *metric* uses there."""
    articles: Set[str] = set()
    for key in metric_rows(result, category, metric):
        for url in result.rows[key]:
            resolved, found = _resolve(result.category_of, url)
            if resolved and found == category:
                articles.add(url)
    return len(articles)


def row_shares(rows: Mapping[str, Sequence[str]]) -> Dict[str, float]:
    """Share of gold rows citing each article, in any source position."""
    gold = [
        {canonical_source_url(url) for url in sources}
        for sources in rows.values()
        if sources
    ]
    if not gold:
        return {}
    counts: Dict[str, int] = {}
    for articles in gold:
        for url in articles:
            counts[url] = counts.get(url, 0) + 1
    return {url: count / len(gold) for url, count in counts.items()}
