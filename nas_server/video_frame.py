"""
Pipeline video frame compositor — split-panel layout.

1920×1080 frames:
  ┌────────────────────┬──────────────────────┐  ← TOP_H (breadcrumb)
  │                    │  PROCESSING STEPS    │
  │   [ Image ]        │  ✓ crop              │
  │   (left 55%)       │  ✓ deconvolution     │
  │                    │  ▶ stretch  ←current │
  │   or Planning Card │  ○ noise_reduction   │
  │                    │  ○ curves            │
  ├────────────────────┴──────────────────────┤  ← H - BAR_H
  │  BOTTOM BAR: step label · stats · score   │
  └───────────────────────────────────────────┘

Text-only planning cards (bullet_lines + no image): left panel becomes card,
  steps panel stays on right.
Legacy mode (no all_steps supplied): original centred layout.
"""
from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance

log = logging.getLogger(__name__)

# ── Canvas dimensions ──────────────────────────────────────────────────────────
W, H    = 1920, 1080
TOP_H   = 46    # stage breadcrumb strip
BAR_H   = 168   # bottom info bar
IMG_H   = H - TOP_H - BAR_H   # 866px image/content zone

# ── Split-panel geometry ───────────────────────────────────────────────────────
SPLIT_X        = 1060   # image panel right edge  (~55%)
IMG_PAD        = 18     # padding inside image panel
STEP_PAD       = 26     # padding inside steps panel
STEP_ROW_H     = 40     # height per step row
STEP_ICON_W    = 20     # icon circle diameter
STEP_HEADER_H  = 56     # header + divider area inside steps panel

# ── Data viz inset panel geometry (bottom-left of image zone) ─────────────────
VIZ_W  = 390             # inset panel width  (px)
VIZ_H  = 220             # inset panel height (px)
VIZ_X  = IMG_PAD + 8    # left edge: 26 px from canvas left
VIZ_Y  = H - BAR_H - IMG_PAD - VIZ_H   # = 674 px — sits above bottom bar

# ── Colour palette ─────────────────────────────────────────────────────────────
C_BG      = (13,  17,  23)
C_BAR     = (22,  27,  34)
C_TOP     = (22,  27,  34)
C_PANEL   = (17,  22,  30)    # steps panel bg (slightly lighter)
C_ROW_HL  = (28,  38,  58)    # current-step row highlight
C_BORDER  = (48,  54,  61)
C_ACCENT  = (88, 166, 255)
C_GREEN   = (63, 185,  80)
C_YELLOW  = (227, 179, 65)
C_RED     = (248,  81, 73)
C_TEXT    = (230, 237, 243)
C_TEXT2   = (139, 148, 158)
C_DIM     = (70,  77,  87)    # pending step text
C_WHITE   = (255, 255, 255)

# ── Font paths ─────────────────────────────────────────────────────────────────
_FONT_R  = "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf"
_FONT_B  = "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"
_FONT_M  = "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf"
import os as _os
_ROBOTO_R = _os.path.expanduser("~/.fonts/Roboto-Regular.ttf")
_ROBOTO_M = _os.path.expanduser("~/.fonts/Roboto-Medium.ttf")
_DEJAVU  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    paths = [_FONT_B if bold else _FONT_R, _FONT_M if mono else _FONT_R, _ROBOTO_R,
             _DEJAVU]
    if mono:
        paths = [_FONT_M, _ROBOTO_R, _DEJAVU]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _score_color(score: float) -> tuple:
    if score >= 7.5:
        return C_GREEN
    if score >= 6.0:
        return C_YELLOW
    return C_RED


def _wrap(text: str, max_chars: int = 60) -> list[str]:
    return textwrap.wrap(text, width=max_chars) or [""]


# ── Core compositor ────────────────────────────────────────────────────────────

