# tempo-eval

Evaluation for [TEMPO](https://tempoarena.org), the benchmark where the world does not wait for
the agent to think. It runs matches between bots, LLM agents, Jev agents, hybrids and humans on
a [tempo](https://github.com/tempoarena/tempo) server, then reports **capability against
latency and cost**, never a single score.

```bash
uv sync --extra tempo          # pinned tempo-arena + tempo-baselines (builds Rust)
uv run tempo-eval run suites/snake-smoke.yaml
uv run tempo-eval publish --site ../tempo-site
```

## What it measures

| | how |
|---|---|
| skill | OpenSkill (Plackett–Luce) rating ± sd per game; ranked by rating − 3 sd |
| win rate | draws count half; 95% Wilson interval |
| latency | decision latency p50/p95 per entrant, missed-deadline rate |
| cost | $ and tokens per game-minute, from what agents report to the server |
| frontier | Pareto frontier of rating vs p50 latency, and its hypervolume on a log-latency axis (1 ms–10 s) |
| game metrics | the mean of every numeric per-seat stat the game reports (Snake length and kills, Breach K/D, ADR…) |

A match counts only if its replay re-simulates to the same state hash with the pinned engine.

## Suites

A suite (`suites/*.yaml`) fixes the game, config, clock (`charged` for leaderboards), observation
mode, entrants, seat line-ups and seeds. `dev` seeds are public. `heldout` seeds come from a
private file named by `$TEMPO_HELDOUT_SEEDS` and are never committed, so official results are on
maps nobody has seen.

## Human play

```bash
uv run tempo-eval play --game breach --human 1 --vs llm,jev,bot:scripted
```

This starts a live match, launches the AI seats, and prints a
[tempo-play](https://github.com/tempoarena/tempo-play) link for each human seat.

## Pinning

`tempo-eval` depends on one exact tempo commit (the `tempo` extra in `pyproject.toml`). Move it
with `scripts/bump-tempo.sh`. Use a local checkout instead with `scripts/dev-link.sh`.
Secrets (`TYPESAFE_API_KEY`) go in `.env`. See `.env.example`.

Apache-2.0.
