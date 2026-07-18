/**
 * pi_register_and_drizzle.js — PI Debayer + StarAlignment + ImageIntegration.
 *
 * Pipeline:
 *   1. Debayer: raw CFA .fit → debayered _d.xisf (in output_dir/debayered/)
 *   2. StarAlignment: _d.xisf → _d_r.xisf + _d_r.xdrz (in output_dir/)
 *   3. ImageIntegration: _d_r.xisf → integrated result XISF
 *   4. Explicit saveAs — result window saved to output_xisf path
 *
 * Note: DrizzleIntegration.executeGlobal() silently fails in PI 1.9.3
 * --automation-mode (returns false, produces 0 windows regardless of config).
 * ImageIntegration is confirmed to work headlessly and gives equivalent quality.
 *
 * Job JSON keys (input):
 *   input_files   string[]  Paths to raw CFA light frames (.fit/.fits)
 *   output_dir    string    Directory for registered _r.xisf + _r.xdrz files
 *   output_xisf   string    Full path for the result XISF
 *
 * Result keys written back:
 *   ok                 bool
 *   frames_registered  int   Count of .xdrz files produced by SA
 *   frames_used        int   Count of registered .xisf files fed to II
 *   output_xisf        string  Actual path of saved result (or null on failure)
 *   error              string  (on failure)
 */

// ---------------------------------------------------------------------------
// Headless crash guards — prevent Dialog/Control subclass includes from crashing PI.
// ---------------------------------------------------------------------------
#define __ADP_SEARCHCOORDINATES_jsh
#define __ADP_CATALOGDOWNLOADER_js
#define __ADP_ASTRONOMICALCATALOGS_jsh
#define __ADP_IMAGESOLVER_js

// DataType.jsh must be first include.
#include <pjsr/DataType.jsh>

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------
var logLines = [];
var logPath  = "/tmp/pi_register_drizzle_last.log";

function logln(msg) {
    var ts = (new Date()).toISOString().substr(11, 12);
    console.writeln("[" + ts + "] " + msg);
    logLines.push("[" + ts + "] " + msg);
}
function flushLog() {
    File.writeTextFile(logPath, logLines.join("\n") + "\n");
}

logln("pi_register_and_drizzle.js starting — PI " + CoreApplication.versionLongString);
flushLog();

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
var inputFiles  = job.input_files  || [];
var outputDir   = job.output_dir   || null;
var outputXisf  = job.output_xisf  || null;
// Optional external StarAlignment reference (synthetic Gaia canonical frame).
// When set, real subs register directly onto the canonical grid.
var referenceImage = job.reference_image || null;
// Drizzle toggle: when false, stop at the 1x ImageIntegration result (no 2x DI).
var doDrizzle = (job.drizzle !== false);

if (!inputFiles.length || !outputDir || !outputXisf) {
    logln("FAIL: input_files, output_dir, or output_xisf missing in job");
    job.ok = false; job.error = "missing required job keys";
    job.log = logLines.join("\n");
    File.writeTextFile(jobPath, JSON.stringify(job));
    flushLog();
    throw new Error("Bad job");
}

logln("Input frames: " + inputFiles.length);
logln("Output dir:   " + outputDir);
logln("Output XISF:  " + outputXisf);
flushLog();

// Ensure output directory exists
if (!File.directoryExists(outputDir)) {
    File.createDirectory(outputDir, true);
}

// ---------------------------------------------------------------------------
// Step 1: Debayer CFA frames → RGB XISF
// ---------------------------------------------------------------------------
var debayerDir = outputDir + "/debayered";
if (!File.directoryExists(debayerDir)) {
    File.createDirectory(debayerDir, true);
}

logln("Debayering " + inputFiles.length + " CFA frames (Auto pattern → RGB XISF)...");

var DB = new Debayer;
DB.cfaPattern             = Debayer.prototype.Auto;
DB.debayerMethod          = Debayer.prototype.VNG;
DB.outputDirectory        = debayerDir;
DB.outputExtension        = ".xisf";
DB.outputPostfix          = "_d";
DB.outputRGBImages        = true;
DB.outputSeparateChannels = false;
DB.evaluateNoise          = false;
DB.evaluateSignal         = false;
DB.overwriteExistingFiles = true;

