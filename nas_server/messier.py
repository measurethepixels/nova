"""Messier Wall — all 110 Messier objects on a weighted bento grid.

Tile area is proportional to the object's apparent size (log-scaled major axis)
with a boost for the classic showpieces, in the spirit of the y-kim
relative-apparent-size Messier poster — but live: each tile shows Henry's own
best processed image where one exists (linking to the target folio), and
renders as a dim "not captured" placeholder otherwise, so the wall doubles as
a visual acquisition checklist that fills in over time.

Data: static catalog below (approximate major-axis arcmin + magnitude — only
used for tile weighting and the hover line, not for science). Imagery/scores:
the same worklist query + preview-thumb resolution the Worklist page uses.
"""
from __future__ import annotations

import urllib.parse as _uparse

# num, common name ('' if none), type code, major axis arcmin, visual mag
# type codes: GX galaxy, GC globular, OC open cluster, PN planetary nebula,
# EN emission nebula, RN reflection nebula, SN supernova remnant,
# DS double star, AS asterism, SC star cloud
_CATALOG = [
    (1,  "Crab Nebula",          "SN", 6.0,  8.4),
    (2,  "",                     "GC", 16.0, 6.6),
    (3,  "",                     "GC", 18.0, 6.2),
    (4,  "",                     "GC", 26.0, 5.9),
    (5,  "",                     "GC", 20.0, 6.7),
    (6,  "Butterfly Cluster",    "OC", 25.0, 4.2),
    (7,  "Ptolemy's Cluster",    "OC", 80.0, 3.3),
    (8,  "Lagoon Nebula",        "EN", 90.0, 6.0),
    (9,  "",                     "GC", 12.0, 8.4),
    (10, "",                     "GC", 20.0, 6.4),
    (11, "Wild Duck Cluster",    "OC", 14.0, 6.3),
    (12, "",                     "GC", 16.0, 7.7),
    (13, "Hercules Cluster",     "GC", 20.0, 5.8),
    (14, "",                     "GC", 11.0, 8.3),
    (15, "",                     "GC", 18.0, 6.2),
    (16, "Eagle Nebula",         "EN", 35.0, 6.4),
    (17, "Omega Nebula",         "EN", 11.0, 6.0),
    (18, "",                     "OC", 9.0,  7.5),
    (19, "",                     "GC", 17.0, 7.5),
    (20, "Trifid Nebula",        "EN", 28.0, 6.3),
    (21, "",                     "OC", 13.0, 6.5),
    (22, "Sagittarius Cluster",  "GC", 32.0, 5.1),
    (23, "",                     "OC", 27.0, 6.9),
    (24, "Sagittarius Star Cloud", "SC", 90.0, 4.6),
    (25, "",                     "OC", 32.0, 4.6),
    (26, "",                     "OC", 15.0, 8.0),
    (27, "Dumbbell Nebula",      "PN", 8.0,  7.4),
    (28, "",                     "GC", 11.0, 7.7),
    (29, "",                     "OC", 7.0,  7.1),
    (30, "",                     "GC", 12.0, 7.7),
    (31, "Andromeda Galaxy",     "GX", 190.0, 3.4),
    (32, "",                     "GX", 8.0,  8.1),
    (33, "Triangulum Galaxy",    "GX", 71.0, 5.7),
    (34, "",                     "OC", 35.0, 5.5),
    (35, "",                     "OC", 28.0, 5.3),
    (36, "",                     "OC", 12.0, 6.3),
    (37, "",                     "OC", 24.0, 6.2),
    (38, "",                     "OC", 21.0, 7.4),
    (39, "",                     "OC", 32.0, 4.6),
    (40, "Winnecke 4",           "DS", 0.8,  9.7),
    (41, "",                     "OC", 38.0, 4.5),
    (42, "Orion Nebula",         "EN", 85.0, 4.0),
    (43, "De Mairan's Nebula",   "EN", 20.0, 9.0),
    (44, "Beehive Cluster",      "OC", 95.0, 3.7),
    (45, "Pleiades",             "OC", 110.0, 1.6),
    (46, "",                     "OC", 27.0, 6.1),
    (47, "",                     "OC", 30.0, 4.2),
    (48, "",                     "OC", 54.0, 5.5),
    (49, "",                     "GX", 10.0, 8.4),
    (50, "",                     "OC", 16.0, 5.9),
    (51, "Whirlpool Galaxy",     "GX", 11.0, 8.4),
    (52, "",                     "OC", 13.0, 5.0),
    (53, "",                     "GC", 13.0, 8.3),
    (54, "",                     "GC", 12.0, 8.4),
    (55, "",                     "GC", 19.0, 7.4),
    (56, "",                     "GC", 8.8,  8.3),
    (57, "Ring Nebula",          "PN", 1.4,  8.8),
    (58, "",                     "GX", 6.0,  9.7),
    (59, "",                     "GX", 5.4,  9.6),
    (60, "",                     "GX", 7.4,  8.8),
    (61, "",                     "GX", 6.5,  9.7),
    (62, "",                     "GC", 15.0, 6.5),
    (63, "Sunflower Galaxy",     "GX", 12.6, 8.6),
    (64, "Black Eye Galaxy",     "GX", 10.0, 8.5),
    (65, "",                     "GX", 8.7,  9.3),
    (66, "",                     "GX", 9.1,  8.9),
    (67, "",                     "OC", 30.0, 6.1),
    (68, "",                     "GC", 11.0, 9.7),
    (69, "",                     "GC", 7.1,  8.3),
    (70, "",                     "GC", 8.0,  9.1),
    (71, "",                     "GC", 7.2,  6.1),
    (72, "",                     "GC", 6.6,  9.4),
    (73, "",                     "AS", 2.8,  9.0),
    (74, "Phantom Galaxy",       "GX", 10.5, 10.0),
    (75, "",                     "GC", 6.8,  9.2),
    (76, "Little Dumbbell",      "PN", 2.7,  10.1),
    (77, "Cetus A",              "GX", 7.1,  8.9),
    (78, "",                     "RN", 8.0,  8.3),
    (79, "",                     "GC", 9.6,  8.6),
    (80, "",                     "GC", 10.0, 7.9),
    (81, "Bode's Galaxy",        "GX", 27.0, 6.9),
    (82, "Cigar Galaxy",         "GX", 11.2, 8.4),
    (83, "Southern Pinwheel",    "GX", 12.9, 7.5),
    (84, "",                     "GX", 6.5,  9.1),
    (85, "",                     "GX", 7.1,  9.1),
    (86, "",                     "GX", 8.9,  8.9),
    (87, "Virgo A",              "GX", 8.3,  8.6),
    (88, "",                     "GX", 6.9,  9.6),
    (89, "",                     "GX", 5.1,  9.8),
    (90, "",                     "GX", 9.5,  9.5),
    (91, "",                     "GX", 5.4,  10.2),
    (92, "",                     "GC", 14.0, 6.3),
    (93, "",                     "OC", 22.0, 6.0),
    (94, "Croc's Eye",           "GX", 11.2, 8.2),
    (95, "",                     "GX", 7.4,  9.7),
    (96, "",                     "GX", 7.6,  9.2),
    (97, "Owl Nebula",           "PN", 3.4,  9.9),
    (98, "",                     "GX", 9.8,  10.1),
    (99, "",                     "GX", 5.4,  9.9),
    (100, "",                    "GX", 7.4,  9.3),
    (101, "Pinwheel Galaxy",     "GX", 28.8, 7.9),
    (102, "Spindle Galaxy",      "GX", 5.2,  9.9),
    (103, "",                    "OC", 6.0,  7.4),
    (104, "Sombrero Galaxy",     "GX", 8.7,  8.0),
    (105, "",                    "GX", 5.4,  9.3),
    (106, "",                    "GX", 18.6, 8.4),
    (107, "",                    "GC", 13.0, 8.9),
    (108, "",                    "GX", 8.7,  10.0),
    (109, "",                    "GX", 7.6,  9.8),
    (110, "",                    "GX", 21.9, 8.9),
]

