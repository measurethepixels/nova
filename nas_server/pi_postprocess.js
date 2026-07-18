/**
 * PixInsight headless post-processing script — full tool suite.
 * Reads job config from path written to /tmp/pi_job_path.txt by Python.
 *
 * Pipeline (all optional via job flags):
 *   Linear:  DynamicBackgroundExtraction | GradientCorrection
 *            ColorCalibration | BackgroundNeutralization | SPCC
 *            MultiscaleLinearTransform (sharpen) | TGVDenoise
 *            BlurXTerminator | NoiseXTerminator
 *   Stretch: HistogramTransformation (auto-stretch)
 *   Non-lin: StarXTerminator | SCNR | NarrowbandNormalization (starless, post-stretch)
 *            CorrectMagentaStars | MorphologicalTransformation
 *            HDRMultiscaleTransform | LocalHistogramEqualization
 *            ColorSaturation | CurvesTransformation
 *   Save:    XISF
 *
 * Invocation (offscreen, no display needed):
 *   LD_LIBRARY_PATH=/opt/PixInsight/bin/lib QT_QPA_PLATFORM=offscreen \
 *   QT_PLUGIN_PATH=/opt/PixInsight/bin/lib/qt-plugins \
 *   /opt/PixInsight/bin/PixInsight --automation-mode -n \
 *   -r="/path/to/pi_postprocess.js" --force-exit
 */

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------
var logLines = [];
var logPath = "/tmp/pi_postprocess_last.log";

function logln(msg) {
    console.writeln(msg);
    logLines.push(msg);
}

function flushLog() {
    File.writeTextFile(logPath, logLines.join("\n") + "\n");
}

logln("pi_postprocess.js starting");

// ---------------------------------------------------------------------------
// Job config — Python writes the path into a sentinel file
// ---------------------------------------------------------------------------
var sentinelPath = "/tmp/pi_job_path.txt";
var jobPath;
try {
    jobPath = File.readFile(sentinelPath).toString().trim();
} catch(e) {
    logln("ERROR reading sentinel: " + e.message);
    flushLog();
    throw new Error("Cannot read sentinel file");
}

if (!jobPath || !File.exists(jobPath)) {
    logln("ERROR: job config not found: " + (jobPath || "(undefined)"));
    flushLog();
    throw new Error("No job config");
}

var job = JSON.parse(File.readFile(jobPath).toString());

// ---------------------------------------------------------------------------
// Parse job parameters
// ---------------------------------------------------------------------------
var inputPath  = job.input;
var outputPath = job.output;

// Linear tools
var runDBE        = job.dbe === true;
var dbeCorrection = job.dbe_correction || "subtraction";   // "subtraction"|"division"

var runGC         = job.gradient_correction !== false && !runDBE;  // skip GC if DBE is on

var runCC         = job.color_calibration !== false;
var runBGN        = job.bgn === true;   // BackgroundNeutralization

var runSPCC         = job.spcc === true;
var spccLpFilter    = job.spcc_lp_filter === true;   // true when LP filter was used during capture
var spccWhiteRef    = job.spcc_white_ref || null;    // null = keep SPCC default spectrum
var gaiaDatabaseDir = job.gaia_db_path || null;      // override PI's global GAIA DR3 SP path (for remote workers)

var runNBN        = job.nbn === true;   // NarrowbandNormalization (Ha/OIII duo-band)
var nbnMethod     = job.nbn_method || "MaximumStars";
var nbnO3Boost    = (typeof job.nbn_o3_boost === "number") ? job.nbn_o3_boost : 1.0;  // PI o3Boost — OIII vibrancy lever
// HOO saturation restore after NBN. NarrowbandNormalization BALANCES Ha/OIII, which on
// SeeStar LP dual-band data leaves the emission near-neutral (grey-tan) unless a per-hue
// saturation pass restores the vivid Ha-red / OIII-teal pop. Must overshoot the standard
// nebula preset (0.35) to overcome NBN's balancing. 0 disables. See reference_seestar_lp_filter.
var nbnHooBoost   = (typeof job.nbn_hoo_boost === "number") ? job.nbn_hoo_boost : 0.55;
var satPreset     = job.sat_preset || "uniform";  // "galaxy"|"nebula"|"uniform" for ColorSaturation

var runMLT        = job.mlt === true;   // MultiscaleLinearTransform sharpening
var mltSharpen    = typeof job.mlt_sharpen    === "number" ? job.mlt_sharpen    : 0.20;
var mltDenoise    = typeof job.mlt_denoise    === "number" ? job.mlt_denoise    : 0.50;
var mltLayers     = typeof job.mlt_layers     === "number" ? job.mlt_layers     : 4;

var runTGV        = job.tgv === true;   // TGVDenoise (PI native, no GPU needed)
var tgvStrength   = typeof job.tgv_strength   === "number" ? job.tgv_strength   : 1.0;
var tgvEdge       = typeof job.tgv_edge       === "number" ? job.tgv_edge       : 0.001;
var tgvIterations = typeof job.tgv_iterations === "number" ? job.tgv_iterations : 100;

var runBXT        = job.bxt !== false;
var bxtPsf        = typeof job.bxt_psf        === "number" ? job.bxt_psf        : 4.0;
var bxtStars      = typeof job.bxt_stars      === "number" ? job.bxt_stars      : 0.50;
var bxtNonStel    = typeof job.bxt_nonstellar === "number" ? job.bxt_nonstellar : 0.30;
var bxtAutoPsf    = job.bxt_auto_psf !== false;   // default true — match GUI default
var bxtAdjHalos   = typeof job.bxt_adjust_halos === "number" ? job.bxt_adjust_halos : 0.0;  // float 0–1, not bool
var bxtCorrectOnly = job.bxt_correct_only === true;   // only fix star shapes, no deblur/sharpen

var runNXT        = job.nxt !== false;
var nxtDenoise    = typeof job.nxt_denoise     === "number" ? job.nxt_denoise     : 0.70;
var nxtIterations = typeof job.nxt_iterations  === "number" ? job.nxt_iterations  : 2;

// Stretch
var runMAS        = job.mas === true;  // MultiscaleAdaptiveStretch (PI native, self-calibrating)
var masNoiseThresh = typeof job.mas_noise_threshold === "number" ? job.mas_noise_threshold : -1;

var runHT         = job.ht === true;   // HistogramTransformation auto-stretch
var htClipLow     = typeof job.ht_clip_low    === "number" ? job.ht_clip_low    : 0.0;
var htTargetBg    = typeof job.ht_target_bg   === "number" ? job.ht_target_bg   : 0.12;

// Non-linear
var runStarXT        = job.starxt === true;
var starxtStarsPath  = job.starxt_stars_output || null;   // path to save stars-only FITS

var runSCNR       = job.scnr === true;
var scnrAmount    = typeof job.scnr_amount    === "number" ? job.scnr_amount    : 0.9;

var runHDRMT        = job.hdrmt === true;  // HDRMultiscaleTransform
var hdrmtLayers     = typeof job.hdrmt_layers      === "number" ? job.hdrmt_layers      : 6;
var hdrmtIterations = typeof job.hdrmt_iterations  === "number" ? job.hdrmt_iterations  : 3;
var hdrmtOverdrive  = typeof job.hdrmt_overdrive   === "number" ? job.hdrmt_overdrive   : 0.0;

var runLHE        = job.lhe === true;   // LocalHistogramEqualization
var lheAmount     = typeof job.lhe_amount      === "number" ? job.lhe_amount      : 0.5;
var lheKernelR    = typeof job.lhe_kernel_r    === "number" ? job.lhe_kernel_r    : 64;
var lheSlopeLimit = typeof job.lhe_slope_limit === "number" ? job.lhe_slope_limit : 2.0;

var runColorSat   = job.color_sat === true;   // ColorSaturation boost
var colorSatBoost = typeof job.color_sat_boost === "number" ? job.color_sat_boost : 0.3;

