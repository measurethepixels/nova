"""
AI target planner — visibility scoring for a date or date range.

Public API:
    compute_plan(date_from, date_to, lat, lon, elevation) -> list[dict]
    get_narrative(results, date_from, date_to) -> str
"""
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body
from astropy.time import Time

from nas_server.database import get_targets_for_planner, update_target_coords

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Persistent disk cache for _seasonal_scarcity
# Computing scarcity for 140+ targets takes ~36s on cold start.
# We persist results to disk so service restarts stay fast (~12s warm).
# Cache is keyed by "{year}_{ra}_{dec}_{lat}_{lon}_{elev}" — year prefix
# ensures automatic invalidation when the calendar year changes.
# ---------------------------------------------------------------------------
_SCARCITY_DISK_PATH = Path.home() / "seestar_database" / "seasonal_scarcity_cache.json"
_scarcity_disk: dict = {}

def _load_scarcity_disk() -> None:
    global _scarcity_disk
    try:
        if _SCARCITY_DISK_PATH.exists():
            _scarcity_disk = json.loads(_SCARCITY_DISK_PATH.read_text())
    except Exception as e:
        log.warning(f"[planner] could not load scarcity cache: {e}")
        _scarcity_disk = {}

def _save_scarcity_disk() -> None:
    try:
        _SCARCITY_DISK_PATH.write_text(json.dumps(_scarcity_disk))
    except Exception as e:
        log.warning(f"[planner] could not save scarcity cache: {e}")

_load_scarcity_disk()

_COORD_RESOLVE_LIMIT = 40  # max SIMBAD lookups per compute call
_CATALOG_RE = re.compile(r'^(M|C)\s*\d+$', re.IGNORECASE)
_AZ_TZ = timezone(timedelta(hours=-7))  # Arizona (UTC-7, no DST)

# SeeStar S50 field-of-view (1920×1080px @ 2.4"/px, 250mm f/5)
_FOV_W_ARCMIN = 76.8   # 1.28° — long axis (landscape)
_FOV_H_ARCMIN = 43.2   # 0.72° — short axis
# With S50 2× framing mode: same-name capture + Siril max framing, no stitching needed
_FOV_2X_W_ARCMIN = 153.6
_FOV_2X_H_ARCMIN = 86.4
# Diagonal of 2× frame — target fits at some rotation angle if its diagonal ≤ this (~176.2')
_FOV_2X_DIAG_ARCMIN = math.sqrt(_FOV_2X_W_ARCMIN ** 2 + _FOV_2X_H_ARCMIN ** 2)


def _parse_angular_size(size_str: str) -> tuple[float, float] | None:
    """Parse folio angular_size_arcmin strings like '9 × 4', '178×63', '12.3–18', '6.6'."""
    s = str(size_str).strip()
    for sep in [' × ', '×', ' x ', 'x']:
        if sep in s:
            parts = s.split(sep, 1)
            try:
                return float(parts[0].strip()), float(parts[1].strip())
            except ValueError:
                pass
    for sep in ['–', '—', '-']:
        if sep in s:
            parts = s.split(sep, 1)
            try:
                vals = [float(p.strip()) for p in parts if p.strip()]
                if vals:
                    v = max(vals)
                    return v, v
            except ValueError:
                pass
    try:
        v = float(s)
        return v, v
    except ValueError:
        return None


def _mosaic_info(size_str: str) -> dict:
    """Classify target size into single/s50_framing/mosaic capture strategy.

    Returns dict with: class, panels_w, panels_h, label
      "single"      — fits in native S50 FoV (76.8'×43.2')
      "s50_framing" — fits in 2× framing mode (153.6'×86.4'), same-name + Siril max
      "mosaic"      — requires traditional multi-panel mosaic with separate panel names
    """
    parsed = _parse_angular_size(size_str)
    if not parsed:
        return {"class": "single", "panels_w": 1, "panels_h": 1, "label": ""}
    w, h = parsed
    long_side, short_side = max(w, h), min(w, h)

    if long_side <= _FOV_W_ARCMIN and short_side <= _FOV_H_ARCMIN:
        return {"class": "single", "panels_w": 1, "panels_h": 1, "label": ""}

    # Target fits in 2× frame at some rotation angle if its diagonal ≤ frame diagonal
    target_diag = math.sqrt(w ** 2 + h ** 2)
    if target_diag <= _FOV_2X_DIAG_ARCMIN:
        return {"class": "s50_framing", "panels_w": 1, "panels_h": 1, "label": "S50 2× frame"}

    # Traditional mosaic — orient long FoV axis along long target axis
    pw = max(1, math.ceil(long_side / _FOV_W_ARCMIN))
    ph = max(1, math.ceil(short_side / _FOV_H_ARCMIN))
    return {"class": "mosaic", "panels_w": pw, "panels_h": ph, "label": f"{pw}×{ph} mosaic"}


def _catalog_score(target_name: str) -> float:
    """1.0 for Messier/Caldwell objects, 0.0 otherwise."""
    return 1.0 if _CATALOG_RE.match(target_name.strip()) else 0.0


def _fmt_local(t: Time) -> str:
    """Format an astropy Time as HH:MM Arizona local time."""
    from datetime import datetime as _dt
    return _dt.fromtimestamp(t.unix, tz=_AZ_TZ).strftime("%H:%M")


_HORIZON_OBSTRUCTION_THRESHOLD = 15.0  # degrees — above this is a real obstruction (house etc.)
_HORIZON_OBSTRUCTION_BUFFER   =  5.0  # degrees of safety margin added above obstructions
_MIN_SCHED_ALT = 20.0  # minimum altitude for scheduling slots (image quality floor)


