"""Deterministic, observe-only Image Atlas measurements (Milestone 2).

Nothing in the production pipeline imports this module.  It describes spatial
evidence on a fixed tile grid and deliberately makes no semantic classification,
mask, processing decision, or probability claim.
"""
from __future__ import annotations

import hashlib
import math
import os
import resource
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from astropy.io import fits
from astropy.stats import mad_std
from scipy.ndimage import distance_transform_edt, gaussian_filter

from nas_server.image_analysis_runner import ImageAnalysisError, analyze
from nas_server.image_atlas import (
    AtlasArrayRef,
    AtlasLayer,
    AtlasManifestError,
    AtlasSnapshot,
    ValidityState,
    new_layer,
    new_snapshot,
    write_snapshot_atomic,
)

METHOD_VERSION = "m2.1"
NATIVE_PIXEL_SCALE = 2.9
NATIVE_TILE_PX = 64
ANGULAR_SIGMAS_ARCSEC = (2.9, 5.8, 11.6)


@dataclass(frozen=True)
class FamilyTelemetry:
    wall_seconds: float
    peak_rss_kib: int
    output_bytes: int


@dataclass(frozen=True)
class AtlasMeasurementResult:
    snapshot: AtlasSnapshot
    arrays: Mapping[str, np.ndarray]
    telemetry: Mapping[str, FamilyTelemetry]


def _array_hash(array: np.ndarray) -> str:
    canonical = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(canonical.dtype).encode("ascii"))
    digest.update(repr(canonical.shape).encode("ascii"))
    digest.update(canonical.tobytes())
    return digest.hexdigest()