var runCurves     = job.curves === true;
var curvesShape   = job.curves_shape || "s_med";

var runUSM        = job.usm === true;   // UnsharpMask
var usmSigma      = typeof job.usm_sigma      === "number" ? job.usm_sigma      : 2.0;
var usmAmount     = typeof job.usm_amount     === "number" ? job.usm_amount     : 0.7;
var usmThreshold  = typeof job.usm_threshold  === "number" ? job.usm_threshold  : 0.02;

var runCMS        = job.cms === true;   // CorrectMagentaStars — fixes OSC debayer pink cores
var runMorph      = job.morph === true; // MorphologicalTransformation — shrink stars
var morphAmount   = typeof job.morph_amount      === "number" ? job.morph_amount      : 0.3;
var morphIter     = typeof job.morph_iterations  === "number" ? job.morph_iterations  : 2;

// Luminance mask params: {fn_name: {lower, upper, fuzziness, blur}}
// Computed by tool_params.compute_lum_masks() on the post-stretch image.
var lumMasks = (typeof job.lum_masks === "object" && job.lum_masks !== null)
               ? job.lum_masks : {};

logln("Input:  " + inputPath);
logln("Output: " + outputPath);
logln("DBE=" + runDBE + " GC=" + runGC + " CC=" + runCC + " BGN=" + runBGN +
      " SPCC=" + runSPCC);
logln("MLT=" + runMLT + " TGV=" + runTGV + " BXT=" + runBXT + " NXT=" + runNXT);
logln("HT=" + runHT + " StarXT=" + runStarXT + " SCNR=" + runSCNR +
      " NBN=" + runNBN + " CMS=" + runCMS + " Morph=" + runMorph);
logln("HDRMT=" + runHDRMT + " LHE=" + runLHE + " ColorSat=" + runColorSat +
      " Curves=" + runCurves + " USM=" + runUSM);
flushLog(); // ensure flags are visible even if script aborts mid-run

// ---------------------------------------------------------------------------
// Background anchor helpers
// Measures sky background from four corner regions and re-anchors it to the
// post-stretch target if it drifts after HDRMT / LHE.
// ---------------------------------------------------------------------------
var bgAnchorEnabled = job.bg_anchor === true;
var bgAnchorTarget  = typeof job.bg_anchor_target === "number" ? job.bg_anchor_target : null;
var bgAnchorTol     = 0.015;  // only correct if drift exceeds this

/** Sample background from four 64×64 corners; returns median of the four medians. */
function sampleBackground(view) {
    var img = view.image;
    var w   = img.width, h = img.height;
    var sz  = Math.min(64, Math.floor(Math.min(w, h) / 8));
    var corners = [
        img.median(new Rect(0,    0,    sz,   sz)),
        img.median(new Rect(w-sz, 0,    w,    sz)),
        img.median(new Rect(0,    h-sz, sz,   h)),
        img.median(new Rect(w-sz, h-sz, w,    h))
    ];
    corners.sort(function(a, b) { return a - b; });
    return (corners[1] + corners[2]) / 2.0;
}

/**
 * If background has drifted above bgAnchorTarget by more than bgAnchorTol,
 * apply a small shadow-clip correction to bring it back.
 * Negative drift (darker than target) is left alone — HDRMT darkening is intentional.
 */
function reanchorBackground(view, stepName) {
    if (!bgAnchorEnabled || bgAnchorTarget === null) return;
    try {
        var current = sampleBackground(view);
        var drift   = current - bgAnchorTarget;
        logln("BG anchor [" + stepName + "]: current=" + current.toFixed(4) +
              " target=" + bgAnchorTarget.toFixed(4) + " drift=" + drift.toFixed(4));
        if (Math.abs(drift) < bgAnchorTol || drift < 0) {
            logln("BG anchor [" + stepName + "]: within tolerance or negative drift — no correction.");
            return;
        }
        // Shift shadow clip by the drift amount to re-seat the background
        var c0 = Math.max(0, Math.min(0.3, drift));
        var ht = new HistogramTransformation();
        ht.H = [[c0, 0.5, 1.0, 0, 1],
                [c0, 0.5, 1.0, 0, 1],
                [c0, 0.5, 1.0, 0, 1],
                [0,  0.5, 1.0, 0, 1],
                [0,  0.5, 1.0, 0, 1]];
        ht.executeOn(view);
        var after = sampleBackground(view);
        logln("BG anchor [" + stepName + "]: corrected → " + after.toFixed(4));
        job["bg_anchor_after_" + stepName] = after;
    } catch(e) {
        logln("BG anchor ERROR [" + stepName + "]: " + e.message);
    }
}

// ---------------------------------------------------------------------------
// Luminance mask helpers
// ---------------------------------------------------------------------------

/**
 * Extract CIE L* luminance from sourceView into a new grayscale window,
 * then apply RangeSelection to produce a soft mask in [lower, upper].
 * Returns the mask ImageWindow, or null on failure.
 */
function createLuminanceMask(sourceView, lower, upper, fuzz, blurSigma) {
    // Wrapped entirely in try-catch: PixelMath createNewImage can throw in
    // headless/automation mode (PI 1.9.3 limitation).  Return null on any
    // failure — callers must handle null gracefully.
    try {
        var maskId = "__seestar_lum_mask__";
        // Remove any window left from a previous step
        try {
            var ew = ImageWindow.windowById(maskId);
            if (ew && !ew.isNull) ew.forceClose();
        } catch(e) {}

        // L($T) extracts perceptual luminance; Gray color space → 1-channel output
        var pm = new PixelMath();
        pm.expression           = "L($T)";
        pm.useSingleExpression  = true;
        pm.createNewImage       = true;
        pm.newImageId           = maskId;
        pm.newImageWidth        = 0;   // same as source
        pm.newImageHeight       = 0;
        pm.newImageColorSpace   = PixelMath.Gray;
        pm.newImageSampleFormat = PixelMath.SameAsTarget;
        if (!pm.executeOn(sourceView)) {
            logln("LumMask: PixelMath L() failed");
            return null;
        }

        var maskWin = ImageWindow.windowById(maskId);
        if (!maskWin || maskWin.isNull) {
            logln("LumMask: window not found after PixelMath");
            return null;
        }

        // Shape the mask with a soft range selection
        var rs = new RangeSelection();
        rs.lower      = lower;
        rs.upper      = upper;
        rs.fuzziness  = fuzz;
        rs.smoothness = blurSigma;
        if (!rs.executeOn(maskWin.mainView)) {
            logln("LumMask: RangeSelection failed (using raw L channel)");
        }

        return maskWin;
    } catch(e) {
        logln("LumMask: createLuminanceMask threw: " + e.message + " — proceeding without mask");
        return null;
    }
}

function applyLumMask(view, maskWin) {
    if (!maskWin || maskWin.isNull) return;
    view.window.mask        = maskWin;
    view.window.maskEnabled = true;
    view.window.maskInverted = false;
}

function clearLumMask(view, maskWin) {
    view.window.maskEnabled = false;
    if (maskWin && !maskWin.isNull) {
        try { maskWin.forceClose(); } catch(e) {}
    }
}

if (!File.exists(inputPath)) {
    logln("ERROR: input file not found: " + inputPath);
    flushLog();
    throw new Error("Input not found");
}

// ---------------------------------------------------------------------------
// Open image
// ---------------------------------------------------------------------------
var windows = ImageWindow.open(inputPath);
if (!windows || windows.length === 0) {
    logln("ERROR: could not open " + inputPath);
    flushLog();
    throw new Error("Open failed");
}
for (var k = 1; k < windows.length; k++) windows[k].forceClose();
var win  = windows[0];
var view = win.mainView;
logln("Opened: " + view.image.width + "x" + view.image.height +
      " channels=" + view.image.numberOfChannels +
      " sampleType=" + view.image.sampleType);