def _horizon_alts(azs_deg: "np.ndarray", horizon: list[tuple[float, float]]) -> "np.ndarray":
    """Return interpolated minimum horizon altitude for each azimuth value in azs_deg.

    Adds a 5° safety buffer wherever the horizon exceeds 15° (a real obstruction like a
    house roof) so targets are never scheduled when barely clearing a physical barrier.
    Low natural-horizon sections (≤15°) are left unchanged.
    """
    if not horizon:
        return np.zeros(len(azs_deg))
    pts = sorted(horizon, key=lambda x: x[0])
    h_azs = np.array([p[0] % 360.0 for p in pts])
    h_alts = np.array([p[1] for p in pts])
    # Duplicate end points for circular (wrap-around) interpolation
    azs_wrap = np.concatenate([[h_azs[-1] - 360.0], h_azs, [h_azs[0] + 360.0]])
    alts_wrap = np.concatenate([[h_alts[-1]], h_alts, [h_alts[0]]])
    raw = np.interp(azs_deg % 360.0, azs_wrap, alts_wrap)
    # Add safety buffer over obstructions; leave open sky sections alone
    buffered = np.where(raw > _HORIZON_OBSTRUCTION_THRESHOLD,
                        raw + _HORIZON_OBSTRUCTION_BUFFER,
                        raw)
    return buffered


def resolve_coords(target_name: str) -> tuple[float, float] | None:
    """Return (ra_deg, dec_deg) from DB cache or SIMBAD. None if unresolvable."""
    from nas_server.database import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ra, dec FROM targets WHERE target=?", (target_name,)
        ).fetchone()
    if row and row[0] is not None and row[1] is not None:
        return float(row[0]), float(row[1])

    # SIMBAD does not read Caldwell designations ("C 7") — it matches some other
    # catalog entry entirely (C 7 -> a Cygnus star, not NGC 2403). Translate
    # Messier/Caldwell IDs to their NGC/IC name before resolving, but cache under
    # the original target name.
    from nas_server.database import _CALDWELL_NGC, _MESSIER_NGC
    lookup_name = _CALDWELL_NGC.get(target_name) or _MESSIER_NGC.get(target_name) or target_name
    try:
        coord = SkyCoord.from_name(lookup_name)
        ra = float(coord.ra.deg)
        dec = float(coord.dec.deg)
        update_target_coords(target_name, ra, dec)
        log.debug(f"[planner] resolved {target_name} (via {lookup_name}): RA={ra:.3f} Dec={dec:.3f}")
        return ra, dec
    except Exception as e:
        log.debug(f"[planner] coord resolve failed for {target_name!r} (via {lookup_name}): {e}")
        return None


def _dark_times(date_str: str, location: EarthLocation) -> list[Time]:
    """Return 30-min samples during nautical dark (sun alt < -12°) for one night."""
    midnight = Time(date_str + "T06:00:00", scale="utc")  # UTC midnight-ish for Arizona
    samples = [midnight + i * u.hour for i in np.arange(-8, 8, 0.5)]
    dark = []
    for t in samples:
        frame = AltAz(obstime=t, location=location)
        sun_alt = get_body("sun", t, location).transform_to(frame).alt.deg
        if sun_alt < -12:
            dark.append(t)
    return dark


def _moon_illumination(time: Time, location: EarthLocation) -> float:
    """Approximate moon illumination fraction 0-1."""
    moon = get_body("moon", time, location)
    sun = get_body("sun", time, location)
    elongation = moon.separation(sun).deg
    return (1 - np.cos(np.radians(elongation))) / 2


@lru_cache(maxsize=512)
def _seasonal_scarcity(ra: float, dec: float, lat: float, lon: float, elev: float) -> float:
    """
    Returns 0.0–1.0: fraction of the year this target is NOT usefully visible.
    1.0 = visible only 1 month/year (maximum scarcity), 0.0 = visible year-round.
    Samples one night per month (15th, local midnight ±4h), checks max alt > 10°.

    Results are persisted to _SCARCITY_DISK_PATH so cold service restarts are fast.
    The cache key includes the current year so results auto-invalidate on Jan 1.
    """
    import numpy as _np
    from astropy.time import Time as _Time
    from astropy.coordinates import EarthLocation as _EL, AltAz as _AltAz, SkyCoord as _SC
    import astropy.units as _u

    year = _Time.now().datetime.year
    disk_key = f"{year}_{ra}_{dec}_{lat}_{lon}_{elev}"
    if disk_key in _scarcity_disk:
        return float(_scarcity_disk[disk_key])

    location = _EL(lat=lat * _u.deg, lon=lon * _u.deg, height=elev * _u.m)
    coord = _SC(ra=ra * _u.deg, dec=dec * _u.deg, frame="icrs")
    visible_months = 0

    for month in range(1, 13):
        # Local midnight for the 15th ≈ 07:00 UTC (Arizona is UTC-7, no DST)
        midnight_utc = _Time(f"{year}-{month:02d}-15T07:00:00", scale="utc")
        times = [midnight_utc + i * _u.hour for i in _np.arange(-4, 4.5, 0.5)]
        frame = _AltAz(obstime=times, location=location)
        alts = coord.transform_to(frame).alt.deg
        if float(_np.max(alts)) > 10:
            visible_months += 1

    result = max(0.0, 1.0 - visible_months / 12.0)
    _scarcity_disk[disk_key] = result
    _save_scarcity_disk()
    return result


