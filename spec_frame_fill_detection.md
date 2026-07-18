# Spec: Frame-Fill Detection — sky anchoring for frame-filling nebulae

Status: SPEC (2026-07-10) — not yet implemented. Evidence: IC 1805 pixel autopsy
(see [[project-ic1805-showdown]], update 2026-07-10).

## 1. Why

The IC 1805 showdown autopsy proved the pipeline loses to Henry's manual on **midtone
placement**, not color/contrast/noise:

| metric | manual 8.2 | pipeline best 7.8 (20260707_204518) |
|---|---|---|
| corner "sky" | **0.190** — above the 0.06–0.16 band | 0.068 — in band |
| p50 / p80 luminance | **0.244 / 0.336** | 0.075 / 0.115 |
| corner σ absolute | 0.067 | 0.070 (equal) |
| corner σ / bg | **0.35 smooth** | 1.02 GRAINY |
| nebula saturation | 0.348 | 0.377 (comparable) |
| midscale contrast | 0.041 | 0.063 (pipeline higher) |

Root cause: on a target that **fills the frame** (IC 1805, NGC 7000, NGC 6888), the
corner pixels are faint nebula, not sky. Every sky-anchored mechanism —
`_compute_stretch_stats` corner-median bg, `_sky_band_distance`, the picker cost,
the folio band charge, downstream black-point pulls — then treats faint signal as
background to be pushed down to 0.06–0.16, muting the whole object. Same absolute
grain at 3× brightness reads 3× smoother, so dark placement also *manufactures* the
GRAINY verdict. This is the hard-number form of [[feedback-faint-nebula-too-dark]].

The band concept stays correct for sky-dominated frames (galaxies, PNe, globulars,
small nebulae). The fix is to detect frame fill and change **what we measure as
sky**, not to widen the band for everyone.

## 2. Detection

Compute once per run on the **post-BGE, pre-stretch linear starless** image (BGE must
have run first — a raw LP gradient can brighten corners and fake a positive), stamp
the result into the run context and `run.log` as `frame_fill: true/false` +
the measurements behind it.

**Recalibrated 2026-07-11** after the library sweep falsified the original dark-σ-unit
tests: on post-BGE linear data a uniformly FILLED frame and a uniformly EMPTY one are
statistically alike in raw level stats (both low dynamic range vs local noise) — the
σ-unit thresholds passed nearly every field, filled or not (galaxy sky read 5–6σ;
NGC 7000 read 3.05σ). The working discriminator is spatial **structure**: box-smoothing
crushes uncorrelated sky noise ∝ box size while real extended nebulosity survives.

All tests run on the **32×32 box-smoothed luminance** `sm`; `σ_dark` = σ of raw pixels
below p5. Three tests; **all** must pass:

1. **Structure.** `range = p98(sm) − p2(sm)` ≥ **100×** the smoothed noise floor
   (`σ_dark/32`). Measured: fillers ≥249, sky/small fields ≤69 (M 1 52, M 105 69,
   M 57 48).
2. **Corner separation.** `(corner_median(sm) − p2(sm)) / range` ≥ **0.15**.
   Measured: fillers 0.19–0.38; M 42 0.002, M 17 0.019, M 51 0.016, NGC 2359 0.067.
3. **Coverage.** Fraction of `sm` above `p2 + 0.10·range` ≥ **0.65**.
   Measured: fillers 0.65–0.96; controls ≤0.53.

Sweep result (2026-07-11): IC 1805 / NGC 7000 / IC 1396 / NGC 6888 / M 16 all TRUE;
M 42, M 17, M 1, NGC 2359, M 8, M 20, SH2-101, all galaxies/PNe/globulars FALSE.
NGC 6914's pixels read fill (real Cygnus emission floor) but the type gate
(reflection) correctly excludes it.

Gates:
- `object_type` ∈ {emission_nebula, supernova_remnant, nebula} only. Galaxies keep
  their branch untouched (picker already ignores sky terms; `sky_mute` +
  [[feedback-galaxy-stretch-darker]] own that look). PNe/reflection/clusters: corners
  are true sky by definition — never fire.
- Optional folio hint `frame_filling: true` (derivable from folio angular size vs the
  S50 0.7°×1.27° FoV) can force-enable, but the pixel tests remain authoritative —
  folios have been wrong before ([[project-folio-color-priors]]).

Expected behaviour on knowns: IC 1805, NGC 7000, NGC 6888, IC 1396 → true;
M 42 (bright core, dark frame edges), M 57, NGC 6914, all galaxies/globulars → false.

## 3. What changes when `frame_fill` is true

All changes live inside the **shared measurement layer** so every consumer inherits
them consistently:

### 3a. `_compute_stretch_stats` (auto_process.py ~780)
- `bg_level` ("sky") = median of the **darkest-percentile pixels** (p1–p3 band of
  luminance) instead of corner median. The per-type band (0.06–0.16) then applies to
  the true dark anchor — dust lanes near-black, corners free to float bright.
