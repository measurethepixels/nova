// DIAG: StarAlignment one target frame against a reference; report match success.
var JP = File.readTextFile("/tmp/pi_job_path.txt").trim();
var job = JSON.parse(File.readTextFile(JP));

var res = { ok: false };
try {
    var SA = new StarAlignment;
    SA.referenceImage = job.ref_path;
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
    SA.rigidTransformations = false;
    SA.targets = [[true, true, job.frame_path]];

    var ok = false;
    try { ok = SA.executeGlobal(); } catch (e) { res.sa_throw = e.toString(); }
    res.sa_ok = ok;
    // SA.outputData rows: [ok, path, ..., numberOfStars?, ...] — capture what we can.
    try {
        res.numStars = SA.outputData.length ? SA.outputData[0].length : -1;
        res.row0 = SA.outputData[0];
    } catch (e) {}
    res.ok = true;
} catch (e) {
    res.error = e.toString();
}
File.writeTextFile(JP, JSON.stringify(res));
