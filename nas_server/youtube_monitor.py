#!/usr/bin/env python3
"""YouTube channel monitor → Telegram (read-only, API-key only).

Polls the Measure the Pixels channel and its videos via the YouTube Data API v3
and pushes CHANGES to the SeeStar Telegram bot:
  - new comments / replies on any video (so Henry can answer early — the window
    that matters most for a new channel),
  - subscriber-count milestones,
  - per-video view milestones,
  - first-24h velocity on a freshly published video.

State (last-seen comment ids, last counts, per-video first-seen time) lives in a
JSON file so each run only reports what changed. Designed to run unattended from a
systemd timer every few hours. No OAuth, no token expiry — public data only.

Quota: the Data API gives 10,000 units/day. This run costs ~1 (channels) + ~1 per
video (playlistItems page) + ~1 per video with comments (commentThreads) — a handful
of units per run, trivially within budget even hourly.
"""
from __future__ import annotations

import glob
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SETTINGS = Path("__DATA_DIR__/settings.json")
STATE = Path("__DATA_DIR__/youtube_monitor_state.json")
API = "https://www.googleapis.com/youtube/v3/"

# milestone ladders — report the first time a count crosses each rung
SUB_MILESTONES = [1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000]
VIEW_MILESTONES = [10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000, 1000000]
MAX_COMMENTS_PER_RUN = 15          # don't flood on the first run of a busy video
MAX_VIDEOS = 50                    # newest N videos tracked for comments/views


FOLIO_DIR = Path("__REPO_ROOT__/nas_server/target_folios")

# catalog designation, e.g. "M 31", "NGC7000", "IC 1805", "Sh2-101", "C 14", "Abell 39".
# Sharpless ("Sh 2-101" / "Sh2-101") is matched first so its internal "2" isn't mistaken
# for the whole number.
_CAT_RE = re.compile(
    r"\b(?:Sh\s*2?\s*-?\s*\d{1,4}"
    r"|(?:Messier|M|NGC|IC|Abell|Caldwell|C)\s*-?\s*\d{1,4})\b",
    re.IGNORECASE)


def _norm(s: str) -> str:
    """Fold a name to a match key: uppercase, alphanumerics only ('Sh 2-101'→'SH2101')."""
    return re.sub(r"[^A-Z0-9]", "", s.upper())
# intent that this comment is asking for a target to be shot next
_REQUEST_RE = re.compile(
    r"\b(do|shoot|image|capture|try|next|please|can you|could you|would love|"
    r"want to see|how about|suggest|request)\b", re.IGNORECASE)


def _build_target_index() -> dict[str, str]:
    """{normalized_name → canonical target} from every folio (designation + common name).
    Lets a comment saying 'Andromeda' or 'the Rosette' resolve to a real target."""
    idx: dict[str, str] = {}
    # sorted for determinism: with setdefault, the first folio to claim an ambiguous
    # nickname word wins the same way every run (a mis-claim is caught in suggestion review)
    for f in sorted(glob.glob(str(FOLIO_DIR / "*.json"))):
        try:
            d = json.loads(Path(f).read_text())
        except Exception:
            continue
        tgt = d.get("target")
        if not tgt:
            continue
        idx[_norm(tgt)] = tgt
        # Messier/Caldwell spelled-out aliases so "Messier 31" resolves too
        mm = re.match(r"([A-Za-z]+)\s*(\d+)", tgt)
        if mm and mm.group(1).upper() in ("M", "C"):
            full = {"M": "MESSIER", "C": "CALDWELL"}[mm.group(1).upper()]
            idx[full + mm.group(2)] = tgt
        cn = d.get("common_name")
        if cn and cn.lower() not in ("", tgt.lower()):
            idx[_norm(cn)] = tgt                           # "ANDROMEDAGALAXY" → "M 31"
            # also index the distinctive word ("ROSETTE" from "Rosette Nebula")
            for w in cn.split():
                if len(w) >= 5 and w.lower() not in ("nebula", "galaxy", "cluster",
                                                     "region", "complex"):
                    idx.setdefault(w.upper(), tgt)
    return idx


