"""Launching agent processes for agent seats.

Agents are separate processes (`tempo-agent` from the pinned tempo-baselines) that join a seat
over WebSocket exactly as any outside entrant would, so the evaluator measures the same path a
submission takes. Their output goes to per-seat log files under the run directory, which is
gitignored: LLM agents may log full prompts.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .suite import Entrant

#: Override the agent launcher, e.g. `TEMPO_AGENT_CMD="uv run --project ../tempo tempo-agent"`.
AGENT_CMD_ENV = "TEMPO_AGENT_CMD"
COST_CAP_ENV = "TEMPO_AGENT_MAX_COST_USD"


def agent_launcher() -> list[str]:
    return shlex.split(os.environ.get(AGENT_CMD_ENV, "tempo-agent"))


def agent_argv(
    ent: Entrant,
    ws_url: str,
    match_id: str,
    seat: int,
    obs_mode: str,
    *,
    seed: int | None = None,
    cost_cap_usd: float | None = None,
    summary_path: Path | None = None,
    launcher: list[str] | None = None,
) -> list[str]:
    """The `tempo-agent` command line for one seat (tempo baselines/src/tempo_baselines/cli.py).

    `model` goes to `--model` (litellm id) except for the `jev` agent, whose model is the
    TypeSafe one (`--jev-model`). The match seed is passed so a stochastic agent's own choices
    are reproducible along with the match.
    """
    argv = [
        *(launcher or agent_launcher()),
        "--agent",
        str(ent.agent),
        "--url",
        ws_url,
        "--match",
        match_id,
        "--seat",
        str(seat),
        "--obs-mode",
        ent.obs_mode or obs_mode,
        "--name",
        ent.name,
    ]
    if ent.model:
        argv += ["--jev-model" if ent.agent == "jev" else "--model", ent.model]
    if ent.jev_model:
        argv += ["--jev-model", ent.jev_model]
    if seed is not None:
        argv += ["--seed", str(seed)]
    if cost_cap_usd is not None:
        argv += ["--max-cost-usd", str(cost_cap_usd)]
    if summary_path is not None:
        argv += ["--summary", str(summary_path)]
    return argv + list(ent.args)


@dataclass
class AgentProcs:
    """The agent processes of one match."""

    procs: dict[int, subprocess.Popen] = field(default_factory=dict)
    logs: list = field(default_factory=list)

    def launch(
        self,
        seat: int,
        argv: list[str],
        log_path: Path,
        cost_cap_usd: float | None,
        cwd: Path | None = None,
    ) -> None:
        env = dict(os.environ)
        if cost_cap_usd is not None:
            env[COST_CAP_ENV] = str(cost_cap_usd)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "wb")  # noqa: SIM115 - closed in stop()
        self.logs.append(log)
        self.procs[seat] = subprocess.Popen(
            argv, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=cwd
        )

    def crashed(self) -> dict[int, int]:
        """Seats whose agent already exited non-zero."""
        return {
            s: p.returncode
            for s, p in self.procs.items()
            if p.poll() is not None and p.returncode != 0
        }

    def stop(self, grace_s: float = 10.0) -> dict[int, int | None]:
        codes: dict[int, int | None] = {}
        for seat, p in self.procs.items():
            try:
                codes[seat] = p.wait(timeout=grace_s)
            except subprocess.TimeoutExpired:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                codes[seat] = None
        for log in self.logs:
            log.close()
        return codes
