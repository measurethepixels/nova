"""Engine-version regression canary for the SeeStar autoprocess pipeline.

PixInsight, Siril and SetiAstro Suite Pro are *pinned pipeline dependencies*:
a minor update can silently change a headless process, a property default, or a
behaviour with zero error output (PI 1.9.3 DrizzleIntegration no-ops in
automation mode; Siril 1.4.2 crashed on seqplatesolve, fixed in 1.4.3). This
harness freezes a metric fingerprint of a few known-good "golden" target finals
on the *current* engines, then — after an engine update and a re-run — diffs the
new finals against that frozen baseline and flags any drift.

Recommended workflow:
  1. BEFORE updating any engine, re-process the golden targets (normal queue) so
     each target's latest run reflects current frames on the current engines.
  2. python -m nas_server.canary snapshot   # freezes critiques/golden_baselines.json
  3. Update the engine to a SIDE-BY-SIDE path; keep the old binary for rollback.
  4. Re-process the golden targets on the new engine.
  5. python -m nas_server.canary check      # diffs latest runs vs the snapshot,
                                            # prints a drift table + engine delta,
                                            # exits non-zero on drift.

Metric (self-contained — does NOT import the gitignored process-critique helper,
so it works on the laptop worker too): every final is normalised by its own
99.99th percentile (PI finals are raw-scale ~1e8; stretch finals are already in
[0,1]) and clipped to [0,1], then corner-median sky, corner-σ grain, and a
percentile ladder are taken. Normalising first makes engines comparable on tonal
SHAPE rather than absolute scale — the same gotcha documented in fits_stats.py /
_compute_stretch_stats.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits

_REPO = Path(__file__).resolve().parent.parent
_MANIFEST = _REPO / "critiques" / "golden_targets.json"
_BASELINE = _REPO / "critiques" / "golden_baselines.json"

# Per-object-type sky-background bands (informational flag only; the diff is on
# raw metric deltas). Mirrors _compute_stretch_stats() in auto_process.py.
_TARGET_BANDS = {
    "galaxy": (0.05, 0.08), "emission_nebula": (0.06, 0.16),
    "reflection_nebula": (0.06, 0.11), "planetary_nebula": (0.05, 0.09),
    "supernova_remnant": (0.05, 0.13), "globular_cluster": (0.04, 0.08),
    "open_cluster": (0.04, 0.08), "nebula": (0.06, 0.14),
}
_PCTS = [50, 80, 95, 99, 99.9]
_PLABELS = ["p50", "p80", "p95", "p99", "p99.9"]
_DEFAULT_TOL = {"sky_bg": 0.015, "bg_noise": 0.020, "pct": 0.040}


def _library_root() -> Path:
    try:
        from nas_server import config
        s = config.load_settings() if hasattr(config, "load_settings") else {}
        return Path(s.get("seestar_library_path", "/mnt/nas_data/SeeStar"))
    except Exception:
        return Path("/mnt/nas_data/SeeStar")


# ---------------------------------------------------------------------------
# Engine version detection
# ---------------------------------------------------------------------------

def _run(cmd, env=None, timeout=25) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return f"__error__ {e}"


def detect_tool_versions() -> dict:
    """Probe the four engine binaries/packages. Each returns 'unknown' on failure;
    every probe is time-boxed so the canary never hangs on a broken install."""
    import os
    out = {}

    # Siril (native CLI)
    txt = _run(["siril-cli", "--version"])
    m = re.search(r"siril\s+(\d+\.\d+\.\d+)", txt, re.I)
    out["siril"] = m.group(1) if m else "unknown"

    # PixInsight Core — needs its own lib dir on LD_LIBRARY_PATH.
    pi_bin = "/opt/PixInsight/bin/PixInsight"
    try:
        from nas_server import pixinsight as _pi
        pi_bin = getattr(_pi, "PI_BIN", pi_bin) or pi_bin
    except Exception:
        pass
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = str(Path(pi_bin).parent / "lib")
    txt = _run([pi_bin, "--version"], env=env, timeout=40)
    m = re.search(r"PixInsight Core\s+([\d.]+(?:\s+\w+)?)", txt)
    out["pixinsight"] = m.group(1).strip() if m else "unknown"

    # SetiAstro Suite Pro — installed Python package in the venv.
    try:
        import importlib.metadata as _md
        out["setiastrosuitepro"] = _md.version("setiastrosuitepro")
    except Exception:
        out["setiastrosuitepro"] = "unknown"

    # GraXpert — standalone binary; prints version to stderr.
    gx = Path.home() / "tools" / "graxpert" / "GraXpert-linux" / "GraXpert"
    if gx.exists():
        txt = _run([str(gx), "--version"], timeout=40)
        m = re.search(r"GraXpert version:\s*([\d.]+(?:\s+release:\s*\w+)?)", txt)
        out["graxpert"] = (m.group(1).strip() if m else "unknown")
    else:
        out["graxpert"] = "unknown"

    return out


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def _measure(path: Path) -> dict:
    """Per-image-normalised corner sky + grain + percentile ladder. See module docstring."""
    d = fits.getdata(str(path)).astype(np.float64)
    if d.ndim == 3 and d.shape[0] in (1, 3):     # (C,H,W) -> (H,W,C)
        d = np.moveaxis(d, 0, -1)
    hi = float(np.percentile(d, 99.99))
    raw_scale = hi > 1.5
    d01 = np.clip(d / hi, 0.0, 1.0) if hi > 1e-12 else np.clip(d, 0, 1)
    pcts = dict(zip(_PLABELS, (float(v) for v in np.percentile(d01.ravel(), _PCTS))))
    g = d01.mean(axis=-1) if d01.ndim == 3 else d01
    h_, w_ = g.shape[:2]
    m = max(h_ // 20, w_ // 20, 50)
    corners = np.concatenate([
        g[:m, :m].ravel(), g[:m, -m:].ravel(),
        g[-m:, :m].ravel(), g[-m:, -m:].ravel(),
    ])
    return {
        "sky_bg": float(np.median(corners)),
        "bg_noise": float(np.std(corners)),
        "pcts": pcts,
        "raw_scale": raw_scale,
    }


def _final_fits(run_dir: Path) -> Path | None:
    for name in ("22_final.fit", "22_final.fits"):
        p = run_dir / name
        if p.exists():
            return p
    cands = sorted(run_dir.glob("*_final.fit")) + sorted(run_dir.glob("*final*.fit"))
    return cands[0] if cands else None


def _runs_root(target: str) -> Path:
    return _library_root() / target / "_processed" / "runs"


def _latest_run(target: str) -> Path | None:
    root = _runs_root(target)
    if not root.exists():
        return None
    runs = sorted((d for d in root.iterdir() if d.is_dir() and _final_fits(d)),
                  key=lambda d: d.name)
    return runs[-1] if runs else None


# ---------------------------------------------------------------------------
# Manifest / baseline IO
# ---------------------------------------------------------------------------

def _load_manifest() -> dict:
    if not _MANIFEST.exists():
        print(f"error: manifest not found at {_MANIFEST}", file=sys.stderr)
        sys.exit(2)
    return json.loads(_MANIFEST.read_text())


def _tol(manifest: dict) -> dict:
    t = dict(_DEFAULT_TOL)
    t.update(manifest.get("tolerances") or {})
    return t


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_versions(_args) -> int:
    v = detect_tool_versions()
    print("Detected engine versions:")
    for k in ("pixinsight", "siril", "setiastrosuitepro", "graxpert"):
        print(f"  {k:18s} {v.get(k, 'unknown')}")
    return 0


def cmd_snapshot(_args) -> int:
    manifest = _load_manifest()
    engines = detect_tool_versions()
    baselines = {}
    for e in manifest.get("targets", []):
        target, otype, run = e["target"], e.get("object_type", "galaxy"), e["baseline_run"]
        run_dir = _runs_root(target) / run
        final = _final_fits(run_dir)
        if not final:
            print(f"  SKIP {target}: no final in {run_dir}", file=sys.stderr)
            continue
        m = _measure(final)
        baselines[target] = {"object_type": otype, "run": run,
                             "final": str(final), "metrics": m}
        print(f"  froze {target:8s} ({otype}) run={run}  "
              f"sky_bg={m['sky_bg']:.4f} grain={m['bg_noise']:.4f}")
    out = {
        "snapshot_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engines": engines,
        "tolerances": _tol(manifest),
        "baselines": baselines,
    }
    _BASELINE.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {len(baselines)} baselines -> {_BASELINE}")
    print(f"Engines: " + ", ".join(f"{k}={v}" for k, v in engines.items()))
    return 0


def _diff_row(label: str, base: float, cand: float, tol: float) -> tuple[str, bool]:
    delta = cand - base
    drift = abs(delta) > tol
    flag = "  DRIFT" if drift else ""
    return (f"  {label:9s} base={base:8.4f}  cand={cand:8.4f}  "
            f"Δ={delta:+8.4f}  (tol {tol:.3f}){flag}"), drift


def cmd_check(args) -> int:
    if not _BASELINE.exists():
        print(f"error: no snapshot at {_BASELINE} — run `snapshot` first", file=sys.stderr)
        return 2
    snap = json.loads(_BASELINE.read_text())
    tol = snap.get("tolerances", _DEFAULT_TOL)
    cur_engines = detect_tool_versions()
    snap_engines = snap.get("engines", {})

    print("Engine versions (snapshot -> current):")
    changed = []
    for k in ("pixinsight", "siril", "setiastrosuitepro", "graxpert"):
        a, b = snap_engines.get(k, "?"), cur_engines.get(k, "?")
        mark = "  <-- CHANGED" if a != b else ""
        if a != b:
            changed.append(k)
        print(f"  {k:18s} {a}  ->  {b}{mark}")
    print()

    targets = snap["baselines"]
    if args.target:
        targets = {args.target: snap["baselines"][args.target]} if args.target in snap["baselines"] else {}
        if not targets:
            print(f"error: {args.target} not in snapshot", file=sys.stderr)
            return 2

    any_drift = False
    for target, b in targets.items():
        if args.run:
            cand_dir = Path(args.run) if "/" in args.run else _runs_root(target) / args.run
        else:
            cand_dir = _latest_run(target)
        print(f"== {target} ({b['object_type']}) ==")
        if not cand_dir or not _final_fits(cand_dir):
            print(f"  no candidate run found under {_runs_root(target)}\n")
            any_drift = True
            continue
        same = cand_dir.name == b["run"]
        print(f"  baseline run : {b['run']}")
        print(f"  candidate run: {cand_dir.name}" + ("   (== baseline; no new run yet)" if same else ""))
        bm = b["metrics"]
        cm = _measure(_final_fits(cand_dir))
        rows = []
        line, d1 = _diff_row("sky_bg", bm["sky_bg"], cm["sky_bg"], tol["sky_bg"]); rows.append(line)
        line, d2 = _diff_row("grain", bm["bg_noise"], cm["bg_noise"], tol["bg_noise"]); rows.append(line)
        pdrift = False
        for pl in _PLABELS:
            line, dp = _diff_row(pl, bm["pcts"][pl], cm["pcts"][pl], tol["pct"]); rows.append(line)
            pdrift = pdrift or dp
        drift = d1 or d2 or pdrift
        any_drift = any_drift or drift
        print("\n".join(rows))
        print(f"  -> {'DRIFT' if drift else 'OK'}\n")

    if changed and not any_drift:
        print(f"VERDICT: engines changed ({', '.join(changed)}) but all golden finals within "
              f"tolerance — safe to promote.")
    elif any_drift:
        print("VERDICT: DRIFT detected — investigate before promoting the engine update.")
    else:
        print("VERDICT: no engine change and no drift.")
    return 1 if any_drift else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="SeeStar engine-update regression canary.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("versions", help="print detected engine versions")
    sub.add_parser("snapshot", help="freeze golden baselines on the current engines")
    pc = sub.add_parser("check", help="diff golden finals vs the frozen snapshot")
    pc.add_argument("--target", help="check a single target (default: all in snapshot)")
    pc.add_argument("--run", help="explicit candidate run dir or run name (default: latest)")
    args = ap.parse_args()
    return {"versions": cmd_versions, "snapshot": cmd_snapshot, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
