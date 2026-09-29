"""Runner -> results -> leaderboard, against the fake server and a fake agent process."""

import json

from tempo_eval.publish import build_leaderboard, write_outputs
from tempo_eval.results import load_results
from tempo_eval.runner import Runner
from tempo_eval.suite import Suite

LEADERBOARD_ENTRY_KEYS = {
    "agent",
    "model",
    "class",
    "obs_mode",
    "rating",
    "rating_sd",
    "matches",
    "win_rate",
    "latency_ms_p50",
    "latency_ms_p95",
    "cost_per_min_usd",
    "metrics",
}


def snake_suite(**kw) -> Suite:
    return Suite.model_validate(
        {
            "name": "snake-test",
            "game": "snake",
            "clock": "charged",
            "leaderboard": True,
            "seeds": {"set": "dev", "count": 3},
            "lineups": [["bot:scripted", "rnd", "llm"]],
            "entrants": {
                "rnd": {"kind": "agent", "agent": "random"},
                "llm": {
                    "kind": "agent",
                    "agent": "llm",
                    "model": "bedrock_mantle/openai.gpt-5.6-luna",
                },
            },
            "limits": {"match_timeout_s": 20, "concurrency": 3, "cost_cap_usd": 0.5},
            **kw,
        }
    )


def test_run_publish_end_to_end(tmp_path, fake_server, fake_agent):
    suite = snake_suite()
    summary = Runner(suite, tmp_path / "results", poll_s=0.05).run(server_url=fake_server.url)
    assert summary["counts"] == {"finished": 9}  # 3 seeds x 3 cyclic seatings
    assert summary["server"]["git_sha"] == "f" * 40

    # one agent process per agent seat, each told exactly which match/seat to join
    launches = [json.loads(line) for line in fake_agent.read_text().splitlines()]
    assert len(launches) == 18
    argv = launches[0]["argv"]
    for flag in ("--agent", "--url", "--match", "--seat", "--obs-mode"):
        assert flag in argv
    assert argv[argv.index("--url") + 1].startswith("ws://127.0.0.1:")
    assert {launch["cap"] for launch in launches} == {"0.5"}
    llm = [la["argv"] for la in launches if "llm" in la["argv"]]
    assert all("bedrock_mantle/openai.gpt-5.6-luna" in a for a in llm)

    loaded = load_results(tmp_path / "results", leaderboard_only=True)
    assert len(loaded.samples) == 9 and not loaded.rejected
    board = build_leaderboard(loaded)
    assert board["tempo_git_sha"] == "f" * 40
    rows = board["games"]["snake"]["entries"]
    by_name = {r["name"]: r for r in rows}
    assert set(by_name) == {"bot:scripted", "rnd", "llm"}
    for r in rows:
        assert set(r) >= LEADERBOARD_ENTRY_KEYS
    # the fake server's strengths: scripted > llm > random
    assert [r["name"] for r in rows] == ["bot:scripted", "llm", "rnd"]
    assert by_name["bot:scripted"]["win_rate"] == 1.0
    assert by_name["llm"]["latency_ms_p50"] == 800.0
    # $0.05 per match-minute for the llm seat
    assert abs(by_name["llm"]["cost_per_min_usd"] - 0.05) < 1e-9
    assert by_name["bot:scripted"]["cost_per_min_usd"] == 0.0
    assert by_name["llm"]["metrics"]["length"] == 6.0
    assert board["games"]["snake"]["frontier"][0] == "bot:scripted"

    written = write_outputs(board, tmp_path / "published", site=tmp_path / "site")
    text = (tmp_path / "site" / "src/data/leaderboard.json").read_text()
    assert json.loads(text) == board
    assert "SECRET" not in text and "prompt" not in text.lower()
    assert "| 1 | bot:scripted |" in written[1].read_text()


def test_two_team_game_and_rotation(tmp_path, fake_team_server, fake_agent):
    suite = Suite.model_validate(
        {
            "name": "breach-test",
            "game": "breach",
            "leaderboard": True,
            "seeds": {"set": "dev", "count": 2},
            "lineups": [["bot:scripted", "bot:scripted", "rnd", "rnd"]],
            "rotation": "swap",
            "entrants": {"rnd": {"kind": "agent", "agent": "random"}},
            "limits": {"match_timeout_s": 20, "concurrency": 4},
        }
    )
    summary = Runner(suite, tmp_path / "r", poll_s=0.05).run(server_url=fake_team_server.url)
    assert summary["counts"] == {"finished": 4}
    board = build_leaderboard(load_results(tmp_path / "r"))
    rows = {r["name"]: r for r in board["games"]["breach"]["entries"]}
    # two seats per team count as one match each, and the stronger team always wins
    assert rows["bot:scripted"]["matches"] == 4 and rows["bot:scripted"]["win_rate"] == 1.0
    assert rows["rnd"]["win_rate"] == 0.0
    assert rows["bot:scripted"]["rating"] > 1000 > rows["rnd"]["rating"]


def test_unreachable_server_records_errors_not_crashes(tmp_path, fake_server, fake_agent):
    suite = snake_suite(limits={"match_timeout_s": 1, "retries": 1})
    runner = Runner(suite, tmp_path / "r", poll_s=0.05)
    # finish nothing: the fake plays forever
    fake_server.play_s = 1e9
    suite.limits.join_timeout_s = 0.2
    summary = runner.run(server_url=fake_server.url)
    assert summary["counts"] == {"timeout": 9}
    assert all(m["attempts"] == 2 for m in summary["matches"])
    assert load_results(tmp_path / "r").samples == []


def test_failed_replay_verification_is_rejected(tmp_path, fake_server, fake_agent, monkeypatch):
    import tempo_eval.runner as runner_mod

    monkeypatch.setattr(runner_mod, "verify_replay", lambda replay: False)
    Runner(snake_suite(seeds={"set": "dev", "count": 1}), tmp_path / "r", poll_s=0.05).run(
        server_url=fake_server.url
    )
    loaded = load_results(tmp_path / "r")
    assert loaded.samples == [] and len(loaded.rejected) == 3
    assert {r["reason"] for r in loaded.rejected} == {"replay did not verify"}
    # unverifiable (None) is kept by default, dropped under require_verified
    monkeypatch.setattr(runner_mod, "verify_replay", lambda replay: None)
    Runner(
        snake_suite(name="s2", seeds={"set": "dev", "count": 1}), tmp_path / "r", poll_s=0.05
    ).run(server_url=fake_server.url)
    assert len(load_results(tmp_path / "r", suites=["s2"]).samples) == 3
    assert load_results(tmp_path / "r", suites=["s2"], require_verified=True).samples == []


def test_suite_cost_cap_stops_new_matches(tmp_path, fake_server, fake_agent):
    suite = snake_suite(
        seeds={"set": "dev", "count": 4},
        limits={"match_timeout_s": 20, "concurrency": 1, "suite_cost_cap_usd": 0.25},
    )
    summary = Runner(suite, tmp_path / "r", poll_s=0.05).run(server_url=fake_server.url)
    # every match costs $0.10 (two agent seats at $0.05); the third match crosses the cap
    assert summary["counts"]["finished"] == 3
    assert summary["counts"]["skipped"] == 9
