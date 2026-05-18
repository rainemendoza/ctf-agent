"""Shared coordinator event loop — used by both Claude SDK and Codex coordinators."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from backend.config import Settings
from backend.cost_tracker import CostTracker
from backend.ctfd import CTFdClient
from backend.deps import CoordinatorDeps
from backend.htb import HTBClient
from backend.models import DEFAULT_MODELS
from backend.platform import PlatformClient
from backend.poller import PlatformPoller
from backend.prompts import ChallengeMeta
from backend.strategy import (
    ChallengeState,
    rank_challenges,
    select_model_specs,
    should_stop_challenge,
)

logger = logging.getLogger(__name__)

# Callable type for a coordinator turn: (message) -> None
TurnFn = Callable[[str], Coroutine[Any, Any, None]]


def _build_platform_client(settings: Settings) -> PlatformClient:
    """Pick the platform backend based on settings.platform."""
    platform = (getattr(settings, "platform", "ctfd") or "ctfd").lower()
    if platform == "htb":
        event_id: int | str | None = None
        raw_event = getattr(settings, "htb_event_id", "") or ""
        if raw_event:
            event_id = int(raw_event) if str(raw_event).isdigit() else raw_event
        return HTBClient(
            token=settings.htb_token,
            mcp_url=settings.htb_mcp_url,
            event_id=event_id,
        )
    if platform == "ctfd":
        return CTFdClient(
            base_url=settings.ctfd_url,
            token=settings.ctfd_token,
            username=settings.ctfd_user,
            password=settings.ctfd_pass,
        )
    raise ValueError(f"Unknown platform: {platform!r} (expected 'ctfd' or 'htb')")


def build_deps(
    settings: Settings,
    model_specs: list[str] | None = None,
    challenges_root: str = "challenges",
    no_submit: bool = False,
    challenge_dirs: dict[str, str] | None = None,
    challenge_metas: dict[str, ChallengeMeta] | None = None,
) -> tuple[PlatformClient, CostTracker, CoordinatorDeps]:
    """Create platform client, cost tracker, and coordinator deps."""
    ctfd = _build_platform_client(settings)
    cost_tracker = CostTracker()
    specs = model_specs or list(DEFAULT_MODELS)
    Path(challenges_root).mkdir(parents=True, exist_ok=True)

    deps = CoordinatorDeps(
        ctfd=ctfd,
        cost_tracker=cost_tracker,
        settings=settings,
        model_specs=specs,
        challenges_root=challenges_root,
        no_submit=no_submit,
        max_concurrent_challenges=getattr(settings, "max_concurrent_challenges", 10),
        challenge_dirs=challenge_dirs or {},
        challenge_metas=challenge_metas or {},
    )

    # Pre-load already-pulled challenges
    for d in Path(challenges_root).iterdir():
        meta_path = d / "metadata.yml"
        if meta_path.exists():
            meta = ChallengeMeta.from_yaml(meta_path)
            if meta.name not in deps.challenge_dirs:
                deps.challenge_dirs[meta.name] = str(d)
                deps.challenge_metas[meta.name] = meta

    return ctfd, cost_tracker, deps


async def run_event_loop(
    deps: CoordinatorDeps,
    ctfd: PlatformClient,
    cost_tracker: CostTracker,
    turn_fn: TurnFn,
    status_interval: int = 60,
) -> dict[str, Any]:
    """Run the shared coordinator event loop.

    Args:
        deps: Coordinator dependencies (shared state).
        ctfd: Platform client (for poller).
        cost_tracker: Cost tracker.
        turn_fn: Async function that sends a message to the coordinator LLM.
        status_interval: Seconds between status updates.
    """
    poller = PlatformPoller(platform=ctfd, interval_s=5.0)
    await poller.start()

    # Start operator message HTTP endpoint
    msg_server = await _start_msg_server(deps.operator_inbox, deps.msg_port)

    logger.info(
        "Coordinator starting: %d models, %d challenges, %d solved",
        len(deps.model_specs),
        len(poller.known_challenges),
        len(poller.known_solved),
    )

    unsolved = poller.known_challenges - poller.known_solved
    initial_msg = (
        f"CTF is LIVE. {len(poller.known_challenges)} challenges, "
        f"{len(poller.known_solved)} solved.\n"
        f"Unsolved: {sorted(unsolved) if unsolved else 'NONE'}\n"
        f"Strategy mode: {getattr(deps.settings, 'strategy_mode', 'balanced')}\n"
        "Fetch challenges and use the strategy queue to spawn the highest-priority unsolved swarms."
    )

    try:
        await turn_fn(initial_msg)

        # Auto-spawn swarms for unsolved challenges if coordinator LLM didn't
        await _backfill_with_strategy(deps, poller)

        last_status = asyncio.get_event_loop().time()

        while True:
            events = []
            evt = await poller.get_event(timeout=5.0)
            if evt:
                events.append(evt)
            events.extend(poller.drain_events())

            # Auto-kill swarms for solved challenges
            for evt in events:
                if evt.kind == "challenge_solved" and evt.challenge_name in deps.swarms:
                    swarm = deps.swarms[evt.challenge_name]
                    if not swarm.cancel_event.is_set():
                        swarm.kill()
                        logger.info("Auto-killed swarm for: %s", evt.challenge_name)
                    state = deps.challenge_states.get(evt.challenge_name)
                    if state:
                        state.status = "solved"

            parts: list[str] = []
            for evt in events:
                if evt.kind == "new_challenge":
                    parts.append(f"NEW CHALLENGE: '{evt.challenge_name}' appeared. Spawn a swarm.")
                    # Refresh the ranked queue and backfill if there is capacity.
                    await _backfill_with_strategy(deps, poller)
                elif evt.kind == "challenge_solved":
                    parts.append(f"SOLVED: '{evt.challenge_name}' — swarm auto-killed.")

            # Detect finished swarms
            for name, task in list(deps.swarm_tasks.items()):
                if task.done():
                    parts.append(f"SOLVER FINISHED: Swarm for '{name}' completed. Check results or retry.")
                    deps.swarm_tasks.pop(name, None)

            # Sync runtime metrics into coordinator state and enforce stop rules.
            if deps.swarms:
                for name, swarm in deps.swarms.items():
                    state = deps.challenge_states.setdefault(name, ChallengeState())
                    status = swarm.get_status()
                    state.cost_usd = float(status.get("cost_usd", state.cost_usd))
                    state.wrong_submissions = int(status.get("wrong_submissions", state.wrong_submissions))
                    state.bump_count = int(status.get("bump_count", state.bump_count))
                    state.started_at = status.get("started_at", state.started_at)
                    state.last_progress_at = status.get("last_progress_at", state.last_progress_at)
                    state.status = "solved" if status.get("winner") else ("running" if not status.get("cancelled") else "deferred")

                    should_stop, reason = should_stop_challenge(state, deps.settings, now=asyncio.get_event_loop().time())
                    if should_stop and state.status == "running":
                        swarm.kill()
                        state.status = "deferred"
                        state.deferred_until = asyncio.get_event_loop().time() + getattr(deps.settings, "retry_deferred_after_s", 1800)
                        logger.info("Stopping %s: %s", name, reason)

            if deps.cost_tracker.total_cost_usd >= getattr(deps.settings, "max_total_cost_usd", 100.0):
                logger.warning(
                    "Global budget reached: $%.2f >= $%.2f",
                    deps.cost_tracker.total_cost_usd,
                    getattr(deps.settings, "max_total_cost_usd", 100.0),
                )
                break

            await _backfill_with_strategy(deps, poller)

            # Drain solver-to-coordinator messages
            while True:
                try:
                    solver_msg = deps.coordinator_inbox.get_nowait()
                    parts.append(f"SOLVER MESSAGE: {solver_msg}")
                except asyncio.QueueEmpty:
                    break

            # Drain operator messages
            while True:
                try:
                    op_msg = deps.operator_inbox.get_nowait()
                    parts.append(f"OPERATOR MESSAGE: {op_msg}")
                    logger.info("Operator message: %s", op_msg[:200])
                except asyncio.QueueEmpty:
                    break

            # Periodic status update — only when there are active swarms or other events
            now = asyncio.get_event_loop().time()
            if now - last_status >= status_interval:
                last_status = now
                active = [n for n, t in deps.swarm_tasks.items() if not t.done()]
                solved_set = poller.known_solved
                unsolved_set = poller.known_challenges - solved_set
                status_line = (
                    f"STATUS: {len(solved_set)} solved, {len(unsolved_set)} unsolved, "
                    f"{len(active)} active swarms. Cost: ${cost_tracker.total_cost_usd:.2f}"
                )
                # Only send to coordinator if there's something happening
                if active or parts:
                    parts.append(status_line)
                else:
                    logger.info(f"Event -> coordinator: {status_line}")

            if parts:
                msg = "\n\n".join(parts)
                logger.info("Event -> coordinator: %s", msg[:200])
                await turn_fn(msg)

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Coordinator shutting down...")
    except Exception as e:
        logger.error("Coordinator fatal: %s", e, exc_info=True)
    finally:
        if msg_server:
            msg_server.close()
            await msg_server.wait_closed()
        await poller.stop()
        for swarm in deps.swarms.values():
            swarm.kill()
        for task in deps.swarm_tasks.values():
            task.cancel()
        if deps.swarm_tasks:
            await asyncio.gather(*deps.swarm_tasks.values(), return_exceptions=True)
        cost_tracker.log_summary()
        try:
            await ctfd.close()
        except Exception:
            pass

    return {
        "results": deps.results,
        "total_cost_usd": cost_tracker.total_cost_usd,
        "total_tokens": cost_tracker.total_tokens,
    }


async def _auto_spawn_one(
    deps: CoordinatorDeps,
    challenge_name: str,
    model_specs: list[str] | None = None,
) -> None:
    """Auto-spawn a swarm for a single challenge if not already running."""
    if challenge_name in deps.swarms:
        return
    active = sum(1 for t in deps.swarm_tasks.values() if not t.done())
    if active >= deps.max_concurrent_challenges:
        return
    try:
        from backend.agents.coordinator_core import do_spawn_swarm
        result = await do_spawn_swarm(deps, challenge_name, model_specs=model_specs)
        logger.info(f"Auto-spawn {challenge_name}: {result[:100]}")
    except Exception as e:
        logger.warning(f"Auto-spawn failed for {challenge_name}: {e}")


async def _backfill_with_strategy(deps: CoordinatorDeps, poller) -> None:
    """Rank unsolved challenges and backfill available capacity from the top of the queue."""
    active = sum(1 for t in deps.swarm_tasks.values() if not t.done())
    capacity = max(0, deps.max_concurrent_challenges - active)
    if capacity <= 0:
        return

    try:
        challenges = await deps.ctfd.fetch_all_challenges()
    except Exception as e:
        logger.warning("Strategy backfill skipped: could not fetch challenges (%s)", e)
        return

    solved = set(poller.known_solved)
    unsolved = [ch for ch in challenges if ch.get("name") not in solved]
    try:
        ranked = rank_challenges(unsolved, deps.challenge_states, getattr(deps.settings, "strategy_mode", "balanced"))
    except Exception as e:
        logger.warning("Strategy backfill skipped: could not rank challenges (%s)", e)
        return
    deps.challenge_queue = ranked

    for candidate in ranked:
        if capacity <= 0:
            break
        state = deps.challenge_states.get(candidate.name)
        if state and state.deferred_until and state.deferred_until > asyncio.get_event_loop().time():
            continue
        if candidate.name in deps.swarms:
            continue
        candidate.model_specs = select_model_specs(getattr(deps.settings, "strategy_mode", "balanced"), candidate.score, deps.model_specs)
        await _auto_spawn_one(deps, candidate.name, model_specs=candidate.model_specs)
        capacity -= 1


async def _start_msg_server(inbox: asyncio.Queue, port: int = 0) -> asyncio.Server | None:
    """Start a tiny HTTP server that accepts operator messages via POST."""

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            # Read HTTP request
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            headers: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if line in (b"\r\n", b"\n", b""):
                    break
                if b":" in line:
                    k, v = line.decode().split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            method = request_line.decode().split()[0] if request_line else ""
            content_length = int(headers.get("content-length", 0))

            if method == "POST" and content_length > 0:
                body = await asyncio.wait_for(reader.read(content_length), timeout=5)
                try:
                    data = json.loads(body)
                    message = data.get("message", body.decode())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    message = body.decode("utf-8", errors="replace")

                inbox.put_nowait(message)
                resp = json.dumps({"ok": True, "queued": message[:200]})
                writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(resp)}\r\n\r\n{resp}".encode())
            else:
                resp = json.dumps({"error": "POST with JSON body required", "usage": "POST {\"message\": \"...\"}"})
                writer.write(f"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: {len(resp)}\r\n\r\n{resp}".encode())

            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    try:
        server = await asyncio.start_server(_handle, "127.0.0.1", port)
        actual_port = server.sockets[0].getsockname()[1]
        logger.info(f"Operator message endpoint listening on http://127.0.0.1:{actual_port}")
        return server
    except OSError as e:
        logger.warning(f"Could not start operator message endpoint: {e}")
        return None
