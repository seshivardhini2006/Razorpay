"""Simulated clock.

Real recovery lifecycles span hours-to-days (e.g. 'retry tomorrow 7pm').
A simulated clock lets the full lifecycle be demonstrated in minutes: an
operator advances the clock and the scheduler fires whatever retries are due.

The sim clock starts at UTC now and only moves when advanced or reset.
"""

import threading
from datetime import datetime, timedelta

_EPOCH = None
_LOCK = threading.Lock()


def _start():
    global _EPOCH
    if _EPOCH is None:
        _EPOCH = datetime.utcnow()


def now() -> datetime:
    """Current simulated 'wall clock' time."""
    with _LOCK:
        _start()
        return _EPOCH
        # NOTE: we do NOT advance automatically; time flows only via advance().
        # (auto-offset commented out so retry timing is fully deterministic to the sim)

        # Uncomment the following to make the sim clock track real seconds too:
        # real_delta = datetime.utcnow() - _WALL_START
        # return _EPOCH + real_delta


def advance(delta: timedelta) -> datetime:
    """Fast-forward the simulated clock by `delta`. Returns the new time."""
    global _EPOCH
    with _LOCK:
        _start()
        _EPOCH = _EPOCH + delta
        return _EPOCH


def reset() -> datetime:
    """Reset the sim clock to real UTC now."""
    global _EPOCH
    with _LOCK:
        _EPOCH = datetime.utcnow()
        return _EPOCH


def isoformat() -> str:
    return now().isoformat() + "Z"