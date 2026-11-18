#!/usr/bin/env python3
"""Ratchet for quality metrics: legacy debt is frozen in baselines, new debt fails.

Baselines live in ``quality/`` (plus ``complexipy-snapshot.json`` at the repo root) and
are *line independent*, so moving code around does not create false positives.

Commands
  ruff [FILES...]     Fail on Ruff violations not covered by quality/ruff-baseline.json.
  format [FILES...]   Fail on files needing `ruff format` that are not in quality/format-baseline.json.
  deptry              Run deptry against the top-level dependencies declared in Pipfile.
  prune               Shrink all baselines to the current state (never grows them).
  init                Maintainer only: (re)create all baselines from scratch.

Without FILES the whole project is checked and *stale* baseline entries (debt that was
paid off) also fail, so improvements are locked in by running `prune`.

The agent guard uses `is_shrink()` to allow baseline changes that only remove debt.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[2]
RUFF_BASELINE = ROOT / "quality" / "ruff-baseline.json"
FORMAT_BASELINE = ROOT / "quality" / "format-baseline.json"
PYREFLY_BASELINE = ROOT / "quality" / "pyrefly-baseline.json"
COMPLEXIPY_SNAPSHOT = ROOT / "complexipy-snapshot.json"
COMPLEXIPY_ARGS = ["complexipy", "flathunter", "--max-complexity-allowed", "15", "--plain"]

RATCHET_FILES = {
    "quality/ruff-baseline.json": "ruff",
    "quality/format-baseline.json": "format",
    "quality/pyrefly-baseline.json": "pyrefly",
    "complexipy-snapshot.json": "complexipy",
}


# ------------------------------------------------------------------ fingerprints


def ruff_fingerprints(violations: list[dict]) -> Counter[str]:
    """Line-independent multiset of Ruff violations: path::code::message."""
    counts: Counter[str] = Counter()
    for violation in violations:
        path = Path(violation["filename"]).resolve().relative_to(ROOT).as_posix()
        counts[f"{path}::{violation.get('code') or 'syntax'}::{violation['message']}"] += 1
    return counts


def pyrefly_entries(data: dict) -> Counter[str]:
    """Line-independent multiset of pyrefly baseline errors."""
    return Counter(
        f"{e['path']}::{e['name']}::{e['concise_description']}" for e in data.get("errors", [])
    )


def complexipy_entries(data: list) -> dict[str, int]:
    """Function -> complexity from a complexipy snapshot."""
    return {
        f"{entry['path']}::{fn['name']}": fn["complexity"]
        for entry in data
        for fn in entry.get("functions", [])
    }


def is_shrink(kind: str, base_text: str | None, head_text: str | None) -> bool:
    """True if the head version of a ratchet file only removes debt compared to base."""
    if head_text is None:  # deleted: fine only if nothing is left to track
        return base_text is None
    if base_text is None:  # newly created baseline: maintainer decision
        return False
    base, head = json.loads(base_text), json.loads(head_text)
    if kind == "ruff":
        return all(count <= base.get(key, 0) for key, count in head.items())
    if kind == "format":
        return set(head) <= set(base)
    if kind == "pyrefly":
        return not (pyrefly_entries(head) - pyrefly_entries(base))
    if kind == "complexipy":
        base_c, head_c = complexipy_entries(base), complexipy_entries(head)
        return all(key in base_c and value <= base_c[key] for key, value in head_c.items())
    return False


# ------------------------------------------------------------------ helpers


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command in the repository root."""
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=check)


