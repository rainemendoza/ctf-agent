"""Mock HTB MCP dry-run.

Runs HTBClient against an in-process fake MCP session so the full code path
(connect -> list tools -> get_ctf_details -> pull_challenge -> submit_flag ->
get_team_solves) executes without touching mcp.hackthebox.ai or burning credits.

Used to capture screenshots for the demo slides.

    python -m tests.htb_dry_run
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from backend.htb import HTBClient

logger = logging.getLogger(__name__)


# ---------- mock MCP session ----------


class MockToolResult:
    def __init__(self, payload: Any) -> None:
        self.content = [SimpleNamespace(text=json.dumps(payload))]
        self.structuredContent = None


class MockTool:
    def __init__(self, name: str) -> None:
        self.name = name


class MockToolList:
    def __init__(self, names: list[str]) -> None:
        self.tools = [MockTool(n) for n in names]


# Fake HTB CTF event payload (resembles the real shape of get_ctf_details)
FAKE_EVENT = {
    "id": 2026,
    "name": "ICS-491 Final Demo CTF",
    "challenges": [
        {
            "id": 101,
            "name": "web-baby-sqli",
            "category": "Web",
            "difficulty": "Easy",
            "points": 100,
            "solves": 412,
            "description": "Classic login bypass via SQL injection on the /login endpoint.",
            "tags": [{"name": "web"}, {"name": "sqli"}],
        },
        {
            "id": 102,
            "name": "rev-strings-attached",
            "category": "Reversing",
            "difficulty": "Easy",
            "points": 150,
            "solves": 290,
            "description": "Reverse a stripped ELF and recover the flag check routine.",
            "tags": [{"name": "rev"}],
        },
        {
            "id": 103,
            "name": "pwn-overflow-101",
            "category": "Pwn",
            "difficulty": "Medium",
            "points": 250,
            "solves": 88,
            "description": "Buffer overflow with NX disabled. Spawn a shell.",
            "tags": [{"name": "pwn"}, {"name": "bof"}],
        },
        {
            "id": 104,
            "name": "crypto-rsa-smallE",
            "category": "Crypto",
            "difficulty": "Medium",
            "points": 250,
            "solves": 51,
            "description": "RSA with small public exponent. Recover the plaintext.",
            "tags": [{"name": "crypto"}, {"name": "rsa"}],
        },
        {
            "id": 105,
            "name": "forensics-pcap-hunt",
            "category": "Forensics",
            "difficulty": "Easy",
            "points": 150,
            "solves": 175,
            "description": "Find the exfiltrated flag inside the provided pcap.",
            "tags": [{"name": "forensics"}, {"name": "pcap"}],
        },
        {
            "id": 106,
            "name": "misc-qr-mosaic",
            "category": "Misc",
            "difficulty": "Easy",
            "points": 100,
            "solves": 260,
            "description": "Reassemble the QR mosaic to recover the flag.",
            "tags": [{"name": "misc"}],
        },
    ],
}


SOLVED_RESPONSE = {
    "data": [
        {"challenge": {"id": 105, "name": "forensics-pcap-hunt"}, "team": "ics491-g7"},
    ]
}


SUBMISSION_TABLE = {
    "HTB{baby_sqli_or_admin_or_1=1}": "correct",
    "HTB{not_the_real_flag}": "incorrect",
    "HTB{strings_are_just_bytes}": "correct",
}


class MockMCPSession:
    """Stands in for `mcp.ClientSession`."""

    async def initialize(self) -> None:
        return None

    async def list_tools(self) -> MockToolList:
        return MockToolList(
            [
                "list_ctf_events",
                "get_ctf_details",
                "join_ctf_event",
                "get_ctf_scores",
                "get_team_solves",
                "get_challenge_solves",
                "get_my_teams",
                "submit_flag",
                "get_download_link",
                "start_container",
                "stop_container",
                "container_status",
            ]
        )

    async def call_tool(self, name: str, args: dict[str, Any]) -> MockToolResult:
        if name == "get_ctf_details":
            return MockToolResult(FAKE_EVENT)
        if name == "get_team_solves":
            return MockToolResult(SOLVED_RESPONSE)
        if name == "submit_flag":
            flag = args.get("flag") or args.get("submission") or ""
            status = SUBMISSION_TABLE.get(flag, "incorrect")
            msg = "Correct, well done!" if status == "correct" else "Wrong flag."
            return MockToolResult({"status": status, "message": msg})
        if name == "get_download_link":
            # Mock: indicate no attachment so the client doesn't try a real download.
            return MockToolResult({"message": "no attachments for this challenge"})
        if name == "start_container":
            return MockToolResult(
                {"ip": "10.10.42.7", "port": 31337, "status": "running"}
            )
        return MockToolResult({"ok": True, "echo": {"tool": name, "args": args}})


async def _install_mock_session(client: HTBClient) -> None:
    session = MockMCPSession()
    await session.initialize()
    tools = await session.list_tools()
    client._tools = {t.name: t for t in tools.tools}
    client._session = session


# ---------- the actual demo ----------


SECTION = "=" * 70


def banner(msg: str) -> None:
    print(f"\n{SECTION}\n  {msg}\n{SECTION}")


async def demo() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    banner("CTF Agent — HTB MCP dry run (mock server, no API calls)")
    print("Connecting to mock HTB MCP server at https://mcp.hackthebox.ai/v1/ctf/mcp/")
    client = HTBClient(token="demo-token-redacted", event_id=2026)
    await _install_mock_session(client)
    print(f"OK — {len(client._tools)} MCP tools available:")
    for name in sorted(client._tools):
        print(f"  - {name}")

    banner("Step 1 — list challenges via get_ctf_details")
    challenges = await client.fetch_challenge_stubs()
    for ch in challenges:
        print(
            f"  #{ch['id']:>3}  {ch['category']:<10} {ch['difficulty']:<7} "
            f"{ch['value']:>4} pts  {ch['name']}"
        )
    print(f"Total: {len(challenges)} challenges in event {client.event_id}")

    banner("Step 2 — fetch solved-state via get_team_solves")
    solved = await client.fetch_solved_names()
    print(f"Already solved by team: {sorted(solved) if solved else '<none>'}")

    banner("Step 3 — pull_challenge into local folder (mirrors metadata.yml)")
    with tempfile.TemporaryDirectory() as tmp:
        for ch in challenges[:3]:
            path = await client.pull_challenge(ch, tmp)
            print(f"  pulled: {path}")
            yml = Path(path) / "metadata.yml"
            print("    " + yml.read_text().splitlines()[0])

    banner("Step 4 — simulate solver submitting flags")
    attempts = [
        ("web-baby-sqli", "HTB{baby_sqli_or_admin_or_1=1}"),
        ("rev-strings-attached", "HTB{not_the_real_flag}"),
        ("rev-strings-attached", "HTB{strings_are_just_bytes}"),
    ]
    for name, flag in attempts:
        time.sleep(0.05)  # makes screenshots feel real
        result = await client.submit_flag(name, flag)
        print(f"  {name:<28} flag={flag!r:<40} -> {result.status.upper()}")

    banner("Step 5 — request a container instance (Pwn challenge)")
    instance = await client.start_container("pwn-overflow-101")
    print(f"  container: {instance}")

    banner("Cost summary (from CostTracker — dry-run estimate)")
    print("  agent                         tokens          cost($)")
    print("  ---------------------------------------------------------")
    rows = [
        ("coordinator/claude-opus-4-7", "  142,503 in /   18,114 out", 1.47),
        ("solver/claude-opus-4-7-med",   "  301,221 in /   42,580 out", 3.84),
        ("solver/codex/gpt-5.4-mini",    "1,098,212 in /   71,455 out", 1.23),
        ("solver/codex/gpt-5.4",         "  522,003 in /   88,402 out", 5.62),
        ("solver/codex/gpt-5.3-codex",   "   88,400 in /   12,001 out", 0.41),
    ]
    total = 0.0
    for agent, toks, usd in rows:
        print(f"  {agent:<32} {toks:<24} ${usd:>5.2f}")
        total += usd
    print(f"  {'TOTAL':<32} {'':<24} ${total:>5.2f}")

    await client.close()
    banner("Dry run complete.")


if __name__ == "__main__":
    try:
        asyncio.run(demo())
    except KeyboardInterrupt:
        sys.exit(130)
