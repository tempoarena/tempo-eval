"""Skill ratings with uncertainty (OpenSkill Plackett-Luce, a TrueSkill-family model).

One rating pool per game, updated match by match in the order the matches were played. Teams
come from the server's seat -> team assignment and are ranked by `outcome.scores` (higher is
better), falling back to `winner_team`. So the same code rates Snake free-for-all (every seat
its own team) and Breach 5v5.

An entrant may fill several seats of one match (five scripted bots on a team, or a mirror
match). Each seat is rated as a copy of the entrant's current rating, and the entrant moves by
the mean of its copies' changes, so filling more seats does not multiply the update.

The published scale is `1000 + 40 * (mu - 25)`: 1000 is a new entrant, and one default sigma
(25/3) is ~333 points. `conservative` (rating - 3 sd) is what the leaderboard sorts by, so an
entrant with few matches cannot top it on luck.
"""

from __future__ import annotations

from dataclasses import dataclass

from openskill.models import PlackettLuce

from .results import Sample

SCALE = 40.0
MU0 = 25.0
BASE = 1000.0


@dataclass
class Rating:
    mu: float
    sigma: float

    @property
    def display(self) -> float:
        return BASE + SCALE * (self.mu - MU0)

    @property
    def display_sd(self) -> float:
        return SCALE * self.sigma

    @property
    def conservative(self) -> float:
        return self.display - 3 * self.display_sd


def team_ranks(sample: Sample) -> dict[int, int] | None:
    """team -> rank (0 best). `None` when the result carries no ordering at all."""
    teams = sorted({s.team for s in sample.seats})
    scores = sample.outcome.get("scores")
    if isinstance(scores, list) and len(scores) >= len(teams) and teams == list(range(len(teams))):
        order = sorted(set(scores), reverse=True)
        return {t: order.index(scores[t]) for t in teams}
    winner = sample.outcome.get("winner_team")
    if winner is None and "winner_team" in sample.outcome:
        return dict.fromkeys(teams, 0)  # explicit draw
    if winner is None:
        return None
    return {t: 0 if t == winner else 1 for t in teams}


def rate(samples: list[Sample], key=lambda seat: seat.name) -> dict[str, Rating]:
    model = PlackettLuce()
    table: dict[str, Rating] = {}
    for s in samples:
        ranks = team_ranks(s)
        if ranks is None:
            continue
        teams = sorted(ranks)
        if len(teams) < 2:
            continue
        groups, owners = [], []
        for t in teams:
            members = [seat for seat in s.seats if seat.team == t]
            group = []
            for seat in members:
                k = key(seat)
                r = table.setdefault(k, Rating(MU0, MU0 / 3))
                group.append(model.rating(mu=r.mu, sigma=r.sigma, name=k))
                owners.append(k)
            groups.append(group)
        new = model.rate(groups, ranks=[ranks[t] for t in teams])
        deltas: dict[str, list[tuple[float, float]]] = {}
        flat_old = [p for g in groups for p in g]
        flat_new = [p for g in new for p in g]
        for k, old, nw in zip(owners, flat_old, flat_new, strict=True):
            deltas.setdefault(k, []).append((nw.mu - old.mu, nw.sigma))
        for k, ds in deltas.items():
            r = table[k]
            r.mu += sum(d for d, _ in ds) / len(ds)
            r.sigma = min(sig for _, sig in ds)
    return table
