# 0001: Agent-driven issue implementation with independent quality control

- **Status:** accepted; decision 1 amended by [0003](0003-review-protected-files-instead-of-label.md)
- **Date:** 2026-11-18

## Context
Issues should be worked on autonomously and iteratively by AI agents. The
maintainer wants clear architecture, tests, security and quality control, with
quality control independent of the implementation, and keeps the final merge.

## Decision
1. **Enforced gates, not just instructions.** Architecture rules
   (`import-linter`), tests, coverage of changed lines, lint, types, SAST, dependency
   audit, secret scanning and CodeQL run in CI on every PR. A guard script rejects
   changes that weaken gates (protected files, suppressions, removed tests) unless the
   maintainer adds an override label.
2. **Separated roles and identities.** Planner, implementer, reviewer and fixer are
   separate runs with separate prompts. The implementer/fixer act as a dedicated GitHub
   App; the reviewer acts as `github-actions[bot]` with read-only repository access, a fresh
   context, the trusted prompt from the default branch and no access to the
   implementer's reasoning. It runs only after all CI gates are green.
3. **Policy in code.** What agents see (only maintainer text and agent output), the
   review verdict (any blocker/major finding or unmet criterion requests changes), round
   limits and label transitions are implemented in `.github/scripts/agent_ops.py`, not in prompts.
4. **The human merges.** Branch protection requires the maintainer's code-owner approval;
   no agent identity can approve or bypass it.
5. **Feedback loop.** "Request changes" by the maintainer triggers a fix round that
   replies to every thread. Feedback that applies beyond one PR is turned into rules
   (`CLAUDE.md`, `ARCHITECTURE.md`, `.importlinter`, CI) by the maintainer.

## Consequences
- Workflows triggered by labels and dispatch run from the default branch, so changes to
  them take effect only after merging.
- Every agent PR costs CI minutes and model tokens. Limits: `AGENT_MAX_PARALLEL` open
  agent PRs and `AGENT_MAX_ROUNDS` automatic fix rounds per PR.
- Existing known dependency vulnerabilities do not block PRs; only newly introduced ones do.
