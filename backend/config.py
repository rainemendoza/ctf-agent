"""Pydantic Settings — credentials from .env file + environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # CTFd
    ctfd_url: str = "http://localhost:8000"
    ctfd_user: str = "admin"
    ctfd_pass: str = "admin"
    ctfd_token: str = ""

    # API Keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""

    # Provider-specific (optional, for Bedrock/Azure/Zen fallback)
    aws_region: str = "us-east-1"
    aws_bearer_token: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    opencode_zen_api_key: str = ""

    # Infra
    sandbox_image: str = "ctf-sandbox"
    max_concurrent_challenges: int = 10
    max_attempts_per_challenge: int = 3
    container_memory_limit: str = "16g"

    # Strategy
    strategy_mode: str = "balanced"  # easy_first | points_first | balanced
    max_challenge_wall_time_s: int = 1800
    max_no_progress_s: int = 600
    max_challenge_cost_usd: float = 5.0
    max_total_cost_usd: float = 100.0
    max_solver_bumps: int = 3
    max_wrong_submissions_per_challenge: int = 5
    retry_deferred_after_s: int = 1800

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}
