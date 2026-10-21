"""Non-authorizing wall-time accounting for existing stack call boundaries."""
from contextlib import contextmanager
import time


class StackTiming:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.stages = {}
        self.errors = []
        self._active = False
        self._finished = None

    @contextmanager
    def stage(self, name):
        if self._active:
            raise ValueError("stack timing stages must not overlap")
        self._active = True
        started = self.clock()
        try:
            yield
        except BaseException as exc:
            self.errors.append({"stage": name, "exception": type(exc).__name__})
            raise
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + max(0.0, self.clock() - started)
            self._active = False

    def call(self, name, fn, *args, **kwargs):
        with self.stage(name):
            return fn(*args, **kwargs)

    def finish(self):
        if self._finished is None:
            if self._active:
                raise ValueError("cannot finish an active stage")
            elapsed = max(0.0, self.clock() - self.started)
            stages = dict(self.stages)
            stages["other_s"] = max(0.0, elapsed - sum(stages.values()))
            # Store exactly the same sum readers calculate; any round-off from
            # the raw monotonic duration is below a floating-point ULP.
            self._finished = {"version": 1, "scope": "attempt_before_cleanup",
                              "stages": stages, "elapsed_s": sum(stages.values()),
                              "errors": list(self.errors)}
        return self._finished


def script_feather_px(script):
    """Report the executed stack command's flag, never an inferred setting."""
    import re
    commands = [line for line in script.splitlines() if line.strip().startswith('stack ')]
    if len(commands) != 1:
        return None
    match = re.search(r'(?:^|\s)-feather=(\d+)(?:\s|$)', commands[0])
    return int(match.group(1)) if match else 0