def load_json(path: Path, default):
    """Read a JSON file or return the default."""
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def dump_json(path: Path, data) -> None:
    """Write stable, diff-friendly JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def rel(files: list[str]) -> list[str]:
    """Normalise file arguments to repo-relative posix paths."""
    return [Path(f).resolve().relative_to(ROOT).as_posix() for f in files]


def current_ruff(files: list[str]) -> tuple[Counter[str], list[dict]]:
    """Run Ruff and return fingerprints plus raw violations."""
    result = run(["ruff", "check", "--output-format", "json", "--exit-zero", *(files or ["."])])
    if result.returncode != 0:
        sys.exit(result.stderr)
    violations = json.loads(result.stdout or "[]")
    return ruff_fingerprints(violations), violations


def current_unformatted(files: list[str]) -> set[str]:
    """Files `ruff format` would change."""
    result = run(["ruff", "format", "--check", "--output-format", "json", *(files or ["."])])
    if result.returncode not in (0, 1):
        sys.exit(result.stderr)
    return {
        Path(item["filename"]).resolve().relative_to(ROOT).as_posix()
        for item in json.loads(result.stdout or "[]")
    }


# ------------------------------------------------------------------ commands


def cmd_ruff(files: list[str]) -> int:
    """New Ruff violations fail; with a full run, stale baseline entries fail too."""
    files = rel(files)
    baseline: dict[str, int] = load_json(RUFF_BASELINE, {})
    current, violations = current_ruff(files)
    new_keys = {key for key, count in current.items() if count > baseline.get(key, 0)}
    failed = False
    if new_keys:
        failed = True
        print("New Ruff violations (fix them; the baseline only covers legacy code):")
        for violation in violations:
            key = next(iter(ruff_fingerprints([violation])))
            if key in new_keys:
                loc = violation["location"]
                path = Path(violation["filename"]).resolve().relative_to(ROOT).as_posix()
                print(
                    f"  {path}:{loc['row']}:{loc['column']}: {violation['code']} {violation['message']}"
                )
        print("  (where a file already had this exact violation, one of the listed ones is legacy)")
    if not files:
        stale = sum(
            count - current.get(key, 0)
            for key, count in baseline.items()
            if count > current.get(key, 0)
        )
        if stale:
            failed = True
            print(
                f"{stale} legacy Ruff violation(s) were fixed. Lock in the improvement: "
                "python .github/scripts/ratchet.py prune"
            )
    print("ruff ratchet: " + ("FAILED" if failed else "ok"))
    return 1 if failed else 0


def cmd_format(raw_files: list[str]) -> int:
    """Files outside the legacy list must be ruff-formatted."""
    files = [f for f in rel(raw_files) if f.endswith(".py")]
    if raw_files and not files:
        return 0
    baseline = set(load_json(FORMAT_BASELINE, []))
    current = current_unformatted(files)
    new = sorted(current - baseline)
    failed = False
    if new:
        failed = True
        print("Not formatted (run `ruff format <file>`):")
        for path in new:
            print(f"  {path}")
    if not raw_files:
        stale = sorted(baseline - current)
        if stale:
            failed = True
            print(
                f"{len(stale)} legacy file(s) are now formatted. Lock in: "
                "python .github/scripts/ratchet.py prune"
            )
    print("format ratchet: " + ("FAILED" if failed else "ok"))
    return 1 if failed else 0


def top_level_requirements() -> list[str]:
    """Names of the packages declared in Pipfile [packages]."""
    with open(ROOT / "Pipfile", "rb") as handle:
        return sorted(tomllib.load(handle).get("packages", {}))


def cmd_deptry() -> int:
    """Imports must be covered by declared dependencies."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write("\n".join(top_level_requirements()) + "\n")
    result = run(["deptry", ".", "--requirements-files", handle.name, "--no-ansi"])
    Path(handle.name).unlink()
    print(result.stdout + result.stderr)
    return result.returncode


def cmd_prune() -> int:
    """Shrink baselines to the current state. Never adds entries."""
    baseline: dict[str, int] = load_json(RUFF_BASELINE, {})
    current, _ = current_ruff([])
    pruned = {k: min(c, current.get(k, 0)) for k, c in baseline.items() if current.get(k, 0)}
    dump_json(RUFF_BASELINE, pruned)

    fmt = set(load_json(FORMAT_BASELINE, []))
    dump_json(FORMAT_BASELINE, sorted(fmt & current_unformatted([])))

    result = run(["pyrefly", "check", "--prune-baseline", "--summary=none"])
    print(result.stdout[-2000:] + result.stderr[-2000:])
    print("Baselines pruned. New violations were NOT added; fix them instead.")
    return 0


def cmd_init() -> int:
    """Create all baselines from the current state (maintainer decision)."""
    current, _ = current_ruff([])
    dump_json(RUFF_BASELINE, dict(current))
    dump_json(FORMAT_BASELINE, sorted(current_unformatted([])))
    run(["pyrefly", "check", "--update-baseline", "--summary=none"])
    COMPLEXIPY_SNAPSHOT.unlink(missing_ok=True)
    run([*COMPLEXIPY_ARGS, "--snapshot-create"])
    print(
        f"ruff: {sum(current.values())} legacy violations; "
        f"format: {len(load_json(FORMAT_BASELINE, []))} legacy files; "
        f"pyrefly: {len(load_json(PYREFLY_BASELINE, {}).get('errors', []))} legacy errors; "
        f"complexipy: {len(complexipy_entries(load_json(COMPLEXIPY_SNAPSHOT, [])))} legacy functions"
    )
    return 0


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["ruff", "format", "deptry", "prune", "init"])
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()
    if args.command == "ruff":
        return cmd_ruff(args.files)
    if args.command == "format":
        return cmd_format(args.files)
    if args.command == "deptry":
        return cmd_deptry()
    if args.command == "prune":
        return cmd_prune()
    return cmd_init()


if __name__ == "__main__":
    sys.exit(main())