// ---------------------------------------------------------------------------
// Ensure Float32 — Seestar FITS is BITPIX=16 (integer). Most PI processes
// require floating-point samples. Use SampleFormatConversion (not PixelMath)
// to actually change the in-memory sample type, then rescale to [0,1].
// PI isFloatSample=false means integer; after conversion it becomes true.
// ---------------------------------------------------------------------------
if (!view.image.isFloatSample) {
    logln("Converting integer image to Float32 (isFloatSample=" +
          view.image.isFloatSample + " bitsPerSample=" + view.image.bitsPerSample + ")...");
    try {
        var sfc = new SampleFormatConversion();
        // ToFloat = IEEE 32-bit float. To32Bit is 32-bit UNSIGNED INTEGER — using it
        // here made PI save full-range uint32 FITS (0..4.29e9), which downstream
        // Python read un-normalized (the 1.7.x NBN saturation-revert bug).
        sfc.format = SampleFormatConversion.prototype.ToFloat;
        var sfcOk = sfc.executeOn(view);
        logln("SampleFormatConversion to Float32: " + (sfcOk ? "OK" : "FAILED") +
              " isFloat now=" + view.image.isFloatSample);
    } catch(e) {
        logln("SampleFormatConversion ERROR: " + e.message);
        // Fallback: PixelMath rescale (changes values to [0,1] even if type stays integer)
        try {
            var pm = new PixelMath();
            pm.expression = "$T";
            pm.useSingleExpression = true;
            pm.createNewImage = false;
            pm.rescale = true;
            pm.rescaleLow = 0;
            pm.rescaleHigh = 1;
            pm.executeOn(view);
            logln("PixelMath rescale fallback applied");
        } catch(e2) {
            logln("Rescale fallback ERROR: " + e2.message);
        }
    }
}

