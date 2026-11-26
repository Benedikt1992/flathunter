#!/usr/bin/env python3
"""Workflow changes of an agent PR, kept as a patch file that the maintainer applies.

The agent app may not push `.github/workflows/` (it has no `workflows` permission). So
the implementer and the fixer commit their workflow changes as `.github/workflows.patch`.
The reviewer and the fixer see that file in the diff like any other file. CI checks that
it applies to the branch and changes nothing outside `.github/workflows/`. The maintainer
applies it before merging.

    python .github/scripts/workflow_patch.py make    # agent: workflow edits -> patch file
    python .github/scripts/workflow_patch.py open    # agent: patch file -> workflow edits
    python .github/scripts/workflow_patch.py check   # CI: the patch applies, workflows only
    python .github/scripts/workflow_patch.py apply   # maintainer: apply, stage, drop the file

Run it from the repository root.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    Run = Callable[..., subprocess.CompletedProcess[str]]

PATCH = ".github/workflows.patch"
WORKFLOWS = ".github/workflows/"


def git(*args: str, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run git in the current directory and capture its output; never raises on failure."""
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False, env=env)


def outside_workflows(paths: list[str]) -> list[str]:
    """Paths that are not below `.github/workflows/`, in their given order."""
    return [path for path in paths if not path.startswith(WORKFLOWS)]


def changed_paths(run: Run) -> tuple[list[str], str]:
    """Paths the patch changes when applied to HEAD, or ([], git's error) if it does not apply.

    The patch is applied to a scratch index built from HEAD, so neither the working tree
    nor the real index matters. `--no-renames` lists both sides of a rename.
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        run("read-tree", "HEAD", env=env)
        applied = run("apply", "--cached", PATCH, env=env)
        if applied.returncode != 0:
            return [], applied.stderr.strip()
        names = run("diff", "--cached", "--name-only", "--no-renames", "-z", "HEAD", env=env)
    return [name for name in names.stdout.split("\0") if name], ""


def problems(run: Run = git) -> list[str]:
    """Reasons why `.github/workflows.patch` cannot be applied as it is; [] without a patch."""
    if not Path(PATCH).is_file():
        return []
    paths, error = changed_paths(run)
    if error:
        return [f"{PATCH} does not apply to this branch: {error}"]
    outside = outside_workflows(paths)
    if outside:
        return [f"{PATCH} changes files outside {WORKFLOWS}: {', '.join(outside)}"]
    return []


def _report(found: list[str]) -> int:
    """Print each problem; exit code 1 if there are any."""
    for problem in found:
        print(problem)
    return 1 if found else 0


def make(run: Run = git) -> int:
    """Write the workflow changes of the working tree to the patch and undo them there."""
    if Path(PATCH).is_file() and run("apply", "--check", PATCH).returncode == 0:
        print(f"{WORKFLOWS} does not contain {PATCH}: run `open` first, or its changes are lost.")
        return 1
    run("add", "--intent-to-add", "--", WORKFLOWS)  # new workflow files show up in the diff
    diff = run("diff", "HEAD", "--", WORKFLOWS).stdout
    if not diff:  # then `add` found no new file either: nothing to undo
        print(f"No changes under {WORKFLOWS}; {PATCH} is unchanged.")
        return 1
    Path(PATCH).write_text(diff, encoding="utf-8")
    # Undoing the diff of HEAD proves that the patch applies to HEAD.
    reverted = run("apply", "-R", PATCH)
    run("reset", "-q", "--", WORKFLOWS)  # drop the intent-to-add entries again
    if reverted.returncode != 0:
        print(f"Wrote {PATCH}, but could not undo the edits: {reverted.stderr.strip()}")
        return 1
    print(f"Wrote {PATCH}; {WORKFLOWS} is back to HEAD. Commit {PATCH}, never {WORKFLOWS}.")
    return 0


def open_patch(run: Run = git) -> int:
    """Apply the patch to the working tree only, so the workflow files can be edited."""
    if not Path(PATCH).is_file():
        print(f"No {PATCH} to open.")
        return 1
    applied = run("apply", PATCH)
    if applied.returncode != 0:
        print(applied.stderr.strip())
        return 1
    print(f"Applied {PATCH} to the working tree. Edit {WORKFLOWS}, then run `make`.")
    return 0


def apply_patch(run: Run = git) -> int:
    """Maintainer: apply the patch to index and working tree, then remove the patch file."""
    if not Path(PATCH).is_file():
        print(f"No {PATCH} on this branch.")
        return 1
    if _report(problems(run)):
        return 1
    applied = run("apply", "--index", PATCH)
    if applied.returncode != 0:
        print(applied.stderr.strip())
        return 1
    run("rm", "-q", PATCH)
    print(f"Applied and staged {PATCH}, and removed it. Check `git diff --cached`, then commit.")
    return 0


def check(run: Run = git) -> int:
    """CI: print the reasons and exit 1 if the patch cannot be applied as it is."""
    return _report(problems(run))


COMMANDS = {"make": make, "open": open_patch, "check": check, "apply": apply_patch}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    return COMMANDS[args.command]()


if __name__ == "__main__":
    sys.exit(main())