def _filter_match_score(target_type: str, moon_illum: float) -> float:
    """
    LP filter (OIII/Ha passband) on S50 gives emission nebulae strong moon immunity.
    Galaxies, clusters, and reflection nebulae are broadband — moon hurts them.
    Returns 0–1; higher = better match between target type and tonight's conditions.

    Keywords intentionally broad so they match verbose folio object_type strings:
      "planetary"  → "Planetary nebula", "Planetary Nebula — …"
      "hii"        → "HII region", "H II region", "HII Emission Nebula"
      "emission"   → "emission nebula", "Diffuse emission nebula"
      "supernova"  → "Supernova Remnant"
      "wolf-rayet" → "Wolf-Rayet bubble / emission nebula"
      "remnant"    → "Supernova Remnant", "…stellar remnant"
      "sharpless"  → "Sharpless 2-190 …"
    """
    t = (target_type or "").lower()
    if any(k in t for k in ("emission", "planetary", "supernova", "hii", "wolf-rayet", "remnant", "sharpless")):
        # Narrowband signal dominates; LP filter suppresses sky background → near moon-immune
        return 0.75 + 0.25 * (1.0 - moon_illum)
    elif any(k in t for k in ("galaxy", "cluster", "reflection", "asterism")):
        # Broadband target; full moon penalty applies
        return 1.0 - 0.8 * moon_illum
    else:
        return 1.0 - 0.5 * moon_illum  # unknown type — moderate penalty


def _freshness_score(days_since_last: float) -> float:
    """
    Encourages session rotation. 0.0 if imaged last night, 1.0 if 14+ days ago or never.
    Prevents the same target being scheduled every night while still allowing re-imaging.
    """
    return min(1.0, days_since_last / 14.0)


@lru_cache(maxsize=256)
def _folio_info(target_name: str) -> dict:
    """
    Return {angular_size_arcmin, rec_hours, panel_count} from the target folio.
    Cached per target — reads the JSON file once per session.
    Defaults: angular_size_arcmin=None, rec_hours=6.0, panel_count=1.
    """
    defaults = {"angular_size_arcmin": None, "rec_hours": 6.0, "panel_count": 1, "object_type": ""}
    from nas_server.folio_generator import load_folio as _lf
    data = _lf(target_name)
    if not data:
        return defaults
    try:
        size_str = (data.get("catalog") or {}).get("angular_size_arcmin", "")
        object_type = str((data.get("catalog") or {}).get("object_type", "") or "")
        rec_h_raw = (data.get("s50_achievability") or {}).get("recommended_integration_hours")
        rec_h = float(rec_h_raw) if rec_h_raw is not None else 6.0
        if size_str:
            # Strip parenthetical comments like "(full nebula; …)" before numeric parsing
            size_clean = str(size_str).split("(")[0].strip()
            parts = size_clean.replace("×", "x").replace("′", "").split("x")
            try:
                size_arcmin = float(parts[0].strip())
            except ValueError:
                size_arcmin = None
            mosaic = _mosaic_info(size_clean)
            panel_count = max(1, mosaic["panels_w"] * mosaic["panels_h"])
        else:
            size_arcmin = None
            panel_count = 1
        return {"angular_size_arcmin": size_arcmin, "rec_hours": rec_h, "panel_count": panel_count, "object_type": object_type}
    except Exception:
        return defaults


def _angular_size_score(arcmin: float | None) -> float:
    """
    S50 native FoV: 76.8′ × 43.2′. Score based on how well the target fills the frame.
    Sweet spot: major axis 15–80′ fits well; 2× framing handles up to 154′.
    Tiny targets (<5′) and very large mosaics (>154′) score lower.
    Returns 0.5 when size is unknown (neutral — don't penalise targets without folios).
    """
    if arcmin is None:
        return 0.5
    if arcmin < 3:
        return 0.15   # point-source-like, no detail to reveal
    if arcmin < 10:
        return 0.45   # small but some structure
    if arcmin <= 80:
        return 1.0    # fits nicely in native FoV — ideal
    if arcmin <= 154:
        return 0.75   # 2× framing handles it well
    return 0.40       # large mosaic — still doable but lower reward per session


