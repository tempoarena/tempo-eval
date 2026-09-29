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