- `bg_noise` / grain = σ measured on those darkest-percentile pixels, NOT corners
  (corner σ on a frame-filler is nebula *structure*; charging it as grain
  double-penalises exactly the runs that should win).
- New field `p50` + `p50_target` = **0.20–0.25** (manual measured 0.244), scaled down
  toward 0.15 as `depth → 0` (same rationale as the existing p99_lo relaxation — a
  thin stack can't hold bright midtones without lifting noise).
- Stamp `frame_fill: true` into the returned stats so downstream logging/graders see
  which semantics applied.

### 3b. `_physics_pick_stretch` (auto_process.py ~945)
- Cost gains a midtone term: `mid_dist = max(0, p50_target_lo - p50)` charged like
  `bg_dist` (only under-brightness is charged; p99/blown already guard the top).
- `bg_dist`, `bg_noise`, `rel_grain` automatically use the new anchors via 3a.
- Folio band (`quality_thresholds.bg_level_range`, read at ~line 2400): when
  `frame_fill`, charge it against the dark anchor, not corners. Frame-filler folios
  should be re-authored knowing "bg" = darkest structure.

### 3c. Downstream sky consumers
- Sky-feedback loop / curves black-point logic: anchor on the same dark percentile.
- `spec_contrast_recovery.md` interaction: its `sky`/`faint_floor` measurements MUST
  use the shared anchor helper when `frame_fill` — a corner-anchored black-point pull
  would re-crush precisely what this spec brightens. Implement the anchor as one
  helper (`_sky_anchor(d, frame_fill)`) both specs call.
- Assessment prose ("TOO BRIGHT/CRUSHED" verdicts) reports which anchor was used.

### 3d. Explicitly unchanged
- p99 floors, channel-dead rejection, colour terms, star recombination.
- `canary.py:49` bands: canary compares engine outputs on FIXED reference images —
  leave its semantics alone; note the divergence in a comment.
- Critique skill `fits_stats.py`: keep corner semantics (it's the cross-run
  comparable), but critiques of frame-fill runs should quote `run.log`'s
  `frame_fill` stats alongside.

## 4. Validation (before any live default flips)

Per [[project-stretch-picker-validation]]: faithful A/B, real `_stack_depth_factor`,
never depth=1.0.

1. **Offline picker A/B (cheap, no re-runs):** existing run dirs already hold all
   stretch candidates on disk. Re-score with new-cost vs old-cost:
   - Frame-fillers: IC 1805 (5+ runs), NGC 7000, NGC 6888, IC 1396.
   - Controls (must be UNCHANGED picks): M 42, M 57, NGC 6914, M 51/M 31 galaxy runs.
   Acceptance: every control pick identical; frame-filler picks move toward brighter
   p50 placement (report per-run old→new p50/bg).
2. **Detector sweep:** run the §2 detector over every stack in the library; eyeball
   the true/false split against the §2 expected list. Any galaxy/PN firing true is a
   blocker.
3. **Live proof:** one IC 1805 re-run + one NGC 7000 re-run at the new defaults.
   Success = p50 ≥ 0.18 on the starless body, grader ≥ 8.0, and Henry's eyeball vs
   the manual 8.2 ("closed most of the gap" is the bar — the manual's fabricated blue
   interior stays out of scope).
4. Critique the runs (process-critique) and feed the batch eval.

## 5. Versioning

MINOR bump (**1.20.0** or next free MINOR at implementation time): measurement/
threshold change, no step add/remove — scores remain comparable per
[[feedback-workflow-versioning]]. `workflow_history.json` entry must cite the IC 1805
autopsy critique + this spec; mirror in `WORKFLOW_CHANGELOG.md`. Do not ship
mid-critique-batch.

## 6. Risks / design traps

- **LP wash false positive:** corners bright from residual light pollution, not
  nebula → mitigated by measuring post-BGE + the coverage test (a gradient rarely
  puts 65% of the frame 3σ above the dark floor after BGE). If it still fires
  wrongly, the p50 push on a signal-less field lifts noise — the depth-scaled target
  bounds the damage.
- **Dust-lane-free fillers:** a filler with NO dark pixels (rare at S50 FoV) makes
  p2 itself signal — the band charge then keeps some restraint; acceptable.
- **Starless vs combined:** all §3 measurements are on the starless body (pre-
  recombine), matching where stretch/curves operate. Post-combine corner σ is star-
  contaminated anyway (measured on M 51: 0.030 → 1.40 across combine).
- **Grader drift:** physics_nl grader consumes the same stats — its "sky" language
  shifts meaning on frame-fill runs. The `frame_fill` stamp in stats keeps prompts
  honest ("dark-anchor sky", not "corner sky").
- **Folio bands authored for corner semantics** will mis-charge under the dark
  anchor until re-authored — during rollout, when `frame_fill` is true and the folio
  band was violated ONLY under the new anchor semantics, log + skip the folio charge
  rather than punishing the variant.

## 7. Out of scope

- Fabricating interior luminosity the data doesn't support (the manual's structured
  blue heart chamber) — physics-faithful chain stays faithful.
- Galaxy branch, mosaic panel handling, any new pipeline step (MAJOR bump territory).
