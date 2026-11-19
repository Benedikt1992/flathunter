#!/usr/bin/env python3
"""Deterministic PR guard: detects changes that weaken quality gates.

Fails a pull request that
  * touches protected files (CI, lint/type/coverage/architecture config, agent rules, deps),
  * adds lint/type/coverage/security suppressions or skips tests,
  * deletes test files or test functions,
unless the maintainer explicitly allowed it by adding the matching override label
(``allow-protected-change``, ``allow-suppression``, ``allow-test-removal``).
Only labels added by the maintainer count; labels added by bots or agents are ignored.

For pull requests authored by the maintainer the findings are reported but do not fail.

Uses only the standard library so it runs before any dependency is installed.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import ratchet

if TYPE_CHECKING:
    from collections.abc import Callable

PROTECTED_PATTERNS = [
    ".github/*",
    ".claude/*",
    "pyproject.toml",
    "pytest.ini",
    ".pre-commit-config.yaml",
    "requirements-dev.txt",
    "quality/*",
    "complexipy-snapshot.json",
    ".importlinter",
    ".pylintrc",
    "pyrightconfig.json",
    ".coveragerc",
    "CLAUDE.md",
    "ARCHITECTURE.md",
    "docs/adr/*",
    "Pipfile",
    "Pipfile.lock",
    "setup.py",
]

SUPPRESSION_PATTERNS = [
    (re.compile(r"#\s*pylint:\s*disable"), "pylint disable"),
    (re.compile(r"#\s*type:\s*ignore"), "type: ignore"),
    (re.compile(r"#\s*pyright:\s*ignore"), "pyright: ignore"),
    (re.compile(r"#\s*noqa\b"), "noqa"),
    (re.compile(r"#\s*ruff:\s*noqa"), "ruff: noqa"),
    (re.compile(r"#\s*pyrefly:\s*ignore"), "pyrefly: ignore"),
    (re.compile(r"#\s*fmt:\s*(off|skip)"), "fmt: off/skip"),
    (re.compile(r"pragma:\s*no\s*mutate"), "pragma: no mutate"),
    (re.compile(r"#\s*nosec\b"), "nosec"),
    (re.compile(r"pragma:\s*no\s*cover"), "pragma: no cover"),
    (re.compile(r"pytest\.mark\.(skip|skipif|xfail)\b"), "skipped/xfail test"),
    (re.compile(r"pytest\.(skip|xfail)\("), "skipped/xfail test"),
    (re.compile(r"unittest\.skip"), "skipped test"),
]

TEST_FUNC = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(")

LABEL_PROTECTED = "allow-protected-change"
LABEL_SUPPRESSION = "allow-suppression"
LABEL_TEST_REMOVAL = "allow-test-removal"


@dataclass
class FileDiff:
    """Added/removed lines of one file in a unified diff."""

    path: str
    old_path: str | None = None
    deleted: bool = False
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


@dataclass
class Finding:
    """A single guard violation and the label that would allow it."""

    label: str
    message: str


def parse_diff(diff_text: str) -> list[FileDiff]:
    """Parse `git diff --unified=0` output into per-file added/removed lines."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.+) b/(.+)$", line)
            current = FileDiff(path=match.group(2) if match else line)
            if match:
                current.old_path = match.group(1)
            files.append(current)
        elif current is None:
            continue
        elif line.startswith("deleted file mode"):
            current.deleted = True
        elif line.startswith("+++ ") or line.startswith("--- "):
            continue
        elif line.startswith("+"):
            current.added.append(line[1:])
        elif line.startswith("-"):
            current.removed.append(line[1:])
    return files


def is_protected(path: str) -> bool:
    """True if the path matches one of the protected patterns."""
    return any(fnmatch.fnmatch(path, pattern) for pattern in PROTECTED_PATTERNS)


