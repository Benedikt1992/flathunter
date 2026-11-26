"""Tests for `agent_ops push-branch` (offline: fake git, and a local bare repository)."""

import argparse
import subprocess
from pathlib import Path

import agent_ops as ops
import pytest
from hypothesis import given
from hypothesis import strategies as st


class FakeGit:
    """Records git calls and answers push calls from a list of (returncode, stdout, stderr)."""

    def __init__(self, pushes, other=None):
        self.pushes = list(pushes)
        self.calls = []
        self.other = other or {}

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "push":
            code, out, err = self.pushes.pop(0)
        else:
            code, out, err = self.other.get(args[0], (0, "", ""))
        return subprocess.CompletedProcess(["git", *args], code, out, err)

    def pushes_made(self):
        return [c for c in self.calls if c[0] == "push"]


def no_sleep(_seconds):
    return None


def test_push_retries_until_success():
    git = FakeGit(
        [
            (1, "", "remote: fatal error in commit_refs_rules"),
            (1, "", "remote: fatal error in commit_refs_rules"),
            (0, "", ""),
        ]
    )
    sleeps = []
    assert ops.push_branch("b", "url", run=git, sleep=sleeps.append)
    assert len(git.pushes_made()) == 3
    assert sleeps == [5, 15]
    assert git.pushes_made()[0] == ("push", "url", "HEAD:refs/heads/b")


