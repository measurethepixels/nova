"""Cloudflare API capabilities for measurethepixels.com: real zone analytics
(the GraphQL Analytics API, not the deprecated REST dashboard endpoint) and
cache purging.

Needs an API token scoped to the zone with Zone:Analytics:Read and
Zone:Cache Purge:Purge (created directly in the Cloudflare dashboard --
no OAuth consent flow, unlike the YouTube module). The token, zone_id, and
account_id live outside the repo at
__DATA_DIR__/cloudflare_api_token.json (gitignored
path, same treatment as settings.json), permissions locked to 600.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

TOKEN_PATH = Path("__DATA_DIR__/cloudflare_api_token.json")
_API = "https://api.cloudflare.com/client/v4"


class NotConfiguredError(RuntimeError):
    """Raised when no Cloudflare API token has been set up yet."""


def _load_config() -> dict[str, str]:
    if not TOKEN_PATH.exists():
        raise NotConfiguredError(
            f"No Cloudflare API token at {TOKEN_PATH}. Create a scoped "
            "token in the Cloudflare dashboard first."
        )
    cfg = json.loads(TOKEN_PATH.read_text())
    if not cfg.get("zone_id"):
        raise NotConfiguredError(
            f"{TOKEN_PATH} has a token but no zone_id -- look it up via "
            "GET /zones?name=<domain> and add it."
        )
    return cfg


def _headers(cfg: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json"}


def pull_analytics(
    since: str,
    until: str,
    limit: int = 30,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Real daily zone analytics via the GraphQL Analytics API: requests,
    bytes transferred, page views, cached vs. total requests/bytes, and
    unique visitors. Dates are 'YYYY-MM-DD'. Returns the list of per-day
    rows (already unwrapped from the GraphQL envelope), oldest first.
    """
    cfg = _load_config()
    query = """
    query ($zoneTag: string, $since: string, $until: string, $limit: int) {
      viewer {
        zones(filter: {zoneTag: $zoneTag}) {
          httpRequests1dGroups(
            limit: $limit,
            filter: {date_geq: $since, date_leq: $until},
            orderBy: [date_ASC]
          ) {
            dimensions { date }
            sum { requests, bytes, pageViews, cachedRequests, cachedBytes }
            uniq { uniques }
          }
        }
      }
    }
    """
    resp = (session or requests).post(
        f"{_API}/graphql",
        headers=_headers(cfg),
        json={
            "query": query,
            "variables": {
                "zoneTag": cfg["zone_id"], "since": since, "until": until, "limit": limit,
            },
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"Cloudflare GraphQL error: {data['errors']}")
    zones = data["data"]["viewer"]["zones"]
    return zones[0]["httpRequests1dGroups"] if zones else []


def purge_cache(
    urls: list[str] | None = None,
    purge_everything: bool = False,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Purge Cloudflare's edge cache for this zone.

    Pass specific `urls` (full URLs, e.g. 'https://measurethepixels.com/')
    to purge just those, or purge_everything=True to clear the whole zone
    -- exactly one of the two must be meaningful; passing neither purges
    nothing and Cloudflare will reject the request.
    """
    if purge_everything and urls:
        raise ValueError("pass urls OR purge_everything, not both")
    if not purge_everything and not urls:
        raise ValueError("must pass urls or purge_everything=True")

    cfg = _load_config()
    body = {"purge_everything": True} if purge_everything else {"files": urls}
    resp = (session or requests).post(
        f"{_API}/zones/{cfg['zone_id']}/purge_cache",
        headers=_headers(cfg),
        json=body,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Cloudflare purge failed: {data.get('errors')}")
    return data
