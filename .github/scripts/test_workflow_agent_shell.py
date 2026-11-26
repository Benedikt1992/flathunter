"""Guard the shell of the agents that change code (implementer and fixer).

They must be able to delete files (scratch files, files the issue removes), to run
the mutation report, which takes longer than Claude Code's default limit of 2 minutes
per shell command, and to turn workflow edits into `.github/workflows.patch` (the agent
app may not push workflow files). Run with `python -m pytest .github/scripts`.
"""

import re

from test_workflow_allowed_bots import WORKFLOWS, action_steps

WRITERS = ("agent-implement.yml", "agent-fix.yml")
TOOLS = re.compile(r'--allowedTools "([^"]*)"')
SETTING = re.compile(r'^\s*(BASH_DEFAULT_TIMEOUT_MS|BASH_MAX_TIMEOUT_MS):\s*"(\d+)"\s*$')
MUTMUT_TIMEOUT_MS = 1200 * 1000  # default --timeout of mutation_report.py


def _step(name: str) -> list[str]:
    (step,) = action_steps((WORKFLOWS / name).read_text(encoding="utf-8"))
    return step


def _settings(step: list[str]) -> dict[str, int]:
    found = (SETTING.match(line) for line in step)
    return {match.group(1): int(match.group(2)) for match in found if match}


def test_settings_are_read_from_the_step_env() -> None:
    step = ["  env:", '    BASH_DEFAULT_TIMEOUT_MS: "5"', '    OTHER: "1"', "  run: x"]
    assert _settings(step) == {"BASH_DEFAULT_TIMEOUT_MS": 5}


def test_writing_agents_may_delete_files() -> None:
    for name in WRITERS:
        (tools,) = TOOLS.findall("\n".join(_step(name)))
        allowed = tools.split(",")
        for tool in ("Bash(rm:*)", "Bash(git rm:*)", "Bash(git mv:*)", "Bash(git commit:*)"):
            assert tool in allowed, f"{name}: {tool} missing"


def test_writing_agents_may_turn_workflow_edits_into_the_patch() -> None:
    for name in WRITERS:
        (tools,) = TOOLS.findall("\n".join(_step(name)))
        assert "Bash(python .github/scripts/workflow_patch.py:*)" in tools.split(","), name


def test_writing_agents_can_wait_for_the_mutation_report() -> None:
    for name in WRITERS:
        settings = _settings(_step(name))
        assert settings["BASH_DEFAULT_TIMEOUT_MS"] > MUTMUT_TIMEOUT_MS, name
        assert settings["BASH_MAX_TIMEOUT_MS"] >= settings["BASH_DEFAULT_TIMEOUT_MS"], name