var dbTargets = [];
for (var i = 0; i < inputFiles.length; i++) {
    dbTargets.push([true, inputFiles[i]]);
}
DB.targetItems = dbTargets;

var dbOk = false;
try {
    dbOk = DB.executeGlobal();
} catch(e) {
    logln("Debayer threw: " + e.message);
}
logln("Debayer done — ok=" + dbOk);
flushLog();

// Collect debayered _d.xisf files
var debayeredFiles = [];
try {
    var ff0 = new FileFind;
    if (ff0.begin(debayerDir + "/*_d.xisf")) {
        do {
            if (ff0.isFile) debayeredFiles.push(debayerDir + "/" + ff0.name);
        } while (ff0.next());
    }
} catch(e) {
    logln("WARNING: could not list debayered files: " + e.message);
}
logln("Debayered files found: " + debayeredFiles.length);
flushLog();

if (debayeredFiles.length === 0) {
    logln("FAIL: Debayer produced no _d.xisf files");
    job.ok = false;
    job.frames_registered = 0;
    job.error = "Debayer produced no output files";
    job.log = logLines.join("\n");
    File.writeTextFile(jobPath, JSON.stringify(job));
    flushLog();
    throw new Error("Debayer failed");
}

// ---------------------------------------------------------------------------
// Step 2: StarAlignment → registered _d_r.xisf + _d_r.xdrz sidecar files
// ---------------------------------------------------------------------------
var SA = new StarAlignment;

var saRef = debayeredFiles[0];
if (referenceImage) {
    if (File.exists(referenceImage)) {
        saRef = referenceImage;
        logln("StarAlignment reference: external canonical frame " + referenceImage);
    } else {
        logln("WARN: reference_image not found (" + referenceImage +
              "); falling back to first debayered frame");
    }
}
SA.referenceImage         = saRef;
SA.referenceIsFile        = true;
SA.writeRegisteredImages  = true;
SA.outputDirectory        = outputDir;
SA.outputExtension        = ".xisf";
SA.outputPrefix           = "";
SA.outputPostfix          = "_r";
SA.outputSampleFormat     = StarAlignment.prototype.f32;
SA.generateDrizzleData    = doDrizzle;   // skip unused .xdrz when not drizzling
SA.overwriteExistingFiles = true;
SA.onError                = StarAlignment.prototype.Continue;

SA.sensitivity            = 0.5;
SA.peakResponse           = 0.8;
SA.brightThreshold        = 3.0;
SA.maxStarDistortion      = 0.6;
SA.allowClusteredSources  = false;
SA.hotPixelFilterRadius   = 1;
SA.noiseReductionFilterRadius = 0;
SA.minStructureSize       = 0;
SA.useTriangles           = false;
SA.maxStars               = 0;

SA.matcherTolerance       = 0.0030;
SA.ransacMaxIterations    = 1500;
SA.ransacTolerance        = 2.0;
SA.maxRatio               = 2.50;
SA.minOverlapFraction     = 0.2;
SA.rigidTransformations   = false;
SA.inheritAstrometricSolution = false;

SA.pixelInterpolation     = StarAlignment.prototype.Auto;
SA.clampingThreshold      = 0.3;

var targets = [];
for (var j = 0; j < debayeredFiles.length; j++) {
    targets.push([true, true, debayeredFiles[j]]);
}
SA.targets = targets;

logln("Running StarAlignment on " + targets.length + " debayered frames...");
var saOk = false;
try {
    saOk = SA.executeGlobal();
} catch(e) {
    logln("StarAlignment threw: " + e.message);
}
logln("StarAlignment done — ok=" + saOk);
flushLog();

// Collect .xdrz files produced by SA
var xdrzFiles = [];
try {
    var ff1 = new FileFind;
    if (ff1.begin(outputDir + "/*_r.xdrz")) {
        do {
            if (ff1.isFile) xdrzFiles.push(outputDir + "/" + ff1.name);
        } while (ff1.next());
    }
} catch(e) {
    logln("WARNING: could not list .xdrz files: " + e.message);
}
logln("Drizzle data files (.xdrz): " + xdrzFiles.length);
flushLog();

