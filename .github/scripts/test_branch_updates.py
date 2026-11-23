"""Tests for the automatic update of agent PR branches (run with `python -m pytest .github/scripts`)."""

import agent_ops as ops
from hypothesis import given
from hypothesis import strategies as st

BOT = "github-actions[bot]"


def _pr(number, branch=None, *, labels=(), cross=False):
    return {
        "number": number,
        "headRefName": branch or f"agent/issue-{number}",
        "headRefOid": f"sha{number}",
        "labels": [{"name": name} for name in labels],
        "isCrossRepository": cross,
    }


def test_select_branch_updates_picks_behind_agent_prs():
    prs = [
        _pr(9),
        _pr(3),
        _pr(4),
        _pr(5, labels=["agent:in-progress"]),
        _pr(6, "feature/x"),
        _pr(7, "agent/issue-abc"),
        _pr(8, cross=True),
        _pr(10, labels=["needs-human", "agent:ready"]),
        _pr(11),
    ]
    behind = {3: 2, 4: 0, 5: 1, 6: 1, 7: 1, 8: 1, 9: 1, 10: 7}
    assert ops.select_branch_updates(prs, behind) == [3, 9, 10]


_PR_STRATEGY = st.lists(
    st.fixed_dictionaries(
        {
            "number": st.integers(1, 30),
            "headRefName": st.sampled_from(["agent/issue-1", "agent/issue-x", "feature/y"]),
            "labels": st.lists(
                st.sampled_from([{"name": "agent:in-progress"}, {"name": "other"}]), max_size=2
            ),
            "isCrossRepository": st.booleans(),
        }
    ),
    max_size=8,
    unique_by=lambda pr: pr["number"],
)


@given(_PR_STRATEGY, st.dictionaries(st.integers(1, 30), st.integers(0, 3)))
def test_select_branch_updates_property(prs, behind):
    def wanted(pr):
        names = {label["name"] for label in pr["labels"]}
        return (
            behind.get(pr["number"], 0) > 0
            and ops.issue_from_branch(pr["headRefName"]) is not None
            and "agent:in-progress" not in names
            and not pr["isCrossRepository"]
        )

    expected = sorted(pr["number"] for pr in prs if wanted(pr))
    assert ops.select_branch_updates(prs, behind) == expected


def test_update_notice_needed_once_per_base():
    notice = ops.render_update_notice("conflict", "aaa", "")
    comments = [{"user": {"login": BOT}, "body": notice}]
    assert ops.update_notice_needed([], "aaa", [BOT])
    assert not ops.update_notice_needed(comments, "aaa", [BOT])
    assert ops.update_notice_needed(comments, "bbb", [BOT])
    stranger = [{"user": {"login": "someone"}, "body": notice}]
    assert ops.update_notice_needed(stranger, "aaa", [BOT])
    quoted = [{"user": {"login": BOT}, "body": "x\n" + notice}]
    assert ops.update_notice_needed(quoted, "aaa", [BOT])


def test_render_update_notice_names_reason_and_action():
    conflict = ops.render_update_notice("conflict", "abc", "")
    assert conflict.startswith("<!-- agent-branch-update base=abc -->")
    assert "resolve the conflict" in conflict
    workflows = ops.render_update_notice("workflows", "abc", ".github/workflows/a.yml")
    assert "Update branch" in workflows
    assert ".github/workflows/a.yml" in workflows
    other = ops.render_update_notice("other", "abc", "boom " * 200)
    assert "Update branch" in other
    assert "boom" in other
    assert len(other) < 1000


def test_classify_update_failure():
    files = [".github/workflows/a.yml"]
    assert ops.classify_update_failure(files, "", conflict=True) == "conflict"
    assert ops.classify_update_failure(files, "") == "workflows"
    err = "refusing to allow a GitHub App to update workflow without `workflows` permission"
    assert ops.classify_update_failure([], err) == "workflows"
    assert ops.classify_update_failure([], "workflow only") == "other"
    assert ops.classify_update_failure([], "permission only") == "other"
    assert ops.classify_update_failure([], "network down") == "other"


def test_branch_update_notice_is_not_a_fix_round():
    notice = ops.render_update_notice("conflict", "abc", "")
    assert not notice.startswith(ops.FIX_MARKER)
    comments = [{"body": notice}, {"body": ops.render_update_notice("other", "def", "x")}]
    assert ops.count_fix_rounds(comments) == 0


