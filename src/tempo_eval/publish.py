"""`tempo-eval publish`: results -> `leaderboard.json` (+ a markdown report).

The JSON has exactly the shape of tempo `docs/ARCHITECTURE.md` §10, plus a few additive fields
the site may use (`name`, `win_rate_ci`, `frontier`, `hypervolume`, ...). It is built from an
allow-list of numeric and identifying fields only: nothing from agent logs, prompts or free
text in results ever reaches it, so publishing cannot leak a prompt or a key.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .metrics import Entry, build_entries, weighted_median, wilson
from .pareto import frontier, hypervolume, normalise
from .results import Loaded

#: where tempo-site reads the leaderboard from (agreed with the site team)
SITE_DATA_PATH = Path("src/data/leaderboard.json")

_SAFE = re.compile(r"[^A-Za-z0-9 _.:/\[\]@+-]")


def _safe(s: str | None, limit: int = 80) -> str | None:
    return None if s is None else _SAFE.sub("", str(s))[:limit]


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(float(x), nd)


def _entry_json(e: Entry) -> dict:
    lo, hi = wilson(e.wins, e.matches)
    total = e.answered + e.missed
    points = []
    for suite, (wins, n, lat_pairs) in sorted(e.suites.items()):
        lat = weighted_median(lat_pairs)
        if lat is not None and n:
            points.append(
                {
                    "suite": _safe(suite),
                    "latency_ms_p50": _r(lat, 2),
                    "win_rate": _r(wins / n),
                    "matches": n,
                }
            )
    hv = hypervolume([(p["latency_ms_p50"], p["win_rate"]) for p in points]) if points else None
    return {
        "name": _safe(e.name),
        "agent": _safe(e.agent),
        "model": _safe(e.model),
        "class": _safe(e.klass),
        "obs_mode": _safe(e.obs_mode),
        "kind": _safe(e.kind),
        "rating": _r(e.rating.display, 1) if e.rating else None,
        "rating_sd": _r(e.rating.display_sd, 1) if e.rating else None,
        "rating_conservative": _r(e.rating.conservative, 1) if e.rating else None,
        "matches": e.matches,
        "verified_matches": e.verified,
        "win_rate": _r(e.win_rate),
        "win_rate_ci": [_r(lo), _r(hi)],
        "latency_ms_p50": _r(e.latency_p50, 2),
        "latency_ms_p95": _r(e.latency_p95, 2),
        "missed_deadline_rate": _r(e.missed / total) if total else None,
        "cost_per_min_usd": _r(e.cost_per_min, 6),
        "tokens_in": e.tokens_in,
        "tokens_out": e.tokens_out,
        "metrics": {_safe(k, 40): _r(v) for k, v in e.metrics().items()},
        "frontier": points,
        "hypervolume": _r(hv),
    }


def build_leaderboard(loaded: Loaded) -> dict:
    by_game = build_entries(loaded.samples)
    games: dict[str, dict] = {}
    for game, entries in sorted(by_game.items()):
        rows = sorted(
            (_entry_json(e) for e in entries.values()),
            key=lambda r: (r["rating_conservative"] is None, -(r["rating_conservative"] or 0.0)),
        )
        # game-level frontier over entries with a latency figure, capability = rating
        pts = [
            (r["latency_ms_p50"], r["rating"])
            for r in rows
            if r["latency_ms_p50"] is not None and r["rating"] is not None
        ]
        named = [
            r["name"] for r in rows if r["latency_ms_p50"] is not None and r["rating"] is not None
        ]
        caps = normalise([p[1] for p in pts])
        front = [named[i] for i in frontier(pts)]
        samples = [s for s in loaded.samples if s.game == game]
        hv = hypervolume([(p[0], c) for p, c in zip(pts, caps, strict=True)]) if pts else None
        games[game] = {
            "entries": rows,
            "frontier": front,
            "hypervolume": _r(hv),
            "matches": len(samples),
            "suites": sorted({s.suite for s in samples}),
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tempo_git_sha": loaded.tempo_shas[-1] if loaded.tempo_shas else None,
        "tempo_git_shas": loaded.tempo_shas,
        "rating_scale": "1000 + 40*(mu-25), OpenSkill Plackett-Luce; sorted by rating - 3 sd",
        "rejected_matches": len(loaded.rejected),
        "games": games,
    }


def render_report(board: dict) -> str:
    lines = [
        "# TEMPO leaderboard",
        "",
        f"Generated {board['generated_at']} from tempo `{board['tempo_git_sha']}`. "
        f"Rating: {board['rating_scale']}. Rejected matches: {board['rejected_matches']}.",
        "",
    ]
    for game, g in board["games"].items():
        lines += [
            f"## {game}",
            "",
            f"{g['matches']} matches from {', '.join(g['suites'])}. "
            f"Latency frontier: {', '.join(g['frontier']) or '-'}; "
            f"hypervolume {g['hypervolume']}.",
            "",
            "| # | entrant | class | obs | rating | ± | matches | win rate (95% CI) "
            "| p50 ms | p95 ms | $/min |",
            "|---|---|---|---|---:|---:|---:|---|---:|---:|---:|",
        ]
        for i, r in enumerate(g["entries"], 1):
            ci = r["win_rate_ci"]
            lines.append(
                f"| {i} | {r['name']} | {r['class']} | {r['obs_mode']} | {r['rating']} | "
                f"{r['rating_sd']} | {r['matches']} | {r['win_rate']:.2f} "
                f"({ci[0]:.2f}–{ci[1]:.2f}) | {r['latency_ms_p50']} | {r['latency_ms_p95']} | "
                f"{r['cost_per_min_usd']} |"
            )
        lines.append("")
    return "\n".join(lines)


def write_outputs(board: dict, out_dir: Path, site: Path | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(board, indent=2) + "\n"
    written = [out_dir / "leaderboard.json", out_dir / "REPORT.md"]
    written[0].write_text(text)
    written[1].write_text(render_report(board))
    if site is not None:
        target = site / SITE_DATA_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        written.append(target)
    return written
