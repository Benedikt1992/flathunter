# Working on flathunter

Read `ARCHITECTURE.md` before changing code. It defines the components, the
dependency rules (enforced by `lint-imports`) and the conventions.

## Commands

```bash
pipenv requirements > requirements.txt && pip install -r requirements.txt   # dependencies
pip install import-linter diff-cover                                       # extra gate tools
python -m pytest -q                       # tests (no network; use requests-mock + fixtures)
python -m pytest test/test_hunter.py -q   # a single test file
coverage run && coverage xml              # tests with coverage (config in .coveragerc)
pylint flathunter                         # lint (conventions and errors fail CI)
pyright                                   # type check
lint-imports                              # architecture contracts (.importlinter)
bandit -r flathunter -ll                  # security lint, medium+ severity
```

Run tests, pylint, pyright and lint-imports before every commit.

## Rules for every change

1. **Scope:** implement exactly what the issue's acceptance criteria ask for.
   No drive-by refactors, renames or formatting changes outside the touched code.
   Record anything else you notice as a follow-up in the PR description.
2. **Tests first:** start with a test that fails because the behaviour is missing,
   then implement. Every acceptance criterion maps to at least one test. Tests must
   assert behaviour, not just execute code. No network access in tests.
3. **Architecture:** respect the dependency rules in `ARCHITECTURE.md`. If the
   issue cannot be solved within them, stop and say so instead of working around them.
4. **Never weaken a gate.** Do not edit `.github/`, `.importlinter`, `.pylintrc`,
   `pyrightconfig.json`, `.coveragerc`, `CLAUDE.md`, `ARCHITECTURE.md`, `docs/adr/`.
   Do not add `# pylint: disable`, `# type: ignore`, `# pyright: ignore`,
   `# noqa`, `# nosec`, `pragma: no cover`, `pytest.mark.skip`/`xfail`, and do
   not delete or loosen existing tests. CI (`agent_guard.py`) rejects these
   unless the maintainer explicitly allows them.
5. **Dependencies:** do not add or upgrade packages (`Pipfile`, `Pipfile.lock`,
   `setup.py`) unless the issue explicitly asks for it.
6. **Security:** no secrets in code, tests or logs; validate external input
   (HTML from portals, user settings from the web UI); use timeouts on network calls;
   never use `shell=True`, `eval`, `pickle` on untrusted data, or disable TLS verification.
7. **Style:** match the surrounding code; type-annotate new and changed functions;
   docstrings on public classes/functions (pylint enforces this).
8. **Commits:** small, focused commits with imperative messages that reference the
   issue (`Add X to Y (#123)`). Never rewrite history on a shared branch.
9. **When unsure:** do not guess requirements. Report open questions; the maintainer
   answers them.

## Agent workflow

The automated issue → PR process (labels, review loop, limits) is described in
`docs/agent-workflow.md`. Agent role prompts live in `.github/agents/`.
