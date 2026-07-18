/**
 * pi_drizzle.js — PI DrizzleIntegration on .xdrz sidecar files from StarAlignment.
 *
 * Produces a 2x-upsampled CFA result using DrizzleIntegration with Winsorized
 * rejection. Input .xdrz files must come from pi_register.js (PI StarAlignment
 * with generateDrizzleData=true). Output is XISF — Python side converts to FITS.
 *
 * Job JSON keys (input):
 *   xdrz_files    string[]  Paths to .xdrz sidecar files
 *   output_xisf   string    Full path for output XISF (e.g. /tmp/.../result.xisf)
 *
 * Result keys written back:
 *   ok            bool
 *   frames_used   int
 *   output_xisf   string  Actual path of output file (may differ from requested)
 *   error         string  (on failure)
 */

// Headless crash guards — prevent Dialog/Control subclass includes from crashing PI.
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
var logPath  = "/tmp/pi_drizzle_last.log";

function logln(msg) {
    console.writeln(msg);
    logLines.push(msg);
}
function flushLog() {
    File.writeTextFile(logPath, logLines.join("\n") + "\n");
}

logln("pi_drizzle.js starting — PI " + CoreApplication.versionLongString);
flushLog();  // write early so a crash before first try-catch is detectable

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
var xdrzFiles  = job.xdrz_files  || [];
var outputXisf = job.output_xisf || null;

if (!xdrzFiles.length || !outputXisf) {
    logln("FAIL: xdrz_files or output_xisf missing in job");
    job.ok = false; job.error = "xdrz_files or output_xisf missing";
    job.log = logLines.join("\n");
    File.writeTextFile(jobPath, JSON.stringify(job));
    flushLog();
    throw new Error("Bad job");
}

// Verify at least some .xdrz files exist
var validXdrz = [];
for (var i = 0; i < xdrzFiles.length; i++) {
    if (File.exists(xdrzFiles[i])) {
        validXdrz.push(xdrzFiles[i]);
    } else {
        logln("WARNING: .xdrz not found: " + xdrzFiles[i]);
    }
}
logln(".xdrz files: " + validXdrz.length + " valid of " + xdrzFiles.length + " specified");
logln("Output XISF: " + outputXisf);

if (validXdrz.length < 2) {
    logln("FAIL: fewer than 2 valid .xdrz files");
    job.ok = false; job.error = "fewer than 2 valid .xdrz files";
    job.log = logLines.join("\n");
    File.writeTextFile(jobPath, JSON.stringify(job));
    flushLog();
    throw new Error("Too few .xdrz files");
}

// Derive output directory and file stem from output_xisf path
// e.g. "/tmp/seestar_stack_X/result.xisf" → dir="/tmp/seestar_stack_X", name="result"
var lastSlash = outputXisf.lastIndexOf('/');
var outDir    = outputXisf.substring(0, lastSlash);
var outFile   = outputXisf.substring(lastSlash + 1);
// Strip extension: "result.xisf" → "result"
var dotIdx = outFile.lastIndexOf('.');
if (dotIdx > 0) outFile = outFile.substring(0, dotIdx);

logln("Output dir:  " + outDir);
logln("Output name: " + outFile);

// ---------------------------------------------------------------------------
// DrizzleIntegration
// ---------------------------------------------------------------------------
var DI;
try {
    DI = new DrizzleIntegration;
} catch(e) {
    logln("FAIL: DrizzleIntegration constructor threw: " + e.message);
    job.ok = false; job.error = "DrizzleIntegration constructor: " + e.message;
    job.log = logLines.join("\n");
    File.writeTextFile(jobPath, JSON.stringify(job));
    flushLog();
    throw e;
}
logln("DrizzleIntegration instantiated OK");
flushLog();

