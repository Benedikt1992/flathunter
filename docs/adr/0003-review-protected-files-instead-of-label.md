# 0003: Review changes to protected files instead of gating them with a label

- **Status:** accepted
- **Date:** 2026-11-22

## Context
ADR 0001 let the agent guard fail every pull request that touched a protected file
(CI, gate configs, agent rules, dependencies) until the maintainer added the
`allow-protected-change` label. Every pull request already needs the maintainer's
code-owner approval, so the label added no protection to the merge. In practice it:

- had no effect on pull requests authored by the maintainer (the guard only reports
  there), which so far were all of them;
- stalled the agent loop when an issue legitimately required a protected change
  (dependency update, `ARCHITECTURE.md`/`.importlinter` for a new component): the red
  guard sent the PR to the fixer, which must not change configuration, and adding the
  label later did not restart the review;
- stayed valid for every later commit, including changes to other protected files.

## Decision
1. The guard reports changed protected files but does not fail on them.
2. The guard still fails on the typical shortcuts of an agent: added suppressions,
   removed tests, and ratchet baselines that grow. A growing baseline accepts new
   violations and therefore counts as a suppression (`allow-suppression`). The labels
   `allow-suppression` and `allow-test-removal` stay; `allow-protected-change` is removed.
3. The review context lists the changed protected files (computed from the diff). The
   reviewer must assess each one in its structured result: required by the issue's tasks
   or a binding rule, with a reason. `decide_verdict` requests changes for any changed
   protected file that is missing from the assessment or not required. The posted review
   shows every changed protected file with the assessment.
4. The maintainer decides with the merge, as for all other changes.

## Consequences
- Issues that require protected changes run through the agent loop without a manual
  step; unrequested protected changes go back to the fixer through the review.
- Whether a protected change is justified is judged by the reviewer model instead of a
  fixed rule. The maintainer's code-owner review remains the final check; the guard
  report and the review list the files so they are not missed in a large diff.
- `CLAUDE.md` (rule 4, roadmap rules 11 and 12), `docs/agent-workflow.md`, the agent
  prompts and the PR template describe the new process.
