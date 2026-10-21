"""Display saved planner evidence; never reconstruct a historical scoring input."""


def stored_plan_slot(slot: dict) -> dict:
    result = dict(slot)
    if result.get("planned_h") is None:
        start, end = slot.get("start_hhmm"), slot.get("end_hhmm")
        if start and end:
            sh, sm = map(int, start.split(":"))
            eh, em = map(int, end.split(":"))
            result["planned_h"] = round(((eh * 60 + em - sh * 60 - sm) % 1440) / 60, 2)
    usable = slot.get("usable_hours")
    recommended = slot.get("scaled_rec_h")
    completion = slot.get("completion_frac")
    if usable is None or recommended is None or completion is None:
        result.update(have_text="Scored snapshot unavailable", need_text="—", done=False)
        return result
    raw = slot.get("raw_hours")
    panels = slot.get("panel_count", 1)
    have = f"{usable:.1f}h usable"
    if raw is not None:
        have += f" ({raw:.1f}h raw)"
    have += f" / {recommended:g}h"
    if panels > 1:
        have += f" ({panels} panels)"
    done = completion >= 0.95
    result.update(have_text=have, done=done,
                  need_text="✓ enough data" if done else f"{max(0, recommended - usable):.1f}h")
    return result
