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