def classify_comment(text: str, target_idx: dict[str, str]) -> tuple[str, str | None]:
    """Return (kind, target). kind ∈ {request, question, aesthetic, other}.
    Only 'request' (a recognized target + asking intent, or a bare catalog mention)
    is acted on in this version — it becomes a reviewable suggestion."""
    t = text.strip()
    low = t.lower()

    # find a target: catalog designation first (high precision), then a folio name
    target = None
    m = _CAT_RE.search(t)
    if m:
        target = target_idx.get(_norm(m.group(0))) or _normalize_cat(m.group(0))
    if not target:
        norm_low = _norm(t)
        for name, canon in target_idx.items():
            if len(name) >= 5 and name in norm_low:
                target = canon
                break

    if target and (_REQUEST_RE.search(low) or (m and len(t) < 60)):
        return "request", target
    if "?" in t or re.match(r"\s*(why|how|what|which|does|did|is|are)\b", low):
        return "question", target
    if re.search(r"\b(too |more |less )?(green|red|blue|dark|bright|colou?r|saturat|"
                 r"noisy|grain|magenta|teal)\b", low):
        return "aesthetic", target
    return "other", target


def _normalize_cat(raw: str) -> str:
    """'ngc7000' → 'NGC 7000', 'm31' → 'M 31', 'Sh 2-101' → 'Sh2-101' — a readable
    canonical for a target with no folio yet."""
    r = raw.strip()
    sh = re.match(r"Sh\s*2?\s*-?\s*(\d+)", r, re.IGNORECASE)
    if sh:
        return f"Sh2-{sh.group(1)}"
    mm = re.match(r"([A-Za-z]+)\s*-?\s*(\d+)", r)
    if not mm:
        return r
    pfx = {"MESSIER": "M", "CALDWELL": "C"}.get(mm.group(1).upper(), mm.group(1).upper())
    return f"{pfx} {mm.group(2)}"


def _get(endpoint: str, params: dict) -> dict:
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _crossed(old: int, new: int, ladder: list[int]) -> int | None:
    """Highest milestone newly crossed between old and new (None if none)."""
    hit = [m for m in ladder if old < m <= new]
    return max(hit) if hit else None


