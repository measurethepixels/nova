// DIAG: StarAlignment on pre-aligned grayscale frames (no debayer), ref = frames[0].
var JP = File.readTextFile("/tmp/pi_job_path.txt").trim();
var job = JSON.parse(File.readTextFile(JP));

var res = { ok: false };
try {
    var SA = new StarAlignment;
    SA.referenceImage = job.frames[0];
    SA.referenceIsFile = true;
    SA.writeRegisteredImages = false;
    SA.generateDrizzleData = false;
    SA.onError = StarAlignment.prototype.Continue;
    SA.useTriangles = false;
    SA.maxStars = 0;
    SA.sensitivity = 0.5;
    SA.peakResponse = 0.8;
    SA.matcherTolerance = 0.0030;
    SA.ransacTolerance = 2.0;
    SA.ransacMaxIterations = 2000;
    SA.maxRatio = 2.5;
    SA.minOverlapFraction = 0.2;

    var tg = [];
    for (var i = 1; i < job.frames.length; i++) tg.push([true, true, job.frames[i]]);
    SA.targets = tg;

    var ok = false;
    try { ok = SA.executeGlobal(); } catch (e) { res.throw = e.toString(); }
    res.sa_ok = ok;
    res.n_targets = tg.length;
    // Count how many targets actually registered. SA.outputData[0][k] is a
    // per-target boolean success flag; capture the count when available.
    try {
        var od = SA.outputData;
        var reg = 0, total = 0;
        if (od && od.length && od[0] && od[0].length) {
            total = od[0].length;
            for (var k = 0; k < od[0].length; k++) if (od[0][k]) reg++;
        }
        res.n_registered = reg;
        res.n_outrows = total;
    } catch (e) { res.od_err = e.toString(); }
    res.ok = true;
} catch (e) {
    res.error = e.toString();
}
File.writeTextFile(JP, JSON.stringify(res));
