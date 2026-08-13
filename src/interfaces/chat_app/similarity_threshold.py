"""Validation for the similarity_score_reference threshold.

Cosine similarities lie in 0..1, so a configured value above 1.0 cannot be a
floor — it is a distance ceiling from the pre-flip convention.  Applying it
literally would filter every source; instead it is treated as 0.0 (no floor)
and a warning is emitted once at process start.
"""

from src.utils.logging import get_logger

logger = get_logger(__name__)


def normalize_similarity_threshold(raw: float) -> float:
    """Return a valid similarity floor, substituting 0.0 for out-of-range values.

    A cosine similarity is in 0..1.  If *raw* exceeds 1.0 it cannot be a
    similarity floor and was almost certainly set under the old distance
    convention.  Emit a warning naming the configured value and return 0.0 so
    the threshold guard stays effectively disabled rather than filtering every
    source.  A value of exactly 1.0 is strict but coherent and is returned
    unchanged.
    """
    if raw > 1.0:
        logger.warning(
            "similarity_score_reference %.4g is not a valid cosine similarity "
            "(must be <= 1.0); substituting 0.0. "
            "Update your deployment config to suppress this warning.",
            raw,
        )
        return 0.0
    return raw
