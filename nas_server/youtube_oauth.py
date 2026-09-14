"""OAuth-authenticated YouTube capabilities: real Analytics API data, comment
replies posted as the channel, and video uploads as a private draft.

Complements youtube_monitor.py's API-key-only public-data path (subscriber
count, view/like/comment counts, milestone alerts) -- that path deliberately
has no OAuth and no write access. This module needs the one-time OAuth
consent completed once via scripts/youtube_oauth_authorize.py; after that,
the saved refresh token renews itself with no further browser interaction.

Every function here is a real, consequential external action once called --
reply_to_comment() posts publicly as the channel, upload_video() puts a real
file on YouTube's servers. Nothing in this module gates that call on its own;
callers are responsible for getting a real go-ahead before invoking either.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials

TOKEN_PATH = Path("__DATA_DIR__/youtube_oauth_token.json")
SETTINGS_PATH = Path("__DATA_DIR__/settings.json")

_DATA_API = "https://www.googleapis.com/youtube/v3"
_UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3/videos"
_ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2"


class NotAuthorizedError(RuntimeError):
    """Raised when no OAuth token has been established yet."""


def _load_token() -> dict[str, Any]:
    if not TOKEN_PATH.exists():
        raise NotAuthorizedError(
            f"No YouTube OAuth token at {TOKEN_PATH}. Run "
            "scripts/youtube_oauth_authorize.py once first."
        )
    return json.loads(TOKEN_PATH.read_text())


def get_session() -> AuthorizedSession:
    """An authenticated requests.Session that attaches and auto-refreshes
    the OAuth access token on every call."""
    tok = _load_token()
    creds = Credentials(
        token=None,
        refresh_token=tok["refresh_token"],
        client_id=tok["client_id"],
        client_secret=tok["client_secret"],
        token_uri=tok["token_uri"],
        scopes=tok["scopes"],
    )
    return AuthorizedSession(creds)


def _default_channel_id() -> str:
    settings = json.loads(SETTINGS_PATH.read_text())
    channel_id = settings.get("youtube_channel_id")
    if not channel_id:
        raise RuntimeError("youtube_channel_id missing from settings.json")
    return channel_id


def pull_analytics(
    start_date: str,
    end_date: str,
    metrics: str = "views,estimatedMinutesWatched,averageViewDuration,"
                   "likes,comments,subscribersGained",
    dimensions: str | None = "day",
    video_id: str | None = None,
    channel_id: str | None = None,
    session: AuthorizedSession | None = None,
) -> dict[str, Any]:
    """Real YouTube Analytics API pull -- retention/traffic-source/
    demographics-tier data the public API key can't reach.

    channel_id defaults to settings.json's youtube_channel_id. Pass
    video_id to scope the report to one video instead of the whole channel.
    Dates are 'YYYY-MM-DD'. Note the Analytics API has its own ~24-48h data
    lag independent of what Studio's dashboard shows -- an empty `rows` for
    very recent dates is normal, not a failure.
    """
    params: dict[str, str] = {
        "ids": f"channel=={channel_id or _default_channel_id()}",
        "startDate": start_date,
        "endDate": end_date,
        "metrics": metrics,
    }
    if dimensions:
        params["dimensions"] = dimensions
    if video_id:
        params["filters"] = f"video=={video_id}"
    resp = (session or get_session()).get(
        f"{_ANALYTICS_API}/reports", params=params, timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def create_playlist(
    title: str,
    description: str = "",
    privacy_status: str = "public",
    session: AuthorizedSession | None = None,
) -> dict[str, Any]:
    """Create a real playlist on the channel. Requires the youtube.force-ssl
    scope. This is a public, permanent action once privacy_status is
    "public" -- there is no draft step; the playlist exists on the channel
    immediately."""
    if privacy_status not in ("private", "unlisted", "public"):
        raise ValueError(f"invalid privacy_status: {privacy_status!r}")
    body = {
        "snippet": {"title": title, "description": description},
        "status": {"privacyStatus": privacy_status},
    }
    resp = (session or get_session()).post(
        f"{_DATA_API}/playlists", params={"part": "snippet,status"}, json=body, timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def add_video_to_playlist(
    playlist_id: str,
    video_id: str,
    session: AuthorizedSession | None = None,
) -> dict[str, Any]:
    """Add a video to a real playlist on the channel. Requires the
    youtube.force-ssl scope. Public and immediate if the playlist is
    public -- no draft step."""
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }
    resp = (session or get_session()).post(
        f"{_DATA_API}/playlistItems", params={"part": "snippet"}, json=body, timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def reply_to_comment(
    parent_comment_id: str,
    text: str,
    session: AuthorizedSession | None = None,
) -> dict[str, Any]:
    """Post a real reply as the channel into an existing comment thread.

    Uses comments.insert (a reply into parent_comment_id), not
    commentThreads.insert (a brand-new top-level comment) -- those are
    different endpoints. Requires the youtube.force-ssl scope. This posts
    publicly and immediately; there is no draft/preview step for a comment.
    """
    body = {"snippet": {"parentId": parent_comment_id, "textOriginal": text}}
    resp = (session or get_session()).post(
        f"{_DATA_API}/comments", params={"part": "snippet"}, json=body, timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def upload_video(
    file_path: str | Path,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy_status: str = "private",
    category_id: str = "28",  # Science & Technology
    contains_synthetic_media: bool = True,
    session: AuthorizedSession | None = None,
) -> dict[str, Any]:
    """Upload a video as a real draft -- private by default, visible only
    to the channel owner in Studio until manually published there.

    Requires the youtube.upload scope. Uses YouTube's resumable upload
    protocol (initiate, then PUT the file to the session URL it returns)
    rather than one multipart POST, since episode files can be large and
    this streams the file rather than loading it into memory.

    `contains_synthetic_media` sets `status.containsSyntheticMedia`, the API
    field behind Studio's "Altered or synthetic content" disclosure toggle
    (docs/channel_playbook.md requires it "on every upload" since NOVA's
    narration is a synthetic voice). Defaults True because every episode
    uploaded through this function so far has synthetic narration; pass
    False explicitly for a video that genuinely has none.
    """
    if privacy_status not in ("private", "unlisted", "public"):
        raise ValueError(f"invalid privacy_status: {privacy_status!r}")
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)

    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": contains_synthetic_media,
        },
    }
    sess = session or get_session()

    init_resp = sess.post(
        _UPLOAD_API,
        params={"part": "snippet,status", "uploadType": "resumable"},
        headers={
            "X-Upload-Content-Type": "video/*",
            "X-Upload-Content-Length": str(file_path.stat().st_size),
        },
        json=metadata,
        timeout=20,
    )
    init_resp.raise_for_status()
    upload_url = init_resp.headers["Location"]

    with open(file_path, "rb") as f:
        put_resp = sess.put(
            upload_url,
            data=f,
            headers={"Content-Type": "video/*"},
            timeout=(30, 3600),  # 30s connect, up to 1h for a large upload
        )
    put_resp.raise_for_status()
    return put_resp.json()


def set_thumbnail(
    video_id: str,
    image_path: str | Path,
    session: AuthorizedSession | None = None,
) -> dict[str, Any]:
    """Set a video's custom thumbnail via thumbnails.set. Requires the
    channel to have verified custom-thumbnail eligibility (phone number) and
    the youtube.upload scope. Works on a private/draft video the same as a
    public one -- there is no separate draft step for a thumbnail."""
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(image_path)
    sess = session or get_session()
    with open(image_path, "rb") as f:
        resp = sess.post(
            "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
            params={"videoId": video_id, "uploadType": "media"},
            data=f,
            headers={"Content-Type": "image/jpeg"},
            timeout=60,
        )
    resp.raise_for_status()
    return resp.json()


def get_video(video_id: str, session: AuthorizedSession | None = None) -> dict[str, Any]:
    """Fetch a video's current snippet + status + paidProductPlacementDetails
    -- the required first step before update_video(), since videos.update
    overwrites the whole part you send, not just the fields you name (see
    update_video's docstring)."""
    resp = (session or get_session()).get(
        f"{_DATA_API}/videos",
        params={"part": "snippet,status,paidProductPlacementDetails", "id": video_id},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items") or []
    if not items:
        raise ValueError(f"no video found for id {video_id!r}")
    return items[0]


def update_video(
    video_id: str,
    description: str | None = None,
    has_paid_product_placement: bool | None = None,
    session: AuthorizedSession | None = None,
) -> dict[str, Any]:
    """Update a live video's description and/or its "Includes paid
    promotion" disclosure (paidProductPlacementDetails.
    hasPaidProductPlacement -- the exact field behind that Studio checkbox).
    Note this is its OWN top-level part on the video resource, NOT nested
    under status, despite living right next to it in Studio's UI.

    videos.update replaces the ENTIRE part you send (snippet, status, or
    paidProductPlacementDetails), not just the fields named in the request
    body -- omitting title/categoryId from snippet, for example, can blank
    them out. To avoid that, this fetches the video's current state first
    (get_video()) and only overwrites the specific field(s) passed in,
    sending the rest back unchanged. Requires the youtube.force-ssl scope.
    This edits a live, public video immediately -- there is no draft/preview
    step, unlike upload_video()'s private-by-default behavior.
    """
    if description is None and has_paid_product_placement is None:
        raise ValueError("pass description and/or has_paid_product_placement")

    sess = session or get_session()
    current = get_video(video_id, session=sess)
    snippet = current["snippet"]
    placement = current.get("paidProductPlacementDetails") or {}

    parts = []
    if description is not None:
        snippet["description"] = description
        parts.append("snippet")
    if has_paid_product_placement is not None:
        placement["hasPaidProductPlacement"] = has_paid_product_placement
        parts.append("paidProductPlacementDetails")

    body: dict[str, Any] = {"id": video_id}
    if "snippet" in parts:
        body["snippet"] = snippet
    if "paidProductPlacementDetails" in parts:
        body["paidProductPlacementDetails"] = placement

    resp = sess.put(
        f"{_DATA_API}/videos",
        params={"part": ",".join(parts)},
        json=body,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()
