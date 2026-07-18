"""Parse a manual PixInsight processing flow and diff it against the pipeline.

Reads a PixInsight project (.xosm), a process container / icon (.xpsm), or a
final image (.xisf, whose embedded ``PixInsight:ProcessingHistory`` property is
parsed). Reconstructs the chronological flow of applied processes with their
parameters, maps each PixInsight process class onto our processing-ontology
step, and optionally diffs the manual flow against what the auto_process
pipeline actually ran for the same target (derived from a run directory's
``NN_auto_<step>...`` output filenames).

Why timestamps: PixInsight stores per-view processing history. A project holds
several views, and the same applied process is duplicated across them, so we key
each applied instance by (class, time.start), de-duplicate, and sort by start
time to recover the true chronological recipe. Instances without a <time> child
(saved process-icon templates) are intentionally ignored — they were not applied.

CLI:
    python -m nas_server.manual_flow <file.xosm|.xpsm|.xisf> [--run-dir RUN_DIR]
"""
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

# PixInsight process class -> our processing-ontology step name.
# Some classes are context-dependent (PixelMath, Script) and resolved in map_step().
PI_TO_ONTOLOGY: dict[str, str] = {
    "Debayer": "debayer",
    "SubframeSelector": "subframe_culling",
    "StarAlignment": "registration",
    "ImageIntegration": "stacking",
    "FastIntegration": "stacking",
    "DrizzleIntegration": "stacking",
    "AutomaticBackgroundExtractor": "background_extraction",
    "DynamicBackgroundExtraction": "background_extraction",
    "GradientCorrection": "background_extraction",
    "GraXpert": "background_extraction",
    "ABE": "background_extraction",
    "SpectrophotometricColorCalibration": "color_calibration",
    "ColorCalibration": "color_calibration",
    "PhotometricColorCalibration": "color_calibration",
    "BackgroundNeutralization": "background_neutralize",
    "BlurXTerminator": "deconvolution",
    "Deconvolution": "deconvolution",
    "NoiseXTerminator": "denoise_linear",
    "TGVDenoise": "denoise_linear",
    "MultiscaleLinearTransform": "denoise_linear",
    "ACDNR": "noise_reduction",
    "StarXTerminator": "remove_stars_linear",
    "StarNet2": "remove_stars_linear",
    "HistogramTransformation": "stretch",
    "GeneralizedHyperbolicStretch": "stretch",
    "ArcsinhStretch": "stretch",
    "MaskedStretch": "stretch",
    "CurvesTransformation": "curves",
    "ColorSaturation": "color_sat",
    "SCNR": "scnr",
    "HDRMultiscaleTransform": "hdr_compression",
    "LocalHistogramEqualization": "dark_enhance",
    "MorphologicalTransformation": "halo_suppression",
    "UnsharpMask": "star_sharpen",
    "MultiscaleMedianTransform": "noise_reduction",
}

# Per-class parameter ids worth surfacing in the human summary (others are kept
# in the parsed dict but not printed). Empty list -> the process is curve/table
# driven and has no single scalar worth showing.
KEY_PARAMS: dict[str, list[str]] = {
    "Debayer": ["debayerMethod", "cfaPattern"],
    "DrizzleIntegration": ["scale", "dropShrink", "enableRejection"],
    "GraXpert": ["operation", "correction", "smoothing"],
    "SpectrophotometricColorCalibration": [
        "narrowbandMode", "redFilterWavelength", "greenFilterWavelength",
        "blueFilterWavelength", "applyCalibration",
    ],
    "BlurXTerminator": ["psf", "autoPSF", "sharpenStars", "sharpenNonStellar", "adjustHalos"],
    "NoiseXTerminator": ["denoise", "detail"],
    "StarXTerminator": ["stars", "unscreen", "overlap"],
    "GeneralizedHyperbolicStretch": ["St", "D", "b", "SP", "LP", "HP"],
    "SCNR": ["amount", "protectionMethod"],
    "HDRMultiscaleTransform": ["numberOfLayers", "overdrive", "toLightness"],
    "LocalHistogramEqualization": ["radius", "amount", "histogramResolution"],
    "PixelMath": ["expression"],
}

# PixInsight script filenames (lowercased, sans dir) -> ontology step.
SCRIPT_TO_ONTOLOGY: dict[str, str] = {
    "imagesolver.js": "plate_solve",
    "statisticalstretch.js": "stretch",
    "ghs.js": "stretch",
    "generalizedhyperbolicstretch.js": "stretch",
    "ihdr.js": "hdr_compression",
    "star_stretch_v2.1.js": "stretch_stars",
    "star_stretch.js": "stretch_stars",
}

_SKIP_CLASSES = {"ImageIdentifier", "ProcessContainer"}


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _parse_instance(el: ET.Element) -> dict:
    """Extract class, time, params and table row-counts from one <instance>."""
    params: dict[str, str] = {}
    tables: dict[str, int] = {}
    start = span = None
    for child in el:
        ctag = _local(child.tag)
        if ctag == "time":
            start = child.get("start")
            span = child.get("span")
        elif ctag == "parameter":
            pid = child.get("id")
            if pid is None:
                continue
            val = child.get("value")
            params[pid] = val if val is not None else (child.text or "").strip()
        elif ctag == "table":
            tid = child.get("id")
            if tid is not None:
                try:
                    tables[tid] = int(child.get("rows", "0"))
                except ValueError:
                    tables[tid] = 0
    return {
        "class": el.get("class"),
        "start": start,
        "span": span,
        "params": params,
        "tables": tables,
    }


