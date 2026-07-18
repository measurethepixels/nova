"""
Shared threading.Event registry for manual review blocking.

Imported by experiments.py (creates events) and main.py (signals them).
Neither imports the other, so there is no circular dependency.
"""
import threading

_lock = threading.Lock()
_events: dict[int, threading.Event] = {}


def register(review_id: int) -> threading.Event:
    ev = threading.Event()
    with _lock:
        _events[review_id] = ev
    return ev


def signal(review_id: int) -> bool:
    with _lock:
        ev = _events.get(review_id)
    if ev:
        ev.set()
        return True
    return False


def unregister(review_id: int) -> None:
    with _lock:
        _events.pop(review_id, None)


def pending_ids() -> list[int]:
    with _lock:
        return list(_events.keys())
