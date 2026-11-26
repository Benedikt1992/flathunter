#!/usr/bin/env python3
"""Mutation testing for the source files a branch changed (mutmut), as a report.

Mutation testing changes the code in small ways ("mutants") and checks whether the
tests notice. A *surviving* mutant means the tests do not really check that code.
Agents tend to write tests that run code without asserting much; this exposes that.

Usage (CI and locally, needs requirements-dev.txt):
    python .github/scripts/mutation_report.py --base origin/fork-main [--out DIR]

Mutated are the changed modules under flathunter/ (tested by test/) and the changed
scripts .github/scripts/*.py without test_*.py (tested by the tests in .github/scripts/).
The scripts run in a second mutmut pass inside a temporary copy of that folder: their
tests import them as top-level modules (`import agent_ops`), and mutmut only activates
a mutant when the module name derived from the path matches that name.
Survivors are split into those in functions the branch changed (relevant for review)
and others in the same file (legacy). Tests that already fail without mutation (e.g.
ones needing network) are deselected so they cannot hide results. The report never
fails the build; the independent reviewer treats survivors in changed functions as
weak tests.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
SCRIPTS_PREFIX = ".github/scripts/"
MAX_SHOWN = 30
MUTMUT_ATTEMPTS = 3


def run(
    cmd: list[str], timeout: int | None = None, cwd: Path = ROOT
) -> subprocess.CompletedProcess[str]:
    """Run a command, by default in the repository root."""
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )


def _changed_files(base: str) -> list[str]:
    """Added or modified files that differ from the merge base."""
    out = run(["git", "diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD"]).stdout
    return out.split()


def changed_sources(base: str) -> list[str]:
    """Python modules under flathunter/ that differ from the merge base."""
    files = _changed_files(base)
    return sorted(p for p in files if p.startswith("flathunter/") and p.endswith(".py"))


def changed_scripts(base: str) -> list[str]:
    """Changed scripts directly in .github/scripts/ (no test_*.py, no subfolders)."""
    found: list[str] = []
    for path in _changed_files(base):
        name = path.removeprefix(SCRIPTS_PREFIX)
        is_script = path.startswith(SCRIPTS_PREFIX) and name.endswith(".py")
        if is_script and "/" not in name and not name.startswith("test_"):
            found.append(path)
    return sorted(found)


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


def mutant_location(name: str, prefix: str = "") -> tuple[str, str]:
    """Map a mutmut 3 mutant name to (module path, Class.method or function).

    `prefix` is put in front of the module path (e.g. `.github/scripts/`).
    """
    base = name.rsplit("__mutmut_", 1)[0]
    module, _, func = base.rpartition(".")
    path = prefix + module.replace(".", "/") + ".py"
    # mutmut names methods "xǁClassǁmethod" and functions "x_function"
    if func.startswith("xǁ"):
        return path, func.removeprefix("xǁ").replace("ǁ", ".")
    return path, func.removeprefix("x_")


def failing_tests(cwd: Path, targets: tuple[str, ...] = ()) -> list[str]:
    """Node ids of tests that fail before any mutation (run in `cwd`)."""
    cmd = [sys.executable, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider"]
    out = run([*cmd, "-m", "not live", *targets], cwd=cwd).stdout
    return sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.MULTILINE)))


def also_copy() -> list[str]:
    """Everything the tests import or read besides the mutated files."""
    extra = [*sorted(p.name for p in ROOT.glob("*.py")), "config.yaml.dist"]
    return ["flathunter/", "test/", *[name for name in extra if (ROOT / name).exists()]]


def mutmut_section(
    paths: list[str],
    deselect: list[str],
    *,
    copy: list[str] | None = None,
    selection: list[str] | None = None,
) -> str:
    """A [tool.mutmut] section mutating `paths` and skipping `deselect`.

    `copy` (files the tests need) and `selection` (tests to run) default to the
    setup for flathunter/.
    """
    args: list[str] = ["-m", "not live"]  # never mutate against the real portals
    for node in deselect:
        args += ["--deselect", node]
    return (
        "\n[tool.mutmut]\n"
        f"source_paths = {json.dumps(paths)}\n"
        f"also_copy = {json.dumps(also_copy() if copy is None else copy)}\n"
        f"pytest_add_cli_args_test_selection = {json.dumps(['test/'] if selection is None else selection)}\n"
        f"pytest_add_cli_args = {json.dumps(args)}\n"
    )


@contextmanager
def temporary_config(original: str) -> Iterator[Callable[[str], None]]:
    """Yield a writer for a temporary [tool.mutmut] section; restore pyproject.toml afterwards.

    mutmut needs the section for `run` as well as for `results` and `show`.
    """

    def write(section: str) -> None:
        PYPROJECT.write_text(original + section, encoding="utf-8")

    try:
        yield write
    finally:
        PYPROJECT.write_text(original, encoding="utf-8")


@contextmanager
def scripts_workspace() -> Iterator[tuple[Path, Callable[[str], None]]]:
    """Yield (directory, config writer) for a temporary copy of .github/scripts/*.py.

    Mutating inside the copy gives the scripts top-level module names that match how
    their tests import them. Tests that read `../workflows` relative to their file find
    a copy of .github/workflows both in the baseline run (tests in the copy) and in
    mutmut's runs (tests in its `mutants/` folder inside the copy). The repository
    itself is never written to.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "scripts"
        work.mkdir()
        for source in (ROOT / SCRIPTS_PREFIX).glob("*.py"):
            shutil.copy2(source, work / source.name)
        for workflows in (work.parent / "workflows", work / "workflows"):
            shutil.copytree(ROOT / ".github" / "workflows", workflows)

        def write(section: str) -> None:
            (work / "pyproject.toml").write_text(section, encoding="utf-8")

        yield work, write


def run_mutmut(
    paths: list[str],
    deselect: list[str],
    timeout: int,
    write: Callable[[str], None],
    cwd: Path,
    make_section: Callable[[list[str], list[str]], str] = mutmut_section,
) -> str:
    """Run mutmut in `cwd`; return "" if it finished, otherwise a warning for the report.

    Tests that only fail inside mutmut's sandbox (e.g. path-dependent ones) abort its
    baseline run; they are added to `deselect` and the run is retried. A failure that
    names no test (e.g. an incompatible mutmut/pytest pair) is reported, never taken
    for an empty result.
    """
    try:
        for _attempt in range(MUTMUT_ATTEMPTS):
            write(make_section(paths, deselect))
            result = run(["mutmut", "run", "--max-children", "4"], timeout=timeout, cwd=cwd)
            if result.returncode == 0:
                return ""
            output = result.stdout + result.stderr
            failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", output, re.MULTILINE)
            if not failed:
                return f"mutmut failed (exit code {result.returncode}): {last_line(output)}"
            deselect.extend(node for node in failed if node not in deselect)
    except subprocess.TimeoutExpired:
        return f"mutmut stopped after {timeout}s; partial results."
    return f"mutmut still failed after {MUTMUT_ATTEMPTS} attempts; results are incomplete."


def last_line(output: str) -> str:
    """The last non-empty line of a command's output, for a one-line warning."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return f"`{lines[-1]}`" if lines else "no output"


def classify(
    results: str, touched: dict[str, set[str]], prefix: str = ""
) -> tuple[dict[str, Counter[str]], list[str]]:
    """Count statuses per file and collect survivors in changed functions."""
    statuses: dict[str, Counter[str]] = {path: Counter() for path in touched}
    relevant: list[str] = []
    for line in results.splitlines():
        if ":" not in line:
            continue
        name, status = (part.strip() for part in line.rsplit(":", 1))
        path, func = mutant_location(name, prefix)
        if path in statuses:
            statuses[path][status] += 1
            if status == "survived" and func in touched[path]:
                relevant.append(name)
    return statuses, relevant


class Group(NamedTuple):
    """Result of one mutmut pass."""

    statuses: dict[str, Counter[str]]
    relevant: list[str]
    deselect: list[str]
    details: list[str]
    warning: str


def render_survivors(relevant: list[str], cwd: Path) -> list[str]:
    """Markdown details (`mutmut show`, run in `cwd`) of survivors in changed functions."""
    lines: list[str] = []
    for name in relevant[:MAX_SHOWN]:
        diff = run(["mutmut", "show", name], cwd=cwd).stdout.strip()
        lines += ["", f"<details><summary><code>{name}</code></summary>", ""]
        lines += ["```diff", diff, "```", "</details>"]
    return lines


def render(groups: list[Group]) -> list[str]:
    """Markdown report lines for all passes."""
    lines = [
        "| File | killed | survived | no tests | timeout | score |",
        "|---|---|---|---|---|---|",
    ]
    for group in groups:
        for path, counts in group.statuses.items():
            killed, survived = counts["killed"], counts["survived"]
            score = f"{killed / (killed + survived):.0%}" if killed + survived else "-"
            lines.append(
                f"| `{path}` | {killed} | {survived} | {counts['no tests']} | "
                f"{counts['timeout']} | {score} |"
            )
    relevant = [name for group in groups for name in group.relevant]
    lines += ["", f"**Surviving mutants in changed functions: {len(relevant)}**", ""]
    if relevant:
        lines.append("Each one is a code change the tests did not notice:")
    for group in groups:
        lines += group.details
    deselected = sum(len(group.deselect) for group in groups)
    if deselected:
        lines += ["", f"_{deselected} test(s) failing before mutation were deselected._"]
    for group in groups:
        if group.warning:
            lines += ["", f"**Warning:** {group.warning}"]
    return lines


def _touched(base: str, paths: list[str]) -> dict[str, set[str]]:
    """Changed functions per file."""
    return {
        path: functions_touching(
            (ROOT / path).read_text(encoding="utf-8"), changed_lines(base, path)
        )
        for path in paths
    }


def _collect(
    touched: dict[str, set[str]],
    deselect: list[str],
    cwd: Path,
    prefix: str,
    warning: str,
) -> Group:
    """Read the results of a finished mutmut pass (its config must still be in place)."""
    results = run(["mutmut", "results", "--all", "true"], cwd=cwd).stdout
    statuses, relevant = classify(results, touched, prefix)
    return Group(statuses, relevant, deselect, render_survivors(relevant, cwd), warning)


def mutate_flathunter(base: str, paths: list[str], timeout: int) -> Group:
    """Mutate changed flathunter/ modules against test/."""
    touched = _touched(base, paths)
    deselect = failing_tests(ROOT)
    with temporary_config(PYPROJECT.read_text(encoding="utf-8")) as write:
        warning = run_mutmut(paths, deselect, timeout, write, ROOT)
        return _collect(touched, deselect, ROOT, "", warning)


def mutate_scripts(base: str, paths: list[str], timeout: int) -> Group:
    """Mutate changed .github/scripts/*.py against the tests in .github/scripts/."""
    touched = _touched(base, paths)
    with scripts_workspace() as (work, write):
        deselect = failing_tests(work, (".",))
        names = [Path(path).name for path in paths]
        # the mutated files must not also be copied unmutated
        copy = sorted(p.name for p in work.glob("*.py") if p.name not in names)
        make_section = partial(mutmut_section, copy=copy, selection=["."])
        warning = run_mutmut(names, deselect, timeout, write, work, make_section)
        return _collect(touched, deselect, work, SCRIPTS_PREFIX, warning)


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
    sources, scripts = changed_sources(args.base), changed_scripts(args.base)
    groups: list[Group] = []
    # One time budget for both passes, so the job limit holds and the report is written.
    started = time.monotonic()
    first_budget = args.timeout // 2 if sources and scripts else args.timeout
    if sources:
        groups.append(mutate_flathunter(args.base, sources, first_budget))
    if scripts:
        remaining = max(1, args.timeout - int(time.monotonic() - started))
        groups.append(mutate_scripts(args.base, scripts, remaining))
    if not groups:
        lines.append(
            "No changed modules under `flathunter/` or scripts under `.github/scripts/`; "
            "nothing to mutate."
        )
    else:
        lines += render(groups)
        summary = {
            "files": {path: dict(c) for g in groups for path, c in g.statuses.items()},
            "changed_function_survivors": [name for g in groups for name in g.relevant],
            "deselected": [node for g in groups for node in g.deselect],
        }

    report = "\n".join(lines) + "\n"
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
