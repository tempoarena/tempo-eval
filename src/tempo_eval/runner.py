"""`tempo-eval run`: play every match a suite implies and keep what the matches produced.

One run of a suite writes `results/<suite>/<run_id>/`:

    run.json                  the suite, the server's version/git SHA, timing, per-match status
    <match_id>/match.json     who sat where, attempts, agent exit codes, verification
    <match_id>/result.json    the server's result (spec: tempo docs/ARCHITECTURE.md §10)
    <match_id>/replay.json    the server's replay
    <match_id>/seat<N>.log    agent output (may hold prompts; results/ is gitignored)

Matches run in parallel up to `limits.concurrency`, each in its own thread: create the match
over HTTP, launch one `tempo-agent` per agent seat, poll until the match is terminal or times
out, then fetch and verify. A match that aborts or times out is retried with the same seed up to
`limits.retries` times; it is never silently replaced by a different seed.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .agents import AgentProcs, agent_argv
from .server import TERMINAL, ServerClient, ServerError, connect_or_start
from .suite import Entrant, Suite, rotations
from .verify import verify_replay


@dataclass
class Job:
    index: int
    seed: int
    seats: list[Entrant]
    repeat: int = 0


@dataclass
class MatchRecord:
    job: int
    seed: int
    seats: list[dict]
    status: str = "pending"  # finished | aborted | timeout | error | skipped
    match_id: str | None = None
    attempts: int = 0
    error: str | None = None
    agent_exit_codes: dict = field(default_factory=dict)
    replay_verified: bool | None = None
    over_cost_cap: bool = False
    cost_usd: float = 0.0
    wall_s: float = 0.0


def seat_order(lineup: list[str], game_spec: dict | None) -> list[str]:
    """Map a written lineup onto the server's seat -> team rule.

    Two-team lineups are written as team blocks (first half one team, second half the other),
    which is how people think about them. tempo assigns two-team seats alternately
    (spec/games/breach.json `seat_teams`: "seat i plays for team i % 2"), so the blocks are
    interleaved: [a0, b0, a1, b1, ...]. Free-for-all lineups are used as written.
    """
    if not game_spec or game_spec.get("teams") != "two" or len(lineup) % 2:
        return list(lineup)
    half = len(lineup) // 2
    out: list[str] = []
    for a, b in zip(lineup[:half], lineup[half:], strict=True):
        out += [a, b]
    return out


def local_game_spec(game: str) -> dict | None:
    """The game spec from the installed tempo, for planning without a server (dry runs)."""
    try:
        import tempo  # type: ignore[import-not-found]

        spec = tempo.spec(game)
    except Exception:
        return None
    return spec if isinstance(spec, dict) else None


def plan_jobs(suite: Suite, game_spec: dict | None = None) -> list[Job]:
    jobs: list[Job] = []
    for seed in suite.seed_list():
        for lineup in suite.lineups:
            for seating in rotations(lineup, suite.rotation):
                for rep in range(suite.matches_per_seed):
                    ents = [suite.entrant(ref) for ref in seat_order(seating, game_spec)]
                    jobs.append(Job(index=len(jobs), seed=seed, seats=ents, repeat=rep))
    return jobs


def match_config(suite: Suite, job: Job) -> dict:
    seats = []
    for e in job.seats:
        if e.kind == "bot":
            seats.append({"kind": "bot", "bot": e.bot})
        else:
            seats.append({"kind": "remote", "name": e.name})
    return {
        "game": suite.game,
        "config": suite.config,
        "seed": job.seed,
        "clock": suite.clock,
        "perception_delay_ms": suite.perception_delay_ms,
        "seats": seats,
        "join_timeout_s": suite.limits.join_timeout_s,
        "max_wall_s": suite.limits.match_timeout_s,
    }


def seat_cost(result: dict) -> list[float]:
    per = (result.get("stats") or {}).get("per_seat") or []
    return [float(((s or {}).get("usage") or {}).get("cost_usd") or 0.0) for s in per]


class Runner:
    def __init__(
        self, suite: Suite, out_root: Path, poll_s: float = 0.5, agent_cwd: Path | None = None
    ):
        self.suite = suite
        self.poll_s = poll_s
        self.agent_cwd = agent_cwd
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = out_root / suite.name / stamp
        self.spent_usd = 0.0
        self._lock = threading.Lock()

    def _over_budget(self) -> bool:
        cap = self.suite.limits.suite_cost_cap_usd
        with self._lock:
            return cap is not None and self.spent_usd >= cap

    def run(self, server_url: str | None = None, log=print) -> dict:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if any(self.suite.entrant(r).kind == "human" for lu in self.suite.lineups for r in lu):
            raise ValueError("suites cannot seat humans; use `tempo-eval play`")
        started = time.time()
        with connect_or_start(
            server_url, self.run_dir / "server-runs", log_path=self.run_dir / "server.log"
        ) as client:
            health = client.health()
            jobs = plan_jobs(self.suite, client.game(self.suite.game))
            log(
                f"[{self.suite.name}] {len(jobs)} matches on {client.base_url} "
                f"(tempo {health.get('version')} {health.get('git_sha', '')[:12]})"
            )
            records: list[MatchRecord] = [None] * len(jobs)  # type: ignore[list-item]
            with ThreadPoolExecutor(max_workers=max(1, self.suite.limits.concurrency)) as pool:
                futs = {pool.submit(self._run_job, client.base_url, j): j for j in jobs}
                for fut in as_completed(futs):
                    rec = fut.result()
                    records[rec.job] = rec
                    log(
                        f"  match {rec.job:3d} seed={rec.seed} {rec.status:9s} "
                        f"{rec.match_id or '-'} verified={rec.replay_verified} "
                        f"${rec.cost_usd:.4f}" + (f"  ({rec.error})" if rec.error else "")
                    )
        summary = {
            "suite": self.suite.model_dump(by_alias=True),
            "run_id": self.run_dir.name,
            "server": {"url": server_url or "local", **health},
            "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
            "wall_s": round(time.time() - started, 3),
            "spent_usd": round(self.spent_usd, 6),
            "counts": _counts(records),
            "matches": [asdict(r) for r in records],
        }
        (self.run_dir / "run.json").write_text(json.dumps(summary, indent=2))
        return summary

    def _run_job(self, base_url: str, job: Job) -> MatchRecord:
        rec = MatchRecord(
            job=job.index, seed=job.seed, seats=[e.model_dump(by_alias=True) for e in job.seats]
        )
        if self._over_budget():
            rec.status, rec.error = "skipped", "suite cost cap reached"
            return rec
        client = ServerClient(base_url)
        t0 = time.monotonic()
        try:
            for attempt in range(1 + self.suite.limits.retries):
                rec.attempts = attempt + 1
                self._attempt(client, job, rec)
                if rec.status == "finished":
                    break
        finally:
            client.close()
            rec.wall_s = round(time.monotonic() - t0, 3)
        return rec

    def _attempt(self, client: ServerClient, job: Job, rec: MatchRecord) -> None:
        rec.error = None
        try:
            created = client.create_match(match_config(self.suite, job))
        except ServerError as e:
            rec.status, rec.error = "error", str(e)
            return
        mid = str(created["match_id"])
        rec.match_id = mid
        mdir = self.run_dir / mid
        mdir.mkdir(parents=True, exist_ok=True)
        procs = AgentProcs()
        for seat, ent in enumerate(job.seats):
            if ent.kind == "agent":
                argv = agent_argv(
                    ent,
                    client.ws_url,
                    mid,
                    seat,
                    self.suite.obs_mode,
                    seed=job.seed,
                    cost_cap_usd=self.suite.limits.cost_cap_usd,
                    summary_path=mdir / f"seat{seat}.summary.json",
                )
                procs.launch(
                    seat,
                    argv,
                    mdir / f"seat{seat}.log",
                    self.suite.limits.cost_cap_usd,
                    cwd=self.agent_cwd,
                )
        deadline = (
            time.monotonic() + self.suite.limits.match_timeout_s + self.suite.limits.join_timeout_s
        )
        status = "waiting"
        try:
            while time.monotonic() < deadline:
                try:
                    status = client.match(mid).get("status", status)
                except ServerError as e:
                    rec.error = str(e)
                if status in TERMINAL:
                    break
                crashed = procs.crashed()
                if crashed and status == "waiting":
                    # an agent died before joining: the match can never start, don't wait out
                    # the join timeout
                    status = "aborted"
                    rec.error = f"agent(s) exited before joining: {crashed}"
                    break
                time.sleep(self.poll_s)
            else:
                status = "timeout"
        finally:
            rec.agent_exit_codes = {
                str(k): v
                for k, v in procs.stop(grace_s=10.0 if status == "finished" else 0.5).items()
            }
        rec.status = status
        if status != "finished":
            rec.error = rec.error or f"match {status}"
            (mdir / "match.json").write_text(json.dumps(asdict(rec), indent=2))
            return
        try:
            result = client.result(mid)
        except ServerError as e:
            rec.status, rec.error = "error", f"result: {e}"
            return
        replay = client.replay(mid)
        (mdir / "result.json").write_text(json.dumps(result, indent=2))
        if replay is not None:
            (mdir / "replay.json").write_text(json.dumps(replay))
        rec.replay_verified = verify_replay(replay)
        costs = seat_cost(result)
        rec.cost_usd = round(sum(costs), 6)
        cap = self.suite.limits.cost_cap_usd
        rec.over_cost_cap = cap is not None and any(c > cap for c in costs)
        with self._lock:
            self.spent_usd += rec.cost_usd
        (mdir / "match.json").write_text(json.dumps(asdict(rec), indent=2))


def _counts(records: list[MatchRecord]) -> dict:
    out: dict[str, int] = {}
    for r in records:
        out[r.status] = out.get(r.status, 0) + 1
    return out
