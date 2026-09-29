"""Per-entrant statistics over a set of samples.

An entry is one entrant in one game, keyed by `(name, class, obs_mode)`: modes are ranked
separately on the leaderboard (plan §9.4), and so are classes.

Latency is aggregated from each match's per-seat summary (`stats.per_seat[i].latency_ms`):
p50 is the answer-weighted median of the match medians and p95 the answer-weighted mean of the
match p95s. That is an approximation of the pooled percentiles, stated here rather than hidden;
the pooled values would need every match's `latency.jsonl`.

Two latencies, because real-time agents have two loops. `latency_ms` is what the server measures
per answer (observation sent -> pad received): an agent that drives its pad every tick through
skills while a model thinks beside it answers in well under a millisecond. `think_ms_mean` is
the agent's own model-call latency (`usage.latency_ms_total / usage.calls`), which is how long
its decisions take to form. The frontier plots `decision_latency_ms`: think time when the entry
calls a model, answer latency otherwise.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .ratings import Rating, rate, team_ranks
from .results import Sample, Seat

#: identifying fields games put in `outcome.per_seat` that are not metrics
NOT_METRICS = frozenset({"seat", "team", "index"})


def entry_key(seat: Seat) -> str:
    return f"{seat.name}|{seat.klass}|{seat.obs_mode}"


def wilson(successes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion (draws count as half a win)."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def weighted_median(pairs: list[tuple[float, float]]) -> float | None:
    pairs = sorted((v, w) for v, w in pairs if v is not None and w > 0)
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]


@dataclass
class SuiteStats:
    """One entry's showing in one suite: a point on its own latency/capability curve."""

    wins: float = 0.0
    matches: int = 0
    lat_p50: list = field(default_factory=list)
    think_ms_total: float = 0.0
    calls: int = 0

    @property
    def decision_latency_ms(self) -> float | None:
        return self.think_ms_total / self.calls if self.calls else weighted_median(self.lat_p50)


@dataclass
class Entry:
    key: str
    name: str
    agent: str
    model: str | None
    klass: str
    obs_mode: str
    kind: str
    matches: int = 0
    wins: float = 0.0
    verified: int = 0
    answered: int = 0
    missed: int = 0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    game_minutes: float = 0.0
    lat_p50: list = field(default_factory=list)
    lat_p95: list = field(default_factory=list)
    metric_sums: dict = field(default_factory=lambda: defaultdict(float))
    metric_counts: dict = field(default_factory=lambda: defaultdict(int))
    think_ms_total: float = 0.0
    calls: int = 0
    suites: dict = field(default_factory=dict)  # suite -> SuiteStats
    rating: Rating | None = None

    @property
    def think_ms_mean(self) -> float | None:
        return self.think_ms_total / self.calls if self.calls else None

    @property
    def decision_latency_ms(self) -> float | None:
        return self.think_ms_mean if self.calls else self.latency_p50

    @property
    def win_rate(self) -> float:
        return self.wins / self.matches if self.matches else 0.0

    @property
    def latency_p50(self) -> float | None:
        return weighted_median(self.lat_p50)

    @property
    def latency_p95(self) -> float | None:
        pairs = [(v, w) for v, w in self.lat_p95 if v is not None and w > 0]
        total = sum(w for _, w in pairs)
        return sum(v * w for v, w in pairs) / total if total else None

    @property
    def cost_per_min(self) -> float | None:
        return self.cost_usd / self.game_minutes if self.game_minutes > 0 else None

    def metrics(self) -> dict[str, float]:
        return {k: self.metric_sums[k] / self.metric_counts[k] for k in sorted(self.metric_sums)}


def _seat_score(sample: Sample, seat: Seat) -> float | None:
    """1 win, 0.5 draw / shared first place, 0 loss."""
    ranks = team_ranks(sample)
    if ranks is None:
        return None
    best = min(ranks.values())
    if ranks[seat.team] != best:
        return 0.0
    tied = sum(1 for r in ranks.values() if r == best)
    return 1.0 if tied == 1 else 0.5


def build_entries(samples: list[Sample]) -> dict[str, dict[str, Entry]]:
    """game -> entry key -> Entry, with ratings filled in."""
    by_game: dict[str, dict[str, Entry]] = defaultdict(dict)
    for s in samples:
        entries = by_game[s.game]
        counted: set[str] = set()
        for seat in s.seats:
            k = entry_key(seat)
            e = entries.get(k) or entries.setdefault(
                k,
                Entry(
                    key=k,
                    name=seat.name,
                    agent=seat.agent,
                    model=seat.model,
                    klass=seat.klass,
                    obs_mode=seat.obs_mode,
                    kind=seat.kind,
                ),
            )
            stats = s.seat_stats(seat.index)
            lat = stats.get("latency_ms") or {}
            answered = int(stats.get("answered") or 0)
            e.answered += answered
            e.missed += int(stats.get("missed_deadlines") or 0)
            if lat.get("p50") is not None:
                e.lat_p50.append((float(lat["p50"]), max(answered, 1)))
            if lat.get("p95") is not None:
                e.lat_p95.append((float(lat["p95"]), max(answered, 1)))
            usage = stats.get("usage") or {}
            e.cost_usd += float(usage.get("cost_usd") or 0.0)
            e.tokens_in += int(usage.get("tokens_in") or 0)
            e.tokens_out += int(usage.get("tokens_out") or 0)
            think, calls = float(usage.get("latency_ms_total") or 0.0), int(usage.get("calls") or 0)
            e.think_ms_total += think
            e.calls += calls
            st = e.suites.setdefault(s.suite, SuiteStats())
            st.think_ms_total += think
            st.calls += calls
            for mk, mv in s.seat_outcome(seat.index).items():
                if mk in NOT_METRICS:
                    continue
                if isinstance(mv, (int, float)) and not isinstance(mv, bool):
                    e.metric_sums[mk] += float(mv)
                    e.metric_counts[mk] += 1
            if k in counted:
                continue  # one match counts once toward matches / wins / minutes
            counted.add(k)
            score = _seat_score(s, seat)
            e.matches += 1
            e.wins += score or 0.0
            e.verified += 1 if s.verified else 0
            e.game_minutes += s.game_minutes
            st.wins += score or 0.0
            st.matches += 1
            if lat.get("p50") is not None:
                st.lat_p50.append((float(lat["p50"]), max(answered, 1)))
    for game, entries in by_game.items():
        table = rate([s for s in samples if s.game == game], key=entry_key)
        for k, e in entries.items():
            e.rating = table.get(k)
    return by_game
