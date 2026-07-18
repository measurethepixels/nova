/**
 * PixInsight headless stacking script — ImageIntegration with SNR weighting.
 * Called after Siril registration; replaces Siril/SASpro integration step.
 *
 * Job JSON fields:
 *   input_files   : array of registered .fit paths
 *   output_path   : where to save the integrated FITS
 *   rejection     : "winsorized" (default) | "sigma" | "linear" | "none"
 *   sigma_low     : float, default 3.0
 *   sigma_high    : float, default 3.0
 *   weight_mode   : "snr" (default) | "equal" | "exposure"
 *   weight_scale  : "avgdev" (default, fast) | "ikss" (accurate, slow) | "mad" | "bwmv"
 *   normalization : "additive_scaling" (default) | "additive" (faster, single-session)
 *
 * Output fields written back to job JSON:
 *   stack_ok      : bool
 *   stack_failed  : bool
 *   frames_used   : int
 *
 * Invocation (offscreen):
 *   LD_LIBRARY_PATH=/opt/PixInsight/bin/lib QT_QPA_PLATFORM=offscreen \
 *   /opt/PixInsight/bin/PixInsight --automation-mode -n -r=pi_stack.js --force-exit
 */

var logLines = [];
var logPath  = "/tmp/pi_stack_last.log";

function logln(msg) {
    console.writeln(msg);
    logLines.push(msg);
}

function flushLog() {
    File.writeTextFile(logPath, logLines.join("\n") + "\n");
}

logln("pi_stack.js starting");

// ---------------------------------------------------------------------------
// Read job config
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

var inputFiles    = job.input_files   || [];
var outputPath    = job.output_path;
var rejMode       = job.rejection     || "winsorized";
var sigmaLow      = (job.sigma_low  !== undefined) ? job.sigma_low  : 3.0;
var sigmaHigh     = (job.sigma_high !== undefined) ? job.sigma_high : 3.0;
var weightMode    = job.weight_mode   || "snr";
var weightScale   = job.weight_scale  || "avgdev";
var normalization = job.normalization || "additive_scaling";
var evaluateSNR   = job.evaluate_snr  || false;

logln("Input frames: " + inputFiles.length);
logln("Output: " + outputPath);
logln("Rejection: " + rejMode + " sigma=" + sigmaLow + "/" + sigmaHigh);
logln("Weight mode: " + weightMode + " / scale: " + weightScale);
logln("Normalization: " + normalization);

if (inputFiles.length === 0) {
    logln("ERROR: no input files");
    job.stack_failed = true;
    File.writeTextFile(jobPath, JSON.stringify(job, null, 2));
    flushLog();
    throw new Error("No input files");
}

// ---------------------------------------------------------------------------
// Build ImageIntegration
// ---------------------------------------------------------------------------
try {
    var ii = new ImageIntegration();

    // Images: [enabled, path, drizzlePath, localNormalizationPath]
    ii.images = inputFiles.map(function(f) {
        return [true, f, "", ""];
    });

    // Combination: Average
    ii.combination = ImageIntegration.prototype.Average;

    // Weighting
    if (weightMode === "snr") {
        ii.weightMode = ImageIntegration.prototype.PSFSignalWeight;
        // Weight scale: avgdev is 10-50x faster than ikss with minimal quality loss
        // for well-behaved data (consistent exposures, same session).
        if      (weightScale === "ikss")  ii.weightScale = ImageIntegration.prototype.WeightScale_IKSS;
        else if (weightScale === "mad")   ii.weightScale = ImageIntegration.prototype.WeightScale_MAD;
        else if (weightScale === "bwmv")  ii.weightScale = ImageIntegration.prototype.WeightScale_BWMV;
        else                              ii.weightScale = ImageIntegration.prototype.WeightScale_AvgDev;
    } else if (weightMode === "exposure") {
        ii.weightMode = ImageIntegration.prototype.ExposureTimeWeight;
    } else {
        ii.weightMode = ImageIntegration.prototype.DontCare;
    }

    // Rejection algorithm
    var rej = ImageIntegration.prototype.WinsorizedSigmaClip;
    if (rejMode === "sigma")   rej = ImageIntegration.prototype.SigmaClip;
    if (rejMode === "linear")  rej = ImageIntegration.prototype.LinearClip;
    if (rejMode === "none")    rej = ImageIntegration.prototype.NoRejection;
    ii.rejection = rej;

    ii.rejectionNormalization = ImageIntegration.prototype.Scale;
    ii.sigmaLow              = sigmaLow;
    ii.sigmaHigh             = sigmaHigh;
    ii.winsorizationCutoff   = 5.0;

    // Normalisation
    if (normalization === "additive") {
        ii.normalization = ImageIntegration.prototype.Additive;
    } else {
        ii.normalization = ImageIntegration.prototype.AdditiveWithScaling;
    }

    // Output options
    ii.generateRejectionMaps   = false;
    ii.generateIntegratedImage = true;
    ii.minWeight               = 0.005;
    ii.evaluateSNR             = evaluateSNR;

    logln("Executing ImageIntegration...");
    var ok = ii.executeGlobal();
    logln("ImageIntegration returned: " + ok);

    if (!ok) {
        logln("ERROR: ImageIntegration.executeGlobal() returned false");
        job.stack_failed = true;
        File.writeTextFile(jobPath, JSON.stringify(job, null, 2));
        flushLog();
        throw new Error("ImageIntegration failed");
    }

    // ---------------------------------------------------------------------------
    // Save result
    // ---------------------------------------------------------------------------
    var integrated = ImageWindow.windowById("integration");
    if (!integrated || integrated.isNull) {
        // Fall back to active window
        integrated = ImageWindow.activeWindow;
    }

    if (!integrated || integrated.isNull) {
        logln("ERROR: no integration result window found");
        job.stack_failed = true;
        File.writeTextFile(jobPath, JSON.stringify(job, null, 2));
        flushLog();
        throw new Error("No result window");
    }

    logln("Saving integrated image to: " + outputPath);
    integrated.saveAs(outputPath, false, false, false, false);

    if (File.exists(outputPath)) {
        logln("Save OK");
        job.stack_ok    = true;
        job.frames_used = inputFiles.length;
    } else {
        logln("ERROR: output file not created");
        job.stack_failed = true;
    }

    integrated.forceClose();

} catch(e) {
    logln("EXCEPTION: " + e.message);
    job.stack_failed = true;
}

// Write result back for Python to read
File.writeTextFile(jobPath, JSON.stringify(job, null, 2));
logln("pi_stack.js done");
flushLog();
