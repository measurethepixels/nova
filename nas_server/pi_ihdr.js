/**
 * iHDR wrapper — runs Uri Darom's iHDR script headlessly on a single image.
 * Invoked as a second PI pass by pixinsight.py when ihdr=true.
 *
 * iHDR performs multiscale iterative HDR: protects bright highlights while
 * revealing fainter structure. Apply on a stretched (non-linear) image.
 *
 * Job keys used:
 *   input, output, ihdr_iterations, ihdr_preservation, ihdr_mask_strength
 */

var logLines = [];
var logPath = "/tmp/pi_ihdr_last.log";

function logln(msg) {
    console.writeln(msg);
    logLines.push(msg);
}

function flushLog() {
    File.writeTextFile(logPath, logLines.join("\n") + "\n");
}

logln("pi_ihdr.js starting");

// Read job config via sentinel
var sentinelPath = "/tmp/pi_job_path.txt";
var jobPath;
try {
    jobPath = File.readFile(sentinelPath).toString().trim();
} catch(e) {
    logln("ERROR reading sentinel: " + e.message);
    flushLog();
    throw new Error("Cannot read sentinel");
}

var job = JSON.parse(File.readFile(jobPath).toString());
var inputPath  = job.input;
var outputPath = job.output;

// iHDR parameters
var ihdrIterations    = typeof job.ihdr_iterations    === "number" ? job.ihdr_iterations    : 5;
var ihdrPreservation  = typeof job.ihdr_preservation  === "number" ? job.ihdr_preservation  : 5;
var ihdrMaskStrength  = typeof job.ihdr_mask_strength === "number" ? job.ihdr_mask_strength : 1.25;
var ihdrLayersPer     = typeof job.ihdr_layers_per    === "number" ? job.ihdr_layers_per    : 1;

logln("Input:  " + inputPath);
logln("Output: " + outputPath);
logln("iHDR: iterations=" + ihdrIterations + " preservation=" + ihdrPreservation +
      " maskStrength=" + ihdrMaskStrength);

if (!File.exists(inputPath)) {
    logln("ERROR: input not found: " + inputPath);
    flushLog();
    throw new Error("Input not found");
}

// Open image
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
      " channels=" + view.image.numberOfChannels);

// Include iHDR script.
// Its main() call will execute and return early in automation mode (no dialogs).
// After the include, all iHDR functions and the 'parameters' var are in scope.
#include "/opt/PixInsight/src/scripts/iHDR/iHDR.js"

// Override parameters with our job values and call the processing function
parameters.targetView    = view;
parameters.iterations    = ihdrIterations;
parameters.preservation  = ihdrPreservation;
parameters.maskStrength  = ihdrMaskStrength;
parameters.layersPer     = ihdrLayersPer;
parameters.repetitions   = 1;
parameters.showTemp      = false;
parameters.oldCheck      = true;

logln("Running iHDR...");
try {
    executeOnTargetView(view);
    logln("iHDR: OK");
} catch(e) {
    logln("iHDR ERROR: " + e.message);
    job.ihdr_failed = true;
}

// Save
logln("Saving to: " + outputPath);
try {
    win.saveAs(outputPath, false, false, false, false);
    var saved = File.exists(outputPath);
    logln("Save " + (saved ? "OK" : "FAILED") + ": " + outputPath);
    job.ihdr_output_exists = saved;
} catch(e) {
    logln("Save ERROR: " + e.message);
    job.ihdr_output_exists = false;
}

win.forceClose();

File.writeTextFile(jobPath, JSON.stringify(job, null, 2));
logln("pi_ihdr.js done");
flushLog();
