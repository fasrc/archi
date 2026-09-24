"""One attribution rule for the preflight census and the per-category slice.

Decided on #525 and #538 rule 4: a bank row is owned by the category of its first
declared source; rows spanning categories go on their own line and out of
per-category source accuracy; rows whose first source has no category go on an
"uncategorized" line and are never moved to a later source. Coverage counts
distinct gold articles over every source; the 10 % concentration rule counts an
article on every row that cites it, over the gold rows; power counts, per metric,
the category's articles cited by the rows that metric attributes to it.
"""

import pytest

from scripts.benchmarking.category_attribution import (
    AMBIGUOUS,
    attribute,
    metric_power,
    row_shares,
    url_categories,
)

KB = "https://docs.rc.fas.harvard.edu/kb"


def _map(**by_slug):
    return {f"{KB}/{slug}": category for slug, category in by_slug.items()}


def _row(*slugs):
    return [f"{KB}/{slug}" for slug in slugs]


# --- url_categories ---------------------------------------------------------------


def test_url_with_one_category_maps_to_it():
    assert url_categories([(f"{KB}/a", "A")]) == {f"{KB}/a": "A"}


def test_empty_category_maps_to_none():
    assert url_categories([(f"{KB}/a", "")]) == {f"{KB}/a": None}


def test_url_with_two_categories_is_ambiguous():
    assert url_categories([(f"{KB}/a", "A"), (f"{KB}/a", "B")]) == {
        f"{KB}/a": AMBIGUOUS
    }


def test_repeated_identical_pairs_are_not_ambiguous():
    assert url_categories([(f"{KB}/a", "A"), (f"{KB}/a", "A")]) == {f"{KB}/a": "A"}


# --- attribute: ownership and the side lines ---------------------------------------


def test_row_is_owned_by_its_first_source():
    result = attribute({"q1": _row("a", "b")}, _map(a="A", b="A"))
    assert result.owner == {"q1": "A"}
    assert result.gold_rows == {"A": ["q1"]}
    assert result.cross_category == []


def test_two_sources_in_different_categories():
    """The #525 test: one row, owned by A, on the cross-category line."""
    result = attribute({"q1": _row("a", "b")}, _map(a="A", b="B"))

    assert result.owner == {"q1": "A"}
    assert result.gold_rows == {"A": ["q1"]}
    assert result.cross_category == ["q1"]
    # Its second article still counts toward B's coverage.
    assert result.coverage == {"A": {f"{KB}/a"}, "B": {f"{KB}/b"}}


def test_uncategorized_first_source_is_not_moved():
    """The #538 test: no owner, on the uncategorized line, B's coverage kept."""
    result = attribute({"q1": _row("a", "b")}, _map(a=None, b="B"))

    assert result.owner == {"q1": None}
    assert result.uncategorized == ["q1"]
    assert result.gold_rows == {}
    assert result.coverage == {"B": {f"{KB}/b"}}


def test_unresolved_source_is_reported_not_dropped():
    result = attribute({"q1": _row("missing")}, _map(a="A"))

    assert result.unresolved == [("q1", f"{KB}/missing")]
    assert result.owner == {"q1": None}
    assert "q1" in result.rows


def test_ambiguous_source_is_unresolved():
    result = attribute({"q1": _row("a")}, {f"{KB}/a": AMBIGUOUS})
    assert result.unresolved == [("q1", f"{KB}/a")]


def test_source_less_row_has_no_owner_and_no_side_line():
    result = attribute({"q1": []}, _map(a="A"))

    assert result.owner == {"q1": None}
    assert result.uncategorized == [] and result.unresolved == []
    assert result.gold_row_count == 0


# --- row_shares: the 10 % concentration rule ---------------------------------------


def test_share_counts_every_citation_position():
    """An article that is only ever a second source still has its share."""
    rows = {f"q{i}": _row(f"first{i}") for i in range(8)}
    rows["q8"] = _row("first8", "second")
    rows["q9"] = _row("first9", "second")

    assert row_shares(rows)[f"{KB}/second"] == pytest.approx(0.2)


def test_share_denominator_is_gold_rows_only():
    """20 sourced + 10 source-less rows, one article cited by 3: 15 %, not 10 %."""
    rows = {f"q{i}": _row(f"other{i}") for i in range(17)}
    rows.update({f"hot{i}": _row("hot") for i in range(3)})
    rows.update({f"refuse{i}": [] for i in range(10)})

    share = row_shares(rows)[f"{KB}/hot"]

    assert share == pytest.approx(0.15)
    assert share > 0.10


def test_a_row_citing_one_article_twice_counts_once():
    rows = {"q1": _row("a", "a"), "q2": _row("b")}
    assert row_shares(rows)[f"{KB}/a"] == pytest.approx(0.5)


# --- metric_power ------------------------------------------------------------------


def test_power_is_counted_on_the_rows_a_metric_uses():
    """Powered for completion, underpowered for source accuracy.

    A owns three rows over three articles; the only row citing article a3 is
    cross-category, so source accuracy (which drops cross-category rows) sees two.
    """
    rows = {"q1": _row("a1"), "q2": _row("a2"), "q3": _row("a3", "b1")}
    mapping = _map(a1="A", a2="A", a3="A", b1="B")
    result = attribute(rows, mapping)

    assert metric_power(result, "A", "completion") == 3
    assert metric_power(result, "A", "source") == 2


def test_power_counts_only_the_categorys_own_articles():
    rows = {"q1": _row("a1", "b1")}
    result = attribute(rows, _map(a1="A", b1="A"))
    assert metric_power(result, "A", "completion") == 2


def test_unknown_metric_is_rejected():
    result = attribute({"q1": _row("a")}, _map(a="A"))
    with pytest.raises(ValueError):
        metric_power(result, "A", "atoms")
