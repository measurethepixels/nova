"""
Per-sub plate-solve capture.

Stores the TRUE solved sky position of each light frame in the DB so subs can be
analysed for alignment (mis-pointed / spoofed frames) before stacking, and so
association can key on real position rather than the header OBJECT name (which can
collide, e.g. M 53/M 56, or be spoofed by pre-EQ ALP coords).

Three entry points, all writing light_files.solved_* via update_light_frame_solve:
  - harvest_solves(process_dir, ordered_sources): read the WCS the stack pipeline
    already wrote (near-zero cost; called by stacker after seqplatesolve).
  - solve_subs(file_paths): stage + run the platesolve-only SSF, then harvest
    (used by idle eager-solve and the on-demand endpoint).
  - flag_alignment_outliers(target): mark subs whose solved position sits far from
    the target/panel median (the panel-11 pre-EQ spoof case).
"""
import logging
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

PLATESOLVE_ONLY_SSF = Path(__file__).parent / "seestar_platesolve_only.ssf"
NAS_WORK_ROOT = Path("/mnt/nas_data/_stack_work")
SOLVE_TIMEOUT_S = 1800  # 30 min cap for a batch solve


def _solve_from_header(header) -> tuple | None:
    """Extract (ra_deg, dec_deg, rot_deg, scale_arcsec_px) at the image center
    from a solved frame header, or None if it has no usable celestial WCS."""
    try:
        import numpy as np
        from astropy.wcs import WCS
        # Frames are 3-layer RGB; SIP distortion needs a 2-D WCS, so select axes 1,2.
        try:
            w = WCS(header, naxis=2)
        except Exception:
            w = WCS(header).celestial
        if not w.has_celestial:
            return None
        w = w.celestial
        nx = header.get("NAXIS1") or (w.pixel_shape[0] if w.pixel_shape else None)
        ny = header.get("NAXIS2") or (w.pixel_shape[1] if w.pixel_shape else None)
        if not nx or not ny:
            return None
        sky = w.pixel_to_world((nx - 1) / 2.0, (ny - 1) / 2.0)
        ra = float(sky.ra.deg)
        dec = float(sky.dec.deg)
        if not (math.isfinite(ra) and math.isfinite(dec)):
            return None
        cd = w.pixel_scale_matrix  # 2x2, deg/pixel
        scale = float(np.sqrt(np.abs(cd[0, 0] * cd[1, 1] - cd[0, 1] * cd[1, 0])) * 3600.0)
        rot = float(math.degrees(math.atan2(cd[1, 0], cd[0, 0])))
        return ra, dec, rot, scale
    except Exception as e:
        log.debug(f"[solve] header WCS parse failed: {e}")
        return None


def harvest_solves(process_dir, ordered_sources, mark_failed: bool = False) -> int:
    """Read solved WCS from process/light_NNNNN.fit and write it to the matching
    source rows. ordered_sources[k] is the source path that became light_{k+1}.

    mark_failed: when True (standalone solve, we know a solve was attempted), a frame
    with no usable WCS is stamped solve_status='failed' so it isn't retried forever.
    When False (harvesting a stack where seqplatesolve may not have run, e.g. a
    non-maxframing SSF), such frames are SKIPPED so idle/on-demand can solve them later.

    Returns the number of frames written.
    """
    from nas_server.database import update_light_frame_solve
    process_dir = Path(process_dir)
    if not process_dir.is_dir():
        return 0
    from astropy.io import fits as afits
    n = 0
    for idx, src in enumerate(ordered_sources, start=1):
        fp = process_dir / f"light_{idx:05d}.fit"
        if not fp.exists():
            continue
        try:
            hdr = afits.getheader(str(fp))
        except Exception:
            continue
        sol = _solve_from_header(hdr)
        if sol is None:
            if mark_failed:
                update_light_frame_solve(str(src), None, None, solve_status="failed")
                n += 1
            continue
        ra, dec, rot, scale = sol
        update_light_frame_solve(str(src), ra, dec, rot, scale, solve_status="ok")
        n += 1
    return n


