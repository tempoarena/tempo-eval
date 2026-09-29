"""`tempo-eval` command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dotenv import load_dotenv
from .publish import build_leaderboard, write_outputs
from .results import load_results
from .runner import Runner, local_game_spec, plan_jobs
from .suite import load_suite


def _kv(pairs: list[str]) -> dict:
    out = {}
    for p in pairs:
        k, _, v = p.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def cmd_run(a: argparse.Namespace) -> int:
    rc = 0
    for path in a.suites:
        suite = load_suite(path)
        if a.dry_run:
            for j in plan_jobs(suite, local_game_spec(suite.game)):
                print(f"{j.index:3d} seed={j.seed} seats={[e.name for e in j.seats]}")
            continue
        summary = Runner(suite, Path(a.out)).run(server_url=a.server)
        print(
            f"[{suite.name}] {summary['counts']} spent ${summary['spent_usd']:.4f} "
            f"-> {Path(a.out) / suite.name / summary['run_id']}"
        )
        if set(summary["counts"]) - {"finished"}:
            rc = 1
    return rc


def cmd_publish(a: argparse.Namespace) -> int:
    loaded = load_results(
        Path(a.results),
        suites=a.suite or None,
        leaderboard_only=not a.all,
        require_verified=a.require_verified,
    )
    board = build_leaderboard(loaded)
    for p in write_outputs(board, Path(a.out), Path(a.site) if a.site else None):
        print(f"wrote {p}")
    print(f"{len(loaded.samples)} matches, {len(loaded.rejected)} rejected")
    return 0


def cmd_play(a: argparse.Namespace) -> int:
    from .play import play

    vs = [v for chunk in a.vs for v in chunk.split(",") if v]
    out = play(
        a.game,
        a.human,
        vs,
        a.server,
        Path(a.out),
        seed=a.seed,
        config=_kv(a.config),
        clock=a.clock,
        obs_mode=a.obs_mode,
    )
    return 0 if out["status"] == "finished" else 1


def main(argv: list[str] | None = None) -> int:
    # progress lines (join URLs above all) must appear immediately even when piped to a file
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    load_dotenv()
    p = argparse.ArgumentParser(prog="tempo-eval", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="play every match of one or more suites")
    r.add_argument("suites", nargs="+")
    r.add_argument("--server", help="use this server (http://host:port) instead of starting one")
    r.add_argument("--out", default="results")
    r.add_argument("--dry-run", action="store_true", help="print the match plan and stop")
    r.set_defaults(fn=cmd_run)

    pb = sub.add_parser("publish", help="results -> leaderboard.json + REPORT.md")
    pb.add_argument("--results", default="results")
    pb.add_argument("--out", default="published")
    pb.add_argument("--site", help="tempo-site checkout; writes src/data/leaderboard.json")
    pb.add_argument("--suite", action="append", help="only these suites (repeatable)")
    pb.add_argument("--all", action="store_true", help="include suites without leaderboard: true")
    pb.add_argument(
        "--require-verified",
        action="store_true",
        help="drop matches whose replay could not be verified",
    )
    pb.set_defaults(fn=cmd_publish)

    pl = sub.add_parser("play", help="live match: human seats + launched AI seats")
    pl.add_argument("--game", required=True)
    pl.add_argument("--human", type=int, default=1, help="number of human seats (first seats)")
    pl.add_argument(
        "--vs",
        action="append",
        default=[],
        help="entrants, comma separated: llm,jev,bot:scripted,'agent:llm model=...'",
    )
    pl.add_argument("--server")
    pl.add_argument("--seed", type=int, default=1)
    pl.add_argument("--clock", default="wallclock")
    pl.add_argument("--obs-mode", default="state")
    pl.add_argument("--config", action="append", default=[], help="game config key=value")
    pl.add_argument("--out", default="results")
    pl.set_defaults(fn=cmd_play)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
