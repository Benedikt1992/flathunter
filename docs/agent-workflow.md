# Agent workflow

Issues are implemented autonomously by AI agents. Quality is checked by
**deterministic gates** and an **independent reviewer agent**. The maintainer always
does the final review and merge. Rationale: [ADR 0001](adr/0001-agent-driven-development.md).

```
Issue ──(maintainer: agent:plan)──► Planner ──► plan comment ──┐  (optional)
  │   "Blockiert durch" still open? → agent:waiting, auto-start when they close
  │                                                            │
  └──(maintainer: agent:ready)◄────────────────────────────────┘
        ▼
   Implementer (agent app identity)  → branch agent/issue-N, draft PR
        ▼
   CI gates  (tests · diff coverage · prek: ruff/pyrefly/complexipy/import-linter/deptry
              · agent guard · mutation report · pip-audit diff · gitleaks · CodeQL)
        ├─ red (after one re-run) ──► Fixer (mode=ci) ──────┐
        ▼ green                                             │
   Independent reviewer (github-actions[bot], read-only)    │
        ├─ changes requested ──► Fixer (mode=review) ───────┤  max AGENT_MAX_ROUNDS
        ▼ approved                                          │  automatic rounds,
   PR ready for review · label needs-human · you requested  │  then agent:stuck
        ▼                                                   │
   Maintainer review ──"Request changes"──► Fixer (mode=human, unlimited) ──► CI → review → you
        ▼ approve
   Maintainer merges (only possible for the maintainer)
```

## Day-to-day use

