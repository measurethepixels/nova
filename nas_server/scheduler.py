"""
Periodic background jobs that run on the NAS.

Current jobs:
  - hourly_scan: Re-scans the library to catch any manually added files
  - nightly_plan: Placeholder for AI target planning (Phase 6)
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from nas_server.config import settings
from nas_server import database

log = logging.getLogger(__name__)

# Files that failed to open — logged once, silenced on subsequent scans.
_scan_skip: set[str] = set()


def hourly_scan():
    """Re-scan the library directory for any FITS files not yet in the DB."""
    import os
    from astropy.io import fits as afits

    library = settings["seestar_library_path"]
    if not os.path.isdir(library):
        log.warning(f"Library path not found: {library}")
        return

    log.info("Running hourly library scan")
    for target_name in os.listdir(library):
        target_path = os.path.join(library, target_name)
        if not os.path.isdir(target_path):
            continue
        for root, _, files in os.walk(target_path):
            for fname in files:
                if not (fname.lower().endswith(".fit") or fname.lower().endswith(".fits")):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with afits.open(fpath) as hdul:
                        h = hdul[0].header
                        obj = h.get("OBJECT", target_name)
                        date = h.get("DATE-OBS", "")
                        exptime = float(h.get("EXPTIME", 0))
                        ra = h.get("RA")
                        dec = h.get("DEC")
                        filt = h.get("FILTER", "")
                    if fname.lower().startswith("stacked"):
                        database.upsert_stacked_file(
                            target=obj, file_name=fname, file_path=fpath,
                            exposure_time=exptime, date=date,
                            number_of_subs=int(h.get("STACKCNT", 0)),
                            latitude=h.get("SITELAT"), longitude=h.get("SITELONG"),
                            ra=ra, dec=dec, filter_type=filt,
                        )
                    elif fname.lower().startswith("light"):
                        database.upsert_light_file(
                            target=obj, date=date, exposure_time=exptime,
                            file_name=fname, file_path=fpath,
                            ra=ra, dec=dec, filter_type=filt,
                        )
                except Exception as e:
                    if fpath not in _scan_skip:
                        log.error(f"Scan error on {fpath}: {e} (will be silenced on future scans)")
                        _scan_skip.add(fpath)

    log.info("Hourly scan complete")

    # Keep _processed/ index fresh so the manual-processing folder-review queue
    # surfaces new candidate folders. Henry flags the final file himself — we do
    # NOT auto-capture/grade every file (most are intermediates).
    try:
        from nas_server.processed_scanner import scan_processed_folders
        scan_processed_folders(library, settings["db_path"])
    except Exception as e:
        log.error(f"Processed-folder scan failed during hourly scan: {e}")


# 7Timer cloudcover index → approximate midpoint %
_CLOUD_PCT = {1: 3, 2: 13, 3: 25, 4: 38, 5: 50, 6: 63, 7: 75, 8: 88, 9: 97}
# 7Timer wind10m speed index → label
_WIND_LABEL = {
    1: "calm", 2: "light", 3: "gentle", 4: "moderate",
    5: "fresh", 6: "strong", 7: "near-gale", 8: "storm",
}
_PREC_LABEL = {"none": None, "rain": "rain", "snow": "snow", "frzr": "freezing rain", "icep": "sleet"}


def _get_weather(lat: float, lon: float) -> tuple[bool, str]:
    """Return (is_clear, summary_str).
    is_clear=True → proceed with plan. Fails open on API error."""
    import urllib.request
    import json as _json
    url = (f"http://www.7timer.info/bin/api.pl"
           f"?lon={lon}&lat={lat}&product=astro&output=json")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = _json.loads(resp.read())
        series = data["dataseries"][:3]  # next 9 hrs (3h steps)

        clouds = [d.get("cloudcover", 1) for d in series]
        avg_cloud = sum(clouds) / len(clouds)
        cloud_pct = _CLOUD_PCT.get(round(avg_cloud), int(avg_cloud * 11))

        winds = [d.get("wind10m", {}).get("speed", 1) for d in series]
        max_wind = max(winds)
        wind_label = _WIND_LABEL.get(max_wind, f"speed {max_wind}")

        prec_types = [d.get("prec_type", "none") for d in series]
        prec = next((p for p in prec_types if p != "none"), "none")
        prec_label = _PREC_LABEL.get(prec)

        lifted = [d.get("lifted_index", 10) for d in series]
        unstable = min(lifted) < -2

        parts = [f"{cloud_pct}% clouds"]
        if max_wind >= 5:
            parts.append(f"{wind_label} winds")
        if prec_label:
            parts.append(prec_label)
        if unstable:
            parts.append("unstable air")
        summary = " · ".join(parts)

        is_clear = avg_cloud < 4.0
        log.info(f"[scheduler] 7Timer: {summary} (avg_cloud={avg_cloud:.1f}, clear={is_clear})")
        return is_clear, summary
    except Exception as e:
        log.warning(f"[scheduler] weather check failed (fail-open): {e}")
        return True, ""


def _generate_plan_chart(results: list, schedule: list, date_str: str) -> bytes | None:
    """Return PNG bytes of an altitude chart with schedule slots highlighted."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import io

        # Use scheduled targets if available, else top 5 results
        if schedule:
            sched_targets = {s["target"] for s in schedule}
            top = [r for r in results if r["target"] in sched_targets and r.get("alt_curve")]
        else:
            top = [r for r in results[:5] if r.get("alt_curve")]
        if not top:
            return None

        times = [p[0] for p in top[0]["alt_curve"]]
        n = len(times)
        xtick_idx = [i for i, t in enumerate(times) if t.endswith(":00")]

        colors = ["#58a6ff", "#3fb950", "#e3b341", "#d2a8ff", "#ff7b72",
                  "#ffa657", "#79c0ff", "#56d364"]
        target_color = {r["target"]: colors[i % len(colors)] for i, r in enumerate(top)}

        # Build schedule slot lookup
        sched_by_target = {s["target"]: s for s in (schedule or [])}

        fig, ax = plt.subplots(figsize=(9, 4))
        fig.patch.set_facecolor("#0d1117")
        ax.set_facecolor("#161b22")

        has_custom_horizon = any(r.get("horizon_curve") for r in top)

        for r in top:
            alts = [p[1] for p in r["alt_curve"]]
            col = target_color[r["target"]]
            slot = sched_by_target.get(r["target"])
            hcurve = r.get("horizon_curve")

            # Ghost dim curve
            ax.plot(range(n), alts, color=col, linewidth=1, alpha=0.25)

            # Custom horizon line for this target (thin dashed, same color)
            if hcurve:
                ax.plot(range(n), hcurve, color=col, linewidth=0.8,
                        linestyle=":", alpha=0.55)
                # Shade blocked zone between horizon and 0 for this target
                ax.fill_between(range(n), 0, hcurve, color=col, alpha=0.04)

            # Bright thick segment for scheduled slot
            if slot:
                si, ei = slot["start_idx"], min(slot["end_idx"], n - 1)
                ax.plot(range(si, ei + 1), alts[si:ei + 1],
                        color=col, linewidth=3, alpha=0.9,
                        label=f"{r['target']} {slot['start_hhmm']}–{slot['end_hhmm']}")
                # Slot shading
                ax.axvspan(si, ei, alpha=0.08, color=col)
            else:
                ax.plot(range(n), alts, color=col, linewidth=1.5, alpha=0.6, label=r["target"])

        # 30° reference only if no custom horizon (custom horizon makes it redundant)
        if not has_custom_horizon:
            ax.axhline(30, color="#8b949e", linestyle="--", linewidth=0.8, alpha=0.5)
        else:
            # Subtle horizon label in bottom-right corner
            ax.text(0.99, 0.02, "custom horizon (dotted)", color="#8b949e",
                    fontsize=6.5, alpha=0.7, transform=ax.transAxes,
                    ha="right", va="bottom")
        ax.set_xlim(0, n - 1)
        ax.set_ylim(0, 90)
        ax.set_xticks(xtick_idx)
        ax.set_xticklabels([times[i] for i in xtick_idx], color="#8b949e", fontsize=8)
        ax.set_yticks([0, 30, 60, 90])
        ax.set_yticklabels(["0°", "30°", "60°", "90°"], color="#8b949e", fontsize=8)
        ax.tick_params(colors="#8b949e")
        for spine in ax.spines.values():
            spine.set_edgecolor("#30363d")
        ax.set_title(f"Tonight's schedule — {date_str}", color="#c9d1d9", fontsize=10)
        ax.legend(loc="upper right", facecolor="#161b22", edgecolor="#30363d",
                  labelcolor="#c9d1d9", fontsize=7.5)

        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", dpi=130,
                    facecolor=fig.get_facecolor())
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        log.warning(f"[scheduler] chart generation failed: {e}")
        return None


