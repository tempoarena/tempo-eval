import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakeserver import FakeServer


@pytest.fixture
def fake_server():
    with FakeServer(play_s=0.2) as s:
        yield s


@pytest.fixture
def fake_team_server():
    with FakeServer(play_s=0.2, two_teams=True) as s:
        yield s


@pytest.fixture
def fake_agent(tmp_path, monkeypatch):
    log = tmp_path / "agents.jsonl"
    script = Path(__file__).parent / "fake_agent.py"
    monkeypatch.setenv("TEMPO_AGENT_CMD", f"{sys.executable} {script}")
    monkeypatch.setenv("FAKE_AGENT_LOG", str(log))
    return log
