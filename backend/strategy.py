"""Deterministic challenge prioritization and stop-policy helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


EASY_KEYWORDS = (
    "sanity",
    "warmup",
    "warm-up",
    "baby",
    "intro",
    "getting started",
    "starter",
    "easy",
)

HARD_KEYWORDS = (
    "hard",
    "boss",
    "final",
    "advanced",
    "expert",
    "challenge",
)

CATEGORY_WEIGHTS: dict[str, float] = {
    "web": 24.0,
    "misc": 20.0,
    "forensics": 16.0,
    "osint": 14.0,
    "ppc": 10.0,
    "crypto": 6.0,
    "reverse": -4.0,
    "reversing": -4.0,
    "re": -4.0,
    "pwn": -8.0,
    "binary": -8.0,
    "mobile": -2.0,
    "hardware": -2.0,
}


@dataclass(slots=True)
class ChallengeState:
    """Runtime state for a challenge in the coordinator queue."""

    status: str = "queued"
    attempt_count: int = 0
    started_at: float | None = None
    last_progress_at: float | None = None
    cost_usd: float = 0.0
    wrong_submissions: int = 0
    priority_score: float = 0.0
    priority_reason: str = ""
    bump_count: int = 0
    deferred_until: float | None = None


@dataclass(slots=True)
class PrioritizedChallenge:
    """Ranked challenge entry produced by the strategy engine."""

    name: str
    score: float
    reason: str
    category: str = ""
    value: int = 0
    solves: int = 0
    status: str = "queued"
    model_specs: list[str] = field(default_factory=list)


def _now_seconds(now: float | None = None) -> float:
    return now if now is not None else datetime.now(timezone.utc).timestamp()


def _text_for_scoring(challenge: Mapping[str, Any]) -> str:
    parts = [
        str(challenge.get("name", "")),
        str(challenge.get("category", "")),
        str(challenge.get("description", "")),
        str(challenge.get("connection_info", "")),
    ]
    tags = challenge.get("tags") or []
    parts.extend(str(tag) for tag in tags)
    return " ".join(parts).lower()


def _keyword_bonus(text: str, keyword_list: tuple[str, ...], bonus: float) -> float:
    return bonus if any(keyword in text for keyword in keyword_list) else 0.0


def _parse_release_age_seconds(challenge: Mapping[str, Any], now: float) -> float | None:
    for key in ("released_at", "created", "date", "release_date"):
        raw = challenge.get(key)
        if raw in (None, ""):
            continue
        if isinstance(raw, (int, float)):
            return max(0.0, now - float(raw))
        if isinstance(raw, str):
            normalized = raw.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(normalized)
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, now - dt.timestamp())
    return None


def _mode_weights(strategy_mode: str) -> tuple[float, float, float]:
    if strategy_mode == "points_first":
        return 2.5, 0.18, 10.0
    if strategy_mode == "easy_first":
        return 3.5, -0.20, 16.0
    return 3.0, -0.08, 12.0


def score_challenge(
    challenge: Mapping[str, Any],
    state: ChallengeState | None = None,
    strategy_mode: str = "balanced",
    now: float | None = None,
) -> PrioritizedChallenge:
    """Assign a deterministic priority score and a short human-readable reason."""
    now_s = _now_seconds(now)
    solve_weight, value_weight, keyword_weight = _mode_weights(strategy_mode)

    name = str(challenge.get("name", "?"))
    category = str(challenge.get("category", ""))
    value = int(challenge.get("value", 0) or 0)
    solves = int(challenge.get("solves", 0) or 0)

    text = _text_for_scoring(challenge)
    category_key = category.strip().lower()
    category_bonus = CATEGORY_WEIGHTS.get(category_key, 0.0)
    easy_bonus = _keyword_bonus(text, EASY_KEYWORDS, keyword_weight)
    hard_penalty = _keyword_bonus(text, HARD_KEYWORDS, -keyword_weight * 0.5)
    description_bonus = 6.0 if str(challenge.get("description", "")).strip() else -3.0
    conn_bonus = 5.0 if str(challenge.get("connection_info", "")).strip() else 0.0
    distfiles_bonus = 4.0 if challenge.get("files") else 0.0

    solve_bonus = min(solves, 100) * solve_weight
    if strategy_mode == "points_first":
        value_term = value * value_weight
    else:
        value_term = -min(value, 600) * abs(value_weight)

    age_bonus = 0.0
    age_seconds = _parse_release_age_seconds(challenge, now_s)
    if age_seconds is not None:
        if age_seconds < 3600:
            age_bonus = 8.0
        elif age_seconds < 86400:
            age_bonus = 4.0
        elif age_seconds > 7 * 86400:
            age_bonus = -2.0

    failure_penalty = 0.0
    deferred_penalty = 0.0
    bump_penalty = 0.0
    wrong_submission_penalty = 0.0
    if state:
        failure_penalty = min(state.attempt_count, 10) * 4.0
        wrong_submission_penalty = min(state.wrong_submissions, 10) * 6.0
        bump_penalty = min(state.bump_count, 10) * 3.0
        if state.status in {"deferred", "exhausted"}:
            deferred_penalty = 20.0
        if state.deferred_until and state.deferred_until > now_s:
            deferred_penalty += 100.0

    score = (
        solve_bonus
        + value_term
        + category_bonus
        + easy_bonus
        + hard_penalty
        + description_bonus
        + conn_bonus
        + distfiles_bonus
        + age_bonus
        - failure_penalty
        - wrong_submission_penalty
        - bump_penalty
        - deferred_penalty
    )

    reasons: list[str] = [f"solves={solves}", f"value={value}"]
    if category_bonus:
        reasons.append(f"category={category_bonus:+.0f}")
    if easy_bonus:
        reasons.append("keyword=easy")
    if hard_penalty:
        reasons.append("keyword=hard")
    if conn_bonus:
        reasons.append("connection_info")
    if distfiles_bonus:
        reasons.append("distfiles")
    if age_bonus:
        reasons.append(f"age={age_bonus:+.0f}")
    if failure_penalty:
        reasons.append(f"attempts=-{failure_penalty:.0f}")
    if wrong_submission_penalty:
        reasons.append(f"wrong=-{wrong_submission_penalty:.0f}")
    if bump_penalty:
        reasons.append(f"bumps=-{bump_penalty:.0f}")
    if deferred_penalty:
        reasons.append(f"deferred=-{deferred_penalty:.0f}")

    if state:
        state.priority_score = score
        state.priority_reason = ", ".join(reasons)

    return PrioritizedChallenge(
        name=name,
        score=score,
        reason=", ".join(reasons),
        category=category,
        value=value,
        solves=solves,
        status=state.status if state else "queued",
    )


def rank_challenges(
    challenges: list[Mapping[str, Any]],
    states: dict[str, ChallengeState],
    strategy_mode: str = "balanced",
    now: float | None = None,
) -> list[PrioritizedChallenge]:
    ranked: list[PrioritizedChallenge] = []
    for challenge in challenges:
        name = str(challenge.get("name", ""))
        state = states.get(name)
        ranked.append(score_challenge(challenge, state=state, strategy_mode=strategy_mode, now=now))
    ranked.sort(key=lambda item: (-item.score, item.name.lower()))
    return ranked


def select_model_specs(strategy_mode: str, score: float, all_models: list[str]) -> list[str]:
    """Choose a model tier for the current challenge."""
    if not all_models:
        return []

    cheap = [m for m in all_models if "mini" in m or m.endswith("/low") or m.endswith("/medium")]
    if not cheap:
        cheap = all_models[:1]

    if strategy_mode == "points_first":
        return list(all_models)
    if strategy_mode == "easy_first":
        return cheap[:1]
    if score >= 80:
        return list(all_models)
    if score >= 45:
        return cheap[:2] if len(cheap) > 1 else cheap[:1]
    return cheap[:1]


def should_stop_challenge(state: ChallengeState, settings: Any, now: float | None = None) -> tuple[bool, str]:
    """Evaluate configured stop/defer rules for a challenge."""
    now_s = _now_seconds(now)

    if state.status == "solved":
        return True, "solved"

    max_wall = getattr(settings, "max_challenge_wall_time_s", 1800)
    max_no_progress = getattr(settings, "max_no_progress_s", 600)
    max_cost = getattr(settings, "max_challenge_cost_usd", 5.0)
    max_solver_bumps = getattr(settings, "max_solver_bumps", 3)
    max_wrong = getattr(settings, "max_wrong_submissions_per_challenge", 5)

    if state.started_at is not None and now_s - state.started_at >= max_wall:
        return True, f"wall_time>{max_wall}s"

    if state.last_progress_at is not None and now_s - state.last_progress_at >= max_no_progress:
        return True, f"no_progress>{max_no_progress}s"

    if state.cost_usd >= max_cost:
        return True, f"cost>${max_cost:.2f}"

    if state.bump_count >= max_solver_bumps:
        return True, f"bumps>={max_solver_bumps}"

    if state.wrong_submissions >= max_wrong:
        return True, f"wrong_submissions>={max_wrong}"

    return False, ""


def build_budget_line(state: ChallengeState, now: float | None = None) -> str:
    """Render a compact status line for operator visibility."""
    now_s = _now_seconds(now)
    age = "n/a"
    if state.started_at is not None:
        age = f"{now_s - state.started_at:.0f}s"
    progress = "n/a"
    if state.last_progress_at is not None:
        progress = f"{now_s - state.last_progress_at:.0f}s"
    return (
        f"{state.status} | attempts={state.attempt_count} | bumps={state.bump_count} | "
        f"wrong={state.wrong_submissions} | cost=${state.cost_usd:.2f} | age={age} | "
        f"last_progress={progress} | priority={state.priority_score:.1f}"
    )