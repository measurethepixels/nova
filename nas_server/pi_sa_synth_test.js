// DIAG: register real CFA subs against a synthetic reference field.
// Debayers each sub (VNG), runs StarAlignment vs job.ref_path, counts matches.
var JP = File.readTextFile("/tmp/pi_job_path.txt").trim();
var job = JSON.parse(File.readTextFile(JP));

function debayer(path) {
    var w = ImageWindow.open(path)[0];
    var P = new Debayer;
    P.bayerPattern = Debayer.prototype.Auto;
    P.debayerMethod = Debayer.prototype.VNG;
    P.executeOn(w.mainView, false);
    var out = path.replace(/\.[^.]+$/, "") + "_d.xisf";
    w.saveAs(out, false, false, false, false);
    w.forceClose();
    return out;
}

var res = { ok: false, n: 0, registered: 0, per: [] };
try {
    for (var i = 0; i < job.subs.length; i++) {
        var d = debayer(job.subs[i]);
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
        SA.targets = [[true, true, d]];
        var ok = false;
        try { ok = SA.executeGlobal(); } catch (e) { ok = false; }
        res.per.push(ok ? 1 : 0);
        if (ok) res.registered++;
        res.n++;
        File.remove(d);
    }
    res.ok = true;
} catch (e) {
    res.error = e.toString();
}
File.writeTextFile(JP, JSON.stringify(res));
