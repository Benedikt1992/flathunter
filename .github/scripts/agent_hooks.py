#!/usr/bin/env python3
"""Claude Code hooks: run the quality gates inside the agent loop.

Configured in .claude/settings.json, so the same checks that run in pre-commit (prek)
and CI also run while an agent (or a human using Claude Code) works:

  post-edit  After Edit/Write of a .py file: auto-format it (unless it is a legacy file
             in quality/format-baseline.json) and report *new* Ruff violations in that
             file. Exit code 2 feeds the report back to Claude.
  stop       Before Claude finishes: if Python files changed, run `prek run --all-files`
             once. On failure, exit code 2 makes Claude fix the problems first.

Both hooks do nothing when the tools are not installed (pip install -r requirements-dev.txt).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RATCHET = ROOT / ".github" / "scripts" / "ratchet.py"
FORMAT_BASELINE = ROOT / "quality" / "format-baseline.json"
MAX_OUTPUT = 6000


def run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """Run a command in the repository root."""
    return subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False
    )


def read_input() -> dict:
    """Hook payload from stdin (empty dict if there is none)."""
    try:
        return json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}


def block(message: str) -> int:
    """Report to Claude and block (exit code 2)."""
    sys.stderr.write(message[-MAX_OUTPUT:] + "\n")
    return 2


def post_edit(payload: dict) -> int:
    """Format and lint the file Claude just wrote."""
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or (payload.get("tool_response") or {}).get("filePath")
    if not raw or not raw.endswith(".py") or not shutil.which("ruff"):
        return 0
    path = Path(raw).resolve()
    if not path.is_file() or ROOT not in path.parents:
        return 0
    relative = path.relative_to(ROOT).as_posix()

    legacy = (
        set(json.loads(FORMAT_BASELINE.read_text(encoding="utf-8")))
        if FORMAT_BASELINE.exists()
        else set()
    )
    if relative not in legacy:
        run(["ruff", "format", "--quiet", relative])

    result = run([sys.executable, str(RATCHET), "ruff", relative])
    if result.returncode != 0:
        return block(
            f"Quality gate (ruff) for {relative} — fix these before continuing:\n" + result.stdout
        )
    return 0


def python_changes() -> bool:
    """True if the working tree or the branch (vs. origin's default) changed Python files."""
    status = run(["git", "status", "--porcelain", "--", "*.py"])
    if status.stdout.strip():
        return True
    # AGENT_BASE_REF is set by the agent workflows (actions/checkout sets no origin/HEAD).
    for base in (os.environ.get("AGENT_BASE_REF", ""), "origin/HEAD", "origin/main"):
        if base and run(["git", "rev-parse", "--verify", "--quiet", base]).returncode == 0:
            diff = run(["git", "diff", "--name-only", f"{base}...HEAD", "--", "*.py"])
            return bool(diff.stdout.strip())
    return False


def stop(payload: dict) -> int:
    """Run all gates once before Claude stops."""
    if payload.get("stop_hook_active") or not shutil.which("prek"):
        return 0
    if not python_changes():
        return 0
    try:
        result = run(["prek", "run", "--all-files"], timeout=600)
    except subprocess.TimeoutExpired:
        return 0
    if result.returncode != 0:
        failed = [line for line in result.stdout.splitlines() if "Failed" in line]
        return block(
            "Quality gates failed (prek run --all-files). Fix the problems below; "
            "do not weaken or bypass a gate.\n" + "\n".join(failed) + "\n\n" + result.stdout
        )
    return 0


def main() -> int:
    """Entry point: agent_hooks.py post-edit|stop (payload on stdin)."""
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = read_input()
    if command == "post-edit":
        return post_edit(payload)
    if command == "stop":
        return stop(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
