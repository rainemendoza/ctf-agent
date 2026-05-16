"""Platform abstraction for remote CTF providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


SubmitStatus = Literal["correct", "already_solved", "incorrect", "unknown"]


@dataclass
class SubmitResult:
    status: SubmitStatus
    message: str
    display: str


class PlatformClient(Protocol):
    """Common interface used by coordinators, pollers, and solvers."""

    async def fetch_challenge_stubs(self) -> list[dict[str, Any]]:
        """Fetch lightweight visible challenge records."""
        ...

    async def get_challenge_id(self, name: str) -> int | str:
        """Resolve a challenge name to the remote platform identifier."""
        ...

    async def submit_flag(self, challenge_name: str, flag: str) -> SubmitResult:
        """Submit a candidate flag for a challenge."""
        ...

    async def fetch_all_challenges(self) -> list[dict[str, Any]]:
        """Fetch full challenge records."""
        ...

    async def fetch_solved_names(self) -> set[str]:
        """Fetch challenge names known to be solved."""
        ...

    async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
        """Create or update a local challenge folder from a remote challenge."""
        ...

    async def close(self) -> None:
        """Close platform resources."""
        ...
