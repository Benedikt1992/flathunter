#!/usr/bin/env python3
"""Compare two `pip-audit -f json` reports and fail on newly introduced vulnerabilities.

The base branch may already contain known vulnerabilities; those are reported but do
not block a pull request. Only (package, vulnerability id) pairs that appear in the
head report but not in the base report fail the check.
"""
from __future__ import annotations

import json
import os
import sys


def vulns(report: dict) -> dict[tuple[str, str], str]:
    """Map (package, vuln id) -> 'version -> fix versions' for every reported vulnerability."""
    found = {}
    for dep in report.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            fixes = ", ".join(vuln.get("fix_versions") or []) or "no fix"
            found[(dep["name"].lower(), vuln["id"])] = f"{dep.get('version', '?')} (fix: {fixes})"
    return found


def main(base_path: str, head_path: str) -> int:
    """Print a summary and return 1 if the head introduces new vulnerabilities."""
    with open(base_path, encoding="utf-8") as handle:
        base = vulns(json.load(handle))
    with open(head_path, encoding="utf-8") as handle:
        head = vulns(json.load(handle))

    new = sorted(set(head) - set(base))
    fixed = sorted(set(base) - set(head))
    lines = ["## Dependency audit", ""]
    lines.append(f"Known vulnerabilities on base: {len(base)} · on this PR: {len(head)}")
    for name, vuln_id in new:
        lines.append(f"- ❌ new: `{name}` {head[(name, vuln_id)]} — {vuln_id}")
    for name, vuln_id in fixed:
        lines.append(f"- ✅ fixed: `{name}` — {vuln_id}")
    if not new:
        lines.append("")
        lines.append("No newly introduced vulnerabilities. ✅")
    report = "\n".join(lines)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report + "\n")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