class _Fleet:
    """Fake GitHub and git for update-branches."""

    def __init__(self, monkeypatch, *, merge_rc=0, push_rc=0, push_err="", workflows="", **fails):
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
        monkeypatch.setenv("APP_TOKEN", "s3cret")
        monkeypatch.delenv("AGENT_BOT_LOGIN", raising=False)
        self.prs = [_pr(3), _pr(4, labels=["agent:in-progress"])]
        self.comments: list[dict] = []
        self.gh_calls: list[tuple] = []
        self.git_calls: list[tuple] = []
        self.opts = (merge_rc, push_rc, push_err, workflows)
        self.fails = fails  # fetch_rc, checkout_rc, abort_rc, compare (PR numbers that fail)
        self.compared: list[str] = []
        monkeypatch.setattr(ops, "gh", self.gh)
        monkeypatch.setattr(ops, "gh_json", self.gh_json)
        monkeypatch.setattr(ops, "_git", self.git)

    def gh(self, *args, **_kw):
        self.gh_calls.append(args)
        if args[:2] == ("pr", "list"):
            return ops.json.dumps(self.prs)
        if args[:2] == ("pr", "comment"):
            self.comments.append({"user": {"login": BOT}, "body": args[-1]})
        if args[0] == "api" and "/compare/" in args[1]:
            self.compared.append(args[1])
            if any(args[1].endswith(f"...sha{n}") for n in self.fails.get("compare", ())):
                return '{"message": "Not Found", "status": "404"}'
            return '{"behind_by": 2}'
        return ""

    def gh_json(self, *_a, **_k):
        return list(self.comments)

    def git(self, *args, **_kw):
        merge_rc, push_rc, push_err, workflows = self.opts
        self.git_calls.append(args)
        out, rc, err = "", 0, ""
        if args[0] == "rev-parse":
            out = "basesha"
        elif args[0] == "config":
            out = "someone"
        elif args[0] == "diff":
            out = workflows
        elif args[0] == "merge":
            rc = self.fails.get("abort_rc", 0) if "--abort" in args else merge_rc
        elif args[0] == "fetch" and args[2].startswith("refs/heads/agent/"):
            rc = self.fails.get("fetch_rc", 0)
        elif args[0] == "checkout":
            rc = self.fails.get("checkout_rc", 0)
        elif args[0] == "push":
            rc, err = push_rc, push_err
        return ops.subprocess.CompletedProcess(args, rc, out, err)

    def pushes(self):
        return [c for c in self.git_calls if c[0] == "push"]


def _run_update():
    return ops.cmd_update_branches(ops.argparse.Namespace(base="fork-main", remote=None))


def test_update_branches_merges_and_pushes_without_force(monkeypatch):
    fleet = _Fleet(monkeypatch)
    assert _run_update() == 0
    assert ("merge", "--no-edit", "origin/fork-main") in fleet.git_calls
    assert [p[2] for p in fleet.pushes()] == ["HEAD:refs/heads/agent/issue-3"]
    assert not any("--force" in c or "-f" in c for c in fleet.git_calls)
    assert fleet.comments == []


def test_update_branches_comments_once(monkeypatch):
    fleet = _Fleet(monkeypatch, merge_rc=1)
    assert _run_update() == 0
    assert _run_update() == 0
    posted = [c for c in fleet.gh_calls if c[:2] == ("pr", "comment")]
    assert len(posted) == 1
    assert posted[0][2] == "3"
    assert "resolve the conflict" in fleet.comments[0]["body"]
    assert ("merge", "--abort") in fleet.git_calls
    assert fleet.pushes() == []


def test_update_branches_workflow_changes_are_not_pushed(monkeypatch):
    fleet = _Fleet(monkeypatch, workflows=".github/workflows/x.yml\n")
    assert _run_update() == 0
    assert fleet.pushes() == []
    assert "Update branch" in fleet.comments[0]["body"]
    assert ".github/workflows/x.yml" in fleet.comments[0]["body"]


def test_update_branches_other_push_failure_comments_redacted_and_fails(monkeypatch):
    err = "fatal: https://x-access-token:s3cret@h denied"
    fleet = _Fleet(monkeypatch, push_rc=1, push_err=err)
    assert _run_update() == 1
    body = fleet.comments[0]["body"]
    assert "denied" in body
    assert "s3cret" not in body


def test_update_branches_moved_branch_is_skipped_silently(monkeypatch):
    fleet = _Fleet(monkeypatch, push_rc=1, push_err="! [rejected] (fetch first)")
    assert _run_update() == 0
    assert fleet.comments == []


def test_update_branches_never_dispatches_a_review(monkeypatch):
    fleet = _Fleet(monkeypatch, merge_rc=1)
    _run_update()
    assert not any("workflow" in " ".join(call) for call in fleet.gh_calls)


def test_update_branches_compares_only_update_candidates(monkeypatch):
    fleet = _Fleet(monkeypatch)
    fleet.prs += [_pr(6, "feature/x"), _pr(8, cross=True)]
    assert _run_update() == 0
    assert fleet.compared == ["repos/o/r/compare/fork-main...sha3"]


def test_update_branches_failed_compare_fails_run_but_updates_others(monkeypatch):
    fleet = _Fleet(monkeypatch, compare=(3,))
    fleet.prs.append(_pr(5))
    assert _run_update() == 1
    assert [p[2] for p in fleet.pushes()] == ["HEAD:refs/heads/agent/issue-5"]


def test_update_branches_fetch_failure_fails_the_run(monkeypatch):
    fleet = _Fleet(monkeypatch, fetch_rc=1)
    assert _run_update() == 1
    assert fleet.pushes() == []
    assert fleet.comments == []


def test_update_branches_checkout_failure_fails_the_run(monkeypatch):
    fleet = _Fleet(monkeypatch, checkout_rc=1)
    assert _run_update() == 1
    assert not any(call[0] == "merge" for call in fleet.git_calls)
    assert fleet.pushes() == []


def test_update_branches_failed_merge_abort_resets(monkeypatch):
    fleet = _Fleet(monkeypatch, merge_rc=1, abort_rc=1)
    assert _run_update() == 0
    assert ("reset", "-q", "--hard") in fleet.git_calls
    assert fleet.pushes() == []


def test_update_branches_merge_abort_success_needs_no_reset(monkeypatch):
    fleet = _Fleet(monkeypatch, merge_rc=1)
    assert _run_update() == 0
    assert not any(call[0] == "reset" for call in fleet.git_calls)
