"""Suites: what to run. A suite is one YAML file under `suites/`.

A suite names a game, a config, a clock, the entrants and the seat line-ups they fill, and a seed
set. Everything a result depends on is in the suite or in the pinned tempo version, so a suite
plus a tempo SHA is a reproducible experiment.

```yaml
name: snake-smoke
game: snake
config: {rounds: 1, round_seconds: 30}
clock: lockstep            # lockstep | wallclock | charged (leaderboard suites use charged)
obs_mode: state            # state | state+vision | vision
seeds: {set: dev, count: 4}
lineups:
  - [scripted, random]     # entrant names, or inline specs like "bot:scripted"
rotation: cyclic           # none | cyclic (free-for-all) | swap (two teams: swap halves)
entrants:
  scripted: {kind: bot, bot: scripted}
  random:   {kind: agent, agent: random}
  luna:     {kind: agent, agent: llm, model: bedrock_mantle/openai.gpt-5.6-luna}
limits: {match_timeout_s: 600, concurrency: 4, retries: 1, cost_cap_usd: 2.0}
```

Seeds (G9): `dev` is a public, fixed range anyone can reproduce. `heldout` is read from the
private file `$TEMPO_HELDOUT_SEEDS` (YAML `{<game>: [seeds]}` or `{default: [seeds]}`), which is
never committed, so official numbers come from maps nobody could have tuned against.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ClockMode = Literal["lockstep", "wallclock", "charged"]
ObsMode = Literal["state", "state+vision", "vision"]
EntrantKind = Literal["bot", "agent", "human"]

#: Public dev seeds start here. Held-out seeds should be drawn from a different range.
DEV_SEED_BASE = 1000
HELDOUT_ENV = "TEMPO_HELDOUT_SEEDS"


class Entrant(BaseModel):
    """One competitor. `name` is its identity on the leaderboard."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    kind: EntrantKind
    #: built-in bot name for `kind: bot` (runs inside the server)
    bot: str | None = None
    #: tempo-baselines agent for `kind: agent` (`random`, `llm`, `llm-skills`, `jev`, `hybrid`)
    agent: str | None = None
    model: str | None = None
    #: leaderboard class: `open` (any model/compute) or `fixed-model`
    klass: str = Field(default="open", alias="class")
    #: overrides the suite's obs_mode for this entrant
    obs_mode: ObsMode | None = None
    #: extra CLI arguments passed to `tempo-agent` verbatim
    args: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_kind(self) -> Entrant:
        if self.kind == "bot" and not self.bot:
            raise ValueError(f"entrant {self.name!r}: kind bot needs `bot`")
        if self.kind == "agent" and not self.agent:
            raise ValueError(f"entrant {self.name!r}: kind agent needs `agent`")
        return self

    @property
    def label(self) -> str:
        """What the leaderboard shows in the `agent` column."""
        if self.kind == "bot":
            return f"bot:{self.bot}"
        if self.kind == "agent":
            return str(self.agent)
        return "human"


def parse_entrant(spec: str) -> Entrant:
    """Parse an inline entrant: `bot:scripted`, `agent:llm model=x class=open`, `llm`, `human`.

    A bare agent name (`llm`, `jev`, `random`...) means `agent:<name>`.
    """
    parts = shlex.split(spec)
    if not parts:
        raise ValueError("empty entrant spec")
    head, rest = parts[0], parts[1:]
    kv: dict[str, str] = {}
    for p in rest:
        if "=" not in p:
            raise ValueError(f"entrant {spec!r}: expected key=value, got {p!r}")
        k, v = p.split("=", 1)
        kv[k] = v
    kind, _, what = head.partition(":")
    if not what:
        kind, what = ("human", "") if head == "human" else ("agent", head)
    data: dict[str, object] = {"kind": kind}
    if kind == "bot":
        data["bot"] = what
    elif kind == "agent":
        data["agent"] = what
    elif kind != "human":
        raise ValueError(f"entrant {spec!r}: unknown kind {kind!r}")
    for k, v in kv.items():
        data[k] = v.split(",") if k == "args" else v
    ent = Entrant.model_validate(data)
    if not ent.name:
        ent.name = spec_name(ent)
    return ent


