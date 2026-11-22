#!/usr/bin/env python3
"""Deterministic PR guard: detects changes that weaken quality gates.

Fails a pull request that
  * adds lint/type/coverage/security suppressions or skips tests,
  * grows a ratchet baseline (accepting new violations is a suppression too),
  * deletes test files or test functions,
unless the maintainer explicitly allowed it by adding the matching override label
(``allow-suppression``, ``allow-test-removal``).
Only labels added by the maintainer count; labels added by bots or agents are ignored.

Changes to other protected files (CI, lint/type/coverage/architecture config, agent
rules, dependencies) are reported, not blocked: the independent reviewer checks each one
against the issue, and the maintainer decides with the merge (ADR 0003).

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
from pathlib import Path
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
    "requirements-*.txt",
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

LABEL_SUPPRESSION = "allow-suppression"
LABEL_TEST_REMOVAL = "allow-test-removal"
PROTECTED_NOTE = (
    "Protected files changed (no label needed: the independent reviewer checks each one "
    "against the issue, the maintainer decides with the merge):"
)


@dataclass
class FileDiff:
    """Added/removed lines of one file in a unified diff."""

    path: str
    old_path: str | None = None
    deleted: bool = False
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    """A blocking guard violation and the label that would allow it."""

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


def is_ratchet_baseline(path: str) -> bool:
    """True for the ratchet baselines, which may only shrink (checked by the guard itself)."""
    return path in ratchet.RATCHET_FILES


def touched_paths(diff: FileDiff) -> list[str]:
    """Old and new path of a changed file (a single path unless it was renamed)."""
    return sorted({diff.path, diff.old_path or diff.path})


def protected_changes(files: list[FileDiff]) -> list[str]:
    """Changed protected files the reviewer has to assess, ratchet baselines excluded.

    Args:
        files: Parsed diff.

    Returns:
        Sorted paths (old and new path of renamed files).
    """
    return sorted(
        {
            path
            for diff in files
            for path in touched_paths(diff)
            if is_protected(path) and not is_ratchet_baseline(path)
        }
    )


def baseline_findings(diff: FileDiff, shrink_ok: Callable[[str], bool]) -> list[Finding]:
    """A ratchet baseline that does not only shrink accepts new violations."""
    return [
        Finding(LABEL_SUPPRESSION, f"`{path}` grows a ratchet baseline (accepts new violations)")
        for path in touched_paths(diff)
        if is_ratchet_baseline(path) and not shrink_ok(path)
    ]


def suppression_findings(diff: FileDiff) -> list[Finding]:
    """Suppression comments and skipped tests added to a Python file."""
    if not diff.path.endswith(".py"):
        return []
    return [
        Finding(
            LABEL_SUPPRESSION,
            f"`{diff.path}` adds a suppression ({name}): `{line.strip()[:120]}`",
        )
        for line in diff.added
        for pattern, name in SUPPRESSION_PATTERNS
        if pattern.search(line)
    ]


def test_removal_findings(diff: FileDiff) -> list[Finding]:
    """A deleted test module or test functions that disappear from one."""
    if not is_test_file(diff.old_path or diff.path):
        return []
    if diff.deleted:
        return [Finding(LABEL_TEST_REMOVAL, f"test file `{diff.path}` is deleted")]
    removed = {m.group(1) for line in diff.removed if (m := TEST_FUNC.match(line))}
    added = {m.group(1) for line in diff.added if (m := TEST_FUNC.match(line))}
    return [
        Finding(LABEL_TEST_REMOVAL, f"test `{name}` is removed from `{diff.path}`")
        for name in sorted(removed - added)
    ]


def find_violations(
    files: list[FileDiff], shrink_ok: Callable[[str], bool] = lambda _path: False
) -> list[Finding]:
    """Return every blocking guard violation in the diff, without duplicates.

    Changes to other protected files are no violations; `protected_changes` lists them
    for the reviewer.

    Args:
        files: Parsed diff.
        shrink_ok: True for a ratchet baseline whose change only removes debt; such a
            change needs no label.

    Returns:
        The violations in diff order.
    """
    findings = [
        finding
        for diff in files
        for finding in (
            *baseline_findings(diff, shrink_ok),
            *suppression_findings(diff),
            *test_removal_findings(diff),
        )
    ]
    return list(dict.fromkeys(findings))


def split_by_grant(
    findings: list[Finding], granted: set[str]
) -> tuple[list[Finding], list[Finding]]:
    """Split findings into blocking and allowed ones.

    Args:
        findings: Guard violations.
        granted: Override labels the maintainer has set on the pull request.

    Returns:
        (blocking, allowed): a finding is allowed only if its own label is granted.
    """
    blocking = [finding for finding in findings if finding.label not in granted]
    allowed = [finding for finding in findings if finding.label in granted]
    return blocking, allowed


def render_report(
    blocking: list[Finding],
    allowed: list[Finding],
    protected: list[str],
    maintainer: str,
    *,
    by_maintainer: bool,
) -> str:
    """Markdown report for the job log and the step summary.

    Args:
        blocking: Violations without the maintainer's override label.
        allowed: Violations the maintainer allowed.
        protected: Changed protected files (reported, never blocking).
        maintainer: Login of the maintainer.
        by_maintainer: The pull request is authored by the maintainer.

    Returns:
        The report.
    """
    lines = ["## Agent guard", ""]
    if not blocking and not allowed:
        lines.append("No suppressions, removed tests or grown baselines. ✅")
    lines += [
        f"- ❌ {finding.message} — needs label `{finding.label}` from @{maintainer}"
        for finding in blocking
    ]
    lines += [f"- ✅ {finding.message} — allowed by `{finding.label}`" for finding in allowed]
    if protected:
        lines += ["", PROTECTED_NOTE, "", *(f"- `{path}`" for path in protected)]
    if blocking and by_maintainer:
        lines += ["", "PR is authored by the maintainer: reported only, not failing."]
    return "\n".join(lines)


def exit_code(blocking: list[Finding], *, by_maintainer: bool) -> int:
    """1 if blocking violations remain in a pull request not authored by the maintainer."""
    return 1 if blocking and not by_maintainer else 0


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

    files = parse_diff(diff_text)
    findings = find_violations(files, shrink_ok)

    granted: set[str] = set()
    token = os.environ.get("GITHUB_TOKEN", "")
    if findings and token:
        api = f"https://api.github.com/repos/{args.repo}/issues/{args.pr}"
        issue = github_get(api, token)
        current = {label["name"] for label in issue.get("labels", [])}
        events = github_get(f"{api}/events?per_page=100", token)
        granted = labels_granted_by(events, current, args.maintainer)

    blocking, allowed = split_by_grant(findings, granted)
    by_maintainer = args.author.lower() == args.maintainer.lower()
    report = render_report(
        blocking, allowed, protected_changes(files), args.maintainer, by_maintainer=by_maintainer
    )
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(report + "\n")

    return exit_code(blocking, by_maintainer=by_maintainer)


if __name__ == "__main__":
    sys.exit(main())