# Classic showpieces get one extra span step — "popularity" in the tile weight.
_SHOWPIECES = {1, 8, 13, 16, 17, 20, 27, 31, 33, 42, 45, 51, 57, 64,
               81, 82, 97, 101, 104}

_TYPE_LABEL = {
    "GX": "Galaxy", "GC": "Globular cluster", "OC": "Open cluster",
    "PN": "Planetary nebula", "EN": "Emission nebula", "RN": "Reflection nebula",
    "SN": "Supernova remnant", "DS": "Double star", "AS": "Asterism",
    "SC": "Star cloud",
}
_TYPE_GLYPH = {
    "GX": "&#127756;", "GC": "&#10057;", "OC": "&#10023;", "PN": "&#9678;",
    "EN": "&#9729;", "RN": "&#9729;", "SN": "&#10040;", "DS": "&#10023;",
    "AS": "&#10023;", "SC": "&#10023;",
}


def _span(size_arcmin: float, num: int) -> int:
    """Tile span (1..6 grid cells per side) from log apparent size + showpiece boost."""
    if size_arcmin < 3:
        s = 1
    elif size_arcmin < 8:
        s = 2
    elif size_arcmin < 20:
        s = 3
    elif size_arcmin < 45:
        s = 4
    elif size_arcmin < 100:
        s = 5
    else:
        s = 6
    if num in _SHOWPIECES:
        s += 1
    return max(1, min(s, 6))


