"""Capability against latency: Pareto frontiers and hypervolume (TEMPO Goal, question 1).

Every result is a point (decision latency, capability). Lower latency and higher capability are
both better. The frontier is the set of points nobody beats on both at once.

Hypervolume is measured on normalised axes so it is comparable across games:

- speed `u = 1 - log10(clamp(latency_ms, 1, 10_000)) / 4`: 1 ms -> 1.0, 10 s -> 0.0. Log because
  the latency budgets TEMPO tracks (50 ms, 250 ms, 1 s, 5 s) are spread geometrically.
- capability `y` in [0, 1] (a win rate, or a rating min-max normalised within the game).

It is the area of the union of the rectangles [0, u_i] x [0, y_i]: the region of
(speed, capability) combinations the entry matches or beats. 1.0 would be instant and perfect.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

LAT_MIN_MS = 1.0
LAT_MAX_MS = 10_000.0


def speed(latency_ms: float) -> float:
    lat = min(max(latency_ms, LAT_MIN_MS), LAT_MAX_MS)
    return 1.0 - math.log10(lat / LAT_MIN_MS) / math.log10(LAT_MAX_MS / LAT_MIN_MS)


def frontier(points: Sequence[tuple[float, float]]) -> list[int]:
    """Indices of the non-dominated `(latency_ms, capability)` points, fastest first.

    Latencies under LAT_MIN_MS count as LAT_MIN_MS: below a millisecond the axis cannot tell
    entries apart, so a 0.25 ms random agent does not "beat" an in-tick bot on speed.
    """
    lat = [max(p[0], LAT_MIN_MS) for p in points]
    order = sorted(range(len(points)), key=lambda i: (lat[i], -points[i][1]))
    out: list[int] = []
    best = -math.inf
    for i in order:
        if points[i][1] > best:
            out.append(i)
            best = points[i][1]
    return out


def hypervolume(points: Sequence[tuple[float, float]]) -> float:
    """Area dominated by `(latency_ms, capability in [0,1])` points on the normalised axes."""
    pts = sorted(((speed(lat), min(max(cap, 0.0), 1.0)) for lat, cap in points), reverse=True)
    area, best_y = 0.0, 0.0
    # sweep from the fastest point to the slowest; each strip adds its new height
    for idx, (u, y) in enumerate(pts):
        best_y = max(best_y, y)
        nxt = pts[idx + 1][0] if idx + 1 < len(pts) else 0.0
        area += (u - nxt) * best_y
    return area


def normalise(values: Sequence[float]) -> list[float]:
    lo, hi = min(values, default=0.0), max(values, default=0.0)
    if hi - lo < 1e-12:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]