| You want to… | Do this |
|---|---|
| Have an issue implemented | Write it with the **Task** template (clear acceptance criteria!), add `agent:ready` |
| Work through a series of dependent issues | Add `agent:ready` to all of them. Issues whose `Blockiert durch:` / `Blocked by:` issues are still open wait (`agent:waiting`) and start automatically, lowest number first, once those are closed |
| See the approach first (larger issues) | Add `agent:plan`, read the plan, then add `agent:ready` (or comment and re-add `agent:plan`) |
| Answer an agent's open questions (`agent:blocked`, plan, PR) | Answer each question in its own conversation (see below), then re-add `agent:plan` or `agent:ready`, or submit a review with **Request changes** |
| Review a finished PR | PRs labelled `needs-human` request your review. Use "Files changed → Changes since your last review" in later rounds |
| Get local changes | **Request changes** with inline comments. The fixer implements each point, replies per thread (doesn't resolve them), then CI + reviewer run again and you are requested again |
| Stop an agent PR | Close it. Re-add `agent:ready` on the issue (after deleting the branch) to start over |
| Allow a gate exception | Add `allow-suppression` (also for a grown baseline) or `allow-test-removal` to the PR (only counts when you add it). Changed protected files need no label: the reviewer checks them, you decide with the merge |

### What kind of feedback goes where

| Feedback | Example | Where |
|---|---|---|
| Local | "rename this", "edge case missing" | Review comment on the PR |
| Spec was wrong/incomplete | "should also support X" | Update the issue's acceptance criteria; for big changes close the PR and restart |
| General rule | "we never log directly", "no new deps for this" | Additionally change `CLAUDE.md` / `ARCHITECTURE.md` / an ADR, and if possible a CI rule (`.importlinter`, pylint, guard). The fixer suggests such rule changes in its round summary |

### Answering an agent's questions

Every question of an agent is its own conversation, with a visible ID (`**Question Q3**`).
The plan, blocked and round comments only say how many questions there are.

- **Before a PR exists** (planner, blocked implementer): one issue comment per question.
  Answer with **Quote reply** on that comment, or start your comment with the ID (`Q3: yes`).
  Quoting several questions answers all of them. Then re-add `agent:plan` or `agent:ready`.
- **On a PR** (finished implementer, fixer): one review thread per question. Inline if the
  question is about a changed line, otherwise on the file or as a PR comment. Reply in the
  thread (PR comments: Quote reply or `Q3:`), then submit a review with **Request changes**.
- The next run sees every question with its status (`beantwortet` or `offen`) and your
  answers. Only your answers count; comments of others never reach an agent. Unresolved
  question threads block the merge (conversation resolution), so none gets lost. You may
  resolve a question thread once you have answered it: the next run still sees the question
  and your answer (status `beantwortet`), but does not reply in that thread.
- IDs are unique per issue and per PR, they do not continue from one to the other.

If a PR is fundamentally on the wrong track: close it, sharpen the issue (acceptance
criteria, non-goals, hints on the approach), and restart with `agent:plan`.

## Labels

| Label | Set by | Meaning |
|---|---|---|
| `agent:plan` | you | write a plan for this issue |
| `agent:plan-ready` | agent | plan posted |
| `agent:ready` | you | implement this issue |
| `agent:waiting` | agent | waiting for the issues in `Blockiert durch` to be closed |
| `agent:queued` | agent | waiting for a free slot (`AGENT_MAX_PARALLEL`) |
| `agent:in-progress` | agent | an agent is working |
| `agent:blocked` | agent | open questions for you |
| `agent:stuck` | agent | workflow failed, fix session failed without new commits, or round limit reached |
| `needs-human` | agent | ready for your review |
| `allow-*` | you | gate overrides for one PR |

Agent workflows only react to labels **you** add (checked against the issue's event
history), and agents only ever see **your** comments plus agent plans/reviews. Text from
other people never reaches a prompt except the issue body itself, which you approve by labelling.

## Independence of quality control

- **Gates are enforced by CI**, not by agent instructions. The agent guard fails on the
  typical shortcuts (suppressions, grown baselines, removed tests) unless you add the
  matching `allow-*` label. Changes to protected files (`.github/`, gate configs, rules,
  dependencies) are not blocked by a label: the guard lists them, the reviewer has to
  assess each one against the issue, and they need your code-owner review anyway
  ([ADR 0003](adr/0003-review-protected-files-instead-of-label.md)). The agent app has no
  `workflows` permission, so it cannot push workflow changes at all.
- **The reviewer is a different identity with read-only access**, runs only after CI is
  green, gets its prompt and rules from the default branch (a PR cannot weaken them),
  sees the issue and the diff but not the implementer's reasoning, and treats the PR
  description as a claim to verify.
- **The verdict is computed by fixed rules** (`agent_ops.py decide_verdict`): any
  blocker/major finding, unmet acceptance criterion or changed protected file that the
  reviewer did not assess as required by the issue means changes requested. The review
  lists every changed protected file with the reviewer's assessment.
- **Only you can approve and merge** (branch protection + CODEOWNERS).

## One-time setup

### 1. Merge this setup into the default branch
Workflows triggered by labels and dispatch only run from the default branch (`fork-main`).

### 2. Create the agent GitHub App
Settings → Developer settings → GitHub Apps → New GitHub App:
- Name e.g. `flathunter-agent`, homepage = repo URL, **Webhook: inactive**.
- Repository permissions: **Contents: Read & write**, **Pull requests: Read & write**,
  **Issues: Read & write**, Metadata: Read. Nothing else (in particular **no Workflows**, no Administration).
- "Only on this account". Create, then **Generate a private key** and **Install App** on this repository only.

Pushes and PRs made with this app's token trigger CI (unlike the default `GITHUB_TOKEN`),
and its identity is separate from the reviewer's.

### 3. Secrets and variables
Settings → Secrets and variables → Actions:

| Kind | Name | Value |
|---|---|---|
| Secret | `AGENT_APP_PRIVATE_KEY` | the app's private key (.pem content) |
| Variable | `AGENT_APP_ID` | the app's App ID |
| Secret | `ANTHROPIC_API_KEY` **or** `CLAUDE_CODE_OAUTH_TOKEN` | model access (`claude setup-token` creates the OAuth token for a Claude subscription) |
| Variable (optional) | `AGENT_MAINTAINER` | your login (default: repository owner) |
| Variable (optional) | `AGENT_MAX_PARALLEL` | open agent PRs at a time (default 2) |
| Variable (optional) | `AGENT_MAX_ROUNDS` | automatic fix rounds per PR (default 3) |
| Variable (optional) | `AGENT_PLAN_MODEL` | model for the planner (default `claude-opus-5-5`) |
| Variable (optional) | `AGENT_IMPLEMENT_MODEL` | model for the implementer (default `claude-sonnet-5-5`) |
| Variable (optional) | `AGENT_REVIEW_MODEL` | model for the independent reviewer (default `claude-opus-5-5`); a different model than the implementer decorrelates mistakes |
| Variable (optional) | `AGENT_FIX_MODEL` | model for fix rounds (default `claude-sonnet-5-5`) |

### 4. Actions settings
Settings → Actions → General:
- Workflow permissions: **Read repository contents** (workflows request what they need).
- **Do not** enable "Allow GitHub Actions to create and approve pull requests".

### 5. Protect the default branch
Settings → Rules → Rulesets → New branch ruleset, target `fork-main`:
- Restrict deletions, block force pushes.
- **Require a pull request before merging**: 1 approval, **Require review from Code Owners**,
  dismiss stale approvals on new commits, require conversation resolution.
- **Require status checks to pass**: `Python 3.10`, `Python 3.11`,
  `Static quality gates (prek)`, `Agent guard`, `Dependency audit`, `Secret scan (gitleaks)`,
  `CodeQL (Python, security-extended)` (they appear in the list after they ran once).
  Require branches to be up to date. `Mutation testing (report)` is informational, do not
  make it required.
- Bypass list: only *Repository admin* (so you can merge your own PRs). Never the agent app.

### 6. Create the labels
Actions → **Agent · Setup** → Run workflow.

### 7. First run
Good first issue: *"Update dependencies with known vulnerabilities"* (the Security
workflow's weekly report lists them). It touches `Pipfile.lock`; the reviewer checks that
the issue asks for it, no label needed.

## Dependencies between issues

Issues declare their predecessors in the first line, e.g.
`… · Blockiert durch: #6, #7 · …` (also `Blocked by:`, ranges like `(#2–#33)`).
`agent-implement.yml` checks them before any work starts (`agent_ops.py check-blockers`):
if one is still open, the issue gets `agent:waiting` and a single comment. `agent-queue.yml`
runs whenever an issue is closed or an agent PR finishes (and manually) and starts every
waiting or queued `agent:ready` issue whose predecessors are all closed, lowest number first,
up to `AGENT_MAX_PARALLEL` active tasks. A closed predecessor counts as done whatever the
close reason; if you close one as "not planned", check its dependents. Text without issue
numbers (e.g. "Merge des Agent-Setups") is not checked.

## Automatic branch updates

The ruleset requires a PR branch to be up to date, so after you merge one agent PR every
other open one would need a click on **Update branch**. `agent-update.yml` does that for you.
It runs after every push to the default branch and, via `workflow_dispatch`, at the end of
every "Agent · Review" and "Agent · Fix" run: the last step of those workflows starts it
with `gh workflow run`, after all label changes and also when the run failed (so PRs that
were skipped while busy catch up). You can also start it by hand from the Actions tab.
A `workflow_run` trigger would not work here: review and fix runs are themselves started
with the `GITHUB_TOKEN`, and runs started that way create no events that start other
workflows (only `workflow_dispatch` and `repository_dispatch` are exempt). If a job is
killed by its timeout, the dispatch step may not run; the next push to the default branch
catches up. In
`agent_ops.py update-branches` it selects every open PR with head `agent/issue-<n>` from this
repository that is behind the default branch and has no `agent:in-progress` label
(`select_branch_updates`), merges the default branch into it (`git merge --no-edit`, never a
rebase or force-push) and pushes with the agent app token, so the required checks run on the
new state.

The update starts no review and is not a fix round: a conflict-free merge does not change the
PR's own diff, and the CI gates run again anyway. It has its own concurrency group
(`agent-branch-update`), so it never displaces a waiting review or fix. If the branch moves
while the update runs (a fixer pushed), the update skips that PR; the next run retries.

**When you have to step in.** If an update is refused, the PR gets one comment per default
branch commit (marker `<!-- agent-branch-update base=<sha> -->`) with the reason:

- *conflict*: resolve the merge conflict yourself, locally or in the web editor.
- *workflow files*: the new default-branch commits change `.github/workflows/`, which the
  app may not push (it lacks the `workflows` permission). Click **Update branch** yourself.
- *other error*: click **Update branch**; if that fails too, open the run log. The run
  fails in this case, so it shows up in the Actions tab.

If GitHub cannot compare a PR with the default branch, or its branch cannot be fetched, that PR
gets no comment, but the run fails (Actions tab). The other PRs are still updated.

## Quality gates in the agent loop

The static gates are defined once in `.pre-commit-config.yaml` and run in three places:

1. **Agent loop**: `.claude/settings.json` hooks. After every edit of a Python file it is
   formatted and checked with Ruff, and new violations go straight back to the agent.
   Before the agent finishes, `prek run --all-files` runs once and failures send it back to work.
   The same hooks help you when you use Claude Code locally.
2. **Git hook**: `pip install -r requirements-dev.txt && prek install`.
3. **CI**: `quality.yml` (authoritative).

All gates take about 1 second. Legacy debt sits in ratchet baselines (`quality/`,
`complexipy-snapshot.json`): new violations fail, fixed debt has to be locked in with
`python .github/scripts/ratchet.py prune`, and the guard rejects any baseline growth
without your `allow-suppression` label. Tooling rationale: [ADR 0002](adr/0002-python-quality-toolchain.md).

## Costs and limits
Each implementation is one model session (≤ 120 turns), each review/fix round another one.
Jobs have timeouts (60–75 min). Tune `AGENT_MAX_PARALLEL`, `AGENT_MAX_ROUNDS` and the models
via repository variables. Failed fixer runs without commits do not use up `AGENT_MAX_ROUNDS`.

## Troubleshooting
- **Nothing happens after labelling**: was the label added by `AGENT_MAINTAINER`? Is the
  setup merged into the default branch? Check the Actions tab.
- **`agent:stuck`**: open the linked run log. Typical causes: missing secrets, model
  limits, round limit reached (then the issue usually needs clarification).
- **`Workflow initiated by non-human actor: github-actions (type: Bot). Add bot to
  allowed_bots list`**: the workflows start each other with `gh workflow run` and the
  `GITHUB_TOKEN`, so the actor is `github-actions[bot]`, and `claude-code-action` aborts
  for bot actors that are not allowed. Every `claude-code-action` step in the dispatchable
  `agent-*.yml` workflows must set `allowed_bots: github-actions` (never `'*'`, which
  would allow every GitHub App). `.github/scripts/test_workflow_allowed_bots.py` checks this.
- **"Fix round … failed" comment on a PR**: the fixer session failed (the step failed or it
  returned no structured result). The comment links the run log. Without new commits the
  PR gets `agent:stuck` (not `needs-human`) and the maintainer is not asked for a review;
  with new commits they are pushed and re-reviewed as usual. A failed run without commits
  does not count against `AGENT_MAX_ROUNDS` (its comment starts with
  `<!-- agent-fix-round mode=… failed -->`). A failed run that produced commits still counts,
  so a repeating loop is stopped. To restart: fix the cause, then request changes in a
  review or re-dispatch `agent-fix.yml`.
- **"Not committed by the fixer and therefore discarded" in a fix round comment**: the
  fixer changed files but did not commit them, and the runner is gone. The fixer and the
  implementer are told to commit each step once its tests pass and to delete their
  scratch files (they may run `rm`, `git rm` and `git mv`). Their shell commands may run
  for up to 25 minutes (`BASH_DEFAULT_TIMEOUT_MS`; Claude Code's default is 2), enough for
  `mutation_report.py`. `.github/scripts/test_workflow_agent_shell.py` checks both settings.
  Re-request the missing points in a review.
- **A CI job failed for a reason outside the PR** (e.g. a GitHub API error in a
  scanner): before the review hands red CI to the fixer, `agent_ops.py wait-checks`
  re-runs the failed jobs of each failed run once (only runs still at attempt 1). Only a
  failure that repeats costs a fix round. If GitHub does not say which attempt a run is
  at, the run is not re-run and its failure goes to the fixer as before.
- **"Warning: mutmut failed …" in the mutation report**: mutmut did not finish (e.g. a
  mutmut version that does not work with the installed pytest), so the numbers are not
  meaningful. The warning quotes mutmut's last output line; run
  `python .github/scripts/mutation_report.py --base origin/fork-main` locally for the full
  output. A timeout, or tests that still fail in mutmut's sandbox after three attempts, are
  reported the same way. Tests that already fail before any mutation are not a warning: they
  are deselected and counted (`_N test(s) failing before mutation were deselected._`).
- **Push failed / work saved as artifact**: both workflows push with
  `python3 .github/scripts/agent_ops.py push-branch <branch>` (at most 4 attempts, waits of 5, 15
  and 45 s; if the remote branch moved it is fetched and merged with `git merge --no-edit`,
  never force-pushed or rebased; a merge conflict is aborted and counts as failed). If the
  push still fails, the job uploads the artifact `agent-work-<issue|pr>-<run id>` (kept 14
  days) with `work.bundle` (the new commits) and `result.json` (title, summary, test plan),
  and the failure comment names it. To take the work over by hand:
  ```bash
  gh run download <run id> -n agent-work-<issue|pr>-<run id> -D agent-work
  git fetch agent-work/work.bundle HEAD:agent/issue-<N>   # fixer: git pull agent-work/work.bundle HEAD on the PR branch
  git push origin agent/issue-<N>
  ```
  For an implementation run, open the PR yourself (title, summary and test plan are in
  `result.json`), then add `agent:in-progress` and re-label as needed. A merge conflict with
  the moved branch has to be resolved by hand before the push. The log of each failed
  attempt is in the run log (the token is redacted).
- **Reviewer keeps requesting the same thing**: the acceptance criteria are probably
  ambiguous; clarify the issue or decide yourself in a review.