def is_test_file(path: str) -> bool:
    """True for test modules."""
    name = os.path.basename(path)
    return path.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def find_violations(
    files: list[FileDiff], shrink_ok: Callable[[str], bool] = lambda _path: False
) -> list[Finding]:
    """Return every guard violation in the diff.

    `shrink_ok(path)` returns True for ratchet baselines whose change only removes debt;
    those may change without the maintainer's label.
    """
    findings: list[Finding] = []
    for diff in files:
        for path in {diff.path, diff.old_path or diff.path}:
            if is_protected(path) and not shrink_ok(path):
                findings.append(Finding(LABEL_PROTECTED, f"`{path}` is a protected file"))

        if diff.path.endswith(".py"):
            for line in diff.added:
                for pattern, name in SUPPRESSION_PATTERNS:
                    if pattern.search(line):
                        findings.append(
                            Finding(
                                LABEL_SUPPRESSION,
                                f"`{diff.path}` adds a suppression ({name}): `{line.strip()[:120]}`",
                            )
                        )

        if is_test_file(diff.old_path or diff.path):
            if diff.deleted:
                findings.append(Finding(LABEL_TEST_REMOVAL, f"test file `{diff.path}` is deleted"))
            else:
                removed = {m.group(1) for line in diff.removed if (m := TEST_FUNC.match(line))}
                added = {m.group(1) for line in diff.added if (m := TEST_FUNC.match(line))}
                for name in sorted(removed - added):
                    findings.append(
                        Finding(LABEL_TEST_REMOVAL, f"test `{name}` is removed from `{diff.path}`")
                    )
    # de-duplicate while keeping order
    seen: set[tuple[str, str]] = set()
    unique = []
    for finding in findings:
        key = (finding.label, finding.message)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def labels_granted_by(events: list[dict], current_labels: set[str], maintainer: str) -> set[str]:
    """Labels that are currently set and whose most recent `labeled` event came from the maintainer."""
    last_actor: dict[str, str] = {}
    for event in events:
        if event.get("event") == "labeled":
            name = (event.get("label") or {}).get("name", "")
            last_actor[name] = (event.get("actor") or {}).get("login", "")
    return {
        name for name in current_labels if last_actor.get(name, "").lower() == maintainer.lower()
    }


def github_get(url: str, token: str) -> list | dict:
    """GET a GitHub API URL, following pagination for list responses."""
    results: list = []
    while url:
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
            link = response.headers.get("Link", "")
        if not isinstance(data, list):
            return data
        results.extend(data)
        match = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = match.group(1) if match else ""
    return results


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="base commit sha")
    parser.add_argument("--head", required=True, help="head commit sha")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--maintainer", required=True)
    parser.add_argument("--author", required=True, help="PR author login")
    args = parser.parse_args()

    diff_text = subprocess.run(
        ["git", "diff", "--unified=0", "--no-color", f"{args.base}...{args.head}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    def git_show(sha: str, path: str) -> str | None:
        result = subprocess.run(
            ["git", "show", f"{sha}:{path}"], capture_output=True, text=True, check=False
        )
        return result.stdout if result.returncode == 0 else None

    def shrink_ok(path: str) -> bool:
        kind = ratchet.RATCHET_FILES.get(path)
        return kind is not None and ratchet.is_shrink(
            kind, git_show(args.base, path), git_show(args.head, path)
        )

    findings = find_violations(parse_diff(diff_text), shrink_ok)

    granted: set[str] = set()
    token = os.environ.get("GITHUB_TOKEN", "")
    if findings and token:
        api = f"https://api.github.com/repos/{args.repo}/issues/{args.pr}"
        issue = github_get(api, token)
        current = {label["name"] for label in issue.get("labels", [])}
        events = github_get(f"{api}/events?per_page=100", token)
        granted = labels_granted_by(events, current, args.maintainer)

    blocking = [f for f in findings if f.label not in granted]
    allowed = [f for f in findings if f.label in granted]
    by_maintainer = args.author.lower() == args.maintainer.lower()

    lines = ["## Agent guard", ""]
    if not findings:
        lines.append("No protected files, suppressions or removed tests. ✅")
    for finding in blocking:
        lines.append(
            f"- ❌ {finding.message} — needs label `{finding.label}` from @{args.maintainer}"
        )
    for finding in allowed:
        lines.append(f"- ✅ {finding.message} — allowed by `{finding.label}`")
    if blocking and by_maintainer:
        lines.append("")
        lines.append("PR is authored by the maintainer: reported only, not failing.")
    report = "\n".join(lines)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report + "\n")

    return 1 if blocking and not by_maintainer else 0


if __name__ == "__main__":
    sys.exit(main())
