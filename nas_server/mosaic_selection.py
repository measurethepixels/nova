"""Frozen admission policy and deterministic coverage-stratified mosaic caps.

Only capture-transition jobs receive a default cap. No missing-position global
fallback is permitted. Cells are a coverage proxy, not a footprint/depth map.
"""
import hashlib
import json
import math
from fractions import Fraction


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def parse_cap_override(value):
    from nas_server.stack_config import parse_feather_override
    try:
        return parse_feather_override(value)
    except ValueError as exc:
        raise ValueError("mosaic_max_subs must be a non-negative integer (0 unlimited)") from exc


def admission_policy(origin, override, settings):
    if origin not in ("manual", "capture-transition"):
        raise ValueError("Unknown stack request origin")
    if override is not None and (type(override) is not int or override < 0):
        raise ValueError("mosaic_max_subs must be a non-negative integer")
    cap = (settings.get("mosaic_auto_max_subs", 1000)
           if origin == "capture-transition" else 0) if override is None else override
    if type(cap) is not int or cap < 0:
        raise ValueError("mosaic_auto_max_subs must be a non-negative integer")
    return dict(version=1, origin=origin, cap=cap, override=override,
                cell_degrees=0.25, partition="panel-or-gnomonic-v1")


def _score(frame):
    try:
        fwhm, ecc = float(frame["fwhm"]), float(frame["eccentricity"])
        score = fwhm * (1 + ecc)
        if fwhm > 0 and ecc >= 0 and math.isfinite(score):
            return score
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return math.inf  # Unmeasured/nonfinite frames always rank last.


def _position(frame):
    try:
        if frame.get("solve_status") != "ok":
            return None
        ra, dec = float(frame["solved_ra"]), float(frame["solved_dec"])
        if math.isfinite(ra) and math.isfinite(dec) and -90 <= dec <= 90:
            return ra % 360, dec
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _quotas(counts, cap):
    """One per stratum, then proportional largest-remainder residual capacity."""
    if len(counts) > cap:
        raise ValueError(f"Mosaic cap {cap} smaller than represented coverage strata ({len(counts)}); no engine started")
    residual = {k: n - 1 for k, n in counts.items()}
    remaining, total = cap - len(counts), sum(residual.values())
    shares = {k: Fraction(remaining * n, total) if total else Fraction(0)
              for k, n in residual.items()}
    quotas = {k: 1 + int(v) for k, v in shares.items()}
    order = sorted(counts, key=lambda k: (-(shares[k] - int(shares[k])), k))
    for key in order[:cap - sum(quotas.values())]:
        quotas[key] += 1
    return quotas


def effective_cap(policy, framing, engine):
    """Applicable output cap, independent of provenance-only request origin."""
    policy = policy or {}
    return policy.get("cap", 0) if (policy.get("applicable", True) and framing == "max" and engine == "siril") else 0


def cap_filter_plan(plan, frames, policy, framing, engine, panels=()):
    """Freeze selection AFTER all eligibility gates and BEFORE copying/engines."""
    from nas_server.stack_selection import plan_filter_stacks
    policy = dict(policy or {"version": 1, "origin": "legacy", "cap": 0})
    configured_cap = policy["cap"]
    cap = effective_cap(policy, framing, engine)
    by_path = {str(f["file_path"]): f for f in frames}
    population = [by_path[p] for p in plan["paths"]]
    audit = {**policy, "configured_cap": configured_cap, "cap": cap,
             "candidates": len(population), "kept": len(population),
             "eligible_source_hash": plan["source_hash"], "strata": {},
             "unscored": sum(not math.isfinite(_score(f)) for f in population),
             "missing_positions": sum(_position(f) is None for f in population)}
    if not cap or len(population) <= cap:
        audit.update(reason="unlimited" if not cap else "within_cap",
                     selected_hash=_hash(sorted(plan["paths"])))
        return {**plan, "mosaic_policy": audit}

    # Declared panels are meaningful identities, unlike arbitrary associated
    # targets. Non-panel frames must have solved centres, even in a mixed set.
    panel_set = set(panels)
    positioned = sorted((str(f["file_path"]), _position(f)) for f in population
                        if f.get("target") not in panel_set and _position(f) is not None)
    origin = positioned[0][1] if positioned else None
    partition = {"algorithm": "panel-or-gnomonic-v1", "origin_degrees": origin,
                 "cell_degrees": policy.get("cell_degrees", 0.25),
                 "boundary": "floor; half-open", "ra_wrap": "trigonometric",
                 "panels": sorted(panel_set)}
    groups = {}
    membership = []
    for frame in population:
        if frame.get("target") in panel_set:
            key = "panel:" + frame["target"]
        else:
            pos = _position(frame)
            if pos is None or origin is None:
                unknown = sum(f.get("target") not in panel_set and _position(f) is None for f in population)
                raise ValueError(f"Mosaic coverage unknown: {unknown}/{len(population)} candidates missing solved centre and declared panel; cap={cap}; no global fallback")
            ra, dec = map(math.radians, pos)
            ra0, dec0 = map(math.radians, origin)
            delta = ra - ra0
            denom = math.sin(dec0)*math.sin(dec) + math.cos(dec0)*math.cos(dec)*math.cos(delta)
            if denom <= 1e-8:
                raise ValueError("Mosaic exceeds tangent-plane hemisphere; coverage policy required")
            x = math.degrees(math.cos(dec)*math.sin(delta)/denom)
            y = math.degrees((math.cos(dec0)*math.sin(dec)-math.sin(dec0)*math.cos(dec)*math.cos(delta))/denom)
            cell = partition["cell_degrees"]
            key = f"cell:{math.floor(x/cell)}:{math.floor(y/cell)}"
        groups.setdefault(key, []).append(frame)
        score = _score(frame)
        membership.append([str(frame["file_path"]), key, score if math.isfinite(score) else None, str(frame.get("date") or "")])
    quotas = _quotas({k: len(v) for k, v in groups.items()}, cap)
    selected = []
    for key, group in sorted(groups.items()):
        ranked = sorted(group, key=lambda f: (_score(f), str(f.get("date") or ""), str(f["file_path"])))
        selected.extend(str(f["file_path"]) for f in ranked[:quotas[key]])
        audit["strata"][key] = dict(candidates=len(group), kept=quotas[key])
    frozen = plan_filter_stacks(frames, selected, plan["filter"])[0]
    audit.update(reason="coverage_stratified", kept=len(selected), partition=partition,
                 partition_hash=_hash(partition), ranking_hash=_hash(sorted(membership)),
                 selected_hash=_hash(sorted(selected)))
    # Preserve original gate/filter accounting alongside the capped source hash.
    frozen.update(eligible_filters=plan["eligible_filters"],
                  excluded_frames=plan["excluded_frames"],
                  ineligible_or_missing_frames=plan["ineligible_or_missing_frames"],
                  mosaic_policy=audit)
    return frozen
