"""Read-only environment observations; never installs or rewrites workflow history."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from urllib.request import Request, urlopen

STATUSES = ("CURRENT", "UPDATE_AVAILABLE", "VALIDATION_REQUIRED", "APPROVED_UPDATE",
            "HOLD", "CHECK_FAILED", "UNKNOWN")
GROUPS = {"application": "Applications", "ai_model": "AI Models",
          "reference_data": "Reference Data", "runtime": "Runtime",
          "deployed_worker": "Deployed Workers"}


def migrate(conn):
    statuses = ",".join(repr(value) for value in STATUSES)
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS environment_components (
            name TEXT PRIMARY KEY, category TEXT NOT NULL,
            installed TEXT, latest TEXT, recommended TEXT,
            status TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(status IN ({statuses})),
            stale INTEGER NOT NULL DEFAULT 1, source TEXT,
            last_successful_check TEXT, detail TEXT, metadata TEXT NOT NULL DEFAULT '{{}}'
        );
        CREATE TABLE IF NOT EXISTS environment_checks (
            id INTEGER PRIMARY KEY, component TEXT NOT NULL, checked_at TEXT NOT NULL,
            source TEXT NOT NULL, installed_version_seen TEXT, latest_version_seen TEXT,
            result TEXT NOT NULL CHECK(result IN ('ok','check_failed','unknown')),
            raw_response_ref TEXT, detail TEXT, observation_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS environment_updates (
            id INTEGER PRIMARY KEY, component TEXT NOT NULL, before_version TEXT,
            after_version TEXT, applied_at TEXT NOT NULL, applied_by TEXT NOT NULL,
            handler_name TEXT NOT NULL, outcome TEXT NOT NULL
        );
    """)


def status_for(installed, latest, recommended, previous="UNKNOWN"):
    # Checks cannot grant approval or silently remove a deliberate hold.
    if previous == "HOLD":
        return "HOLD"
    if not installed:
        return "UNKNOWN"
    if recommended and installed != recommended:
        if previous == "APPROVED_UPDATE" and latest == recommended:
            return "APPROVED_UPDATE"
        return "VALIDATION_REQUIRED"
    if not latest:
        return "UNKNOWN"
    if installed == latest:
        return "CURRENT"
    if re.fullmatch(r"\d+(\.\d+)+", installed) and re.fullmatch(r"\d+(\.\d+)+", latest):
        old = tuple(map(int, installed.split(".")))
        new = tuple(map(int, latest.split(".")))
        return "UPDATE_AVAILABLE" if new > old else "VALIDATION_REQUIRED"
    return "VALIDATION_REQUIRED"