def _luminance(data: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    value = np.asarray(data, dtype=np.float32)
    if value.ndim == 2:
        lum = value
    elif value.ndim == 3 and value.shape[0] <= 4:
        lum = (0.3 * value[0] + 0.5 * value[min(1, value.shape[0] - 1)]
               + 0.2 * value[min(2, value.shape[0] - 1)])
    elif value.ndim == 3 and value.shape[-1] <= 4:
        lum = (0.3 * value[..., 0] + 0.5 * value[..., min(1, value.shape[-1] - 1)]
               + 0.2 * value[..., min(2, value.shape[-1] - 1)])
    else:
        raise ValueError("Atlas input must be a 2-D image or a 1-4 channel image")
    finite = np.isfinite(lum)
    if not finite.any():
        return lum, {"normalization": "none", "input_min": None, "input_max": None}
    low = float(np.min(lum[finite]))
    high = float(np.max(lum[finite]))
    if low < 0.0 or high > 1.0:
        span = max(high - low, 1e-12)
        lum = (lum - low) / span
        normalization = "finite_minmax_to_0_1"
    else:
        normalization = "identity_0_1"
    return lum.astype(np.float32), {
        "normalization": normalization, "input_min": low, "input_max": high,
    }


def _tile_shape(pixel_scale: float) -> int:
    return max(16, int(round(NATIVE_TILE_PX * NATIVE_PIXEL_SCALE / pixel_scale)))


def _valid_mask(data: np.ndarray, lum: np.ndarray) -> np.ndarray:
    """Treat non-finite samples and all-channel exact-zero stack padding as unsupported."""
    value = np.asarray(data)
    if value.ndim == 2:
        nonzero = value != 0
    elif value.ndim == 3 and value.shape[0] <= 4:
        nonzero = np.any(value != 0, axis=0)
    elif value.ndim == 3 and value.shape[-1] <= 4:
        nonzero = np.any(value != 0, axis=-1)
    else:
        nonzero = np.ones_like(lum, dtype=bool)
    return np.isfinite(lum) & nonzero


def _tile_reduce(array: np.ndarray, valid: np.ndarray, tile_px: int,
                 reducer: Callable[[np.ndarray], float]) -> tuple[np.ndarray, np.ndarray]:
    height, width = array.shape
    rows = math.ceil(height / tile_px)
    cols = math.ceil(width / tile_px)
    values = np.full((rows, cols), np.nan, dtype=np.float32)
    support = np.zeros((rows, cols), dtype=np.int32)
    for row in range(rows):
        y0, y1 = row * tile_px, min((row + 1) * tile_px, height)
        for col in range(cols):
            x0, x1 = col * tile_px, min((col + 1) * tile_px, width)
            mask = valid[y0:y1, x0:x1]
            support[row, col] = int(mask.sum())
            if support[row, col]:
                values[row, col] = reducer(array[y0:y1, x0:x1][mask])
    return values, support


def _median(values: np.ndarray) -> float:
    return float(np.median(values))


def _quantile(q: float) -> Callable[[np.ndarray], float]:
    return lambda values: float(np.quantile(values, q))


def _mad(values: np.ndarray) -> float:
    return float(mad_std(values, ignore_nan=True))


def _mean_abs(values: np.ndarray) -> float:
    return float(np.mean(np.abs(values)))


def _peak_abs(values: np.ndarray) -> float:
    return float(np.quantile(np.abs(values), 0.90))


def _family_timer(function: Callable[[], dict[str, np.ndarray]]) -> tuple[dict[str, np.ndarray], FamilyTelemetry]:
    started = time.perf_counter()
    arrays = function()
    elapsed = time.perf_counter() - started
    size = sum(np.ascontiguousarray(value).nbytes for value in arrays.values())
    return arrays, FamilyTelemetry(elapsed, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, size)


def _support_geometry(lum: np.ndarray, valid: np.ndarray, tile_px: int) -> dict[str, np.ndarray]:
    ones = np.ones_like(lum, dtype=np.float32)
    valid_fraction, raw = _tile_reduce(ones, np.ones_like(valid), tile_px, _median)
    usable, _ = _tile_reduce(valid.astype(np.float32), np.ones_like(valid), tile_px, np.sum)
    raw = np.empty_like(usable, dtype=np.int32)
    height, width = lum.shape
    for row in range(raw.shape[0]):
        for col in range(raw.shape[1]):
            raw[row, col] = ((min((row + 1) * tile_px, height) - row * tile_px)
                             * (min((col + 1) * tile_px, width) - col * tile_px))
    valid_fraction = usable / np.maximum(raw, 1)
    edge_distance = distance_transform_edt(valid)
    edge, _ = _tile_reduce(edge_distance, valid, tile_px, _median)
    return {
        "support.raw_pixels": raw,
        "support.usable_pixels": usable.astype(np.int32),
        "support.valid_fraction": valid_fraction.astype(np.float32),
        "support.edge_distance_px": edge,
    }


def _intensity(lum: np.ndarray, valid: np.ndarray, tile_px: int) -> dict[str, np.ndarray]:
    fields: dict[str, np.ndarray] = {}
    for name, reducer in (
        ("center", _median), ("q05", _quantile(0.05)), ("q95", _quantile(0.95)),
    ):
        fields[f"intensity.{name}"], _ = _tile_reduce(lum, valid, tile_px, reducer)
    fields["intensity.tonal_range"] = fields["intensity.q95"] - fields["intensity.q05"]
    fields["intensity.highlight_headroom"] = 1.0 - fields["intensity.q95"]
    near_black, _ = _tile_reduce((lum <= 0.01).astype(np.float32), valid, tile_px, np.mean)
    fields["intensity.near_black_fraction"] = near_black
    return fields


def _residuals(lum: np.ndarray, valid: np.ndarray, tile_px: int,
               pixel_scale: float) -> tuple[dict[str, np.ndarray], list[np.ndarray]]:
    fields: dict[str, np.ndarray] = {}
    bands: list[np.ndarray] = []
    for index, arcsec in enumerate(ANGULAR_SIGMAS_ARCSEC):
        sigma = arcsec / pixel_scale
        smooth_a = gaussian_filter(lum, sigma=sigma, mode="reflect")
        smooth_b = gaussian_filter(lum, sigma=sigma * 2.0, mode="reflect")
        band = (smooth_a - smooth_b).astype(np.float32)
        bands.append(band)
        spread, _ = _tile_reduce(band, valid, tile_px, _mad)
        response, _ = _tile_reduce(band, valid, tile_px, _peak_abs)
        detectability = response / np.maximum(spread, 1e-12)
        fields[f"noise.residual_sigma_s{index}"] = spread
        fields[f"detectability.score_s{index}"] = detectability.astype(np.float32)
    return fields, bands


def _multiscale(bands: Sequence[np.ndarray], valid: np.ndarray,
                tile_px: int) -> dict[str, np.ndarray]:
    fields: dict[str, np.ndarray] = {}
    energies = []
    for index, band in enumerate(bands):
        energy, _ = _tile_reduce(band * band, valid, tile_px,
                                 lambda values: float(np.sqrt(np.mean(values))))
        fields[f"structure.band_energy_s{index}"] = energy
        energies.append(energy)
    coherence = np.minimum(energies[0], energies[1]) / np.maximum(
        np.maximum(energies[0], energies[1]), 1e-12)
    fields["structure.adjacent_scale_coherence"] = coherence.astype(np.float32)
    fields["structure.high_frequency_risk"] = (
        energies[0] / np.maximum(energies[1], 1e-12)
    ).astype(np.float32)
    return fields


def _star_support(lum: np.ndarray, tile_px: int, sources: Sequence[Mapping[str, Any]] | None,
                  grid_shape: tuple[int, int]) -> tuple[dict[str, np.ndarray], ValidityState]:
    names = ("source_count", "fwhm_median", "eccentricity_median",
             "bright_core_count", "halo_wing_risk")
    fields = {f"stars.{name}": np.full(grid_shape, np.nan, dtype=np.float32) for name in names}
    if sources is None:
        return fields, ValidityState.UNKNOWN
    buckets: list[list[list[Mapping[str, Any]]]] = [
        [[] for _ in range(grid_shape[1])] for _ in range(grid_shape[0])
    ]
    for source in sources:
        row, col = int(float(source["y"]) // tile_px), int(float(source["x"]) // tile_px)
        if 0 <= row < grid_shape[0] and 0 <= col < grid_shape[1]:
            buckets[row][col].append(source)
    bright_threshold = float(np.quantile(lum[np.isfinite(lum)], 0.999))
    for row in range(grid_shape[0]):
        for col in range(grid_shape[1]):
            tile_sources = buckets[row][col]
            fields["stars.source_count"][row, col] = len(tile_sources)
            if not tile_sources:
                continue
            fwhm = np.array([float(source["fwhm"]) for source in tile_sources])
            ecc = np.array([float(source["eccentricity"]) for source in tile_sources])
            bright = 0
            for source in tile_sources:
                y = min(max(int(round(float(source["y"]))), 0), lum.shape[0] - 1)
                x = min(max(int(round(float(source["x"]))), 0), lum.shape[1] - 1)
                bright += int(lum[y, x] >= bright_threshold)
            fields["stars.fwhm_median"][row, col] = np.median(fwhm)
            fields["stars.eccentricity_median"][row, col] = np.median(ecc)
            fields["stars.bright_core_count"][row, col] = bright
            fields["stars.halo_wing_risk"][row, col] = np.quantile(fwhm, 0.90) * math.sqrt(len(fwhm))
    return fields, ValidityState.SUPPORTED


def _gradient_artifact(lum: np.ndarray, valid: np.ndarray, tile_px: int,
                       bands: Sequence[np.ndarray], processing_steps: Sequence[str]) -> dict[str, np.ndarray]:
    broad = gaussian_filter(lum, sigma=max(tile_px / 2.0, 1.0), mode="reflect")
    gy, gx = np.gradient(broad)
    gradient = np.hypot(gx, gy)
    gradient_field, _ = _tile_reduce(gradient, valid, tile_px, _median)
    broad_residual, _ = _tile_reduce(lum - broad, valid, tile_px, _mad)
    texture, _ = _tile_reduce(bands[0], valid, tile_px, _mad)
    grid_shape = gradient_field.shape
    seam = np.zeros(grid_shape, dtype=np.float32)
    seam[:, 1:] = np.abs(np.diff(gradient_field, axis=1))
    if grid_shape[0] > 1:
        seam[1:, :] = np.maximum(seam[1:, :], np.abs(np.diff(gradient_field, axis=0)))
    return {
        "risk.broad_gradient": gradient_field,
        "risk.broad_model_residual": broad_residual,
        "risk.seam_discontinuity": seam,
        "risk.texture_anomaly": texture,
        "provenance.post_processing": np.full(
            grid_shape, 1.0 if processing_steps else 0.0, dtype=np.float32),
    }


def _metadata_for(name: str, *, tile_px: int, pixel_scale: float,
                  support_names: tuple[str, str], processing_steps: Sequence[str]) -> dict[str, Any]:
    family = name.split(".", 1)[0]
    units = {
        "support": "pixels_or_fraction", "intensity": "normalized_luminance",
        "noise": "normalized_luminance", "detectability": "dimensionless_score_not_probability",
        "structure": "normalized_luminance_response", "stars": "count_or_pixels",
        "risk": "dimensionless_indicator", "provenance": "boolean_indicator",
    }[family]
    estimator = (
        "finite_nonzero_support_and_euclidean_edge_distance" if family == "support" else
        "local_quantile_summary" if family == "intensity" else
        "difference_of_gaussians_local_mad_sigma" if family == "noise" else
        "difference_of_gaussians_p90_response_over_local_mad_score" if family == "detectability" else
        "difference_of_gaussians_rms_and_adjacent_scale_ratio" if family == "structure" else
        "crash_isolated_sep_sources_projected_to_tile_grid" if family == "stars" else
        "broad_gaussian_gradient_residual_and_discontinuity" if family == "risk" else
        "declared_checkpoint_processing_history"
    )
    metadata = {
        "estimator": estimator,
        "tile_px": tile_px,
        "tile_arcsec": tile_px * pixel_scale,
        "raw_support_layer": support_names[0],
        "usable_support_layer": support_names[1],
        "units": units,
        "correlation_resampling_caveat": (
            "Stacking, drizzle, registration and Gaussian filtering correlate adjacent samples; "
            "pixel count is not independent-sample count."
        ),
        "processing_steps": list(processing_steps),
        "semantic_claim": "none",
    }
    index = next((value for value in range(len(ANGULAR_SIGMAS_ARCSEC))
                  if name.endswith(f"_s{value}")), None)
    if index is not None:
        metadata["scale_sigma_arcsec"] = ANGULAR_SIGMAS_ARCSEC[index]
        metadata["scale_sigma_px"] = ANGULAR_SIGMAS_ARCSEC[index] / pixel_scale
    if family == "stars":
        metadata["source_support_layer"] = "stars.source_count"
        metadata["native_failure_semantics"] = "unknown; never numeric fallback evidence"
    return metadata


def measure_atlas(data: np.ndarray, *, checkpoint_id: str, checkpoint_hash: str,
                  pixel_scale_arcsec: float, coordinate_frame_id: str,
                  sources: Sequence[Mapping[str, Any]] | None = None,
                  processing_steps: Sequence[str] = ()) -> AtlasMeasurementResult:
    """Compute deterministic spatial evidence without changing any pixels."""
    lum, normalization = _luminance(data)
    valid = _valid_mask(data, lum)
    tile_px = _tile_shape(pixel_scale_arcsec)
    arrays: dict[str, np.ndarray] = {}
    telemetry: dict[str, FamilyTelemetry] = {}

    support, telemetry["support_geometry"] = _family_timer(
        lambda: _support_geometry(lum, valid, tile_px))
    arrays.update(support)
    intensity, telemetry["intensity_dynamic_range"] = _family_timer(
        lambda: _intensity(lum, valid, tile_px))
    arrays.update(intensity)
    started = time.perf_counter()
    residual_fields, bands = _residuals(lum, valid, tile_px, pixel_scale_arcsec)
    telemetry["noise_detectability"] = FamilyTelemetry(
        time.perf_counter() - started, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        sum(value.nbytes for value in residual_fields.values()))
    arrays.update(residual_fields)
    multiscale, telemetry["multiscale_structure"] = _family_timer(
        lambda: _multiscale(bands, valid, tile_px))
    arrays.update(multiscale)
    star_fields, star_state = _star_support(
        lum, tile_px, sources, arrays["support.raw_pixels"].shape)
    arrays.update(star_fields)
    telemetry["star_compact_source"] = FamilyTelemetry(
        0.0, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        sum(value.nbytes for value in star_fields.values()))
    risks, telemetry["gradient_artifact_risk"] = _family_timer(
        lambda: _gradient_artifact(lum, valid, tile_px, bands, processing_steps))
    arrays.update(risks)

    layers: list[AtlasLayer] = []
    grid_shape = arrays["support.raw_pixels"].shape
    params = {
        "tile_px": tile_px, "pixel_scale_arcsec": pixel_scale_arcsec,
        "angular_sigmas_arcsec": ANGULAR_SIGMAS_ARCSEC,
        "normalization": normalization,
    }
    support_names = ("support.raw_pixels", "support.usable_pixels")
    for name, array in arrays.items():
        state = star_state if name.startswith("stars.") else ValidityState.SUPPORTED
        layer_params = dict(params, field=name)
        ref = AtlasArrayRef(
            coordinate_frame_id=coordinate_frame_id,
            shape=tuple(int(value) for value in array.shape), axes=("tile_y", "tile_x"),
            value_domain=_metadata_for(
                name, tile_px=tile_px, pixel_scale=pixel_scale_arcsec,
                support_names=support_names, processing_steps=processing_steps)["units"],
            storage_key=f"{name}.npy", data_sha256=_array_hash(array),
        )
        layers.append(new_layer(
            name=name, array_ref=ref, checkpoint_hash=checkpoint_hash,
            method="image_atlas_m2_tile_measurement", method_version=METHOD_VERSION,
            params=layer_params, validity_state=state,
            metadata=_metadata_for(
                name, tile_px=tile_px, pixel_scale=pixel_scale_arcsec,
                support_names=support_names, processing_steps=processing_steps),
        ))
    snapshot = replace(
        new_snapshot(checkpoint_id=checkpoint_id, checkpoint_hash=checkpoint_hash),
        layers=tuple(layers),
    )
    assert all(value.shape == grid_shape for value in arrays.values())
    return AtlasMeasurementResult(snapshot=snapshot, arrays=arrays, telemetry=telemetry)


def measure_checkpoint(path: str | Path, *, checkpoint_id: str, checkpoint_hash: str,
                       pixel_scale_arcsec: float, coordinate_frame_id: str,
                       processing_steps: Sequence[str] = ()) -> AtlasMeasurementResult:
    """Measure a FITS checkpoint and obtain star evidence only through isolation."""
    path = Path(path)
    with fits.open(path, memmap=True) as hdul:
        data = np.asarray(hdul[0].data, dtype=np.float32)
    sources: Sequence[Mapping[str, Any]] | None
    star_started = time.perf_counter()
    try:
        psf = analyze(str(path)).get("psf", {})
        status = psf.get("extraction_status")
        sources = (psf.get("sources") if status in {
            None, "supported-empty", "supported-sources",
        } else None)
    except (ImageAnalysisError, OSError, ValueError):
        sources = None
    star_elapsed = time.perf_counter() - star_started
    result = measure_atlas(
        data, checkpoint_id=checkpoint_id, checkpoint_hash=checkpoint_hash,
        pixel_scale_arcsec=pixel_scale_arcsec, coordinate_frame_id=coordinate_frame_id,
        sources=sources, processing_steps=processing_steps,
    )
    telemetry = dict(result.telemetry)
    previous = telemetry["star_compact_source"]
    telemetry["star_compact_source"] = FamilyTelemetry(
        star_elapsed, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        previous.output_bytes)
    return AtlasMeasurementResult(
        snapshot=result.snapshot, arrays=result.arrays, telemetry=telemetry)


def write_measurement_bundle(directory: str | Path, result: AtlasMeasurementResult) -> None:
    """Atomically write each deterministic array, then finalize the manifest last."""
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    for layer in result.snapshot.layers:
        array = result.arrays[layer.name]
        if _array_hash(array) != layer.array_ref.data_sha256:
            raise AtlasManifestError(f"array changed after layer identity was created: {layer.name}")
        final_path = destination / str(layer.array_ref.storage_key)
        temp_path = final_path.with_name(f".{final_path.name}.partial")
        with temp_path.open("wb") as handle:
            np.save(handle, array, allow_pickle=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, final_path)
    write_snapshot_atomic(destination / "atlas.json", result.snapshot)


def load_measurement_array(directory: str | Path, layer: AtlasLayer) -> np.ndarray:
    path = Path(directory) / str(layer.array_ref.storage_key)
    array = np.load(path, allow_pickle=False)
    if tuple(array.shape) != layer.array_ref.shape or _array_hash(array) != layer.array_ref.data_sha256:
        raise AtlasManifestError(f"stored array failed shape/hash verification: {layer.name}")
    return array
