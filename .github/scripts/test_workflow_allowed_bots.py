"""Guard the `allowed_bots` input of claude-code-action steps in the agent workflows.

Workflows start each other with `gh workflow run`, so the actor is `github-actions[bot]`.
claude-code-action aborts for bot actors that are not in `allowed_bots`. Run with
`python -m pytest .github/scripts`. Plain text parsing: no YAML dependency needed.
"""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / "workflows"
ACTION = "anthropics/claude-code-action"
ALLOWED = re.compile(r"^\s*allowed_bots:\s*github-actions\s*$")
WILDCARD = re.compile(r"^\s*allowed_bots:.*\*")
DISPATCH = re.compile(r"^\s*workflow_dispatch\s*:", re.MULTILINE)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def action_steps(text: str) -> list[list[str]]:
    """Return the lines of every step that uses claude-code-action."""
    lines = text.splitlines()
    steps: list[list[str]] = []
    for number, line in enumerate(lines):
        if "uses:" not in line or ACTION not in line:
            continue
        start = number
        while not lines[start].lstrip().startswith("- "):
            start -= 1
        step_indent = _indent(lines[start])
        end = number + 1
        while end < len(lines) and (not lines[end].strip() or _indent(lines[end]) > step_indent):
            end += 1
        steps.append(lines[start:end])
    return steps


def dispatchable_workflows() -> list[Path]:
    return [
        path
        for path in sorted(WORKFLOWS.glob("agent-*.yml"))
        if DISPATCH.search(path.read_text(encoding="utf-8"))
    ]


def test_finds_the_agent_steps() -> None:
    names = {
        path.name
        for path in dispatchable_workflows()
        if action_steps(path.read_text(encoding="utf-8"))
    }
    assert {"agent-implement.yml", "agent-review.yml", "agent-fix.yml"} <= names


def test_action_steps_split_on_step_boundaries() -> None:
    text = (
        "steps:\n"
        "  - name: A\n"
        "    uses: anthropics/claude-code-action@v1\n"
        "    with:\n"
        "      allowed_bots: github-actions\n"
        "\n"
        "  - name: B\n"
        "    run: echo allowed_bots: github-actions\n"
    )
    steps = action_steps(text)
    assert len(steps) == 1
    assert any(ALLOWED.match(line) for line in steps[0])
    assert not any("name: B" in line for line in steps[0])


def test_every_dispatchable_action_step_allows_github_actions() -> None:
    for path in dispatchable_workflows():
        for step in action_steps(path.read_text(encoding="utf-8")):
            assert any(ALLOWED.match(line) for line in step), (
                f"{path.name}: claude-code-action step lacks `allowed_bots: github-actions`"
            )


def test_wildcard_pattern_finds_star_anywhere_in_the_value() -> None:
    for line in (
        "  allowed_bots: '*'",
        '  allowed_bots: "*"',
        "  allowed_bots: github-actions,*",
        "  allowed_bots: github-actions, '*'",
    ):
        assert WILDCARD.match(line), line
    assert not WILDCARD.match("  allowed_bots: github-actions")
    assert not WILDCARD.match("  # allowed_bots: github-actions")


def test_no_workflow_allows_all_bots() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not WILDCARD.match(line), f"{path.name}: allowed_bots must not be '*'"
