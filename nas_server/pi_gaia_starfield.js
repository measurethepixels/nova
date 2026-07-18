/**
 * pi_gaia_starfield.js — Query the local Gaia DR3 XPSD catalog for a sky region
 * and dump (ra, dec, magG) to CSV. Used to build a synthetic StarAlignment
 * reference frame at a canonical per-target WCS (Stage 2 canonical registration).
 *
 * Uses the core Gaia process directly (no AdP includes — those crash headless).
 * The XPSD database directory is whatever is configured in PI's Gaia preferences
 * (already set up for SPCC/plate-solving via ~/pi_gaia_db/Pix_Database/).
 *
 * Job JSON keys (path read from /tmp/pi_job_path.txt):
 *   center_ra     number  Cone center RA  (deg)
 *   center_dec    number  Cone center Dec (deg)
 *   radius_deg    number  Cone search radius (deg)
 *   mag_limit     number  Faint magnitude limit (magnitudeHigh, default 17)
 *   source_limit  number  Max sources to return (default 50000)
 *   output_csv    string  Path to write "ra,dec,magG" CSV
 *
 * Result keys written back:
 *   ok            bool
 *   n_sources     int
 *   output_csv    string
 *   error         string  (on failure)
 *   log           string
 */

var logLines = [];
var logPath  = "/tmp/pi_gaia_starfield_last.log";

function logln(msg) {
    console.writeln(msg);
    logLines.push(msg);
}
function flushLog() {
    File.writeTextFile(logPath, logLines.join("\n") + "\n");
}

logln("pi_gaia_starfield.js starting — PI " + CoreApplication.versionLongString);

var sentinelPath = "/tmp/pi_job_path.txt";
var jobPath;
try {
    jobPath = File.readFile(sentinelPath).toString().trim();
} catch (e) {
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

var centerRA   = Number(job.center_ra);
var centerDec  = Number(job.center_dec);
var radiusDeg  = Number(job.radius_deg);
var magLimit   = (job.mag_limit  !== undefined && job.mag_limit  !== null) ? Number(job.mag_limit)  : 17.0;
var srcLimit   = (job.source_limit !== undefined && job.source_limit !== null) ? Number(job.source_limit) : 50000;
var outputCsv  = job.output_csv || null;

function fail(msg) {
    logln("FAIL: " + msg);
    job.ok = false;
    job.error = msg;
    job.log = logLines.join("\n");
    File.writeTextFile(jobPath, JSON.stringify(job));
    flushLog();
    throw new Error(msg);
}

if (!isFinite(centerRA) || !isFinite(centerDec) || !isFinite(radiusDeg) || radiusDeg <= 0)
    fail("invalid cone parameters: ra=" + centerRA + " dec=" + centerDec + " radius=" + radiusDeg);
if (!outputCsv)
    fail("output_csv not specified");
if (typeof Gaia == "undefined")
    fail("The Gaia process is not installed in this PixInsight.");

logln(format("Cone: RA=%.5f Dec=%.5f radius=%.4f deg  magLimit=%.1f  sourceLimit=%d",
             centerRA, centerDec, radiusDeg, magLimit, srcLimit));

var server = new Gaia;
server.command            = "search";
server.dataRelease        = Gaia.prototype.DataRelease_3;
server.centerRA           = centerRA;
server.centerDec          = centerDec;
server.radius             = radiusDeg;
server.magnitudeLow       = -1.5;
server.magnitudeHigh      = magLimit;
server.sourceLimit        = srcLimit;
server.sortBy             = Gaia.prototype.SortBy_G;
server.generateTextOutput = false;
server.verbosity          = 1;

var ok = false;
try {
    ok = server.executeGlobal();
} catch (e) {
    fail("Gaia search threw: " + e.message);
}
if (!ok)
    fail("Gaia search returned false (no database configured?)");

// sources rows: [ra, dec, parx, pmra, pmdec, magG, magBP, magRP, flags, flux]
var S = server.sources;
var n = S ? S.length : 0;
logln("Gaia returned " + n + " sources (excessCount=" + server.excessCount + ")");
if (n === 0)
    fail("Gaia returned 0 sources for this region");

var lines = ["ra,dec,magG"];
for (var i = 0; i < n; ++i) {
    var s = S[i];
    lines.push(s[0].toFixed(7) + "," + s[1].toFixed(7) + "," + s[5].toFixed(3));
}
File.writeTextFile(outputCsv, lines.join("\n") + "\n");
logln("Wrote " + n + " sources → " + outputCsv);

job.ok         = true;
job.n_sources  = n;
job.output_csv = outputCsv;
job.error      = null;
job.log        = logLines.join("\n");
File.writeTextFile(jobPath, JSON.stringify(job));
flushLog();

logln("pi_gaia_starfield.js done — n=" + n);
