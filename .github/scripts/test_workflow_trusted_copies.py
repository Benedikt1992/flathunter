"""Guard the trusted copies of the gate scripts in the agent workflows.

Steps that run after an agent session must not run scripts from the working tree, which
the agent may have changed. The workflows therefore copy `.github/scripts/` before the
session and point `OPS` into that copy. `agent_ops.py` imports `agent_guard`, so a copy
of the single file fails with ModuleNotFoundError (run 45051047262, issue #48).
Run with `python -m pytest .github/scripts`. Plain text parsing: no YAML dependency.
"""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / "workflows"
SINGLE_FILE_COPY = re.compile(r"\bcp\s+(?!-r\b)\S*\.github/scripts/\S+\.py\b")
DIRECTORY_COPY = re.compile(r"\bcp\s+-r\s+[^\n]*\.github/scripts/?(?:\s|$)")
OPS_FROM_COPY = re.compile(r"OPS=\S*/scripts/agent_ops\.py")
# OPS exported for later steps; agent-plan.yml only sets a local variable on its checkout,
# which is fine because the planner cannot change files.
OPS_EXPORT = re.compile(r"OPS=[^\n]*\$GITHUB_ENV")


def agent_workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("agent-*.yml"))


def test_patterns_tell_file_and_directory_copies_apart() -> None:
    single = 'cp .github/scripts/agent_ops.py "$RUNNER_TEMP/agent_ops.py"'
    directory = 'cp -r .github/scripts .github/agents "$T/"'
    assert SINGLE_FILE_COPY.search(single)
    assert not SINGLE_FILE_COPY.search(directory)
    assert DIRECTORY_COPY.search(directory)
    assert not DIRECTORY_COPY.search(single)
    assert OPS_FROM_COPY.search('echo "OPS=$T/scripts/agent_ops.py" >> "$GITHUB_ENV"')
    assert not OPS_FROM_COPY.search('echo "OPS=$RUNNER_TEMP/agent_ops.py" >> "$GITHUB_ENV"')
    assert OPS_EXPORT.search('echo "OPS=$T/scripts/agent_ops.py" >> "$GITHUB_ENV"')
    assert not OPS_EXPORT.search("OPS=.github/scripts/agent_ops.py")


def test_no_agent_workflow_copies_a_single_script() -> None:
    for path in agent_workflows():
        text = path.read_text(encoding="utf-8")
        assert not SINGLE_FILE_COPY.search(text), (
            f"{path.name}: copy the whole .github/scripts/ directory, not single files"
        )


def test_ops_points_into_a_copy_of_the_scripts_directory() -> None:
    users = [p for p in agent_workflows() if OPS_EXPORT.search(p.read_text(encoding="utf-8"))]
    assert {"agent-implement.yml", "agent-fix.yml", "agent-review.yml"} <= {p.name for p in users}
    for path in users:
        text = path.read_text(encoding="utf-8")
        assert DIRECTORY_COPY.search(text), f"{path.name}: no copy of .github/scripts/"
        assert OPS_FROM_COPY.search(text), f"{path.name}: OPS does not point into the copy"