// Confirm source debayered images are reachable (xdrz files reference these)
// xdrz files are in process/, debayered xisf are in process/debayered/
var debayeredDir = validXdrz[0].replace(/\/[^\/]+$/, "") + "/debayered";
logln("xdrz[0]: " + validXdrz[0]);
logln("debayeredDir guess: " + debayeredDir);
var debayeredCount = 0;
try {
    var ff1 = new FileFind;
    if (ff1.begin(debayeredDir + "/*_d.xisf")) {
        do { if (ff1.isFile) debayeredCount++; } while (ff1.next());
    }
} catch(e) {}
logln("Debayered source images visible to PI: " + debayeredCount + " in " + debayeredDir);
flushLog();

try {
    // Input: array of [enabled, xdrzPath] pairs (boolean flag first, then path)
    DI.inputData = validXdrz.map(function(f) { return [true, f]; });

    // Output
    DI.outputDirectory = outDir;
    DI.outputFileName  = outFile;   // → outDir/outFile.xisf

    // Minimal drizzle parameters — keep simple to avoid unknown property issues
    DI.scale      = 2;    // 2x output
    DI.dropShrink = 1.0;  // CFA default
} catch(e) {
    logln("FAIL: DI property assignment threw: " + e.message);
    job.ok = false; job.error = "DI property: " + e.message;
    job.log = logLines.join("\n");
    File.writeTextFile(jobPath, JSON.stringify(job));
    flushLog();
    throw e;
}
logln("DI properties set OK (scale=2, dropShrink=1.0, minimal)");
flushLog();

logln(format("Running DrizzleIntegration: %d files, scale=2, dropShrink=1.0, Kernel_Square ...",
    validXdrz.length));

var diOk = false;
try {
    diOk = DI.executeGlobal();
} catch(e) {
    logln("DrizzleIntegration threw: " + e.message);
}
logln("DrizzleIntegration done — ok=" + diOk);
flushLog();

// Define expected output path early (used in ImageWindow save attempt below)
var actualOutput = outDir + "/" + outFile + ".xisf";

// Check if DI created an ImageWindow (might not auto-save in headless mode)
try {
    var windows = ImageWindow.windows;
    logln("Open ImageWindows after DI: " + windows.length);
    for (var wi = 0; wi < windows.length; wi++) {
        var w = windows[wi];
        var sz = w.mainView.image.width + "x" + w.mainView.image.height;
        logln("  Window[" + wi + "]: id=" + w.mainView.id + " size=" + sz
              + " path=" + (w.filePath || "(unsaved)"));
        if (!diOk && !w.filePath) {
            // DI failed to auto-save — try to save it explicitly
            logln("  → saving window explicitly to " + actualOutput);
            w.saveAs(actualOutput, false, false, false, false);
            if (File.exists(actualOutput)) {
                logln("  → explicit save succeeded");
                diOk = true;
            }
        }
    }
} catch(e) {
    logln("ImageWindow inspect threw: " + e.message);
}
flushLog();

// Scan outDir for any .xisf files PI may have written (name might differ)
var xisfFound = [];
try {
    var ff2 = new FileFind;
    if (ff2.begin(outDir + "/*.xisf")) {
        do { if (ff2.isFile) xisfFound.push(ff2.name); } while (ff2.next());
    }
} catch(e) {}
logln("XISF files in outDir after DI: " + (xisfFound.length ? xisfFound.join(", ") : "(none)"));
flushLog();

// Verify output file exists
var outputExists = File.exists(actualOutput);
logln("Output exists: " + outputExists + " at " + actualOutput);

// ---------------------------------------------------------------------------
// Write result
// ---------------------------------------------------------------------------
job.ok           = diOk && outputExists;
job.frames_used  = validXdrz.length;
job.output_xisf  = outputExists ? actualOutput : null;
job.error        = (!diOk || !outputExists) ? "DrizzleIntegration did not produce output" : null;
job.log          = logLines.join("\n");
File.writeTextFile(jobPath, JSON.stringify(job));
flushLog();

logln("pi_drizzle.js done — ok=" + job.ok);
