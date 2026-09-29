"""Against the real pinned engine (skipped unless `uv sync --extra tempo` / dev-link)."""

import json

import pytest

tempo = pytest.importorskip("tempo")

from tempo_eval.metrics import build_entries  # noqa: E402
from tempo_eval.results import load_results  # noqa: E402
from tempo_eval.verify import verifier_available, verify_replay  # noqa: E402


def play_bots(seed: int) -> tuple[dict, dict]:
    env = tempo.make(
        "snake", seed=seed, seats=["bot", "bot", "bot"], config={"rounds": 1, "round_seconds": 5}
    )
    env.reset()
    while not env.done:
        env.step([])
    return env.result(), env.replay()


def test_real_replays_verify_and_tampering_is_caught():
    assert verifier_available()
    _, rep = play_bots(1000)
    assert verify_replay(rep) is True
    assert verify_replay({**rep, "final_hash": "0" * 16}) is False
    pads = json.loads(json.dumps(rep["pads"]))
    for _, pad in pads[0]:  # steer seat 0 the other way on every run
        pad["ls"] = [0.0, -1.0] if pad["ls"][1] >= 0 else [0.0, 1.0]
    assert verify_replay({**rep, "pads": pads}) is False
    assert verify_replay({**rep, "seed": rep["seed"] + 1}) is False


def test_real_results_load_into_entries(tmp_path):
    run = tmp_path / "s" / "r1"
    matches = []
    for job, seed in enumerate((1000, 1001)):
        result, rep = play_bots(seed)
        mid = f"m{job}"
        (run / mid).mkdir(parents=True)
        (run / mid / "result.json").write_text(json.dumps(result))
        seats = [{"kind": "bot", "bot": "scripted", "name": "bot:scripted"}] * 3
        matches.append(
            {
                "job": job,
                "seed": seed,
                "seats": seats,
                "status": "finished",
                "match_id": mid,
                "replay_verified": verify_replay(rep),
            }
        )
    (run / "run.json").write_text(
        json.dumps(
            {
                "suite": {"name": "s", "game": "snake"},
                "run_id": "r1",
                "server": {},
                "matches": matches,
            }
        )
    )
    loaded = load_results(tmp_path)
    assert len(loaded.samples) == 2
    e = build_entries(loaded.samples)["snake"]["bot:scripted|open|state"]
    assert e.matches == 2 and e.verified == 2
    assert "mean_length" in e.metrics() and "seat" not in e.metrics()
