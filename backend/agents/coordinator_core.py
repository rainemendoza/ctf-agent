"""Shared coordinator tool logic — called by both Claude SDK and Codex coordinators."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from backend.deps import CoordinatorDeps
from backend.prompts import ChallengeMeta
from backend.solver_base import FLAG_FOUND
from backend.strategy import (
    ChallengeState,
    build_budget_line,
    rank_challenges,
    select_model_specs,
)

logger = logging.getLogger(__name__)


async def do_fetch_challenges(deps: CoordinatorDeps) -> str:
    challenges = await deps.ctfd.fetch_all_challenges()
    solved = await deps.ctfd.fetch_solved_names()
    result = [
        {
            "name": ch.get("name", "?"),
            "category": ch.get("category", "?"),
            "value": ch.get("value", 0),
            "solves": ch.get("solves", 0),
            "status": "SOLVED" if ch.get("name") in solved else "unsolved",
            "description": (ch.get("description") or "")[:200],
        }
        for ch in challenges
    ]
    return json.dumps(result, indent=2)


async def do_get_solve_status(deps: CoordinatorDeps) -> str:
    solved = await deps.ctfd.fetch_solved_names()
    swarm_status = {name: swarm.get_status() for name, swarm in deps.swarms.items()}
    return json.dumps({"solved": sorted(solved), "active_swarms": swarm_status}, indent=2)


def _state_for(deps: CoordinatorDeps, challenge_name: str) -> ChallengeState:
    state = deps.challenge_states.get(challenge_name)
    if state is None:
        state = ChallengeState()
        deps.challenge_states[challenge_name] = state
    return state


def _sync_state_from_swarm(deps: CoordinatorDeps, challenge_name: str) -> ChallengeState:
    state = _state_for(deps, challenge_name)
    swarm = deps.swarms.get(challenge_name)
    if not swarm:
        return state

    status = swarm.get_status()
    state.cost_usd = float(status.get("cost_usd", state.cost_usd))
    state.wrong_submissions = int(status.get("wrong_submissions", state.wrong_submissions))
    state.bump_count = int(status.get("bump_count", state.bump_count))
    state.last_progress_at = status.get("last_progress_at", state.last_progress_at)
    state.started_at = status.get("started_at", state.started_at)

    if status.get("winner"):
        state.status = "solved"
    elif status.get("cancelled"):
        state.status = "deferred"
    else:
        state.status = "running"
    return state


async def do_spawn_swarm(
    deps: CoordinatorDeps,
    challenge_name: str,
    model_specs: list[str] | None = None,
) -> str:
    # Retire ALL finished swarms before checking capacity
    finished = [
        name for name, swarm in deps.swarms.items()
        if swarm.cancel_event.is_set()
        or (name in deps.swarm_tasks and deps.swarm_tasks[name].done())
    ]
    for name in finished:
        del deps.swarms[name]
        deps.swarm_tasks.pop(name, None)

    active_count = len(deps.swarms)
    if active_count >= deps.max_concurrent_challenges:
        return f"At capacity ({active_count}/{deps.max_concurrent_challenges} challenges running). Wait for one to finish."

    if challenge_name in deps.swarms:
        return f"Swarm still running for {challenge_name}"

    state = _state_for(deps, challenge_name)
    state.attempt_count += 1
    state.status = "queued"

    # Auto-pull challenge if needed
    if challenge_name not in deps.challenge_dirs:
        challenges = await deps.ctfd.fetch_all_challenges()
        ch_data = next((c for c in challenges if c.get("name") == challenge_name), None)
        if not ch_data:
            return f"Challenge '{challenge_name}' not found on configured platform"
        output_dir = str(Path(deps.challenges_root))
        ch_dir = await deps.ctfd.pull_challenge(ch_data, output_dir)
        deps.challenge_dirs[challenge_name] = ch_dir
        deps.challenge_metas[challenge_name] = ChallengeMeta.from_yaml(Path(ch_dir) / "metadata.yml")

    from backend.agents.swarm import ChallengeSwarm

    chosen_models = model_specs or select_model_specs(
        getattr(deps.settings, "strategy_mode", "balanced"),
        state.priority_score,
        deps.model_specs,
    )
    swarm = ChallengeSwarm(
        challenge_dir=deps.challenge_dirs[challenge_name],
        meta=deps.challenge_metas[challenge_name],
        ctfd=deps.ctfd,
        cost_tracker=deps.cost_tracker,
        settings=deps.settings,
        model_specs=chosen_models,
        no_submit=deps.no_submit,
        coordinator_inbox=deps.coordinator_inbox,
    )
    deps.swarms[challenge_name] = swarm
    state.status = "running"
    now = time.monotonic()
    state.started_at = now
    state.last_progress_at = now
    state.deferred_until = None
    state.priority_score = state.priority_score or 0.0
    state.priority_reason = state.priority_reason or "spawned"

    async def _run_and_cleanup() -> None:
        result = await swarm.run()
        # Flag already submitted/confirmed by solver's submit_fn — just record the result
        if result and result.status == FLAG_FOUND:
            deps.results[challenge_name] = {
                "flag": result.flag,
                "submit": "DRY RUN" if deps.no_submit else "confirmed by solver",
            }
            _state_for(deps, challenge_name).status = "solved"
        else:
            current = _state_for(deps, challenge_name)
            if current.status != "solved":
                current.status = "deferred"

    task = asyncio.create_task(_run_and_cleanup(), name=f"swarm-{challenge_name}")
    deps.swarm_tasks[challenge_name] = task
    return f"Swarm spawned for {challenge_name} with {len(chosen_models)} models"


async def do_check_swarm_status(deps: CoordinatorDeps, challenge_name: str) -> str:
    swarm = deps.swarms.get(challenge_name)
    if not swarm:
        return f"No swarm running for {challenge_name}"
    return json.dumps(swarm.get_status(), indent=2)


async def do_get_strategy_plan(deps: CoordinatorDeps) -> str:
    try:
        challenges = await deps.ctfd.fetch_all_challenges()
        solved = await deps.ctfd.fetch_solved_names()
        unsolved = [ch for ch in challenges if ch.get("name") not in solved]
        ranked = rank_challenges(unsolved, deps.challenge_states, getattr(deps.settings, "strategy_mode", "balanced"))
        deps.challenge_queue = ranked
        return json.dumps(
            [
                {
                    "name": item.name,
                    "score": round(item.score, 2),
                    "reason": item.reason,
                    "category": item.category,
                    "value": item.value,
                    "solves": item.solves,
                    "status": item.status,
                }
                for item in ranked
            ],
            indent=2,
        )
    except Exception as e:
        logger.warning("get_strategy_plan failed: %s", e)
        return json.dumps({"error": f"get_strategy_plan failed: {e}"}, indent=2)


async def do_get_budget_status(deps: CoordinatorDeps) -> str:
    for name in list(deps.swarms):
        _sync_state_from_swarm(deps, name)
    status = {
        "strategy_mode": getattr(deps.settings, "strategy_mode", "balanced"),
        "total_cost_usd": deps.cost_tracker.total_cost_usd,
        "max_total_cost_usd": getattr(deps.settings, "max_total_cost_usd", 100.0),
        "queue": [
            {
                "name": name,
                "status": state.status,
                "priority_score": state.priority_score,
                "priority_reason": state.priority_reason,
                "budget": build_budget_line(state),
            }
            for name, state in sorted(deps.challenge_states.items())
        ],
    }
    return json.dumps(status, indent=2)


async def do_defer_challenge(deps: CoordinatorDeps, challenge_name: str, reason: str) -> str:
    state = _state_for(deps, challenge_name)
    state.status = "deferred"
    state.priority_reason = reason
    state.deferred_until = asyncio.get_event_loop().time() + getattr(deps.settings, "retry_deferred_after_s", 1800)
    swarm = deps.swarms.get(challenge_name)
    if swarm:
        swarm.kill()
    return f"Deferred {challenge_name}: {reason}"


async def do_promote_challenge(deps: CoordinatorDeps, challenge_name: str, bonus: float = 25.0) -> str:
    state = _state_for(deps, challenge_name)
    state.priority_score += bonus
    state.priority_reason = f"manual promotion +{bonus:.1f}"
    state.status = "queued"
    return f"Promoted {challenge_name} by {bonus:.1f}"


async def do_set_strategy_mode(deps: CoordinatorDeps, strategy_mode: str) -> str:
    strategy_mode = strategy_mode.strip().lower()
    if strategy_mode not in {"easy_first", "points_first", "balanced"}:
        return f"Invalid strategy_mode: {strategy_mode}"
    deps.settings.strategy_mode = strategy_mode
    return f"strategy_mode set to {strategy_mode}"


async def do_submit_flag(deps: CoordinatorDeps, challenge_name: str, flag: str) -> str:
    if deps.no_submit:
        return f'DRY RUN — would submit "{flag.strip()}" for {challenge_name}'
    try:
        result = await deps.ctfd.submit_flag(challenge_name, flag)
        return result.display
    except Exception as e:
        return f"submit_flag error: {e}"


async def do_kill_swarm(deps: CoordinatorDeps, challenge_name: str) -> str:
    swarm = deps.swarms.get(challenge_name)
    if not swarm:
        return f"No swarm running for {challenge_name}"
    swarm.kill()
    _state_for(deps, challenge_name).status = "deferred"
    return f"Swarm for {challenge_name} cancelled"


async def do_bump_agent(deps: CoordinatorDeps, challenge_name: str, model_spec: str, insights: str) -> str:
    swarm = deps.swarms.get(challenge_name)
    if not swarm:
        return f"No swarm running for {challenge_name}"
    solver = swarm.solvers.get(model_spec)
    if not solver:
        return f"No solver for {model_spec} in {challenge_name}"
    solver.bump(insights)
    _state_for(deps, challenge_name).bump_count += 1
    return f"Bumped {model_spec} on {challenge_name}"


async def do_read_solver_trace(deps: CoordinatorDeps, challenge_name: str, model_spec: str, last_n: int = 20) -> str:
    """Read the last N trace events from a solver's JSONL log."""
    swarm = deps.swarms.get(challenge_name)
    if not swarm:
        return f"No swarm for {challenge_name}"
    solver = swarm.solvers.get(model_spec)
    if not solver:
        return f"No solver for {model_spec}"
    trace_path = getattr(solver, "tracer", None)
    if not trace_path:
        return "No tracer on solver"
    path = trace_path.path if hasattr(trace_path, "path") else str(trace_path)
    try:
        lines = Path(path).read_text().strip().split("\n")
        recent = lines[-last_n:]
        summary = []
        for line in recent:
            try:
                d = json.loads(line)
                t = d.get("type", "?")
                if t == "tool_call":
                    args_str = str(d.get("args", ""))[:100]
                    summary.append(f"step {d.get('step','?')} CALL {d.get('tool','?')}: {args_str}")
                elif t == "tool_result":
                    result_str = str(d.get("result", ""))[:100]
                    summary.append(f"step {d.get('step','?')} RESULT {d.get('tool','?')}: {result_str}")
                elif t in ("finish", "error", "bump", "turn_failed"):
                    summary.append(f"** {t}: {json.dumps({k:v for k,v in d.items() if k != 'ts'})}")
                elif t == "usage":
                    summary.append(f"usage: in={d.get('input_tokens',0)} out={d.get('output_tokens',0)} cost=${d.get('cost_usd',0):.4f}")
                else:
                    summary.append(f"{t}: {str(d)[:80]}")
            except Exception:
                summary.append(line[:100])
        return "\n".join(summary)
    except FileNotFoundError:
        return f"Trace file not found: {path}"
    except Exception as e:
        return f"Error reading trace: {e}"


async def do_broadcast(deps: CoordinatorDeps, challenge_name: str, message: str) -> str:
    """Broadcast a message to all solvers working on a challenge."""
    swarm = deps.swarms.get(challenge_name)
    if not swarm:
        return f"No swarm running for {challenge_name}"
    await swarm.message_bus.broadcast(message)
    return f"Broadcast to all solvers on {challenge_name}"