def _slot_hhmm_to_utc(plan_date: str, hhmm: str) -> str:
    """Convert a plan slot's local HH:MM to a UTC ISO string.

    plan_date is the local date when nightly_plan() ran (18:00 AZ = 01:00 UTC next day).
    Evening slots (hour >= 14) belong to the local day before plan_date; morning slots
    (hour < 14) belong to the local day of plan_date. UTC = local + 7h (AZ = UTC-7).
    """
    from datetime import datetime, timedelta
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    plan_utc = datetime.strptime(plan_date, "%Y-%m-%d")
    local_base = plan_utc - timedelta(days=1)   # local evening date
    local_dt = local_base.replace(hour=h, minute=m)
    if h < 14:                                   # early-morning slot → next local day
        local_dt += timedelta(days=1)
    utc_dt = local_dt + timedelta(hours=7)
    return utc_dt.strftime("%Y-%m-%dT%H:%M")


def _evaluate_last_night(date_str: str) -> str | None:
    """Evaluate last plan vs actual captures; update learning. Returns a Telegram summary or None."""
    from nas_server.database import (
        get_unevaluated_planner_run, get_captures_for_date,
        get_capture_timestamps_for_night,
        update_target_learn, mark_planner_run_evaluated,
    )
    from datetime import datetime, timedelta

    run = get_unevaluated_planner_run()
    if not run:
        return None
    plan_date, plan_slots = run
    if not plan_slots:
        mark_planner_run_evaluated(plan_date)
        return None

    next_date = (datetime.strptime(plan_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    captured_set = set(get_captures_for_date(plan_date)) | set(get_captures_for_date(next_date))

    # Per-target timestamp ranges for the night: {target: (first_utc, last_utc)}
    ts = get_capture_timestamps_for_night(plan_date)
    for t, (f, l) in get_capture_timestamps_for_night(next_date).items():
        if t in ts:
            ts[t] = (min(ts[t][0], f), max(ts[t][1], l))
        else:
            ts[t] = (f, l)

    plan_set = {s["target"] for s in plan_slots}
    matched  = plan_set & captured_set
    off_plan = captured_set - plan_set
    skipped  = plan_set - captured_set

    mark_planner_run_evaluated(plan_date)

    if not captured_set:
        return None  # Nothing observed at all — no learning signal

    # Evaluate each skipped slot with window-aware logic
    penalised = set()
    for i, slot in enumerate(plan_slots):
        target = slot["target"]
        if target in captured_set:
            continue

        start_hhmm = slot.get("start_hhmm")
        if not start_hhmm:
            # Old format — fall back: skip only if something else was captured
            update_target_learn(target, skip_delta=1)
            penalised.add(target)
            continue

        slot_start_utc = _slot_hhmm_to_utc(plan_date, start_hhmm)

        # Rule 1: Was anything captured *at or after* this slot's start?
        # If not, the user stopped before this slot began — no preference signal.
        any_capture_after = any(
            last_utc >= slot_start_utc for (_, last_utc) in ts.values()
        )
        if not any_capture_after:
            log.info(f"[learn] {target}: skipped but nothing captured after {start_hhmm} — no signal")
            continue

        # Rule 3: Did the slot end before the user's first capture of the night?
        # Late-start scenario: planned 7–9pm, user started at 10pm — not a skip.
        end_hhmm = slot.get("end_hhmm")
        if end_hhmm and ts:
            slot_end_utc = _slot_hhmm_to_utc(plan_date, end_hhmm)
            first_capture_utc = min(first_utc for (first_utc, _) in ts.values())
            if slot_end_utc <= first_capture_utc:
                log.info(f"[learn] {target}: slot ended {end_hhmm} before first capture — late start, no signal")
                continue

        # Rule 2: Did the immediately preceding planned target overrun into this slot?
        # If so, the skip was forced by time, not preference.
        if i > 0:
            prev_target = plan_slots[i - 1]["target"]
            if prev_target in ts:
                _, prev_last_utc = ts[prev_target]
                if prev_last_utc >= slot_start_utc:
                    log.info(f"[learn] {target}: skipped due to {prev_target} overrun — no signal")
                    continue

        # True skip: user was observing but chose not to capture this target
        update_target_learn(target, skip_delta=1)
        penalised.add(target)
        log.info(f"[learn] {target}: true skip recorded")

    parts = []
    if matched:
        parts.append(f"✓ {', '.join(sorted(matched))}")
    if off_plan:
        parts.append(f"off-plan: {', '.join(sorted(off_plan))}")
    unpenalised_skips = skipped - penalised
    if penalised:
        parts.append(f"skipped: {', '.join(sorted(penalised))}")
    if unpenalised_skips:
        parts.append(f"no signal: {', '.join(sorted(unpenalised_skips))}")
    return f"Last night ({plan_date}) — " + " · ".join(parts) if parts else None


def _load_horizon() -> list[tuple[float, float]] | None:
    """Return custom horizon as list of (az, alt) tuples, or None."""
    raw = settings.get("observer_horizon")
    if not raw:
        return None
    try:
        return [(float(p[0]), float(p[1])) for p in raw]
    except Exception:
        return None


def _stack_calibration_masters(since_date: str) -> None:
    """Median-stack unmastered calibration sub-frames into masters. Runs in morning_plan."""
    import os
    import numpy as np
    from astropy.io import fits as afits
    from nas_server.database import (
        get_unmastered_calibration_groups, get_calibration_frames_for_master, get_conn,
    )
    from nas_server.config import settings
    from nas_server import telegram

    cal_lib = settings.get("calibration_library_path", "")
    if not cal_lib:
        log.warning("[cal_master] calibration_library_path not set — skipping master stacking")
        return

    groups = get_unmastered_calibration_groups(since_date)
    if not groups:
        log.info("[cal_master] no new calibration groups to stack")
        return

    report_lines = []

    for g in groups:
        ftype   = g["frame_type"]
        gain    = g["gain"]
        temp_c  = g["temp_c"]
        exptime = g["exposure_time"]
        filt    = g["filter"]
        count   = g["frame_count"]

        frames = get_calibration_frames_for_master(
            frame_type=ftype, gain=gain, temp_c=temp_c,
            exposure_time=exptime, filter=filt,
        )
        if len(frames) < 5:
            continue

        parts = []
        if gain is not None:
            parts.append(f"{gain}g")
        if exptime is not None:
            parts.append(f"{exptime:.0f}s")
        if temp_c is not None:
            parts.append(f"{temp_c:.0f}C")
        if filt and filt != "none":
            parts.append(filt)
        subdir = "_".join(parts) if parts else "default"
        out_dir = os.path.join(cal_lib, ftype, subdir)
        os.makedirs(out_dir, exist_ok=True)
        master_path = os.path.join(out_dir, f"master_{ftype}.fit")

        try:
            stack = []
            ref_header = None
            for fp in frames:
                try:
                    with afits.open(fp) as hdul:
                        if ref_header is None:
                            ref_header = hdul[0].header.copy()
                        stack.append(hdul[0].data.astype(np.float32))
                except Exception as e:
                    log.debug(f"[cal_master] skipping {fp}: {e}")

            if len(stack) < 3:
                report_lines.append(f"❌ {count} {ftype}s ({subdir}) — too few readable frames")
                continue

            cube = np.array(stack)
            # sigma-clip then median
            med = np.median(cube, axis=0)
            std = np.std(cube, axis=0)
            mask = np.abs(cube - med) <= 3 * std
            masked = np.where(mask, cube, np.nan)
            master_data = np.nanmedian(masked, axis=0).astype(np.float32)

            hdr = ref_header or afits.Header()
            hdr["NFRAMES"] = len(stack)
            hdr["CALTYPE"] = ftype
            hdr["HISTORY"] = f"Median master from {len(stack)} frames (sigma-clip 3.0)"
            afits.writeto(master_path, master_data, hdr, overwrite=True)

            # Register master row + link sub-frames
            from datetime import datetime as _dt
            now = _dt.utcnow().strftime("%Y-%m-%d")
            with get_conn() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO calibration_frames
                       (frame_type, camera, filter, gain, temp_c, exposure_time,
                        date, file_path, is_master, valid)
                       VALUES (?,?,?,?,?,?,?,?,1,1)""",
                    (ftype, "seestar_s50", filt or "none", gain, temp_c, exptime, now, master_path),
                )
                master_id = conn.execute(
                    "SELECT id FROM calibration_frames WHERE file_path=?", (master_path,)
                ).fetchone()[0]
                conn.execute(
                    "UPDATE calibration_frames SET master_of=? WHERE file_path IN ({})".format(
                        ",".join("?" * len(frames))
                    ),
                    [master_id] + list(frames),
                )

            report_lines.append(f"✅ {len(stack)} {ftype}s → {subdir}")
            log.info(f"[cal_master] created: {master_path} from {len(stack)} frames")

        except Exception as e:
            report_lines.append(f"❌ {ftype} {subdir} — {e}")
            log.error(f"[cal_master] exception stacking {ftype}: {e}")

    if report_lines:
        telegram.send(
            "🔧 <b>Calibration masters built</b>\n" + "\n".join(report_lines)
        )


def _morning_plan():
    """7:30am AZ: compute & save tonight's plan; send plan table + weather forecast to Telegram."""
    from datetime import datetime, timedelta
    from nas_server.planner import compute_plan, compute_schedule
    from nas_server.database import save_planner_run, update_target_learn
    from nas_server import telegram

    date_str = datetime.now().strftime("%Y-%m-%d")

    # Reset NINA polar alignment flag for the new night
    try:
        import urllib.request
        urllib.request.urlopen(
            urllib.request.Request("http://127.0.0.1:8000/nina/reset_ready", data=b"", method="POST"),
            timeout=3,
        )
        log.info("[morning_plan] NINA ready flag reset")
    except Exception as e:
        log.debug(f"[morning_plan] NINA reset skipped: {e}")

    # Stack any calibration frames from last night
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        _stack_calibration_masters(since_date=yesterday)
    except Exception as e:
        log.warning(f"[morning_plan] calibration master stacking failed: {e}")
    try:
        lat  = float(settings.get("observer_lat", 33.18))
        lon  = float(settings.get("observer_lon", -111.57))
        elev = float(settings.get("observer_elevation_m", 350))
        horizon = _load_horizon()

        is_clear, wx_summary = _get_weather(lat, lon)

        results  = compute_plan(date_from=date_str, date_to=date_str,
                                lat=lat, lon=lon, elevation=elev, horizon=horizon)
        schedule = compute_schedule(results, horizon)

        if not schedule:
            clear_icon = "✅ Clear" if is_clear else "🌧 Cloudy"
            telegram.send(f"<b>🌙 Tonight's Plan — {date_str}</b>\n"
                          f"{clear_icon} — {wx_summary}\n\nNo schedulable targets found.")
            return

        # Persist tonight's plan for tomorrow's learning evaluation
        plan_slots = [
            {"target": s["target"], "start_hhmm": s["start_hhmm"], "end_hhmm": s["end_hhmm"]}
            for s in schedule
        ]
        save_planner_run(date_str, plan_slots)
        for s in plan_slots:
            update_target_learn(s["target"], plan_delta=1)

        # Build plain-text plan table (no Claude)
        clear_icon = "✅ Clear" if is_clear else "🌧 Cloudy"
        lines = [f"<b>🌙 Tonight's Plan — {date_str}</b>",
                 f"{clear_icon} — {wx_summary}\n"]
        for slot in schedule:
            need = slot["rec_h"] - slot["int_hours"]
            need_str = f"{need:.1f}h needed" if need > 0.1 else "✓"
            have_str = f"{slot['int_hours']:.1f}h" if slot["int_hours"] > 0 else "NEW"
            lines.append(
                f"• <b>{slot['target']}</b>  {slot['start_hhmm']}–{slot['end_hhmm']}"
                f"  ({slot['planned_h']:.1f}h planned · {have_str} · {need_str})"
            )
        msg = "\n".join(lines)

        chart_bytes = _generate_plan_chart(results, schedule, date_str)
        if chart_bytes:
            telegram.send_photo_bytes(chart_bytes, caption=msg)
        else:
            telegram.send(msg)
        log.info(f"[morning_plan] sent {len(schedule)} slots, clear={is_clear}")
    except Exception as e:
        log.warning(f"[morning_plan] failed: {e}")


def nightly_plan():
    """6pm AZ: evaluate last night + check weather; if clear call Claude and send narrative."""
    from datetime import datetime
    from nas_server.planner import compute_plan, compute_schedule, get_narrative
    from nas_server import telegram

    date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        # Always evaluate last night's plan first
        learn_digest = _evaluate_last_night(date_str)
        if learn_digest:
            telegram.send(f"📊 {learn_digest}")

        lat  = float(settings.get("observer_lat", 33.18))
        lon  = float(settings.get("observer_lon", -111.57))
        elev = float(settings.get("observer_elevation_m", 350))
        horizon = _load_horizon()

        is_clear, wx_summary = _get_weather(lat, lon)
        if not is_clear:
            log.info("[nightly_plan] cloudy — skipping Claude narrative")
            telegram.send(f"🌧 <b>Not imaging tonight ({date_str})</b>\n{wx_summary}")
            return

        # Good weather — re-compute plan and call Claude for narrative
        results  = compute_plan(date_from=date_str, date_to=date_str,
                                lat=lat, lon=lon, elevation=elev, horizon=horizon)
        schedule = compute_schedule(results, horizon)
        if not schedule:
            log.info("[nightly_plan] no schedule — skipping narrative")
            return

        scheduled_set = {s["target"] for s in schedule}
        for r in results:
            r["scheduled"] = r["target"] in scheduled_set

        narrative = get_narrative(results, date_str, date_str)
        chart_bytes = _generate_plan_chart(results, schedule, date_str)

        caption = f"🌙 <b>Tonight — {date_str}</b>\n✅ {wx_summary}"
        if narrative:
            caption += f"\n\n{narrative}"

        if chart_bytes:
            telegram.send_photo_bytes(chart_bytes, caption=caption)
        elif caption:
            telegram.send(caption)
        log.info(f"[nightly_plan] sent narrative for {len(schedule)} slots")
    except Exception as e:
        log.warning(f"[nightly_plan] failed: {e}")


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(hourly_scan, "interval", hours=1, id="hourly_scan")
    scheduler.add_job(_morning_plan, CronTrigger(hour=7, minute=30), id="morning_plan")
    scheduler.add_job(nightly_plan, CronTrigger(hour=18, minute=0), id="nightly_plan")
    scheduler.start()
    log.info("Scheduler started (hourly scan, 7:30am morning plan, 6pm weather check / Claude narrative)")
    return scheduler
