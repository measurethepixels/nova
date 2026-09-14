"""Pure frame-quality ordering helpers shared by cull projection and selection."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")
K = TypeVar("K")


def worst_percentile_keys(
    items: Iterable[T],
    fraction: float,
    *,
    score: Callable[[T], float],
    identity: Callable[[T], K],
) -> set[K]:
    """Return identities for the highest-scoring (worst) fraction of items.

    Frame composite quality is lower-is-better, so rejection must take the
    high end of the distribution.  Guard ``n == 0`` explicitly: a ``[-0:]``
    slice would otherwise select every item.
    """
    ranked = sorted(items, key=score, reverse=True)
    n_reject = max(0, int(len(ranked) * fraction))
    if n_reject == 0:
        return set()
    return {identity(item) for item in ranked[:n_reject]}


def projected_cull_counts(
    scores: list[dict], bottom_pct: float, min_stars: int,
    ecc_threshold: float = 0.66,
) -> tuple[int, int]:
    """Return projected (kept, rejected) using the stack-time ordering rule."""
    percentile_ids = worst_percentile_keys(
        scores,
        bottom_pct,
        score=lambda item: item["composite"],
        identity=id,
    )
    rejected = sum(
        1 for item in scores
        if item["stars"] < min_stars
        or item["ecc"] > ecc_threshold
        or id(item) in percentile_ids
    )
    return len(scores) - rejected, rejected
