"""Pure, independently callable extractors for grading-evidence measurements.

The functions in this module deliberately have no production call sites.  They
accept normalized image arrays and caller-supplied semantic masks/catalogues,
then persist reproducible facts through :mod:`nas_server.grading_evidence`.
"""
from __future__ import annotations

import hashlib
import numpy as np

from nas_server.grading_evidence import record_measurement
from nas_server.quality_regions import REGION_CONTRACT_VERSION, semantic_regions

MASK_VERSION = REGION_CONTRACT_VERSION


def _image(image) -> np.ndarray:
    a = np.asarray(image, dtype=np.float64)
    if a.ndim == 2:
        a = a[..., None]
    if a.ndim != 3 or a.shape[2] not in (1, 3) or not np.isfinite(a).all():
        raise ValueError("image must be a finite HxW or HxWx(1|3) array")
    return a


def _mask(mask, shape) -> np.ndarray:
    a = np.asarray(mask, dtype=bool)
    if a.shape != tuple(shape):
        raise ValueError("mask shape must match image")
    return a


def artifact_digest(image) -> str:
    """Return a stable digest when the caller does not already have one."""
    a = _image(image)
    return hashlib.sha256(a.astype("<f8", copy=False).tobytes()).hexdigest()


def _percentiles(values, ps=(10, 50, 90)):
    if len(values) == 0:
        return None
    return {f"p{p}": float(np.percentile(values, p)) for p in ps}


def _mad(values) -> float:
    med = np.median(values)
    return float(1.4826 * np.median(np.abs(values - med)))


def _record_many(name: str, values: dict, *, artifact_sha256: str,
                 source_stage: str, context: dict, region: dict,
                 integrity="valid", intermediate=None) -> list[str]:
    ids = []
    for metric, (value, units, population) in values.items():
        ids.append(record_measurement(
            operation_key=f"{artifact_sha256}:{source_stage}:{name}/1.0:{metric}",
            metric=metric, value=value if integrity not in ("invalid", "unavailable") else None,
            units=units, population=population, region=region,
            region_mask_version=MASK_VERSION, method=f"{name}/1.0",
            uncertainty={"kind": "robust_sampling", "value": 0.0 if value is not None else 1.0},
            integrity=integrity, artifact_sha256=artifact_sha256,
            source_stage=source_stage, measurement_version=f"{name}/1.0",
            context=context, intermediate_outputs=intermediate or {}))
    return ids


def extract_noise(image, *, sky_mask, artifact_sha256, source_stage="final_preview",
                  context=None):
    a = _image(image); regions=semantic_regions(a.shape[:2], true_sky=sky_mask)
    sky=regions.mask("true_sky"); context = context or {}
    if sky.sum() < 16:
        return _record_many("noise", {"background_sigma": (None, "normalized", "true_sky")},
                            artifact_sha256=artifact_sha256, source_stage=source_stage,
                            context=context, region={"method": MASK_VERSION, "coverage": float(sky.mean())},
                            integrity="unavailable")
    pixels = a[sky]; lum = pixels.mean(axis=1)
    hf = np.diff(a, axis=0)[sky[1:]]
    channels = [_mad(pixels[:, c]) for c in range(a.shape[2])]
    row_medians = [np.median(a[y][sky[y]]) for y in range(a.shape[0]) if sky[y].any()]
    column_medians = [np.median(a[:, x][sky[:, x]]) for x in range(a.shape[1])
                      if sky[:, x].any()]
    values = {
        "background_sigma": (_mad(lum), "normalized", "true_sky"),
        "background_channel_sigma": (channels, "normalized", "true_sky"),
        "high_frequency_power": ({"luminance": float(np.mean(hf.mean(axis=1) ** 2)) if hf.size else 0.0,
                                  "channels": np.mean(hf * hf, axis=0).tolist() if hf.size else [0.0] * a.shape[2]}, "power", "true_sky"),
        "correlated_noise": (float(np.mean((lum[:-1]-lum.mean())*(lum[1:]-lum.mean()))) if len(lum)>1 else 0.0, "covariance", "true_sky"),
        "pattern_noise": ({"row_median_sigma": _mad(np.asarray(row_medians)),
                           "column_median_sigma": _mad(np.asarray(column_medians))},
                          "normalized", "true_sky"),
    }
    return _record_many("noise", values, artifact_sha256=artifact_sha256,
                        source_stage=source_stage, context=context,
                        region={"method": MASK_VERSION, "coverage": float(sky.mean())})


