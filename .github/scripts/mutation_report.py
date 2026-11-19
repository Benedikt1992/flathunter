#!/usr/bin/env python3
"""Mutation testing for the source files a branch changed (mutmut), as a report.

Mutation testing changes the code in small ways ("mutants") and checks whether the
tests notice. A *surviving* mutant means the tests do not really check that code.
Agents tend to write tests that run code without asserting much; this exposes that.

Usage (CI and locally, needs requirements-dev.txt):
    python .github/scripts/mutation_report.py --base origin/fork-main [--out DIR]

Only changed modules under flathunter/ are mutated. Survivors are split into those in
functions the branch changed (relevant for review) and others in the same file
(legacy). Tests that already fail without mutation (e.g. ones needing network) are
deselected so they cannot hide results. The report never fails the build; the
independent reviewer treats survivors in changed functions as weak tests.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
MAX_SHOWN = 30


def run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command in the repository root."""
    return subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False
    )


def changed_sources(base: str) -> list[str]:
    """Python modules under flathunter/ that differ from the merge base."""
    out = run(["git", "diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD"]).stdout
    return sorted(p for p in out.split() if p.startswith("flathunter/") and p.endswith(".py"))


def changed_lines(base: str, path: str) -> set[int]:
    """Line numbers (new version) touched in a file."""
    diff = run(["git", "diff", "--unified=0", f"{base}...HEAD", "--", path]).stdout
    lines: set[int] = set()
    for match in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", diff, re.MULTILINE):
        start, count = int(match.group(1)), int(match.group(2) or "1")
        lines.update(range(start, start + max(count, 1)))
    return lines


def functions_touching(source: str, lines: set[int]) -> set[str]:
    """Qualified names (Class.method / function) whose body contains a changed line."""
    names: set[str] = set()

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                span = set(range(child.lineno, (child.end_lineno or child.lineno) + 1))
                if span & lines:
                    names.add(f"{prefix}{child.name}")
                visit(child, f"{prefix}{child.name}.")

    visit(ast.parse(source), "")
    return names


def mutant_location(name: str) -> tuple[str, str]:
    """Map a mutmut 3 mutant name to (module path, Class.method or function)."""
    base = name.rsplit("__mutmut_", 1)[0]
    module, _, func = base.rpartition(".")
    # mutmut names methods "xǁClassǁmethod" and functions "x_function"
    func = func.removeprefix("xǁ").replace("ǁ", ".") if func.startswith("xǁ") else func
    func = func.removeprefix("x_")
    return module.replace(".", "/") + ".py", func


def failing_tests() -> list[str]:
    """Node ids of tests that fail before any mutation."""
    out = run(
        [sys.executable, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", "-m", "not live"]
    ).stdout
    return sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.MULTILINE)))


def also_copy() -> list[str]:
    """Everything the tests import or read besides the mutated files."""
    extra = [*sorted(p.name for p in ROOT.glob("*.py")), "config.yaml.dist"]
    return ["flathunter/", *[name for name in extra if (ROOT / name).exists()]]


def mutmut_section(paths: list[str], deselect: list[str]) -> str:
    """A [tool.mutmut] section mutating `paths` and skipping `deselect`."""
    args: list[str] = ["-m", "not live"]  # never mutate against the real portals
    for node in deselect:
        args += ["--deselect", node]
    return (
        "\n[tool.mutmut]\n"
        f"paths_to_mutate = {json.dumps(paths)}\n"
        'tests_dir = ["test/"]\n'
        f"also_copy = {json.dumps(also_copy())}\n"
        f"pytest_add_cli_args = {json.dumps(args)}\n"
    )


def run_mutmut(paths: list[str], deselect: list[str], timeout: int) -> bool:
    """Run mutmut with a temporary config; False if it timed out.

    Tests that only fail inside mutmut's sandbox (e.g. path-dependent ones) abort its
    baseline run; they are added to `deselect` and the run is retried.
    """
    original = PYPROJECT.read_text(encoding="utf-8")
    try:
        for _attempt in range(3):
            PYPROJECT.write_text(original + mutmut_section(paths, deselect), encoding="utf-8")
            result = run(["mutmut", "run", "--max-children", "4"], timeout=timeout)
            if result.returncode == 0:
                return True
            output = result.stdout + result.stderr
            failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", output, re.MULTILINE)
            if not failed:
                return True
            deselect.extend(node for node in failed if node not in deselect)
    except subprocess.TimeoutExpired:
        return False
    finally:
        PYPROJECT.write_text(original, encoding="utf-8")
    return True


def classify(
    results: str, touched: dict[str, set[str]]
) -> tuple[dict[str, Counter[str]], list[str]]:
    """Count statuses per file and collect survivors in changed functions."""
    statuses: dict[str, Counter[str]] = {path: Counter() for path in touched}
    relevant: list[str] = []
    for line in results.splitlines():
        if ":" not in line:
            continue
        name, status = (part.strip() for part in line.rsplit(":", 1))
        path, func = mutant_location(name)
        if path in statuses:
            statuses[path][status] += 1
            if status == "survived" and func in touched[path]:
                relevant.append(name)
    return statuses, relevant


def render(statuses: dict[str, Counter[str]], relevant: list[str], deselected: int) -> list[str]:
    """Markdown report lines."""
    lines = [
        "| File | killed | survived | no tests | timeout | score |",
        "|---|---|---|---|---|---|",
    ]
    for path, counts in statuses.items():
        killed, survived = counts["killed"], counts["survived"]
        score = f"{killed / (killed + survived):.0%}" if killed + survived else "-"
        lines.append(
            f"| `{path}` | {killed} | {survived} | {counts['no tests']} | "
            f"{counts['timeout']} | {score} |"
        )
    lines += ["", f"**Surviving mutants in changed functions: {len(relevant)}**", ""]
    if relevant:
        lines.append("Each one is a code change the tests did not notice:")
    for name in relevant[:MAX_SHOWN]:
        diff = run(["mutmut", "show", name]).stdout.strip()
        lines += ["", f"<details><summary><code>{name}</code></summary>", ""]
        lines += ["```diff", diff, "```", "</details>"]
    if deselected:
        lines += ["", f"_{deselected} test(s) failing before mutation were deselected._"]
    return lines


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="base ref, e.g. origin/fork-main")
    parser.add_argument("--out", default="mutation-report", help="output directory")
    parser.add_argument("--timeout", type=int, default=1200, help="seconds for mutmut run")
    args = parser.parse_args()
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    lines = ["## Mutation testing", ""]
    summary: dict[str, object] = {}
    paths = changed_sources(args.base)
    if not paths:
        lines.append("No changed modules under `flathunter/`; nothing to mutate.")
    else:
        touched = {
            path: functions_touching(
                (ROOT / path).read_text(encoding="utf-8"), changed_lines(args.base, path)
            )
            for path in paths
        }
        deselect = failing_tests()
        if not run_mutmut(paths, deselect, args.timeout):
            lines.append(f"Warning: mutmut stopped after {args.timeout}s; results are partial.")
        statuses, relevant = classify(run(["mutmut", "results", "--all", "true"]).stdout, touched)
        lines += render(statuses, relevant, len(deselect))
        summary = {
            "files": {path: dict(counts) for path, counts in statuses.items()},
            "changed_function_survivors": relevant,
            "deselected": deselect,
        }

    report = "\n".join(lines) + "\n"
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
