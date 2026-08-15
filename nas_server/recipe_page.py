#!/usr/bin/env python3
"""Rich per-recipe pages — turn one processing run into a /workflows-doc-style page.

For each step NOVA actually applied (read from the run's run.log), render:
  - the step's what/why prose (reused from workflow_docs.STEP_DOCS),
  - the ACTUAL settings NOVA chose (run.log step_records `params` / winner),
  - three "do it yourself" blocks — PixInsight, Siril, SASpro — with real settings.

The PixInsight column reuses workflow_docs.PI_MANUAL; the Siril and SASpro columns
live here (SIRIL_MANUAL / SASPRO_MANUAL). Honest by design: where a tool genuinely
doesn't do a step, its block says so and points at the tool that does.

Used two ways:
  - served live by main.py at /recipe/{target} and /recipe/{target}/{run}
    (issue #96 -- main.py's routes are thin wrappers around resolve_run_dir()/
    render_recipe_page() here, never the other way around, so this module stays
    directly unit-testable without importing nas_server.main, matching this
    project's established test convention),
  - emitted as static /recipes/<target>.html by scripts/site_export.py (publish,
    issue #209).
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from nas_server.safe_path import UnsafePathError, safe_resolve
from nas_server.workflow_docs import (
    COMPARISON_SLIDER_CSS,
    COMPARISON_SLIDER_JS,
    PI_MANUAL,
    STEP_DOCS,
    comparison_slider,
)

# ── Siril manual recipe per step ─────────────────────────────────────────────
SIRIL_MANUAL = {
    "stacking": (
        "<b>Scripts → OSC_Preprocessing</b> (or the manual sequence).<br>"
        "<ol>"
        "<li>Convert lights to a sequence, calibrate (<code>calibrate</code> with your "
        "master flat/dark/bias), then <code>register</code> (Global Star Alignment).</li>"
        "<li><code>stack rej 3 3 -norm=addscale -output_norm</code> — Winsorized sigma "
        "3/3, additive+scaling normalization.</li>"
        "<li>For 2× drizzle add <code>-drizzle</code> to the register step.</li>"
        "</ol>"
    ),
    "crop": (
        "<b>Selection + Crop.</b><ol>"
        "<li>Drag a rectangle inside the ragged stacking border, then "
        "<i>Image Processing → Crop</i> (or the <code>crop x y w h</code> command).</li>"
        "<li>Siril preserves the WCS after the crop, so plate-solve stays valid.</li>"
        "</ol>"
    ),
    "remove_pedestal": (
        "<b>PixelMath / offset.</b><ol>"
        "<li>Set an output pedestal at calibration (<code>-pedestal=</code>) so it's gone "
        "before stacking, or</li>"
        "<li>subtract the floor afterward with PixelMath: <code>iif(I&gt;m, I-m, 0)</code> "
        "where <code>m</code> is the background level from <i>Statistics</i>.</li>"
        "</ol>"
    ),
    "cosmetic_correction": (
        "<b>Cosmetic correction (at calibration).</b><ol>"
        "<li>In <i>Calibration</i> enable <i>Cosmetic correction</i> with "
        "<i>CC from sigma</i>: Cold ≈ 3, Hot ≈ 3–5.</li>"
        "<li>Or run <code>find_hot</code> to build a bad-pixel map and apply it. Confirm "
        "star cores are untouched in the preview.</li>"
        "</ol>"
    ),
    "background_extraction": (
        "<b>Image Processing → Background Extraction.</b><ol>"
        "<li>Samples per line ≈ 20; raise <i>Tolerance</i> so samples on nebulosity are "
        "rejected. Use RBF for busy gradients, polynomial (deg 4) for smooth ones.</li>"
        "<li>Correction: <i>Subtraction</i> for light pollution. Run on linear data.</li>"
        "<li>Check the generated model shows no imprint of the target.</li>"
        "</ol>"
    ),
    "color_calibration": (
        "<b>Image Processing → Spectrophotometric Color Calibration (SPCC)</b> "
        "(Siril 1.2+; else Photometric CC).<ol>"
        "<li>Plate-solve first (<i>Image Information → Astrometry</i>).</li>"
        "<li>Choose the OSC sensor + your filter; run on linear data.</li>"
        "<li>No solve? Fall back to <i>Color Calibration → Background Neutralization</i> + "
        "manual white reference.</li>"
        "</ol>"
    ),
    "deconvolution": (
        "<b>Image Processing → Deconvolution.</b><ol>"
        "<li>Generate a PSF from stars (<i>PSF from stars</i>), then Richardson–Lucy, "
        "~10–20 iterations, with a moderate regularization to avoid ringing.</li>"
        "<li>Linear data only. Siril has no AI decon — for BlurXTerminator-class results "
        "use PixInsight or SASpro's Cosmic Clarity Sharpen instead.</li>"
        "</ol>"
    ),
    "denoise_linear": (
        "<b>Image Processing → Denoise.</b><ol>"
        "<li>Anscombe VST + NL-Bayes; leave <i>modulation</i> at 1.0, enable "
        "<i>cosmetic correction</i> off. Run on linear data.</li>"
        "<li>Preview at 1:1 to confirm faint structure survives.</li>"
        "</ol>"
    ),
    "star_sharpen": (
        "<b>No direct equivalent.</b> Siril sharpens stars only via the same deconvolution "
        "pass. For a stars-only correct-only sharpen, use PixInsight BlurXTerminator "
        "(<i>Correct Only</i>) or SASpro Cosmic Clarity on the star layer."
    ),
    "remove_stars_linear": (
        "<b>Image Processing → StarNet Star Removal</b> (StarNet++ must be configured in "
        "<i>Preferences → Miscellaneous</i>).<ol>"
        "<li>Run on the linear image; Siril produces a starless image and a stars-only "
        "image you can recombine later.</li>"
        "</ol>"
    ),
    "stretch": (
        "<b>Image Processing → Generalized Hyperbolic Stretch (GHS)</b> for control, or "
        "<i>Histogram Transformation / Asinh</i> for a quick stretch.<ol>"
        "<li>GHS: set the Symmetry point (<code>SP</code>) just above the background, raise "
        "<code>D</code> (stretch intensity) and <code>b</code> (local intensity) to lift "
        "the galaxy without blowing the core.</li>"
        "<li>Autostretch (the eyeball icon) is a fast starting point but not final.</li>"
        "</ol>"
    ),
    "background_neutralize": (
        "<b>Image Processing → Color Calibration → Background Neutralization</b>, then "
        "<i>Remove green noise (SCNR)</i> at amount ~0.5 to kill the green cast."
    ),
    "color_boost": (
        "<b>Image Processing → Color Saturation.</b><ol>"
        "<li>Raise saturation modestly (background protection on) so only the galaxy "
        "midtones gain color, not the sky.</li>"
        "</ol>"
    ),
    "curves": (
        "<b>Image Processing → Curves</b> (Siril 1.2+).<ol>"
        "<li>Gentle S-curve: lift the upper-mid for contrast, pin the shadow point so the "
        "sky doesn't crush. Small moves only.</li>"
        "</ol>"
    ),
    "stretch_stars": (
        "<b>Stretch the stars-only image separately</b> with GHS/Asinh, lighter than the "
        "galaxy, then bump saturation ~1.2 and SCNR the green before recombining."
    ),
    "sky_mute": (
        "<b>No one-click tool.</b> Build a range-selection mask on the background and pull "
        "its level down with Curves, protecting the galaxy — the manual equivalent of the "
        "pipeline's masked sky-mute."
    ),
    "combine_stars_screen": (
        "<b>PixelMath screen blend.</b> With the stretched starless and stars-only images "
        "loaded: <code>~((~starless)*(~stars))</code> (screen), or use "
        "<i>Image Processing → Pixel Math</i> to add the stars back over the galaxy."
    ),
}

# ── SASpro (Seti Astro Suite Pro) manual recipe per step ─────────────────────
SASPRO_MANUAL = {
    "stacking": (
        "<b>Image MM (Multi-Method) integration.</b><ol>"
        "<li>Load calibrated, registered frames; Image MM weights and rejects, producing "
        "a detail-forward stack. (SASpro doesn't register — stack in Siril/PI first, or "
        "feed pre-registered frames.)</li>"
        "</ol>"
    ),
    "background_extraction": (
        "<b>ADBE — Automatic Dynamic Background Extraction.</b><ol>"
        "<li>Run ADBE on the cropped linear image. Choose polynomial complexity and "
        "optional RBF correction from the gradient actually present; there is no "
        "target-specific universal preset.</li>"
        "<li>Save and inspect the background model. Compare representative sky regions "
        "and extended target structure before accepting the subtraction.</li>"
        "</ol>"
    ),
    "color_calibration": (
        "<b>SSSC — Seti Astro Spectral Calibration.</b><ol>"
        "<li>Run SSSC on a linear, plate-solved color image and record the Gaia-XP "
        "matches, response solution, coefficients, and any fallback.</li>"
        "<li>Treat it as a related physical calibration path, not as the identical SPCC "
        "process. Too few spectrum-bearing stars can force a reduced solution or fallback.</li>"
        "<li>If the field cannot support a stable solution, repair the astrometry, use a "
        "validated SPCC path in PixInsight or Siril, or record the fallback explicitly.</li>"
        "</ol>"
    ),
    "deconvolution": (
        "<b>Cosmic Clarity — Sharpen.</b><ol>"
        "<li>Run the Cosmic Clarity Sharpen module (Stellar / Non-Stellar modes), amount "
        "~0.5 stellar / 0.3 non-stellar — the AI-decon equivalent of BlurXTerminator.</li>"
        "<li>Linear data; check star profiles for ringing.</li>"
        "</ol>"
    ),
    "denoise_linear": (
        "<b>Cosmic Clarity — Denoise.</b><ol>"
        "<li>Run on linear data, strength ~0.5; it's a learned denoiser that preserves "
        "faint structure better than a blur.</li>"
        "</ol>"
    ),
    "star_sharpen": (
        "<b>Cosmic Clarity — Sharpen (Stellar only)</b> on the star layer, or skip in "
        "SASpro and let PixInsight BlurXTerminator Correct-Only handle stars."
    ),
    "remove_stars_linear": (
        "<b>Cosmic Clarity — DarkStar</b> (or StarNet).<ol>"
        "<li>DarkStar removes stars and can generate the stars-only layer for later "
        "recombination — the SASpro path the pipeline uses.</li>"
        "</ol>"
    ),
    "stretch": (
        "<b>Statistical Stretch.</b><ol>"
        "<li>Set the target median (background) and enable <i>linked</i> channels to "
        "protect color; it places the sky and lifts signal in one measured step — the "
        "SASpro tool behind the pipeline's stretch candidates.</li>"
        "<li>For galaxies, keep the target median low (darker sky) and let contrast come "
        "from Curves after.</li>"
        "</ol>"
    ),
    "color_boost": (
        "<b>Color / Saturation controls</b> (or the SCNR + saturation pass). Boost galaxy "
        "midtone saturation with background protection so the sky stays neutral."
    ),
    "curves": (
        "<b>Curves Utility.</b> Gentle S-curve for contrast; pin the shadow so the sky "
        "doesn't crush, lift the upper-mid where the galaxy structure lives."
    ),
    "stretch_stars": (
        "<b>Star Stretch.</b><ol>"
        "<li>On the stars-only layer: the Star Stretch module auto-picks a stretch from the "
        "star profile, with a saturation boost and SCNR — exactly what the pipeline runs.</li>"
        "</ol>"
    ),
    "background_neutralize": (
        "<b>Background Neutralization / Remove Green.</b> Neutralize the sky, then SCNR the "
        "green cast — do this before the color boost."
    ),
    "combine_stars_screen": (
        "<b>Image Combine — Screen blend.</b><ol>"
        "<li>Combine the stretched starless galaxy and the stars-only layer with the "
        "<i>Screen</i> blend mode to lay the stars back over the galaxy without clipping.</li>"
        "</ol>"
    ),
    # Steps that need explicit cross-tool limits or conditional handling.
    "crop": "<b>Not a SASpro step.</b> Crop in Siril or PixInsight before you bring the "
            "stack into SASpro for stretching.",
    "remove_pedestal": (
        "<b>Pedestal Removal — conditional.</b><ol>"
        "<li>Use only when calibration provenance independently establishes a residual "
        "offset in each channel; a positive image minimum is not enough.</li>"
        "<li>SASpro subtracts each channel's minimum and has no adjustable amount. "
        "Record the minima first, then compare clipping, channel statistics, and color.</li>"
        "<li>Otherwise skip this step. Undo if a cold pixel or real sky signal set the "
        "subtraction, or if channel balance changes.</li>"
        "</ol>"
    ),
    "cosmetic_correction": (
        "<b>Stacking Suite — Cosmetic Correction.</b><ol>"
        "<li>Enable it while calibrating light frames, before registration and integration.</li>"
        "<li>Match the input state: use Bayer-aware correction for undebayered CFA data; "
        "use the debayered/mono path only for matching inputs.</li>"
        "<li>Start with Hot σ 5.0 and Cold σ 5.0, then inspect corrected frames and a "
        "difference image. Raise the relevant threshold if star cores or compact detail change.</li>"
        "</ol>"
    ),
    "sky_mute": "<b>No dedicated tool.</b> Use a mask + Curves to pull the background down "
                "while protecting the galaxy.",
}

_TOOL_LABEL = {"pi": "PixInsight", "siril": "Siril", "saspro": "SASpro"}
_TOOL_DICT = {"pi": PI_MANUAL, "siril": SIRIL_MANUAL, "saspro": SASPRO_MANUAL}


def _params_str(params: dict | None) -> str:
    if not params:
        return ""
    parts = [f"{k} = {v}" for k, v in params.items()]
    return ", ".join(parts)


def _step_block(
    step_key: str,
    variant: str | None,
    params: dict | None,
    *,
    comparison_html: str = "",
) -> str:
    doc = STEP_DOCS.get(step_key, {})
    title = doc.get("title") or step_key.replace("_", " ").title()
    what = doc.get("what", "")
    settings = _params_str(params)
    variant_tag = (f' <span class="variant">{html.escape(variant)}</span>'
                   if variant else "")
    settings_row = (f'<div class="settings"><b>NOVA used:</b> '
                    f'<code>{html.escape(settings)}</code></div>' if settings else "")

    tabs, panes = [], []
    for i, tk in enumerate(("pi", "siril", "saspro")):
        body = _TOOL_DICT[tk].get(step_key)
        if not body:
            body = ("<i>No specific recipe recorded for this tool — see the other two "
                    "tabs.</i>")
        active = " active" if i == 0 else ""
        tabs.append(f'<button class="ttab{active}" data-t="{tk}-{step_key}">'
                    f'{_TOOL_LABEL[tk]}</button>')
        panes.append(f'<div class="tpane{active}" id="{tk}-{step_key}">{body}</div>')

    return (
        f'<div class="step-card">'
        f'<h3>{html.escape(title)}{variant_tag}</h3>'
        f'<p class="what">{what}</p>'
        f'{settings_row}'
        f'{comparison_html}'
        f'<div class="tools"><div class="ttabs">{"".join(tabs)}</div>'
        f'{"".join(panes)}</div>'
        f'</div>'
    )


# Old runs did not write preview_before/preview_after onto every step record,
# but they did preserve these named checkpoints. Each fallback is a pair of
# persisted run artifacts from immediately before and after that one operation.
# Do not add a pair here when the output folds in another step: an honest gap is
# better evidence than a visually plausible reconstruction.
_LEGACY_COMPARISON_FILES = {
    "denoise_linear": (
        "auto_preview_deconvolution_a0.jpg",
        "auto_preview_denoise_linear_vframe.jpg",
    ),
    "star_sharpen": (
        "auto_preview_denoise_linear_vframe.jpg",
        "auto_preview_star_sharpen_vframe.jpg",
    ),
    "remove_stars_linear": (
        "auto_preview_star_sharpen_vframe.jpg",
        "auto_preview_starless_vframe.jpg",
    ),
    "stretch": ("auto_preview_pre_stretch.jpg", "auto_stretch_mas_preview.jpg"),
    "curves": (
        "auto_preview_color_boost_a0.jpg",
        "auto_preview_curves_vframe.jpg",
    ),
    "combine_stars_screen": (
        "auto_preview_curves_vframe.jpg",
        "auto_preview_combined.jpg",
    ),
}


def _resolve_preview(run_dir: Path, recorded_name: str | None) -> Path | None:
    """Resolve a run-local JPEG, including numbered post-run filenames."""
    if not recorded_name:
        return None
    name = Path(recorded_name)
    if name.name != recorded_name or name.suffix.lower() not in {".jpg", ".jpeg"}:
        return None
    direct = run_dir / name
    if direct.is_file():
        return direct
    numbered = sorted(run_dir.glob(f"*_{name.name}"))
    return numbered[0] if len(numbered) == 1 and numbered[0].is_file() else None


def recipe_comparison_sources(run_dir: str | Path) -> dict[str, tuple[Path, Path]]:
    """Return only genuine, persisted before/after pairs for applied steps."""
    run_dir = Path(run_dir)
    data = json.loads((run_dir / "run.log").read_text())
    records = {
        record.get("step"): record
        for record in data.get("step_records", [])
        if record.get("step")
    }
    pairs: dict[str, tuple[Path, Path]] = {}
    for applied in data.get("steps_applied", []):
        step, _ = _parse_applied(applied)
        record = records.get(step, {})
        names = (record.get("preview_before"), record.get("preview_after"))
        if not all(names):
            names = _LEGACY_COMPARISON_FILES.get(step, (None, None))
        before = _resolve_preview(run_dir, names[0])
        after = _resolve_preview(run_dir, names[1])
        if before and after:
            pairs[step] = (before, after)
    return pairs


def _comparison_html(step_key: str, base_url: str | None, available: set[str]) -> str:
    if base_url is None:
        return ""
    if step_key in available:
        return (
            '<div class="step-comparison"><h4>NOVA evidence</h4>'
            + comparison_slider(
                step_key,
                f"{step_key.replace('_', ' ')} in this recorded run",
                base_url=base_url,
            )
            + "</div>"
        )
    return (
        '<div class="comparison-missing"><b>Comparison not recorded.</b> '
        "This run did not preserve a separate before-and-after preview for this step."
        "</div>"
    )


def _parse_applied(step_str: str) -> tuple[str, str | None]:
    """'stretch[mas]' -> ('stretch','mas'); 'crop' -> ('crop', None)."""
    if "[" in step_str and step_str.endswith("]"):
        base, var = step_str[:-1].split("[", 1)
        return base, var
    return step_str, None


def _guard_corrections_html(step_records: list[dict]) -> str:
    """Plain-text evidence highlights for any `type: guard` step that caught
    and fixed itself mid-run (e.g. post_mute_crush_lift's background floor
    restore) -- the "headline corrections" issue #96 asks for. Generic over
    any `<name>_before`/`<name>_after` numeric key pair a guard records, not
    hardcoded to one target or one guard step. Deliberately plain text, not a
    before/after image slider -- that component is an explicit non-goal for
    this issue."""
    items = []
    for r in step_records:
        if r.get("type") != "guard":
            continue
        step = r.get("step", "guard")
        pairs = []
        for key, value in r.items():
            if key.endswith("_before") and isinstance(value, (int, float)):
                label = key[: -len("_before")]
                after_key = f"{label}_after"
                after = r.get(after_key)
                if isinstance(after, (int, float)):
                    pairs.append((label, value, after))
        if not pairs:
            continue
        detail = " · ".join(
            f"{html.escape(label)} {before:g} → {after:g}" for label, before, after in pairs
        )
        items.append(
            f'<li><b>{html.escape(step.replace("_", " "))}</b>: {detail} '
            f'<span class="corrected">self-corrected</span></li>'
        )
    if not items:
        return ""
    return (
        '<div class="corrections"><h3>Self-corrections during this run</h3>'
        f'<ul>{"".join(items)}</ul></div>'
    )


def render_recipe_body(run_dir: str | Path, *, comparison_base_url: str | None = None) -> dict:
    """Return {'target','workflow','version','score','html'} for one run dir."""
    run_dir = Path(run_dir)
    j = json.loads((run_dir / "run.log").read_text())
    target = j.get("target") or run_dir.parents[1].name
    step_records = j.get("step_records", [])
    params_by_step = {r.get("step"): r.get("params")
                      for r in step_records if r.get("step")}
    final = (j.get("final_scores") or {}).get("overall")

    available_comparisons = set(recipe_comparison_sources(run_dir))
    cards = []
    for s in j.get("steps_applied", []):
        key, var = _parse_applied(s)
        cards.append(
            _step_block(
                key,
                var,
                params_by_step.get(key),
                comparison_html=_comparison_html(
                    key, comparison_base_url, available_comparisons
                ),
            )
        )

    body = (
        f'<div class="prose"><h2>{html.escape(target)} — how NOVA processed it</h2>'
        f'<p class="sub">Workflow <code>{html.escape(str(j.get("workflow","")))}</code> '
        f'v{html.escape(str(j.get("workflow_version","")))} · physics score '
        f'<b>{final if final is not None else "—"}</b>. Every step below is what the AI '
        f'actually did, with the settings it chose — and how to reproduce it by hand in '
        f'PixInsight, Siril, or SASpro.</p>'
        f'{_guard_corrections_html(step_records)}'
        f'</div>'
        f'<div class="steps">{"".join(cards)}</div>'
    )
    return {"target": target, "workflow": j.get("workflow"),
            "version": j.get("workflow_version"), "score": final, "html": body}


class RecipeRunNotFoundError(Exception):
    """No such target, no runs, or no such specific run -- also raised (rather
    than letting UnsafePathError escape) for a `run` value that fails path-
    safety validation, so callers only ever need to handle one error type."""


def resolve_run_dir(library_path: str | Path, target: str, *, run: str | None) -> Path:
    """A specific run (immutable, stable identity) if `run` is given, else the
    target's most recent completed run (live preview). Never silently falls
    back to a different run than what was asked for -- the whole point of a
    recipe/evidence page is that it's run-aware and provenance-backed, not a
    floating "whatever's newest" pointer that could change meaning later."""
    try:
        runs_dir = safe_resolve(Path(library_path), target, "_processed", "runs")
    except UnsafePathError as exc:
        raise RecipeRunNotFoundError(f"Invalid target path: {target!r}") from exc
    if not runs_dir.is_dir():
        raise RecipeRunNotFoundError(f"No runs found for '{target}'")

    if run:
        try:
            run_dir = safe_resolve(runs_dir, run)
        except UnsafePathError as exc:
            raise RecipeRunNotFoundError(f"Invalid run path: {run!r}") from exc
        if not (run_dir / "run.log").is_file():
            raise RecipeRunNotFoundError(f"Run '{run}' not found for '{target}'")
        return run_dir

    candidates = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir() and (p / "run.log").is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RecipeRunNotFoundError(f"No completed runs found for '{target}'")
    return candidates[0]


def render_recipe_page(run_dir: str | Path) -> str:
    """Full standalone HTML page (shared site chrome + this run's recipe body)
    for one run dir -- what both /recipe/{target} routes in main.py return, and
    what scripts/site_export.py will emit statically for issue #209."""
    from nas_server.story import _page_shell

    content = render_recipe_body(run_dir)
    body = content["html"] + f"<script>{RECIPE_JS}</script>"
    return _page_shell(
        title=f"{content['target']} — NOVA Recipe",
        body=body,
        extra_css=RECIPE_CSS,
    )


RECIPE_CSS = """
.corrections{max-width:900px;margin:14px auto 0;background:rgba(255,196,0,.06);
  border:1px solid rgba(255,196,0,.3);border-radius:10px;padding:12px 18px}
.corrections h3{margin:0 0 6px;font-size:1rem}
.corrections ul{margin:.3rem 0;padding-left:1.2rem}
.corrections li{margin:.25rem 0;font-size:.9rem}
.corrections .corrected{font-size:.7rem;background:rgba(63,185,80,.18);color:#3fb950;
  padding:1px 7px;border-radius:10px;margin-left:4px}
.steps{display:flex;flex-direction:column;gap:18px;max-width:900px;margin:0 auto}
.step-card{background:rgba(255,255,255,.03);border:1px solid rgba(128,128,128,.25);
  border-radius:10px;padding:16px 18px}
.step-card h3{margin:0 0 6px;font-size:1.15rem}
.step-card .variant{font-size:.7rem;background:rgba(88,166,255,.18);color:#58a6ff;
  padding:2px 7px;border-radius:10px;vertical-align:middle;margin-left:6px}
.step-card .what{margin:.2rem 0 .6rem;opacity:.85;font-size:.93rem}
.step-card .settings{font-size:.85rem;margin-bottom:10px}
.step-card .settings code{background:rgba(88,166,255,.12);padding:2px 6px;border-radius:5px}
.step-comparison{margin:1rem 0 1.2rem}
.step-comparison h4{margin:0 0 .45rem;font-size:.7rem;letter-spacing:.06em;text-transform:uppercase}
.comparison-missing{margin:1rem 0 1.2rem;border:1px solid rgba(128,128,128,.25);
  padding:.75rem .85rem;font-size:.85rem;opacity:.8}
.tools .ttabs{display:flex;gap:4px;margin-bottom:8px}
.ttab{background:rgba(128,128,128,.12);border:1px solid rgba(128,128,128,.3);
  color:inherit;padding:5px 12px;border-radius:7px 7px 0 0;cursor:pointer;font-size:.85rem}
.ttab.active{background:rgba(88,166,255,.18);border-color:rgba(88,166,255,.5);color:#58a6ff}
.tpane{display:none;font-size:.9rem;line-height:1.5;background:rgba(0,0,0,.15);
  padding:12px 14px;border-radius:0 8px 8px 8px}
.tpane.active{display:block}
.tpane ol{margin:.3rem 0 .3rem 1.1rem}.tpane li{margin:.25rem 0}
.tpane code{background:rgba(128,128,128,.2);padding:1px 5px;border-radius:4px}
.prose .sub{opacity:.8}
""" + COMPARISON_SLIDER_CSS

RECIPE_JS = """
document.querySelectorAll('.ttab').forEach(b=>b.addEventListener('click',()=>{
  const card=b.closest('.tools');
  card.querySelectorAll('.ttab').forEach(x=>x.classList.remove('active'));
  card.querySelectorAll('.tpane').forEach(x=>x.classList.remove('active'));
  b.classList.add('active');
  document.getElementById(b.dataset.t).classList.add('active');
}));
""" + COMPARISON_SLIDER_JS
