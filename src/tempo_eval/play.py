"""`tempo-eval play`: a live match with human seats, AI seats launched for you.

Creates a `wallclock` match (humans play in real time), starts one `tempo-agent` per agent seat,
and prints a tempo-play join URL per human seat. Bots run inside the server. The finished match
lands in `results/play/<run_id>/` like any other, so human sessions can be evaluated too.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode

from .agents import AgentProcs, agent_argv
from .server import TERMINAL, ServerClient, connect_or_start
from .suite import Entrant, parse_entrant

LOCAL_PLAY_URL = "http://localhost:5173"


def join_urls(ws_url: str, match_id: str, seat: int) -> list[str]:
    q = urlencode({"server": ws_url, "match": match_id, "seat": seat})
    bases = [os.environ.get("TEMPO_PLAY_URL") or LOCAL_PLAY_URL]
    preview = os.environ.get("TEMPO_PLAY_PREVIEW_URL")
    if preview:
        bases.append(preview)
    return [f"{b.rstrip('/')}/?{q}" for b in bases]


def lineup(humans: int, vs: list[str]) -> list[Entrant]:
    seats = [Entrant(kind="human", name=f"human{i}") for i in range(humans)]
    return seats + [parse_entrant(v) for v in vs]


def play(
    game: str,
    humans: int,
    vs: list[str],
    server_url: str | None,
    out_root: Path,
    seed: int,
    config: dict,
    clock: str = "wallclock",
    obs_mode: str = "state",
    perception_delay_ms: int = 150,
    join_timeout_s: float = 600.0,
    log=print,
) -> dict:
    seats = lineup(humans, vs)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = out_root / "play" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    with connect_or_start(
        server_url, run_dir / "server-runs", log_path=run_dir / "server.log"
    ) as client:
        spec = client.game(game)
        lo, hi = (spec.get("seats") or {}).get("min", 1), (spec.get("seats") or {}).get("max", 99)
        if not lo <= len(seats) <= hi:
            raise ValueError(f"{game} takes {lo}..{hi} seats, lineup has {len(seats)}")
        cfg = {
            "game": game,
            "config": config,
            "seed": seed,
            "clock": clock,
            "perception_delay_ms": perception_delay_ms,
            "seats": [
                {"kind": "bot", "bot": e.bot}
                if e.kind == "bot"
                else {"kind": "remote", "name": e.name}
                for e in seats
            ],
            "join_timeout_s": join_timeout_s,
            "max_wall_s": 3 * 3600,
        }
        created = client.create_match(cfg)
        mid = str(created["match_id"])
        teams = {s.get("index"): s.get("team") for s in created.get("seats", [])}
        log(f"match {mid} ({game}, seed {seed}, {clock}) on {client.base_url}")
        procs = AgentProcs()
        for i, e in enumerate(seats):
            if e.kind == "agent":
                procs.launch(
                    i,
                    agent_argv(e, client.ws_url, mid, i, obs_mode),
                    run_dir / mid / f"seat{i}.log",
                    None,
                )
            who = e.name if e.kind != "bot" else f"bot:{e.bot}"
            log(f"  seat {i} team {teams.get(i, '?')}: {who}")
            if e.kind == "human":
                for url in join_urls(client.ws_url, mid, i):
                    log(f"      join: {url}")
        return _wait(client, mid, procs, run_dir, log)


def _wait(client: ServerClient, mid: str, procs: AgentProcs, run_dir: Path, log) -> dict:
    status = "waiting"
    try:
        while status not in TERMINAL:
            time.sleep(1.0)
            status = client.match(mid).get("status", status)
    except KeyboardInterrupt:
        log("interrupted; stopping agents")
        status = "interrupted"
    finally:
        procs.stop(grace_s=5.0 if status == "finished" else 0.5)
    out: dict = {"match_id": mid, "status": status}
    if status == "finished":
        result = client.result(mid)
        (run_dir / mid).mkdir(parents=True, exist_ok=True)
        (run_dir / mid / "result.json").write_text(json.dumps(result, indent=2))
        out["outcome"] = result.get("outcome")
        log(f"finished: winner team {(result.get('outcome') or {}).get('winner_team')}")
    return out