def score_target(
    ra: float, dec: float,
    dark_times: list[Time],
    location: EarthLocation,
    moon_positions: list[SkyCoord],
    moon_illum: float,
    int_hours: float,
    priority: int,
    horizon: list[tuple[float, float]] | None = None,
    target_name: str = "",
    learn_score: float = 0.5,
    transient: bool = False,
    target_type: str = "",
    days_since_last_obs: float = 999.0,
    processing_score: float = 0.5,
    processing_tag: str = "",
) -> dict:
    if not dark_times:
        return None

    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
    frame = AltAz(obstime=dark_times, location=location)
    altaz = coord.transform_to(frame)
    alts = altaz.alt.deg

    max_alt = float(np.max(alts))
    if max_alt < 5:
        return None  # never rises above the horizon

    # Determine which time samples the target is above the (custom or default) horizon.
    # With a custom horizon the user controls what's reachable in each direction — no
    # additional floor applied, so southern targets like C 77/C 80 can appear if the
    # horizon profile allows it. Without a custom profile use a 15° generic floor.
    if horizon:
        azs = altaz.az.deg
        min_alts = _horizon_alts(azs, horizon)
        visible_mask = alts >= min_alts
    else:
        visible_mask = alts >= 15.0

    n_visible = int(np.sum(visible_mask))
    if n_visible == 0:
        return None  # always below horizon

    # Each dark sample is 0.5h apart
    time_visible_h = round(n_visible * 0.5, 1)

    peak_idx = int(np.argmax(alts))

    # Transit timing: fraction of visible window that is pre-transit (still rising).
    # 0.0 = already fully setting when night begins; 1.0 = all rising, transits at end.
    # ~0.5 = transits mid-visible-window, which is ideal.
    _vis_arr = np.where(visible_mask)[0]
    _pre_transit = int(np.sum(_vis_arr <= peak_idx))
    transit_timing_score = _pre_transit / len(_vis_arr) if len(_vis_arr) > 0 else 0.0
    _transit_isot = dark_times[peak_idx].isot  # UTC ISO string from astropy
    transit_utc = _transit_isot[:16]
    # Convert to MST (Arizona, UTC-7, no DST) for display
    try:
        from datetime import datetime as _dt, timezone as _tz2, timedelta as _td2
        _MST7 = _tz2(offset=_td2(hours=-7))
        _dt_utc = _dt.fromisoformat(_transit_isot[:19].replace("T", " ")).replace(tzinfo=_tz2.utc)
        transit_mst = _dt_utc.astimezone(_MST7).strftime("%Y-%m-%d %H:%M")
    except Exception:
        transit_mst = transit_utc

    moon_seps = [coord.separation(mp).deg for mp in moon_positions]
    min_moon_sep = float(np.min(moon_seps))

    # Altitude score: with custom horizon → time above it; without → max_alt based
    if horizon:
        altitude_score = min(time_visible_h / 6.0, 1.0)
    else:
        altitude_score = min(max_alt / 60.0, 1.0)

    # Seasonal urgency: targets barely visible tonight are "use it or lose it"
    urgency_score = max(0.0, 1.0 - time_visible_h / 3.0)

    # Seasonal scarcity: fraction of the year this target is NOT usefully visible
    scarcity_score = _seasonal_scarcity(
        round(ra, 4), round(dec, 4),
        round(location.lat.deg, 3), round(location.lon.deg, 3),
        round(location.height.value, 0)
    )

    moon_sep_score    = min(min_moon_sep / 60.0, 1.0)
    moon_illum_score  = 1.0 - moon_illum
    priority_score    = min(priority / 10.0, 1.0)
    cat_score         = _catalog_score(target_name)
    freshness_score    = _freshness_score(days_since_last_obs)

    # Folio-derived: angular size, panel count, per-panel recommended hours, and object_type.
    # Use folio object_type as fallback when the DB type column is empty (common — DB type
    # is rarely populated, but folios have rich verbose type strings for all targets).
    fi            = _folio_info(target_name)
    panel_count   = fi["panel_count"]
    scaled_rec_h  = fi["rec_hours"] * panel_count   # total hours needed for all panels
    angular_size_score = _angular_size_score(fi["angular_size_arcmin"])
    # Resolve effective type: DB value first, folio object_type as fallback
    effective_type = target_type or fi.get("object_type", "")
    filter_match_score = _filter_match_score(effective_type, moon_illum)

    # Integration score: uses folio-scaled rec_hours so a 6-panel mosaic at 5h collected
    # stays near 1.0 (barely started) rather than dropping to 0.5 like a finished panel.
    integration_score = 1.0 / (1.0 + int_hours / max(scaled_rec_h, 1.0))

    # In-progress mosaic bonus: a multi-panel mosaic that has been started but is < 95%
    # complete gets a push to keep it going — abandoning halfway wastes all prior work.
    completion_frac = int_hours / scaled_rec_h if scaled_rec_h > 0 else 1.0
    if panel_count > 1 and 0 < completion_frac < 0.95:
        in_progress_score = 1.0 - completion_frac   # 0.9 at 10% done, tapers to 0 near finish
    else:
        in_progress_score = 0.0

    # Completion priority: prioritise targets close to their recommended integration
    # over brand-new ones (finish what you started), and downgrade targets that already
    # have enough data ("done"). A new target (0h) sits at a modest baseline; the score
    # ramps up toward the finish line, then drops sharply once enough data is collected.
    if completion_frac <= 0.0:
        completion_priority_score = 0.25            # never started — yields to near-done
        completion_tag = ""
    elif completion_frac < 0.95:
        completion_priority_score = 0.25 + 0.75 * (completion_frac / 0.95)  # ramps to ~1.0
        completion_tag = "almost done" if completion_frac >= 0.7 else ""
    else:
        completion_priority_score = 0.05            # enough data — downgrade ("done")
        completion_tag = "enough data"

    # Messier Wall gap (2026-07-12, Henry: "make it a priority in processing and
    # planning to fill the grid"): any Messier object still short of its recommended
    # integration gets a flat catalog-completion boost so Messier gaps outrank
    # otherwise-equivalent non-Messier targets. Deliberately a SEPARATE acquisition
    # term (see [[feedback-planner-acquisition-priority]]) — completion_priority
    # still decides near-done-vs-new ordering among the gaps, and a "done" Messier
    # (>=95% of rec hours) gets nothing.
    import re as _re
    _is_messier = bool(_re.fullmatch(r"M ?\d{1,3}", (target_name or "").strip()))
    messier_gap_score = 1.0 if (_is_messier and completion_frac < 0.95) else 0.0

    # Scheduling window: compute before combined score so window_score can be included.
    # Mirrors the sched_floor logic in compute_schedule exactly.
    if horizon:
        _sf = np.maximum(min_alts, _MIN_SCHED_ALT) if max_alt >= _MIN_SCHED_ALT else min_alts
    else:
        _sf = np.full(len(alts), _MIN_SCHED_ALT if max_alt >= _MIN_SCHED_ALT else 15.0)
    _sv = np.where(alts >= _sf)[0]
    # Penalise targets with very short schedulable windows — normalised to 2 h (1.0 at ≥ 2 h).
    window_score = min(len(_sv) * 0.5 / 2.0, 1.0)

    combined = (
        0.10 * altitude_score
        + 0.14 * moon_sep_score
        + 0.06 * moon_illum_score
        + 0.10 * integration_score
        + 0.04 * priority_score
        + 0.04 * urgency_score
        + 0.04 * cat_score
        + 0.06 * learn_score
        + 0.05 * scarcity_score
        + 0.07 * filter_match_score
        + 0.06 * freshness_score
        + 0.04 * angular_size_score
        + 0.06 * in_progress_score
        + 0.06 * transit_timing_score
        + 0.08 * window_score
        + 0.10 * completion_priority_score  # finish near-done targets; downgrade "done"
        + 0.06 * processing_score   # ease off targets with a good processed result
        + 0.08 * messier_gap_score  # fill the Messier Wall (catalog completion)
    )

    # Transient targets (comets, planets, events) get a hard 1.5× boost —
    # intentionally uncapped so they always rise near the top of the schedule.
    if transient:
        combined *= 1.5

    alt_curve = [[_fmt_local(dark_times[i]), round(float(alts[i]), 1)]
                 for i in range(len(dark_times))]

    horizon_curve = None
    if horizon:
        horizon_curve = [round(float(min_alts[i]), 1) for i in range(len(dark_times))]

    window_start_hhmm = alt_curve[_sv[0]][0]  if len(_sv) > 0 else None
    window_end_hhmm   = alt_curve[_sv[-1]][0] if len(_sv) > 0 else None

    return {
        "max_alt": round(max_alt, 1),
        "time_visible_h": time_visible_h,
        "transit_utc": transit_utc,
        "transit_mst": transit_mst,
        "min_moon_sep": round(min_moon_sep, 1),
        "moon_illum_pct": round(moon_illum * 100, 0),
        "int_hours": round(int_hours, 1),
        "altitude_score": round(altitude_score, 3),
        "moon_sep_score": round(moon_sep_score, 3),
        "moon_illum_score": round(moon_illum_score, 3),
        "integration_score": round(integration_score, 3),
        "priority_score": round(priority_score, 3),
        "urgency_score": round(urgency_score, 3),
        "scarcity_score": round(scarcity_score, 3),
        "filter_match_score": round(filter_match_score, 3),
        "freshness_score": round(freshness_score, 3),
        "angular_size_score": round(angular_size_score, 3),
        "in_progress_score": round(in_progress_score, 3),
        "transit_timing_score": round(transit_timing_score, 3),
        "window_score": round(window_score, 3),
        "completion_priority_score": round(completion_priority_score, 3),
        "completion_tag": completion_tag,
        "completion_frac": round(completion_frac, 3),
        "messier_gap_score": round(messier_gap_score, 3),
        "messier_gap": int(messier_gap_score > 0),
        "processing_score": round(processing_score, 3),
        "processing_tag": processing_tag,
        "panel_count": panel_count,
        "scaled_rec_h": round(scaled_rec_h, 1),
        "catalog_score": round(cat_score, 3),
        "learn_score": round(learn_score, 3),
        "combined_score": round(combined, 3),
        "transient": int(transient),
        "window_start_hhmm": window_start_hhmm,
        "window_end_hhmm":   window_end_hhmm,
        "alt_curve": alt_curve,
        "horizon_curve": horizon_curve,
    }


