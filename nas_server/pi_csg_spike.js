// SPIKE: isolate which AdP include breaks headless execution
#include "/opt/PixInsight/src/scripts/AdP/WCSmetadata.jsh"
#include "/opt/PixInsight/src/scripts/AdP/AstronomicalCatalogs.jsh"

var JP = File.readTextFile("/tmp/pi_job_path.txt").trim();
function out(obj) { File.writeTextFile(JP, JSON.stringify(obj)); }

out({ ok: true, stage: "after_both_includes" });