def record(conn, name, category, observation):
    """Append each attempt; retain last good values on failure/unknown."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT OR IGNORE INTO environment_components(name,category) VALUES (?,?)",
                 (name, category))
    conn.row_factory = sqlite3.Row
    old = dict(conn.execute("SELECT * FROM environment_components WHERE name=?", (name,)).fetchone())
    result = observation["result"]
    source = observation["source"]
    cursor = conn.execute("""INSERT INTO environment_checks
        (component,checked_at,source,installed_version_seen,latest_version_seen,result,
         raw_response_ref,detail,observation_json) VALUES (?,?,?,?,?,?,?,?,?)""",
        (name, now, source, observation.get("installed"), observation.get("latest"),
         result, observation.get("raw_response_ref"), observation.get("detail"),
         json.dumps(observation)))
    conn.execute("UPDATE environment_checks SET raw_response_ref=? WHERE id=? AND raw_response_ref IS NULL",
                 (f"sqlite:environment_checks/{cursor.lastrowid}/observation_json", cursor.lastrowid))
    if result == "ok":
        installed, latest = observation.get("installed"), observation.get("latest")
        status = status_for(installed, latest, old["recommended"], old["status"])
        conn.execute("""UPDATE environment_components SET installed=?,latest=?,status=?,
            stale=0,source=?,last_successful_check=?,detail=?,metadata=? WHERE name=?""",
            (installed, latest, status, source, now, observation.get("detail"),
             json.dumps(observation.get("metadata", {})), name))
    else:
        status = "CHECK_FAILED" if result == "check_failed" or old["last_successful_check"] else "UNKNOWN"
        if old["status"] == "HOLD":
            status = "HOLD"
        conn.execute("UPDATE environment_components SET status=?,stale=1,detail=? WHERE name=?",
                     (status, observation.get("detail"), name))


def command(args):
    # No shell and no browser-provided command. Call sites below are fixed probes.
    result = subprocess.run(args, capture_output=True, text=True, timeout=20, check=True)
    return result.stdout + result.stderr


def fetch(url):
    with urlopen(Request(url, headers={"User-Agent": "NOVA-environment-health"}), timeout=15) as response:
        return response.read(2_000_001).decode("utf-8")


def version(text):
    match = re.search(r"\b\d+\.\d+(?:\.\d+)*\b", text)
    if not match:
        raise ValueError("No recognizable version in probe response")
    return match.group()


def inventory(root: Path, settings: dict):
    """Named probes only. Unverified/manual sources are explicitly unknown."""
    python = str(Path.home() / "seestar-venv-312/bin/python")
    probes = {
        "NOVA": ("application", lambda: {
            "installed": command(["git", "-C", str(root), "rev-parse", "HEAD"]).strip(),
            "source": "local Git HEAD", "result": "ok"}),
        "Python system": ("runtime", lambda: {
            "installed": version(command(["/usr/bin/python3", "--version"])),
            "source": "system Python --version", "result": "ok"}),
        "Python processing venv": ("runtime", lambda: {
            "installed": version(command([python, "--version"])),
            "source": "processing venv --version", "result": "ok"}),
        "ASTAP": ("application", lambda: {
            "installed": command(["/opt/astap/astap_cli", "-h"]).splitlines()[0],
            "source": "ASTAP -h", "result": "ok"}),
        "Siril": ("application", lambda: {
            "installed": version(command(["siril-cli", "--version"])),
            "latest": version(re.search(r"Download SIRIL version[^<]+", fetch("https://siril.org/download/")).group()),
            "source": "siril-cli --version; https://siril.org/download/", "result": "ok"}),
        "RC-Astro CLI": ("application", rcastro_probe),
    }
    for name, package, category in (("SASpro", "setiastrosuitepro", "application"),
                                    ("RunPod SDK", "runpod", "runtime")):
        def package_probe(package=package):
            output = command([python, "-m", "pip", "show", package])
            installed = re.search(r"^Version: (.+)$", output, re.M).group(1)
            url = f"https://pypi.org/pypi/{package}/json"
            return {"installed": installed, "latest": json.loads(fetch(url))["info"]["version"],
                    "source": f"processing venv pip show; {url}", "result": "ok"}
        probes[name] = (category, package_probe)
    # These rows stay explicit rather than copying historical versions and
    # misrepresenting a workflow snapshot as a live observation.
    manual = {"PixInsight": "application", "PI third-party repositories": "application",
              "GraXpert": "application", "GraXpert background model": "ai_model",
              "GraXpert denoise model": "ai_model", "SASpro models": "ai_model",
              "PI filter definitions": "reference_data", "NOVA filter definitions": "reference_data",
              "SPCC catalog/config": "reference_data"}
    for name, category in manual.items():
        probes[name] = (category, lambda: {"result": "unknown", "source": "manual inventory",
            "detail": "No verified live probe configured; not inferred from workflow history."})
    def reference_probe():
        path = root / "nas_server/filter_curves/seestar_s50_lp_pixinsight.fits"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"result": "ok", "source": "tracked NOVA filter response file",
                "installed": "sha256:" + digest, "metadata": {"file": path.name},
                "detail": "File identity only; no claim of calibration approval or upstream currency."}
    probes["NOVA filter definitions"] = ("reference_data", reference_probe)
    probes["RC-Astro worker image"] = (
        "deployed_worker", lambda: worker_image_probe(root))
    for name in ("BlurXTerminator", "NoiseXTerminator", "StarXTerminator"):
        def model_probe(name=name):
            files = sorted((Path.home() / ".config/RC-Astro").glob(name + ".*.onnx"))
            if not files:
                return {"result": "unknown", "source": "local RC-Astro model files",
                        "detail": "No installed model files found."}
            hashes = {}
            for path in files:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                hashes[path.name] = digest.hexdigest()
            return {"result": "ok", "source": "local RC-Astro model files, SHA256",
                    "installed": ", ".join(hashes), "metadata": {"sha256": hashes},
                    "detail": "Installed files; does not identify the model chosen by a processing run."}
        probes[name] = ("ai_model", model_probe)
    return probes


def worker_image_probe(root: Path):
    """Read the checked-in deployment declaration; never query RunPod/registry."""
    path = root / "docker/rcastro-worker/deployed-image.json"
    document = json.loads(path.read_text())
    if document.get("schema_version") != 1:
        raise ValueError("unsupported deployed worker metadata schema")
    image = document["image"]
    stack = document["gpu_stack"]
    digest = image["digest"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("deployed worker digest must be an immutable sha256 identity")
    required_image = ("repository", "tag", "platform")
    required_stack = ("cuda", "cudnn", "pytorch", "onnx_runtime")
    if any(not image.get(key) for key in required_image):
        raise ValueError("deployed worker image metadata is incomplete")
    if any(not stack.get(key) for key in required_stack):
        raise ValueError("deployed worker GPU-stack metadata is incomplete")
    return {
        "result": "ok",
        "installed": digest,
        "source": "tracked RC-Astro worker deployment metadata",
        "metadata": {
            "image": {key: image[key] for key in required_image},
            "gpu_stack": {key: stack[key] for key in required_stack},
        },
        "detail": "Passive declaration only; no registry, RunPod API, or pod was queried.",
    }


def rcastro_probe():
    events = [json.loads(line) for line in command(["rc-astro", "--json", "update"]).splitlines() if line.strip()]
    event = next(item for item in events if item.get("topic") == "updateList")
    return {"result": "ok", "installed": event["current"], "latest": event["latest"],
            "source": "rc-astro --json update (no install)"}


def run_checks(conn, root, settings):
    migrate(conn)
    for name, (category, probe) in inventory(root, settings).items():
        try:
            observation = probe()
        except Exception as exc:
            # Do not persist command output or credentials from failed probes.
            observation = {"result": "check_failed", "source": name + " probe",
                           "detail": type(exc).__name__ + ": probe failed; prior result retained"}
        record(conn, name, category, observation)
    conn.commit()


CARD_CSS = """
.eh-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:.75rem;margin:.75rem 0 1.75rem}
.eh-card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:.85rem 1rem;display:flex;flex-direction:column;gap:.5rem}
.eh-card-head{display:flex;justify-content:space-between;align-items:flex-start;gap:.5rem}
.eh-name{font-weight:700;color:var(--text);word-break:break-word}
.eh-badge{flex:none;display:inline-block;padding:.15rem .55rem;border-radius:999px;font-size:.72rem;font-weight:700;letter-spacing:.02em;white-space:nowrap}
.eh-badge.current,.eh-badge.approved_update{background:rgba(63,185,80,.15);color:#3fb950}
.eh-badge.update_available{background:rgba(88,166,255,.15);color:#58a6ff}
.eh-badge.validation_required{background:rgba(210,165,40,.18);color:#d2a528}
.eh-badge.hold{background:rgba(139,148,158,.2);color:#c9d1d9}
.eh-badge.check_failed{background:rgba(248,81,73,.18);color:#f85149}
.eh-badge.unknown{background:rgba(139,148,158,.15);color:#8b949e}
.eh-stale{color:#d2a528;font-size:.72rem;font-weight:700}
.eh-row{display:flex;justify-content:space-between;gap:.75rem;font-size:.88rem}
.eh-row .eh-label{color:var(--text2);flex:none}
.eh-row .eh-val{text-align:right;word-break:break-word}
.eh-detail{font-size:.82rem;color:var(--text2);border-top:1px solid var(--border);padding-top:.45rem}
.eh-source{font-size:.75rem;color:var(--text2);border-top:1px solid var(--border);padding-top:.45rem}
.eh-card details{font-size:.78rem;color:var(--text2)}
.eh-card summary{cursor:pointer;color:var(--accent)}
.eh-card pre{white-space:pre-wrap;word-break:break-word;font-size:.72rem;background:var(--bg3);padding:.5rem;border-radius:6px;margin-top:.35rem}
"""


def render(conn):
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute("SELECT * FROM environment_components ORDER BY name")]
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        rows = []
    escape = lambda value: html.escape(str(value) if value is not None else "Unknown")
    body = '<h1>NOVA Environment Health</h1><p>Check automatically, upgrade deliberately. Read-only Phase 1; no install controls. Latest does not mean recommended.</p>'
    for category, title in GROUPS.items():
        body += f"<h2>{title}</h2><div class='eh-grid'>"
        for row in rows:
            if row["category"] != category:
                continue
            badge_class = re.sub(r"[^a-z_]", "", row["status"].lower())
            stale_mark = "<span class='eh-stale'>STALE</span>" if row["stale"] else ""
            body += f"<div class='eh-card'><div class='eh-card-head'><span class='eh-name'>{escape(row['name'])}</span>" \
                    f"<span><span class='eh-badge {badge_class}'>{escape(row['status'])}</span> {stale_mark}</span></div>"
            for label, key in (("Installed", "installed"), ("Latest", "latest"), ("Recommended", "recommended")):
                if key == "recommended" and row[key] is None:
                    continue
                body += f"<div class='eh-row'><span class='eh-label'>{label}</span><span class='eh-val'>{escape(row[key])}</span></div>"
            if row["detail"]:
                body += f"<div class='eh-detail'>{escape(row['detail'])}</div>"
            body += f"<div class='eh-source'>{escape(row['source'])}<br>{escape(row['last_successful_check'])}</div>"
            body += f"<details><summary>Identity metadata</summary><pre>{escape(row['metadata'])}</pre></details>"
            body += "</div>"
        body += "</div>"
    if not rows:
        body += "<p>No checks recorded. Run the documented environment checker first.</p>"
    return body
