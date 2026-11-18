# Working on flathunter

Read `ARCHITECTURE.md` before changing code. It defines the components, the
dependency rules (enforced by `lint-imports`) and the conventions.

## Setup and commands

```bash
pipenv requirements --dev > requirements.txt && pip install -r requirements.txt -r requirements-dev.txt
prek install                              # optional: run the gates as git pre-commit hook

python -m pytest -q                       # tests (no network; use requests-mock + fixtures)
python -m pytest test/test_hunter.py -q   # a single test file
prek run --all-files                      # ALL static gates (~1 s): what CI runs
ruff check <file> / ruff format <file>    # lint / format a single file
pyrefly check                             # strict type check
python .github/scripts/mutation_report.py --base origin/fork-main   # do my tests catch bugs?
```

Run the tests and `prek run --all-files` before every commit. In Claude Code the
project hooks (`.claude/settings.json`) do this automatically: every edited Python
file is formatted and linted, and before you finish all gates run once.

## Quality gates and ratchets

The tooling is strict (Ruff with all rules, Pyrefly strict, cognitive complexity ≤ 15),
but legacy code is grandfathered: existing violations are frozen in baselines
(`quality/*.json`, `complexipy-snapshot.json`). The rule is simple:

- **New or changed code must be clean.** A new violation anywhere fails, even in a
  legacy file. Fix it; never add it to a baseline.
- **Debt may only go down.** If you remove legacy violations, the gates ask you to run
  `python .github/scripts/ratchet.py prune`, which shrinks the baselines. Commit that.
  Baselines can never grow without the maintainer (the guard enforces this).
- Formatting: new files and already-formatted files must be `ruff format`-clean. Do not
  reformat untouched legacy files (that creates noise and upstream merge conflicts).

## Rules for every change

1. **Scope:** implement exactly what the issue's acceptance criteria ask for.
   No drive-by refactors, renames or formatting changes outside the touched code.
   Record anything else you notice as a follow-up in the PR description.
2. **Tests first:** start with a test that fails because the behaviour is missing,
   then implement. Every acceptance criterion maps to at least one test. Tests must
   assert behaviour, not just execute code: mutation testing (`mutation_report.py`)
   shows code your tests don't really check, and the reviewer uses it. For pure
   functions (parsers, filters, formatting) add Hypothesis property tests
   (see `test/test_list_properties.py`). No network access in tests.
3. **Architecture:** respect the dependency rules in `ARCHITECTURE.md`. If the
   issue cannot be solved within them, stop and say so instead of working around them.
4. **Never weaken a gate.** Do not edit `.github/`, `.claude/`, `pyproject.toml`,
   `.pre-commit-config.yaml`, `requirements-dev.txt`, `.importlinter`, `.coveragerc`,
   `quality/` (except `prune`), `CLAUDE.md`, `ARCHITECTURE.md`, `docs/adr/`.
   Do not add suppressions: `# noqa`, `# ruff: noqa`, `# type: ignore`,
   `# pyrefly: ignore`, `# pylint: disable`, `# nosec`, `# fmt: off/skip`,
   `pragma: no cover`, `pragma: no mutate`, `pytest.mark.skip`/`xfail`; and do not
   delete or loosen existing tests. CI (`agent_guard.py`) rejects these unless the
   maintainer explicitly allows them.
5. **Dependencies:** do not add or upgrade packages (`Pipfile`, `Pipfile.lock`,
   `setup.py`, `requirements-dev.txt`) unless the issue explicitly asks for it.
6. **Security:** no secrets in code, tests or logs; validate external input
   (HTML from portals, user settings from the web UI); use timeouts on network calls;
   never use `shell=True`, `eval`, `pickle` on untrusted data, or disable TLS verification.
   Ruff's security rules (S) enforce much of this.
7. **Style:** fully type-annotated, Google-style docstrings on public classes and
   functions, small functions (Ruff + complexipy enforce this for new code).
8. **Commits:** small, focused commits with imperative messages that reference the
   issue (`Add X to Y (#123)`). Never rewrite history on a shared branch.
9. **When unsure:** do not guess requirements. Report open questions; the maintainer
   answers them.

## Agent workflow

The automated issue → PR process (labels, review loop, limits) is described in
`docs/agent-workflow.md`. Agent role prompts live in `.github/agents/`. Tooling
decisions: `docs/adr/0002-python-quality-toolchain.md`.
