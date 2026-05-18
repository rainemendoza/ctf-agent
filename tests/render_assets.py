"""Render slide assets: architecture diagram + terminal screenshots.

Outputs land in docs/assets/.

    python -m tests.render_assets
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.patches as patches
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ASSETS = Path("docs/assets")
ASSETS.mkdir(parents=True, exist_ok=True)


# ---------- architecture diagram ----------


def _box(ax, x, y, w, h, text, *, face="#eef3ff", edge="#3754b4", text_color="#0d1b4a"):
    box = patches.FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.05,rounding_size=0.10",
        linewidth=1.5,
        edgecolor=edge,
        facecolor=face,
    )
    ax.add_patch(box)
    ax.text(
        x + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        fontsize=10,
        color=text_color,
        family="DejaVu Sans",
    )


def _arrow(ax, x1, y1, x2, y2, *, color="#3754b4", label=None):
    ax.annotate(
        "",
        xy=(x2, y2),
        xytext=(x1, y1),
        arrowprops=dict(arrowstyle="->", lw=1.4, color=color),
    )
    if label:
        ax.text(
            (x1 + x2) / 2 + 0.05,
            (y1 + y2) / 2,
            label,
            fontsize=8,
            color=color,
            style="italic",
        )


def render_architecture() -> Path:
    fig, ax = plt.subplots(figsize=(13, 7.5), dpi=180)
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 9)
    ax.axis("off")
    ax.set_title(
        "CTF Agent — Hack The Box (MCP) architecture",
        fontsize=14,
        weight="bold",
        pad=8,
    )

    # HTB platform
    _box(ax, 0.4, 7.2, 4.2, 1.2, "Hack The Box CTF Platform\nmcp.hackthebox.ai (Streamable HTTP)", face="#fef0d8", edge="#b3700a")

    # MCP tools list
    _box(
        ax,
        5.2,
        6.7,
        4.2,
        1.7,
        "HTB MCP tools\nget_ctf_details · submit_flag\nget_team_solves · get_download_link\nstart_container · container_status",
        face="#fef0d8",
        edge="#b3700a",
    )

    # PlatformClient interface
    _box(ax, 10.0, 7.2, 3.6, 1.2, "PlatformClient (Protocol)\nCTFdClient | HTBClient", face="#e9f7e9", edge="#1f7a1f")

    # Poller
    _box(ax, 0.4, 5.2, 4.2, 1.2, "Poller (5s)\nnew/solved challenges", face="#f6e6ff", edge="#6a2bc3")

    # Coordinator
    _box(
        ax,
        5.2,
        5.2,
        4.2,
        1.2,
        "Coordinator LLM\n(Claude / Codex)\nstrategy + spawn",
        face="#f6e6ff",
        edge="#6a2bc3",
    )

    # Strategy / message bus
    _box(
        ax,
        10.0,
        5.2,
        3.6,
        1.2,
        "Strategy + Message Bus\nrank · stop · share findings",
        face="#f6e6ff",
        edge="#6a2bc3",
    )

    # Swarms
    for i, name in enumerate(["Swarm: web-baby-sqli", "Swarm: rev-strings-attached", "Swarm: pwn-overflow-101"]):
        _box(
            ax,
            0.4 + i * 4.6,
            3.0,
            4.2,
            1.5,
            f"{name}\nClaude Opus + GPT-5.4 + GPT-5.4-mini\n(race to flag)",
            face="#e8f1ff",
            edge="#1f4cb4",
        )

    # Sandboxes
    for i, name in enumerate(["Docker Sandbox", "Docker Sandbox", "Docker Sandbox"]):
        _box(
            ax,
            0.4 + i * 4.6,
            0.6,
            4.2,
            1.6,
            f"{name}\npwntools · radare2 · gdb\nz3 · volatility · ffmpeg",
            face="#f4f4f4",
            edge="#666",
        )

    # arrows
    _arrow(ax, 4.6, 7.8, 5.2, 7.8)
    _arrow(ax, 9.4, 7.8, 10.0, 7.8)
    _arrow(ax, 11.8, 7.2, 11.8, 6.4, label="Implements")
    _arrow(ax, 2.5, 7.2, 2.5, 6.4, label="polls")
    _arrow(ax, 4.6, 5.8, 5.2, 5.8)
    _arrow(ax, 9.4, 5.8, 10.0, 5.8)
    _arrow(ax, 7.3, 5.2, 7.3, 4.5, label="spawn")
    _arrow(ax, 2.5, 3.0, 2.5, 2.2)
    _arrow(ax, 7.1, 3.0, 7.1, 2.2)
    _arrow(ax, 11.7, 3.0, 11.7, 2.2)

    out = ASSETS / "architecture.png"
    plt.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


# ---------- terminal screenshot ----------


def render_terminal(log_text: str, out_name: str, title: str = "ctf-solve dry-run") -> Path:
    lines = log_text.splitlines()
    if len(lines) > 60:
        lines = lines[:60] + ["...", "(output truncated for slide)"]

    title_band_px = 36
    line_px = 18
    pad_px = 20
    h_px = title_band_px + pad_px + line_px * len(lines) + pad_px
    w_px = 1480
    fig = plt.figure(figsize=(w_px / 100, h_px / 100), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w_px)
    ax.set_ylim(0, h_px)
    ax.invert_yaxis()
    ax.axis("off")

    ax.add_patch(patches.Rectangle((0, 0), w_px, h_px, color="#1e1e2e"))
    ax.add_patch(patches.Rectangle((0, 0), w_px, title_band_px, color="#2b2b3a"))
    for i, color in enumerate(["#ff5f56", "#ffbd2e", "#27c93f"]):
        ax.add_patch(patches.Circle((18 + i * 26, title_band_px / 2), 7, color=color))
    ax.text(w_px / 2, title_band_px / 2, title, ha="center", va="center", color="#cccccc", fontsize=10)

    y = title_band_px + pad_px
    for line in lines:
        color = "#e6e6e6"
        if "CORRECT" in line and "INCORRECT" not in line:
            color = "#9ee493"
        elif "INCORRECT" in line:
            color = "#f08080"
        elif line.startswith("==="):
            color = "#7aa2f7"
        elif "WARNING" in line:
            color = "#e0af68"
        elif line.lstrip().startswith("#"):
            color = "#9ece6a"
        ax.text(14, y, line, color=color, family="monospace", fontsize=10, va="top")
        y += line_px

    out = ASSETS / out_name
    plt.savefig(out, bbox_inches="tight", facecolor="#1e1e2e", pad_inches=0)
    plt.close(fig)
    return out


# ---------- cost bar chart ----------


def render_cost_chart() -> Path:
    agents = [
        "coordinator\n(claude-opus-4.7)",
        "solver\n(claude-opus-4.7-med)",
        "solver\n(gpt-5.4-mini)",
        "solver\n(gpt-5.4)",
        "solver\n(gpt-5.3-codex)",
    ]
    costs = [1.47, 3.84, 1.23, 5.62, 0.41]
    fig, ax = plt.subplots(figsize=(11, 4.5), dpi=160)
    bars = ax.bar(agents, costs, color="#3754b4", edgecolor="#0d1b4a")
    ax.set_ylabel("Cost (USD)")
    ax.set_title("Cost per agent — dry-run estimate (total ~$12.57)")
    for bar, c in zip(bars, costs, strict=False):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.08, f"${c:.2f}", ha="center", fontsize=9)
    ax.set_ylim(0, max(costs) * 1.25)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    out = ASSETS / "cost_chart.png"
    plt.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def main() -> None:
    arch = render_architecture()
    print(f"wrote {arch}")
    log_path = Path("/tmp/htb_dryrun.log")
    if log_path.exists():
        text = log_path.read_text()
    else:
        text = "(no log captured — run tests.htb_dry_run first)"
    # Split by each `  Step N — ` heading (or first/last sections).
    raw = text.splitlines()
    headings_idx = [i for i, ln in enumerate(raw) if ln.startswith("  Step ") or ln.startswith("  Cost ") or ln.startswith("  CTF Agent")]
    sections: list[list[str]] = []
    for i, idx in enumerate(headings_idx):
        # Include the surrounding "===" banner lines (1 above, 1 below)
        start = max(0, idx - 1)
        end = headings_idx[i + 1] - 1 if i + 1 < len(headings_idx) else len(raw)
        sections.append(raw[start:end])

    # Distribute sections across 3 panes
    target = 3
    panes: list[list[str]] = [[] for _ in range(target)]
    sizes = [0] * target
    for sec in sections:
        idx = sizes.index(min(sizes))
        panes[idx].extend(sec)
        panes[idx].append("")
        sizes[idx] += len(sec) + 1
    for i, pane in enumerate(panes, 1):
        if not pane:
            continue
        out = render_terminal("\n".join(pane), f"terminal_{i}.png", title=f"ctf-solve --platform htb (dry-run, pane {i})")
        print(f"wrote {out}")
    cost = render_cost_chart()
    print(f"wrote {cost}")


if __name__ == "__main__":
    main()
