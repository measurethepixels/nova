/**
 * pi_register.js — PI Debayer + StarAlignment with drizzle sidecar generation.
 *
 * Debayers raw CFA frames (reads BAYERPAT from FITS header via Auto mode),
 * then registers the debayered RGB files and produces .xdrz sidecar files
 * needed by DrizzleIntegration.
 *
 * Pipeline:
 *   1. Debayer: raw CFA .fit → debayered _d.xisf (in output_dir/debayered/)
 *   2. StarAlignment: _d.xisf → _d_r.xisf + _d_r.xdrz (in output_dir/)
 *
 * Job JSON keys (input):
 *   input_files   string[]  Paths to raw CFA light frames (.fit/.fits)
 *   output_dir    string    Directory for registered _r.xisf + _r.xdrz files
 *
 * Result keys written back:
 *   ok                 bool
 *   frames_registered  int   Count of .xdrz files produced
 *   error              string  (on failure)
 */

// ---------------------------------------------------------------------------
// Headless crash guards — same set as pi_solve.js.
// ---------------------------------------------------------------------------
#define __ADP_SEARCHCOORDINATES_jsh
#define __ADP_CATALOGDOWNLOADER_js
#define __ADP_ASTRONOMICALCATALOGS_jsh
#define __ADP_IMAGESOLVER_js

// DataType.jsh must be first — defines DataType_Double / DataType_UCString
#include <pjsr/DataType.jsh>

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------
var logLines = [];
var logPath  = "/tmp/pi_register_last.log";

function logln(msg) {
    console.writeln(msg);
    logLines.push(msg);
}
function flushLog() {
    File.writeTextFile(logPath, logLines.join("\n") + "\n");
}

logln("pi_register.js starting — PI " + CoreApplication.versionLongString);

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
var inputFiles = job.input_files || [];
var outputDir  = job.output_dir  || null;

if (!inputFiles.length || !outputDir) {
    logln("FAIL: input_files or output_dir missing in job");
    job.ok = false; job.error = "input_files or output_dir missing";
    job.log = logLines.join("\n");
    File.writeTextFile(jobPath, JSON.stringify(job));
    flushLog();
    throw new Error("Bad job");
}

logln("Input frames: " + inputFiles.length);
logln("Output dir:   " + outputDir);

// Ensure output directory exists
if (!File.directoryExists(outputDir)) {
    File.createDirectory(outputDir, true);
}

// ---------------------------------------------------------------------------
// Step 1: Debayer CFA frames → RGB XISF
// ---------------------------------------------------------------------------
// Debayer.prototype.Auto reads BAYERPAT FITS keyword (SeeStar uses GRBG).
// targetItems format: [[enabled, filePath], ...] (2-column array).
var debayerDir = outputDir + "/debayered";
if (!File.directoryExists(debayerDir)) {
    File.createDirectory(debayerDir, true);
}

logln("Debayering " + inputFiles.length + " CFA frames (Auto pattern → RGB XISF)...");

var DB = new Debayer;
DB.cfaPattern            = Debayer.prototype.Auto;  // reads BAYERPAT from FITS header
DB.debayerMethod         = Debayer.prototype.VNG;   // WBPP default
DB.outputDirectory       = debayerDir;
DB.outputExtension       = ".xisf";
DB.outputPostfix         = "_d";
DB.outputRGBImages       = true;
DB.outputSeparateChannels = false;
DB.evaluateNoise         = false;
DB.evaluateSignal        = false;
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
// Step 2: StarAlignment on debayered RGB files
// ---------------------------------------------------------------------------
// NOTE: Do NOT set SA.mode — the default (star pattern matching) is what
// WBPP uses. RegisterMatch tries to plate-solve each frame in headless mode
// and silently fails, producing only the reference .xdrz.
var SA = new StarAlignment;

SA.referenceImage         = debayeredFiles[0];  // first debayered frame is reference
SA.referenceIsFile        = true;
SA.writeRegisteredImages  = true;
SA.outputDirectory        = outputDir;
SA.outputExtension        = ".xisf";
SA.outputPrefix           = "";
SA.outputPostfix          = "_r";
SA.outputSampleFormat     = StarAlignment.prototype.f32;
SA.generateDrizzleData    = true;               // produces .xdrz sidecar files
SA.overwriteExistingFiles = true;
SA.onError                = StarAlignment.prototype.Continue;  // skip bad frames

// Star detection — WBPP defaults
SA.sensitivity            = 0.5;
SA.peakResponse           = 0.8;
SA.brightThreshold        = 3.0;
SA.maxStarDistortion      = 0.6;
SA.allowClusteredSources  = false;
SA.hotPixelFilterRadius   = 1;
SA.noiseReductionFilterRadius = 0;
SA.minStructureSize       = 0;
SA.useTriangles           = false;
SA.maxStars               = 0;  // 0 = use all detected stars

// Registration solver
SA.matcherTolerance       = 0.0030;
SA.ransacMaxIterations    = 1500;
SA.ransacTolerance        = 2.0;
SA.maxRatio               = 2.50;
SA.minOverlapFraction     = 0.2;
SA.rigidTransformations   = false;
SA.inheritAstrometricSolution = false;

// Interpolation
SA.pixelInterpolation     = StarAlignment.prototype.Auto;
SA.clampingThreshold      = 0.3;

// Build target list: [[enabled, selected, filePath], ...]
// WBPP uses enableTargetFrames(files, 3) which sets columns 0+1 to true for ALL files.
// The reference frame is designated via SA.referenceImage, not the lock flag.
var targets = [];
for (var j = 0; j < debayeredFiles.length; j++) {
    targets.push([true, true, debayeredFiles[j]]);
}
SA.targets = targets;

logln("Running StarAlignment on " + targets.length + " debayered frames (generateDrizzleData=true)...");
var saOk = false;
try {
    saOk = SA.executeGlobal();
} catch(e) {
    logln("StarAlignment threw: " + e.message);
}
logln("StarAlignment done — ok=" + saOk);
flushLog();

// ---------------------------------------------------------------------------
// Count .xdrz output files
// ---------------------------------------------------------------------------
var framesRegistered = 0;
try {
    var ff = new FileFind;
    if (ff.begin(outputDir + "/*_r.xdrz")) {
        do {
            if (ff.isFile) framesRegistered++;
        } while (ff.next());
    }
    logln("Registered frames (.xdrz files found): " + framesRegistered);
} catch(e) {
    logln("WARNING: could not count .xdrz files: " + e.message);
    framesRegistered = saOk ? (debayeredFiles.length - 1) : 0;
}

// ---------------------------------------------------------------------------
// Write result
// ---------------------------------------------------------------------------
job.ok                = saOk && framesRegistered > 0;
job.frames_registered = framesRegistered;
job.error             = (!saOk || framesRegistered === 0)
                        ? "StarAlignment produced no .xdrz files" : null;
job.log               = logLines.join("\n");
File.writeTextFile(jobPath, JSON.stringify(job));
flushLog();

logln("pi_register.js done — debayered=" + debayeredFiles.length
      + " registered=" + framesRegistered);
