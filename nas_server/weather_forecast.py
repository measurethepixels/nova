"""Night-window weather forecast shared by the scheduler and agent tools.

Open-Meteo supplies the primary hourly forecast and local sunset/sunrise times.
7Timer remains an astronomy-specific comparison source.  A large disagreement
is reported as uncertainty and fails open so one model cannot suppress a plan.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import logging
from urllib.parse import urlencode
from urllib.request import urlopen


log = logging.getLogger(__name__)

_CLOUD_PCT = {1: 3, 2: 13, 3: 25, 4: 38, 5: 50, 6: 63, 7: 75, 8: 88, 9: 97}
_CLEAR_THRESHOLD_PCT = 40
_DISAGREEMENT_PCT = 35


def _fetch_json(url: str) -> dict:
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read())


def _night_bounds(data: dict, night_date: str) -> tuple[datetime, datetime]:
    daily = data["daily"]
    index = daily["time"].index(night_date)
    offset = timezone(timedelta(seconds=int(data.get("utc_offset_seconds", 0))))
    sunset = datetime.fromisoformat(daily["sunset"][index]).replace(tzinfo=offset)
    sunrise = datetime.fromisoformat(daily["sunrise"][index + 1]).replace(tzinfo=offset)
    return sunset, sunrise


def _open_meteo_clouds(data: dict, start: datetime, end: datetime) -> list[int]:
    offset = start.tzinfo
    values = []
    for stamp, cloud in zip(data["hourly"]["time"], data["hourly"]["cloud_cover"]):
        when = datetime.fromisoformat(stamp).replace(tzinfo=offset)
        if start <= when <= end:
            values.append(int(cloud))
    return values


def _seven_timer_clouds(data: dict, start: datetime, end: datetime) -> list[int]:
    initialized = datetime.strptime(str(data["init"]), "%Y%m%d%H").replace(tzinfo=timezone.utc)
    values = []
    for item in data.get("dataseries", []):
        when = initialized + timedelta(hours=int(item["timepoint"]))
        if start.astimezone(timezone.utc) <= when <= end.astimezone(timezone.utc):
            values.append(_CLOUD_PCT.get(int(item.get("cloudcover", 1)), 0))
    return values


def _mean(values: list[int]) -> int:
    return round(sum(values) / len(values))


def get_tonight_weather(
    lat: float,
    lon: float,
    night_date: str | None = None,
    *,
    fetch_json=_fetch_json,
) -> tuple[bool, str]:
    """Return ``(proceed, summary)`` for local sunset through next sunrise.

    ``proceed`` is false only when the available models agree that the observing
    night is cloudy. Network/model disagreement fails open and is made explicit.
    """
    requested_date = night_date or date.today().isoformat()
    om_query = urlencode({
        "latitude": lat,
        "longitude": lon,
        "hourly": "cloud_cover",
        "daily": "sunrise,sunset",
        "timezone": "auto",
        "forecast_days": 3,
    })
    om_url = f"https://api.open-meteo.com/v1/forecast?{om_query}"
    st_url = ("https://www.7timer.info/bin/api.pl?"
              + urlencode({"lon": lon, "lat": lat, "product": "astro", "output": "json"}))

    om_values: list[int] = []
    st_values: list[int] = []
    start = end = None
    errors: list[str] = []
    try:
        om_data = fetch_json(om_url)
        start, end = _night_bounds(om_data, requested_date)
        om_values = _open_meteo_clouds(om_data, start, end)
    except Exception as exc:
        errors.append(f"Open-Meteo unavailable: {exc}")

    try:
        st_data = fetch_json(st_url)
        if start is None or end is None:
            # Conservative local-evening fallback when Open-Meteo cannot provide
            # the site's timezone and solar bounds.
            offset = datetime.now().astimezone().tzinfo
            start = datetime.fromisoformat(f"{requested_date}T18:00").replace(tzinfo=offset)
            end = start + timedelta(hours=12)
        st_values = _seven_timer_clouds(st_data, start, end)
    except Exception as exc:
        errors.append(f"7Timer unavailable: {exc}")

    if not om_values and not st_values:
        log.warning("[weather] all forecast sources failed: %s", "; ".join(errors))
        return True, "weather unavailable — plan retained"

    parts: list[str] = []
    om_avg = _mean(om_values) if om_values else None
    st_avg = _mean(st_values) if st_values else None
    if om_avg is not None:
        parts.append(f"Open-Meteo {om_avg}% clouds ({min(om_values)}–{max(om_values)}%)")
    if st_avg is not None:
        parts.append(f"7Timer {st_avg}% clouds")
    if start and end:
        parts.append(f"night window {start.strftime('%-I:%M%p')}–{end.strftime('%-I:%M%p')}")

    if om_avg is not None and st_avg is not None:
        if abs(om_avg - st_avg) >= _DISAGREEMENT_PCT:
            parts.append("models disagree — uncertain, plan retained")
            proceed = True
        else:
            proceed = om_avg < _CLEAR_THRESHOLD_PCT and st_avg < _CLEAR_THRESHOLD_PCT
    else:
        only = om_avg if om_avg is not None else st_avg
        proceed = bool(only is not None and only < _CLEAR_THRESHOLD_PCT)
        parts.append("single-source forecast")

    summary = " · ".join(parts)
    log.info("[weather] %s (proceed=%s)", summary, proceed)
    return proceed, summary
