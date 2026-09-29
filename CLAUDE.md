# CLAUDE.md

Guidance for Claude Code (and every coding agent) working in `tempoarena/tempo-eval`.

## Meta rules (override defaults, apply to every task)

1. **Commit as you go, with meaningful messages.** One self-contained logical change per commit,
   staged by explicit path (never `git add -A` / `git add .`).
   [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):
   `<type>(<scope>): <what changed, imperative>` plus a body that says **why** (trade-offs, what
   was measured, what is deliberately left out). Types: `feat fix perf refactor test docs build
   ci chore`. Scopes: `suite runner metrics ratings publish play cli deps`. Bad: `update`, `wip`.
2. **Secrets never enter git.** Keys live in `.env` (gitignored; `.env.example` lists names only).
   The hook `scripts/check-secrets.sh` must stay enabled (`git config core.hooksPath .githooks`);
   CI runs it with `--all`. Held-out seeds are secret too: only `$TEMPO_HELDOUT_SEEDS` points at
   them. `results/` is gitignored because agent logs can hold full prompts; only `publish`
   output (an allow-list of numbers and names) leaves this machine.
3. **Pull before you start, push when green:** `git pull --rebase`, then
   `uv run ruff check . && uv run ruff format --check . && uv run pytest`, then push `main`.
4. **Keep this file under 200 lines.**

## What this is

The evaluation half of TEMPO (tempoarena.org). It depends on a **pinned** tempo and is never
imported by it. It runs suites of matches on a tempo server, verifies replays, computes ratings,
latency/cost statistics and capability-vs-latency frontiers, and publishes `leaderboard.json` for
`tempo-site`. It also runs live human sessions (`play`).

Contract: `../tempo/docs/ARCHITECTURE.md` (§6 server, §8 agent CLI, §10 `result.json` and
`leaderboard.json`) and `../tempo/spec/protocol.md`. If tempo changes those, change this repo in
the same sitting.

## Commands

```bash
uv sync                                   # env without tempo (tests use a fake server)
uv sync --extra tempo                     # + the pinned tempo-arena and tempo-baselines
scripts/dev-link.sh [../tempo]            # use a local tempo checkout instead (then uv run --no-sync)
scripts/bump-tempo.sh [<sha>]             # move the pin (default tempo origin/main)
scripts/build-server.sh                   # tempo-server at the pin -> .tools/ (fallback server)
uv run pytest -q                          # all tests;  uv run pytest tests/test_pipeline.py -k cost
uv run ruff check . && uv run ruff format .

uv run --extra tempo tempo-eval run suites/snake-smoke.yaml [--server http://127.0.0.1:8765] [--dry-run]
uv run tempo-eval publish --site ../tempo-site         # leaderboard suites only; --all for every suite
uv run tempo-eval play --game breach --human 1 --vs llm,jev,bot:scripted
```

Env: `TEMPO_AGENT_CMD` (agent launcher, default `tempo-agent`), `TEMPO_SERVER_BIN` (use a built
`tempo-server` instead of `tempo.serve`), `TEMPO_HELDOUT_SEEDS`, `TEMPO_PLAY_URL`,
`TEMPO_PLAY_PREVIEW_URL`; agents read `TYPESAFE_API_KEY` and AWS credentials themselves.

## Layout

```
src/tempo_eval/
  suite.py     Suite/Entrant models, inline entrant specs, seed sets, seat rotations
  server.py    HTTP client for tempo-server; start one in a child process
  agents.py    launch/stop tempo-agent processes, per-seat logs
  runner.py    plan jobs, run in parallel, retries, cost caps, store results
  verify.py    replay re-simulation through the pinned tempo._native
  results.py   load finished matches -> Samples (rejects unverified/failed replays)
  ratings.py   OpenSkill Plackett-Luce per game, team ranks from outcome.scores
  metrics.py   per-entry stats (win rate CI, latency, cost/min, game metrics)
  pareto.py    frontier + hypervolume on log-latency axis
  publish.py   leaderboard.json (§10 shape, allow-listed) + REPORT.md
  play.py      live human sessions and join URLs
suites/        experiment definitions (dev seeds public; heldout via env only)
tests/         fakeserver.py stands in for tempo-server; fake_agent.py for tempo-agent
published/     committed outputs of `publish` (small, safe)
```

## Rules

- A result counts only if its replay verifies (or, without `--require-verified`, cannot be
  checked). Never "fix" a failing verification by skipping it; report it.
- Retry a failed match with the **same** seed. Never substitute seeds: it biases results.
- Leaderboard suites use `clock: charged` (G8). Model agents must declare model calls as
  `think`s (tempo protocol) or charged mode lets the sim race past their thinking.
- Decision latency (frontier axis) prefers the engine's charged think (`think_ticks`/`thinks`),
  then the agent's reported model-call latency, then answer latency.
- Two-team lineups are written as team blocks; the runner interleaves them to tempo's seat rule
  (seat i -> team i % 2). `play` seats literally and prints each seat's team.
- A match where any agent process exits non-zero is `agent_error`, never a result.
- Only **approved** games are ranked (tempo `docs/GAME_LIFECYCLE.md`). The runner records the
  game's `status` in run.json; `publish` drops prototype/unknown games even with `--all`.
  Prototypes get `suites/proto-<game>.yaml` smoke tests (scripted bots, 2 matches).
- Suite `config` keys must exist in the pinned game's `config_defaults` (a test checks it).
- Model suites stay tiny and carry `cost_cap_usd` / `suite_cost_cap_usd`.
- Publishing is allow-list only: add a field to `publish._entry_json` deliberately, never dump
  result objects wholesale.
