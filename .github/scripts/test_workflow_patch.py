"""Tests for `workflow_patch.py` (offline: a local git repository per test).

Run with `python -m pytest .github/scripts`. The last test checks the patch of the
branch under test itself, so CI fails while `.github/workflows.patch` cannot be applied.
"""

import subprocess
from pathlib import Path

import pytest
import workflow_patch as wp
from hypothesis import given
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[2]
CI = ".github/workflows/ci.yml"
NEW = ".github/workflows/new.yml"
CI_TEXT = "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
CI_EDITED = CI_TEXT.replace("ubuntu-latest", "ubuntu-24.04")
NEW_TEXT = "name: New\non: workflow_dispatch\n"


def sh(*args):
    """Run git in the current directory and return stdout; fail the test on errors."""
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


def commit_all(message):
    sh("add", "-A")
    sh("commit", "-q", "-m", message)


def status():
    return sh("status", "--porcelain", "--untracked-files=all").splitlines()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository with one workflow, a README and a source file; cwd is its root."""
    monkeypatch.chdir(tmp_path)
    sh("init", "-q", "-b", "main")
    sh("config", "user.name", "Test")
    sh("config", "user.email", "test@example.invalid")
    write(CI, CI_TEXT)
    write("README.md", "readme\n")
    write("flathunter/app.py", "x = 1\n")
    commit_all("base")
    return tmp_path


def make_patch(capsys):
    """Edit the workflow, add a new one and turn both into a committed patch."""
    write(CI, CI_EDITED)
    write(NEW, NEW_TEXT)
    assert wp.make() == 0
    capsys.readouterr()
    commit_all("patch")


def test_outside_workflows_keeps_order_and_ignores_workflow_files():
    paths = [".github/workflows/a.yml", "b.py", ".github/workflows/x/c.yml", ".github/a.yml"]
    assert wp.outside_workflows(paths) == ["b.py", ".github/a.yml"]


@given(
    inside=st.lists(st.text(min_size=1).map(lambda name: wp.WORKFLOWS + name)),
    outside=st.lists(st.text().filter(lambda name: not name.startswith(wp.WORKFLOWS))),
    data=st.data(),
)
def test_outside_workflows_finds_exactly_the_other_paths(inside, outside, data):
    paths = data.draw(st.permutations(inside + outside))
    assert wp.outside_workflows(paths) == [path for path in paths if path in set(outside)]


@pytest.mark.usefixtures("repo")
def test_no_patch_means_no_problems():
    assert wp.problems() == []
    assert wp.check() == 0


@pytest.mark.usefixtures("repo")
def test_make_writes_the_patch_and_restores_the_workflows(capsys):
    write(CI, CI_EDITED)
    write(NEW, NEW_TEXT)
    assert wp.make() == 0
    assert capsys.readouterr().out == (
        "Wrote .github/workflows.patch; .github/workflows/ is back to HEAD. "
        "Commit .github/workflows.patch, never .github/workflows/.\n"
    )
    assert status() == ["?? .github/workflows.patch"]
    assert Path(CI).read_text(encoding="utf-8") == CI_TEXT
    assert not Path(NEW).exists()
    patch = Path(wp.PATCH).read_text(encoding="utf-8")
    assert "+    runs-on: ubuntu-24.04" in patch
    assert "new file mode 100644" in patch
    assert "+name: New" in patch
    assert wp.problems() == []


@pytest.mark.usefixtures("repo")
def test_make_keeps_staged_changes_outside_workflows(capsys):
    write("flathunter/app.py", "x = 2\n")
    sh("add", "flathunter/app.py")
    write(CI, CI_EDITED)
    assert wp.make() == 0
    assert capsys.readouterr().out.startswith("Wrote ")
    assert status() == ["M  flathunter/app.py", "?? .github/workflows.patch"]


@pytest.mark.usefixtures("repo")
def test_make_includes_staged_workflow_edits(capsys):
    write(CI, CI_EDITED)
    sh("add", CI)
    assert wp.make() == 0
    assert capsys.readouterr().out.startswith("Wrote ")
    assert "+    runs-on: ubuntu-24.04" in Path(wp.PATCH).read_text(encoding="utf-8")
    assert status() == ["?? .github/workflows.patch"]


@pytest.mark.usefixtures("repo")
def test_make_reports_edits_it_cannot_undo(capsys):
    Path(".github/workflows/icon.bin").write_bytes(b"\0\1\2")
    assert wp.make() == 1
    out = capsys.readouterr().out
    assert out.startswith("Wrote .github/workflows.patch, but could not undo the edits: error: ")
    assert out.endswith("error: .github/workflows/icon.bin: patch does not apply\n")
    assert Path(".github/workflows/icon.bin").exists()
    assert status() == ["?? .github/workflows.patch", "?? .github/workflows/icon.bin"]


@pytest.mark.usefixtures("repo")
def test_make_without_workflow_changes_writes_nothing(capsys):
    write("README.md", "changed\n")
    assert wp.make() == 1
    assert capsys.readouterr().out == (
        "No changes under .github/workflows/; .github/workflows.patch is unchanged.\n"
    )
    assert status() == [" M README.md"]


@pytest.mark.usefixtures("repo")
def test_make_refuses_to_drop_a_patch_that_is_not_open(capsys):
    make_patch(capsys)
    before = Path(wp.PATCH).read_text(encoding="utf-8")
    write(".github/workflows/other.yml", NEW_TEXT)
    assert wp.make() == 1
    assert capsys.readouterr().out == (
        ".github/workflows/ does not contain .github/workflows.patch: "
        "run `open` first, or its changes are lost.\n"
    )
    assert Path(wp.PATCH).read_text(encoding="utf-8") == before
    assert Path(CI).read_text(encoding="utf-8") == CI_TEXT
    assert status() == ["?? .github/workflows/other.yml"]


@pytest.mark.usefixtures("repo")
def test_open_brings_the_edits_back_and_make_keeps_them(capsys):
    make_patch(capsys)
    assert wp.open_patch() == 0
    assert capsys.readouterr().out == (
        "Applied .github/workflows.patch to the working tree. "
        "Edit .github/workflows/, then run `make`.\n"
    )
    assert Path(CI).read_text(encoding="utf-8") == CI_EDITED
    assert Path(NEW).read_text(encoding="utf-8") == NEW_TEXT
    write(NEW, NEW_TEXT + "jobs: {}\n")
    assert wp.make() == 0
    patch = Path(wp.PATCH).read_text(encoding="utf-8")
    assert "+    runs-on: ubuntu-24.04" in patch
    assert "+jobs: {}" in patch
    assert status() == [" M .github/workflows.patch"]


@pytest.mark.usefixtures("repo")
def test_open_without_patch_fails(capsys):
    assert wp.open_patch() == 1
    assert capsys.readouterr().out == "No .github/workflows.patch to open.\n"


@pytest.mark.usefixtures("repo")
def test_open_twice_reports_git_error(capsys):
    make_patch(capsys)
    assert wp.open_patch() == 0
    capsys.readouterr()
    assert wp.open_patch() == 1
    out = capsys.readouterr().out
    assert out.startswith("error: ")
    assert out.endswith("\n")
    assert not out.endswith("\n\n")


@pytest.mark.usefixtures("repo")
def test_patch_outside_workflows_is_reported(capsys):
    write(CI, CI_EDITED)
    write("README.md", "changed\n")
    write("flathunter/app.py", "x = 2\n")
    write(wp.PATCH, sh("diff", "HEAD"))
    sh("checkout", "--", ".")
    message = (
        ".github/workflows.patch changes files outside .github/workflows/: "
        "README.md, flathunter/app.py"
    )
    assert wp.problems() == [message]
    assert wp.check() == 1
    assert capsys.readouterr().out == message + "\n"


@pytest.mark.usefixtures("repo")
def test_both_sides_of_a_rename_are_checked():
    sh("mv", "README.md", ".github/workflows/readme.yml")
    write(wp.PATCH, sh("diff", "--cached", "-M", "HEAD"))
    sh("reset", "-q", "--hard")
    assert "rename from README.md" in Path(wp.PATCH).read_text(encoding="utf-8")
    assert wp.problems() == [
        ".github/workflows.patch changes files outside .github/workflows/: README.md"
    ]


@pytest.mark.usefixtures("repo")
def test_staged_changes_of_the_branch_do_not_count(capsys):
    make_patch(capsys)
    write("flathunter/app.py", "x = 2\n")
    sh("add", "flathunter/app.py")
    assert wp.problems() == []


@pytest.mark.usefixtures("repo")
def test_patch_that_no_longer_applies_is_reported(capsys):
    make_patch(capsys)
    write(CI, CI_TEXT.replace("ubuntu-latest", "macos-latest"))
    commit_all("base moved")
    found = wp.problems()
    assert len(found) == 1
    assert found[0].startswith(".github/workflows.patch does not apply to this branch: error: ")
    assert found[0] == found[0].strip()


@pytest.mark.usefixtures("repo")
def test_empty_patch_is_reported():
    write(wp.PATCH, "")
    found = wp.problems()
    assert len(found) == 1
    assert found[0].startswith(".github/workflows.patch does not apply to this branch: ")
    assert "No valid patches" in found[0]


@pytest.mark.usefixtures("repo")
def test_apply_stages_the_workflows_and_removes_the_patch(capsys):
    make_patch(capsys)
    assert wp.apply_patch() == 0
    assert capsys.readouterr().out == (
        "Applied and staged .github/workflows.patch, and removed it. "
        "Check `git diff --cached`, then commit.\n"
    )
    assert sorted(status()) == [
        "A  .github/workflows/new.yml",
        "D  .github/workflows.patch",
        "M  .github/workflows/ci.yml",
    ]
    assert Path(CI).read_text(encoding="utf-8") == CI_EDITED
    assert not Path(wp.PATCH).exists()


@pytest.mark.usefixtures("repo")
def test_apply_refuses_a_patch_with_problems(capsys):
    write("flathunter/app.py", "x = 2\n")
    write(wp.PATCH, sh("diff", "HEAD"))
    sh("checkout", "--", ".")
    commit_all("bad patch")
    assert wp.apply_patch() == 1
    assert capsys.readouterr().out == (
        ".github/workflows.patch changes files outside .github/workflows/: flathunter/app.py\n"
    )
    assert status() == []


@pytest.mark.usefixtures("repo")
def test_apply_reports_a_patch_that_conflicts_with_the_working_tree(capsys):
    make_patch(capsys)
    write(CI, "local edit\n")
    assert wp.apply_patch() == 1
    assert capsys.readouterr().out.startswith("error: ")
    assert Path(wp.PATCH).exists()


@pytest.mark.usefixtures("repo")
def test_apply_without_patch_fails(capsys):
    assert wp.apply_patch() == 1
    assert capsys.readouterr().out == "No .github/workflows.patch on this branch.\n"


@pytest.mark.usefixtures("repo")
def test_main_runs_the_named_command(capsys):
    write(wp.PATCH, "")
    assert wp.main(["check"]) == 1
    assert "does not apply" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        wp.main(["push"])


def test_help_starts_with_the_module_summary(capsys):
    with pytest.raises(SystemExit):
        wp.main(["--help"])
    assert "Workflow changes of an agent PR, kept as a patch file" in capsys.readouterr().out


def test_this_branch_has_no_broken_workflow_patch(monkeypatch):
    monkeypatch.chdir(ROOT)
    assert wp.problems() == []
