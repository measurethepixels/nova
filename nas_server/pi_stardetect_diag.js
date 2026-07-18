// DIAG: run PI StarDetector on two images, dump brightest-star pixel positions.
#include <pjsr/StarDetector.jsh>

var JP = File.readTextFile("/tmp/pi_job_path.txt").trim();
var job = JSON.parse(File.readTextFile(JP));

function detect(path, topN) {
    var w = ImageWindow.open(path)[0];
    var view = w.mainView;
    var img = view.image;
    // Collapse to luminance if multichannel.
    if (img.numberOfChannels > 1) {
        var L = new ImageWindow(img.width, img.height, 1, 32, true, false, "L");
        var pm = new PixelMath;
        pm.expression = "$T[0]*0.333 + $T[1]*0.334 + $T[2]*0.333";
        pm.createNewImage = false;
        pm.executeOn(view, false);
    }
    var sd = new StarDetector;
    sd.structureLayers = 5;
    sd.noiseLayers = 1;
    sd.hotPixelFilterRadius = 1;
    sd.sensitivity = 0.1;
    sd.peakResponse = 0.8;
    sd.maxDistortion = 0.5;
    var S = sd.stars(view.image);
    w.forceClose();
    // S: array of {pos:{x,y}, flux, size, ...}
    S.sort(function (a, b) { return b.flux - a.flux; });
    var out = [];
    for (var i = 0; i < Math.min(topN, S.length); i++)
        out.push([S[i].pos.x, S[i].pos.y, S[i].flux]);
    return { n: S.length, top: out };
}

var res = { ok: false };
try {
    res.ref = detect(job.ref_path, 250);
    res.frame = detect(job.frame_path, 250);
    res.ok = true;
} catch (e) {
    res.error = e.toString();
}
File.writeTextFile(JP, JSON.stringify(res));
