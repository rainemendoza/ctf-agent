# CTF Agent Change Summary

This report explains the major code changes that were added to the CTF agent, why they were made, and why they matter.

## What changed at a high level

The project moved from a mostly direct spawn-and-run coordinator to a strategy-driven system with:

- ranked challenge selection
- per-challenge runtime state
- explicit stop and defer rules
- better Codex backend integration
- safer shutdown behavior
- coverage tests for the new strategy layer

That shift matters because the agent is not just trying to run code anymore. It is now trying to manage limited time, cost, and concurrency while choosing the best challenge to attack next.

## 1. Strategy engine

File: [backend/strategy.py](../backend/strategy.py)

Purpose:
- Added deterministic scoring for challenges based on category, solve count, value, keywords, age, and current runtime state.
- Added helpers for choosing which models to assign to a challenge.
- Added stop/defer policy checks based on wall time, lack of progress, cost, bumps, and wrong submissions.

Why it matters:
- This is the core decision layer that replaces a simple "spawn everything" approach.
- It helps the coordinator spend effort on high-value or high-confidence challenges first.
- It also prevents the system from wasting time and money on stuck swarms.

## 2. Coordinator state tracking

Files: [backend/deps.py](../backend/deps.py), [backend/agents/coordinator_core.py](../backend/agents/coordinator_core.py), [backend/agents/coordinator_loop.py](../backend/agents/coordinator_loop.py)

Purpose:
- Added persistent coordinator state for each challenge.
- Added a ranked challenge queue.
- Added new coordinator tools such as strategy planning, budget inspection, challenge promotion, deferral, and strategy mode switching.
- Changed the coordinator loop so it can backfill the highest-priority unsolved challenges instead of only spawning in a fixed order.

Why it matters:
- The coordinator can now reason about the competition at a higher level instead of acting as a thin dispatcher.
- The new queue and state tracking make the agent more adaptive when new challenges appear or when a swarm is clearly stuck.
- This is the main architectural upgrade that turns the system into a real competition controller.

## 3. Swarm runtime tracking

File: [backend/agents/swarm.py](../backend/agents/swarm.py)

Purpose:
- Added timestamps for swarm start and last progress.
- Counted bumps and wrong submissions.
- Exposed runtime status such as cost and progress through `get_status()`.

Why it matters:
- The strategy layer needs live metrics to decide when to stop, defer, or reprioritize a challenge.
- Without these fields, the coordinator would not know whether a swarm is making progress or just consuming budget.

## 4. Codex solver request fix

File: [backend/agents/codex_solver.py](../backend/agents/codex_solver.py)

Purpose:
- Removed the unsupported `serviceTier: flex` field from the Codex `thread/start` request.

Why it matters:
- That field caused Codex to reject the request with an API error.
- Removing it allows solver threads to start normally and begin attacking the challenge.

## 5. Codex coordinator shutdown hardening

File: [backend/agents/codex_coordinator.py](../backend/agents/codex_coordinator.py)

Purpose:
- Made shutdown tolerant of a subprocess that has already exited.

Why it matters:
- Ctrl+C cleanup is now safer and less noisy.
- The coordinator no longer throws avoidable `ProcessLookupError` exceptions during shutdown.

## 6. Sandbox startup context

File: [backend/sandbox.py](../backend/sandbox.py)

Purpose:
- Verified that the sandbox expects a working Docker environment and standard Docker socket availability.

Why it matters:
- The earlier runtime failure was environment-related, not a logic bug in the sandbox itself.
- Once Docker was available, the agent could launch containers and run solvers as intended.

## 7. Tests

File: [tests/test_strategy.py](../tests/test_strategy.py)

Purpose:
- Added tests for ranking behavior, model selection, and stop rules.

Why it matters:
- These tests protect the new strategy behavior from regressions.
- They make sure the more important behavior changes stay stable over time.

## Overall importance

The most important change is the move from a fixed, reactive coordinator to a strategy-based controller.

Before these changes, the system mainly tried to run all swarms and let them continue indefinitely.
After these changes, it can:

- choose the best challenge to attack next
- vary model usage based on challenge difficulty
- stop wasting budget on stuck work
- expose its reasoning and runtime state through coordinator tools
- recover cleanly from backend and shutdown issues

That is the difference between a script that launches solvers and an agent that can actually manage a CTF competition.