// ---------------------------------------------------------------------------
// DynamicBackgroundExtraction (DBE)
// ---------------------------------------------------------------------------
if (runDBE) {
    logln("Running DynamicBackgroundExtraction (correction=" + dbeCorrection + ")...");
    try {
        var dbe = new DynamicBackgroundExtraction();
        dbe.correction = (dbeCorrection === "division")
            ? DynamicBackgroundExtraction.prototype.Division
            : DynamicBackgroundExtraction.prototype.Subtraction;
        dbe.smoothing = 5.0;
        dbe.useRollingPenaltyTerm = true;
        var ok = dbe.executeOn(view);
        logln("DBE: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.dbe_failed = true;
    } catch(e) {
        logln("DBE ERROR: " + e.message);
        job.dbe_failed = true;
    }
}

// ---------------------------------------------------------------------------
// GradientCorrection (only when DBE is not running)
// ---------------------------------------------------------------------------
if (runGC) {
    logln("Running GradientCorrection...");
    try {
        var gc = new GradientCorrection();
        var ok = gc.executeOn(view);
        logln("GradientCorrection: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.gc_failed = true;
    } catch(e) {
        logln("GradientCorrection ERROR: " + e.message);
        job.gc_failed = true;
    }
}

// ---------------------------------------------------------------------------
// ColorCalibration
// ---------------------------------------------------------------------------
if (runCC) {
    logln("Running ColorCalibration...");
    try {
        var cc = new ColorCalibration();
        var ok = cc.executeOn(view);
        logln("ColorCalibration: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.cc_failed = true;
    } catch(e) {
        logln("ColorCalibration ERROR: " + e.message);
        job.cc_failed = true;
    }
}

// ---------------------------------------------------------------------------
// BackgroundNeutralization
// ---------------------------------------------------------------------------
if (runBGN) {
    logln("Running BackgroundNeutralization...");
    try {
        var bgn = new BackgroundNeutralization();
        var ok = bgn.executeOn(view);
        logln("BackgroundNeutralization: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.bgn_failed = true;
    } catch(e) {
        logln("BackgroundNeutralization ERROR: " + e.message);
        job.bgn_failed = true;
    }
}

// ---------------------------------------------------------------------------
// NOTE: GAIA DR3 SP catalog paths are configured via PI settings XML
// (~/.PixInsight/core-001-pxi.settings) on each machine at setup time.
// The gaia_db_path worker setting was an attempt to configure this via
// Settings.write() but that API only writes process-level settings, not
// the application-level catalog preferences that SPCC reads.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Plate-solve helper for SPCC.
//
// SPCC.executeOn() requires a valid astrometric solution attached to the view.
// The crop step earlier in the pipeline strips the WCS solution written at stack
// time (only RA/DEC/FOCALLEN/XPIXSZ pointing hints survive), so SPCC was silently
// failing in-pipeline despite working standalone on a solved file. Re-solve here,
// in-memory, immediately before SPCC. The ImageSolver attaches the solution to the
// window on success (SaveKeywords + regenerateAstrometricSolution), which is exactly
// what SPCC reads. Mirrors pi_solve.js's headless invocation + include guards.
// ---------------------------------------------------------------------------
#define USE_SOLVER_LIBRARY true
#define TITLE             "ImageSolver"
#define SETTINGS_MODULE   "SOLVER"
#define STAR_CSV_FILE     (File.systemTempDirectory + "/pi_postproc_solve_stars.csv")
// Guard the UI-only AdP scripts whose Dialog/Control prototype assignments crash
// PI in headless/offscreen mode (same set pi_solve.js suppresses).
#define __ADP_SEARCHCOORDINATES_jsh
#define __ADP_CATALOGDOWNLOADER_js
#define __ADP_ASTRONOMICALCATALOGS_jsh
#define __ADP_IMAGESOLVER_js
#include <pjsr/DataType.jsh>
#include "/opt/PixInsight/src/scripts/AdP/WCSmetadata.jsh"
#include "__REPO_ROOT__/nas_server/astro_catalogs_headless.jsh"
#include "/opt/PixInsight/src/scripts/AdP/SearchCoordinatesDialog.js"
#include "/opt/PixInsight/src/scripts/AdP/CatalogDownloader.js"
#include "__REPO_ROOT__/nas_server/imageSolver_headless.js"

function imageHasWCS(window) {
    var kws = window.keywords;
    for (var i = 0; i < kws.length; i++)
        if (kws[i].name === "CTYPE1") return true;
    return false;
}

/**
 * Manual XISF->FITS masters (e.g. IC 1805) often carry no FOCALLEN/XPIXSZ, so
 * ImageSolver.Init() gets no scale seed and the solve fails the "initial resolution
 * within a factor of 2 of the solution" guard. Inject SeeStar S50 defaults
 * (250 mm focal, 2.9 um IMX462 pixel -> ~2.39 arcsec/px) when those keywords are
 * absent. Both native (~2.9 arcsec/px) and 2x-drizzled (~1.45 arcsec/px) S50 scales
 * fall within the factor-2 window of this seed, so it never derails a real header.
 */
/**
 * Stale-WCS sanitizer (workflow 1.24.0). A mutilated header — CTYPE/CRVAL/CRPIX
 * survive a rewrite but the CD matrix is lost, leaving default CDELT=1 deg/px —
 * poisons ImageSolver's scale seed (3600"/px → factor-2 guard fails → SPCC falls
 * back). Root cause of the 2026-07-16 batch failures (M 85/88/91/97/102/109).
 * Strip the junk scale keywords and convert CRVAL into plain RA/DEC hint keywords
 * so the solver seeds position from them and scale from the S50 defaults.
 */
function sanitizeStaleWCS(window) {
    var kws = window.keywords;
    var cdelt = null, crval1 = null, crval2 = null, hasRA = false, hasDEC = false;
    for (var i = 0; i < kws.length; i++) {
        var n = kws[i].name;
        if (n === "CDELT1") cdelt = parseFloat(kws[i].value);
        else if (n === "CRVAL1") crval1 = parseFloat(kws[i].value);
        else if (n === "CRVAL2") crval2 = parseFloat(kws[i].value);
        else if (n === "RA" || n === "OBJCTRA") hasRA = true;
        else if (n === "DEC" || n === "OBJCTDEC") hasDEC = true;
    }
    // sane S50 scales are 1.19–2.9 "/px = 3e-4..8e-4 deg/px; anything ≥ 0.01 deg/px
    // (36"/px) is a stale default, not a real solution.
    if (cdelt === null || Math.abs(cdelt) < 0.01)
        return false;
    var drop = { "CDELT1":1, "CDELT2":1, "CROTA1":1, "CROTA2":1,
                 "CTYPE1":1, "CTYPE2":1, "CRPIX1":1, "CRPIX2":1,
                 "CRVAL1":1, "CRVAL2":1 };
    var kept = [];
    for (var j = 0; j < kws.length; j++)
        if (!drop[kws[j].name]) kept.push(kws[j]);
    if (!hasRA && crval1 !== null && !isNaN(crval1))
        kept.push(new FITSKeyword("RA", format("%.6f", crval1),
                                  "hint from stale CRVAL1 [sanitized]"));
    if (!hasDEC && crval2 !== null && !isNaN(crval2))
        kept.push(new FITSKeyword("DEC", format("%.6f", crval2),
                                  "hint from stale CRVAL2 [sanitized]"));
    window.keywords = kept;
    logln(format("Plate solve: stale WCS sanitized (CDELT=%.3f deg/px junk) — " +
                 "kept RA/DEC hint %.4f/%.4f", cdelt, crval1 || 0, crval2 || 0));
    return true;
}

function ensureS50ScaleHints(window) {
    var kws = window.keywords;
    var hasFocal = false, hasXpix = false, hasYpix = false;
    for (var i = 0; i < kws.length; i++) {
        var n = kws[i].name;
        if (n === "FOCALLEN") hasFocal = true;
        else if (n === "XPIXSZ") hasXpix = true;
        else if (n === "YPIXSZ") hasYpix = true;
    }
    if (hasFocal && hasXpix && hasYpix) return false;
    if (!hasFocal) kws.push(new FITSKeyword("FOCALLEN", "250", "SeeStar S50 focal length (mm) [injected]"));
    if (!hasXpix)  kws.push(new FITSKeyword("XPIXSZ", "2.9", "SeeStar S50 IMX462 pixel (um) [injected]"));
    if (!hasYpix)  kws.push(new FITSKeyword("YPIXSZ", "2.9", "SeeStar S50 IMX462 pixel (um) [injected]"));
    window.keywords = kws;
    logln("Plate solve: injected S50 scale hints (FOCALLEN=250mm, XPIXSZ/YPIXSZ=2.9um) — " +
          "manual master lacked them.");
    return true;
}

/**
 * Plate-solve `window` in-memory so SPCC has a WCS. Returns true on success
 * (or if a solution is already present). On failure SPCC will run unsolved and
 * set spcc_failed, which is the existing fallback path.
 */
function plateSolveForSPCC(window) {
    if (imageHasWCS(window)) {
        logln("Plate solve: WCS already present — skipping re-solve.");
        return true;
    }
    logln("Plate solve: no WCS on image (crop strips it) — solving before SPCC...");
    flushLog();
    try {
        var solver = new ImageSolver();
        solver.solverCfg.useActive            = true;
        solver.solverCfg.catalogMode          = CatalogMode.prototype.Automatic;
        solver.solverCfg.autoMagnitude        = true;
        solver.solverCfg.distortionCorrection = true;
        solver.solverCfg.sensitivity          = 0.5;
        solver.solverCfg.peakResponse         = 0.5;
        solver.solverCfg.maxStarDistortion    = 0.6;
        solver.solverCfg.showStars            = false;
        solver.solverCfg.showStarMatches      = false;
        solver.solverCfg.showSimplifiedSurfaces = false;
        solver.solverCfg.showDistortion       = false;

        // Strip mutilated-WCS junk (CDELT=1 deg/px) BEFORE Init seeds from it.
        sanitizeStaleWCS(window);
        // Manual masters may lack scale keywords — seed S50 defaults before Init reads them.
        ensureS50ScaleHints(window);
        // Extract RA/DEC/FOCALLEN/XPIXSZ hints from the FITS header.
        solver.Init(window, false);
        logln(format("Plate solve hints: focal=%.1fmm pixel=%.2fum res-hint=%.3f arcsec/px",
            solver.metadata.focal || 0, solver.metadata.xpixsz || 0,
            solver.metadata.resolution ? solver.metadata.resolution * 3600 : 0));
        // Stale-WCS guard (workflow 1.24.0): a mutilated header (CTYPE/CRVAL survive
        // but the CD matrix is gone, leaving CDELT=1 deg/px) makes Init trust a
        // 3600"/px resolution seed, which fails the solver's factor-2 guard — root
        // cause of the 2026-07-16 batch SPCC failures (M 85/88/91/97/102/109). If
        // the seed is outside any sane S50 scale, reseed the S50 defaults and keep
        // CRVAL as the position hint.
        var resArc = solver.metadata.resolution ? solver.metadata.resolution * 3600 : 0;
        if (resArc <= 0.3 || resArc > 30) {
            var S50_RES = 206.265 * 2.9 / 250;   // 2.39 arcsec/px native
            logln(format("Plate solve: insane res-hint %.1f\"/px from stale header — " +
                         "reseeding S50 scale (%.2f\"/px)", resArc, S50_RES));
            solver.metadata.focal = 250;
            solver.metadata.xpixsz = 2.9;
            solver.metadata.resolution = S50_RES / 3600;
            var kws2 = window.keywords;
            for (var ki = 0; ki < kws2.length; ki++) {
                var kn = kws2[ki].name;
                if (kn === "CRVAL1" && !(solver.metadata.ra > 0))
                    solver.metadata.ra = parseFloat(kws2[ki].value);
                if (kn === "CRVAL2" && !(solver.metadata.dec || solver.metadata.dec === 0))
                    solver.metadata.dec = parseFloat(kws2[ki].value);
            }
            logln(format("Plate solve: position hint RA=%.4f DEC=%.4f",
                solver.metadata.ra || 0, solver.metadata.dec || 0));
        }
        flushLog();

        var ok = false;
        try {
            ok = solver.SolveImage(window);
        } catch(se) {
            logln("Plate solve SolveImage threw: " + se.message);
        }
        if (ok) {
            logln(format("Plate solve OK: RA=%.5f Dec=%.5f res=%.3f arcsec/px",
                solver.metadata.ra, solver.metadata.dec,
                solver.metadata.resolution ? solver.metadata.resolution * 3600 : 0));
        } else {
            logln("Plate solve FAILED — SPCC will run unsolved and fall back.");
        }
        flushLog();
        return ok;
    } catch(e) {
        logln("Plate solve ERROR: " + e.message);
        flushLog();
        return false;
    }
}

// ---------------------------------------------------------------------------
// SpectrophotometricColorCalibration (SPCC) — requires plate-solved image
// SeeStar S50: Sony IMX462 color sensor, built-in UV/IR cut, 250mm f/5, 2.39"/px
// ---------------------------------------------------------------------------
if (runSPCC) {
    logln("Running SPCC (SeeStar S50, lp_filter=" + spccLpFilter + ")...");
    flushLog();
    // Crop strips the stack-time WCS; SPCC needs it. Re-solve in-memory first.
    plateSolveForSPCC(win);
    try {
        var spcc = new SpectrophotometricColorCalibration();

        // Sony Color Sensor Bayer + UV/IR cut -- correct defaults for SeeStar S50.
        // These match the SPCC defaults; set explicitly for safety.
        spcc.redFilterName   = "Sony Color Sensor R-UVIRcut";
        spcc.greenFilterName = "Sony Color Sensor G-UVIRcut";
        spcc.blueFilterName  = "Sony Color Sensor B-UVIRcut";

        if (spccLpFilter) {
            // SeeStar built-in "LP" is a dual-band (dual-narrowband) filter:
            // Ha ~656nm/20nm (lands in the red channel) and OIII ~500nm/30nm
            // (lands in green + blue). SPCC must run in narrowband mode with
            // these passbands, or it tries to fit a broadband Sony curve to
            // dual-band data and skews the colour balance.
            spcc.narrowbandMode      = true;
            spcc.redFilterWavelength = 656.3;
            spcc.redFilterBandwidth  = 20.0;
            spcc.greenFilterWavelength = 500.7;
            spcc.greenFilterBandwidth  = 30.0;
            spcc.blueFilterWavelength  = 500.7;
            spcc.blueFilterBandwidth   = 30.0;
            logln("SPCC: LP dual-band mode -- Ha 656.3/20 (R), OIII 500.7/30 (G,B)");
        } else {
            // Broadband OSC mode (UV/IR-cut)
            spcc.narrowbandMode = false;
        }

        spcc.applyCalibration = true;

        // Catalog: Gaia DR3 SP (requires local database installed via PI Gaia preferences)
        spcc.catalogId          = "GaiaDR3SP";
        spcc.autoLimitMagnitude = true;
        spcc.targetSourceCount  = 8000;

        // Background neutralization
        spcc.neutralizeBackground = true;

        // Suppress graph/map output -- not safe in headless mode
        spcc.generateGraphs    = false;
        spcc.generateStarMaps  = false;
        spcc.generateTextFiles = false;

        var ok = spcc.executeOn(view);
        logln("SPCC: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.spcc_failed = true;
    } catch(e) {
        logln("SPCC ERROR: " + e.message);
        job.spcc_failed = true;
    }
    flushLog();
}

// ---------------------------------------------------------------------------
// MultiscaleLinearTransform — wavelet sharpening + noise reduction (linear)
// ---------------------------------------------------------------------------
if (runMLT) {
    logln("Running MultiscaleLinearTransform (sharpen=" + mltSharpen +
          " denoise=" + mltDenoise + " layers=" + mltLayers + ")...");
    try {
        var mlt = new MultiscaleLinearTransform();
        // Layer format: [enabled, biasEnabled, bias, nrEnabled, nrThreshold, nrAmount, nrIterations]
        // Fine layers (0-1) get sharpening bias; coarser layers (2+) get noise reduction
        mlt.layers = [];
        for (var li = 0; li < mltLayers; li++) {
            var bias  = (li < 2) ? mltSharpen : 0.0;
            var doNR  = (li >= 1);
            var nrAmt = doNR ? mltDenoise : 0.0;
            mlt.layers.push([true, bias > 0, bias, doNR, 3.0, nrAmt, 1]);
        }
        mlt.scalingFunction = MultiscaleLinearTransform.prototype.B3Spline5x5;
        mlt.deringing = mltSharpen > 0;
        mlt.deringingDark = 0.01;
        var ok = mlt.executeOn(view);
        logln("MLT: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.mlt_failed = true;
    } catch(e) {
        logln("MLT ERROR: " + e.message);
        job.mlt_failed = true;
    }
}

// ---------------------------------------------------------------------------
// TGVDenoise — PI native, CPU-based, no GPU required
// ---------------------------------------------------------------------------
if (runTGV) {
    logln("Running TGVDenoise (strength=" + tgvStrength + " edge=" + tgvEdge +
          " iter=" + tgvIterations + ")...");
    try {
        var tgv = new TGVDenoise();
        tgv.rgbkMode = false;
        tgv.filterEnabledL = true;
        tgv.strengthL = tgvStrength;
        tgv.edgeProtectionL = tgvEdge;
        tgv.smoothnessL = 2.0;
        tgv.maxIterationsL = tgvIterations;
        tgv.convergenceEnabledL = false;
        tgv.filterEnabledC = true;
        tgv.strengthC = tgvStrength * 2.0;   // chrominance benefits from stronger reduction
        tgv.edgeProtectionC = tgvEdge * 2.0;
        tgv.smoothnessC = 6.0;
        tgv.maxIterationsC = tgvIterations;
        tgv.convergenceEnabledC = false;
        tgv.supportEnabled = false;
        var ok = tgv.executeOn(view);
        logln("TGVDenoise: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.tgv_failed = true;
    } catch(e) {
        logln("TGVDenoise ERROR: " + e.message);
        job.tgv_failed = true;
    }
}

// ---------------------------------------------------------------------------
// BlurXTerminator — AI deconvolution/sharpening
// ---------------------------------------------------------------------------
if (runBXT) {
    logln("Running BlurXTerminator (psf=" + (bxtAutoPsf ? "auto" : bxtPsf) +
          " nonstellar=" + bxtNonStel + " stars=" + bxtStars +
          (bxtCorrectOnly ? " CORRECT-ONLY" : "") + ")...");
    try {
        var bxt = new BlurXTerminator();
        // Do NOT set ai_file — let BXT find its installed model
        bxt.correct_only        = bxtCorrectOnly;
        bxt.auto_nonstellar_psf = bxtAutoPsf;
        if (!bxtAutoPsf) {
            bxt.nonstellar_psf_diameter = bxtPsf;
        }
        bxt.sharpen_stars       = bxtCorrectOnly ? 0.0 : bxtStars;
        bxt.sharpen_nonstellar  = bxtCorrectOnly ? 0.0 : bxtNonStel;
        bxt.lum_only            = false;
        bxt.adjust_halos        = bxtAdjHalos;
        logln("BXT state: correct_only=" + bxt.correct_only +
              " auto_psf=" + bxt.auto_nonstellar_psf +
              " stars=" + bxt.sharpen_stars +
              " nonstellar=" + bxt.sharpen_nonstellar);
        var ok = bxt.executeOn(view);
        logln("BXT: " + (ok ? "OK" : "FAILED"));
        if (!ok) {
            job.bxt_failed = true;
            // Attempt to log internal error state
            try { logln("BXT ai_file: " + bxt.ai_file); } catch(ei) {}
        }
    } catch(e) {
        logln("BXT ERROR: " + e.message);
        job.bxt_failed = true;
    }
}

// ---------------------------------------------------------------------------
// NoiseXTerminator — AI noise reduction
// ---------------------------------------------------------------------------
if (runNXT) {
    logln("Running NoiseXTerminator (denoise=" + nxtDenoise + " iterations=" + nxtIterations + ")...");
    try {
        var nxt = new NoiseXTerminator();
        nxt.denoise    = nxtDenoise;
        nxt.iterations = nxtIterations;
        logln("NXT state: denoise=" + nxt.denoise + " iterations=" + nxt.iterations);
        var ok = nxt.executeOn(view);
        logln("NXT: " + (ok ? "OK" : "FAILED"));
        if (!ok) {
            job.nxt_failed = true;
            // Try with lower denoise in case the value is out of range for this model
            logln("NXT: retrying with denoise=0.5 iterations=1...");
            try {
                nxt.denoise    = 0.5;
                nxt.iterations = 1;
                ok = nxt.executeOn(view);
                logln("NXT retry: " + (ok ? "OK" : "FAILED"));
                if (ok) job.nxt_failed = false;
            } catch(er) { logln("NXT retry ERROR: " + er.message); }
        }
    } catch(e) {
        logln("NXT ERROR: " + e.message);
        job.nxt_failed = true;
    }
}

// ---------------------------------------------------------------------------
// MultiscaleAdaptiveStretch — PI native, self-calibrating to noise floor
// No MGC prerequisite needed when background extraction is already done.
// ---------------------------------------------------------------------------
if (runMAS) {
    logln("Running MultiscaleAdaptiveStretch...");
    flushLog();
    try {
        var mas = new MultiscaleAdaptiveStretch();
        // Log what properties are available so we can tune in future runs
        logln("MAS defaults: " +
              "clippingFraction=" + (typeof mas.clippingFraction !== "undefined" ? mas.clippingFraction : "N/A") +
              " scalingFactor=" +   (typeof mas.scalingFactor    !== "undefined" ? mas.scalingFactor    : "N/A") +
              " noiseThreshold=" +  (typeof mas.noiseThreshold   !== "undefined" ? mas.noiseThreshold   : "N/A"));
        // Apply noise threshold override if provided
        if (masNoiseThresh >= 0) {
            if (typeof mas.noiseThreshold !== "undefined") {
                mas.noiseThreshold = masNoiseThresh;
                logln("MAS noiseThreshold overridden to " + masNoiseThresh);
            } else if (typeof mas.clippingFraction !== "undefined") {
                mas.clippingFraction = masNoiseThresh;
                logln("MAS clippingFraction overridden to " + masNoiseThresh);
            }
        }
        var ok = mas.executeOn(view);
        logln("MAS: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.mas_failed = true;
    } catch(e) {
        logln("MAS ERROR: " + e.message);
        job.mas_failed = true;
    }
    flushLog();
}

// ---------------------------------------------------------------------------
// HistogramTransformation — auto-stretch (midtone transfer function)
// ---------------------------------------------------------------------------
if (runHT) {
    logln("Running HistogramTransformation (auto-stretch target_bg=" + htTargetBg + ")...");
    try {
        // Compute median and MAD-based midtone for each channel then apply
        var img    = view.image;
        var median = img.median();
        var mad    = img.MAD();
        // MTF midtone value from median and mad
        var c0 = Math.max(0, Math.min(1, median - htClipLow));
        var m  = 0.0;
        if (mad > 0) {
            var normalized = (median - htClipLow) / mad;
            m = Math.max(0, Math.min(1, htTargetBg / (2 * htTargetBg - 1) *
                (htTargetBg - 1) / (normalized * (2 * htTargetBg - 1) - 1)));
        } else {
            m = htTargetBg;
        }
        logln("HT computed: c0=" + c0.toFixed(4) + " m=" + m.toFixed(4));
        var ht = new HistogramTransformation();
        // H format: [c0, m, c1, r0, r1] per channel — R, G, B, L, A
        // c0=shadow clip, m=midtone transfer, c1=highlight clip, r0/r1=output range
        ht.H = [[c0, m, 1.0, 0, 1],
                [c0, m, 1.0, 0, 1],
                [c0, m, 1.0, 0, 1],
                [0,  0.5, 1.0, 0, 1],
                [0,  0.5, 1.0, 0, 1]];
        var ok = ht.executeOn(view);
        logln("HT: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.ht_failed = true;
    } catch(e) {
        logln("HT ERROR: " + e.message);
        job.ht_failed = true;
    }
    // Record post-stretch background as anchor target (if not explicitly provided)
    if (bgAnchorEnabled && bgAnchorTarget === null) {
        bgAnchorTarget = sampleBackground(view);
        logln("BG anchor target set post-stretch: " + bgAnchorTarget.toFixed(4));
        job.bg_anchor_measured_target = bgAnchorTarget;
    }
}

// ---------------------------------------------------------------------------
// StarXTerminator — AI star removal
// When starxtStarsPath is set, stars=true generates a stars-only window that
// StarXT creates automatically; we find it by diffing the window list and save it.
// ---------------------------------------------------------------------------
if (runStarXT) {
    logln("Running StarXTerminator (stars=" + (starxtStarsPath ? "true" : "false") + ")...");
    flushLog();
    try {
        // Snapshot open window IDs before StarXT so we can find the new stars window
        var winsBefore = ImageWindow.windows;
        var idsBefore = [];
        for (var wi = 0; wi < winsBefore.length; wi++) {
            idsBefore.push(winsBefore[wi].mainView.id);
        }

        var starxt = new StarXTerminator();
        starxt.stars   = (starxtStarsPath !== null);   // generate stars window when path provided
        starxt.unscreen = true;
        var ok = starxt.executeOn(view);
        logln("StarXTerminator: " + (ok ? "OK" : "FAILED"));

        if (ok && starxtStarsPath) {
            // Find the new stars window StarXT created
            var winsAfter = ImageWindow.windows;
            var starsWin = null;
            for (var wa = 0; wa < winsAfter.length; wa++) {
                var wid = winsAfter[wa].mainView.id;
                var seen = false;
                for (var wb = 0; wb < idsBefore.length; wb++) {
                    if (idsBefore[wb] === wid) { seen = true; break; }
                }
                if (!seen) { starsWin = winsAfter[wa]; break; }
            }
            if (starsWin) {
                starsWin.saveAs(starxtStarsPath, false, false, false, false);
                var starsSaved = File.exists(starxtStarsPath);
                logln("StarXT stars saved: " + (starsSaved ? "OK" : "FAILED") + " -> " + starxtStarsPath);
                job.starxt_stars_saved = starsSaved;
                starsWin.forceClose();
            } else {
                logln("WARNING: StarXT stars window not found -- stars not saved");
                job.starxt_stars_saved = false;
            }
        }

        if (!ok) job.starxt_failed = true;
    } catch(e) {
        logln("StarXTerminator ERROR: " + e.message);
        job.starxt_failed = true;
    }
    flushLog();
}

// ---------------------------------------------------------------------------
// SCNR — green cast removal (Seestar teal artifact)
// ---------------------------------------------------------------------------
if (runSCNR) {
    logln("Running SCNR (amount=" + scnrAmount + ")...");
    try {
        var scnr = new SCNR();
        scnr.amount = scnrAmount;
        scnr.protectionMethod = SCNR.prototype.AverageNeutral;
        scnr.colorToRemove    = SCNR.prototype.Green;
        scnr.preserveLightness = true;
        var ok = scnr.executeOn(view);
        logln("SCNR: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.scnr_failed = true;
    } catch(e) {
        logln("SCNR ERROR: " + e.message);
        job.scnr_failed = true;
    }
}

// ---------------------------------------------------------------------------
// NarrowbandNormalization — balance Ha/OIII on starless stretched image
// ---------------------------------------------------------------------------
if (runNBN) {
    logln("Running NarrowbandNormalization (HOO palette, o3Boost=" + nbnO3Boost + ")...");
    try {
        var nbn = new NarrowbandNormalization();
        // NOTE: `normalizationMode` does NOT exist in PI 1.9.3 — the old nbn_method
        // (MaximumStars/Equalize) was a silent no-op. The real vibrancy lever is o3Boost
        // on the HOO palette. See project_canonical_framing / nbn investigation 2026-06-08.
        nbn.palette = 0;            // HOO
        nbn.o3Boost = nbnO3Boost;   // OIII boost — pushes the teal-blue core
        var ok = nbn.executeOn(view);
        logln("NarrowbandNormalization: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.nbn_failed = true;
    } catch(e) {
        logln("NarrowbandNormalization ERROR: " + e.message);
        job.nbn_failed = true;
    }
    // Restore the HOO palette: NBN balanced the channels to near-neutral, so push a
    // strong per-hue saturation mapping Ha→red (0°/360°) and OIII→teal (~180°/0.5),
    // leaving green/blue stars alone. Without this the genuine Ha/OIII signal renders
    // as muted grey-tan (measured emission B/R 0.86–0.97 on M 42 / NGC 2244 NBN runs).
    if (!job.nbn_failed && nbnHooBoost > 0) {
        logln("Running HOO saturation restore (ColorSaturation boost=" + nbnHooBoost + ")...");
        try {
            var csHOO = new ColorSaturation();
            // HS: [x=hue(0=red,0.5=cyan,0.833=magenta), y=saturation_delta]
            csHOO.HS = [[0.0,   nbnHooBoost],
                        [0.080, nbnHooBoost * 0.70],
                        [0.250, 0.05],                 // yellow-green (stars) ~untouched
                        [0.420, nbnHooBoost * 0.45],
                        [0.500, nbnHooBoost * 0.85],   // OIII teal
                        [0.560, nbnHooBoost * 0.60],
                        [0.750, 0.06],                 // blue ~untouched
                        [0.830, nbnHooBoost * 0.55],   // Ha/OIII magenta blend zone
                        [1.0,   nbnHooBoost]];
            var okH = csHOO.executeOn(view);
            logln("HOO saturation restore: " + (okH ? "OK" : "FAILED"));
            if (!okH) job.nbn_hoo_failed = true;
        } catch(e) {
            logln("HOO saturation restore ERROR: " + e.message);
            job.nbn_hoo_failed = true;
        }
    }
}

// CorrectMagentaStars — not a PI built-in process; handled via CurvesTransformation
// targeting the magenta hue range when cms=true. This is a lightweight substitute
// that desaturates the 280-320° hue range (magenta) selectively.
if (runCMS) {
    logln("Running CorrectMagentaStars (via CurvesTransformation hue desaturate)...");
    try {
        var ctMag = new CurvesTransformation();
        // Desaturate magenta range (hue ~300°) in the saturation curve
        // HS hue positions are 0–1 = 0°–360°. Magenta ≈ 0.80–0.90.
        ctMag.S = [[0.0, 0.0], [0.75, 0.75], [0.82, 0.45], [0.88, 0.45], [0.95, 0.95], [1.0, 1.0]];
        var ok = ctMag.executeOn(view);
        logln("CMS (hue desaturate magenta): " + (ok ? "OK" : "FAILED"));
        if (!ok) job.cms_failed = true;
    } catch(e) {
        logln("CMS ERROR: " + e.message);
        job.cms_failed = true;
    }
}

// ---------------------------------------------------------------------------
// MorphologicalTransformation — erode star discs to shrink star sizes
// ---------------------------------------------------------------------------
if (runMorph) {
    logln("Running MorphologicalTransformation (amount=" + morphAmount +
          " iterations=" + morphIter + ")...");
    try {
        var morph = new MorphologicalTransformation();
        // Selection operator (blend between eroded and original) is gentler than pure Erosion
        morph.operator           = MorphologicalTransformation.prototype.Selection;
        morph.numberOfIterations = morphIter;
        morph.amount             = morphAmount;
        morph.selectionPoint     = 0.20;   // 0=pure erosion, 1=dilation; 0.2 shrinks well
        // 5x5 diamond structuring element (hex mask in structureWayTable)
        morph.structureSize      = 5;
        morph.structureWayTable  = [[[
            0x00,0x01,0x01,0x01,0x00,
            0x01,0x01,0x01,0x01,0x01,
            0x01,0x01,0x01,0x01,0x01,
            0x01,0x01,0x01,0x01,0x01,
            0x00,0x01,0x01,0x01,0x00
        ]]];
        var ok = morph.executeOn(view);
        logln("MorphologicalTransformation: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.morph_failed = true;
    } catch(e) {
        logln("MorphologicalTransformation ERROR: " + e.message);
        job.morph_failed = true;
    }
}

// ---------------------------------------------------------------------------
// HDRMultiscaleTransform — dynamic range compression for bright nebulae
// ---------------------------------------------------------------------------
if (runHDRMT) {
    var hdrmtMaskCfg = lumMasks.hdrmt || null;
    var hdrmtMaskWin = null;
    if (hdrmtMaskCfg) {
        try {
            hdrmtMaskWin = createLuminanceMask(view,
                hdrmtMaskCfg.lower, hdrmtMaskCfg.upper,
                hdrmtMaskCfg.fuzziness || 0.08, hdrmtMaskCfg.blur || 4);
            applyLumMask(view, hdrmtMaskWin);
            logln("HDRMT: lum mask lower=" + hdrmtMaskCfg.lower + " upper=" + hdrmtMaskCfg.upper);
        } catch(e) {
            logln("HDRMT: lum mask setup threw: " + e.message + " — proceeding without mask");
            hdrmtMaskWin = null;
        }
    }
    logln("Running HDRMultiscaleTransform (layers=" + hdrmtLayers +
          " iter=" + hdrmtIterations + " overdrive=" + hdrmtOverdrive + ")...");
    try {
        var hdrmt = new HDRMultiscaleTransform();
        hdrmt.numberOfLayers    = hdrmtLayers;
        hdrmt.numberOfIterations = hdrmtIterations;
        hdrmt.medianTransform   = true;
        hdrmt.overdrive         = hdrmtOverdrive;
        hdrmt.toLightness       = true;
        hdrmt.luminanceMask     = true;
        hdrmt.deringing         = false;
        var ok = hdrmt.executeOn(view);
        logln("HDRMT: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.hdrmt_failed = true;
    } catch(e) {
        logln("HDRMT ERROR: " + e.message);
        job.hdrmt_failed = true;
    }
    clearLumMask(view, hdrmtMaskWin);
    reanchorBackground(view, "hdrmt");
}

// ---------------------------------------------------------------------------
// LocalHistogramEqualization — local contrast enhancement
// ---------------------------------------------------------------------------
if (runLHE) {
    var lheMaskCfg = lumMasks.lhe || null;
    var lheMaskWin = null;
    if (lheMaskCfg) {
        try {
            lheMaskWin = createLuminanceMask(view,
                lheMaskCfg.lower, lheMaskCfg.upper,
                lheMaskCfg.fuzziness || 0.06, lheMaskCfg.blur || 4);
            applyLumMask(view, lheMaskWin);
            logln("LHE: lum mask lower=" + lheMaskCfg.lower + " upper=" + lheMaskCfg.upper);
        } catch(e) {
            logln("LHE: lum mask setup threw: " + e.message + " — proceeding without mask");
            lheMaskWin = null;
        }
    }
    logln("Running LocalHistogramEqualization (amount=" + lheAmount + " r=" + lheKernelR + ")...");
    try {
        var lhe = new LocalHistogramEqualization();
        lhe.radius      = lheKernelR;
        lhe.slopeLimit  = lheSlopeLimit;   // contrast limit (CLAHE clip threshold)
        lhe.amount      = lheAmount;
        lhe.circular    = true;
        var ok = lhe.executeOn(view);
        logln("LHE: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.lhe_failed = true;
    } catch(e) {
        logln("LHE ERROR: " + e.message);
        job.lhe_failed = true;
    }
    clearLumMask(view, lheMaskWin);
    reanchorBackground(view, "lhe");
}

// ---------------------------------------------------------------------------
// ColorSaturation — boost saturation (after SPCC/CC)
// ---------------------------------------------------------------------------
if (runColorSat) {
    var csatMaskCfg = lumMasks.color_sat || null;
    var csatMaskWin = null;
    if (csatMaskCfg) {
        try {
            csatMaskWin = createLuminanceMask(view,
                csatMaskCfg.lower, csatMaskCfg.upper,
                csatMaskCfg.fuzziness || 0.06, csatMaskCfg.blur || 4);
            applyLumMask(view, csatMaskWin);
            logln("ColorSat: lum mask lower=" + csatMaskCfg.lower + " upper=" + csatMaskCfg.upper);
        } catch(e) {
            logln("ColorSat: lum mask setup threw: " + e.message + " — proceeding without mask");
            csatMaskWin = null;
        }
    }
    logln("Running ColorSaturation (boost=" + colorSatBoost + " preset=" + satPreset + ")...");
    try {
        var csat = new ColorSaturation();
        // HS array: [x=hue_position(0-1), y=saturation_delta]
        // x: 0=red, 0.167=yellow, 0.333=green, 0.5=cyan, 0.583=blue, 0.667=blue, 0.750=violet, 0.833=magenta
        // Galaxy preset: boost blues (spiral arms), dampen yellows (prevents core going orange)
        var hsGalaxy  = [[0.0,  0.08], [0.110, -0.05], [0.220,  0.00], [0.420,  0.10],
                         [0.580, 0.30], [0.670,  0.35], [0.750,  0.25], [0.830,  0.12], [1.0,  0.08]];
        // Nebula preset: boost Ha-red (0°) and OIII-cyan (190°), boost magenta blend zone
        var hsNebula  = [[0.0,  0.35], [0.080,  0.25], [0.250,  0.05], [0.420,  0.05],
                         [0.500, 0.18], [0.530,  0.18], [0.750,  0.08], [0.830,  0.28], [1.0,  0.35]];
        // Uniform: flat boost at colorSatBoost across all hues
        var hsUniform = [[0.0, colorSatBoost], [0.166, colorSatBoost], [0.333, colorSatBoost],
                         [0.5, colorSatBoost], [0.666, colorSatBoost], [0.833, colorSatBoost],
                         [1.0, colorSatBoost]];
        csat.HS = (satPreset === "galaxy") ? hsGalaxy
                : (satPreset === "nebula") ? hsNebula
                : hsUniform;
        var ok = csat.executeOn(view);
        logln("ColorSaturation: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.color_sat_failed = true;
    } catch(e) {
        logln("ColorSaturation ERROR: " + e.message);
        job.color_sat_failed = true;
    }
    clearLumMask(view, csatMaskWin);
}

// ---------------------------------------------------------------------------
// CurvesTransformation — parametric tone curve
// ---------------------------------------------------------------------------
if (runCurves) {
    var curvesMaskCfg = lumMasks.curves_pi || lumMasks.curves || null;
    var curvesMaskWin = null;
    if (curvesMaskCfg) {
        try {
            curvesMaskWin = createLuminanceMask(view,
                curvesMaskCfg.lower, curvesMaskCfg.upper,
                curvesMaskCfg.fuzziness || 0.06, curvesMaskCfg.blur || 4);
            applyLumMask(view, curvesMaskWin);
            logln("Curves: lum mask lower=" + curvesMaskCfg.lower + " upper=" + curvesMaskCfg.upper);
        } catch(e) {
            logln("Curves: lum mask setup threw: " + e.message + " — proceeding without mask");
            curvesMaskWin = null;
        }
    }
    var curvesPointsArr = job.curves_points;  // data-driven control points from tool_params.py
    var usingCustomPts  = Array.isArray(curvesPointsArr) && curvesPointsArr.length >= 3;
    logln("Running CurvesTransformation (shape=" + curvesShape +
          (usingCustomPts ? ", custom_pts=" + curvesPointsArr.length : "") + ")...");
    try {
        var ct = new CurvesTransformation();
        if (usingCustomPts) {
            // Data-driven bespoke control points computed from actual pixel statistics
            ct.K = curvesPointsArr;
            logln("CurvesTransformation: using " + curvesPointsArr.length +
                  " data-driven control points (sky→" +
                  (curvesPointsArr[2] ? curvesPointsArr[2][1].toFixed(3) : "?") + ")");
        } else if (curvesShape === "s_mild") {
            ct.K = [[0,0],[0.25,0.22],[0.5,0.5],[0.75,0.78],[1,1]];
        } else if (curvesShape === "s_med") {
            ct.K = [[0,0],[0.25,0.20],[0.5,0.5],[0.75,0.80],[1,1]];
        } else if (curvesShape === "s_strong") {
            ct.K = [[0,0],[0.25,0.18],[0.5,0.5],[0.75,0.82],[1,1]];
        } else if (curvesShape === "rolloff_highlights") {
            ct.K = [[0,0],[0.5,0.52],[0.75,0.78],[0.90,0.92],[1,1]];
        } else if (curvesShape === "lift_shadows") {
            ct.K = [[0,0.02],[0.25,0.30],[0.5,0.52],[0.75,0.76],[1,1]];
        } else if (curvesShape === "globular_balanced") {
            // For globular clusters: slightly lift faint outer halo; roll off bright
            // core highlights to reduce blown-out core appearance.  Works without a
            // luminance mask (designed for the full pixel range).
            // Calibrated on C80 (Omega Centauri) 2026-05-24 — best for large, diffuse globulars
            // where the outer halo is very faint and needs the +0.01 shadow lift.
            // Dark background/halo (0-0.20): tiny lift (+0.01) → faint structure visible
            // Midtones (0.45): slight compression → richer midrange
            // Upper cluster (0.65–0.92): progressive rolloff → core detail preserved
            ct.K = [[0,0.01],[0.20,0.21],[0.45,0.44],[0.65,0.61],[0.80,0.74],[0.92,0.85],[1,0.94]];
        } else if (curvesShape === "globular_core_rolloff") {
            // For compact, bright-core globulars (M13, M3, M5, M92 etc.):
            // − Sky darkening: 0.15 sky → ~0.12 output (removes current +0.01 lift, adds −0.03 pull)
            // − Halo neutral zone 0.35–0.50: zero delta — halo gradient reads naturally
            // − Shoulder starts at 0.62, compresses hard through the top
            // − Cap 0.88 (vs 0.94 on globular_balanced) — core whites muted significantly
            //
            // Effect on M13 (measured 2026-05-29):
            //   sky  0.148 → 0.123 (−0.035)   background visibly darker
            //   p95  0.445 → 0.445 (±0.000)   halo structure untouched
            //   p99  0.910 → 0.803 (−0.036)   highlights pulled back
            //   p99.9 0.993→ 0.873 (−0.059)   near-clipping region at 0.87 vs 0.93
            ct.K = [[0,0],[0.10,0.07],[0.20,0.18],[0.35,0.35],[0.50,0.50],
                    [0.62,0.58],[0.75,0.68],[0.88,0.78],[0.96,0.84],[1,0.88]];
        } else {
            ct.K = [[0,0],[0.25,0.22],[0.5,0.5],[0.75,0.78],[1,1]];
        }
        // End of if/else chain for curves_points vs named shapes
        var ok = ct.executeOn(view);
        logln("CurvesTransformation: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.curves_failed = true;
    } catch(e) {
        logln("CurvesTransformation ERROR: " + e.message);
        job.curves_failed = true;
    }
    clearLumMask(view, curvesMaskWin);
}

// ---------------------------------------------------------------------------
// UnsharpMask — luminance sharpening (post-stretch, use sparingly)
// ---------------------------------------------------------------------------
if (runUSM) {
    var usmMaskCfg = lumMasks.usm || null;
    var usmMaskWin = null;
    if (usmMaskCfg) {
        try {
            usmMaskWin = createLuminanceMask(view,
                usmMaskCfg.lower, usmMaskCfg.upper,
                usmMaskCfg.fuzziness || 0.06, usmMaskCfg.blur || 4);
            applyLumMask(view, usmMaskWin);
            logln("USM: lum mask lower=" + usmMaskCfg.lower + " upper=" + usmMaskCfg.upper);
        } catch(e) {
            logln("USM: lum mask setup threw: " + e.message + " — proceeding without mask");
            usmMaskWin = null;
        }
    }
    logln("Running UnsharpMask (sigma=" + usmSigma + " amount=" + usmAmount +
          " threshold=" + usmThreshold + ")...");
    try {
        var usm = new UnsharpMask();
        usm.sigma        = usmSigma;
        usm.amount       = usmAmount;
        usm.threshold    = usmThreshold;
        usm.useLuminance = true;
        var ok = usm.executeOn(view);
        logln("USM: " + (ok ? "OK" : "FAILED"));
        if (!ok) job.usm_failed = true;
    } catch(e) {
        logln("USM ERROR: " + e.message);
        job.usm_failed = true;
    }
    clearLumMask(view, usmMaskWin);
}

// ---------------------------------------------------------------------------
// Save output
// ---------------------------------------------------------------------------
logln("Saving to: " + outputPath);
try {
    win.saveAs(outputPath, false, false, false, false);
    var saved = File.exists(outputPath);
    logln("Save " + (saved ? "OK" : "FAILED") + ": " + outputPath);
    job.output_exists = saved;
} catch(e) {
    logln("Save ERROR: " + e.message);
    job.output_exists = false;
}

win.forceClose();

// Write result back so Python can read per-step status
File.writeTextFile(jobPath, JSON.stringify(job, null, 2));

logln("pi_postprocess.js done");
flushLog();