def extract_gradient(image, *, sky_mask, artifact_sha256, source_stage="final_preview",
                     context=None, mesh=4, min_cells=6, sigma_clip=3.0,
                     clip_iterations=3):
    a = _image(image)
    regions = semantic_regions(a.shape[:2], true_sky=sky_mask)
    sky = regions.mask("true_sky")
    context = dict(context or {}) | {"measurement_method_parameters": {
        "mesh": mesh, "min_cells": min_cells, "sigma_clip": sigma_clip,
        "clip_iterations": clip_iterations, "surface_order": 1,
        "sufficiency_rule": "engineering-support/1.0",
    }}
    h, w = sky.shape; cells=[]; rejected=[]
    for iy in range(mesh):
        for ix in range(mesh):
            ys=slice(iy*h//mesh,(iy+1)*h//mesh); xs=slice(ix*w//mesh,(ix+1)*w//mesh)
            m=sky[ys, xs]
            if m.mean() < .25 or m.sum() < 4:
                rejected.append({"cell": [iy, ix], "reason": "insufficient_verified_sky"}); continue
            v=a[ys, xs][m]
            keep = np.ones(len(v), dtype=bool)
            for _ in range(clip_iterations):
                med=np.median(v[keep], axis=0); dev=np.abs(v-med)
                mad=np.median(dev[keep], axis=0)
                revised=np.all(dev <= np.maximum(sigma_clip * mad, 1e-12), axis=1)
                if np.array_equal(revised, keep): break
                keep=revised
            if keep.sum() < 4:
                rejected.append({"cell": [iy, ix], "reason": "sigma_clip"}); continue
            cells.append(((ix+.5)/mesh,(iy+.5)/mesh,np.median(v[keep],axis=0)))
    region={"method": MASK_VERSION, "coverage": float(sky.mean())}
    # A plane can be numerically solvable while being supported only by a
    # small corner of the image.  Require both full rank and broad coverage so
    # the reported whole-frame gradient is structurally justified.
    positions = np.asarray([[x, y] for x, y, _ in cells], dtype=float)
    fit_rank = (int(np.linalg.matrix_rank(
        np.column_stack((np.ones(len(positions)), positions))))
        if len(positions) else 0)
    quadrants = ({(int(x >= 0.5), int(y >= 0.5)) for x, y in positions}
                 if len(positions) else set())
    x_span = float(np.ptp(positions[:, 0])) if len(positions) else 0.0
    y_span = float(np.ptp(positions[:, 1])) if len(positions) else 0.0
    spatial_support = {
        "rule": "planar-fit-spatial-support/1.0",
        "fit_coefficients": 3,
        "design_rank": fit_rank,
        "quadrants": len(quadrants),
        "x_span": x_span,
        "y_span": y_span,
        "sufficient": (fit_rank == 3 and len(quadrants) >= 3
                       and x_span >= 0.5 and y_span >= 0.5),
    }
    if len(cells) < min_cells or not spatial_support["sufficient"]:
        if len(cells) >= min_cells:
            rejected.append({"reason": "inadequate_spatial_support",
                             "evidence": spatial_support})
        return _record_many("gradient", {"gradient_peak_to_peak": (None,"normalized","true_sky")},
            artifact_sha256=artifact_sha256, source_stage=source_stage, context=context,
            region=region, integrity="unavailable", intermediate={
                "accepted_cells": len(cells), "rejected_cells": rejected,
                "spatial_support": spatial_support})
    xy=np.array([[1,x,y] for x,y,_ in cells]); z=np.array([v for _,_,v in cells])
    coef=np.linalg.lstsq(xy,z,rcond=None)[0]; model=xy@coef; residual=z-model
    slopes=coef[1:].T.tolist()
    values={
        "gradient_peak_to_peak": ((model.max(axis=0)-model.min(axis=0)).tolist(),"normalized","true_sky"),
        "gradient_model_rms": (float(np.sqrt(np.mean((model-np.median(model,axis=0))**2))),"normalized","true_sky"),
        "gradient_residual_rms": (float(np.sqrt(np.mean(residual**2))),"normalized","true_sky"),
        "gradient_directional_slope": (slopes,"normalized_per_frame","true_sky"),
        "gradient_chromatic_slopes": ({"channel_slopes": slopes,
                                        "relative_to_green": [[v-slopes[1][i] for i,v in enumerate(s)] for s in slopes] if len(slopes)==3 else []},"normalized_per_frame","true_sky"),
        "gradient_usable_cell_fraction": (len(cells)/(mesh*mesh),"fraction","sky_mesh"),
        "gradient_mask_coverage": (float(1-sky.mean()),"fraction","frame"),
    }
    return _record_many("gradient", values, artifact_sha256=artifact_sha256,
        source_stage=source_stage, context=context, region=region,
        intermediate={"accepted_cells": len(cells), "rejected_cells": rejected,
                      "surface_order": 1, "spatial_support": spatial_support})


def extract_star_roundness(*, stars, image_shape, artifact_sha256,
                           source_stage="final_preview", context=None,
                           bad_eccentricity=.6):
    stars=list(stars); context=context or {}; h,w=image_shape[:2]
    star_pixels=np.zeros((h,w),dtype=bool)
    for star in stars:
        if "x" in star and "y" in star:
            star_pixels[min(h-1,max(0,int(star["y"]))),
                        min(w-1,max(0,int(star["x"])))] = True
    regions=semantic_regions((h,w), stars=star_pixels)
    required={"x","y","fwhm","eccentricity","saturated"}
    if not stars or any(not required <= s.keys() for s in stars):
        return _record_many("star-roundness", {"star_count": (None,"count","stars")}, artifact_sha256=artifact_sha256,
            source_stage=source_stage, context=context, region={"method":"catalogue/1.0","coverage":0.0}, integrity="unavailable")
    def summary(group):
        return {"count":len(group), "fwhm":_percentiles([s["fwhm"] for s in group]),
                "eccentricity":_percentiles([s["eccentricity"] for s in group],(50,90)),
                "bad_shape_fraction":sum(s["eccentricity"]>bad_eccentricity for s in group)/len(group) if group else None,
                "saturated_fraction":sum(bool(s["saturated"]) for s in group)/len(group) if group else None}
    for s in stars:
        r=np.hypot((s["x"]-w/2)/(w/2),(s["y"]-h/2)/(h/2)); s["_bin"]="center" if r<.33 else "mid" if r<.67 else "edge"
    dist={"all":summary(stars)} | {b:summary([s for s in stars if s["_bin"]==b]) for b in ("center","mid","edge")}
    values={"star_count":(len(stars),"count","stars"),"star_shape_distribution":(dist,"distribution","stars")}
    return _record_many("star-roundness", values, artifact_sha256=artifact_sha256, source_stage=source_stage,
                        context=context, region=regions.provenance(
                            "stars", derivation="catalogue-field-radius-bins/1.0"))


def extract_stretch(image, *, sky_mask, target_mask, artifact_sha256, core_mask=None,
                    source_stage="final_preview", context=None):
    a=_image(image); regions=semantic_regions(a.shape[:2], true_sky=sky_mask,
        target=target_mask, core=core_mask)
    sky=regions.mask("true_sky"); target=regions.mask("target"); context=context or {}
    if sky.sum()<8 or target.sum()<8:
        return _record_many("stretch", {"stretch_percentiles":(None,"normalized","frame")}, artifact_sha256=artifact_sha256,
            source_stage=source_stage, context=context, region={"method":MASK_VERSION,"coverage":float((sky|target).mean())}, integrity="unavailable")
    lum=a.mean(axis=2); core = target if core_mask is None else regions.mask("core"); values={
        "stretch_percentiles":(_percentiles(lum.ravel(),(50,95,99,99.9)),"normalized","frame"),
        "dark_anchor":(float(np.median(lum[sky])),"normalized","true_sky"),
        "target_background_separation":(float(np.median(lum[target])-np.median(lum[sky])),"normalized","target_vs_sky"),
        "shadow_clip_fraction":(float(np.mean(lum[sky]<=.001)),"fraction","true_sky"),
        "highlight_clip_fraction":(float(np.mean(lum[target]>=.999)),"fraction","target"),
        "core_preservation":(float(1-np.mean(lum[core]>=.999)) if core.any() else 0.0,"fraction","target_core"),
        "frame_fill_fraction":(float(target.mean()),"fraction","frame"),
    }
    return _record_many("stretch",values,artifact_sha256=artifact_sha256,source_stage=source_stage,context=context,
                        region={"method":MASK_VERSION,"coverage":float((sky|target).mean())})


def extract_color_balance(image, *, sky_mask, target_mask, artifact_sha256, star_mask=None,
                          source_stage="final_preview", context=None):
    a=_image(image)
    regions=semantic_regions(a.shape[:2], true_sky=sky_mask, target=target_mask,
                             stars=star_mask)
    sky=regions.mask("true_sky"); target=regions.mask("target"); context=context or {}
    if a.shape[2]!=3 or sky.sum()<8:
        return _record_many("color", {"sky_channel_ratios":(None,"ratio","true_sky")}, artifact_sha256=artifact_sha256,
            source_stage=source_stage, context=context, region={"method":MASK_VERSION,"coverage":float(sky.mean())}, integrity="unavailable")
    med=np.median(a[sky],axis=0); ratios={"r_over_g":float(med[0]/med[1]),"b_over_g":float(med[2]/med[1])} if med[1] else {}
    ys,xs=np.where(sky); design=np.column_stack((np.ones(len(xs)),xs/max(1,a.shape[1]-1),ys/max(1,a.shape[0]-1)))
    slopes=np.linalg.lstsq(design,a[sky],rcond=None)[0][1:].T.tolist()
    stars=regions.mask("stars")
    values={"sky_channel_ratios":(ratios,"ratio","true_sky"),
            "sky_chromatic_gradients":(slopes,"normalized_per_frame","true_sky"),
            "target_channel_distribution":({"r":_percentiles(a[target,0]),"g":_percentiles(a[target,1]),"b":_percentiles(a[target,2])},"distribution","target"),
            "star_color_spread":(_mad((a[stars,2]-a[stars,0])) if stars.any() else 0.0,"normalized","stars"),
            "sky_contamination_estimate":(float(1-sky.mean()),"fraction","candidate_sky"),
            "sky_mask_coverage":(float(sky.mean()),"fraction","frame")}
    return _record_many("color",values,artifact_sha256=artifact_sha256,source_stage=source_stage,context=context,
                        region={"method":MASK_VERSION,"coverage":float(sky.mean())})


def extract_dynamic_range(image, *, target_mask, core_mask, star_mask,
                          artifact_sha256, source_stage="final_preview", context=None):
    a=_image(image); lum=a.mean(axis=2)
    regions=semantic_regions(lum.shape,target=target_mask,core=core_mask,stars=star_mask)
    target=regions.mask("target"); core=regions.mask("core"); stars=regions.mask("stars"); context=context or {}
    if target.sum()<8:
        return _record_many("dynamic-range", {"percentile_headroom":(None,"normalized","target")}, artifact_sha256=artifact_sha256,
            source_stage=source_stage, context=context, region={"method":MASK_VERSION,"coverage":float(target.mean())}, integrity="unavailable")
    values={"shadow_clip_fraction":(float(np.mean(lum[target]<=.001)),"fraction","target"),
            "highlight_clip_fractions":({str(t):float(np.mean(lum[target]>=t)) for t in (.95,.98,.999)},"fraction","target"),
            "core_saturation_fraction":(float(np.mean(lum[core]>=.999)) if core.any() else 0.0,"fraction","target_core"),
            "stellar_saturation_fraction":(float(np.mean(lum[stars]>=.999)) if stars.any() else 0.0,"fraction","stars"),
            "percentile_headroom":(float(1-np.percentile(lum[target],99.9)),"normalized","target")}
    values["target_local_contrast"]=(float(np.percentile(lum[target],90)-np.percentile(lum[target],10)),"normalized","target")
    return _record_many("dynamic-range",values,artifact_sha256=artifact_sha256,source_stage=source_stage,context=context,
                        region={"method":MASK_VERSION,"coverage":float(target.mean())})


def extract_detail_preservation(image, *, target_mask, artifact_sha256,
                                comparison=None, comparison_ref=None,
                                stellar_fwhm=None,
                                source_stage="final_preview", context=None):
    a=_image(image); regions=semantic_regions(a.shape[:2],target=target_mask)
    target=regions.mask("target"); context=context or {}
    registration = (comparison_ref or {}).get("registration") or {}
    identity_ok = bool(comparison is not None and comparison_ref
        and comparison_ref.get("artifact_sha256") == artifact_digest(comparison)
        and comparison_ref.get("source_stage")
        and registration.get("valid") is True
        and registration.get("transform_id"))
    if not identity_ok or target.sum()<8:
        return _record_many("detail", {"detail_retention":(None,"ratio","target")}, artifact_sha256=artifact_sha256,
            source_stage=source_stage, context=context | {
                "comparison_identity": comparison_ref or {},
                "comparison_verified": False,
            }, region={"method":MASK_VERSION,"coverage":float(target.mean())}, integrity="unavailable")
    before=_image(comparison)
    if before.shape!=a.shape: raise ValueError("registered comparison shape must match image")
    def energy(x):
        lum=x.mean(axis=2); gy,gx=np.gradient(lum); return float(np.mean((gx[target]**2+gy[target]**2)))
    eb,ea=energy(before),energy(a)
    values={"detail_retention":(float(ea/eb) if eb else 0.0,"ratio","target"),
            "spatial_frequency_energy":(ea,"power","target"),
            "stellar_fwhm":(float(stellar_fwhm) if stellar_fwhm is not None else 0.0,"pixels","stars")}
    return _record_many("detail",values,artifact_sha256=artifact_sha256,source_stage=source_stage,
                        context=context | {"comparison_identity": comparison_ref,
                                           "comparison_verified": True},
                        region={"method":MASK_VERSION,"coverage":float(target.mean())})


def extract_composition(*, target_mask, artifact_sha256, input_target_mask=None,
                        orientation=None, wcs_valid=False, source_stage="final_preview", context=None):
    raw_target=np.asarray(target_mask,dtype=bool)
    target=(semantic_regions(raw_target.shape,target=raw_target).mask("target")
            if raw_target.ndim==2 else raw_target); context=context or {}
    if target.ndim!=2 or not target.any():
        return _record_many("composition", {"target_centroid":(None,"normalized_xy","target")}, artifact_sha256=artifact_sha256,
            source_stage=source_stage, context=context, region={"method":MASK_VERSION,"coverage":0.0}, integrity="unavailable")
    ys,xs=np.where(target); h,w=target.shape; bbox=[int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)]
    margins=[bbox[0]/w,bbox[1]/h,(w-bbox[2])/w,(h-bbox[3])/h]
    crop=None
    if input_target_mask is not None:
        original=np.asarray(input_target_mask,dtype=bool)
        if original.shape==target.shape and original.sum(): crop=float(max(0,1-target.sum()/original.sum()))
    values={"target_centroid":([float(xs.mean()/w),float(ys.mean()/h)],"normalized_xy","target"),
            "target_bounding_box":(bbox,"pixels","target"),"target_coverage":(float(target.mean()),"fraction","target"),
            "target_margins":(margins,"fraction","frame_edges")}
    if crop is not None: values["crop_loss_fraction"]=(crop,"fraction","target_footprint")
    if orientation is not None: values["orientation"]=(orientation,"degrees","frame")
    return _record_many("composition",values,artifact_sha256=artifact_sha256,source_stage=source_stage,
                        context=context|{"wcs_valid":bool(wcs_valid)},region={"method":MASK_VERSION,"coverage":float(target.mean())})