def main(verbose: bool = False) -> None:
    s = _load_json(SETTINGS, {})
    key = s.get("youtube_api_key")
    channel_id = s.get("youtube_channel_id")
    if not key or not channel_id:
        print("youtube_api_key / youtube_channel_id missing from settings.json", file=sys.stderr)
        sys.exit(1)

    from nas_server import telegram, database
    telegram.configure(s["telegram_token"], s["telegram_chat_id"])
    target_idx = _build_target_index()

    state = _load_json(STATE, {})
    seen_comments = set(state.get("seen_comments", []))
    last_subs = state.get("last_subs", 0)
    video_state = state.get("videos", {})       # vid -> {last_views, first_seen, title}
    first_run = not state  # nothing tracked yet → seed silently, don't blast history
    msgs: list[str] = []

    # 1. channel-level: subs + uploads playlist
    ch = _get("channels", {"part": "snippet,statistics,contentDetails",
                           "id": channel_id, "key": key})
    citems = ch.get("items") or []
    if not citems:
        print("channel not found / API error:", json.dumps(ch)[:300], file=sys.stderr)
        sys.exit(1)
    c = citems[0]
    subs = int(c["statistics"].get("subscriberCount", 0))
    uploads = c["contentDetails"]["relatedPlaylists"]["uploads"]

    if not first_run:
        m = _crossed(last_subs, subs, SUB_MILESTONES)
        if m is not None:
            msgs.append(f"🎉 <b>{m} subscribers!</b> (Measure the Pixels)")
    last_subs = subs

    # 2. newest uploads
    vids = []
    try:
        pl = _get("playlistItems", {"part": "snippet,contentDetails",
                                    "playlistId": uploads, "maxResults": MAX_VIDEOS, "key": key})
        vids = pl.get("items", [])
    except Exception as e:
        if verbose:
            print("no uploads yet or playlist error:", e)

    now = int(time.time())
    for it in vids:
        vid = it["contentDetails"]["videoId"]
        title = it["snippet"]["title"]
        vs = video_state.setdefault(vid, {"last_views": 0, "first_seen": now, "title": title})
        vs["title"] = title

        # views milestone
        try:
            st = _get("videos", {"part": "statistics", "id": vid, "key": key})
            vitems = st.get("items") or []
            views = int(vitems[0]["statistics"].get("viewCount", 0)) if vitems else vs["last_views"]
        except Exception:
            views = vs["last_views"]
        if not first_run:
            m = _crossed(vs["last_views"], views, VIEW_MILESTONES)
            if m is not None:
                msgs.append(f"👀 <b>{title}</b> passed <b>{m:,} views</b>.")
            # 24h velocity flag (once, when a young video shows real traction)
            age_h = (now - vs["first_seen"]) / 3600
            if age_h <= 24 and not vs.get("v24_flagged") and views >= 100:
                msgs.append(f"🚀 <b>{title}</b> — {views:,} views in first {age_h:.0f}h.")
                vs["v24_flagged"] = True
        vs["last_views"] = views

        # new comments
        try:
            ct = _get("commentThreads", {"part": "snippet", "videoId": vid,
                                         "maxResults": 20, "order": "time", "key": key})
        except Exception:
            ct = {"items": []}
        new_here = []
        for t in ct.get("items", []):
            cid = t["id"]
            if cid in seen_comments:
                continue
            seen_comments.add(cid)
            top = t["snippet"]["topLevelComment"]["snippet"]
            new_here.append((top.get("authorDisplayName", "?"),
                             top.get("textOriginal", "")))
        if not first_run:
            for author, text in new_here[:MAX_COMMENTS_PER_RUN]:
                kind, req_target = classify_comment(text, target_idx)
                tag = ""
                if kind == "request" and req_target:
                    # NOVA "reads them and decides what to build next": a viewer request
                    # becomes a reviewable suggestion (deduped per target). Henry approves
                    # it into a planner priority bump — no unattended acquisition change.
                    sid = database.add_agent_suggestion(
                        description=(f"Viewer @{author} requested target on YouTube: "
                                     f"{req_target} — “{text[:140]}”"),
                        file_hint="nas_server/planner.py",
                        source="comment",
                        dedup_key=f"comment-target:{req_target}")
                    tag = (f"\n📥 filed as target request → <b>{req_target}</b> "
                           f"(suggestion #{sid})" if sid
                           else f"\n📥 {req_target} already in the request queue")
                elif kind in ("question", "aesthetic"):
                    tag = f"\n🏷️ {kind}" + (f" · {req_target}" if req_target else "")
                msgs.append(f"💬 <b>{author}</b> on <i>{title}</i>:\n{text[:200]}{tag}")

    # 3. send + persist
    if first_run:
        telegram.send(f"📡 YouTube monitor armed for <b>Measure the Pixels</b> "
                      f"({subs} subs, {len(vids)} videos). Future comments, "
                      f"milestones, and view velocity will land here.")
    for m in msgs:
        telegram.send(m)
    if verbose:
        print(f"{'seeded' if first_run else 'reported'} {len(msgs)} events; "
              f"subs={subs} videos={len(vids)} seen_comments={len(seen_comments)}")

    STATE.write_text(json.dumps({
        "seen_comments": sorted(seen_comments)[-2000:],   # cap growth
        "last_subs": last_subs,
        "videos": video_state,
        "updated": now,
    }, indent=1))


if __name__ == "__main__":
    main(verbose="-v" in sys.argv or "--verbose" in sys.argv)
