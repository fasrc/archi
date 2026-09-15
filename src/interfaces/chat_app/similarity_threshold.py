"""Ordering and threshold rules for the sources shown beside an answer.

Retriever scores reaching the citation layer are higher-is-better: every producer
in `PostgresVectorStore` returns `1.0 - distance`, for the hybrid query and the
semantic-only one alike. So sources sort descending and the configured
`similarity_score_reference` is a *floor*.

What that floor is measured in, however, depends on how the sources were
retrieved, and the scales are not comparable:

- Under `cosine`, `1.0 - distance` over a 0..2 distance gives a similarity in
  -1..1 -- so an operator's 0.3 means roughly what they expect.
- Under `l2` / `inner_product` the same expression is monotonically correct but
  unbounded, so no fixed number means the same thing.
- On the classic QA path the score is `hybrid_search`'s `combined_score`, a
  weighted blend of a semantic score and an unbounded BM25 score, so the number
  is query-dependent rather than an absolute relevance.

The floor is therefore opt-in and ships disabled; calibrating it across those
scales is tracked separately and is not something this module can decide.

This logic lives here rather than inline in `app.py` so it can be unit-tested
directly.
"""

from typing import Any, List, Optional, Sequence, Tuple

from src.utils.logging import get_logger

logger = get_logger(__name__)

# A score of -1.0 means "no score available" rather than a very poor match. It is
# never compared against the floor and never renders a relevance figure.
NO_SCORE_SENTINEL = -1.0


def normalize_similarity_threshold(raw: Optional[float]) -> Optional[float]:
    """Return a usable similarity floor, or ``None`` when there is no floor.

    Two configured values mean "no floor", and both are reported as ``None`` so
    the caller skips the comparison entirely rather than applying a number that
    happens to filter:

    - **Above 1.0** -- cannot be a similarity, and is almost certainly a distance
      ceiling left over from the retired convention. Obeyed literally it would
      filter every source and the answer would cite nothing, which is worse than
      the inert guard it replaced, so it is discarded with a warning naming the
      value.
    - **At or below 0.0** -- the shipped default. Returning the number ``0.0``
      would make it a real floor rather than an absent one: a cosine similarity
      runs down to -1.0, so an anti-correlated source would be dropped, and
      because the list is ordered best-first every source after it would be
      dropped too. "Cite everything unless the operator opts in" has to mean no
      comparison at all.

    A value of exactly 1.0 is strict but coherent ("cite only an exact match")
    and is honoured, as is any value in between.
    """
    if raw is None:
        return None
    if raw > 1.0:
        logger.warning(
            "similarity_score_reference %.4g is not a valid cosine similarity "
            "(must be <= 1.0); ignoring it and applying no floor. "
            "Update your deployment config to suppress this warning.",
            raw,
        )
        return None
    if raw <= 0.0:
        return None
    return raw


def order_and_filter_by_similarity(
    documents: Sequence,
    scores: Sequence,
    floor: Optional[float],
) -> List[Tuple[Optional[float], Any]]:
    """Pair documents with their scores, best first, dropping those below *floor*.

    Sources are ordered by descending score, so the most relevant is cited first.
    With a floor configured, the first source scoring below it stops the scored
    run -- correct precisely because the order is best-first, so nothing after it
    scores higher.

    Sentinel entries are the exception and the reason this cannot be a plain
    ``break``. ``-1.0`` means "no score available", so it is not eligible for
    threshold filtering -- but it is also numerically the smallest value present,
    so descending order puts it *after* every real score. A loop that stopped at
    the first below-floor score would therefore never reach it, and a source
    documented as bypassing the threshold would be dropped by it. The scored run
    and the sentinels are partitioned first for that reason, and the sentinels
    are appended after the surviving scored entries.

    When *scores* is empty the documents are returned in their given order, each
    paired with ``None``.
    """
    if not scores:
        return [(None, document) for document in documents]

    # `reverse=True` on the pair itself would compare documents whenever two
    # scores tie, which Document does not support. Sorting on the score alone
    # avoids that; the leading flag keeps a stray `None` from being compared
    # against a float and sends it to the back with the sentinels.
    def _rank(pair):
        score = pair[0]
        return (score is not None, score if score is not None else 0.0)

    pairs = sorted(zip(scores, documents), key=_rank, reverse=True)

    scored = [pair for pair in pairs if pair[0] != NO_SCORE_SENTINEL]
    sentinels = [pair for pair in pairs if pair[0] == NO_SCORE_SENTINEL]

    if floor is not None:
        kept = []
        for score, document in scored:
            if score is not None and score < floor:
                logger.debug(
                    "Stopping at document scoring %s, below the similarity floor %s",
                    score,
                    floor,
                )
                break
            kept.append((score, document))
        scored = kept

    return scored + sentinels
