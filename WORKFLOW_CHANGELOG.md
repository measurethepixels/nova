# Autoprocess Workflow Changelog

Human-readable mirror of `workflow_history.json` (the machine source of truth). The
**current version** is stamped into every run's `run.log` (`workflow_version`) and
inherited by each saved critique, so the batch evaluation can group runs by the
pipeline version that produced them.

**Semver policy (scoring pipeline):**
- **MAJOR** — scoring model or step add/remove; scores no longer comparable across
  versions (resets the critique baseline).
- **MINOR** — param/threshold/gate tuning that changes output but keeps comparability.
- **PATCH** — bug fix not expected to change good-run output.

**To bump:** append a new entry to `workflow_history.json` (set `current` and add to
`versions`), mirror it here, and record which critiques drove the change. Code reads the
latest version automatically — no other edit needed.

## Pinned engine versions (updated 2026-06-11)

| Engine | Version | Role |
|--------|---------|------|
| PixInsight Core | **1.9.3** Lockhart | plate solve, SPCC, StarXT/NoiseXT, register/drizzle, curves |
| Siril | **1.4.3** (native `siril-cli`) | calibrate, register, `seqplatesolve`, stack |
| SetiAstro Suite Pro | **1.18.0** (`setiastrosuitepro`, venv) | Image MM deconv stack, stat/GHS stretch, ADBE |
| GraXpert | **3.0.2** Umbriel | AI background/gradient extraction |
| ASTAP | **CLI + D20 star DB** (`/opt/astap`, installed 2026-07-02) | astropy-consistent plate solve at ingest; blind solve for hint-less/pre-EQ-spoofed headers; also unblocks SASpro's own solver |

**SASpro 1.15.0 → 1.18.0 promoted 2026-06-11** via the canary protocol below: 1.8.0
re-baseline (M 51 / M 42 / M 13) → isolated-venv API check (all 21 pipeline-imported
symbols present, signatures identical; `abe_run` gained a backward-compatible
`correction_mode` kwarg) → live upgrade (zero dependency churn — numpy/numba/scipy/astropy
unchanged) → `canary check` PASSED with all three golden finals **bit-identical** to
baseline. Not a semver bump (no pipeline code changed). Laptop worker still on 1.15.0
until its next deploy. Unlocks for future feature work: NBExtract (1.17), SSSC (1.18),
ADBE linear fix (1.16.3).

These are **pinned pipeline dependencies**, not apps to keep current: a minor update can
silently change a headless process, property default, or behaviour with no error output
(PI 1.9.3 `DrizzleIntegration` no-ops in automation mode; Siril 1.4.2 crashed on
`seqplatesolve`, fixed in 1.4.3). Treat each version as part of the pipeline contract.

**Update protocol** (highest risk: PI > SASpro > Siril):
1. Re-process the golden targets (M 51 / M 42 / M 13) on the **current** engines so each
   target's latest run is fresh, then `python -m nas_server.canary snapshot` to freeze
   `critiques/golden_baselines.json`.
2. Install the new engine to a **side-by-side** path; keep the old binary for rollback.
3. Re-process the golden targets on the new engine.
4. `python -m nas_server.canary check` — diffs the new finals vs the snapshot and exits
   non-zero on drift. Promote only if it passes.

Updating an engine is **not** a semver bump on its own (these are environment state); bump
only if pipeline *code* changes in response to the new engine.

---

## 1.24.3 — 2026-07-18 (patch)