def save_frame(
    output_path: str | Path,
    *,
    image_path: str | Path | None = None,
    stage: str = "process",
    step_label: str = "",
    caption: str = "",
    commentary: str = "",
    stats: dict[str, str] | None = None,
    score: float | None = None,
    score_delta: float | None = None,
    bullet_lines: list[str] | None = None,
    target: str = "",
    duration_s: float = 3.0,
    # Step-panel state (supplied by VideoSession via set_step_context)
    all_steps: list[str] | None = None,
    completed_steps: list[str] | None = None,
    current_step: str | None = None,
    # Optional data visualization inset (curve graph or histogram)
    data_viz: dict | None = None,
) -> Path:
    """
    Render a 1920×1080 annotated video frame and save to output_path.

    Split-panel mode (all_steps supplied):
      • Left ~55%: image or planning card text
      • Right ~45%: steps progress list

    Legacy mode (no all_steps): centred image with optional text card, unchanged.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    has_split = bool(all_steps)

    canvas = _build_canvas(image_path, split=has_split)

    # Data viz inset — drawn on top of the image but beneath all UI chrome
    if data_viz:
        try:
            _draw_data_viz_inset(canvas, data_viz)
        except Exception as _dve:
            log.debug(f"[video_frame] data_viz inset failed: {_dve}")

    draw = ImageDraw.Draw(canvas)

    _draw_top_strip(draw, stage)
    _draw_bottom_bar(draw, canvas, step_label, caption, stats or {}, score, score_delta, target)

    if has_split:
        # Right panel — steps list; swap for planning-card content when bullet_lines present
        if bullet_lines:
            _draw_steps_panel_card(draw, step_label, caption, bullet_lines,
                                   all_steps, completed_steps, current_step)
        else:
            _draw_steps_panel(draw, all_steps, completed_steps, current_step)

        # Left panel text card when no image was provided
        if image_path is None and bullet_lines:
            _draw_left_panel_card(draw, step_label, caption, bullet_lines)
    else:
        # Legacy: full-width text card (no split)
        if image_path is None and bullet_lines:
            _draw_text_card(draw, step_label, caption, bullet_lines)

    canvas.save(str(out), "JPEG", quality=93, optimize=True)
    return out


# ── Layer builders ─────────────────────────────────────────────────────────────

def _build_canvas(image_path: str | Path | None, split: bool = False) -> Image.Image:
    """Create base canvas with bokeh background + sharp source image."""
    canvas = Image.new("RGB", (W, H), C_BG)

    if image_path is None:
        if split:
            # Fill steps panel area with panel colour
            draw = ImageDraw.Draw(canvas)
            draw.rectangle([SPLIT_X, TOP_H, W, H - BAR_H], fill=C_PANEL)
        return canvas

    src = Image.open(str(image_path)).convert("RGB")
    iw, ih = src.size

    panel_w = SPLIT_X if split else W

    # ── Blurred bokeh background (image panel only) ───────────────────────────
    bg = src.resize((panel_w, H), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=45))
    bg = ImageEnhance.Brightness(bg).enhance(0.15)
    canvas.paste(bg, (0, 0))

    # ── Steps panel solid bg ─────────────────────────────────────────────────
    if split:
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([SPLIT_X, TOP_H, W, H - BAR_H], fill=C_PANEL)

    # ── Sharp source image — fit within image zone ───────────────────────────
    zone_w = panel_w - 2 * IMG_PAD
    zone_h = IMG_H - 2 * IMG_PAD

    scale = min(zone_w / iw, zone_h / ih)
    fit_w = int(iw * scale)
    fit_h = int(ih * scale)
    sharp = src.resize((fit_w, fit_h), Image.LANCZOS)

    # Centre within panel zone
    x = IMG_PAD + (zone_w - fit_w) // 2
    y = TOP_H + IMG_PAD + (zone_h - fit_h) // 2
    canvas.paste(sharp, (x, y))

    draw = ImageDraw.Draw(canvas)
    _draw_image_glow(draw, x, y, fit_w, fit_h)

    return canvas


def _draw_image_glow(draw: ImageDraw.Draw, x: int, y: int, w: int, h: int) -> None:
    """Faint border/glow around the sharp image."""
    for i in [3, 2, 1]:
        draw.rectangle([x - i, y - i, x + w + i - 1, y + h + i - 1], outline=C_BORDER)


def _draw_top_strip(draw: ImageDraw.Draw, current_stage: str) -> None:
    """Stage breadcrumb: STACK › PROCESS › DONE"""
    draw.rectangle([0, 0, W, TOP_H], fill=C_TOP)
    draw.line([0, TOP_H - 1, W, TOP_H - 1], fill=C_BORDER, width=1)

    stages = [("stack", "STACK"), ("process", "PROCESS"), ("done", "DONE")]
    fn_sm   = _font(14)
    fn_sm_b = _font(14, bold=True)
    sep = "  ›  "

    parts = [(sid, slabel, sid == current_stage) for sid, slabel in stages]
    full = sep.join(p[1] for p in parts)
    bbox = draw.textbbox((0, 0), full, font=fn_sm_b)
    cx = (W - (bbox[2] - bbox[0])) // 2
    y_c = TOP_H // 2 - 7

    for i, (sid, slabel, is_current) in enumerate(parts):
        fn    = fn_sm_b if is_current else fn_sm
        color = C_ACCENT if is_current else C_TEXT2
        draw.text((cx, y_c), slabel, font=fn, fill=color)
        bbox  = draw.textbbox((cx, y_c), slabel, font=fn)
        cx    = bbox[2]
        if i < len(parts) - 1:
            draw.text((cx, y_c), sep, font=fn_sm, fill=C_BORDER)
            cx = draw.textbbox((cx, y_c), sep, font=fn_sm)[2]


def _draw_bottom_bar(
    draw: ImageDraw.Draw,
    canvas: Image.Image,
    step_label: str,
    caption: str,
    stats: dict[str, str],
    score: float | None,
    delta: float | None,
    target: str,
) -> None:
    bar_y = H - BAR_H
    draw.rectangle([0, bar_y, W, H], fill=C_BAR)
    draw.line([0, bar_y, W, bar_y], fill=C_BORDER, width=1)

    fn_label  = _font(32, bold=True)
    fn_cap    = _font(20)
    fn_target = _font(15)
    PAD = 32

    ty = bar_y + 14
    if target:
        draw.text((PAD, ty), target.upper(), font=fn_target, fill=C_TEXT2)
        ty += 22

    draw.text((PAD, ty), step_label or "Processing", font=fn_label, fill=C_TEXT)
    ty += 44

    if caption:
        for line in _wrap(caption, max_chars=55)[:2]:
            draw.text((PAD, ty), line, font=fn_cap, fill=C_TEXT2)
            ty += 28

    if score is not None:
        _draw_score_badge(draw, score, delta)

    if stats:
        _draw_stat_pills(draw, stats, bar_y)


def _draw_score_badge(draw: ImageDraw.Draw, score: float, delta: float | None) -> None:
    fn_score = _font(48, bold=True)
    fn_denom = _font(22)
    fn_delta = _font(18)
    color    = _score_color(score)
    bar_y    = H - BAR_H
    PAD_R    = 40

    score_text = f"{score:.1f}"
    denom_text = "/10"
    score_w = draw.textbbox((0, 0), score_text, font=fn_score)[2]
    denom_w = draw.textbbox((0, 0), denom_text, font=fn_denom)[2]

    r  = 44
    cx = W - PAD_R - r
    cy = bar_y + BAR_H // 2 - 6

    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=C_BG)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=3)
    draw.text((cx - score_w // 2 - 4, cy - 28), score_text, font=fn_score, fill=color)
    draw.text((cx + score_w // 2 - 8, cy + 4), denom_text, font=fn_denom, fill=C_TEXT2)

    if delta is not None and abs(delta) > 0.05:
        sign  = "↑" if delta > 0 else "↓"
        dcol  = C_GREEN if delta > 0 else C_RED
        draw.text((cx - 20, cy + 30), f"{sign}{abs(delta):.1f}", font=fn_delta, fill=dcol)


def _draw_stat_pills(draw: ImageDraw.Draw, stats: dict[str, str], bar_y: int) -> None:
    fn_key  = _font(15)
    fn_val  = _font(17, bold=True)
    PILL_H  = 36
    PILL_PX = 14
    PILL_G  = 10

    pills: list[tuple[str, str, int]] = []
    for k, v in list(stats.items())[:6]:
        kw = draw.textbbox((0, 0), k, font=fn_key)[2]
        vw = draw.textbbox((0, 0), str(v), font=fn_val)[2]
        pills.append((k, str(v), kw + 6 + vw + PILL_PX * 2))

    total_w = sum(p[2] for p in pills) + PILL_G * (len(pills) - 1)
    x = max(360, min((W - total_w) // 2, W - 300 - total_w))
    y = bar_y + (BAR_H - PILL_H) // 2

    for key, val, pw in pills:
        draw.rounded_rectangle([x, y, x + pw, y + PILL_H], radius=6,
                               fill=C_BG, outline=C_BORDER)
        kw = draw.textbbox((0, 0), key, font=fn_key)[2]
        draw.text((x + PILL_PX, y + 10), key, font=fn_key, fill=C_TEXT2)
        draw.text((x + PILL_PX + kw + 6, y + 8), val, font=fn_val, fill=C_TEXT)
        x += pw + PILL_G


# ── Steps panel ────────────────────────────────────────────────────────────────

def _draw_steps_panel(
    draw: ImageDraw.Draw,
    all_steps: list[str],
    completed_steps: list[str] | None,
    current_step: str | None,
) -> None:
    """Right panel: all steps with done/current/pending state."""
    px      = SPLIT_X + 1          # right of divider
    py_top  = TOP_H
    py_bot  = H - BAR_H
    panel_w = W - px

    # Vertical divider
    draw.line([SPLIT_X, py_top, SPLIT_X, py_bot], fill=C_BORDER, width=1)

    # ── Header ────────────────────────────────────────────────────────────────
    fn_hdr = _font(12)
    hdr_y  = py_top + 16
    draw.text((px + STEP_PAD, hdr_y), "PROCESSING STEPS",
              font=fn_hdr, fill=C_TEXT2)
    div_y = hdr_y + 26
    draw.line([px + STEP_PAD, div_y, W - STEP_PAD, div_y], fill=C_BORDER, width=1)

    # ── Build state lookup ────────────────────────────────────────────────────
    done_set: set[str] = set()
    for s in (completed_steps or []):
        done_set.add(s.split("[")[0])   # strip "[variant]" suffix

    cur_base = (current_step or "").split("[")[0]

    # ── Step rows ─────────────────────────────────────────────────────────────
    # Adaptively shrink row height / font / icon so ALL steps fit the panel.
    # Workflows have grown long enough that a fixed 40px row overflowed and the
    # tail steps were replaced by an ellipsis. Scale to the available height.
    row_top = div_y + 12
    row_end = py_bot - 6
    avail   = row_end - row_top
    n_steps = max(1, len(all_steps))

    row_h = STEP_ROW_H
    if n_steps * row_h > avail:
        row_h = max(18, avail // n_steps)

    fsize  = max(10, min(16, row_h - 8))
    fn_r   = _font(fsize)
    fn_b   = _font(fsize, bold=True)
    icon_w = STEP_ICON_W if row_h >= 30 else max(10, row_h - 8)

    row_y = row_top
    for step in all_steps:
        if row_y + row_h > row_end + row_h:   # hard safety; adaptive sizing avoids this
            break

        sbase      = step.split("[")[0]
        is_done    = sbase in done_set
        is_current = sbase == cur_base

        # Row highlight for current step
        if is_current:
            draw.rounded_rectangle(
                [px + 6, row_y - 2, W - 6, row_y + row_h - 4],
                radius=5, fill=C_ROW_HL,
            )

        # Icon (vertically centred in the row)
        ix = px + STEP_PAD
        iy = row_y + (row_h - icon_w) // 2

        if is_done:
            # Filled green circle with a vector check (the bundled Ubuntu font has
            # no U+2713 glyph, so a drawn tick avoids the empty-box tofu).
            draw.ellipse([ix, iy, ix + icon_w, iy + icon_w], fill=C_GREEN)
            _cw = max(2, icon_w // 7)
            draw.line(
                [(ix + icon_w * 0.27, iy + icon_w * 0.52),
                 (ix + icon_w * 0.43, iy + icon_w * 0.68),
                 (ix + icon_w * 0.74, iy + icon_w * 0.33)],
                fill=C_BG, width=_cw, joint="curve",
            )
        elif is_current:
            # Filled accent circle (solid dot)
            draw.ellipse([ix, iy, ix + icon_w, iy + icon_w], fill=C_ACCENT)
            _inset = max(3, icon_w // 4)
            draw.ellipse([ix + _inset, iy + _inset,
                          ix + icon_w - _inset, iy + icon_w - _inset], fill=C_BG)
        else:
            # Hollow dim circle
            draw.ellipse([ix, iy, ix + icon_w, iy + icon_w],
                         outline=(55, 62, 72), width=1)

        # Label
        display = STEP_DISPLAY.get(sbase, sbase.replace("_", " ").title())
        if is_done:
            color, fn = C_GREEN, fn_r
        elif is_current:
            color, fn = C_ACCENT, fn_b
        else:
            color, fn = C_DIM, fn_r

        _ty = row_y + (row_h - fsize) // 2 - 1
        draw.text((ix + icon_w + 10, _ty), display, font=fn, fill=color)
        row_y += row_h


def _draw_steps_panel_card(
    draw: ImageDraw.Draw,
    step_label: str,
    caption: str,
    bullet_lines: list[str],
    all_steps: list[str] | None,
    completed_steps: list[str] | None,
    current_step: str | None,
) -> None:
    """
    Right panel for planning/summary cards: show bullet content at the top,
    then the normal steps list below.
    """
    px     = SPLIT_X + 1
    py_top = TOP_H
    py_bot = H - BAR_H
    panel_w = W - px

    draw.line([SPLIT_X, py_top, SPLIT_X, py_bot], fill=C_BORDER, width=1)

    fn_h  = _font(17, bold=True)
    fn_b  = _font(15)
    fn_sm = _font(13)

    y = py_top + 18

    # Mini heading (e.g. "Claude Plans Non-Linear Phase")
    label = step_label
    if len(label) > 34:
        label = label[:31] + "…"
    draw.text((px + STEP_PAD, y), label, font=fn_h, fill=C_ACCENT)
    y += 26

    if caption:
        for line in _wrap(caption, max_chars=34)[:2]:
            draw.text((px + STEP_PAD, y), line, font=fn_sm, fill=C_TEXT2)
            y += 18
    y += 4

    div_y = y
    draw.line([px + STEP_PAD, div_y, W - STEP_PAD, div_y], fill=C_BORDER, width=1)
    y = div_y + 10

    # Bullets
    bx = px + STEP_PAD
    for line in (bullet_lines or [])[:8]:
        if y + 24 > py_bot - 60:
            draw.text((bx, y), "…", font=fn_sm, fill=C_DIM)
            y += 22
            break
        if len(line) > 38:
            line = line[:35] + "…"
        draw.ellipse([bx, y + 7, bx + 6, y + 13], fill=C_ACCENT)
        draw.text((bx + 12, y + 1), line, font=fn_b, fill=C_TEXT)
        y += 24

    # Divider + remaining steps compact
    if all_steps:
        y += 6
        draw.line([px + STEP_PAD, y, W - STEP_PAD, y], fill=C_BORDER, width=1)
        y += 8
        fn_tiny = _font(12)

        done_set: set[str] = set()
        for s in (completed_steps or []):
            done_set.add(s.split("[")[0])
        cur_base = (current_step or "").split("[")[0]

        for step in all_steps:
            if y + 22 > py_bot - 6:
                break
            sbase      = step.split("[")[0]
            is_done    = sbase in done_set
            is_current = sbase == cur_base
            display    = STEP_DISPLAY.get(sbase, sbase.replace("_", " ").title())
            if is_done:
                pfx, color = "✓ ", C_GREEN
            elif is_current:
                pfx, color = "▶ ", C_ACCENT
            else:
                pfx, color = "○ ", C_DIM
            draw.text((px + STEP_PAD, y), pfx + display, font=fn_tiny, fill=color)
            y += 22


def _draw_left_panel_card(
    draw: ImageDraw.Draw,
    step_label: str,
    caption: str,
    bullet_lines: list[str],
) -> None:
    """
    Text planning card rendered in the left panel (when image_path is None).
    Centred within 0..SPLIT_X.
    """
    zone_top = TOP_H + 20
    zone_bot = H - BAR_H - 10
    zone_cx  = SPLIT_X // 2

    card_x1 = 30
    card_x2 = SPLIT_X - 30
    card_y1 = zone_top + 20
    card_y2 = zone_bot - 20

    draw.rounded_rectangle([card_x1, card_y1, card_x2, card_y2],
                           radius=12, fill=(22, 27, 34), outline=C_BORDER)

    fn_h1   = _font(34, bold=True)
    fn_h2   = _font(19)
    fn_body = _font(18)
    fn_sm   = _font(15)

    y = card_y1 + 36

    bbox = draw.textbbox((0, 0), step_label, font=fn_h1)
    tw   = bbox[2] - bbox[0]
    draw.text((zone_cx - tw // 2, y), step_label, font=fn_h1, fill=C_TEXT)
    y   += 50

    if caption:
        for line in _wrap(caption, max_chars=48)[:2]:
            bbox = draw.textbbox((0, 0), line, font=fn_h2)
            tw   = bbox[2] - bbox[0]
            draw.text((zone_cx - tw // 2, y), line, font=fn_h2, fill=C_TEXT2)
            y   += 30
        y += 10

    draw.line([card_x1 + 40, y, card_x2 - 40, y], fill=C_BORDER, width=1)
    y += 18

    bx = card_x1 + 44
    for line in (bullet_lines or [])[:10]:
        if not line.strip():
            y += 8
            continue
        if len(line) > 55:
            line = line[:52] + "…"
        if y > card_y2 - 36:
            draw.text((bx + 12, y), "…", font=fn_sm, fill=C_TEXT2)
            break
        draw.ellipse([bx, y + 7, bx + 6, y + 13], fill=C_ACCENT)
        draw.text((bx + 12, y), line, font=fn_body, fill=C_TEXT)
        y += 30


# ── Legacy full-width text card (no split) ─────────────────────────────────────

def _draw_text_card(
    draw: ImageDraw.Draw,
    step_label: str,
    caption: str,
    bullet_lines: list[str],
) -> None:
    """Centred planning card across full canvas width."""
    zone_top = TOP_H + 20
    zone_bot = H - BAR_H - 10
    zone_cx  = W // 2

    card_x1 = W // 2 - 560
    card_x2 = W // 2 + 560
    card_y1 = zone_top + 30
    card_y2 = zone_bot - 30

    draw.rounded_rectangle([card_x1, card_y1, card_x2, card_y2],
                           radius=12, fill=(22, 27, 34), outline=C_BORDER)

    fn_h1   = _font(40, bold=True)
    fn_h2   = _font(22)
    fn_body = _font(20)
    fn_sm   = _font(17)

    y = card_y1 + 40

    bbox = draw.textbbox((0, 0), step_label, font=fn_h1)
    tw   = bbox[2] - bbox[0]
    draw.text((zone_cx - tw // 2, y), step_label, font=fn_h1, fill=C_TEXT)
    y   += 56

    if caption:
        for line in _wrap(caption, max_chars=70)[:2]:
            bbox = draw.textbbox((0, 0), line, font=fn_h2)
            tw   = bbox[2] - bbox[0]
            draw.text((zone_cx - tw // 2, y), line, font=fn_h2, fill=C_TEXT2)
            y   += 32
        y += 12

    draw.line([card_x1 + 40, y, card_x2 - 40, y], fill=C_BORDER, width=1)
    y += 20

    bx = card_x1 + 50
    for line in (bullet_lines or [])[:12]:
        if not line.strip():
            y += 10
            continue
        if len(line) > 80:
            line = line[:77] + "…"
        if y > card_y2 - 40:
            draw.text((bx + 16, y), "…", font=fn_sm, fill=C_TEXT2)
            break
        draw.ellipse([bx, y + 8, bx + 6, y + 14], fill=C_ACCENT)
        draw.text((bx + 16, y), line, font=fn_body, fill=C_TEXT)
        y += 32


# ── Data visualization insets ─────────────────────────────────────────────────

def _draw_data_viz_inset(canvas: Image.Image, data_viz: dict) -> None:
    """Dispatch to curve or histogram inset renderer."""
    viz_type = data_viz.get("type", "")
    if viz_type == "curve":
        _draw_curve_inset(canvas, data_viz)
    elif viz_type == "histogram":
        _draw_histogram_inset(canvas, data_viz)


def _viz_panel_bg(
    draw: ImageDraw.Draw,
    x: int, y: int, w: int, h: int,
    title: str,
) -> tuple[int, int, int, int]:
    """
    Draw inset panel: dark rounded rect + title strip.
    Returns (cx1, cy1, cx2, cy2) — the inner chart rectangle.
    """
    draw.rounded_rectangle([x, y, x + w, y + h], radius=8,
                           fill=(10, 14, 20), outline=(58, 68, 84), width=1)
    fn_title = _font(11)
    draw.text((x + 14, y + 7), title.upper(), font=fn_title, fill=C_TEXT2)
    # Inner chart area (padding inside the panel)
    cx1, cy1 = x + 18, y + 27
    cx2, cy2 = x + w - 12, y + h - 20
    draw.rectangle([cx1, cy1, cx2, cy2], fill=(7, 10, 16), outline=(32, 40, 52))
    return cx1, cy1, cx2, cy2


def _viz_grid(
    draw: ImageDraw.Draw,
    cx1: int, cy1: int, cx2: int, cy2: int,
    n: int = 4,
) -> None:
    """Faint grid lines across the chart area."""
    cw, ch = cx2 - cx1, cy2 - cy1
    for i in range(1, n):
        gx = cx1 + i * cw // n
        gy = cy1 + i * ch // n
        draw.line([gx, cy1, gx, cy2], fill=(20, 26, 34), width=1)
        draw.line([cx1, gy, cx2, gy], fill=(20, 26, 34), width=1)


def _draw_curve_inset(canvas: Image.Image, data: dict) -> None:
    """
    Tone curve inset — bottom-left of the image zone.

    data keys:
      points     [[in, out], ...]   control points (sorted by input)
      sky_before float | None       sky_bg value before curves (marks input level)
      sky_after  float | None       sky_bg target after curves (marks output level)
    """
    import numpy as np

    points = data.get("points", [])
    if not points or len(points) < 2:
        return

    draw = ImageDraw.Draw(canvas)
    cx1, cy1, cx2, cy2 = _viz_panel_bg(draw, VIZ_X, VIZ_Y, VIZ_W, VIZ_H,
                                        "Tone Curve — data-driven")
    _viz_grid(draw, cx1, cy1, cx2, cy2, n=4)

    cw, ch = cx2 - cx1, cy2 - cy1

    def to_px(iv: float, ov: float) -> tuple[int, int]:
        return (
            int(cx1 + max(0.0, min(1.0, iv)) * cw),
            int(cy2 - max(0.0, min(1.0, ov)) * ch),   # Y flipped
        )

    # Identity diagonal (dashed grey)
    dash_step = 10
    for t in range(0, 100, dash_step * 2):
        t0, t1 = t / 100, min(1.0, (t + dash_step) / 100)
        draw.line([to_px(t0, t0), to_px(t1, t1)], fill=(44, 54, 66), width=1)

    # Curve — linear interpolation through control points is close enough for viz
    pts = sorted(points, key=lambda p: p[0])
    xs  = np.array([p[0] for p in pts])
    ys  = np.array([p[1] for p in pts])
    xi  = np.linspace(0.0, 1.0, 300)
    yi  = np.clip(np.interp(xi, xs, ys), 0.0, 1.0)

    line_pts = [to_px(float(xi[i]), float(yi[i])) for i in range(len(xi))]
    for i in range(len(line_pts) - 1):
        draw.line([line_pts[i], line_pts[i + 1]], fill=C_ACCENT, width=2)

    # Control point dots (tiny white dots)
    for p in pts:
        px_, py_ = to_px(p[0], p[1])
        draw.ellipse([px_ - 2, py_ - 2, px_ + 2, py_ + 2], fill=C_WHITE)

    fn_tiny = _font(10)

    # Sky-before marker: yellow dot on the curve at input = sky_bg
    sky_before = data.get("sky_before")
    sky_after  = data.get("sky_after")
    if sky_before is not None and 0 < sky_before < 1:
        sky_y_curve = float(np.interp(sky_before, xs, ys))
        sx, sy = to_px(sky_before, sky_y_curve)
        draw.ellipse([sx - 5, sy - 5, sx + 5, sy + 5], fill=C_YELLOW, outline=(0, 0, 0))
        label_x = sx + 7 if sx < cx2 - 60 else sx - 50
        draw.text((label_x, sy - 8), f"sky in {sky_before:.3f}", font=fn_tiny, fill=C_YELLOW)

    # Sky-after dashed horizontal: shows where sky lands after the curve
    if sky_after is not None and 0 < sky_after < 1:
        out_y = int(cy2 - sky_after * ch)
        dx = cx1 + 4
        while dx < cx2 - 4:
            draw.line([dx, out_y, min(dx + 8, cx2 - 4), out_y], fill=C_GREEN, width=1)
            dx += 14
        draw.text((cx2 + 2, out_y - 6), f"→ {sky_after:.3f}", font=fn_tiny, fill=C_GREEN)

    # X-axis tick labels (0.25 steps)
    fn_ax = _font(10)
    for v in [0.25, 0.5, 0.75, 1.0]:
        ax, _ = to_px(v, 0)
        draw.text((ax - 6, cy2 + 4), f"{v:.2f}", font=fn_ax, fill=C_DIM)


def _draw_histogram_inset(canvas: Image.Image, data: dict) -> None:
    """
    Pixel histogram inset — bottom-left of the image zone.

    data keys:
      image_path  str | Path   FITS file to compute histogram from
      percentiles dict         {"sky_bg": float, "p95": float, "p99": float}
    """
    image_path = data.get("image_path")
    if not image_path:
        return

    try:
        from astropy.io import fits as _fits
        import numpy as np

        with _fits.open(str(image_path)) as hdul:
            raw = hdul[0].data.astype(np.float32)

        if raw.ndim == 3 and raw.shape[0] == 3:
            # Convert to luminance (weighted)
            flat = (0.2126 * raw[0] + 0.7152 * raw[1] + 0.0722 * raw[2]).ravel()
        else:
            flat = raw.ravel()

        flat = flat[(flat >= 0) & (flat <= 1.0)]
        if len(flat) == 0:
            return

        n_bins = 160
        counts, _ = np.histogram(flat, bins=n_bins, range=(0.0, 1.0))

        draw = ImageDraw.Draw(canvas)
        cx1, cy1, cx2, cy2 = _viz_panel_bg(draw, VIZ_X, VIZ_Y, VIZ_W, VIZ_H,
                                            "Pixel Distribution")
        _viz_grid(draw, cx1, cy1, cx2, cy2, n=4)

        cw, ch = cx2 - cx1, cy2 - cy1

        # √-scale bars: makes faint regions visible alongside bright peaks
        bar_heights = np.sqrt(counts.astype(float))
        max_h = float(bar_heights.max()) if bar_heights.max() > 0 else 1.0

        bw = max(1, cw // n_bins)
        for i in range(n_bins):
            bx  = cx1 + int(i * cw / n_bins)
            bh  = int((bar_heights[i] / max_h) * ch)
            if bh > 0:
                # Colour shifts: dark = blue-grey, mid = blue-white, bright = warm
                ratio = i / n_bins
                if ratio < 0.3:
                    col = (45, 75, 140)
                elif ratio < 0.7:
                    col = (70, 120, 200)
                else:
                    col = (150, 160, 180)
                draw.rectangle([bx, cy2 - bh, bx + bw - 1, cy2], fill=col)

        # Percentile markers
        pcts = data.get("percentiles", {})
        fn_tiny = _font(9)
        markers = [
            ("sky_bg", pcts.get("sky_bg"),  C_GREEN,  "sky"),
            ("p95",    pcts.get("p95"),     C_YELLOW, "p95"),
            ("p99",    pcts.get("p99"),     C_RED,    "p99"),
        ]
        for _key, val, col, lbl in markers:
            if val is None or not (0 < val <= 1.0):
                continue
            mx = cx1 + int(val * cw)
            draw.line([mx, cy1, mx, cy2], fill=col, width=1)
            # Label above the bar
            lbl_x = mx + 2 if mx < cx2 - 22 else mx - 20
            draw.text((lbl_x, cy1 + 2), lbl, font=fn_tiny, fill=col)

        # X-axis tick labels
        fn_ax = _font(10)
        for v in [0.25, 0.5, 0.75, 1.0]:
            ax = cx1 + int(v * cw)
            draw.text((ax - 6, cy2 + 4), f"{v:.2f}", font=fn_ax, fill=C_DIM)

    except Exception as _he:
        log.debug(f"[video_frame] histogram inset failed: {_he}")


# ── Step metadata ──────────────────────────────────────────────────────────────

STEP_DISPLAY = {
    "remove_pedestal":        "Pedestal Removal",
    "cosmetic_correction":    "Cosmetic Correction",
    "crop":                   "Crop",
    "crop_artifact":          "Crop — Edge-Artifact Trim",
    "crop_canonical":         "Crop — Canonical Frame",
    "crop_coverage":          "Crop — Coverage (80%)",
    "crop_intersection":      "Crop — Intersection",
    "crop_lir":               "Crop — Largest Rectangle",
    "color_calibration":      "Color Calibration (SPCC)",
    "background_extraction":  "Background Extraction",
    "background_neutralize":  "Background Neutralize",
    "deconvolution":          "Deconvolution (BlurXT)",
    "denoise_linear":         "Linear Denoise (NoiseXT)",
    "star_sharpen":           "Star Shape Correction (BXT)",
    "remove_stars_linear":    "Star Separation (SXT)",
    "noise_reduction":        "Noise Reduction (CC)",
    "stretch":                "Non-linear Stretch",
    "curves":                 "Curves / Tone Map",
    "scnr":                   "Green Cast Removal",
    "hdr_compression":        "HDR Compression",
    "hdr_core_blend":         "Masked-Core HDR Blend",
    "sky_green_rebalance":    "Sky Green Rebalance",
    "halo_suppression":       "Halo Suppression",
    "clahe":                  "CLAHE Local Contrast",
    "dark_enhance":           "Faint Structure Lift",
    "color_sat":              "Color Saturation",
    "color_boost":            "Hue-Selective Color Boost",
    "narrowband_norm":        "Narrowband Normalization",
    "narrowband_composite":   "Palette Composite",
    "xp_channel_extract":     "XP Channel Extraction (Ha/OIII)",
    "nb_palette":             "Narrowband Palette (HOO/SHO/Foraxx)",
    "stretch_stars":          "Stretch Stars Layer",
    "combine_stars_screen":   "Recombine Stars",
    "assess_initial":         "Initial Assessment",
    "assess_pre_stretch":     "Pre-Stretch Check",
    "assess_post_stretch":    "Post-Stretch Check",
    "assess_final":           "Final Assessment",
}

STEP_CAPTIONS = {
    "remove_pedestal":        "Zeroing the black point — subtracts ADC bias offset from the raw stack",
    "cosmetic_correction":    "Replacing hot pixels, cold pixels, and cosmic ray hits with local median",
    "crop":                   "Choosing the best frame — canonical reproject vs stacking-coverage crops",
    "crop_artifact":          "Trimming ragged/black/grainy edge stacking artifacts, keeping the full target",
    "crop_canonical":         "Reprojecting onto the fixed per-target reference frame",
    "crop_coverage":          "Largest rectangle with at least 80% frame coverage",
    "crop_intersection":      "Largest rectangle covered by every frame",
    "crop_lir":               "Largest inscribed rectangle over the coverage mask",
    "color_calibration":      "Spectrophotometric Color Calibration — stellar color reference",
    "background_extraction":  "AI gradient and background removal (GraXpert)",
    "background_neutralize":  "Neutralizing background color cast",
    "deconvolution":          "Restoring diffraction-limited resolution (BlurXTerminator)",
    "denoise_linear":         "Noise suppression before stretch (NoiseXTerminator)",
    "star_sharpen":           "Correcting star roundness after denoising — BXT correct-only mode, background untouched",
    "remove_stars_linear":    "Separating stars from nebula before stretch (StarXTerminator)",
    "noise_reduction":        "Post-stretch noise reduction (CC Denoise AI)",
    "stretch":                "Converting linear data to non-linear display",
    "curves":                 "Tone mapping and contrast enhancement",
    "scnr":                   "Removing green channel contamination from Bayer interpolation",
    "hdr_compression":        "Compressing bright core to reveal detail across dynamic range",
    "hdr_core_blend":         "HDR-recovering the blown core under a feathered mask — faint signal untouched",
    "sky_green_rebalance":    "Neutralizing residual sky green without touching object or star color",
    "halo_suppression":       "Reducing star halo bloat around bright stars",
    "clahe":                  "Local contrast enhancement for large-scale structure",
    "dark_enhance":           "Lifting faint outer halo and low-surface-brightness structure",
    "color_sat":              "Enriching nebula and stellar color saturation",
    "color_boost":            "Hue-selective saturation — boosting target wavelengths, suppressing neutral background",
    "narrowband_norm":        "Balancing Ha and OIII sky background across channels after stretch",
    "narrowband_composite":   "Assembling Ha / OIII / SII mono channels into a colour palette (SHO or Foraxx)",
    "xp_channel_extract":     "Unmixing true Ha and OIII channels via Gaia-XP stellar spectra (NNLS)",
    "nb_palette":             "Compositing the extracted Ha/OIII channels into a colour palette (HOO, SHO, Foraxx)",
    "stretch_stars":          "Stretching the separated star layer before recombination",
    "combine_stars_screen":   "Recombining stars with processed starless image",
}