def spec_name(ent: Entrant) -> str:
    """A stable default name: `bot:scripted`, `llm[openai.gpt-5.6-luna]`."""
    base = ent.label
    if ent.model:
        base += f"[{ent.model.split('/')[-1]}]"
    return base


class SeedSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    set: Literal["dev", "heldout", "explicit"] = "dev"
    count: int = 1
    #: first dev seed index (offset from DEV_SEED_BASE)
    start: int = 0
    seeds: list[int] = Field(default_factory=list)


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_timeout_s: float = 900.0
    join_timeout_s: float = 120.0
    concurrency: int = 2
    retries: int = 1
    #: per agent process; passed as TEMPO_AGENT_MAX_COST_USD and checked after each match
    cost_cap_usd: float | None = None
    #: stop launching new matches once the suite has spent this much
    suite_cost_cap_usd: float | None = None


class Suite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    game: str
    description: str = ""
    config: dict = Field(default_factory=dict)
    clock: ClockMode = "lockstep"
    obs_mode: ObsMode = "state"
    perception_delay_ms: int = 150
    seeds: SeedSpec = Field(default_factory=SeedSpec)
    matches_per_seed: int = 1
    #: each lineup is one seat list; items are entrant names or inline entrant specs
    lineups: list[list[str]]
    #: how each lineup is re-seated across matches so no entrant always gets the same seat:
    #: `cyclic` for free-for-all games, `swap` (first half <-> second half) for two-team games
    #: where the server assigns teams by seat index, `none` to run lineups exactly as written
    rotation: Literal["none", "cyclic", "swap"] = "cyclic"
    entrants: dict[str, Entrant] = Field(default_factory=dict)
    limits: Limits = Field(default_factory=Limits)
    #: counts toward the public leaderboard
    leaderboard: bool = False

    @field_validator("entrants", mode="before")
    @classmethod
    def _name_entrants(cls, v: dict) -> dict:
        out = {}
        for name, e in (v or {}).items():
            e = dict(e)
            e.setdefault("name", name)
            out[name] = e
        return out

    @model_validator(mode="after")
    def _resolve(self) -> Suite:
        if not self.lineups or any(not lu for lu in self.lineups):
            raise ValueError("suite needs at least one non-empty lineup")
        for lu in self.lineups:
            for item in lu:
                self.entrant(item)  # raises on an unknown name / bad spec
        return self

    def entrant(self, ref: str) -> Entrant:
        if ref in self.entrants:
            return self.entrants[ref]
        return parse_entrant(ref)

    def seed_list(self) -> list[int]:
        return resolve_seeds(self.seeds, self.game)


def resolve_seeds(spec: SeedSpec, game: str) -> list[int]:
    if spec.set == "explicit":
        return list(spec.seeds)
    if spec.set == "dev":
        return [DEV_SEED_BASE + spec.start + i for i in range(spec.count)]
    path = os.environ.get(HELDOUT_ENV)
    if not path:
        raise ValueError(f"seed set `heldout` needs ${HELDOUT_ENV} pointing at the private file")
    data = yaml.safe_load(Path(path).read_text()) or {}
    seeds = data.get(game, data.get("default"))
    if not seeds:
        raise ValueError(f"{path}: no held-out seeds for game {game!r} (or `default`)")
    return [int(s) for s in seeds][spec.start : spec.start + spec.count]


def load_suite(path: str | Path) -> Suite:
    data = yaml.safe_load(Path(path).read_text())
    return Suite.model_validate(data)


def rotations(lineup: list[str], rotation: str) -> list[list[str]]:
    """The distinct seatings of a lineup under `rotation` (see `Suite.rotation`)."""
    if rotation == "none":
        cands = [list(lineup)]
    elif rotation == "swap":
        half = len(lineup) // 2
        cands = [list(lineup), lineup[half:] + lineup[:half]]
    else:
        cands = [lineup[k:] + lineup[:k] for k in range(len(lineup))]
    seen: list[list[str]] = []
    for r in cands:
        if r not in seen:
            seen.append(r)
    return seen