def solve_batch_collect(file_paths) -> dict:
    """Plate-solve subs standalone and RETURN the per-frame WCS — no DB writes.

    For the distributed-solve worker (laptop): stages copies, runs the platesolve-only
    SSF, reads each solved header, and returns
      {file_path: {"solved_ra","solved_dec","solved_rot","solved_scale","solve_status"}}
    with solve_status 'ok' (usable WCS) or 'failed' (no WCS / not produced). The caller
    POSTs this back so the VM is the single writer. Mirrors solve_subs but keeps the DB
    untouched on the worker node.
    """
    from astropy.io import fits as afits
    file_paths = [Path(p) for p in file_paths if Path(p).exists()]
    if not file_paths:
        return {}
    root = NAS_WORK_ROOT if NAS_WORK_ROOT.is_dir() else Path(tempfile.gettempdir())
    work_dir = Path(tempfile.mkdtemp(prefix="solve_", dir=str(root)))
    light = work_dir / "light"
    light.mkdir(parents=True, exist_ok=True)
    out = {}
    try:
        ordered = sorted(file_paths)
        for i, src in enumerate(ordered, start=1):
            shutil.copy2(src, light / f"light_{i:06d}.fit")
        log.info(f"[solve] collecting {len(ordered)} subs in {work_dir}")
        proc = subprocess.Popen(
            ["siril-cli", "-s", str(PLATESOLVE_ONLY_SSF), "-d", str(work_dir)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            proc.communicate(timeout=SOLVE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            log.error("[solve] siril platesolve timed out")
        process_dir = work_dir / "process"
        for idx, src in enumerate(ordered, start=1):
            fp = process_dir / f"light_{idx:05d}.fit"
            sol = None
            if fp.exists():
                try:
                    sol = _solve_from_header(afits.getheader(str(fp)))
                except Exception:
                    sol = None
            if sol is None:
                out[str(src)] = {"solved_ra": None, "solved_dec": None,
                                 "solved_rot": None, "solved_scale": None,
                                 "solve_status": "failed"}
            else:
                ra, dec, rot, scale = sol
                out[str(src)] = {"solved_ra": ra, "solved_dec": dec,
                                 "solved_rot": rot, "solved_scale": scale,
                                 "solve_status": "ok"}
        ok = sum(1 for v in out.values() if v["solve_status"] == "ok")
        log.info(f"[solve] collected {ok}/{len(ordered)} solves")
        return out
    finally:
        subprocess.Popen(["rm", "-rf", str(work_dir)])


def solve_subs_astap(file_paths, work_root=None) -> dict:
    """Plate-solve subs with ASTAP (D20 DB) — fast VM-side path for the idle worker.

    ~0.2-1 s per sub vs Siril's tens-of-seconds batches, so the 100k+ sub backlog is
    drainable on the VM without the laptop. SAFETY (2026-07-02 ingest-solve lesson):
    each sub is COPIED to a tmp dir and solved there (-update on the copy only) —
    library FITS are NEVER modified; results go to the DB alone via
    update_light_frame_solve. Header hints seed the solve; a hint-less/spoofed header
    (pre-EQ southern captures) falls back to blind inside astap_solve.
    Returns {file_path: solve_status}.
    """
    from nas_server.database import update_light_frame_solve
    from nas_server.seti_astro import astap_solve
    from astropy.io import fits as _f
    file_paths = [Path(p) for p in file_paths if Path(p).exists()]
    if not file_paths:
        return {}
    work_dir = Path(tempfile.mkdtemp(prefix="astapsolve_"))
    out: dict = {}
    try:
        for src in sorted(file_paths):
            tmp = work_dir / src.name
            try:
                shutil.copy2(src, tmp)
                r = astap_solve(tmp, search_deg=10, timeout=60)
                sol = _solve_from_header(_f.getheader(str(tmp), memmap=False))                     if r.get("ok") else None
                if sol:
                    ra, dec, rot, scale = sol
                    update_light_frame_solve(str(src), ra, dec, rot, scale,
                                             solve_status="ok")
                    out[str(src)] = "ok"
                else:
                    update_light_frame_solve(str(src), None, None,
                                             solve_status="failed")
                    out[str(src)] = "failed"
            except Exception as e:
                log.debug(f"[solve] astap sub failed {src.name}: {e}")
                update_light_frame_solve(str(src), None, None, solve_status="failed")
                out[str(src)] = "failed"
            finally:
                tmp.unlink(missing_ok=True)
        n_ok = sum(1 for v in out.values() if v == "ok")
        log.info(f"[solve] astap solved {n_ok}/{len(out)} subs")
        for t in {_target_of(p) for p in file_paths}:
            if t:
                try:
                    flag_alignment_outliers(t)
                except Exception as e:
                    log.debug(f"[solve] outlier flag failed for {t}: {e}")
        return out
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def solve_subs(file_paths, work_root=None) -> dict:
    """Plate-solve the given subs standalone and persist the result.

    Stages copies into a temp work dir, runs the platesolve-only SSF, harvests the
    per-frame WCS, then flags outliers per target. Returns {file_path: status}.
    """
    file_paths = [Path(p) for p in file_paths if Path(p).exists()]
    if not file_paths:
        return {}
    root = Path(work_root) if work_root else (
        NAS_WORK_ROOT if NAS_WORK_ROOT.is_dir() else Path(tempfile.gettempdir()))
    work_dir = Path(tempfile.mkdtemp(prefix="solve_", dir=str(root)))
    light = work_dir / "light"
    light.mkdir(parents=True, exist_ok=True)
    try:
        ordered = sorted(file_paths)
        for i, src in enumerate(ordered, start=1):
            shutil.copy2(src, light / f"light_{i:06d}.fit")
        log.info(f"[solve] solving {len(ordered)} subs in {work_dir}")
        proc = subprocess.Popen(
            ["siril-cli", "-s", str(PLATESOLVE_ONLY_SSF), "-d", str(work_dir)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            proc.communicate(timeout=SOLVE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            log.error("[solve] siril platesolve timed out")
        n = harvest_solves(work_dir / "process", ordered, mark_failed=True)
        log.info(f"[solve] harvested {n}/{len(ordered)} solves")
        # Any source we staged but didn't harvest -> mark failed so we don't retry forever.
        from nas_server.database import update_light_frame_solve
        process_dir = work_dir / "process"
        for idx, src in enumerate(ordered, start=1):
            if not (process_dir / f"light_{idx:05d}.fit").exists():
                update_light_frame_solve(str(src), None, None, solve_status="failed")
        result = _read_back_status(ordered)
        for t in {_target_of(p) for p in ordered}:
            if t:
                try:
                    flag_alignment_outliers(t)
                except Exception as e:
                    log.debug(f"[solve] outlier flag failed for {t}: {e}")
        return result
    finally:
        subprocess.Popen(["rm", "-rf", str(work_dir)])


def _target_of(file_path) -> str | None:
    from nas_server.database import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT target FROM light_files WHERE file_path=?", (str(file_path),)
        ).fetchone()
        return row[0] if row else None


def _read_back_status(paths) -> dict:
    from nas_server.database import get_conn
    out = {}
    with get_conn() as conn:
        for p in paths:
            row = conn.execute(
                "SELECT solve_status FROM light_files WHERE file_path=?", (str(p),)
            ).fetchone()
            out[str(p)] = row[0] if row else None
    return out


def flag_alignment_outliers(target: str, max_sep_deg: float = 2.0) -> dict:
    """Mark subs whose solved position sits more than max_sep_deg from the median
    solved position of their own (target, panel) group as solve_status='outlier'.

    Grouping is per source target so each mosaic panel is judged against its own
    siblings, not the whole mosaic. Does NOT set exclude — that stays a user/stack
    decision so stack membership (and critique comparability) is never changed
    silently. Returns a summary dict.
    """
    from nas_server.database import get_conn, update_light_frame_solve
    names = _expand_targets(target)
    with get_conn() as conn:
        ph = ",".join("?" * len(names))
        rows = [dict(r) for r in conn.execute(
            f"SELECT file_path, target, solved_ra, solved_dec, solved_rot, "
            f"solved_scale, solve_status FROM light_files "
            f"WHERE target IN ({ph}) AND solve_status IN ('ok','outlier') "
            f"AND solved_ra IS NOT NULL", tuple(names)).fetchall()]
    if not rows:
        return {"target": target, "solved": 0, "outliers": 0, "groups": {}}

    groups: dict = {}
    for r in rows:
        groups.setdefault(r["target"], []).append(r)

    summary = {"target": target, "solved": len(rows), "outliers": 0, "groups": {}}
    for gname, grp in groups.items():
        med_ra = _circular_median([r["solved_ra"] for r in grp])
        med_dec = sorted(r["solved_dec"] for r in grp)[len(grp) // 2]
        out_n = 0
        for r in grp:
            sep = _angular_sep(r["solved_ra"], r["solved_dec"], med_ra, med_dec)
            want = "outlier" if sep > max_sep_deg else "ok"
            if want != r["solve_status"]:
                update_light_frame_solve(
                    r["file_path"], r["solved_ra"], r["solved_dec"],
                    r["solved_rot"], r["solved_scale"], solve_status=want)
            if want == "outlier":
                out_n += 1
        summary["groups"][gname] = {
            "n": len(grp), "outliers": out_n,
            "center_ra": round(med_ra, 4), "center_dec": round(med_dec, 4)}
        summary["outliers"] += out_n
    return summary


def _expand_targets(target: str) -> set:
    """target + its confirmed associations + any mosaic panels grouped under it."""
    from nas_server.database import get_conn
    names = {target}
    with get_conn() as conn:
        row = conn.execute(
            "SELECT association FROM targets WHERE target=?", (target,)).fetchone()
        if row and row[0]:
            names |= {t.strip() for t in row[0].split(",") if t.strip()}
        try:
            for (n,) in conn.execute(
                    "SELECT target FROM targets WHERE mosaic_association=?", (target,)):
                names.add(n)
        except Exception:
            pass  # column may not exist on older schemas
    return names


def solve_target(target: str, batch: int = 40, max_batches: int = 2000) -> dict:
    """Solve every unsolved sub for a target (and its panels/associations), in
    batches, then return the alignment summary. Used by the on-demand endpoint."""
    from nas_server.database import get_unsolved_light_frames, update_light_frame_solve
    names = _expand_targets(target)
    solved = 0
    for name in names:
        for _ in range(max_batches):
            rows = get_unsolved_light_frames(target=name, limit=batch)
            if not rows:
                break
            paths = [r["file_path"] for r in rows if Path(r["file_path"]).exists()]
            for r in rows:
                if not Path(r["file_path"]).exists():
                    update_light_frame_solve(r["file_path"], None, None, solve_status="failed")
            if not paths:
                break
            solve_subs(paths)
            solved += len(paths)
        try:
            flag_alignment_outliers(name)
        except Exception as e:
            log.debug(f"[solve] outlier flag failed for {name}: {e}")
    return {"solved_now": solved, **alignment_summary(target)}


def alignment_summary(target: str) -> dict:
    """Read-only QA report: per (target/panel) group, the solved-position cluster
    center, how many subs solved/failed, and which subs are off-pointing outliers.
    Keys on TRUE solved position, so it is robust to spoofed/colliding header names."""
    from nas_server.database import get_conn
    names = _expand_targets(target)
    with get_conn() as conn:
        ph = ",".join("?" * len(names))
        rows = [dict(r) for r in conn.execute(
            f"SELECT file_path, target, solved_ra, solved_dec, solved_rot, "
            f"solve_status FROM light_files "
            f"WHERE target IN ({ph}) AND solve_status IS NOT NULL",
            tuple(names)).fetchall()]
    groups: dict = {}
    for r in rows:
        groups.setdefault(r["target"], []).append(r)
    out = {"target": target, "groups": {},
           "total_solved": 0, "total_failed": 0, "total_outliers": 0}
    for gname, grp in groups.items():
        ok = [r for r in grp if r["solve_status"] in ("ok", "outlier")
              and r["solved_ra"] is not None]
        failed = sum(1 for r in grp if r["solve_status"] == "failed")
        outliers = [r["file_path"] for r in grp if r["solve_status"] == "outlier"]
        center_ra = round(_circular_median([r["solved_ra"] for r in ok]), 4) if ok else None
        center_dec = (round(sorted(r["solved_dec"] for r in ok)[len(ok) // 2], 4)
                      if ok else None)
        # Field rotation: solved_rot is each sub's on-sky position angle. The spread
        # shows how rotated the subs are relative to each other (large for alt-az
        # SeeStar captures — registration corrects this at stack time).
        rots = [r["solved_rot"] for r in ok if r["solved_rot"] is not None]
        rot_median = rot_spread = None
        if rots:
            rot_median = _circular_median([x % 360.0 for x in rots])
            devs = [abs(_wrap180(x - rot_median)) for x in rots]
            rot_median = round(_wrap180(rot_median), 2)
            rot_spread = round(max(devs), 2)  # max deviation from median, degrees
        out["groups"][gname] = {
            "solved": len(ok), "failed": failed, "outliers": len(outliers),
            "center_ra": center_ra, "center_dec": center_dec,
            "rot_median": rot_median, "rot_spread": rot_spread,
            "outlier_files": outliers}
        out["total_solved"] += len(ok)
        out["total_failed"] += failed
        out["total_outliers"] += len(outliers)
    return out


def _angular_sep(ra1, dec1, ra2, dec2) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    cs = (math.sin(d1) * math.sin(d2)
          + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cs))))


def _wrap180(deg: float) -> float:
    """Wrap an angle to (-180, 180]."""
    d = (deg + 180.0) % 360.0 - 180.0
    return d if d != -180.0 else 180.0


def _circular_median(ras) -> float:
    """True median RA, safe across the 0/360 wrap and robust to outliers (unlike a
    vector mean, which a single far-off spoof frame would drag off the cluster)."""
    s = sorted(r % 360.0 for r in ras)
    if s and (s[-1] - s[0]) > 180.0:  # cluster straddles 0/360 — unwrap the low side
        s = sorted((r if r >= 180.0 else r + 360.0) for r in s)
    return s[len(s) // 2] % 360.0 if s else 0.0