def _collect_timed_instances(root: ET.Element) -> list[dict]:
    """All applied (timed) instances, de-duplicated by (class, start), sorted."""
    seen: dict[tuple, dict] = {}
    for el in root.iter():
        if _local(el.tag) != "instance":
            continue
        cls = el.get("class")
        if not cls or cls in _SKIP_CLASSES:
            continue
        inst = _parse_instance(el)
        if inst["start"] is None:  # not applied — a saved icon/template
            continue
        seen.setdefault((cls, inst["start"]), inst)
    return sorted(seen.values(), key=lambda d: d["start"])


def parse_flow(path: str | Path) -> list[dict]:
    """Parse .xosm / .xpsm / .xisf into an ordered list of applied process dicts."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".xisf":
        from xisf import XISF
        md = XISF(str(path)).get_images_metadata()[0]
        try:
            hist = md["XISFProperties"]["PixInsight:ProcessingHistory"]["value"]
        except KeyError:
            return []
        root = ET.fromstring(hist)
    elif suffix in (".xosm", ".xpsm"):
        root = ET.parse(str(path)).getroot()
    else:
        raise ValueError(f"unsupported file type: {suffix} (expected .xosm/.xpsm/.xisf)")
    return _collect_timed_instances(root)


def map_step(inst: dict) -> str:
    """Map a parsed instance to an ontology step, resolving context-dependent ones."""
    cls = inst["class"]
    if cls == "PixelMath":
        expr = (inst["params"].get("expression") or "").lower()
        if "star" in expr:
            return "combine_stars_screen"
        return "pixelmath_custom"
    if cls == "Script":
        fp = (inst["params"].get("filePath") or "").lower()
        name = fp.rsplit("/", 1)[-1] or "script"
        if name in SCRIPT_TO_ONTOLOGY:
            return SCRIPT_TO_ONTOLOGY[name]
        return f"script:{name}"
    return PI_TO_ONTOLOGY.get(cls, f"unmapped:{cls}")


def summarize_flow(flow: list[dict]) -> str:
    """Human-readable ordered recipe with key params and ontology mapping."""
    sessions = {(inst["start"] or "")[:10] for inst in flow}
    hdr = f"Manual PixInsight flow — {len(flow)} applied steps"
    if len(sessions) > 1:
        hdr += f" across {len(sessions)} sessions ({min(sessions)}..{max(sessions)})"
    lines = [hdr, "=" * 60]
    for i, inst in enumerate(flow, 1):
        cls = inst["class"]
        when = (inst["start"] or "")[5:19].replace("T", " ")  # MM-DD HH:MM:SS
        step = map_step(inst)
        keys = KEY_PARAMS.get(cls, [])
        shown = []
        for k in keys:
            if k in inst["params"] and inst["params"][k] != "":
                v = inst["params"][k]
                if k == "expression" and len(v) > 60:
                    v = v[:57] + "..."
                shown.append(f"{k}={v}")
        if inst["tables"]:
            shown.append("+".join(f"{k}[{n}]" for k, n in inst["tables"].items()))
        detail = ("  " + ", ".join(shown)) if shown else ""
        lines.append(f"{i:2}. {when}  {cls:32} -> {step}{detail}")
    return "\n".join(lines)


def _ontology_step_names() -> set[str]:
    path = Path(__file__).parent / "processing_ontology.json"
    try:
        return set(json.loads(path.read_text())["processing_steps"].keys())
    except Exception:
        return set()


def pipeline_steps_from_run(run_dir: str | Path) -> list[str]:
    """Recover the pipeline's executed step order from NN_auto_<step>...fit files."""
    run_dir = Path(run_dir)
    known = sorted(_ontology_step_names(), key=len, reverse=True)
    first_idx: dict[str, int] = {}
    for f in run_dir.glob("*.fit"):
        m = re.match(r"^(\d+)_auto_(.+)$", f.stem)
        if not m:
            continue
        idx, rest = int(m.group(1)), m.group(2)
        if rest.startswith("preview"):
            continue
        for s in known:
            if rest == s or rest.startswith(s + "_"):
                if s not in first_idx or idx < first_idx[s]:
                    first_idx[s] = idx
                break
    return [s for s, _ in sorted(first_idx.items(), key=lambda kv: kv[1])]


def diff_against_pipeline(flow: list[dict], pipeline_steps: list[str]) -> str:
    """Side-by-side of manual ontology steps vs pipeline executed steps."""
    manual = []
    for inst in flow:
        s = map_step(inst)
        if s not in manual:  # dedup, keep first occurrence order
            manual.append(s)
    mset, pset = set(manual), set(pipeline_steps)
    lines = ["", "Manual vs pipeline (by ontology step)", "=" * 60]
    lines.append(f"  manual  : {' -> '.join(manual)}")
    lines.append(f"  pipeline: {' -> '.join(pipeline_steps)}")
    only_m = [s for s in manual if s not in pset]
    only_p = [s for s in pipeline_steps if s not in mset]
    lines.append("")
    lines.append(f"  only in MANUAL  (pipeline skipped): {only_m or '(none)'}")
    lines.append(f"  only in PIPELINE (you skipped)    : {only_p or '(none)'}")
    lines.append(f"  in both                           : {[s for s in manual if s in pset] or '(none)'}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse a manual PixInsight flow and diff vs pipeline.")
    ap.add_argument("file", help="PixInsight .xosm / .xpsm / .xisf")
    ap.add_argument("--run-dir", help="auto_process run dir to diff against", default=None)
    args = ap.parse_args()

    flow = parse_flow(args.file)
    print(summarize_flow(flow))
    if args.run_dir:
        pipe = pipeline_steps_from_run(args.run_dir)
        print(diff_against_pipeline(flow, pipe))


if __name__ == "__main__":
    main()