// Validate registration on the _r.xisf frames (always produced), NOT the .xdrz
// sidecars (only generated when drizzling). Enumerate the registered frames now.
var registeredFiles = [];
try {
    var ffR = new FileFind;
    if (ffR.begin(outputDir + "/*_r.xisf")) {
        do {
            if (ffR.isFile) registeredFiles.push(outputDir + "/" + ffR.name);
        } while (ffR.next());
    }
} catch(e) {
    logln("WARNING: could not list registered .xisf files: " + e.message);
}
registeredFiles.sort();
logln("Registered _r.xisf files: " + registeredFiles.length);
job.frames_registered = registeredFiles.length;
flushLog();

if (registeredFiles.length < 2) {
    logln("FAIL: SA produced fewer than 2 registered _r.xisf files (" + registeredFiles.length + ")");
    job.ok = false;
    job.error = "StarAlignment produced fewer than 2 registered frames";
    job.log = logLines.join("\n");
    File.writeTextFile(jobPath, JSON.stringify(job));
    flushLog();
    throw new Error("Too few registered frames");
}
if (doDrizzle && xdrzFiles.length < 2) {
    logln("FAIL: drizzle requested but SA produced fewer than 2 .xdrz files (" + xdrzFiles.length + ")");
    job.ok = false;
    job.error = "Drizzle requested but StarAlignment produced fewer than 2 .xdrz files";
    job.log = logLines.join("\n");
    File.writeTextFile(jobPath, JSON.stringify(job));
    flushLog();
    throw new Error("Too few .xdrz files for drizzle");
}

// Close any ImageWindows left open by SA to free memory before DI
try {
    var saWindows = ImageWindow.windows;
    if (saWindows.length > 0) {
        logln("Closing " + saWindows.length + " SA result windows before DI...");
        for (var k = saWindows.length - 1; k >= 0; k--) {
            saWindows[k].close();
        }
    }
} catch(e) {
    logln("WARNING: could not close SA windows: " + e.message);
}
flushLog();

// ---------------------------------------------------------------------------
// Step 3: SA → ImageIntegration(generateDrizzleData=true) → DrizzleIntegration.
// This is the WBPP-proven order. ImageIntegration enriches each _r.xdrz with
// normalization/rejection/weight data that DrizzleIntegration needs to run
// reliably headlessly, AND produces a guaranteed 1x result as a fallback.
// DrizzleIntegration then produces the preferred 2x drizzled result.
// Result windows are retrieved via the process output properties
// (II.integrationImageId / DI.integrationImageId) — NOT a guessed window id,
// which is why earlier headless DI attempts "silently failed".
// ---------------------------------------------------------------------------
var savedPath = null;
var framesUsed = xdrzFiles.length;
var iiWin = null;
var diWin = null;

function xdrzFor(regPath) { return regPath.replace(/\.xisf$/i, ".xdrz"); }

// registeredFiles was enumerated and validated above (≥2 guaranteed here).
framesUsed = registeredFiles.length;
flushLog();

// ---- Step 3a: ImageIntegration (generateDrizzleData=true) ----
if (registeredFiles.length >= 2) {
    try {
        var II = new ImageIntegration();
        // [enabled, path, drizzlePath, localNormalizationPath]
        II.images = registeredFiles.map(function(f) {
            var dz = xdrzFor(f);
            return [true, f, File.exists(dz) ? dz : "", ""];
        });
        II.combination             = ImageIntegration.prototype.Average;
        II.weightMode              = ImageIntegration.prototype.DontCare;
        II.rejection               = (registeredFiles.length >= 8)
            ? ImageIntegration.prototype.WinsorizedSigmaClip
            : ImageIntegration.prototype.NoRejection;
        II.normalization           = ImageIntegration.prototype.Additive;
        II.generateIntegratedImage = true;
        // Only accumulate drizzle data when we'll actually run DrizzleIntegration.
        // With drizzle off this allocated ~23GB of per-frame drizzle structures in RAM
        // (unused) — the bulk of the 37GB II peak measured on a 532-frame M 33 stack.
        II.generateDrizzleData     = doDrizzle;
        II.generateRejectionMaps   = false;
        II.evaluateSNR             = false;
        II.closePreviousImages     = false;

        // NOTE: II.bufferSizeMB / II.stackSizeMB strip-processing knobs are INERT under
        // --automation-mode (verified: 16→1 MB and 1024→64 MB both leave peak unchanged),
        // and PI won't run headless -r scripts WITHOUT --automation-mode. So memory cannot
        // be tuned here. Peak ≈ baseline + canvas_Mpx × ~10.7 MB × N — bounded upstream by
        // the frame-count cap in stacker.py. Left at PI defaults.
        logln("Running ImageIntegration (generateDrizzleData=" + doDrizzle + ") on "
              + registeredFiles.length + " frames...");
        flushLog();
        var iiOk = II.executeGlobal();
        logln("ImageIntegration returned: " + iiOk + " id=" + II.integrationImageId);
        flushLog();
        if (iiOk && II.integrationImageId) {
            iiWin = ImageWindow.windowById(II.integrationImageId);
            if (iiWin && iiWin.isNull) iiWin = null;
        }
    } catch(e) {
        logln("ImageIntegration block threw: " + e.message);
    }
} else {
    logln("FAIL: fewer than 2 registered frames — cannot integrate");
}
flushLog();

