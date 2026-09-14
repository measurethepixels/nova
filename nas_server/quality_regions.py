"""Versioned semantic-region foundation for additive quality measurements."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

REGION_CONTRACT_VERSION = "semantic-regions/1.0.0"


@dataclass(frozen=True)
class SemanticRegions:
    shape: tuple[int, int]
    true_sky: np.ndarray | None = None
    target: np.ndarray | None = None
    stars: np.ndarray | None = None
    core: np.ndarray | None = None

    def mask(self, role: str) -> np.ndarray:
        value = getattr(self, role)
        if value is None:
            return np.zeros(self.shape, dtype=bool)
        mask = np.asarray(value, dtype=bool)
        if mask.shape != self.shape:
            raise ValueError(f"{role} mask shape must match image")
        return mask

    def provenance(self, role: str, *, derivation: str) -> dict:
        mask = self.mask(role)
        return {
            "method": REGION_CONTRACT_VERSION,
            "role": role,
            "derivation": derivation,
            "coverage": float(mask.mean()),
            "frame_fill": bool(self.target is not None and self.mask("target").mean() >= 0.9),
        }


def semantic_regions(shape, **roles) -> SemanticRegions:
    """Validate caller-supplied roles through one shared versioned contract."""
    model = SemanticRegions(tuple(shape), **roles)
    for role, value in roles.items():
        if value is not None:
            model.mask(role)
    sky = model.mask("true_sky")
    target = model.mask("target")
    if sky.any() and target.any() and np.any(sky & target):
        raise ValueError("true_sky and target masks must not overlap")
    return model
