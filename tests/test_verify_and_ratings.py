from tempo_eval.play import join_urls, lineup
from tempo_eval.ratings import team_ranks
from tempo_eval.results import Sample, Seat
from tempo_eval.verify import verify_replay


def test_verify_replay_compares_normalised_hashes():
    rep = {"final_hash": "00000000DEADBEEF"}
    assert verify_replay(rep, verifier=lambda s: "deadbeef") is True
    assert verify_replay(rep, verifier=lambda s: "0xdeadbeef") is True
    assert verify_replay(rep, verifier=lambda s: "beef") is False
    assert verify_replay(None, verifier=lambda s: "x") is None

    def boom(s):
        raise RuntimeError("diverged")

    assert verify_replay(rep, verifier=boom) is False


def _sample(outcome, teams):
    seats = [Seat(i, t, f"p{i}", "a", None, "open", "state", "agent") for i, t in enumerate(teams)]
    return Sample("s", "r", "g", "charged", True, "m", 1, seats, {"outcome": outcome}, True)


def test_team_ranks():
    assert team_ranks(_sample({"scores": [3, 1, 3]}, [0, 1, 2])) == {0: 0, 1: 1, 2: 0}
    assert team_ranks(_sample({"winner_team": 1}, [0, 0, 1, 1])) == {0: 1, 1: 0}
    assert team_ranks(_sample({"winner_team": None}, [0, 1])) == {0: 0, 1: 0}
    assert team_ranks(_sample({}, [0, 1])) is None


def test_play_lineup_and_urls(monkeypatch):
    seats = lineup(1, ["llm", "jev", "bot:scripted"])
    assert [s.kind for s in seats] == ["human", "agent", "agent", "bot"]
    monkeypatch.delenv("TEMPO_PLAY_URL", raising=False)
    monkeypatch.setenv("TEMPO_PLAY_PREVIEW_URL", "https://tempo-play-x.vercel.app")
    urls = join_urls("ws://127.0.0.1:8765", "abc", 0)
    assert urls[0] == "http://localhost:5173/?server=ws%3A%2F%2F127.0.0.1%3A8765&match=abc&seat=0"
    assert urls[1].startswith("https://tempo-play-x.vercel.app/?server=")


def test_agent_argv_routes_models_to_the_right_flag():
    from tempo_eval.agents import agent_argv
    from tempo_eval.suite import parse_entrant

    jev = agent_argv(
        parse_entrant("jev model=jev-latest"), "ws://h", "m", 1, "state", launcher=["tempo-agent"]
    )
    assert jev[jev.index("--jev-model") + 1] == "jev-latest" and "--model" not in jev
    hy = agent_argv(
        parse_entrant("hybrid model=bedrock_mantle/x jev_model=jev-1"),
        "ws://h",
        "m",
        0,
        "vision",
        seed=7,
        launcher=["tempo-agent"],
    )
    assert hy[hy.index("--model") + 1] == "bedrock_mantle/x"
    assert hy[hy.index("--jev-model") + 1] == "jev-1"
    assert hy[hy.index("--seed") + 1] == "7" and hy[hy.index("--obs-mode") + 1] == "vision"


def test_play_marks_human_seats_and_prints_every_seats_delay(tmp_path, fake_server, fake_agent):
    from tempo_eval.play import play

    lines: list[str] = []
    out = play(
        "snake",
        1,
        ["llm", "bot:scripted"],
        fake_server.url,
        tmp_path,
        seed=3,
        config={},
        log=lines.append,
    )
    assert out["status"] == "finished"
    seats = fake_server.configs[-1]["seats"]
    assert seats[0] == {"kind": "remote", "name": "human0", "human": True}
    assert seats[1]["perception_delay_ms"] == 150 and "human" not in seats[1]
    assert seats[2] == {"kind": "bot", "bot": "scripted"}
    text = "\n".join(lines)
    assert "seat 0 team 0: human0  (perception delay 0 ms)" in text
    assert "(perception delay 150 ms)" in text and "WARNING" not in text
