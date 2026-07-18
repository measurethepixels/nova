#include "/opt/PixInsight/src/scripts/AdP/WCSmetadata.jsh"
#include "/opt/PixInsight/src/scripts/AdP/AstronomicalCatalogs.jsh"
var JP = File.readTextFile("/tmp/pi_job_path.txt").trim();
var res = { ran: true, ok: false };
try {
   res.has_gaia = (typeof GaiaDR3Catalog !== "undefined") || (typeof GaiaCatalog !== "undefined");
   res.ok = true;
} catch (e) { res.error = e.toString(); }
File.writeTextFile(JP, JSON.stringify(res));
