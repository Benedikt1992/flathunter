# 0002: Python quality toolchain with ratchets

- **Status:** accepted
- **Date:** 2026-11-18

## Context
ADR 0001 introduced enforced quality gates for agent-written code. The first version
used pylint, pyright (basic) and bandit. For an agent factory the gates must be fast
enough to run inside the agent loop, strict for new code, and must not force a
big-bang cleanup. This is a fork that regularly merges upstream, so mass reformatting
or mass fixes of legacy code would cause constant merge conflicts.

Measured on the code base at this point (Python 3.11, locked dependencies):

| Tool | Legacy findings |
|---|---|
| Ruff, all rules | ~1,580 (after excluding formatter-conflicting rules) |
| `ruff format` | 75 of 85 files would change |
| Pyrefly strict | 268 errors (default preset: 16) |
| complexipy > 15 | 4 functions (max. 23) |
| vulture ≥ 60 % | 48 findings, almost all false positives (pydantic fields, Flask routes) |
| deptry | 1 real issue plus 14 declared-but-unused packages (test tools in `[packages]`) |
| mutmut on `filter.py` | 154 killed, 28 survived |

## Decision
1. **Ruff replaces pylint and bandit.** It has all rules enabled (`select = ["ALL"]`)
   except those that conflict with the formatter. Its security rules (S) replace bandit,
   and PGH003/PGH004/RUF100 require specific suppression codes. Ruff is fast enough
   (milliseconds) for per-edit checks in the agent loop.
2. **Pyrefly (strict preset) replaces pyright.** Its built-in baseline is
   line-independent, and `--error-stale-baseline` locks in fixed errors.
   We did not choose ty because it is still beta (0.0.x). We did not choose
   `mypy --strict` because it is slower and has no comparable baseline.
3. **Ratchet instead of big bang.**
   - **Ruff** uses a line-independent baseline (`quality/ruff-baseline.json`,
     path + rule + message, counted).
   - **Formatting** keeps a list of legacy files (`quality/format-baseline.json`).
   - **Complexity** uses complexipy's snapshot.
   - New debt fails everywhere. Paid-off debt must be pruned, so baselines shrink.
     The agent guard rejects any growth without the maintainer's label.
   - New and changed code therefore meets the full standard, and legacy code improves
     whenever it is touched.
4. **Same checks in three places.** `.pre-commit-config.yaml`, run by prek (a
   pre-commit-compatible runner written in Rust), defines the gates once. Claude Code
   hooks run them in the agent loop, git runs them as a pre-commit hook, and CI runs
   them in `quality.yml`. Tool versions are pinned in `requirements-dev.txt`.
5. **Test effectiveness.**
   - diff-cover stays (coverage of changed lines ≥ 80 %).
   - mutmut runs on changed modules as a report. Survivors in changed functions are
     evidence for the independent reviewer, who rates them `major`. We did not make it
     a hard gate because the suite still contains network-dependent tests, and
     equivalent mutants need judgement.
   - Hypothesis is available for property tests.
6. **Also adopted:** complexipy (cognitive complexity) next to Ruff's cyclomatic rules
   (C901, PLR09xx), and deptry (imports must match declared dependencies). DEP002 is
   not enforced until test tools move to `[dev-packages]`.

### Not adopted (for now)
- **vulture:** at a useful confidence level it produced mostly false positives. Ruff
  covers unused imports, variables and arguments.
- **Tach:** import-linter already enforces the architecture and is actively maintained.
  We may revisit Tach for public-interface enforcement.
- **pyscn:** it overlaps with Ruff, complexipy and import-linter.
- **Semgrep, osv-scanner:** CodeQL plus Ruff S and pip-audit cover the current needs.
  Add Semgrep when project-specific rules are needed.
- **SonarQube, CodeScene, Qlty:** these need external accounts or services. They can be
  added later as additional signals; they must not replace the in-repo gates.
- **Additional AI reviewers:** the independent Claude reviewer exists (ADR 0001). Any
  further vendor should first be evaluated on this repository's own PRs.

## Consequences
- pylint, pyright and bandit configs and workflows are removed. pylint and pyright
  remain in `Pipfile` until the dev-dependency split (see deptry follow-up).
- Static gates take about 1 s locally, and mutation reports take 30–60 s per changed module.
- Baseline files change only by shrinking. Re-initialising them
  (`ratchet.py init`) is a maintainer decision.
- Pyrefly results depend on the installed dependency versions. CI and the agent
  workflows use Python 3.11 with the locked dependencies, which is also how the
  baseline was generated.