def compute_plan(
    date_from: str,
    date_to: str,
    lat: float,
    lon: float,
    elevation: float,
    horizon: list[tuple[float, float]] | None = None,
) -> list[dict]:
    """Score all active targets for the given date range. Returns sorted list (best first)."""
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=elevation * u.m)

    # Collect all nights in range
    d0 = datetime.strptime(date_from, "%Y-%m-%d")
    d1 = datetime.strptime(date_to, "%Y-%m-%d")
    nights = []
    d = d0
    while d <= d1:
        nights.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    # Dark-hours + moon for each night, union
    all_dark: list[Time] = []
    moon_coords: list[SkyCoord] = []
    moon_illums: list[float] = []
    for night in nights:
        dark = _dark_times(night, location)
        all_dark.extend(dark)
        if dark:
            mid = dark[len(dark) // 2]
            moon_coords.append(get_body("moon", mid, location))
            moon_illums.append(_moon_illumination(mid, location))

    if not all_dark:
        log.warning("[planner] no dark hours found for date range")
        return []

    # Pre-compute per-time moon positions once, converted to ICRS to match target coords.
    # (get_body returns GCRS; comparing GCRS with ICRS triggers astropy warnings.)
    moon_positions = [get_body("moon", t, location).transform_to("icrs") for t in all_dark]

    # Median-night illumination for scoring
    if moon_illums:
        mid_illum = moon_illums[len(moon_illums) // 2]
    else:
        mid_illum = _moon_illumination(all_dark[0], location)

    from nas_server.database import get_target_learn_scores, seed_learn_from_history
    seeded = seed_learn_from_history()
    if seeded:
        log.info(f"[planner] seeded learn scores for {seeded} targets from history")
    learn_scores = get_target_learn_scores()

    # Processing-quality signal (Phase 4 feedback loop). Acquisition priority only —
    # deliberately separate from learn_score (aesthetic). The planner schedules what to
    # *acquire*, so this only EASES OFF targets that already have a good processed result
    # (you don't need more light on a target you've already finished well). It does NOT
    # boost acquisition of unprocessed targets — "needs processing" is a worklist concern,
    # not a reason to collect more data. How-much-data-is-enough is handled separately by
    # completion_priority_score. A user "Needs-rework" flag lifts the target back to
    # neutral so it isn't suppressed. Targets absent from the worklist (no stacks) stay at
    # score_target's neutral 0.5 default.
    proc_map: dict[str, tuple[float, str]] = {}
    try:
        from nas_server.database import get_worklist, get_list, REWORK_LIST
        _flagged = set(get_list(REWORK_LIST))
        for r in get_worklist():
            if r["bucket"] == "good":   # well-finished result → ease off acquisition
                proc_map[r["target"]] = (0.1, "finished")
        for t in _flagged:  # explicit user flag → don't suppress; keep neutral
            if proc_map.get(t, (0.5, ""))[0] < 0.5:
                proc_map[t] = (0.5, "flagged")
    except Exception as e:
        log.warning(f"[planner] processing-quality signal unavailable: {e}")

    targets = get_targets_for_planner()
    log.info(f"[planner] scoring {len(targets)} targets for {date_from}–{date_to}")

    # Resolve coords — DB cache first, then SIMBAD (up to limit)
    resolved = 0
    for t in targets:
        if t["ra"] is not None:
            continue
        if resolved >= _COORD_RESOLVE_LIMIT:
            break
        coords = resolve_coords(t["target"])
        if coords:
            t["ra"], t["dec"] = coords
            resolved += 1

    results = []
    skipped = 0
    for t in targets:
        if t["ra"] is None or t["dec"] is None:
            skipped += 1
            continue
        metrics = score_target(
            t["ra"], t["dec"],
            all_dark, location,
            moon_positions, mid_illum,
            t["group_int_hours"], t["priority"],
            horizon=horizon,
            target_name=t["target"],
            learn_score=learn_scores.get(t["target"], 0.5),
            transient=bool(t.get("transient", 0)),
            target_type=t.get("target_type", ""),
            days_since_last_obs=t.get("days_since_last_obs", 999.0),
            processing_score=proc_map.get(t["target"], (0.5, ""))[0],
            processing_tag=proc_map.get(t["target"], (0.5, ""))[1],
        )
        if metrics is None:
            continue
        results.append({
            "target": t["target"],
            "association": t.get("association") or "",
            "own_int_hours": t["int_hours"],
            **metrics,
        })

    results.sort(key=lambda x: x["combined_score"], reverse=True)

    # Companion bonus: if a target's association partner ranks in the top 20,
    # give it a 10% score boost to keep co-imageable pairs together in the schedule.
    top_20 = {r["target"] for r in results[:20]}
    boosted = []
    for r in results:
        assoc = r.get("association") or ""
        if assoc:
            partners = [a.strip() for a in assoc.split(",") if a.strip()]
            if any(p in top_20 for p in partners):
                r = dict(r)  # don't mutate original
                r["combined_score"] = round(r["combined_score"] * 1.10, 3)
                r["companion_bonus"] = True
        boosted.append(r)
    results = boosted
    results.sort(key=lambda x: x["combined_score"], reverse=True)

    log.info(f"[planner] scored {len(results)} targets, skipped {skipped} (no coords)")
    top = results[:40]

    # Annotate top results with mosaic info from folios
    from nas_server.folio_generator import load_folio
    for r in top:
        info = {"class": "single", "panels_w": 1, "panels_h": 1, "label": ""}
        folio = load_folio(r["target"])
        if folio:
            size_str = (folio.get("catalog") or {}).get("angular_size_arcmin")
            if size_str:
                info = _mosaic_info(str(size_str))
        r["mosaic_class"] = info["class"]
        r["mosaic_panels_w"] = info["panels_w"]
        r["mosaic_panels_h"] = info["panels_h"]
        r["mosaic_label"] = info["label"]
        r["is_mosaic"] = info["class"] != "single"

    return top


def compute_schedule(
    results: list[dict],
    horizon: list[tuple[float, float]] | None = None,
    force_targets: set[str] | None = None,
) -> list[dict]:
    """Build an optimized nightly observing schedule using per-slot scoring.

    Each 30-min slot is assigned to whichever candidate scores highest *at that
    moment* — the score is the nightly combined_score weighted by the fraction of
    peak altitude the target is at right now.  This naturally fills the whole
    night without dead time and avoids scheduling targets when they are low.

    A hysteresis band (15%) prevents rapid target-switching: once a target wins a
    slot it keeps it unless a competitor scores ≥15% better.  Targets drop out of
    contention once they have accumulated their desired integration for the night.
    """
    from nas_server.folio_generator import load_folio

    if not results:
        return []

    n = len(results[0].get("alt_curve", []))
    if n < 2:
        return []

    # ── Build candidate pool ──────────────────────────────────────────────────
    # force_targets: when set (Replan mode), only these targets are considered and the
    # "already complete" filter is skipped — the user is explicitly overriding the auto plan.
    replan_mode = force_targets is not None
    candidates = []
    for r in results:
        if replan_mode and r["target"] not in force_targets:
            continue
        if r.get("time_visible_h", 0) < 0.5:
            continue
        alt_curve = r.get("alt_curve", [])
        if len(alt_curve) != n:
            continue

        folio = load_folio(r["target"])
        if folio is None and not replan_mode:
            try:
                from nas_server.database import add_agent_suggestion
                fname = r["target"].replace(" ", "_").replace("/", "_") + ".json"
                add_agent_suggestion(
                    description=f"Create folio for {r['target']} — scheduled tonight but no folio exists",
                    file_hint=f"nas_server/target_folios/{fname}",
                    source="planner",
                    dedup_key=f"folio:{r['target']}",
                )
            except Exception:
                pass

        # Use _folio_info for rec_h/panel_count so the defaults (6.0h, 1 panel) match
        # score_target exactly — prevents targets from being wrongly skipped here when
        # they score fine in compute_plan because the two code paths disagreed on rec_h.
        fi = _folio_info(r["target"])
        rec_h = fi["rec_hours"]          # 6.0h default when no folio
        panel_count = fi["panel_count"]  # 1 default
        scaled_rec_h = rec_h * panel_count

        # In normal mode skip targets that already have sufficient integration.
        # In replan mode the user picked them explicitly so always include.
        if not replan_mode and r["int_hours"] >= scaled_rec_h:
            continue

        alts_list = [p[1] for p in alt_curve]
        max_alt = max(alts_list)
        hcurve = r.get("horizon_curve")

        # Per-slot minimum altitude: 20° quality floor for targets that can reach it;
        # fall back to custom horizon for southern targets that peak below 20°.
        if max_alt >= _MIN_SCHED_ALT:
            sched_floor = [max(hcurve[i] if hcurve else 15.0, _MIN_SCHED_ALT)
                           for i in range(n)]
        else:
            sched_floor = [hcurve[i] if hcurve else 15.0 for i in range(n)]

        vis_count = sum(1 for i in range(n) if alts_list[i] >= sched_floor[i])
        # Require at least 1 hour of schedulable window (2 × 30-min slots).
        # A single-slot window means start == end in the table — not worth scheduling.
        if vis_count < 2:
            continue

        # In both modes, cap desired_h by the remaining integration deficit.
        # In replan mode we previously used the full visible window, which caused one
        # dominant target (e.g. M 13 at 90° for 6.5h) to eat every slot and crowd out
        # the other manually-selected targets. Using deficit ensures every selected
        # target gets time proportional to what it still needs.
        # Floor: at least 0.5h per candidate so they all appear in the schedule.
        deficit_h = max(0.5, scaled_rec_h - r["int_hours"])
        desired_h = min(deficit_h, vis_count * 0.5)

        candidates.append({
            "target": r["target"],
            "association": r.get("association") or "",
            "is_mosaic": r.get("is_mosaic", False),
            "mosaic_class": r.get("mosaic_class", "single"),
            "mosaic_panels_w": r.get("mosaic_panels_w", 1),
            "mosaic_panels_h": r.get("mosaic_panels_h", 1),
            "mosaic_label": r.get("mosaic_label", ""),
            "combined_score": r["combined_score"],
            "desired_h": desired_h,
            "alt_curve": alt_curve,
            "alts_list": alts_list,
            "max_alt": max_alt,
            "sched_floor": sched_floor,
            "int_hours": r["int_hours"],
            "own_int_hours": r.get("own_int_hours", r["int_hours"]),
            "rec_h": rec_h,
            "scaled_rec_h": scaled_rec_h,
        })

        if len(candidates) >= 40:
            break

    if not candidates:
        return []

    # ── Slot-by-slot greedy assignment ───────────────────────────────────────
    # At each 30-min sample: score = combined_score × (alt / peak_alt).
    # A target naturally holds its slot until remaining_h reaches 0, so long-deficit
    # targets fill long blocks and short-deficit targets yield quickly — no hard minimum.
    # Hysteresis (20% threshold) prevents flip-flopping between similarly-scored targets.
    _SWITCH_THRESHOLD = 0.80  # competitor must score ≥20% better to displace current
    slot_assignment: list[str | None] = [None] * n
    remaining = {c["target"]: c["desired_h"] for c in candidates}
    current_target: str | None = None

    for t in range(n):
        best_target: str | None = None
        best_score = -1.0
        for c in candidates:
            if remaining.get(c["target"], 0) <= 0:
                continue
            alt = c["alts_list"][t]
            if alt < c["sched_floor"][t]:
                continue
            # Normalise by max(peak_alt, 75°) so circumpolar targets that never
            # exceed 40° can't game the ratio against high-transit targets at the
            # same physical altitude. A target peaking at 38° gets alt/75 ≈ 0.47
            # while one at 65° of its 73° peak gets 65/75 ≈ 0.87 — correct ordering.
            score = c["combined_score"] * (alt / max(c["max_alt"], 75.0))
            if score > best_score:
                best_score = score
                best_target = c["target"]

        # Hysteresis: stick with current unless the new winner is clearly better
        if current_target and current_target != best_target and remaining.get(current_target, 0) > 0:
            curr = next((c for c in candidates if c["target"] == current_target), None)
            if curr:
                alt = curr["alts_list"][t]
                if alt >= curr["sched_floor"][t]:
                    curr_score = curr["combined_score"] * (alt / max(curr["max_alt"], 75.0))
                    if curr_score >= best_score * _SWITCH_THRESHOLD:
                        best_target = current_target

        slot_assignment[t] = best_target
        current_target = best_target
        if best_target:
            remaining[best_target] = round(remaining[best_target] - 0.5, 2)

    # ── Replan guarantee pass ────────────────────────────────────────────────
    # In replan mode the user explicitly selected every candidate — guarantee each
    # appears at least once in the schedule. If a target has 0 assigned slots (was
    # outcompeted every time slot), steal its single best slot from whichever target
    # currently holds it.
    if replan_mode:
        assigned_targets = set(t for t in slot_assignment if t is not None)
        missing = [c for c in candidates if c["target"] not in assigned_targets]
        for c in missing:
            # Find the slot where this target scores best
            best_t, best_s = -1, -1.0
            for t in range(n):
                alt = c["alts_list"][t]
                if alt < c["sched_floor"][t]:
                    continue
                s = c["combined_score"] * (alt / c["max_alt"])
                if s > best_s:
                    best_s = s
                    best_t = t
            if best_t >= 0:
                slot_assignment[best_t] = c["target"]
                log.debug(f"[scheduler] replan guarantee: inserted {c['target']} at slot {best_t}")

    # ── Group consecutive same-target slots into schedule entries ─────────────
    schedule: list[dict] = []
    i = 0
    while i < n:
        target = slot_assignment[i]
        if target is None:
            i += 1
            continue
        j = i + 1
        while j < n and slot_assignment[j] == target:
            j += 1
        c = next((x for x in candidates if x["target"] == target), None)
        if c is None:
            i = j
            continue
        planned_h = round((j - i) * 0.5, 2)
        start_hhmm = c["alt_curve"][i][0]
        if j < n:
            end_hhmm = c["alt_curve"][j][0]
        else:
            # Last block runs to end of night — add 30 min to the final slot's label
            _last = c["alt_curve"][n - 1][0]
            _h, _m = int(_last[:2]), int(_last[3:])
            _tot = _h * 60 + _m + 30
            end_hhmm = f"{(_tot // 60) % 24:02d}:{_tot % 60:02d}"
        schedule.append({
            "target": target,
            "association": c.get("association") or "",
            "is_mosaic": c.get("is_mosaic", False),
            "mosaic_class": c.get("mosaic_class", "single"),
            "mosaic_panels_w": c.get("mosaic_panels_w", 1),
            "mosaic_panels_h": c.get("mosaic_panels_h", 1),
            "mosaic_label": c.get("mosaic_label", ""),
            "start_idx": i,
            "end_idx": j,
            "start_hhmm": start_hhmm,
            "end_hhmm": end_hhmm,
            "planned_h": planned_h,
            "desired_h": round(c["desired_h"], 2),
            "rec_h": c["rec_h"],
            "scaled_rec_h": round(c["scaled_rec_h"], 1),
            "int_hours": round(c["int_hours"], 1),
            "own_int_hours": round(c.get("own_int_hours", c["int_hours"]), 1),
        })
        i = j

    return schedule


def get_narrative(results: list[dict], date_from: str, date_to: str,
                  schedule: list[dict] | None = None) -> str:
    """Generate a 3-4 sentence planning narrative via Claude Haiku.

    Describes the schedule in sequence — what runs first and why, then what follows.
    Uses the actual schedule (with start/stop times) when provided so the narrative
    matches the plan exactly and never contradicts the scheduler's decisions.
    """
    if not results:
        return ""
    try:
        from nas_server.config import settings
        import anthropic
        key = settings.get("anthropic_api_key")
        if not key:
            return ""

        # Build the narrative from the schedule (ordered) rather than the scored results.
        # This ensures the narrative describes what's actually planned, in sequence.
        if schedule:
            sched_targets = schedule
        else:
            sched_targets = [r for r in results if r.get("scheduled")]

        if not sched_targets:
            return ""

        # Look up score/alt/moon details from results
        result_by_target = {r["target"]: r for r in results}

        table_lines = ["# | Target | Start → Stop | Planned | Max Alt | Moon Sep | Have | Need"]
        table_lines.append("--|--------|-------------|---------|---------|----------|------|-----")
        for i, s in enumerate(sched_targets, 1):
            r = result_by_target.get(s["target"], {})
            max_alt = r.get("max_alt", 0)
            moon_sep = r.get("min_moon_sep", 0)
            int_h = s.get("int_hours", r.get("int_hours", 0))
            rec_h = s.get("rec_h", r.get("scaled_rec_h", 6.0))
            need_h = max(0.0, rec_h - int_h)
            start = s.get("start_hhmm", "?")
            end   = s.get("end_hhmm", "?")
            planned_h = s.get("planned_h", 0)
            table_lines.append(
                f"{i} | {s['target']} | {start}→{end} | {planned_h:.1f}h | "
                f"{max_alt:.0f}° | {moon_sep:.0f}° | {int_h:.1f}h | {need_h:.1f}h"
            )
        table = "\n".join(table_lines)
        date_label = date_from if date_from == date_to else f"{date_from} to {date_to}"

        prompt = (
            f"You are an astrophotography assistant for Henry (Chandler AZ, Bortle 6, "
            f"SeeStar S50 smart telescope with LP filter available).\n\n"
            f"Date: {date_label}\n\n"
            f"Tonight's imaging schedule (in order):\n{table}\n\n"
            f"Write 3-4 sentences describing this specific schedule in sequence — explain "
            f"why each target is in its time slot (altitude, moon conditions, integration "
            f"deficit). Describe the plan as given; do not suggest changes or alternatives. "
            f"Be concise and practical."
        )

        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.warning(f"[planner] narrative failed: {e}")
        return ""