def test_push_gives_up_after_four_attempts(monkeypatch, capsys):
    git = FakeGit([(1, "", "err-" + str(n)) for n in range(1, 5)])
    sleeps = []
    monkeypatch.setattr(ops, "_git", git)
    monkeypatch.setattr(ops.time, "sleep", sleeps.append)
    monkeypatch.setenv("APP_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    code = ops.cmd_push_branch(argparse.Namespace(branch="b", remote=None))
    assert code == 1
    out = capsys.readouterr().out
    for n in range(1, 5):
        assert "push attempt " + str(n) + "/4 failed: err-" + str(n) in out
    assert sleeps == [5, 15, 45]
    assert len(git.pushes_made()) == 4


def test_push_success_returns_zero_and_builds_url_from_env(monkeypatch):
    git = FakeGit([(0, "", "")])
    monkeypatch.setattr(ops, "_git", git)
    monkeypatch.setenv("APP_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    assert ops.cmd_push_branch(argparse.Namespace(branch="b", remote=None)) == 0
    assert git.pushes_made() == [
        ("push", "https://x-access-token:tok@github.com/o/r.git", "HEAD:refs/heads/b")
    ]


def test_push_never_forces_or_rebases():
    rejected = (1, "", " ! [rejected] HEAD -> b (fetch first)")
    git = FakeGit([rejected, (0, "", "")])
    assert ops.push_branch("b", "url", run=git, sleep=no_sleep)
    assert ("merge", "--no-edit", "FETCH_HEAD") in git.calls
    assert ("fetch", "url", "refs/heads/b") in git.calls
    flat = [part for call in git.calls for part in call]
    for forbidden in ("--force", "-f", "--force-with-lease", "rebase", "reset"):
        assert forbidden not in flat
    assert not any(part.startswith("+") for part in flat)


def test_push_failed_merge_is_aborted_and_gives_up():
    git = FakeGit([(1, "", "! [rejected] non-fast-forward")], other={"merge": (1, "", "conflict")})
    sleeps = []
    assert not ops.push_branch("b", "url", run=git, sleep=sleeps.append)
    assert ("merge", "--abort") in git.calls
    assert len(git.pushes_made()) == 1
    assert sleeps == []


def test_push_failed_fetch_gives_up_without_merge():
    git = FakeGit([(1, "", "non-fast-forward")], other={"fetch": (128, "", "no such ref")})
    assert not ops.push_branch("b", "url", run=git, sleep=no_sleep)
    assert ("merge", "--no-edit", "FETCH_HEAD") not in git.calls


def test_transient_error_is_not_treated_as_moved_branch():
    git = FakeGit([(1, "", "! [remote rejected] HEAD -> b (fatal error)"), (0, "", "")])
    assert ops.push_branch("b", "url", run=git, sleep=no_sleep)
    assert not any(call[0] in ("fetch", "merge") for call in git.calls)


def test_merge_gets_an_identity_only_if_missing():
    missing = FakeGit([(1, "", "fetch first"), (0, "", "")])
    assert ops.push_branch("b", "url", run=missing, sleep=no_sleep)
    assert ("config", "user.email", ops.DEFAULT_COMMITTER_EMAIL) in missing.calls

    configured = FakeGit([(1, "", "fetch first"), (0, "", "")], other={"config": (0, "me@x\n", "")})
    assert ops.push_branch("b", "url", run=configured, sleep=no_sleep)
    assert not any(call[:2] == ("config", "user.name") for call in configured.calls)


def test_push_output_never_contains_token(capsys):
    url = "https://x-access-token:s3cr3t-token@github.com/o/r.git"
    leak = "fatal: unable to access '" + url + "/'"
    git = FakeGit([(1, "out " + url, leak)] * 4)
    assert not ops.push_branch("b", url, token="s3cr3t-token", run=git, sleep=no_sleep)
    captured = capsys.readouterr()
    assert "s3cr3t-token" not in captured.out + captured.err
    assert "x-access-token:***@github.com" in captured.out


@given(
    token=st.text(alphabet=st.characters(exclude_characters="*"), min_size=1),
    before=st.text(),
    after=st.text(),
)
def test_redact_removes_token(token, before, after):
    redacted = ops.redact(before + token + after, token)
    assert "***" in redacted
    assert token not in redacted


@given(text=st.text(), token=st.text(min_size=1))
def test_redact_leaves_text_without_token_alone(text, token):
    if token not in text:
        assert ops.redact(text, token) == text


def test_redact_with_empty_token_is_identity():
    assert ops.redact("abc", "") == "abc"


# real git, local bare repository as remote


def _git_in(path, *args):
    prefix = ["--git-dir=" + str(path)] if str(path).endswith(".git") else []
    result = subprocess.run(
        ["git", *prefix, *args], cwd=path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def repos(tmp_path):
    bare = tmp_path / "remote.git"
    _git_in(tmp_path, "init", "--bare", "-b", "main", str(bare))
    clones = {}
    for name in ("a", "b"):
        path = tmp_path / name
        _git_in(tmp_path, "clone", str(bare), str(path))
        _git_in(path, "config", "user.email", name + "@example.org")
        _git_in(path, "config", "user.name", name)
        clones[name] = path
    (clones["a"] / "base.txt").write_text("base\nline2\n")
    _git_in(clones["a"], "add", ".")
    _git_in(clones["a"], "commit", "-m", "base")
    _git_in(clones["a"], "push", "origin", "HEAD:refs/heads/work")
    _git_in(clones["b"], "pull", "origin", "work")
    return bare, clones


def _runner(path):
    return lambda *args: ops._git(*args, cwd=str(path))


def _commit(path, name, text, message):
    (Path(path) / name).write_text(text)
    _git_in(path, "add", name)
    _git_in(path, "commit", "-m", message)


def test_push_merges_moved_remote_branch(repos):
    bare, clones = repos
    _commit(clones["a"], "a.txt", "from a\n", "a")
    _git_in(clones["a"], "push", "origin", "HEAD:refs/heads/work")
    a_head = _git_in(clones["a"], "rev-parse", "HEAD")
    _commit(clones["b"], "b.txt", "from b\n", "b")
    b_head = _git_in(clones["b"], "rev-parse", "HEAD")

    assert ops.push_branch("work", str(bare), run=_runner(clones["b"]), sleep=no_sleep)

    remote_head = _git_in(bare, "rev-parse", "refs/heads/work")
    parents = _git_in(bare, "rev-list", "--parents", "-n1", remote_head).split()[1:]
    assert set(parents) == {a_head, b_head}
    assert _git_in(bare, "show", "refs/heads/work:a.txt") == "from a"
    assert _git_in(bare, "show", "refs/heads/work:b.txt") == "from b"


def test_push_aborts_merge_on_conflict(repos):
    bare, clones = repos
    _commit(clones["a"], "base.txt", "A\nline2\n", "a edits")
    _git_in(clones["a"], "push", "origin", "HEAD:refs/heads/work")
    _commit(clones["b"], "base.txt", "B\nline2\n", "b edits")
    b_head = _git_in(clones["b"], "rev-parse", "HEAD")
    remote_before = _git_in(bare, "rev-parse", "refs/heads/work")

    assert not ops.push_branch("work", str(bare), run=_runner(clones["b"]), sleep=no_sleep)

    assert _git_in(clones["b"], "rev-parse", "HEAD") == b_head
    assert _git_in(clones["b"], "status", "--porcelain") == ""
    assert not (clones["b"] / ".git" / "MERGE_HEAD").exists()
    assert _git_in(bare, "rev-parse", "refs/heads/work") == remote_before


def test_push_to_new_branch_works(repos):
    bare, clones = repos
    _commit(clones["b"], "b.txt", "b\n", "b")
    assert ops.push_branch("fresh", str(bare), run=_runner(clones["b"]), sleep=no_sleep)
    head = _git_in(clones["b"], "rev-parse", "HEAD")
    assert _git_in(bare, "rev-parse", "refs/heads/fresh") == head


# GitHub refuses an app's push of a new branch whose workflow files differ from the default
# branch, even if the agent's commits do not touch them (the base moved meanwhile).

REFUSAL = (
    " ! [remote rejected] HEAD -> b (refusing to allow a GitHub App to create or update"
    " workflow `.github/workflows/agent-fix.yml` without `workflows` permission)"
)

REFUSAL_GIVE_UP = "GitHub refused workflow files and replaying on the base did not help; giving up"

# Models GitHub for the agent: a new branch is compared with main, an update with its old
# head. Pushes to main stand for the maintainer, who may change workflows.
WORKFLOW_HOOK = """#!/bin/sh
zero=0000000000000000000000000000000000000000
while read -r old new ref; do
  [ "$ref" = refs/heads/main ] && continue
  from=$old; [ "$old" = "$zero" ] && from=refs/heads/main
  files=$(git diff --name-only "$from" "$new" -- .github/workflows)
  if [ -n "$files" ]; then
    first=$(echo "$files" | head -n 1)
    echo "refusing to allow a GitHub App to create or update workflow \\`$first\\` without \\`workflows\\` permission" >&2
    exit 1
  fi
done
"""


@pytest.fixture
def github(tmp_path, monkeypatch):
    """Remote whose main changed a workflow after the agent branched off (base: main)."""
    monkeypatch.setenv("DEFAULT_BRANCH", "main")
    bare = tmp_path / "remote.git"
    _git_in(tmp_path, "init", "--bare", "-b", "main", str(bare))
    clones = {}
    for name in ("maint", "agent"):
        clones[name] = tmp_path / name
        _git_in(tmp_path, "clone", "-q", str(bare), str(clones[name]))
        _git_in(clones[name], "config", "user.email", name + "@example.org")
        _git_in(clones[name], "config", "user.name", name)
        if name == "maint":
            (clones[name] / ".github" / "workflows").mkdir(parents=True)
            _commit(clones[name], ".github/workflows/ci.yml", "old\n", "base")
            _commit(clones[name], "app.txt", "app\n", "app")
            _git_in(clones[name], "push", "-q", "origin", "HEAD:refs/heads/main")
    _git_in(clones["agent"], "checkout", "-q", "-b", "agent/issue-1")
    _commit(clones["maint"], ".github/workflows/ci.yml", "new\n", "maintainer changes a workflow")
    _git_in(clones["maint"], "push", "-q", "origin", "HEAD:refs/heads/main")
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(WORKFLOW_HOOK)
    hook.chmod(0o755)
    return bare, clones


def _recording(path, calls):
    def run(*args):
        calls.append(args)
        return ops._git(*args, cwd=str(path))

    return run


def _remote_heads(bare):
    return _git_in(bare, "for-each-ref", "--format=%(refname)", "refs/heads").splitlines()


def test_new_branch_is_replayed_on_the_moved_base_when_workflows_are_refused(github):
    bare, clones = github
    agent = clones["agent"]
    _commit(agent, "feature.txt", "feature\n", "agent work")
    # the workflow patch stays uncommitted, as the prompts ask (one file intent-to-add)
    (agent / ".github" / "workflows" / "ci.yml").write_text("agent patch\n")
    (agent / ".github" / "workflows" / "new.yml").write_text("new\n")
    _git_in(agent, "add", "-N", ".github/workflows/new.yml")
    calls, sleeps = [], []

    pushed = ops.push_branch(
        "agent/issue-1", str(bare), run=_recording(agent, calls), sleep=sleeps.append
    )

    assert pushed
    head = _git_in(bare, "rev-parse", "refs/heads/agent/issue-1")
    assert _git_in(bare, "rev-parse", head + "^") == _git_in(bare, "rev-parse", "refs/heads/main")
    assert _git_in(bare, "show", head + ":feature.txt") == "feature"
    assert _git_in(bare, "show", head + ":.github/workflows/ci.yml") == "new"
    assert _git_in(bare, "log", "-1", "--format=%an|%s", head) == "agent|agent work"
    assert _git_in(agent, "rev-parse", "HEAD") == head  # HEAD names the pushed commit
    assert _git_in(agent, "symbolic-ref", "HEAD") == "refs/heads/agent/issue-1"
    assert (agent / ".github" / "workflows" / "ci.yml").read_text() == "agent patch\n"
    assert (agent / ".github" / "workflows" / "new.yml").read_text() == "new\n"
    assert len(_git_in(agent, "worktree", "list").splitlines()) == 1  # scratch worktree removed
    assert [c for c in calls if c[0] == "push"] == [
        ("push", str(bare), "HEAD:refs/heads/agent/issue-1"),
        ("push", str(bare), head + ":refs/heads/agent/issue-1"),
    ]
    assert sleeps == []
    flat = [part for call in calls for part in call]
    for forbidden in ("--force", "-f", "--force-with-lease", "rebase", "reset"):
        assert forbidden not in flat


def test_workflow_refusal_without_a_base_gives_up_at_once(github, monkeypatch, capsys):
    bare, clones = github
    monkeypatch.delenv("DEFAULT_BRANCH")
    _commit(clones["agent"], "feature.txt", "feature\n", "agent work")
    head = _git_in(clones["agent"], "rev-parse", "HEAD")
    calls, sleeps = [], []

    assert not ops.push_branch(
        "agent/issue-1", str(bare), run=_recording(clones["agent"], calls), sleep=sleeps.append
    )

    assert [c[0] for c in calls] == ["push"]  # retrying cannot help
    assert sleeps == []
    assert _remote_heads(bare) == ["refs/heads/main"]
    assert _git_in(clones["agent"], "rev-parse", "HEAD") == head
    out = capsys.readouterr().out
    assert out.startswith("push attempt 1/4 failed: ")
    assert "push attempt 2/4" not in out
    assert out.splitlines()[-1] == REFUSAL_GIVE_UP


def test_replay_is_tried_once_when_the_commits_change_a_workflow_themselves(github, capsys):
    bare, clones = github
    agent = clones["agent"]
    _commit(agent, "feature.txt", "feature\n", "agent work")
    _commit(agent, ".github/workflows/own.yml", "own\n", "agent commits a workflow")
    head = _git_in(agent, "rev-parse", "HEAD")
    calls, sleeps = [], []

    assert not ops.push_branch(
        "agent/issue-1", str(bare), run=_recording(agent, calls), sleep=sleeps.append
    )

    assert len([c for c in calls if c[0] == "push"]) == 2
    assert len([c for c in calls if "cherry-pick" in c]) == 1
    assert sleeps == []
    assert _remote_heads(bare) == ["refs/heads/main"]
    assert _git_in(agent, "rev-parse", "HEAD") == head  # unpushed work stays as it was
    assert len(_git_in(agent, "worktree", "list").splitlines()) == 1
    assert REFUSAL_GIVE_UP in capsys.readouterr().out.splitlines()


def test_replay_conflict_gives_up_and_leaves_head_alone(github):
    bare, clones = github
    _commit(clones["maint"], "app.txt", "maintainer\n", "maintainer edits app")
    _git_in(clones["maint"], "push", "-q", "origin", "HEAD:refs/heads/main")
    agent = clones["agent"]
    _commit(agent, "app.txt", "agent\n", "agent edits app")
    head = _git_in(agent, "rev-parse", "HEAD")
    calls, sleeps = [], []

    assert not ops.push_branch(
        "agent/issue-1", str(bare), run=_recording(agent, calls), sleep=sleeps.append
    )

    assert len([c for c in calls if c[0] == "push"]) == 1
    assert sleeps == []
    assert _remote_heads(bare) == ["refs/heads/main"]
    assert _git_in(agent, "rev-parse", "HEAD") == head
    assert _git_in(agent, "status", "--porcelain") == ""
    assert len(_git_in(agent, "worktree", "list").splitlines()) == 1


@pytest.mark.parametrize("ls_remote", [(0, "abc\trefs/heads/b\n", ""), (128, "", "fatal")])
def test_existing_or_unknown_remote_branch_is_never_replayed(monkeypatch, ls_remote):
    # an existing branch would need a force push; an unknown answer is not trusted
    monkeypatch.setenv("DEFAULT_BRANCH", "main")
    git = FakeGit([(1, "", REFUSAL)], other={"ls-remote": ls_remote})
    sleeps = []
    assert not ops.push_branch("b", "url", run=git, sleep=sleeps.append)
    assert git.calls == [
        ("push", "url", "HEAD:refs/heads/b"),
        ("ls-remote", "--exit-code", "url", "refs/heads/b"),
    ]
    assert sleeps == []


def test_replay_gives_up_if_the_base_cannot_be_fetched(monkeypatch):
    monkeypatch.setenv("DEFAULT_BRANCH", "fork-main")
    git = FakeGit([(1, "", REFUSAL)], other={"ls-remote": (2, "", ""), "fetch": (128, "", "x")})
    assert not ops.push_branch("b", "url", run=git, sleep=no_sleep)
    assert git.calls[1:] == [
        ("ls-remote", "--exit-code", "url", "refs/heads/b"),
        ("fetch", "url", "refs/heads/fork-main"),
    ]


def test_replay_gives_up_if_the_scratch_worktree_cannot_be_added(monkeypatch):
    monkeypatch.setenv("DEFAULT_BRANCH", "main")
    git = FakeGit(
        [(1, "", REFUSAL)],
        other={"ls-remote": (2, "", ""), "rev-parse": (0, "abc\n", ""), "worktree": (1, "", "x")},
    )
    assert not ops.push_branch("b", "url", run=git, sleep=no_sleep)
    assert not any("cherry-pick" in call for call in git.calls)
    assert len(git.pushes_made()) == 1


def test_cmd_push_branch_replays_on_the_default_branch(github, monkeypatch):
    bare, clones = github
    _commit(clones["agent"], "feature.txt", "feature\n", "agent work")
    real_git = ops._git
    monkeypatch.setattr(ops, "_git", lambda *args: real_git(*args, cwd=str(clones["agent"])))
    monkeypatch.setenv("APP_TOKEN", "")
    code = ops.cmd_push_branch(argparse.Namespace(branch="agent/issue-1", remote=str(bare)))
    assert code == 0
    assert _git_in(bare, "rev-parse", "refs/heads/agent/issue-1^") == _git_in(
        bare, "rev-parse", "refs/heads/main"
    )


def test_default_branch_comes_from_the_workflow_env(monkeypatch):
    monkeypatch.delenv("DEFAULT_BRANCH", raising=False)
    assert ops.default_branch() == ""
    monkeypatch.setenv("DEFAULT_BRANCH", "fork-main")
    assert ops.default_branch() == "fork-main"


def test_plain_push_makes_no_other_git_call():
    git = FakeGit([(0, "", "")])
    assert ops.push_branch("b", "url", run=git, sleep=no_sleep) is True
    assert git.calls == [("push", "url", "HEAD:refs/heads/b")]


def test_moved_remote_branch_is_merged_without_waiting():
    git = FakeGit([(1, "", " ! [rejected] HEAD -> b (fetch first)"), (0, "", "")])
    sleeps = []
    assert ops.push_branch("b", "url", run=git, sleep=sleeps.append)
    assert sleeps == []
    assert git.pushes_made() == [("push", "url", "HEAD:refs/heads/b")] * 2


def test_unmergeable_remote_branch_says_why(capsys):
    git = FakeGit([(1, "", "non-fast-forward")], other={"merge": (1, "", "conflict")})
    assert ops.push_branch("b", "url", run=git, sleep=no_sleep) is False
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "push attempt 1/4 failed: non-fast-forward",
        "remote branch moved and could not be merged; giving up",
    ]


def test_failed_push_returns_false_and_prints_errors_unchanged_without_token(capsys):
    git = FakeGit([(1, "", "XXXX")] * 4)
    assert ops.push_branch("b", "url", run=git, sleep=no_sleep) is False
    assert "push attempt 4/4 failed: XXXX" in capsys.readouterr().out.splitlines()
