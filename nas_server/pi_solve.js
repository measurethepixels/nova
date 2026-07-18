/**
 * pi_solve.js — Plate-solve via PI ImageSolver + local GAIA DR3.
 *
 * No flip applied: Siril's output is already correctly oriented.
 *
 * Job JSON keys:
 *   input_fits    string  Path to FITS file (WCS written in-place on success)
 *   ra_hint       number  Optional RA hint in degrees
 *   dec_hint      number  Optional Dec hint in degrees
 *   search_radius number  Search radius in degrees (default 5)
 *
 * Result keys written back to job JSON:
 *   ok            bool    True if plate solve succeeded
 *   ra_solved     number  degrees
 *   dec_solved    number  degrees
 *   resolution_arcsec  number
 *   error         string  (on failure)
 *   log           string
 */

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------
var logLines = [];
var logPath  = "/tmp/pi_solve_last.log";

function logln(msg) {
    console.writeln(msg);
    logLines.push(msg);
}
function flushLog() {
    File.writeTextFile(logPath, logLines.join("\n") + "\n");
}

logln("pi_solve.js starting — PI " + CoreApplication.versionLongString);

// ---------------------------------------------------------------------------
// Job config
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

var inputPath    = job.input_fits    || null;
var raHint       = (job.ra_hint  !== undefined && job.ra_hint  !== null) ? Number(job.ra_hint)  : null;
var decHint      = (job.dec_hint !== undefined && job.dec_hint !== null) ? Number(job.dec_hint) : null;
var searchRadius = job.search_radius || 5.0;

if (!inputPath || !File.exists(inputPath)) {
    logln("FAIL: input_fits not found: " + inputPath);
    job.ok = false;
    job.error = "input not found: " + inputPath;
    job.log = logLines.join("\n");
    File.writeTextFile(jobPath, JSON.stringify(job));
    flushLog();
    throw new Error("No input file");
}

logln("Input: " + inputPath);
if (raHint  !== null) logln("RA hint:  " + raHint.toFixed(5)  + " deg");
if (decHint !== null) logln("Dec hint: " + decHint.toFixed(5) + " deg");

// ---------------------------------------------------------------------------
// Open image
// ---------------------------------------------------------------------------
var windows = ImageWindow.open(inputPath);
if (!windows || windows.length === 0) {
    logln("FAIL: cannot open FITS: " + inputPath);
    job.ok = false;
    job.error = "cannot open FITS";
    job.log = logLines.join("\n");
    File.writeTextFile(jobPath, JSON.stringify(job));
    flushLog();
    throw new Error("Cannot open FITS");
}
var imgWindow = windows[0];
logln(format("Opened: %s  [%d x %d x %d]",
    imgWindow.filePath,
    imgWindow.mainView.image.width,
    imgWindow.mainView.image.height,
    imgWindow.mainView.image.numberOfChannels));

// ---------------------------------------------------------------------------
// Plate solve via ImageSolver + GAIA DR3
// ---------------------------------------------------------------------------
#define USE_SOLVER_LIBRARY true
#define TITLE             "ImageSolver"
#define SETTINGS_MODULE   "SOLVER"
#define STAR_CSV_FILE     (File.systemTempDirectory + "/pi_solve_stars.csv")

// Pre-define include guards for UI-only scripts — their Dialog/Control prototype
// assignments crash PI in headless/offscreen mode.
#define __ADP_SEARCHCOORDINATES_jsh
#define __ADP_CATALOGDOWNLOADER_js

// astro_catalogs_headless.jsh: AstronomicalCatalogs.jsh minus PathEditControl,
// DirEditControl, CustomXEPHFilesControls, and VizierMirrorDialog (all crash headless).
#define __ADP_ASTRONOMICALCATALOGS_jsh  // prevent imageSolver_headless re-including original

// imageSolver_headless.js: ImageSolver.js minus ImageSolverDialog class (line 254-2184
// of the original). ImageSolverDialog.prototype = new Dialog crashes PI headless.
#define __ADP_IMAGESOLVER_js  // self-guard

// DataType.jsh must precede catalog includes — defines DataType_Double, DataType_UCString
// macros used in catalog constructor property-push calls at global scope.
#include <pjsr/DataType.jsh>
#include "/opt/PixInsight/src/scripts/AdP/WCSmetadata.jsh"
#include "__REPO_ROOT__/nas_server/astro_catalogs_headless.jsh"
#include "/opt/PixInsight/src/scripts/AdP/SearchCoordinatesDialog.js"
#include "/opt/PixInsight/src/scripts/AdP/CatalogDownloader.js"
#include "__REPO_ROOT__/nas_server/imageSolver_headless.js"

var solver = new ImageSolver();
solver.solverCfg.useActive            = true;
solver.solverCfg.catalogMode          = CatalogMode.prototype.Automatic;
solver.solverCfg.autoMagnitude        = true;
solver.solverCfg.maxIterations        = 100;
solver.solverCfg.distortionCorrection = true;
solver.solverCfg.sensitivity          = 0.5;
solver.solverCfg.peakResponse         = 0.5;
solver.solverCfg.maxStarDistortion    = 0.6;
solver.solverCfg.showStars            = false;
solver.solverCfg.showStarMatches      = false;
solver.solverCfg.showSimplifiedSurfaces = false;
solver.solverCfg.showDistortion       = false;