// ---- Step 3b: DrizzleIntegration (preferred 2x result; skipped if doDrizzle false) ----
if (!doDrizzle) {
    logln("Drizzle disabled (job.drizzle=false) — keeping 1x ImageIntegration result");
}
if (doDrizzle && xdrzFiles.length >= 2) {
    try {
        var DI = new DrizzleIntegration();
        DI.inputData                = xdrzFiles.map(function(f) { return [true, f, ""]; });
        DI.scale                    = 2;
        DI.dropShrink               = 0.9;
        DI.kernelFunction           = DrizzleIntegration.prototype.Kernel_Square;
        DI.enableCFA                = false;  // frames are already debayered RGB
        DI.enableRejection          = false;
        DI.enableImageWeighting     = false;
        DI.enableSurfaceSplines     = false;
        DI.enableLocalDistortion    = false;
        DI.enableLocalNormalization = false;
        DI.closePreviousImages      = false;
        DI.showImages               = false;  // headless: don't spawn GUI windows

        logln("Running DrizzleIntegration: " + xdrzFiles.length + " .xdrz, scale=2...");
        flushLog();
        var diOk = false;
        try {
            diOk = DI.executeGlobal();
        } catch(ee) {
            logln("DI.executeGlobal threw: " + ee.message);
        }
        logln("DrizzleIntegration returned: " + diOk + " id=" + DI.integrationImageId);
        flushLog();
        if (diOk && DI.integrationImageId) {
            diWin = ImageWindow.windowById(DI.integrationImageId);
            if (diWin && diWin.isNull) diWin = null;
            // Close the drizzle weight window if one was produced.
            try {
                var dwId = DI.weightImageId;
                if (dwId) {
                    var dw = ImageWindow.windowById(dwId);
                    if (dw && !dw.isNull) dw.forceClose();
                }
            } catch(e3) {}
        }
    } catch(e) {
        logln("DrizzleIntegration block threw: " + e.message);
    }
}
flushLog();

// ---- Save: prefer DI 2x, else II 1x ----
var resultWin  = diWin || iiWin;
var resultKind = diWin ? "drizzle_2x" : (iiWin ? "integration_1x" : "none");
if (resultWin && !resultWin.isNull) {
    logln("Saving " + resultKind + " result: id=" + resultWin.mainView.id
          + " size=" + resultWin.mainView.image.width + "x" + resultWin.mainView.image.height);
    resultWin.saveAs(outputXisf, false, false, false, false);
    if (File.exists(outputXisf)) {
        savedPath = outputXisf;
        logln("Saved → " + outputXisf);
    } else {
        logln("saveAs failed — file not found");
    }
}
job.drizzle_used = (diWin != null);

// Cleanup result windows.
try {
    if (diWin && !diWin.isNull) diWin.forceClose();
    if (iiWin && !iiWin.isNull) iiWin.forceClose();
} catch(e) {}

logln("Final output: " + (savedPath || "(none)") + " (" + resultKind + ")");

// ---------------------------------------------------------------------------
// Write result
// ---------------------------------------------------------------------------
job.ok                = savedPath !== null;
job.frames_registered = xdrzFiles.length;
job.frames_used       = framesUsed;
job.output_xisf       = savedPath;
job.error             = savedPath ? null : "DrizzleIntegration and ImageIntegration both produced no output";
job.log               = logLines.join("\n");
File.writeTextFile(jobPath, JSON.stringify(job));
flushLog();

logln("pi_register_and_drizzle.js done — ok=" + job.ok);
