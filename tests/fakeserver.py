"""A stand-in for tempo-server's HTTP half (spec/protocol.md), for tests only.

Matches "play" for `play_s` seconds and then finish. The outcome is scripted so tests can assert
on ratings: the seat whose entrant name sorts by STRENGTH wins. Result and replay documents have
the shape of tempo docs/ARCHITECTURE.md §10 and spec/replay.schema.json.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STRENGTH = {"bot:scripted": 3, "scripted": 3, "llm": 2, "jev": 2, "random": 1}


def strength(seat: dict) -> int:
    name = seat.get("name") or f"bot:{seat.get('bot')}"
    return STRENGTH.get(name, STRENGTH.get(seat.get("bot", ""), 0))


class FakeServer:
    def __init__(self, play_s: float = 0.2, two_teams: bool = False):
        self.play_s = play_s
        self.two_teams = two_teams
        self.prototypes: set[str] = {"tidefall"}
        self.configs: list[dict] = []
        self.matches: dict[str, dict] = {}
        self._n = 0
        self._lock = threading.Lock()
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _send(self, code: int, body) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                p = self.path.split("?")[0].strip("/").split("/")
                if p == ["health"]:
                    return self._send(
                        200,
                        {
                            "ok": True,
                            "version": "0.1.0-fake",
                            "git_sha": "f" * 40,
                            "games": ["snake", "breach"],
                        },
                    )
                if p[0] == "games" and len(p) == 2:
                    return self._send(
                        200,
                        {
                            "id": p[1],
                            "tick_hz": 32,
                            "teams": "two" if srv.two_teams else "ffa_or_teams",
                            "status": "prototype" if p[1] in srv.prototypes else "approved",
                            "seats": {"min": 1, "max": 10, "default": 4},
                        },
                    )
                if p[0] == "matches" and len(p) >= 2 and p[1] in srv.matches:
                    m = srv.status(p[1])
                    if len(p) == 2:
                        return self._send(200, m)
                    if m["status"] != "finished":
                        return self._send(404, {"error": "not finished"})
                    if p[2] == "result":
                        return self._send(200, srv.result(p[1]))
                    if p[2] == "replay":
                        return self._send(
                            200, {"format": "tempo-replay/1", "final_hash": "00000000deadbeef"}
                        )
                return self._send(404, {"error": "not found"})

            def do_POST(self):
                if self.path != "/matches":
                    return self._send(404, {"error": "not found"})
                n = int(self.headers.get("content-length", 0))
                cfg = json.loads(self.rfile.read(n))
                return self._send(200, srv.create(cfg))

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> FakeServer:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()

    def team_of(self, i: int, n: int) -> int:
        return i % 2 if self.two_teams else i  # tempo's rule for two-team games

    def create(self, cfg: dict) -> dict:
        with self._lock:
            self._n += 1
            mid = f"m{self._n:04d}"
        n = len(cfg["seats"])
        self.configs.append(cfg)
        delay = cfg.get("perception_delay_ms", 150)
        seats = [
            {
                "index": i,
                "team": self.team_of(i, n),
                "role": "player",
                "kind": s["kind"],
                # tempo's rule: explicit per-seat delay, else 0 for humans, else the match's
                "perception_delay_ms": s.get("perception_delay_ms", 0 if s.get("human") else delay),
            }
            for i, s in enumerate(cfg["seats"])
        ]
        self.matches[mid] = {"cfg": cfg, "t0": time.monotonic(), "seats": seats}
        return {"match_id": mid, "seats": seats}

    def status(self, mid: str) -> dict:
        m = self.matches[mid]
        done = time.monotonic() - m["t0"] >= self.play_s
        return {
            "match_id": mid,
            "game": m["cfg"]["game"],
            "status": "finished" if done else "running",
            "seats": m["seats"],
        }

    def result(self, mid: str) -> dict:
        m = self.matches[mid]
        cfg, seats = m["cfg"], m["seats"]
        nteams = max(s["team"] for s in seats) + 1
        scores = [0.0] * nteams
        for s, sc in zip(seats, cfg["seats"], strict=False):
            scores[s["team"]] += strength(sc)
        best = max(scores)
        winner = scores.index(best) if scores.count(best) == 1 else None
        per_stats = []
        for sc in cfg["seats"]:
            bot = sc["kind"] == "bot"
            per_stats.append(
                {
                    "latency_ms": {
                        "p50": 0.1 if bot else 800.0,
                        "p95": 0.2 if bot else 2000.0,
                        "max": 1.0 if bot else 4000.0,
                    },
                    "answered": 960,
                    "missed_deadlines": 0 if bot else 12,
                    "msgs_sent": 0,
                    "usage": {
                        "tokens_in": 0 if bot else 50_000,
                        "tokens_out": 0 if bot else 2_000,
                        "cost_usd": 0.0 if bot else 0.05,
                        "calls": 0 if bot else 10,
                        "latency_ms_total": 0.0 if bot else 9_000.0,
                    },
                    "thinks": 0 if bot else 10,
                    "think_ticks": 0 if bot else 320,
                }
            )
        return {
            "match_id": mid,
            "game": cfg["game"],
            "config": cfg["config"],
            "seed": cfg["seed"],
            "clock": cfg["clock"],
            "seats": [
                {**s, "name": sc.get("name") or f"bot:{sc.get('bot')}"}
                for s, sc in zip(seats, cfg["seats"], strict=False)
            ],
            "outcome": {
                "winner_team": winner,
                "scores": scores,
                "per_seat": [
                    {
                        "length": 3.0 * strength(sc),
                        "kills": strength(sc) - 1,
                        "note": "SECRET PROMPT TEXT must never be published",
                    }
                    for sc in cfg["seats"]
                ],
                "details": {},
            },
            "stats": {"per_seat": per_stats},
            "ticks": 32 * 60,
            "duration_ms": 60_000,
            "replay_hash": "00000000deadbeef",
            "tempo_version": "0.1.0-fake",
            "tempo_git_sha": "f" * 40,
        }