Small-target recenter crop is now **WCS-first**: center on the target's catalog
RA/Dec projected through the crop-stage WCS (trustworthy since the ASTAP audit +
1.24.2 CRPIX fix), sized from the folio angular size × measured pixel scale. The
brightest-extended-blob detector is demoted to fallback — a bright star's halo
survives the median filter and can out-peak a low-surface-brightness galaxy, which
cropped M 102 out of its own frame (the 3.5 run; caught by Henry's screenshot).
`WCS(hdr, naxis=2)` required for 3-axis color headers with SIP.

## 1.24.2 — 2026-07-18 (patch)

Small-target recenter crop now shifts CRPIX with the crop. It copied the header
verbatim — full-frame reference pixel on a 480px cutout — so every recentered
target (folio <11') shipped a WCS pointing ~0.5° off-sky and SPCC queried Gaia at
the wrong position: the second (fast-fail) SPCC mode. Explains the rescue-batch
split: M 85/97/102 (recentered) still fell back after 1.24.0 fixed the PC-form
mode for M 88/91/109.

## 1.24.1 — 2026-07-18 (patch)

Post-guard re-score fix: 1.24.0 replaced the final evaluator's scores with a
physics re-grade when `channel_crush_guard` fired — physics grades on a different
scale (M 109: a 7.2-class run recorded 6.5 with color_balance 1.0), breaking
cross-run comparability. The evaluator's `final_scores` stay authoritative; the
shipped-pixel physics grade now attaches as `final_scores.post_guard_physics`.

## 1.24.0 — 2026-07-18 (minor)

Batch eval 2026-07-16 fixes (M 85/88/91/97 galaxy regressions, 3 urgent issues).

- **SPCC mutilated-WCS rescue**: the crop step's astropy WCS rewrite emits the
  PC-matrix + CDELT=1.0 header form; PI 1.9.3 reads CDELT literally → 3600"/px
  seed → solver fails → SPCC always fell back to generic CC (pink nuclei,
  unlinked stretch). `spcc()` now normalizes PC→CD on a temp copy (pure header
  math, no re-solve); absent/insane WCS gets an ASTAP pre-solve instead.
  Verified on M 88: SPCC OK in 26 s (was 2×150 s fail → fallback). A
  keyword-level sanitize+reseed belt also landed in `pi_postprocess.js`.
- **Galaxy stretch sky-ceiling gate** (`_GAL_SKY_CEIL = 0.10`, relative): the
  detail-first galaxy branch now refuses winners whose sky exceeds sky_mute's
  healthy pull range when an under-ceiling candidate exists. Thin stacks handed
  the mute 0.11+ skies and it over-crushed the result.
- **channel_crush_guard re-score**: the guard mutates pixels after the final
  evaluation; the shipped image now gets a physics re-grade so `run.log`'s final
  score describes the shipped pixels (root cause of the "final ≠
  best_publishable / evaluator scored the wrong image" flag).

## 1.23.1 — 2026-07-17 (patch)

Instrumentation only (channel/episode Phase 1) — no pixel-path change.

- **Stretch candidate scores persisted**: `_physics_pick_stretch` now emits every
  candidate's physics metrics (cost, bg_dist, p99, under, blown, bg_noise, grain,
  winner flag, dropped reason for grain-DQ / grain-cap cuts) via a `scored_out`
  out-param; the `stretch_pick` step_record stores them as `candidate_scores`.
- **Hard-veto reasons persisted**: a step that ends skipped after an artifact-class
  hard veto (decon undershoot, halo output contract, …) now records
  `skip_reason="hard_veto: <reason>"` + `veto_metrics` instead of the generic
  "no improvement".

Both land in `run.log` so the episode generator (`make_episode.py`) can render the
9-candidate grid and failure-beat shots from real data instead of re-measuring.

## 1.23.0 — 2026-07-15 (minor)

**Small-target compositional recenter-crop (M 1 under-framed).** The coverage crop
only trims blank borders, so a target much smaller than the S50 frame ships as a speck
(M 1 6' filled ~10% of a 77'x43' field; the processing was good at 7.0, the *framing*
was poor). New `_small_target_recenter_crop` wraps the crop fn when the folio size
< 11' (25% of the 43' short axis) **and** there's no saved/reviewed crop (a user's
manual framing stays authoritative). It finds the target as the brightest extended
(non-star) blob, measures its radius, and crops to 4.5x that radius centered on it,
with a 240px native-res floor. Sizing from the measured blob is scale-free — necessary
because drizzled finals (~1.19"/px) aren't inferable from frame dimensions. Triggers on
M 1, M 57, M 76, M 27, M 97, NGC 6826, M 105, small galaxies; skips M 51 (11.2') and
larger. Validated: M 1 Crab centered, 2032x3712 -> 784px, well-framed.

---

## 1.22.2 — 2026-07-15 (patch)

**Grain-DQ scoped to galaxies (SH 2-273 regression fix).** The 1.21.0 grain
disqualifier (`bg_noise > 0.10` drops a candidate) was branch-neutral, but on faint
nebulae the correct brighter stretch is *necessarily* grainier — the DQ killed it and
left a conservative dark pick. Measured: SH 2-273 regressed 6.8 → 4.5 (DQ dropped
`stat_bright`, dark `ghs_soft` won, "faint nebulosity almost entirely absent"). The
galaxy branch needs the DQ (it ignores grain by design); the nebula branch already
treats grain as a placement-equal tiebreaker — the right discipline for faint signal.
Fix: apply the DQ only when `is_galaxy`. Validated: SH 2-273 → `stat_bright` (restores
6.8); M 108/NGC 4565 keep clean picks with veralux_strong still DQ'd. Caught by the
reprocess campaign (1 of ~20 wave-1 reprocesses regressed).

---

## 1.22.1 — 2026-07-14 (patch)

**Frame-fill false-positive fix (M 1).** M 1 — the 6' Crab remnant in a dense Taurus
star field — triggered `frame_fill=True` (coverage 0.93); the smoothed "coverage" was
unresolved stars + noise, not emission. The dark-anchor over-brightening then amplified
a residual gradient into a broad green band (grader 3.2, "severe gradient contamination").
The pixel tests can't separate it — M 1 and IC 1805 both measure corner/floor = 1.04
pre-stretch. Fix: an **angular-size gate** — a target < 30' cannot fill the 43'×77' frame,
so it's frame-fill-ineligible regardless of pixel coverage (folio size threaded from
`_folio_info`). M 1 (7') ineligible; NGC 7000/IC 1805 still fire; unknown size falls
through to pixels. Note: the horizontal bands Henry saw are in the SeeStar EAA live-stacks
only — Siril sigma-clip rejection removes the satellite trails from the pipeline stack
(verified clean, 3.1σ); the green band was created in processing, not inherited.

---

## 1.22.0 — 2026-07-13 (minor)

**Batch-eval HIGH fixes, Bump 2** (`docs/plan_batch_eval_20260713_impl.md`) + 07-07
carry-overs.

- **sky_green_rebalance signal-green cap**: the sky-only design correctly leaves
  object colour alone — which let colour_boost/curves-amplified SIGNAL casts sail
  through while the step reported "no excess" (NGC 281 G p99 lead 7.9%). New opt-in
  signal mode: when the signal-region `(G_p99 − max(R,B)_p99)/G_p99 > 0.06`, cap G
  toward max(R,B) inside the signal mask (bounded 0.6, never-invert machinery
  shared with the sky pass). **Folio prior gate on the FIRST dominant_colors entry
  only** — whole-list substring matching wrongly blocked NGC 281 and M 42 (secondary
  OIII accents); Thor's Helmet's teal-dominant first entry correctly blocks.
  Retro-validated: NGC 281 fires, NGC 2359 blocked, M 42 clear. The 07-07 pre-gate
  half already exists (min_gr self-gate + max(R,B) cap postdate the v1.14.2 floods).
- **Star-layer grain gate** (P4+P5 unified): combine doubled final noise via star-
  layer speckle (NGC 2359 ×2.1, NGC 281 ×2.0; starless clean). Pre-combine, star-
  layer corner σ > 2× starless → re-stretch stars one factor step gentler (single
  retry). Retro-validated: fires on both measured cases (2.4×/2.1×). **Deviation:**
  the eval's post-combine denoise fallback was dropped — post-combine corner σ is
  star-contaminated (healthy M 51 final measures 0.084), the 0.08 threshold would
  misfire.
- **07-07 carry-overs**: pi_globular_core_rolloff inversion guard (revert when p99
  rises — it brightened cores while darkening midtones); denoise_linear[nxt]
  near-identity labeling (<10% rms reduction → `near_identity` step record).

---

## 1.21.0 — 2026-07-13 (minor)

**Batch-eval CRITICAL fixes, Bump 1** (`docs/plan_batch_eval_20260713_impl.md`).

- **Stretch picker grain disqualifier**: `bg_noise > 0.10` drops a candidate before
  any branch ranking (keep-cleanest fallback). The galaxy detail-first branch had no
  grain guard at all — veralux_strong won M 108 at σ 0.198 (26× the cleanest) and
  NGC 4565 at σ 0.143. Replay: exactly the 3 grainy live picks flip to clean `mas`;
  every clean pick reproduces (2 apparent flips proven replay-depth artifacts).
- **Galaxy post-mute crush guard**: additive lift back to the band floor when the
  combined-tail sky lands below `band_lo − 0.005` (measured finals 0.031–0.045 vs
  the 0.05 floor). Fixes the downstream crush, not the deliberately-bright 1.7.0
  pick.
- **halo_suppression output contract** — the step was CRITICAL in two consecutive
  evals with three distinct modes. Hard-veto unless: |sky delta| ≤ 0.020, noise
  ratio ≤ 1.5× (1.18.0 guard kept), p99.9 rise ≤ 0.01; veto reason carries the mode
  (sky-crush / sky-raise / p99.9-rise / aliasing). `reduction_level` capped at 2 on
  sparse fields (<1000 stars — the level-3 sky-raise inversion). Unit-validated 7/7
  against the measured failures from both evals.
- (Rides along, from 1.20.1-era: SPCC log wording — "LP data — SPCC deliberately
  skipped" vs real failure; log-string only.)

---

## 1.20.1 — 2026-07-12 (patch)

**Frame-fill picker cost hole fix.** The NGC 7000 live-proof run (`20260712_175526`)
failed exactly where 1.20.0's cost was blind: a flat-grey `stf` won (dark floor 0.160 /
p50 0.177 — tonal **spread 0.017** vs the manual reference's 0.107), graded 4.2
post-stretch, final 2.5 with a non-degradation restore. Henry flagged it live. The
1.20.0 placement charged floor position + p50 brightness but not spread, so a
contrastless wash scored best on both. New frame-fill placement term:

- **ceiling-only floor charge** — a floor above band-hi is unfixable grey wash; a floor
  *below* band is not charged (the bg clamp lifts it additively, structure intact);
- **spread deficit ×1.5** — `max(0, spread_target − (p50 − floor))`, target 0.06–0.10 by
  depth; flatness is the one defect nothing downstream repairs;
- **crushed-floor exception** — floor <0.005 with dark σ <0.0015 is CLIPPED (faint
  nebula destroyed, not parked low) and pays 2× the band distance.

A/B: 7/7 controls unchanged; NGC 7000 → `stat_bright` (spread 0.096); IC 1805 keeps its
live `mas` (8.2); IC 1396/SH 2-273 reject clipped veralux variants for `ghs_soft`.

---

## 1.20.0 — 2026-07-12 (minor)

**Frame-fill detection — dark-anchor sky for frame-filling nebulae**
(`docs/spec_frame_fill_detection.md`). The IC 1805 pixel autopsy proved the manual 8.2
beats the pipeline's best 7.8 on **midtone placement alone**: manual p50 0.244 / corners
0.190 vs pipeline 0.075 / 0.068, with the same absolute grain, comparable saturation, and
*higher* pipeline midscale contrast. On a frame-filling target the corners are faint
nebula, not sky — so every corner-anchored mechanism (band charge, picker cost, clamp,
feedback, grades) was muting the object. This is the hard-number proof of the
faint-nebula-too-dark feedback.

- **Detector** (`_frame_fill_detect`, once per run on the post-BGE pre-stretch starless):
  32×32 box-smoothed luminance; structure ≥100× smoothed noise floor, corner separation
  ≥0.15 of smoothed range, coverage ≥0.65. Smoothing is load-bearing — raw level stats
  cannot distinguish a uniformly filled frame from a uniformly empty one post-BGE.
  Type-gated to emission/SNR/generic nebula. Library sweep: IC 1805, NGC 7000, IC 1396,
  NGC 6888, M 16, SH 2-273 TRUE; M 42/M 17/M 1/NGC 2359/M 8/M 20/SH2-101 and all
  galaxies/PNe/globulars FALSE.
- **When frame_fill:** `bg_level`/`bg_noise` anchor on the darkest-percentile pixels
  (p1–p3 — the manual's dark anchor measures 0.137, comfortably in band) instead of
  corners; new p50 midtone target (0.15–0.20 by depth) charged into the picker cost and
  nebula placement bucket; folio band charge skipped (folio bands are corner-authored).
  Detector output stamped into `run.log` (top-level `frame_fill` + stretch step_record).
- **Offline A/B** (`scripts/frame_fill_ab.py`, real depth per the faithful-A/B protocol):
  7/7 controls unchanged; every frame-fill pick moved brighter or un-crushed —
  IC 1805 stat→mas (p50 0.072→0.151), NGC 7000 stat→stf (0.073→0.209 vs manual 0.244),
  M 16 & NGC 6888 escaped crushed-floor stf, SH 2-273 dropped a crushed grainy
  veralux_strong for in-band mas.

---

## 1.19.0 — 2026-07-08 (minor)

**narrowband_hoo — HOO-preserving palette step** (Henry-requested alternative to narrowband_norm's Equalize/SHO look). Keeps Ha the **dominant red**; OIII a bounded blue/teal accent: `R=Ha, G=0.15·Ha+0.9·O, B=O` with `O = min(OIII, oiii_cap·Ha)`. The local cap guarantees Ha dominance and forces `O→0` where there's no Ha — auto-killing the field-flood a naive OIII surface produces on Ha-dominant fields (both RGB-proxy and raw xp_oiii flood ~half the frame without it). OIII from `run_dir/xp_oiii.fit` (Ha ref from xp_ha.fit at matched stretch) or an RGB blue-cyan-excess proxy. Knob: `oiii_cap` (0.6 near-pure-red … 1.0 max teal), soft/strong variants. Force via `force_hoo`; SCNR skipped on this path. **Limitation:** needs spatially-distinct OIII to show teal — on HII regions where OIII co-locates with Ha (IC 1805) the overlap reads gray; best for PNe / Veil / OIII-rich bicolor targets. Force-only, so no change to existing runs.

## 1.18.0 — 2026-07-07 (minor)

**halo_suppression dense-field aliasing guard** (batch-eval Pattern 1). On LP data with 16k+ stars the ratio-based colour subtraction flags nebula next to overlapping halos as "halo colour" and paints vivid magenta/cyan/green rings frame-wide — bg_noise +129/157 %, p99.9 pegged to ~1.0 on both v1.17.2 IC 1805 runs (these were the star rings mis-attributed to deconvolution during the session). Now hard-vetoed when post bg_noise > 1.5× pre or p99.9 jumps past 0.995 from < 0.98. No false-positive on the clean IC 1805/NGC 7000/M 42 runs.

## 1.17.3 — 2026-07-07 (patch)

**Hard-veto honored in step acceptance.** The 1.17.2 guard fired on both IC 1805 runs, but `if new_sum > best_sum` promoted the damaged output anyway — ringing *sharpens* stars, so the physics delta (+6.48) outvoted the objective FAIL and both finals shipped with the artifacts the guard had caught (magenta/cyan star rings, violet blob). The guard now sets `hard_veto=True` and best-result promotion skips hard-vetoed attempts. Normal marginal-case iteration unchanged.

## 1.17.2 — 2026-07-07 (patch)

**BXT local-undershoot guard.** On IC 1805, deconvolution dug large black holes with magenta rims into smooth nebula while whole-frame metrics *improved* (FWHM 2.59→2.14 px, SNR ×1.43) — the objective gate is structurally blind to local ringing; Henry caught it in the step preview. New `_bxt_undershoot_check`: 8 px cell grid, veto when a connected blob (≥40 cells) of output local minima drops below 0.5× the input's local floor. Calibrated on labeled pairs (fires on the 86-cell artifact run; quiet on clean IC 1805/M 42/NGC 7000). Veto keeps the pre-decon image.

## 1.17.1 — 2026-07-06 (patch)

**pi_curves silent no-op fixed** (critique-batch cross-run finding). `_clean_curve_points` merged any control point within 0.03 of x=0 *into* the [0,0] black anchor — on dark images (NBN sky ≈0.05 → point at x≈0.027) this destroyed the x=0 endpoint, PI rejected the whole curve (`CurvesTransformation.K(): the instance is not valid`), and the JS saved the input unchanged: bit-identical output, exit 0. **Not a version regression** — stats-triggered (curves input sky < ~0.06), made common by the darker LP/NBN path; the batch's 1.15.0→1.16.1 window was scheduling coincidence (curves worked in M 42/M 51 1.16.0 runs; measured no-op only in the two dark NGC 7000 runs). Fix: endpoints can no longer be merged away and are re-pinned post-clean. Verified PI-valid on the failing input.

---

## 1.17.0 — 2026-07-06 (minor)

**background_extraction frame-fill exception.** On frame-filling nebulae (NGC 7000) every "sky" cell is nebula, so the sky-gradient metric wobbles without meaning — the gate vetoed GraXpert on a +0.014 move (0.062→0.076) even though it measurably fixed the real corner wash (TL 1.029→1.006 vs median) with **no** faint-structure loss (central IQR/med 0.112→0.126 — improved). Henry compared renders: "with background extraction looks better." New rule: a trivial absolute gradient move (<0.05) at low levels (<0.25) with SNR held (≥0.90×) is **accepted** — rejection requires evidence of harm, not metric noise. Big gradient jumps or SNR drops still veto.

---

## 1.16.2 — 2026-07-05 (patch)

**Mosaic-aware FoV for `astap_solve()`.** The FoV hint is now derived from header geometry (`NAXIS2 × scale`, clamped 0.4–8°) instead of the fixed 1.3° single-panel default. Found on the first mosaic processed with the ingest solve on: NGC 7000's 60 MP mosaic solve FAILED silently → old WCS kept → XP matching stuck at 10 stars → NBN palette fell back to the approximation path (Ha amber/brown instead of red). The failing call now solves in 6.8 s. Single-panel stacks derive ≈ the old default — no behavior change for them.

---

## 1.16.1 — 2026-07-04 (patch)

**`astap_ingest_solve` default ON + golden re-baseline.** F4 audit complete: golden trio (M 51 / M 42 / M 13) all scored 7.8 at 1.16.0 with 0 orientation flips and plate-solved crop positions exact to the saved boxes; M 13's 1.14.0 blue-corner-glow regression confirmed gone; Henry signed off 2026-07-04. Canary baselines re-frozen to the three 1.16.0 runs. Code default flipped False→True so nodes without the settings key (laptop worker) solve at ingest too — otherwise their Siril-convention WCS + the migrated (ASTAP-convention) crop boxes land ~36′ off (the round-5 failure mode). Deploy note: laptop picks this up on its next git pull.

---

## 1.16.0 — 2026-07-04 (minor)

**LP standard-chain colour routing** (Henry: "PI CC look, keep it simple"). SSSC is spectrally faithful — but on dual-band data that renders the nebula green-teal (M 42 bright-nebula G/R: SSSC **1.21** vs PI CC **0.71** = the approved 8.2 look; SCNR can't trim it because the cast is green-*cyan*, G < (R+B)/2 pointwise). And SPCC's broadband white reference is wrong for dual-band — it only ever "worked" by **failing** into the PI CC fallback. Changes:
1. `spcc()` attempts SSSC only when `allow_sssc=True` — passed by the pipeline only for NBN/palette runs (`narrowband_norm`/`nb_palette` forced), the branch SSSC was built for (1.9.0).
2. On LP data the standard chain now skips SPCC entirely and goes **straight to PI ColorCalibration** with the `.spcc_failed` sentinel semantics (unlinked stretch + SCNR enabled) — the proven 8.2 recipe made explicit instead of failure-dependent.

---

## 1.15.0 — 2026-07-03 (minor)

**F4 colour-path completion (SSSC on LP/dual-band).** Three coupled changes — items 1–2 shipped 2026-07-03 unbumped (discipline violation, corrected here):
1. **`xp_stars.measure_stars_rgb` local-annulus photometry.** The global SEP background can't follow bright structured Hα nebulosity at star scales, so embedded stars carried residual per-channel background that biased colour ratios and the solved SSSC gains (M 42: star R/G 0.30 vs SPCC's 1.49 — red crushed ~4×, the "green and brown" finals). Now subtracts a per-star local-annulus (2.5r–4r) residual per channel; stars drowned in nebula are dropped. Fixed gains physically sensible: R=1.756 G=1.0 B=1.098, Stage 3, RMS 0.25.
2. **SSSC drizzle gate removed.** The "drizzle photometry bias" was the nebulosity contamination above, fixed at root. Henry approved the fixed SSSC colour on drizzled M 42 vs SPCC side-by-side.
3. **SCNR allowed when the calibrator was SSSC** (`.sssc_applied`). SSSC solves star-based per-channel *gains* — it can't rebalance dual-band OIII emission landing in G, so the nebula body stays green through the linked stretch. Every good M 42 run (8.2, colour 7.5–7.8) had SCNR; all three SSSC-succeeded runs that skipped it scored 5.5–6.2 with "dominant green cast" as issue #1. PI-SPCC-success runs still skip SCNR (linked-WB protection); NBN/nb_palette runs still skip it (OIII/teal protection).

---

## 1.14.2 — 2026-07-02 (patch)

**Idle-worker per-sub solving: ASTAP-first.** `solve_subs_astap()` copies each sub to tmp, solves there (~0.1–0.6 s measured; 4/4 on 10 s M 71 subs, scale 2.38″ ≈ native 2.37), and writes **DB-only** (`solved_ra/dec/rot/scale`) — library FITS are never modified (the ingest-solve lesson applied). Siril batch solve remains the automatic fallback. Unblocks the **~106k-sub backlog** stalled on the offline laptop; pre-EQ-spoofed subs auto-blind-solve. PATCH — feeds alignment-outlier exclusion only, zero pipeline-output blast radius.

---

## 1.14.1 — 2026-07-02 (patch)

**Ingest ASTAP solve DISABLED by default** (`settings.astap_ingest_solve`, default `false`). The golden rerun regressed (M 42 5.8 dim mids; M 13 6.2 **new blue corner glow**; M 51 fine at 8.2): Henry identified **cropping** as the failure mode, and the run videos show previews **flip-flopping orientation between steps**. Root: the corrected WCS changes what every downstream consumer computes — crop/canonical framing picked wrong regions (implicitly calibrated to the old convention), and **PI steps re-solve internally, rewriting a WCS without the `PLTSOLVD` marker**, so the renderer alternated flip rules step-to-step. **Kept:** D20 DB, `astap_solve()` (incl. blind mode), the SSSC sanity gate (green-cast protection), renderer marker logic. **Reintroduction checklist:** audit `target_crop`/`crop_analysis`/`canonical_frame` WCS usage; preserve/strip markers consistently through PI rewrites; verify preview-rule consistency across all steps; then golden validation. Lesson: validated components ≠ validated system.

---

## 1.14.0 — 2026-07-02 (minor)

**ASTAP re-solve at ingest — the root WCS fix.** Siril/PI plate solutions are written in a convention astropy misreads (axis-inverted; drizzled stacks also offset), breaking every astropy consumer: **SSSC/XP wrong-star matching** (SH2-101 green cast), **mirrored previews**, **drizzled XP no-op**. Installed the ASTAP **D20** star DB (`/opt/astap`); `seti_astro.astap_solve()` re-solves the working copy at ingest (~0.2 s), and the astropy-consistent header propagates through all steps (WCS-preservation now carries the `PLTSOLVD` marker; renderer uses the pure theoretical flip rule for those headers, legacy rule for old files).

**Validated:** SH2-101 XP matching 39/120-at-any-orientation → **119/120 identity vs 4–5 flipped**; M 42 (drizzled+offset) 0–4 → **101/120** matches, true scale 1.1876″/px. Solve failure is non-fatal (1.13.2 sanity gate remains the backstop). Also unblocks SASpro's own ASTAP-first solver. (The Gaia XP libraries are *spectral* data — SASpro has no positional solver on them; its backends are ASTAP + astrometry.net web.)

---

## 1.13.2 — 2026-07-02 (patch)

**SSSC output sanity gate** (SH2-101 green-cast post-mortem). Measured chain: stack signal G/R 1.43 → post-SSSC **3.01** (R halved) — **SSSC injected the green; the stretch only revealed it.** Cause: with the WCS axis/offset mismatch vs the data, the XP matcher pairs detected stars with **wrong catalog stars**; sparse fields fail closed (M 42 → SPCC fallback) but dense fields (Cygnus) pass the RMS gate with wrong-star colours → garbage gains. New gate in `spcc()`: signal-region G/R before/after SSSC; a move sharply **away from neutral** rejects SSSC (clears `.sssc_applied`) → legacy SPCC/CC fallback. Ingest-time WCS axis repair was prototyped (`xp_stars.verify_and_repair_wcs`) and **rejected**: XP matching is non-discriminating in dense fields (39/39/37/40 under all four orientations) and M 42 matches zero everywhere (translational offset, not a flip). PATCH.

---

## 1.13.1 — 2026-07-01 (patch)

**Fix 1.13.0 contrast-recovery regressions (found by the critique batch).** Two bugs: (1) `target_sky` was **0.05 — below the nebula band floor 0.06** — so recovery *crushed* the sky (M 42 0.121-in-band → 0.050-crushed, faint-signal loss) and amplified grain (std/bg up to 1.24–2.13). Now targets the **band floor + margin (~0.08)** and only fires on a genuinely-soft sky (never darkens an already-in-band sky). (2) The body expansion **blew the cores** (M 42 Trapezium white mass, p99.9=1.0) — added **highlight protection** (identity above knee 0.80). Gain 1.2→1.15. Validated: M 42 sky 0.121→**0.077 (in-band)**, contrast 0.171→0.193, sat 0.306→0.346, p99.9 **0.973** (cores safe), 0% faint crush. The critique showed the two best batch runs (NGC 2244 8.5, M 31 8.4) were the ones where recovery did NOT fire — this retune stops it hurting the ones it touches. **⚠ Re-baseline golden M 42** (1.13.0 froze a crushed baseline).

---

## 1.13.0 — 2026-06-30 (minor)

**Sky-adaptive, signal-aware contrast recovery for nebulae** (`docs/spec_contrast_recovery.md`). The 1.12.0 colour-first pick (`mas`) has a brighter sky that softens contrast (Henry: "vibrancy better but contrast less"). This downstream step — in the combine tail, where galaxies get `sky_mute` and **nebulae were previously skipped** — pulls the black point to darken the sky (M 42: 0.125→0.045) and expands contrast around the nebula-body midtone (**body-pivoted, gain ×1.2 — Henry's blind-tuned sweet spot**) with the faint floor (sky+3σ) **protected** (identity below it), so the faint Ha/OIII is **not crushed** (validated: **0.0% faint crushed, 0.2% blown**; M 42 contrast 0.163→0.186, saturation 0.406→**0.508**). Hue-preserving (channels scaled by the luminance ratio). Note: an earlier floor-pivoted smoothstep was the wrong lever (barely moved contrast — steepened low-mids while compressing highlights); body-pivoted expansion is what delivers punch, and it's not depth-scaled (noise is below the protected floor → safe on thin stacks). Gated to bright-sky/soft picks (sky ≥ 0.08; no-op on dark stf-type). MINOR — gated downstream behaviour, scores comparable; nebula only.

**⚠ Canary:** changes nebula finals — re-baseline the golden nebula + sign-off; bundle with 1.12.0/1.12.1 on the next restart.

---

## 1.12.1 — 2026-06-30 (patch)

**WCS preservation extended to every step (preview-flip fix, round 2).** 1.11.1 fixed only `color_calibration`, but PI-based steps (**deconvolution/BXT, NXT, star_sharpen**) *also* drop the astropy-readable WCS — so the preview renderer flips the image (the "preview flipped after deconvolution" Henry caught mid-run; confirmed: deconvolution output had NO WCS while color_calibration had it). The main step loop now re-injects the input WCS into any step output that lost it (generic), and `bxt_deconvolve` does it directly. Also covers **forced steps** (denoise_linear/star_sharpen/curves via PI bypass the main loop) and a **final safety net** (copies WCS from an early WCS-bearing intermediate onto `final.fit` if lost), so the final preview always renders orientation-consistent. Metadata only; pixels/canary unaffected. Metadata/preview only; scored pixels unchanged. PATCH.

---

## 1.12.0 — 2026-06-30 (minor)

**Narrow colour-swap in the nebula stretch picker — "maximise colour SUBJECT TO clean".** The picker already honoured *clean* (it lands on the colourful-clean `stat` variants Henry likes) but never *reached* for colour when it was freely available — so it could ship a desaturated stretch (M 42 = `stf`) when a comparably-clean, more-colourful one (`mas`) existed. After the physics pick, it now SWAPS only to a candidate that is **comparably clean** (σ ≤ 1.2× the winner — grain stays a hard constraint; Henry consistently rejected grainy `veralux` even when more colourful), **well placed**, and **meaningfully more saturated** (mean signal sat ≥ winner + 0.05).

Grounded in measurement + **7 blind labels**: colour is largely *irrecoverable* downstream (M 42 `color_boost` closed <30% of the stf→mas gap) while grain is a hard reject. The rule **reproduces 6/7 blind labels and changes only M 42 (stf→mas)**, leaving the 5 correct picks untouched. Galaxy branch untouched (already `mas`-first). MINOR — changes stretch selection, not the scoring model.

**⚠ Canary:** M 42 is the golden nebula baseline — this flips its final (stf→mas). Needs a canary re-baseline + Henry sign-off after the full-pipeline confirmation run.

---

## 1.11.2 — 2026-06-30 (patch)

**Bounded mid-run stretch vision tiebreak — DEFAULT OFF pilot** (`settings.stretch_vision_tiebreak`, default `false`). When enabled, the nebula stretch picker — on a *genuine close call* (top two variants share the same sky **and** exposure bucket, so grain alone separated them) — makes **one cheap Haiku vision compare** of the two previews and lets it break the tie. Any failure / physics-only mode keeps the physics pick. Targets exactly the aesthetic call physics is weakest at (the M 57 grain-vs-placement case) **without re-scoring any step**.

Default behaviour unchanged (flag off → zero API calls, identical picks). Uses a **cheap vision API call**, deliberately *not* the headless-skill mechanism (which is reserved for the deferred weekly batch-eval — see below — to avoid hot-path latency + Max-session-quota contention). This is the **mid-run half** of the critique feedback system; the **deferred half** is the new weekly `/critique-batch-eval` (meta-analysis of accumulated critiques → proposed physics-rule fixes, on the shared `memory-tidy.timer`).

**To pilot:** set `stretch_vision_tiebreak: true`, run a few nebula targets, compare finals + verify the tiebreak picks against the faithful harness, then decide on default. PATCH — opt-in, no default-run output change.

**PILOT RESULT (2026-06-30): FAILED the bar — stays default-off.** Phase 1 (`scripts/stretch_tiebreak_phase1.py`): 33% fire rate, 0% fallback, 3/4 disagreements → gates passed. Phase 2 (blind randomized stretch-preview A/B): 0 win / 1 tie / 1 loss — Henry preferred the physics pick on NGC 6334 (a thin stack, depth 0.00; the tiebreak chose grainier for "better sky", same failure mode as M 57). Not promoted; code stays as a harmless opt-in. The real fix remains the noted depth-aware grain refinement. See memory `stretch-picker-ab-faithful`.

---

## 1.11.1 — 2026-06-29 (patch)

**Preserve the celestial WCS through `color_calibration` (orientation fix).** The PI color-cal path drops the astropy-readable WCS from its output; the preview renderer flips to north-up using the WCS (CDELT2 sign), so a **WCS-less color-cal output renders vertically flipped** vs the WCS-bearing `background_extraction` stage — the "image looks wrong after color calibration" bug (caught on M 42). M 42 hits it because it's **drizzled** (1.19"/px) → SSSC no-ops → PI SPCC/CC fallback (the WCS-dropping path).

Fix: `_preserve_celestial_wcs(src, dst)` re-injects the input WCS into the color-cal output when missing (no-op if a real WCS is present), called at all three `spcc()` success returns. **Empirically verified**: WCS-injected color-cal preview matches the bge preview `as-is` (corr 1.000) instead of `flipud` (1.000). Final scored pixels unchanged (WCS is header + preview); golden M 42 is drizzled so downstream xp/nb no-ops regardless. On native nebulae this restores a usable post-color-cal WCS (latent improvement — monitor). **Takes effect on next `seestar.service` restart** (deferred; M 42 canary + 9 requeues mid-run on old code).

---

## 1.11.0 — 2026-06-29 (minor)

**Nebula stretch-picker: separate sky-placement from highlight-health so grain actually breaks ties.** The nebula `_neb_key` lumped `under`+`blown` (highlight health) into the same "placement" term as sky-band distance at 0.02 resolution. On faint LP nebulae the large, variable `under` term spread candidates across many buckets, so the grain tiebreaker never fired and **the most aggressive/grainiest in-band stretch won** (e.g. IC 1805 `veralux_strong` grain 0.808 beating `stat` 0.165 at equal sky placement).

New key: `(sky_bucket@0.02, health_bucket@0.05, grain, pref)` — sky placement primary (Ha corners can't be pulled back downstream), highlight health a **coarse** secondary, **grain the real tiebreaker**.

Surfaced by the daily `/critique-pending` pass. **VALIDATION CORRECTION (2026-06-30):** the
initial A/B harness used `depth=1.0`, which mispredicted picks on thin stacks (the picker's
`under` term scales with integration depth) — only 1/9 matched live runs, so the original
"7 cleaner / 2 grainier" numbers here were unsound. Re-validated with a FAITHFUL harness
(`scripts/stretch_picker_ab.py`, now recomputes real depth via `_stack_depth_factor` and
self-checks that the new-key replay reproduces each run's recorded pick). On the trustworthy
(faith=OK, current-code) runs 1.11.0 is **net-positive: 5 cleaner** (NGC 6914 0.58→0.16,
NGC 2244 0.40→0.14, IC 1396 0.53→0.27, SH2-101 0.14→0.08, NGC 7000 0.18→0.17), **1 grainier**
(M 57, a thin stack depth 0.29, where sky/health placement legitimately overrode grain), rest
neutral. **Golden M 42 pick unchanged (`stf`).** Galaxy/globular branches untouched. MINOR —
changes stretch selection, not the scoring model/steps. See memory `stretch-picker-ab-faithful`.
Open refinement: depth-aware grain weighting so thin stacks don't pick a much-grainier variant.

---

## 1.10.3 — 2026-06-29 (patch)

**S50 plate scale corrected in `image_analyzer.py` (2.55 → 2.37 arcsec/px).** The native SeeStar S50 plate scale is **2.37"/px** (measured from plate-solved WCS on native M 13/M 51 stacks; physics: 2.9µm IMX462 px / 250mm FL ≈ 2.39; drizzle = 1.19"/px). `psf_arcsec`/`psf_diameter` (consumed by `tool_params.py` for BXT nonstellar PSF) is now ~7% smaller and more accurate.

Surfaced by the weekly memory-tidy pass, which flagged the plate scale as "wrong" based on a stale `reference-s50-pixel-scale` memory claiming 2.9"/px. Ground truth reversed that: **2.9 was the pixel size in microns, not the plate scale.** `agent.py`'s 2.4 was already correct; the memory was fixed.

---

## 1.10.2 — 2026-06-29 (patch)

**SyQon Prism Mini added as experiment variant for `noise_reduction`.** Wires `syqon_prism_denoise()` in `seti_astro.py` — pure in-process numpy path via `prism_denoise_rgb01()`; no subprocess, no GUI objects needed. Model weights (376 MB) downloaded to `~/.local/share/SASpro/runtime/py312/models/syqon_denoise/prism_mini.pt` from the SASpro 1.18 GitHub release. Smoke-tested on a synthetic 128×128 image (CPU, variant=free, output shape confirmed).

Two experiment variants registered in the ontology: `prism_mini_gentle` (strength=0.65) and `prism_mini_standard` (strength=0.85). Standard runs are unchanged — `cc_denoise_inprocess` (CosmicClarity) remains the `seti_astro_fn` default.

**Parallax (SyQon's BXT/deconv replacement):** the architecture in SASpro 1.18.0 is still a placeholder passthrough (`_build_PlaceholderParallaxNet()`) — no real weights can be integrated yet. The PI hold on BXT remains.

---

## 1.10.1 — 2026-06-21 (patch)

**SSSC registered as an experiment-only color_calibration variant.** Phase C3 validation
ran SSSC vs SPCC on five broadband stacks at the color-calibration stage:

| Target | Type | XP stars matched | SSSC ran? | Corner sky B/R (SPCC → SSSC) |
|--------|------|------------------|-----------|------------------------------|
| M 13 | globular | 84 (Stage 2) | yes | 1.012 → 1.003 |
| NGC 188 | open cluster | 45 (Stage 1) | yes | 1.003 → 0.974 |
| M 3 | globular | 31 (Stage 1) | yes | 0.960 → 0.988 |
| M 51 | galaxy | 15 | no — SPCC fallback | — |
| M 42 | emission nebula | 5 | no — SPCC fallback | — |

SSSC broadband only fires on star-rich fields (the ≥20 XP-match gate); galaxies and
nebulae — the bulk of broadband targets — fall back to SPCC. Where it does run it ties
SPCC's sky neutrality (~0.01–0.03), no improvement. So the **broadband default stays
SPCC**; the **LP/dual-band default stays SSSC** (shipped 1.9.0, where SPCC's broadband
white reference is physically wrong). SSSC is registered as a `color_calibration`
experiment variant (`cc_sssc`, alongside `cc_spcc`/`cc_pi`/`cc_none`) so it can be A/B'd
on star-rich targets, but it is **not** the broadband default.

Classified **PATCH**: `experiment_variants` are consumed only when a color_calibration
experiment is explicitly run, so standard runs are byte-identical (canary: golden trio
no drift). This is an additive optional capability, not a default flip — a future
broadband flip to SSSC would be a separate MINOR + canary re-baseline. Henry chose
"experiment only" 2026-06-21.

- `processing_ontology.json`: `color_calibration.experiment_variants` added; step note
  updated to document broadband=SPCC / LP=SSSC defaults and the experiment-only status
  of `cc_sssc`.

---

## 1.10.0 — 2026-06-13 (minor)

**background_neutralize blue-cast fix.** IC 1805's 1.9.0 final carried a measured
dark-corner blue cast (sky B/R 1.26) that desaturated the Hα toward brownish-red. Two
compounding bugs let it through:

1. **Wrong measurement region.** The completeness pass sampled the single
   `auto_rect_50x50` patch. On frame-filling targets (the Heart fills the centre) that
   patch lands on an already-neutral spot, so the step was a **no-op** while the dark
   corners still carried the cast (IC 1805 corner B/R 1.25 survived). It now measures the
   **four dark corners** — the same region `image_analyzer` gates the step on — and
   subtracts the per-channel offset that drives each corner median onto the darkest
   channel's, neutralising the corner sky to grey. Verified: IC 1805 corner B/R 1.25→1.00,
   G/R 0.95→1.00 in all three pivot modes.
2. **Colour step gated on gradient.** `_objective_check` lumped `background_neutralize`
   with `background_extraction` and required a gradient drop (`grad_drop > 0`). A colour
   correction doesn't change the gradient, so when the gradient was already flat the step
   was **always rejected** (IC 1805 log: "gradient 1.000→1.000, 0% drop → rejected"). It
   now has its own branch gated on **sky-cast improvement** (`cast_drop > 0.02`, SNR hold).
   `background_extraction` keeps the gradient gate and the introduces-a-cast reject guard.

M51/M13 corners are already neutral post-SPCC (no-op), but **M42 carries a mild residual
corner cast** (imbal 0.07–0.14; its neutralize was *skipped* in baseline) that the now-working
step corrects — a deliberate improvement that shifts the M42 golden baseline. Hence a **minor**
bump with a **canary re-baseline** (Henry chose the general scope over an IC-1805-only surgical
fix); M42 before/after signed off before the new baseline is committed.

## 1.9.0 — 2026-06-12 (minor)

**NBExtract + SSSC — true narrowband workflow for LP dual-band data + spectrophotometric
color calibration** (the two SASpro 1.18 flagship features, unblocked by the 2026-06-11
engine promotion and the Gaia XP spectral library install, 16 files / 62 GB, G<14).

The SeeStar "LP" filter is dual-narrowband (Hα 20nm + OIII 30nm), but until now the NBN
branch only *approximated* channel separation (Hα≈R, OIII≈B via `ha_dominance_ratio`),
and LP data had **no valid color calibration** — SPCC's broadband white reference is
wrong for dual-band light.

**Changes:**

1. **`xp_stars.py` (NEW)** — shared Gaia-XP plumbing: SEP star detection → WCS px→sky →
   XP-library match (reprojected dist² < 9 px gate) → SASpro raw-aperture RGB photometry
   → spectra enrichment for both consumers. `probe <fits>` and `--selftest` CLI
   (synthetic mixing-matrix recovery corr 1.0; synthetic R(λ) solve RMS 0.0015).
2. **NBExtract — `xp_channel_extract`** (two-phase): the mixing matrix A is fit on the
   star-full **post-color_calibration** image, then NNLS-applied to the **starless**
   linear image at the stretch boundary, so the extracted `xp_ha`/`xp_oiii` channels are
   nebula-only. Fit deliberately happens after color calibration: SSSC rescales channels
   per-pixel, so fit and apply must see the same color domain.
3. **`nb_palette` (NEW step + module)** — composites the true channels into palettes:
   HOO, Foraxx (dynamic G mix), synthetic SHO (S = 0.8·Hα). Force-only aesthetic step
   with 4 experiment variants (`nbp_hoo`, `nbp_foraxx`, `nbp_sho_gold`,
   `nbp_hoo_aggressive`); never score-gated. Replaces the `narrowband_norm`
   approximation path when forced.
   - **Black-sky compositing (required — Henry, 2026-06-12):** each channel carries its
     OWN background pedestal (NNLS dumps the B-channel sky offset into OIII), so a shared
     blackpoint left a COLORED sky (navy/teal wash). The compositor now anchors per
     channel — low-percentile sky → 0 before the stretch, re-anchored to a small floor
     after — and solves the midtone from the signal **high-percentile** (→ 0.80), not the
     sky median (anchoring the median targets the sky, which both tinted the background
     and under-stretched the nebula). Synthetic bicolor proof: sky RGB = [0,0,0] with
     structure preserved.
   - **Extreme-flux-ratio fallback (`NB_PALETTE_MAX_FLUX_RATIO = 20`):** a bicolor palette
     needs a real OIII channel. On strongly Hα-dominant fields (IC 1805: ratio 157) NNLS
     correctly attributes ALL structure to Hα and leaves OIII a flat noise pedestal
     (contrast 0.89 vs Hα 3.06, no morphology) — compositing it can never produce a black
     sky. Above the threshold `nb_palette` is skipped and the proven `narrowband_norm`
     path (Hα≈R, OIII≈G/B + o3_boost) handles the field. nb_palette is for genuine
     bicolor targets (Rosette, Veil) only.
4. **SSSC — `sssc_calibrate`** — solves the camera system response from matched XP star
   spectra (SASpro Stages 1–3) and applies the Stage-2 quadratic gains. Real Bayer
   throughput curve **shapes are mandatory** — identity curves degenerate to a
   gray-world fit that forces R=G=B (found in design; selftest proves the corrected
   math). Broadband = `SONY_COLOR_SENSOR_*-UVIRCUT`; LP = `SONY_CMOS_*-UVIRCUT × OPT.
   L-eNhance` (closest published analogue to the SeeStar's internal dual-band).
   `SessionResponseCache` seeds each solve from the previous one (verified live:
   IC 1805 second run seeded from prior ctrl_points). Quality gate: applied-stage
   (Stage-2 sigma-clipped) RMS < 2.0 — mirrors SASpro's own prior-trust threshold; the
   headline `residual_rms` is the unclipped all-star value and is outlier-dominated on
   nebula-rich fields.
5. **LP routing (new default)** — `color_calibration` on LP data tries **SSSC first**;
   any failure falls through to the legacy PI SPCC/CC path unchanged. SSSC success
   writes `.sssc_applied` and counts as calibration success for the SCNR-skip and
   linked-stretch rules (preserve-linked-color holds). **Broadband SSSC ships
   experiment-only**: the M 13 side-by-side showed a green core shift vs SPCC's neutral
   — the default flip waits for C3 validation (separate bump + canary re-baseline).
   SSSC results are **exempt from the whole-frame SNR objective gate** (and get the
   lenient catastrophic-only stats-veto floor): the solved per-channel gains (k_R ≈ 2.26
   on LP data) legitimately shift the SNR metric, which vetoed a correct calibration on
   the first live integration run (IC 1805, SNR×0.81 vs the 0.90 gate). The real quality
   check is the solver's applied-stage RMS < 2.0 gate, mirroring the crop precedent of
   not second-guessing a deliberate transform with whole-frame stats.
6. **Latent gate bug fixed** — LP detection now happens ONCE on the source stack header
   (`_is_lp_run`): the crop step strips FILTER from run-dir intermediates, so the old
   mid-pipeline header sniffs (xp hook, `spcc_lp_filter` param) would have silently
   no-opped on every real run.

**Verified headless:** xp_stars selftest; IC 1805 LP end-to-end (280 XP stars, Stage 3,
applied RMS 0.888, 60 s, cache-seeded on rerun); M 13 broadband (84 stars, Stage 2, RMS
0.586, 6 s). Old-drizzle stacks (1.19″/px WCS/row-order mismatch) fail the star-match
gate closed and no-op the XP machinery by design.

**Critiques:** none — feature work from the 2026-06-11 plan, not critique-driven.

## 1.8.0 — 2026-06-10 (minor)

**NBN vibrancy restored: fix the silent revert of the nbn saturation pass.** The 1.7.0
NBN runs on IC 1805 (5.8) and NGC 2244 (7.2) rendered steel-grey instead of the
pathfound vibrant gold/teal SHO variants (`IC_o3_1p5_sat` / `RO_o3_1p3_sat`, trials
2026-06-08). The pathfound chain *did* run — `narrowband_norm` then `color_boost`
preset=nbn — but the saturation pass was thrown away by two compounding bugs, with the
step still logging "applied" (output bit-identical to input on both runs).

**Root cause.** NBN's step input was a non-float working copy, so the pi_postprocess.js
"Ensure Float32" block fired — but it used `SampleFormatConversion.To32Bit`, which is
32-bit **unsigned integer**, so PI saved the NBN output as full-range uint32
(0..4.29e9). The nbn `color_boost` (the SOLE saturation pass — `hoo_boost=0` by design)
then ran correctly, but its midtone `_lum_mask_blend` computed the mask on the raw
uint32 luminance: every pixel sat far above the 0.85 upper bound, the mask was 0
everywhere, and the blend replaced the boosted output with the original. NBN itself
balances Hα/OIII to near-neutral (bright-region saturation 0.402 → 0.198), so losing
the restore pass is exactly what produced the muted palette. (The earlier critiques
mis-framed this as an NBN palette inversion — the SHO palette is the intended
aesthetic; aesthetic steps are not score-gated.)

**Changes:**
1. **`pi_postprocess.js`** — ensure-float block: `To32Bit` → `ToFloat` (IEEE 32-bit
   float).
2. **`experiments.py` `_lum_mask_blend`** — min-max normalize original/processed to
   [0,1] when out of range before computing the mask and blending, so an
   integer-scaled input can never zero the mask again.
3. **`auto_process.py`** — the nbn `color_boost` preset is exempt from the midtone
   lum-mask blend: the pathfound look was generated unmasked, and the mask's upper
   bound (~0.85) would exclude the bright cores where the restored saturation matters
   most. Galaxy/nebula presets keep the mask. The skip is logged.
4. **`seti_astro.py` `narrowband_normalize`** — safety net: any non-float or
   out-of-range PI output is rewritten as normalized float32 before returning to the
   pipeline.

**Critiques:** `20260610_114522_seestar_nebula` (IC 1805), `20260610_101238_seestar_nebula`
(NGC 2244) — both to be revised: palette intended, defect was the reverted saturation pass.

**Also in 1.8.0 — the three approved 2026-06-10 critique recommendations:**

5. **`hdr_core_blend` — masked-core HDR blend** (new step; M 42 + M 31 critiques).
   Global `hdr_compression` recovers the blown core but crushes faint signal — the
   "no improvement" objective veto fires and the step never lands. New
   `seti_astro.hdr_core_blend` runs the same `compute_wavescale_hdr` engine but blends
   it only under a feathered core luminance mask
   (`M = gaussian(clip((gaussian(L,6)-thr)/(0.97-thr)), 20)`; thr 0.72 nebula / 0.80
   galaxy, set from object_type). Blend is chroma-preserving (luminance-ratio), with a
   **clipped-hue refill**: above L≈0.78 the stored pixel ratios are already washed
   near-neutral (M 31 bulge measured R/G 1.00 at the center vs 1.06–1.10 at r≥100 px),
   so dimming alone reveals grey — chroma is refilled from the surrounding well-exposed
   region via normalized convolution (σ=100, weights = 1−clipness). Added to
   `seestar_galaxy` and `seestar_nebula` `optional_nonlinear_steps` (galaxy path
   previously had no HDR step at all); reuses the existing core-clip-fraction gate;
   exempt from the whole-frame stats veto (touches 0.15–0.9% of frame — same precedent
   as crop). Verified: M 42 wisp/dark-lane recovery matches the approved prototype;
   M 31 bulge dims to structure with warm hue held.

6. **Folio-aware stretch pick (nebula branch)** (M 42 critique). The 1.7.0 detail-first
   picker chose `mas` for M 42 with corner sky 0.122 — "in band" for the wide per-type
   emission range (0.06–0.16) but 42% over the folio's `bg_level_range` [0.05, 0.09];
   cost −0.2. `_physics_pick_stretch` now takes `folio_band` and charges the distance
   outside the folio band into the nebula placement key. Galaxy branch unchanged
   (deliberately ignores sky; `sky_mute_masked` handles it). Verified on the M 42 run's
   9 real variants: pick flips mas (0.122) → stf (0.050, in-band, lower grain).

7. **`sky_green_rebalance` — sky-only masked green cap** (new step; M 31 + M 42
   critiques). The SPCC-success rule skips SCNR, so small residual sky greens go
   unpolished (M 31 sky G/R 1.12, M 42 1.06). New `seti_astro.sky_green_rebalance`
   neutralizes green only in a sigma-clipped sky mask, leaving object signal and SPCC
   star colors untouched. Design notes from testing: the cap is `max(R,B)` not the
   SCNR-style average (average inverted M 31's weak-blue sky to magenta, G/R 0.84),
   and the excess field is computed on σ=6-smoothed channels so the pointwise cap
   can't clamp just the high noise tail and drag the median below neutral. Added to
   both galaxy and nebula optional lists. Verified: sky G/R 1.045→1.012 (M 42),
   1.061→1.010 (M 31); bright-region max diff ≤0.012. Full SCNR on SPCC data remains
   forbidden (IC 434 precedent).

---

## 1.7.0 — 2026-06-10 (minor)

**Galaxy workflow: detail-first stretch selection + masked sky mute.** Henry's directive
from the M 51 mas review: *"the key is to pick the good galaxy stretch and not worry
about the sky."* Galaxy detail looked excellent on PI MaskedStretch (mas), but the
picker could never choose it — and the downstream sky handling dimmed the very outskirts
the good stretch preserved.

**(1) Galaxy branch in the physics stretch picker.** For `object_type` containing
"galaxy", `_physics_pick_stretch` now ignores **all** sky terms (bg_dist, bg_noise,
rel_grain, and the 1.6.0 grain-cap) — every one of them worked against the
faint-preserving stretches: mas/veralux leave the sky high (big bg_dist) and the
grain-cap cut them on sky-corner σ, crowning ghs_strong, which crushes faint arms and
tidal bridges. Selection is now highlight health only (under + blown, bucketed 0.05) +
the dead-channel reject, tie-broken by a faint-preserving preference order
(mas > veralux_strong > veralux > stat_bright > stat > stf > ghs family). **Physics
only — no Claude API call** (Henry's explicit constraint). Validated against an
in-session vision ground truth on 4 galaxies: M 51 / M 81 / M 101 → mas,
NGC 4565 → veralux family (its mas is nearly black; the health gate rejects it) —
**4/4 reproduced offline**. Nebula and non-galaxy branches unchanged.

**(2) MAS output normalized to 0–1.** PI MaskedStretch writes int32-scaled FITS
(BITPIX=32, BZERO=2147483648, values to 4.27e9). Previews auto-normalize so it *looked*
fine, but stats read p99≈2.8e9 — mas had never actually been selectable. `_run_mas` now
divides by max (when max > 1.5), clips to 0–1, writes back float32 with
BZERO/BSCALE/DATAMIN/DATAMAX stripped.

**(3) `sky_mute_masked` replaces the flat sky mute on the galaxy path.** The old
`_mute_sky` scaled the whole frame down, dimming faint outskirts along with the sky.
The new `seti_astro.sky_mute_masked` auto-tunes a luminance mask: σ-clipped sky
estimate (5 iters, clip at med+2.5σ), smoothstep mask from sky+1.5σ → sky+5σ, gaussian
feather (σ3), dilation blend to protect halo edges, then a black-point lift
(bp = sky−1σ) darkens **only** the sky; the galaxy composites through untouched.
M 51 test: sky 0.018 → 0.001, galaxy coverage 11.6%. Step record stays `sky_mute`;
non-galaxy types keep the flat mute.

**(4) color_boost — no change needed.** The existing `_lum_mask_blend` midtone mask
(lower = clamp(median×1.3, 0.08, 0.30), computed from the post-stretch image) already
luminance-gates saturation: measured on mas M 51, mask lower 0.194 vs sky 0.149 — only
the galaxy's ~10% of pixels receive the boost, so faint outskirts aren't
chroma-amplified.

**(5) color_boost delta smoothing — posterized color fix.** Henry's zoomed M 51 report:
"the color is too blotchy and abrupt." Stage-by-stage comparison localized it to
color_boost: hue is noisy in faint regions, so the per-pixel hue-Gaussian delta flipped
adjacent pixels between the +0.30 blue boost and the −0.05 yellow damp — a posterized
orange/purple patchwork. The saturation delta **field** is now smoothed
(`gaussian_filter(σ=4)`) before applying — luminance detail untouched, boost transitions
spatially gradual. Verified on the M 51 mas stretch: speckle gone at σ4, blue arm boost
preserved (σ2 leaves residual speckle, σ6 adds nothing).

**(6) Manual crop editor apply fix.** Both of Henry's manual crops (M 31 −17°, M 42 0°)
landed misaligned: the editor preview is flipped vertically for north-up stacks
(CDELT2 > 0) and Cropper.js reports x/y in the rotated bounding-canvas frame, but
apply-crop mapped both straight into the unflipped/unrotated array. New
`target_crop.cropper_box_to_array` un-rotates the canvas box center, un-flips the row,
negates the rotation angle in array space, then scales to full resolution
(`main.review_apply_crop` now uses it). Poisoned saved crops for M 31/M 42 cleared —
both targets need a fresh manual crop + re-queue.

**(7) Sky-only gradient metric — background_extraction was being wrongly rejected.**
On M 81, GraXpert flattened the sky *perfectly* (row-band span 0.00022 → 0.00003), yet
the gate rejected it: the all-cells gradient metric is dominated by grid cells sitting
on M 81/M 82, and SPCC's bg_anchor drops the denominator pedestal, so the metric read
0.176 → 0.248. The uncorrected gradient was then amplified 9× by the mas stretch into
the hazy "green cast" top Henry flagged. New `gradient_severity_sky`
(image_analyzer) measures flatness over the object-excluded bg cells only
(M 81: 0.085 → 0.006 — correctly accepted); the BE/background_neutralize gate, the
physics gradient score, **and the generic post-step stats veto** now prefer it (the
veto was a second independent guard on the old metric — it reverted the M 81 GraXpert
result even after the gate fix). M 81's weak sky mute (sky σ ±0.066) was a downstream
symptom of the same uncorrected gradient.

**(8) green_cap_masked — post-SPCC lime cast on galaxies.** With the gradient fixed,
M 81 still rendered yellow-green (grader: color_balance 3.5, "bulge should be warm
golden/orange"). Trace: SPCC's linked white balance neutralises the sky perfectly but
leaves the galaxy *signal* ~10% green-heavy on S50 OSC data (sky-subtracted galaxy
G/R 1.04–1.12 post-SPCC; an Sab bulge should be ~0.9), and the mas stretch amplifies
that into the lime wash. New `green_cap_masked` (seti_astro) runs on the linear
**starless** layer right after star split, galaxies only: feathered luminance mask +
sky-subtracted green cap `G_sig → min(G_sig, (R_sig+B_sig)/2)` at amount 0.7 (Henry's
A/B pick: G/R 1.10 → 1.00, warm cream bulge with blue arm separation). The sky pedestal
is byte-identical on output and star colors keep SPCC calibration, so the
SPCC-success SCNR-skip rule (IC 434 sky-crush) is not violated. Self-gates — passes
through unchanged when measured signal G/R ≤ 1.02.

**(9) Hα/HII knot boost in the galaxy color_boost preset.** Henry: "Are we able to
boost the Ha signal more in the galaxy?" The galaxy preset only boosted blue
(240°/280°) plus a tiny 10° red band, leaving the pink HII knots washed out next to
the Reddit 42 h reference. Added a (340°, 25°, +0.22) pink-magenta saturation band
plus an 8% red-channel lift where that band fires — computed on the *original* hue
field with the same σ4 delta smoothing as the main boost, so it cannot re-introduce
posterized edges. Henry's pick from the M 51 arm-zoom A/B (+0.22 + red lift).

Minor bump: gate/param tuning + an implementation swap on the existing `sky_mute` slot;
no scoring step added/removed, scores remain comparable.

## 1.6.1 — 2026-06-09 (patch)

**Three crop/queue bug fixes — no expected change to good-run output.**

**(3) A user-chosen crop was being discarded by the objective SNR veto.** Henry asked
"did it discard my crop?" for M 51 — its coverage edge-crop (no rotation) was
force-applied by geometry, then the generic post-step stats-veto reverted it because
whole-frame SNR dropped 102.7→59.0 (0.57, below the 0.85 guard), logging
"crop — no improvement, skipped." Cropping reframes the image (different pixel
population), so a before/after SNR comparison is meaningless. Per Henry's directive
("Don't veto crop. I am choosing it"), crop is now **fully exempt** from objective
gating: `_objective_check("crop")` returns ok/improved=True, and the post-step
stats-veto skips `step_name == "crop"`. A crop chosen in the manual review is always
applied — the pipeline never second-guesses framing with stats.


**(1) Manual crop rotation left black corner wedges.** Henry added a small rotation in
the Cropper.js editor and the crop "wouldn't reset to a rectangle." The old apply-crop
path cropped the axis-aligned box *first*, then `scipy.ndimage.rotate(reshape=True,
cval=0.0)` rotated that box inside a larger canvas — leaving zero-filled triangular
wedges in the corners. Fixed with **rotate-then-crop**: new
`target_crop.rotated_crop(data, cx, cy, cw, ch, rotate_deg)` uses
`scipy.ndimage.affine_transform` to inverse-map each output pixel into the rotated
source, so the whole `cw×ch` box is filled with real pixels. Shared by the
`/review/{id}/apply-crop` endpoint and the saved-crop fractional fallback
(`target_crop._apply_frac_crop`). Rotation direction preserved (matches the prior
`scipy.ndimage.rotate(-rotate)`, Cropper.js +angle = CW; verified ≤1px). The WCS
sky-box reuse path was already correct (folds rotation into PA), so this only touches
the immediate winner FITS and the no-WCS fallback.

**(2) Park-on-review didn't advance the queue.** When M 31's crop review grace elapsed
it parked (released its queue slot), but its `auto_process` thread kept holding
`PIPELINE_LOCK` while blocked at `crop_review` `ev.wait()`. The worker popped the next
target (M 51), which logged "starting" then stalled ~40 min on the lock until M 31 fully
finished. Fix: `PIPELINE_LOCK` now tracks its owner thread
(`auto_process.mark_pipeline_lock_held` / `clear_pipeline_lock_held` /
`release_pipeline_lock_for_park` / `reacquire_pipeline_lock_after_park`);
`crop_review._await_decision` **releases** the lock when a review parks and **re-acquires**
it before resuming, so the next local job runs immediately while the idle parked review
waits — real PI execution stays serialized. The laptop worker calls `auto_process`
without the lock, so the release is owner-guarded (no-op there).

---

## 1.6.0 — 2026-06-08 (minor)

**NBN vibrancy overhaul — real OIII boost on the HOO palette.** Surfaced by Henry's
investigation into why auto nbn runs (IC 1805, NGC 2244) came out muddy grey-tan
instead of the vibrant blue-OIII / warm-Ha palette he gets by hand. Two latent no-op
bugs were the cause: (1) the pipeline set `nbn.normalizationMode`, which does **not**
exist in PI 1.9.3 — so `o3Boost` sat at its 1.0 default and the teal OIII core never
surfaced; (2) the `color_boost` step ran with the `galaxy` preset because the standard
param build reads ontology `parameters` while the key is actually `params`. Both fixed:
o3Boost is now auto-scaled from Hα dominance and a dedicated warm-gold/teal nbn
saturation preset is routed in. NBN is an **aesthetic** step and is **not** score-gated,
so the scoring model is unchanged — 1.5.0 and 1.6.0 critiques remain comparable for
non-nbn runs. Validated on IC 1805 (ratio 2.54 → o3Boost 1.50) and Rosette
(2.16 → 1.35) trials matching Henry's reference image.

- **NBN o3Boost lever** (`pi_postprocess.js`): the NarrowbandNormalization block now
  sets `nbn.palette=0` (HOO) and `nbn.o3Boost=job.nbn_o3_boost` (the real OIII-boost
  property), dropping the dead `nbn.normalizationMode` assignment. o3Boost is the lever
  that surfaces the teal-blue OIII core; left at 1.0 it produced muddy yellow-brown.
- **Ratio-driven o3Boost** (`auto_process.py`): narrowband_norm auto-scales
  `o3Boost = clamp(0.50 + 0.395·ha_dominance_ratio, 1.25, 1.7)` — a more Hα-dominant
  target gets more OIII boost to surface its weak teal core. Curve fitted to IC 1805
  (2.54→1.50) and Rosette (2.16→1.35) visual sweet spots. Manual overrides
  `nbn_o3_soft`(1.3)/`nbn_o3_strong`(1.7). Measured ratio + chosen boost logged per run.
- **NBN color_boost preset** (`seti_astro.py` + `auto_process.py` + ontology): new
  `nbn` preset (gold/copper dust 35°/+0.40, amber bleed 18°/+0.22, teal OIII core
  195°/+0.32, global_sat_lift 0.06) routed in explicitly whenever narrowband_norm is
  forced — fixing the latent `params`/`parameters` key-mismatch that left nbn runs on
  the galaxy preset. This is now the **sole** saturation pass for nbn; the in-JS HOO
  ColorSaturation restore is disabled (`hoo_boost=0`) to avoid double/triple saturation.
- **Threaded params:** `nbn_o3_boost` + `nbn_hoo_boost` through
  `pixinsight.run_postprocess`; `o3_boost` + `hoo_boost` through
  `seti_astro.narrowband_normalize`. The `/narrowband_norm` branch endpoint `method=`
  now means `auto` (ratio-scaled) | `soft` (1.3) | `strong` (1.7) instead of the dead
  MaximumStars/Equalize.

**Stretch picker + crop review (folded into 1.6.0, no separate bump).** Added in the
same uncommitted, not-yet-run 1.6.0 window. Both are aesthetic/framing choices outside
the score-gated model, so comparability is unaffected.

- **Nebula stretch picker** (`auto_process._physics_pick_stretch`): for nebula
  object_types the pick is now dominated by **sky placement** — the variant whose
  background sits closest to the per-type band with the least under-exposed/blown
  clipping wins (`key = round((bg_dist+under+blown)/0.02)`), and relative grain only
  breaks ties within that bucket. Previously grain could win outright and select an
  STF-overshoot variant that washed the sky bright. Galaxy/other types use the blended
  cost plus the new **grain-cap** below.
- **Galaxy/non-nebula stretch grain-cap** (`auto_process._physics_pick_stretch`): before
  the blended-cost sort, drop any candidate whose grain (`bg_noise + 0.25·rel_grain`)
  exceeds **1.5× the cleanest IN-BAND candidate's grain**, then take the lowest cost among
  survivors. Fixes the **M 51 6/9** regression: veralux (grain 0.248) beat the cleaner
  stat_bright (0.145) — both ON-TARGET sky — because veralux's brighter midtones lowered
  its under-exposure term more than `noise_w·bg_noise` charged its grain; final noise
  tanked to 5.5 and a non-degradation restore shipped an earlier checkpoint. Reference
  grain comes from in-band candidates only, so a uniformly-grainy field (no clean in-band
  option) is untouched. Opposite intent to the nebula change above (nebula corners hold
  real Ha → de-weight grain; galaxies → discipline it). Critiques
  `20260609_021519_seestar_galaxy` + `20260604_143439_seestar_galaxy`.
- **Crop is now a remembered manual review (Claude veto removed):** on a target's
  **first** auto-process the crop step opens a manual review (`crop_review.py`)
  presenting every `crop_multi` candidate (artifact-trim / canonical / coverage /
  intersection / LIR), each with an STF preview, plus a Cropper.js editor to draw or
  rotate a custom crop. The choice is persisted per target (`target_crop.py` /
  `target_crops` table) as a **WCS sky-box** (center RA/Dec + on-sky size + PA, primary)
  with fractional bounds + rotation as a no-WCS fallback, and reused on every later run
  by reprojecting onto the saved box — identical framing every session regardless of
  mosaic canvas growth. The old `_claude_confirm_crop` / `_claude_crop_clips_subject`
  vision veto is deleted. The review honors the standard grace→park→wait queue pattern
  (5-min grace, then releases the queue and waits indefinitely).
- **Crop clear/redo controls:** the saved crop can be cleared from the target page
  ("Clear & redo crop" → `POST /target/{target}/clear-crop`) so the next run re-opens
  the review, and a per-run "Re-crop (force fresh crop review)" queue flag (`re_crop` on
  `/queue` + the queue-view add-job checkbox) forces a one-off fresh review without
  clearing the stored crop. Manual-crop geometry (incl. rotation) is persisted from the
  apply-crop endpoint, which knows the exact applied box.

---

## 1.5.0 — 2026-06-07 (minor)

**Alignment outliers are dropped from stacking.** Surfaced by the IC 1805
panel-drop investigation: one pre-EQ sub (RA 69.98/DEC 20.27, ~50° off-pointing)
was a spoofed-coordinate frame polluting the max-frame mosaic. Each sub's TRUE
plate-solved sky position/rotation is now stored in the DB and an off-cluster sub
is excluded at stack-selection time. Frame selection changes for off-pointing data;
the scoring model and every processing step are unchanged — no scoring step added or
removed — so 1.4.0 and 1.5.0 critiques remain comparable for well-pointed data.

- **Per-sub plate-solve capture:** `light_files` gains `solved_ra/dec/rot/scale` +
  `solve_status` (`ok|failed|outlier|NULL`) + `solved_at`. Populated three ways —
  harvesting the WCS the stack pipeline already solves (near-zero cost), an
  idle-worker eager-solve, and on-demand `POST /solve/{target}`. Keys
  association/alignment QA on TRUE solved position instead of the spoofable/colliding
  header OBJECT name.
- **Alignment-outlier flagging** (`sub_solver.flag_alignment_outliers`): per
  (target/panel) group, a robust circular-median solved position; any sub > 2.0° off
  is stamped `solve_status='outlier'`. Does NOT touch the manual `exclude` column.
- **Stack-time outlier exclusion** (`stacker.py`): subs with `solve_status='outlier'`
  are dropped from `qualifying_paths` after the exptime filter, gated by setting
  `exclude_alignment_outliers` (default `true`). An off-pointing sub no longer enters
  registration/integration.
- **Rotation visibility** (`alignment_summary`): per-group `rot_median` + `rot_spread`
  (max deviation from median, deg) from `solved_rot`, so subs rotated relative to each
  other are inspectable. Informational only — alt-az field rotation is expected and
  corrected by registration, so rotation does not auto-exclude.

---

## 1.4.0 — 2026-06-07 (minor)

A new **artifact-trim** crop candidate becomes the preferred crop. Surfaced by
Henry's annotated review of IC 1805 (mosaic) and NGC 6334: the strict LIR clipped
the subject on tilted mosaics, canonical over-sized bordered targets, and the
existing coverage maps didn't even match the stack dimensions. The crop step is
unchanged in identity and the scoring model is unchanged — no scoring step added or
removed — so 1.3.0 and 1.4.0 critiques remain comparable.

- **Artifact-trim crop candidate (preferred):** `crop_multi` adds
  `_artifact_trim_bounds` — STF-stretches a luminance, then per edge scans inward and
  trims while the band is anomalous by EITHER brightness (median below `0.55×`plateau
  = falloff/black band, or above plateau by `0.18` = residual gradient) OR
  high-frequency noise (above `1.6×` interior = grainy low-frame-coverage border).
  Stops after a clean run, capped at 35% per edge, with a degenerate-box guard that
  falls back to the full frame. Coverage-map-independent (works on linear stacks and
  manual XISF masters). It keeps the FULL target while removing edge stacking
  artifacts and is now chosen by default over canonical/LIR; canonical/coverage/lir
  are still generated for the record and remain the fallback. Validated on IC 1805
  master (full Heart kept, ~10% ragged bottom trimmed) and NGC 6334 (top 4% / bottom
  25% / left 8% / right 13%). See [[feedback-crop-edge-artifacts]].

---

## 1.3.0 — 2026-06-07 (minor)

Four fixes from Henry's review of the 1.2.0 reprocess batch. Gate/threshold/param
tuning plus one conditional step skip — the SCNR skip is gated on the SPCC-success path
(mirrors the existing narrowband SCNR skip), the scoring model is unchanged, and no
scoring step is added or removed, so 1.2.0 and 1.3.0 critiques remain comparable.

- **Crop over-crop floor (#102):** `crop_multi` enforces a native-resolution floor
  (short side ≥ `max(900px, 0.45 × native short)`) and falls back to the largest-area
  candidate when the chosen candidate (usually canonical, sized from folio angular size)
  would downsample a small target below S50 resolution — fixes the ~76px M 57 / ~442px
  M 109 thumbnails. See [[feedback-no-overcrop-s50]].
- **Crop under-crop gate (#103):** the post-stretch crop-force check now measures corner
  brightness on a stretched luminance (1/99.5 percentile + gamma) instead of linear data,
  and adds a black-edge band detector (>30% near-black in a 2% edge band) — recovers
  vignetted/stacking-border finals (NGC 6914, IC 1805, M 83) the linear-threshold gate
  silently passed.
- **Stretch background clamp (#104):** after the stretch winner is picked, an additive
  sky-level clamp pulls the background into the per-object-type band when a variant
  (e.g. `veralux_strong`) overshoots above it, so the final sky lands in-band instead of
  washed bright.
- **SCNR-on-SPCC skip + channel-crush guard (#105):** SCNR is skipped on the SPCC-success
  path (`color_calibration` applied and no `.spcc_failed` sentinel) because its non-linked
  Average-Neutral green clip breaks SPCC's linked white balance and crushes the neutral
  sky toward black (IC 434 / SH 2-273 R=0.0 / G=0.0019 "G/R=140" cast); SCNR still runs
  when SPCC failed or never ran. A belt-and-suspenders `_guard_channel_crush` at final
  save re-neutralises any residual coloured near-black pedestal left by a downstream step.

---

## 1.2.0 — 2026-06-07 (minor)

Eight further quality fixes from a second pass over the same 20-critique batch.
Gate/threshold/param/default-fn/prompt tuning only — the deconvolution default swaps
engine (cc_sharpen → BXT) but the step itself is unchanged, the scoring model is
unchanged, and no scoring step is added or removed, so 1.1.0 and 1.2.0 critiques remain
comparable. (Recommendation #7, post-GHS sky pull, was already implemented in 1.1.x; #10
two-path HDR is deferred as a separate MAJOR initiative.)

- **Crop gate (#1):** force a crop when measured corner `spread/bg > 0.40`
  (vignette / uneven corners), not just on low coverage — recovers vignetted finals
  (NGC 6914, M 83, NGC 6334).
- **dark_enhance gate (#2):** target-aware shadow-SNR floor (5 for galaxy/nebula vs 12
  otherwise) so faint outer-halo / reflection veil is lifted instead of blanket-vetoed
  (IC 4592, M 109, NGC 4565, NGC 6914, SH 2-273).
- **noise_reduction gate (#3):** keep a denoise that holds SNR when the result is still
  grainy (`σ/bg > 0.18`) instead of reverting on a marginal linear "no improvement"
  (NGC 7635, SH 2-273).
- **deconvolution default (#4):** standard-mode default is now `bxt_deconvolve` (full
  BlurXTerminator), best-in-class for SeeStar; `cc_sharpen_inprocess` remains the
  automatic fallback when PixInsight is unavailable and an explicit experiment variant
  (NGC 6914 star-ringing halos post-stretch).
- **Scale hints (#5):** inject S50 `FOCALLEN=250mm` + `XPIXSZ/YPIXSZ=2.9µm` into manual
  XISF→FITS masters that lack them so ImageSolver/SPCC gets a scale seed within the
  factor-2 window (IC 1805).
- **halo_suppression cap (#6):** cap `reduction_level` at 1 in dense star fields
  (clusters or >1000 detected stars) so aggressive halo reduction doesn't eat stars /
  darken the field (NGC 6914 Cygnus).
- **Grader colour context (#8):** feed the measured per-channel corner-sky R/G/B balance
  into `assess_stacked_image` so the vision grader stops missing real sky colour casts
  (NGC 2244 NBN blue cast B/R 1.46).
- **NBN neutralize completeness (#9):** `background_neutralize` adds a residual-cast
  top-up that equalises sky-patch per-channel medians when a cast remains after the pivot
  shift (NGC 2244 NBN sky B/R 1.46).

---

## 1.1.0 — 2026-06-07 (minor)

Seven quality fixes from the first critique batch (19 critiques, runs 2026-06-05/06).
Gate/threshold/param/prompt tuning only — no scoring step added or removed and the
scoring model is unchanged, so 1.0.0 and 1.1.0 critiques remain comparable.

- **HDR gate (#84):** also fire on a small intensely-blown core (near-white fraction
  `lum≥0.98 ≥ 0.10%`), not just a ≥1% top-decile fraction — recovers HDR on compact
  blown cores like the M 42 Trapezium.
- **Stretch picker (#85):** add a relative-grain (`bg_noise/bg_level`) tiebreaker,
  guarded to backgrounds above 0.04, to break ties away from grainy `veralux_strong`.
- **Background gate (#86):** `_objective_check` now rejects a background step that
  *worsens* per-channel sky balance (`sky_b/r`, `sky_g/r`) and flags residual cast for
  a harder retry.
- **Workflow routing (#87):** resolve `object_type` from DB `targets.type` before the
  name heuristic, fixing substring collisions (e.g. `M 109` matching `m 10` → globular).
- **NBN palette (#88):** post-NarrowbandNormalization HOO `ColorSaturation` restore in
  `pi_postprocess.js` so genuine Hα/OIII signal isn't left muted grey-tan (measured
  emission B/R 0.86–0.97 on M 42 / NGC 2244 NBN).
- **Grader context (#89):** feed the measured background band into
  `assess_stacked_image` so it stops calling measured-correct backgrounds "too dark".
- **Data-integrity QA (#90):** `_data_integrity_flags` surfaces wrong-field, pre-EQ
  pointing-spoof, and manual-source provenance issues to log/Telegram and `run.log` so a
  run isn't graded for problems it didn't cause.

---

## 1.0.0 — 2026-06-05 (baseline)

First versioned reference point for the critique-driven iteration loop. No code
changes — this establishes the line that subsequent runs are compared against.

- Critiques evaluated: none (baseline).
- Changes: none.
