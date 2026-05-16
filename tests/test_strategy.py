from __future__ import annotations

from types import SimpleNamespace

from backend.strategy import ChallengeState, rank_challenges, select_model_specs, should_stop_challenge


def test_rank_challenges_prefers_easy_web_with_keywords() -> None:
    challenges = [
        {
            "name": "Hard Reverse",
            "category": "reverse",
            "value": 500,
            "solves": 2,
            "description": "A hard reversing challenge.",
        },
        {
            "name": "Warmup Web",
            "category": "web",
            "value": 100,
            "solves": 40,
            "description": "Warmup baby web challenge.",
            "connection_info": "https://example.ctf.local",
            "files": ["dist.zip"],
        },
    ]

    ranked = rank_challenges(challenges, {}, strategy_mode="balanced")

    assert [item.name for item in ranked] == ["Warmup Web", "Hard Reverse"]
    assert "keyword=easy" in ranked[0].reason
    assert "connection_info" in ranked[0].reason


def test_points_first_raises_high_value_score() -> None:
    low = {
        "name": "Low Value",
        "category": "misc",
        "value": 100,
        "solves": 5,
        "description": "A short challenge.",
    }
    high = {
        "name": "High Value",
        "category": "misc",
        "value": 500,
        "solves": 5,
        "description": "A short challenge.",
    }

    balanced = rank_challenges([low, high], {}, strategy_mode="balanced")
    points_first = rank_challenges([low, high], {}, strategy_mode="points_first")

    assert balanced[0].score != points_first[0].score
    assert points_first[0].name == "High Value"


def test_should_stop_challenge_enforces_wall_time_and_wrong_submissions() -> None:
    settings = SimpleNamespace(
        max_challenge_wall_time_s=1800,
        max_no_progress_s=600,
        max_challenge_cost_usd=5.0,
        max_solver_bumps=3,
        max_wrong_submissions_per_challenge=5,
    )

    wall_clock = ChallengeState(status="running", started_at=0.0, last_progress_at=100.0, cost_usd=1.0)
    stop, reason = should_stop_challenge(wall_clock, settings, now=1900.0)
    assert stop is True
    assert reason == "wall_time>1800s"

    wrong = ChallengeState(status="running", started_at=1000.0, last_progress_at=1800.0, wrong_submissions=5)
    stop, reason = should_stop_challenge(wrong, settings, now=1900.0)
    assert stop is True
    assert reason == "wrong_submissions>=5"


def test_select_model_specs_uses_tiering() -> None:
    models = ["codex/gpt-5.4-mini", "codex/gpt-5.3-codex", "claude-sdk/claude-opus-4-6/medium"]

    assert select_model_specs("easy_first", 20.0, models) == ["codex/gpt-5.4-mini"]
    assert select_model_specs("points_first", 20.0, models) == models
    assert select_model_specs("balanced", 90.0, models) == models