solver.Init(imgWindow, false);

if (raHint  !== null) solver.metadata.ra  = raHint;
if (decHint !== null) solver.metadata.dec = decHint;

logln(format("Focal: %.1f mm  Pixel: %.3f um", solver.metadata.focal, solver.metadata.xpixsz));
if (solver.metadata.resolution)
    logln(format("Resolution hint: %.3f arcsec/px", solver.metadata.resolution * 3600));

var solved = false;
try {
    solved = solver.SolveImage(imgWindow);
} catch(e) {
    logln("SolveImage threw: " + e.message);
}

if (solved) {
    var res_deg = solver.metadata.resolution; // degrees/px
    var rot     = (solver.metadata.rotation !== undefined) ? solver.metadata.rotation : 0;
    var w       = imgWindow.mainView.image.width;
    var h       = imgWindow.mainView.image.height;

    logln(format("Plate solve OK: RA=%.5f Dec=%.5f res=%.3f arcsec/px rot=%.2f deg",
        solver.metadata.ra, solver.metadata.dec,
        res_deg ? res_deg * 3600 : 0, rot));

    // Explicitly write standard FITS WCS keywords — saveAs alone doesn't persist them.
    // IMPORTANT: SolveImage → metadata.SaveKeywords() has already written signed CDELT1/CDELT2
    // values that encode the actual image orientation (south-up → CDELT2 < 0, mirrored east-right
    // → CDELT1 > 0).  Read those PI-computed signs FIRST, then re-write for FITS persistence.
    // Do NOT hardcode ±res_deg — that discards parity info and breaks orientation detection.
    var kws = imgWindow.keywords;
    function getKw(name, defaultVal) {
        for (var i = 0; i < kws.length; i++) {
            if (kws[i].name === name) return parseFloat(kws[i].value);
        }
        return defaultVal;
    }
    function setKw(name, value, comment) {
        for (var i = 0; i < kws.length; i++) {
            if (kws[i].name === name) { kws.splice(i, 1); break; }
        }
        kws.push(new FITSKeyword(name, value, comment));
    }
    // Read what PI's solver wrote; fall back to standard convention if absent
    var cdelt1_val = getKw("CDELT1", -res_deg);  // negative = east-left (standard)
    var cdelt2_val = getKw("CDELT2",  res_deg);  // positive = north-up; negative = south-up
    var crota2_val = getKw("CROTA2",  rot);       // 180° also indicates south-up
    logln(format("Orientation from PI: CDELT1=%.6f CDELT2=%.6f CROTA2=%.2f",
                 cdelt1_val, cdelt2_val, crota2_val));

    setKw("WCSAXES", "2",                            "Number of WCS axes");
    setKw("CTYPE1",  "'RA---TAN'",                   "WCS projection type for axis 1");
    setKw("CTYPE2",  "'DEC--TAN'",                   "WCS projection type for axis 2");
    setKw("CRVAL1",  solver.metadata.ra.toString(),  "RA at reference pixel (deg)");
    setKw("CRVAL2",  solver.metadata.dec.toString(), "Dec at reference pixel (deg)");
    setKw("CRPIX1",  (w / 2.0).toString(),           "Reference pixel axis 1");
    setKw("CRPIX2",  (h / 2.0).toString(),           "Reference pixel axis 2");
    setKw("CDELT1",  cdelt1_val.toString(),           "Degrees per pixel axis 1 (RA; sign = parity)");
    setKw("CDELT2",  cdelt2_val.toString(),           "Degrees per pixel axis 2 (Dec; neg = south-up)");
    setKw("CROTA2",  crota2_val.toString(),           "Image rotation (deg, CCW)");
    setKw("EQUINOX", "2000.0",                       "Equinox of coordinates");
    setKw("RADESYS", "'ICRS'",                       "Reference frame");
    imgWindow.keywords = kws;

    imgWindow.saveAs(inputPath, false, false, false, false);
    logln("Saved solved FITS with WCS: " + inputPath);
} else {
    logln("Plate solve FAILED");
}

imgWindow.forceClose();

// ---------------------------------------------------------------------------
// Write result
// ---------------------------------------------------------------------------
job.ok                = solved;
job.ra_solved         = solved ? solver.metadata.ra : null;
job.dec_solved        = solved ? solver.metadata.dec : null;
job.resolution_arcsec = (solved && solver.metadata.resolution) ? solver.metadata.resolution * 3600 : null;
job.south_up          = solved ? (cdelt2_val < 0 || Math.abs(crota2_val % 360 - 180) < 10) : null;
job.mirrored          = solved ? (cdelt1_val > 0) : null;
job.cdelt1            = solved ? cdelt1_val : null;
job.cdelt2            = solved ? cdelt2_val : null;
job.crota2            = solved ? crota2_val : null;
job.error             = solved ? null : "plate solve failed";
job.log               = logLines.join("\n");
File.writeTextFile(jobPath, JSON.stringify(job));
flushLog();

logln("pi_solve.js done — solved=" + solved);