def messier_page() -> str:
    from nas_server.story import _page_shell
    from nas_server.database import get_worklist
    from nas_server.web import _worklist_thumb_url

    rows = {r["target"]: r for r in get_worklist()}
    try:
        from nas_server.messier_tiles import get_tiles
        wall_tiles = get_tiles()
    except Exception:
        wall_tiles = {}

    tiles = []
    captured = 0
    for num, name, tcode, size, mag in _CATALOG:
        target = f"M {num}"
        row = rows.get(target)
        thumb = None
        score = None
        if row:
            thumb = _worklist_thumb_url(target, row.get("best_output_path") or "")
            score = row.get("best_overall")
        span = _span(size, num)
        qt = _uparse.quote(target, safe="")
        label_name = f'<span class="mname">{name}</span>' if (name and span >= 3) else ""
        title = f"M {num}" + (f" — {name}" if name else "") + \
                f" | {_TYPE_LABEL.get(tcode, tcode)} | {size:g}&#8242; | mag {mag:g}"
        wt = wall_tiles.get(str(num))
        if wt:
            # WCS-centered tile (own image, or harvested from a host field that
            # contains this object — e.g. M 32 from an M 31 final)
            host = wt.get("host_target", "")
            guest = wt.get("guest")
            hq = _uparse.quote(host, safe="")
            wscore = wt.get("score")
            captured += 1
            score_chip = (f'<span class="mscore">{wscore:.1f}</span>'
                          if isinstance(wscore, (int, float)) and not guest else "")
            via = (f'<span class="mvia">via {host}</span>' if guest else "")
            tiles.append(
                f'<a class="mtile s{span}" href="/folio/{hq if guest else qt}" '
                f'title="{title}" '
                f'style="background-image:url(\'/messier-tile/m{num}.jpg\')">'
                f'{score_chip}{via}<span class="mlabel">M{num}{label_name}</span></a>')
        elif thumb:
            captured += 1
            score_chip = (f'<span class="mscore">{score:.1f}</span>'
                          if isinstance(score, (int, float)) else "")
            tiles.append(
                f'<a class="mtile s{span}" href="/folio/{qt}" title="{title}" '
                f'style="background-image:url(\'{thumb}\')">'
                f'{score_chip}<span class="mlabel">M{num}{label_name}</span></a>')
        elif row:  # data exists (stack) but no processed preview yet
            tiles.append(
                f'<a class="mtile s{span} mstacked" href="/folio/{qt}" title="{title} — stacked, not processed">'
                f'<span class="mglyph">{_TYPE_GLYPH.get(tcode, "&#10023;")}</span>'
                f'<span class="mlabel">M{num}{label_name}</span></a>')
        else:
            tiles.append(
                f'<div class="mtile s{span} mempty" title="{title} — not captured">'
                f'<span class="mglyph">{_TYPE_GLYPH.get(tcode, "&#10023;")}</span>'
                f'<span class="mlabel">M{num}{label_name}</span></div>')

    css = """
  .mwrap { padding: 1rem; max-width: 1500px; margin: 0 auto; }
  .mgrid { display: grid; gap: 6px;
           grid-template-columns: repeat(auto-fill, minmax(56px, 1fr));
           grid-auto-rows: 56px; grid-auto-flow: dense; }
  @media (min-width: 900px) {
    .mgrid { grid-template-columns: repeat(auto-fill, minmax(68px, 1fr));
             grid-auto-rows: 68px; }
  }
  .mtile { position: relative; display: block; border-radius: 8px; overflow: hidden;
           background-color: var(--bg2); background-size: cover;
           background-position: center; border: 1px solid var(--border);
           transition: transform .12s ease, border-color .12s ease; }
  .mtile:hover { transform: scale(1.02); border-color: var(--accent);
                 text-decoration: none; z-index: 2; }
  .s1 { grid-column: span 1; grid-row: span 1; }
  .s2 { grid-column: span 2; grid-row: span 2; }
  .s3 { grid-column: span 3; grid-row: span 3; }
  .s4 { grid-column: span 4; grid-row: span 4; }
  .s5 { grid-column: span 5; grid-row: span 5; }
  .s6 { grid-column: span 6; grid-row: span 6; }
  /* phones: ~5-6 columns exist, a 5/6-span tile would overflow the grid */
  @media (max-width: 640px) {
    .s5, .s6 { grid-column: span 4; grid-row: span 4; }
  }
  .mlabel { position: absolute; left: 6px; bottom: 4px; color: #fff;
            font-weight: 700; font-size: .78rem; line-height: 1.15;
            text-shadow: 0 1px 3px rgba(0,0,0,.9); }
  .mname { display: block; font-weight: 400; font-size: .68rem;
           color: rgba(255,255,255,.85); }
  .mscore { position: absolute; top: 4px; right: 5px; background: rgba(13,17,23,.72);
            color: var(--green); border-radius: 4px; padding: 0 5px;
            font-size: .7rem; font-weight: 700; }
  .mvia { position: absolute; top: 4px; left: 5px; background: rgba(13,17,23,.72);
          color: var(--text2); border-radius: 4px; padding: 0 5px;
          font-size: .62rem; }
  .mempty, .mstacked { border-style: dashed; }
  .mempty .mlabel, .mstacked .mlabel { color: var(--text2); text-shadow: none; }
  .mglyph { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -55%);
            color: var(--bg3); font-size: 1.6rem; }
  .mstacked .mglyph { color: #3a4149; }
  .mstacked { background-color: #141a22; }
  .mkey { display: flex; flex-wrap: wrap; gap: .5rem 1.2rem; align-items: baseline;
          color: var(--text2); font-size: .82rem; margin: .5rem 0 1rem; }
  .mkey b { color: var(--text); }
"""
    body = f"""
<div class="mwrap">
  <h2 style="margin:.4rem 0">Messier Wall</h2>
  <div class="mkey">
    <span><b>{captured}</b> / 110 processed</span>
    <span>tile size &#8776; log apparent size + showpiece boost</span>
    <span style="border:1px dashed var(--border);border-radius:4px;padding:0 6px">dashed = not processed yet</span>
  </div>
  <div class="mgrid">
    {''.join(tiles)}
  </div>
</div>
"""
    return _page_shell("Messier Wall", body, css)
