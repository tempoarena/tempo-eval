"""Replay verification: a result counts only if its replay re-simulates to the same hash.

Re-simulation uses the pinned tempo's native module, so it is the same engine code that produced
the replay, run again on this machine. Three answers: `True` (verified), `False` (the replay does
not reproduce: reject the result), `None` (no verifier installed, or no replay: unverified).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import cache


@cache
def _verifier() -> Callable[[str], str] | None:
    """Find tempo's replay re-simulator. Returns `f(replay_json) -> final hash hex`."""
    try:
        import tempo  # type: ignore[import-not-found]
    except ImportError:
        return None
    native = getattr(tempo, "_native", None)
    for owner in (tempo, native):
        for name in ("verify_replay", "replay_hash"):
            fn = getattr(owner, name, None) if owner is not None else None
            if callable(fn):
                return _normalise(fn)
    return None


def _normalise(fn: Callable) -> Callable[[str], str]:
    def call(replay_json: str) -> str:
        out = fn(replay_json)
        if isinstance(out, int):
            return f"{out:016x}"
        if isinstance(out, dict):  # {"hash": ..., "ok": ...}
            out = out.get("hash") or out.get("final_hash")
        return str(out).lower().removeprefix("0x")

    return call


def _norm_hash(h: object) -> str:
    if isinstance(h, int):
        return f"{h:016x}"
    return str(h).lower().removeprefix("0x").zfill(16)


def verify_replay(replay: dict | None, verifier: Callable[[str], str] | None = None) -> bool | None:
    if not replay or "final_hash" not in replay:
        return None
    fn = verifier or _verifier()
    if fn is None:
        return None
    try:
        got = fn(json.dumps(replay))
    except Exception:
        return False
    return _norm_hash(got) == _norm_hash(replay["final_hash"])


def verifier_available() -> bool:
    return _verifier() is not None
