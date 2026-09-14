#!/usr/bin/env python3
"""Headless file runner for the SASpro 1.20.1 SyQon Parallax engines."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from astropy.io import fits


def _load(path: Path) -> tuple[np.ndarray, fits.Header, bool, float]:
    with fits.open(path, memmap=False) as hdul:
        data = np.asarray(hdul[0].data, dtype=np.float32)
        header = hdul[0].header.copy()
    channels_first = data.ndim == 3 and data.shape[0] in (1, 3)
    if channels_first:
        data = np.moveaxis(data, 0, -1)
    mono = data.ndim == 2 or (data.ndim == 3 and data.shape[-1] == 1)
    if data.ndim == 2:
        data = np.stack([data] * 3, axis=-1)
    elif data.shape[-1] == 1:
        data = np.repeat(data, 3, axis=-1)
    scale = float(np.nanmax(data)) if data.size else 1.0
    normalized = np.clip(np.nan_to_num(data) / scale if scale > 1.01 else data, 0, 1)
    return normalized.astype(np.float32), header, mono, scale


def _save(path: Path, data: np.ndarray, header: fits.Header,
          mono: bool, scale: float) -> None:
    result = np.clip(data, 0, 1)
    if scale > 1.01:
        result = result * scale
    if mono:
        result = result.mean(axis=2)
    else:
        result = np.moveaxis(result, -1, 0)
    fits.PrimaryHDU(result.astype(np.float32), header=header).writeto(path, overwrite=True)


def _validate_dimensions(image: np.ndarray) -> None:
    height, width = image.shape[:2]
    if min(height, width) < 512:
        raise ValueError(
            "Parallax requires a full-field image with both dimensions at least "
            "512 pixels; small crops can produce invalid coordinate-aware output"
        )


def run(args) -> dict:
    image, header, mono, scale = _load(args.input)
    _validate_dimensions(image)

    from setiastro.saspro.sharpen_engines.syqon_parallax_engine import (
        parallax_correction_rgb01,
        parallax_sharpen_rgb01,
        parallax_star_reduce_rgb01,
    )
    from setiastro.saspro.remove_stars import (
        _apply_mtf_unlinked_rgb,
        _invert_mtf_unlinked_rgb,
        _mtf_params_unlinked,
    )

    mtf_params = None
    inference_image = image
    if args.use_mtf:
        mtf_params = _mtf_params_unlinked(image, targetbg=args.mtf_target)
        inference_image = _apply_mtf_unlinked_rgb(image, mtf_params)
    common = {"tile": args.tile, "overlap": args.overlap, "pad": args.pad,
              "use_gpu": True, "prefer_dml": False,
              "mode": "aesthetics" if args.mode == "defined" else "classic",
              "batch_size": args.batch_size}
    if args.operation == "syqon_parallax_correct":
        model = args.model
        if not model.is_file():
            raise FileNotFoundError(f"Parallax model not found: {model}")
        result, info = parallax_correction_rgb01(inference_image, str(model), **common)
    elif args.operation == "syqon_parallax_sharpen":
        model = args.model
        if not model.is_file():
            raise FileNotFoundError(f"Parallax model not found: {model}")
        result, info = parallax_sharpen_rgb01(inference_image, str(model), args.alpha, **common)
    else:
        model = args.model
        if not model.is_file():
            raise FileNotFoundError(f"Parallax model not found: {model}")
        result, info = parallax_star_reduce_rgb01(inference_image, str(model), args.level, **common)
    if mtf_params is not None:
        result = _invert_mtf_unlinked_rgb(result, mtf_params)
    _save(args.output, result, header, mono, scale)
    return info


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("operation", choices=(
        "syqon_parallax_correct", "syqon_parallax_sharpen",
        "syqon_parallax_star_reduce",
    ))
    value.add_argument("-i", "--input", type=Path, required=True)
    value.add_argument("-o", "--output", type=Path, required=True)
    value.add_argument("--tile", type=int, default=512)
    value.add_argument("--overlap", type=int, default=64)
    value.add_argument("--pad", type=int, default=96)
    value.add_argument("--batch-size", default="Auto")
    value.add_argument("--alpha", type=float, default=0.5)
    value.add_argument("--level", type=int, default=5)
    value.add_argument("--mode", choices=("natural", "defined"), default="natural")
    value.add_argument("--model", type=Path, required=True,
                       help="Private model path selected by the worker contract")
    value.add_argument("--mtf", dest="use_mtf",
                       action=argparse.BooleanOptionalAction, default=True)
    value.add_argument("--mtf-target", type=float, default=0.10)
    return value


if __name__ == "__main__":
    run(parser().parse_args())
