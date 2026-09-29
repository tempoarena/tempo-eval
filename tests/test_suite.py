import pytest

from tempo_eval.suite import DEV_SEED_BASE, Suite, parse_entrant, rotations


def suite(**kw) -> Suite:
    base = {"name": "t", "game": "snake", "lineups": [["bot:scripted", "random"]]}
    return Suite.model_validate({**base, **kw})


def test_inline_entrants():
    e = parse_entrant("agent:llm model=bedrock_mantle/openai.gpt-5.6-luna class=fixed-model")
    assert (e.kind, e.agent, e.model, e.klass) == (
        "agent",
        "llm",
        "bedrock_mantle/openai.gpt-5.6-luna",
        "fixed-model",
    )
    assert e.name == "llm[openai.gpt-5.6-luna]"
    assert parse_entrant("bot:scripted").label == "bot:scripted"
    assert parse_entrant("jev").agent == "jev"
    assert parse_entrant("human").kind == "human"
    with pytest.raises(ValueError):
        parse_entrant("robot:x")


def test_named_entrants_and_unknown_refs():
    s = suite(entrants={"s": {"kind": "bot", "bot": "scripted"}}, lineups=[["s", "random"]])
    assert s.entrant("s").name == "s"
    with pytest.raises(ValueError):
        suite(entrants={"s": {"kind": "bot"}}, lineups=[["s"]])


def test_dev_seeds_are_a_public_fixed_range():
    assert suite(seeds={"set": "dev", "count": 3, "start": 2}).seed_list() == [
        DEV_SEED_BASE + 2,
        DEV_SEED_BASE + 3,
        DEV_SEED_BASE + 4,
    ]


def test_heldout_seeds_come_from_the_private_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TEMPO_HELDOUT_SEEDS", raising=False)
    s = suite(seeds={"set": "heldout", "count": 2})
    with pytest.raises(ValueError, match="TEMPO_HELDOUT_SEEDS"):
        s.seed_list()
    f = tmp_path / "private.yaml"
    f.write_text("snake: [91, 92, 93]\ndefault: [1]\n")
    monkeypatch.setenv("TEMPO_HELDOUT_SEEDS", str(f))
    assert s.seed_list() == [91, 92]


def test_rotations():
    assert rotations(["a", "b", "c"], "cyclic") == [
        ["a", "b", "c"],
        ["b", "c", "a"],
        ["c", "a", "b"],
    ]
    assert rotations(["a", "a"], "cyclic") == [["a", "a"]]
    assert rotations(["a", "a", "b", "b"], "swap") == [["a", "a", "b", "b"], ["b", "b", "a", "a"]]
    assert rotations(["a", "b"], "none") == [["a", "b"]]


def test_every_shipped_suite_parses():
    from pathlib import Path

    from tempo_eval.suite import load_suite

    paths = sorted(Path(__file__).parent.parent.glob("suites/*.yaml"))
    assert paths
    for p in paths:
        load_suite(p)


def test_two_team_lineups_follow_the_servers_seat_rule():
    from tempo_eval.runner import seat_order

    two = {"teams": "two"}
    assert seat_order(["a1", "a2", "b1", "b2"], two) == ["a1", "b1", "a2", "b2"]
    assert seat_order(["a", "b", "c"], {"teams": "ffa_or_teams"}) == ["a", "b", "c"]
    assert seat_order(["a", "b"], None) == ["a", "b"]


def test_suite_configs_use_only_keys_the_pinned_game_accepts():
    """The registry rejects unknown config keys; catch a renamed key here, not mid-run."""
    from pathlib import Path

    tempo = pytest.importorskip("tempo")
    from tempo_eval.suite import load_suite

    for p in sorted(Path(__file__).parent.parent.glob("suites/*.yaml")):
        s = load_suite(p)
        allowed = set(tempo.spec(s.game)["config_defaults"])
        assert set(s.config) <= allowed, f"{p.name}: unknown keys {set(s.config) - allowed}"
