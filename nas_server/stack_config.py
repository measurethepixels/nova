"""Stack-configuration identity: canonical defaults, config-unique filename
tokens, and mosaic-aware framing resolution.

Deliberately free of numpy/astropy/etc — stacker.py needs them for actual
image processing, but queue_manager.add_stack_job() only needs THIS module
for enqueue-time dedup fingerprinting, and must stay importable in CI's
pure-logic test suite (no heavy deps installed there). Moved out of
stacker.py specifically because stacker.py imports numpy unconditionally at
module level, which broke that (see PR #39 CI failure, 2026-07-26).
"""
import hashlib
import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Canonical stack-config defaults. Single source of truth for "what counts as
# default" — used by both the filename config-tokens and the queue dedup
# fingerprint, so a duplicate-config job is definitionally a same-filename job.
# NOTE: reconciles the pre-existing ecc_threshold default mismatch between
# queue_manager.add_stack_job (0.6) and main.py's /stack + stacker.stack_target
# (0.66) — 0.66 is treated as canonical since it's what stack_target and the
# HTTP endpoint both already use.
STACK_CONFIG_DEFAULTS = {
    "cull": True,
    "bottom_pct": 0.10,
    "min_stars": 20,
    "fast": False,
    "framing": "min",
    "hero": False,
    "drizzle": False,
    "exptime": None,
    "eq_only": True,
    "filter_name": "auto",
    "ecc_threshold": 0.66,
    "sky_level_factor": 3.0,
    "gradient_threshold": 0.5,
}


def _round_field(value):
    """Round floats to 3 decimals — shared precision rule between the
    filename hash token and queue_manager._stack_fingerprint's dedup
    comparison (see both docstrings)."""
    return round(value, 3) if isinstance(value, float) else value


def _target_forces_max_framing(target_name: str) -> bool:
    """True if target_name has mosaic panels (separate panel targets listing
    this as their mosaic_association) or its own mosaic=1 flag — the two
    conditions stack_target() checks to force framing="max" regardless of
    what was requested (see the mosaic-detection block early in
    stack_target()). Exposed standalone, read-only (no frame-gathering side
    effects), so queue-time dedup can resolve the EFFECTIVE framing before a
    job ever reaches stack_target() — see resolve_effective_framing().

    NOTE: intentionally re-implements the same two lookups stack_target()
    itself does inline, rather than sharing code, to avoid touching that
    already-working, RED-risk stacking path for this fix. If the mosaic-
    detection conditions in stack_target() ever change, update both.
    """
    try:
        from nas_server.database import get_mosaic_panel_targets
        if get_mosaic_panel_targets(target_name):
            return True
    except Exception as e:
        logger.warning(f"[stack] {target_name}: mosaic panel lookup failed: {e}")
    try:
        from nas_server.database import get_conn
        with get_conn() as c:
            row = c.execute(
                "SELECT mosaic FROM targets WHERE target=?", (target_name,)
            ).fetchone()
        if row and row[0]:
            return True
    except Exception as e:
        logger.warning(f"[stack] {target_name}: mosaic flag lookup failed: {e}")
    return False


def resolve_effective_framing(target_name: str, framing: str) -> str:
    """Resolve the framing stack_target() will actually run with once its own
    mosaic detection runs. Two stack requests that differ only in requested
    framing but resolve to the same effective value must be treated as
    duplicates (and will land on the same output filename) — so this must be
    called before computing a dedup fingerprint or a filename config token,
    not just the raw requested framing (see PR #39 review, finding 3)."""
    if framing == "max":
        return "max"
    return "max" if _target_forces_max_framing(target_name) else framing


def _stack_config_tokens(cfg: dict) -> str:
    """Build the filename config segment from fields that diverge from default.

    Returns "" for an all-default config (byte-identical filenames to the
    pre-existing scheme, preserving lookup/backward-compat for ~thousands of
    existing stack files). Headline shape flags become short readable tokens;
    the remaining numeric/selection gates collapse into one short hash token
    so the filename stays bounded and phone-readable.
    """
    tokens = []
    if cfg.get("drizzle", False):
        tokens.append("drz")
    if cfg.get("hero", False):
        tokens.append("hero")
    if cfg.get("framing", "min") == "max":
        tokens.append("mf")
    if cfg.get("fast", False):
        tokens.append("fast")
    if not cfg.get("eq_only", True):
        tokens.append("azeq")

    hash_fields = ("bottom_pct", "ecc_threshold", "min_stars", "sky_level_factor",
                   "gradient_threshold", "exptime", "filter_name", "cull")
    # Round floats before comparing/hashing: (a) two requests differing only
    # by float noise (0.660103 vs 0.6601) collapse to the same token instead
    # of spuriously diverging, and (b) this keeps the token in sync with
    # queue_manager._stack_fingerprint's dedup rounding (same fields, same
    # precision) — two jobs the dedup layer treats as identical must always
    # land on the same filename token, never a different one.
    normalized = {f: _round_field(cfg.get(f, STACK_CONFIG_DEFAULTS[f])) for f in hash_fields}
    default_normalized = {f: _round_field(STACK_CONFIG_DEFAULTS[f]) for f in hash_fields}
    if normalized != default_normalized:
        # 8 hex chars (32 bits) — a 4-char/16-bit token was demonstrated to
        # collide organically (ecc_threshold 0.660103 vs 0.660529 both hashed
        # to "3d52"), which would make _move_with_overwrite_warning silently
        # clobber a genuinely different config's output. 32 bits makes an
        # accidental collision practically impossible for the realistic
        # number of distinct configs one target ever accumulates.
        digest = hashlib.sha1(
            json.dumps(normalized, sort_keys=True, default=str).encode()
        ).hexdigest()
        tokens.append(f"c{digest[:8]}")

    return "_".join(tokens)


def make_processed_filename(target: str, obs_date: str | None, total_secs: float | None,
                             temp_c: float | None, tool: str, step: str, ext: str,
                             config_tokens: str = "") -> str:
    """Generate a standardized processed filename.
    Example: C_77_20260419_6510s_0C_siril_stack.fit
    With config_tokens (non-default stack config): ..._0C_drz_siril_stack.fit
    """
    safe = target.replace(" ", "_").replace("/", "_")
    date = obs_date[:8] if obs_date and len(obs_date) >= 8 else "unknown"
    integ = f"{int(round(total_secs))}s" if total_secs else "0s"
    temp = f"{int(round(temp_c))}C" if temp_c is not None else "nC"
    cfg_part = f"{config_tokens}_" if config_tokens else ""
    return f"{safe}_{date}_{integ}_{temp}_{cfg_part}{tool}_{step}.{ext}"


def _move_with_overwrite_warning(src: Path, dst: Path, target_name: str, kind: str) -> None:
    """shutil.move src to dst, logging a WARNING instead of silently clobbering
    an existing file at dst. Config-unique filenames (see _stack_config_tokens)
    make same-name collisions rare; when one does happen it's an intentional
    re-stack of the same config, which duplicate-job prevention already ensures
    can't race — so overwrite is correct, it just must not be silent."""
    if dst.exists():
        logger.warning(f"[stack] {target_name}: overwriting existing {kind} {dst}")
    shutil.move(str(src), str(dst))
