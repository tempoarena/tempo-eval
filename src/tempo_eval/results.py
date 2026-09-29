"""Reading back what `tempo-eval run` wrote.

A `Sample` is one finished match joined with who sat where (from `match.json`, which is the
evaluator's own record and therefore authoritative for entrant identity) and what happened
(from the server's `result.json`). Rejections happen here, once, for every consumer:

- a match whose replay failed to re-simulate (`replay_verified is False`) is dropped;
- with `require_verified`, so is one that could not be checked at all (`None`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Seat:
    index: int
    team: int
    name: str
    agent: str
    model: str | None
    klass: str
    obs_mode: str
    kind: str


@dataclass
class Sample:
    suite: str
    run_id: str
    game: str
    clock: str
    leaderboard: bool
    match_id: str
    seed: int
    seats: list[Seat]
    result: dict
    verified: bool | None
    order: tuple = field(default=())

    @property
    def outcome(self) -> dict:
        return self.result.get("outcome") or {}

    def seat_stats(self, i: int) -> dict:
        per = (self.result.get("stats") or {}).get("per_seat") or []
        return per[i] if i < len(per) and per[i] else {}

    def seat_outcome(self, i: int) -> dict:
        per = self.outcome.get("per_seat") or []
        return per[i] if i < len(per) and isinstance(per[i], dict) else {}

    @property
    def tick_hz(self) -> float:
        hz = self.result.get("tick_hz") or (self.result.get("config") or {}).get("tick_hz") or 32
        return float(hz)

    @property
    def game_minutes(self) -> float:
        return (self.result.get("ticks") or 0) / self.tick_hz / 60.0


@dataclass
class Loaded:
    samples: list[Sample]
    rejected: list[dict]
    tempo_shas: list[str]


def _seat_team(result: dict, i: int) -> int:
    seats = result.get("seats") or []
    if i < len(seats) and isinstance(seats[i], dict) and "team" in seats[i]:
        return int(seats[i]["team"])
    return i


def load_results(
    root: Path,
    suites: list[str] | None = None,
    leaderboard_only: bool = False,
    require_verified: bool = False,
) -> Loaded:
    samples: list[Sample] = []
    rejected: list[dict] = []
    shas: list[str] = []
    for run_json in sorted(root.glob("*/*/run.json")):
        run = json.loads(run_json.read_text())
        suite = run["suite"]
        if suites and suite["name"] not in suites:
            continue
        if leaderboard_only and not suite.get("leaderboard"):
            continue
        sha = (run.get("server") or {}).get("git_sha")
        if sha and sha not in shas:
            shas.append(sha)
        suite_obs = suite.get("obs_mode", "state")
        for rec in run["matches"]:
            if rec.get("status") != "finished" or not rec.get("match_id"):
                continue
            mdir = run_json.parent / rec["match_id"]
            rpath = mdir / "result.json"
            if not rpath.exists():
                rejected.append({"match_id": rec["match_id"], "reason": "missing result.json"})
                continue
            verified = rec.get("replay_verified")
            if verified is False or (require_verified and verified is None):
                rejected.append(
                    {
                        "match_id": rec["match_id"],
                        "reason": "replay did not verify"
                        if verified is False
                        else "replay unverified",
                    }
                )
                continue
            result = json.loads(rpath.read_text())
            seats = []
            rseats = result.get("seats") or []
            for i, e in enumerate(rec["seats"]):
                # identity (entrant name, class, model) is the evaluator's own record; the seat
                # kind is the server's (`bot` only for in-server bots, else what the client
                # declared in hello)
                rs = rseats[i] if i < len(rseats) and isinstance(rseats[i], dict) else {}
                kind = rs.get("kind") or e["kind"]
                agent = f"bot:{e['bot']}" if e["kind"] == "bot" else (e.get("agent") or kind)
                seats.append(
                    Seat(
                        index=i,
                        team=_seat_team(result, i),
                        name=e["name"],
                        agent=agent,
                        model=e.get("model"),
                        klass=e.get("class", "open"),
                        obs_mode=e.get("obs_mode") or suite_obs,
                        kind=kind,
                    )
                )
            samples.append(
                Sample(
                    suite=suite["name"],
                    run_id=run["run_id"],
                    game=suite["game"],
                    clock=suite.get("clock", "lockstep"),
                    leaderboard=bool(suite.get("leaderboard")),
                    match_id=rec["match_id"],
                    seed=rec["seed"],
                    seats=seats,
                    result=result,
                    verified=verified,
                    order=(run["run_id"], rec["job"]),
                )
            )
    samples.sort(key=lambda s: s.order)
    return Loaded(samples=samples, rejected=rejected, tempo_shas=shas)
