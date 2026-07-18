"""
Narrowband palette compositor (workflow 1.9.x, Phase B2).

Builds HOO / SHO / Foraxx composites from the TRUE Ha/OIII channels produced
by the xp_channel_extract hook (Gaia-XP mixing-matrix NNLS unmixing of LP
dual-band data — see nas_server/xp_stars.py). The channels are linear mono
FITS (xp_ha.fit / xp_oiii.fit) written into the autoprocess run dir right
after background_extraction, so they are gradient-free and share dimensions
with everything downstream of the crop step.

Pipeline contract: the seti_astro.nb_palette wrapper is called like any other
variant fn — fn(input_path, output_path, **params) — where input_path is the
current post-stretch STARLESS working image. The composite REPLACES it; the
existing star machinery re-screens the RGB stars afterwards.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

PALETTES = ("hoo", "sho", "foraxx")


def find_xp_channels(near: str | Path, line1: str = "ha",
                     line2: str = "oiii") -> tuple[Path | None, Path | None]:
    """Locate xp_<line1>.fit / xp_<line2>.fit near a working file.

    Experiment variants run with input in run_dir (or run_dir/experiments/<step>/),
    so walk a few parents up from the input file.
    """
    p = Path(near)
    if p.is_file() or p.suffix:
        p = p.parent
    candidates = [p, *list(p.parents)[:3]]
    for d in candidates:
        c1, c2 = d / f"xp_{line1}.fit", d / f"xp_{line2}.fit"
        if c1.exists() and c2.exists():
            return c1, c2
    return None, None


def compose(ha: np.ndarray, oiii: np.ndarray, palette: str = "hoo",
            s_mix: float = 0.8) -> np.ndarray:
    """Composite two STRETCHED mono channels into an (H,W,3) palette image.

    hoo    — R=Ha, G=OIII, B=OIII (classic bicolor)
    sho    — R=S_syn, G=Ha, B=OIII with synthetic SII = s_mix-scaled Ha
    foraxx — R=Ha, B=OIII, G dynamic: w=O^(1-O); G = w*Ha + (1-w)*O
             (the documented Foraxx PixelMath for HO data)
    """
    ha = np.clip(ha, 0.0, 1.0).astype(np.float32)
    oiii = np.clip(oiii, 0.0, 1.0).astype(np.float32)
    if palette == "hoo":
        r, g, b = ha, oiii, oiii
    elif palette == "sho":
        s_syn = np.clip(float(s_mix) * ha, 0.0, 1.0)
        r, g, b = s_syn, ha, oiii
    elif palette == "foraxx":
        w = np.power(oiii, 1.0 - oiii)
        g = np.clip(w * ha + (1.0 - w) * oiii, 0.0, 1.0)
        r, b = ha, oiii
    else:
        raise ValueError(f"unknown palette: {palette!r} (expected one of {PALETTES})")
    return np.stack([r, g, b], axis=-1).astype(np.float32)
