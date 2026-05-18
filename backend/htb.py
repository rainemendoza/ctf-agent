"""Hack The Box client — talks to HTB's official MCP server.

Uses the streamable-HTTP MCP transport against `https://mcp.hackthebox.ai/v1/ctf/mcp/`.
Implements the same `PlatformClient` interface as `CTFdClient` so the rest of the
agent (poller, coordinator, swarm, solver tools) is unchanged.

The HTB CTF MCP exposes tools roughly grouped as:
  - Event:        list_ctf_events, get_ctf_details, join_ctf_event
  - Scoring:      get_ctf_scores, get_all_solves, get_team_solves, get_challenge_solves
  - Team:         get_my_teams
  - Challenge:    submit_flag, get_download_link
  - Container:    start_container, stop_container, container_status

Because public docs don't pin down exact parameter names, the client probes
`list_tools()` once and tries a small set of common parameter names per call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.platform import SubmitResult

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "https://mcp.hackthebox.ai/v1/ctf/mcp/"
DEFAULT_HTB_API = "https://www.hackthebox.com"


@dataclass
class HTBClient:
    """Hack The Box platform client backed by the official HTB MCP server."""

    token: str = ""
    mcp_url: str = DEFAULT_MCP_URL
    event_id: int | str | None = None
    api_base: str = DEFAULT_HTB_API

    _stack: AsyncExitStack | None = field(default=None, repr=False)
    _session: Any = field(default=None, repr=False)
    _tools: dict[str, Any] = field(default_factory=dict, repr=False)
    _http: httpx.AsyncClient | None = field(default=None, repr=False)
    _challenge_ids: dict[str, int | str] = field(default_factory=dict, repr=False)
    _challenge_cache: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    # ---------- connection ----------

    async def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        async with self._connect_lock:
            if self._session is not None:
                return self._session
            try:
                from mcp import ClientSession  # type: ignore
                from mcp.client.streamable_http import streamablehttp_client  # type: ignore
            except ImportError as e:
                raise RuntimeError(
                    "The 'mcp' Python package is required for the HTB backend. "
                    "Install it with: uv add mcp  (or pip install mcp)."
                ) from e

            if not self.token:
                raise RuntimeError(
                    "HTB MCP token is required. Generate one from your HTB profile "
                    "(Profile Settings -> MCP Access) and set HTB_TOKEN."
                )

            self._stack = AsyncExitStack()
            headers = {"Authorization": f"Bearer {self.token}"}
            read, write, _ = await self._stack.enter_async_context(
                streamablehttp_client(self.mcp_url, headers=headers)
            )
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools = await session.list_tools()
            self._tools = {t.name: t for t in tools.tools}
            logger.info("HTB MCP connected. Tools: %s", ", ".join(sorted(self._tools)))
            self._session = session
            return session

    async def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=True,
                headers={"Authorization": f"Bearer {self.token}"} if self.token else {},
            )
        return self._http

    async def close(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                pass
            self._stack = None
            self._session = None
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None

    # ---------- MCP plumbing ----------

    def _pick_tool(self, *candidates: str) -> str | None:
        for name in candidates:
            if name in self._tools:
                return name
        # case-insensitive fallback
        lower = {n.lower(): n for n in self._tools}
        for c in candidates:
            if c.lower() in lower:
                return lower[c.lower()]
        return None

    async def _call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        session = await self._ensure_session()
        result = await session.call_tool(tool_name, args)
        return _extract_mcp_payload(result)

    async def _call_first(
        self, candidates: list[str], arg_variants: list[dict[str, Any]]
    ) -> Any:
        """Try multiple tool-name candidates with multiple arg shapes."""
        tool = next((self._pick_tool(c) for c in candidates if self._pick_tool(c)), None)
        if not tool:
            raise RuntimeError(
                f"No matching HTB MCP tool found among {candidates}. "
                f"Available tools: {sorted(self._tools)}"
            )
        last_err: Exception | None = None
        for args in arg_variants:
            try:
                return await self._call_tool(tool, args)
            except Exception as e:  # noqa: BLE001 - try next variant
                last_err = e
                continue
        raise RuntimeError(f"All arg variants failed for {tool}: {last_err}")

    # ---------- PlatformClient methods ----------

    async def fetch_challenge_stubs(self) -> list[dict[str, Any]]:
        await self._ensure_session()
        challenges = await self._fetch_event_challenges()
        normalized = [_normalize_challenge_stub(c) for c in challenges]
        for ch in normalized:
            self._challenge_ids[ch["name"]] = ch["id"]
            self._challenge_cache[ch["name"]] = ch
        return normalized

    async def fetch_all_challenges(self) -> list[dict[str, Any]]:
        # For HTB the per-event details usually return full records already.
        return await self.fetch_challenge_stubs()

    async def fetch_solved_names(self) -> set[str]:
        await self._ensure_session()
        try:
            payload = await self._call_first(
                ["get_team_solves", "get_my_solves", "get_all_solves"],
                self._event_arg_variants(),
            )
        except Exception as e:
            logger.warning("HTB: could not fetch solved names (%s)", e)
            return set()

        names: set[str] = set()
        for item in _iter_records(payload):
            name = (
                _dig(item, "challenge", "name")
                or item.get("challenge_name")
                or item.get("name")
            )
            if name:
                names.add(str(name))
        return names

    async def get_challenge_id(self, name: str) -> int | str:
        if name in self._challenge_ids:
            return self._challenge_ids[name]
        await self.fetch_challenge_stubs()
        if name not in self._challenge_ids:
            raise RuntimeError(f'Challenge "{name}" not found in HTB event')
        return self._challenge_ids[name]

    async def submit_flag(self, challenge_name: str, flag: str) -> SubmitResult:
        await self._ensure_session()
        challenge_id = await self.get_challenge_id(challenge_name)
        arg_variants = []
        for id_key in ("challenge_id", "id"):
            for flag_key in ("flag", "submission"):
                args: dict[str, Any] = {id_key: challenge_id, flag_key: flag}
                if self.event_id is not None:
                    args["ctf_id"] = self.event_id
                    args["event_id"] = self.event_id
                arg_variants.append(args)
        try:
            payload = await self._call_first(
                ["submit_flag", "submit_challenge_flag"], arg_variants
            )
        except Exception as e:
            return SubmitResult("unknown", str(e), f"HTB submit error: {e}")

        return _classify_submit(payload, flag)

    async def pull_challenge(self, challenge: dict[str, Any], output_dir: str) -> str:
        """Materialize an HTB challenge as a local folder with metadata.yml + distfiles."""
        import yaml

        name = challenge.get("name", f"challenge-{challenge.get('id', 'x')}")
        slug = _slugify(name)
        ch_dir = Path(output_dir) / slug
        ch_dir.mkdir(parents=True, exist_ok=True)

        # Optional attachment download via MCP-provided link
        if challenge.get("id") is not None and self._pick_tool("get_download_link"):
            try:
                link_payload = await self._call_first(
                    ["get_download_link"],
                    [{"challenge_id": challenge["id"]}, {"id": challenge["id"]}],
                )
                url = _extract_url(link_payload)
                if url:
                    await self._download_to(ch_dir / "distfiles", url, name)
            except Exception as e:
                logger.warning("HTB: download link failed for %s: %s", name, e)

        meta = {
            "name": name,
            "category": challenge.get("category", ""),
            "description": (challenge.get("description") or "").strip(),
            "value": challenge.get("value", challenge.get("points", 0)),
            "connection_info": challenge.get("connection_info") or "",
            "tags": challenge.get("tags") or [],
            "solves": challenge.get("solves", 0),
            "difficulty": challenge.get("difficulty", ""),
            "platform": "htb",
            "htb_id": challenge.get("id"),
        }
        (ch_dir / "metadata.yml").write_text(
            yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False)
        )
        return str(ch_dir)

    # ---------- HTB container helpers (exposed for solver tools) ----------

    async def start_container(self, challenge_name: str) -> dict[str, Any]:
        challenge_id = await self.get_challenge_id(challenge_name)
        return _as_dict(await self._call_first(
            ["start_container"],
            [{"challenge_id": challenge_id}, {"id": challenge_id}],
        ))

    async def stop_container(self, challenge_name: str) -> dict[str, Any]:
        challenge_id = await self.get_challenge_id(challenge_name)
        return _as_dict(await self._call_first(
            ["stop_container"],
            [{"challenge_id": challenge_id}, {"id": challenge_id}],
        ))

    async def container_status(self, challenge_name: str) -> dict[str, Any]:
        challenge_id = await self.get_challenge_id(challenge_name)
        return _as_dict(await self._call_first(
            ["container_status"],
            [{"challenge_id": challenge_id}, {"id": challenge_id}],
        ))

    # ---------- internals ----------

    def _event_arg_variants(self) -> list[dict[str, Any]]:
        if self.event_id is None:
            return [{}]
        return [
            {"ctf_id": self.event_id},
            {"event_id": self.event_id},
            {"id": self.event_id},
        ]

    async def _fetch_event_challenges(self) -> list[dict[str, Any]]:
        if self.event_id is None:
            raise RuntimeError(
                "HTB event_id is required. Set HTB_EVENT_ID or pass --htb-event-id."
            )
        payload = await self._call_first(
            ["get_ctf_details", "get_ctf_event", "get_event_details"],
            self._event_arg_variants(),
        )
        if isinstance(payload, dict):
            for key in ("challenges", "data", "items"):
                if isinstance(payload.get(key), list):
                    return payload[key]
            # Sometimes data is nested under e.g. {"ctf": {...,"challenges":[...]}}
            for v in payload.values():
                if isinstance(v, dict) and isinstance(v.get("challenges"), list):
                    return v["challenges"]
        if isinstance(payload, list):
            return payload
        return []

    async def _download_to(self, dest_dir: Path, url: str, label: str) -> None:
        client = await self._ensure_http()
        dest_dir.mkdir(parents=True, exist_ok=True)
        fname = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or f"{_slugify(label)}.bin"
        dest = dest_dir / fname
        if dest.exists():
            return
        resp = await client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        logger.info("HTB downloaded %s (%d bytes)", fname, len(resp.content))


# ---------- helpers ----------


def _slugify(name: str) -> str:
    slug = re.sub(r'[<>:"/\\|?*.\x00-\x1f]', "", name.lower().strip())
    slug = re.sub(r"[\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-") or "challenge"


def _extract_mcp_payload(result: Any) -> Any:
    """Pull JSON / text out of an MCP CallToolResult."""
    if result is None:
        return None
    # Newer SDKs expose .structuredContent
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    parts = getattr(result, "content", None) or []
    texts: list[str] = []
    for p in parts:
        text = getattr(p, "text", None)
        if text:
            texts.append(text)
    if not texts:
        return None
    joined = "\n".join(texts).strip()
    try:
        return json.loads(joined)
    except (json.JSONDecodeError, ValueError):
        return joined


def _as_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        return {"message": payload}
    return {"data": payload}


def _iter_records(payload: Any):
    if isinstance(payload, list):
        yield from payload
    elif isinstance(payload, dict):
        for key in ("data", "items", "solves", "results"):
            v = payload.get(key)
            if isinstance(v, list):
                yield from v
                return
        # Fall back to dict-of-records
        for v in payload.values():
            if isinstance(v, dict):
                yield v


def _dig(d: dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _normalize_challenge_stub(raw: dict[str, Any]) -> dict[str, Any]:
    name = (
        raw.get("name")
        or raw.get("title")
        or raw.get("challenge_name")
        or f"challenge-{raw.get('id', 'x')}"
    )
    ch_id = raw.get("id") or raw.get("challenge_id") or raw.get("ID")
    category = raw.get("category") or _dig(raw, "category_name") or raw.get("type") or ""
    desc = raw.get("description") or raw.get("desc") or ""
    points = raw.get("points") or raw.get("value") or 0
    solves = raw.get("solves") or raw.get("solve_count") or 0
    difficulty = raw.get("difficulty") or ""
    tags = raw.get("tags") or []
    # Normalize tags into list of strings
    norm_tags = []
    for t in tags:
        if isinstance(t, dict):
            norm_tags.append(t.get("name") or t.get("value") or "")
        else:
            norm_tags.append(str(t))
    return {
        "id": ch_id,
        "name": str(name),
        "category": str(category),
        "description": str(desc),
        "value": int(points) if isinstance(points, (int, float, str)) and str(points).isdigit() else points or 0,
        "solves": solves,
        "difficulty": difficulty,
        "tags": [t for t in norm_tags if t],
        "type": raw.get("type", ""),
        "connection_info": raw.get("connection_info") or raw.get("docker_ip") or "",
    }


def _extract_url(payload: Any) -> str | None:
    if isinstance(payload, str) and payload.startswith("http"):
        return payload
    if isinstance(payload, dict):
        for key in ("url", "download_url", "link", "href"):
            v = payload.get(key)
            if isinstance(v, str) and v.startswith("http"):
                return v
        data = payload.get("data")
        if isinstance(data, dict):
            return _extract_url(data)
    return None


def _classify_submit(payload: Any, flag: str) -> SubmitResult:
    text = payload if isinstance(payload, str) else json.dumps(payload) if payload else ""
    blob = text.lower()
    if isinstance(payload, dict):
        status = (
            payload.get("status")
            or _dig(payload, "data", "status")
            or payload.get("result")
            or ""
        ).lower()
        message = payload.get("message") or _dig(payload, "data", "message") or ""
    else:
        status = ""
        message = text

    if status == "correct" or "correct" in blob and "incorrect" not in blob:
        return SubmitResult("correct", message, f'CORRECT — "{flag}" accepted. {message}'.strip())
    if "already" in blob or status == "already_solved":
        return SubmitResult(
            "already_solved", message, f'ALREADY SOLVED — "{flag}" accepted. {message}'.strip()
        )
    if "incorrect" in blob or "invalid" in blob or status == "incorrect":
        return SubmitResult(
            "incorrect", message, f'INCORRECT — "{flag}" rejected. {message}'.strip()
        )
    return SubmitResult("unknown", message or text, f"Unknown HTB submit status: {text[:200]}")
