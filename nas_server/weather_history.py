"""Cached historical observing-night conditions for the private dashboard.

Open-Meteo's archive data is a weather estimate, not proof that weather caused
a missed observing session.  This module deliberately returns only bounded,
night-level summaries and keeps the user's coordinates out of the response.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import urlopen


ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CACHE_MAX_AGE = timedelta(hours=24)
MAX_DAYS = 366
ARCHIVE_LAG_DAYS = 5
RAIN_MM = 0.2
CLOUD_PCT = 60


def archive_coverage_end(requested_end: date, *, today: date | None = None) -> date:
    """Latest bounded archive date; recent nights remain unclassified."""
    current = today or date.today()
    return min(requested_end, current - timedelta(days=ARCHIVE_LAG_DAYS))


def _fetch_json(url: str) -> dict:
    with urlopen(url, timeout=15) as response:
        return json.loads(response.read())


def _nightly_conditions(data: dict, start: date, end: date) -> dict[str, dict]:
    """Reduce local hourly values into 18:00–06:59 observing nights."""
    buckets: dict[str, dict[str, list[float]]] = {}
    times = data.get("hourly", {}).get("time", [])
    clouds = data.get("hourly", {}).get("cloud_cover", [])
    precipitation = data.get("hourly", {}).get("precipitation", [])
    for stamp, cloud, rain in zip(times, clouds, precipitation):
        when = datetime.fromisoformat(stamp)
        if when.hour >= 18:
            night = when.date()
        elif when.hour <= 6:
            night = when.date() - timedelta(days=1)
        else:
            continue
        if not start <= night <= end:
            continue
        bucket = buckets.setdefault(night.isoformat(), {"cloud": [], "rain": []})
        if cloud is not None:
            bucket["cloud"].append(float(cloud))
        if rain is not None:
            bucket["rain"].append(float(rain))

    result: dict[str, dict] = {}
    for night, values in buckets.items():
        cloud_avg = round(sum(values["cloud"]) / len(values["cloud"])) if values["cloud"] else None
        rain_mm = round(sum(values["rain"]), 1) if values["rain"] else 0.0
        if rain_mm >= RAIN_MM:
            condition = "rain"
        elif cloud_avg is not None and cloud_avg >= CLOUD_PCT:
            condition = "cloud"
        else:
            condition = "clear"
        result[night] = {
            "condition": condition,
            "cloud_pct": cloud_avg,
            "precipitation_mm": rain_mm,
        }
    return result


def _cache_matches(payload: dict, *, lat: float, lon: float, start: date, end: date) -> bool:
    try:
        fetched = datetime.fromisoformat(payload["fetched_at"].replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - fetched
        query = payload["query"]
        return (
            age <= CACHE_MAX_AGE
            and query["lat"] == round(lat, 4)
            and query["lon"] == round(lon, 4)
            and query["start"] == start.isoformat()
            and query["end"] == end.isoformat()
        )
    except (KeyError, TypeError, ValueError):
        return False


def get_historical_conditions(
    lat: float,
    lon: float,
    start: date,
    end: date,
    *,
    cache_path: Path,
    fetch_json: Callable[[str], dict] = _fetch_json,
) -> dict[str, dict]:
    """Return cached or fetched night summaries for a bounded date range."""
    span = (end - start).days + 1
    if span < 1 or span > MAX_DAYS:
        raise ValueError(f"date range must contain 1–{MAX_DAYS} days")

    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cached = {}
    if _cache_matches(cached, lat=lat, lon=lon, start=start, end=end):
        return cached.get("nights", {})

    # Include the following morning so the final observing night is complete.
    query = urlencode({
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": (end + timedelta(days=1)).isoformat(),
        "hourly": "cloud_cover,precipitation",
        "timezone": "auto",
    })
    data = fetch_json(f"{ARCHIVE_URL}?{query}")
    nights = _nightly_conditions(data, start, end)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "query": {
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "nights": nights,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return nights
