"""Tests for the mutation report helpers (run with `python -m pytest .github/scripts`)."""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import mutation_report as mr
import pytest
from hypothesis import given
from hypothesis import strategies as st

SOURCE = """
def top(x):
    return x + 1

class Helper:
    def get_price(self, expose):
        value = expose["price"]
        return value

    def untouched(self):
        return 0
"""


def test_mutant_location_for_methods_and_functions():
    assert mr.mutant_location("flathunter.filter.xǁExposeHelperǁget_price__mutmut_20") == (
        "flathunter/filter.py",
        "ExposeHelper.get_price",
    )
    assert mr.mutant_location("flathunter.utils.list.x_chunk_list__mutmut_3") == (
        "flathunter/utils/list.py",
        "chunk_list",
    )
    assert mr.mutant_location("flathunter.x.x__private__mutmut_1") == (
        "flathunter/x.py",
        "_private",
    )


def test_functions_touching_changed_lines():
    assert mr.functions_touching(SOURCE, {7}) == {"Helper.get_price"}
    assert mr.functions_touching(SOURCE, {3, 11}) == {"top", "Helper.untouched"}
    assert mr.functions_touching(SOURCE, {5}) == set()  # class line only


def test_classify_separates_changed_functions():
    results = """
    flathunter.filter.xǁHelperǁget_price__mutmut_1: survived
    flathunter.filter.xǁHelperǁget_price__mutmut_2: killed
    flathunter.filter.xǁHelperǁuntouched__mutmut_1: survived
    flathunter.other.x_f__mutmut_1: survived
"""
    statuses, relevant = mr.classify(results, {"flathunter/filter.py": {"Helper.get_price"}})
    assert statuses == {"flathunter/filter.py": Counter(survived=2, killed=1)}
    assert relevant == ["flathunter.filter.xǁHelperǁget_price__mutmut_1"]


def test_mutmut_section_deselects_failing_tests():
    section = mr.mutmut_section(["flathunter/filter.py"], ["test/a.py::t"])
    assert 'source_paths = ["flathunter/filter.py"]' in section
    assert '["-m", "not live", "--deselect", "test/a.py::t"]' in section
    assert '"flathunter/"' in section


def _fake_git(monkeypatch, files):
    """Make `git diff --name-only` report `files`."""

    def fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 0, "\n".join(files), "")

    monkeypatch.setattr(mr, "run", fake_run)


def test_changed_sources_and_scripts_are_split(monkeypatch):
    _fake_git(monkeypatch, [".github/scripts/agent_ops.py", "flathunter/x.py", "README.md"])
    assert mr.changed_scripts("base") == [".github/scripts/agent_ops.py"]
    assert mr.changed_sources("base") == ["flathunter/x.py"]


def test_changed_scripts_excludes_tests_and_other_folders(monkeypatch):
    _fake_git(
        monkeypatch,
        [
            ".github/scripts/test_agent_ops.py",
            ".github/scripts/sub/x.py",
            ".github/workflows/x.py",
            ".github/scripts/notes.md",
            "other/.github/scripts/y.py",
            "setup.py",
        ],
    )
    assert mr.changed_scripts("base") == []
    assert mr.changed_sources("base") == []


_DIRS = [".github/scripts/", "flathunter/", "", ".github/scripts/sub/", "other/.github/scripts/"]
_NAMES = st.builds(
    lambda prefix, stem, ext: prefix + stem + ext,
    st.sampled_from(["test_", ""]),
    st.text(alphabet="abc_", min_size=1, max_size=5),
    st.sampled_from([".py", ".md"]),
)


@given(st.lists(st.builds(lambda d, n: d + n, st.sampled_from(_DIRS), _NAMES), max_size=10))
def test_changed_scripts_property(files):
    with pytest.MonkeyPatch.context() as monkeypatch:
        _fake_git(monkeypatch, files)
        found = mr.changed_scripts("base")
    scripts = ".github/scripts/"
    expected = sorted(
        p
        for p in files
        if p.startswith(scripts)
        and p.endswith(".py")
        and "/" not in p.removeprefix(scripts)
        and not p.removeprefix(scripts).startswith("test_")
    )
    assert found == expected


def test_mutant_location_with_prefix():
    prefix = ".github/scripts/"
    assert mr.mutant_location("agent_ops.xǁCǁm__mutmut_3", prefix) == (
        ".github/scripts/agent_ops.py",
        "C.m",
    )
    assert mr.mutant_location("agent_ops.x_f__mutmut_1", prefix) == (
        ".github/scripts/agent_ops.py",
        "f",
    )


def test_classify_with_prefix_ignores_other_modules():
    results = """
    agent_ops.x_f__mutmut_1: survived
    agent_ops.x_f__mutmut_2: killed
    agent_ops.x_g__mutmut_1: survived
    ratchet.x_f__mutmut_1: survived
"""
    statuses, relevant = mr.classify(
        results, {".github/scripts/agent_ops.py": {"f"}}, ".github/scripts/"
    )
    assert statuses == {".github/scripts/agent_ops.py": Counter(survived=2, killed=1)}
    assert relevant == ["agent_ops.x_f__mutmut_1"]


def test_mutmut_section_for_scripts():
    section = mr.mutmut_section(
        ["agent_ops.py"], [], copy=["agent_ops.py", "test_agent_ops.py"], selection=["."]
    )
    assert 'source_paths = ["agent_ops.py"]' in section
    assert 'also_copy = ["agent_ops.py", "test_agent_ops.py"]' in section
    assert 'pytest_add_cli_args_test_selection = ["."]' in section
    assert "flathunter" not in section
    assert "test/" not in section


def test_mutmut_section_defaults_for_flathunter():
    section = mr.mutmut_section(["flathunter/x.py"], [])
    assert 'pytest_add_cli_args_test_selection = ["test/"]' in section
    assert '"flathunter/"' in section


REPO_FILES = {
    ".github/scripts/agent_ops.py": "def f():\n    return 1\n",
    ".github/scripts/mutation_report.py": "ROOT = None\n",
    ".github/scripts/test_mutation_report.py": "def test_x():\n    pass\n",
    ".github/scripts/notes.md": "not a script\n",
    ".github/scripts/sub/helper.py": "def g():\n    return 3\n",
    ".github/workflows/agent-fix.yml": "name: fix\n",
    "flathunter/filter.py": "def f():\n    return 2\n",
    "pyproject.toml": "[tool.x]\n",
}


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """A minimal repository (REPO_FILES) as `mr.ROOT`, so tests never read the real one.

    The tests also run inside mutmut's copy of this folder, where the real files are
    not at `parents[2]` of the script.
    """
    root = tmp_path / "repo"
    for name, text in REPO_FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(mr, "ROOT", root)
    monkeypatch.setattr(mr, "PYPROJECT", root / "pyproject.toml")
    return root


def test_scripts_workspace_copies_scripts_and_workflows(fake_root):
    with mr.scripts_workspace() as (work, write):
        # only the scripts directly in .github/scripts/, unchanged
        assert sorted(p.name for p in work.iterdir() if p.is_file()) == [
            "agent_ops.py",
            "mutation_report.py",
            "test_mutation_report.py",
        ]
        assert (work / "agent_ops.py").read_text(encoding="utf-8") == "def f():\n    return 1\n"
        # tests read ../workflows: from the copy (baseline) and from mutmut's mutants/
        for workflows in (work.parent / "workflows", work / "workflows"):
            assert (workflows / "agent-fix.yml").read_text(encoding="utf-8") == "name: fix\n"
        write("[tool.mutmut]\n")
        assert (work / "pyproject.toml").read_text(encoding="utf-8") == "[tool.mutmut]\n"
        assert not work.is_relative_to(fake_root)
    assert not work.parent.exists()
    # the repository itself is never written to
    assert sorted(str(p.relative_to(fake_root)) for p in fake_root.rglob("*") if p.is_file()) == (
        sorted(REPO_FILES)
    )


class FakeMutmut:
    """Stand-in for `run`: git reports `files`, mutmut reports canned results."""

    def __init__(self, files, results):
        self.files, self.results, self.calls = files, results, []
        self.sections = []  # pyproject.toml of the directory at each `mutmut run`

    def __call__(self, cmd, cwd=mr.ROOT, **_kwargs):
        self.calls.append((cmd, cwd))
        if cmd[:2] == ["mutmut", "run"]:
            self.sections.append((Path(cwd) / "pyproject.toml").read_text(encoding="utf-8"))
        out = ""
        if cmd[:2] == ["git", "diff"]:
            out = "\n".join(self.files) if "--name-only" in cmd else "@@ -1 +1,2 @@"
        elif cmd[:2] == ["mutmut", "results"]:
            out = self.results
        elif cmd[:2] == ["mutmut", "show"]:
            out = "-    a\n+    b"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def mutmut_calls(self, sub):
        return [cwd for cmd, cwd in self.calls if cmd[:2] == ["mutmut", sub]]


def _main(monkeypatch, tmp_path, files, results):
    """Run `main()` against the `fake_root` repository with faked git and mutmut."""
    fake = FakeMutmut(files, results)
    monkeypatch.setattr(mr, "run", fake)
    monkeypatch.setattr(mr, "functions_touching", lambda _source, _lines: {"f"})
    monkeypatch.setattr(sys, "argv", ["mr", "--base", "b", "--out", str(tmp_path / "out")])
    code = mr.main()
    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    return code, report, summary, fake


@pytest.mark.usefixtures("fake_root")
def test_main_scripts_only_reports_agent_ops(monkeypatch, tmp_path):
    results = "agent_ops.x_f__mutmut_1: survived\nagent_ops.x_f__mutmut_2: killed\n"
    code, report, summary, fake = _main(
        monkeypatch, tmp_path, [".github/scripts/agent_ops.py"], results
    )
    assert code == 0
    assert "| `.github/scripts/agent_ops.py` | 1 | 1 | 0 | 0 | 50% |" in report
    assert "agent_ops.x_f__mutmut_1" in report
    assert "Surviving mutants in changed functions: 1" in report
    assert "nothing to mutate" not in report
    assert summary["files"] == {".github/scripts/agent_ops.py": {"survived": 1, "killed": 1}}
    assert summary["changed_function_survivors"] == ["agent_ops.x_f__mutmut_1"]
    # only the scripts pass ran: in a temp copy, never in the repository
    cwds = fake.mutmut_calls("run")
    assert len(cwds) == 1
    assert cwds[0] != mr.ROOT
    assert set(fake.mutmut_calls("show")) == set(cwds)
    assert mr.PYPROJECT.read_text(encoding="utf-8") == "[tool.x]\n"
    # the mutated script is not also copied unmutated, its tests are
    (section,) = fake.sections
    assert 'source_paths = ["agent_ops.py"]' in section
    assert 'pytest_add_cli_args_test_selection = ["."]' in section
    match = re.search(r"^also_copy = (.*)$", section, re.MULTILINE)
    assert match
    copied = json.loads(match.group(1))
    assert "agent_ops.py" not in copied
    assert "test_mutation_report.py" in copied


def test_mutmut_section_keeps_explicit_empty_selection():
    section = mr.mutmut_section(["a.py"], [], copy=[], selection=[])
    assert "pytest_add_cli_args_test_selection = []" in section
    assert "also_copy = []" in section


@pytest.mark.usefixtures("fake_root")
def test_main_only_script_tests_changed_mutates_nothing(monkeypatch, tmp_path):
    code, report, summary, fake = _main(
        monkeypatch, tmp_path, [".github/scripts/test_agent_ops.py"], ""
    )
    assert code == 0
    assert "nothing to mutate" in report
    assert summary == {}
    assert fake.mutmut_calls("run") == []


@pytest.mark.usefixtures("fake_root")
def test_main_flathunter_only_unchanged(monkeypatch, tmp_path):
    results = "flathunter.filter.x_f__mutmut_1: survived\n"
    _code, report, summary, fake = _main(monkeypatch, tmp_path, ["flathunter/filter.py"], results)
    assert "| `flathunter/filter.py` | 0 | 1 | 0 | 0 | 0% |" in report
    assert summary["changed_function_survivors"] == ["flathunter.filter.x_f__mutmut_1"]
    assert fake.mutmut_calls("run") == [mr.ROOT]
    assert fake.mutmut_calls("results") == [mr.ROOT]
    assert mr.PYPROJECT.read_text(encoding="utf-8") == "[tool.x]\n"


@pytest.mark.usefixtures("fake_root")
def test_main_both_groups_in_one_report(monkeypatch, tmp_path):
    results = "flathunter.filter.x_f__mutmut_1: survived\nagent_ops.x_f__mutmut_1: survived\n"
    _code, report, summary, fake = _main(
        monkeypatch, tmp_path, ["flathunter/filter.py", ".github/scripts/agent_ops.py"], results
    )
    assert "`flathunter/filter.py`" in report
    assert "`.github/scripts/agent_ops.py`" in report
    assert "Surviving mutants in changed functions: 2" in report
    assert len(fake.mutmut_calls("run")) == 2
    assert len(summary["files"]) == 2


def _proc(code=0, out="", err=""):
    return subprocess.CompletedProcess(["cmd"], code, out, err)


class FakeRun:
    """Stand-in for `run`: records (cmd, timeout, cwd) and answers from a list.

    An exception in the list is raised instead; after the list, calls succeed silently.
    """

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, cmd, timeout=None, cwd=None):
        self.calls.append((cmd, timeout, cwd))
        answer = self.answers.pop(0) if self.answers else _proc()
        if isinstance(answer, BaseException):
            raise answer
        return answer


def test_mutant_location_strips_only_the_last_suffix_and_the_x_prefix():
    assert mr.mutant_location("m.x_a__mutmut_b__mutmut_7") == ("m.py", "a__mutmut_b")
    # a class whose name starts with "x_" keeps it
    assert mr.mutant_location("m.xǁx_Cfgǁload__mutmut_1") == ("m.py", "x_Cfg.load")


def test_changed_files_compare_added_and_modified_files_with_the_merge_base(monkeypatch):
    listing = "flathunter/a.py\n.github/scripts/b.py\n"
    fake = FakeRun(_proc(0, listing), _proc(0, listing))
    monkeypatch.setattr(mr, "run", fake)
    assert mr.changed_sources("origin/x") == ["flathunter/a.py"]
    assert mr.changed_scripts("origin/x") == [".github/scripts/b.py"]
    git = ["git", "diff", "--name-only", "--diff-filter=AM", "origin/x...HEAD"]
    assert fake.calls == [(git, None, None), (git, None, None)]


def test_failing_tests_runs_the_offline_tests_and_collects_node_ids(monkeypatch, tmp_path):
    output = (
        "FAILED test_a.py::test_x - AssertionError\n"
        "ERROR test_b.py::test_y\n"
        "FAILED test_a.py::test_x - reported twice\n"
        "  FAILED indented.py::not_a_summary_line\n"
        "1 failed, 1 error in 0.1s\n"
    )
    fake = FakeRun(_proc(1, output))
    monkeypatch.setattr(mr, "run", fake)
    assert mr.failing_tests(tmp_path, (".",)) == ["test_a.py::test_x", "test_b.py::test_y"]
    pytest_cmd = [sys.executable, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider"]
    assert fake.calls == [([*pytest_cmd, "-m", "not live", "."], None, tmp_path)]


def _section(paths, deselect):
    return f"{paths} {deselect}"


def test_run_mutmut_runs_once_when_mutmut_succeeds(monkeypatch, tmp_path):
    fake = FakeRun(_proc(0))
    monkeypatch.setattr(mr, "run", fake)
    written = []
    assert mr.run_mutmut(["a.py"], [], 77, written.append, tmp_path, _section) == ""
    assert fake.calls == [(["mutmut", "run", "--max-children", "4"], 77, tmp_path)]
    assert written == ["['a.py'] []"]


def test_run_mutmut_deselects_tests_failing_in_the_sandbox_and_retries(monkeypatch, tmp_path):
    fake = FakeRun(
        _proc(1, "FAILED t.py::a - boom\nERROR t.py::old\n", "ERROR t.py::b\n"), _proc(0)
    )
    monkeypatch.setattr(mr, "run", fake)
    deselect = ["t.py::old"]
    written = []
    assert mr.run_mutmut(["a.py"], deselect, 5, written.append, tmp_path, _section) == ""
    assert deselect == ["t.py::old", "t.py::a", "t.py::b"]
    assert written == [
        "['a.py'] ['t.py::old']",
        "['a.py'] ['t.py::old', 't.py::a', 't.py::b']",
    ]
    assert len(fake.calls) == 2


def test_run_mutmut_reports_a_failure_that_names_no_test(monkeypatch, tmp_path):
    # mutmut 3.7 with pytest 9.2: every run ended like this and looked like 0 mutants
    output = (
        "AttributeError: 'Function' object has no attribute '_nodeid'\n\n"
        "failed to collect stats. runner returned 1\n\n"
    )
    monkeypatch.setattr(mr, "run", FakeRun(_proc(2, output)))
    warning = mr.run_mutmut(["a.py"], [], 5, lambda _text: None, tmp_path, _section)
    assert warning == "mutmut failed (exit code 2): `failed to collect stats. runner returned 1`"


def test_run_mutmut_gives_up_after_three_attempts(monkeypatch, tmp_path):
    fake = FakeRun(*(_proc(1, f"FAILED t.py::{name}\n") for name in "abcd"))
    monkeypatch.setattr(mr, "run", fake)
    deselect = []
    warning = mr.run_mutmut(["a.py"], deselect, 5, lambda _text: None, tmp_path, _section)
    assert warning == "mutmut still failed after 3 attempts; results are incomplete."
    assert deselect == ["t.py::a", "t.py::b", "t.py::c"]
    assert len(fake.calls) == 3


def test_run_mutmut_reports_a_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(mr, "run", FakeRun(subprocess.TimeoutExpired(["mutmut"], 9)))
    warning = mr.run_mutmut(["a.py"], [], 9, lambda _text: None, tmp_path, _section)
    assert warning == "mutmut stopped after 9s; partial results."


def test_last_line_skips_blank_lines_and_trims():
    assert mr.last_line("first\n  second  \n\n   \n") == "`second`"
    assert mr.last_line("\n \n") == "no output"


def test_render_survivors_shows_each_diff_up_to_the_limit(monkeypatch, tmp_path):
    names = [f"m.x_f__mutmut_{i}" for i in range(mr.MAX_SHOWN + 1)]
    fake = FakeRun(*(_proc(0, f"  -a{i}\n+b{i}\n") for i in range(len(names))))
    monkeypatch.setattr(mr, "run", fake)
    lines = mr.render_survivors(names, tmp_path)
    assert lines[:14] == [
        *["", "<details><summary><code>m.x_f__mutmut_0</code></summary>", ""],
        *["```diff", "-a0\n+b0", "```", "</details>"],
        *["", "<details><summary><code>m.x_f__mutmut_1</code></summary>", ""],
        *["```diff", "-a1\n+b1", "```", "</details>"],
    ]
    assert len(lines) == 7 * mr.MAX_SHOWN
    assert fake.calls[0] == (["mutmut", "show", "m.x_f__mutmut_0"], None, tmp_path)
    assert len(fake.calls) == mr.MAX_SHOWN


HEADER = ["| File | killed | survived | no tests | timeout | score |", "|---|---|---|---|---|---|"]


def test_render_lists_files_survivors_deselected_tests_and_warnings():
    first = mr.Group(
        {"a.py": Counter(killed=3, survived=1, timeout=2), "b.py": Counter({"no tests": 4})},
        ["a.x_f__mutmut_1"],
        ["t::x"],
        ["<details a>"],
        "",
    )
    second = mr.Group({}, [], ["t::y"], [], "mutmut stopped after 9s; partial results.")
    assert mr.render([first, second]) == [
        *HEADER,
        "| `a.py` | 3 | 1 | 0 | 2 | 75% |",
        "| `b.py` | 0 | 0 | 4 | 0 | - |",
        "",
        "**Surviving mutants in changed functions: 1**",
        "",
        "Each one is a code change the tests did not notice:",
        "<details a>",
        "",
        "_2 test(s) failing before mutation were deselected._",
        "",
        "**Warning:** mutmut stopped after 9s; partial results.",
    ]


def test_render_without_survivors_has_no_hint_and_no_warning():
    group = mr.Group({"a.py": Counter(killed=2)}, [], [], [], "")
    assert mr.render([group]) == [
        *HEADER,
        "| `a.py` | 2 | 0 | 0 | 0 | 100% |",
        "",
        "**Surviving mutants in changed functions: 0**",
        "",
    ]


@pytest.mark.usefixtures("fake_root")
def test_touched_reads_the_file_and_its_changed_lines(monkeypatch):
    fake = FakeRun(_proc(0, "@@ -0,0 +1,2 @@\n"))
    monkeypatch.setattr(mr, "run", fake)
    assert mr._touched("origin/x", ["flathunter/filter.py"]) == {"flathunter/filter.py": {"f"}}
    git = ["git", "diff", "--unified=0", "origin/x...HEAD", "--", "flathunter/filter.py"]
    assert fake.calls == [(git, None, None)]


def test_collect_reads_all_results_of_the_pass_directory(monkeypatch, tmp_path):
    results = "    agent_ops.x_f__mutmut_1: survived\n    agent_ops.x_g__mutmut_1: killed\n"
    fake = FakeRun(_proc(0, results), _proc(0, "-a\n+b"))
    monkeypatch.setattr(mr, "run", fake)
    touched = {".github/scripts/agent_ops.py": {"f"}}
    group = mr._collect(touched, ["t::a"], tmp_path, ".github/scripts/", "careful")
    assert group == mr.Group(
        {".github/scripts/agent_ops.py": Counter(survived=1, killed=1)},
        ["agent_ops.x_f__mutmut_1"],
        ["t::a"],
        [
            *["", "<details><summary><code>agent_ops.x_f__mutmut_1</code></summary>", ""],
            *["```diff", "-a\n+b", "```", "</details>"],
        ],
        "careful",
    )
    assert fake.calls[:2] == [
        (["mutmut", "results", "--all", "true"], None, tmp_path),
        (["mutmut", "show", "agent_ops.x_f__mutmut_1"], None, tmp_path),
    ]


def _fake_pass_steps(monkeypatch):
    """Replace the steps of a mutation pass; returns the list of recorded calls."""
    calls = []

    def touched(base, paths):
        calls.append(("touched", base, paths))
        return {"x": {"f"}}

    def baseline(cwd, targets=()):
        calls.append(("baseline", cwd, targets))
        return ["t::a"]

    def run_mutmut(paths, deselect, timeout, write, cwd, make_section=mr.mutmut_section):
        write("[tool.mutmut]\n")
        calls.append(("mutmut", paths, list(deselect), timeout, cwd, make_section(paths, [])))
        return "careful"

    def collect(touched, deselect, cwd, prefix, warning):
        config = (cwd / "pyproject.toml").read_text(encoding="utf-8")
        calls.append(("collect", touched, deselect, cwd, prefix, warning, config))
        return "group"

    monkeypatch.setattr(mr, "_touched", touched)
    monkeypatch.setattr(mr, "failing_tests", baseline)
    monkeypatch.setattr(mr, "run_mutmut", run_mutmut)
    monkeypatch.setattr(mr, "_collect", collect)
    return calls


def test_mutate_flathunter_runs_in_the_repository(fake_root, monkeypatch):
    calls = _fake_pass_steps(monkeypatch)
    assert mr.mutate_flathunter("origin/x", ["flathunter/filter.py"], 42) == "group"
    section = mr.mutmut_section(["flathunter/filter.py"], [])
    config = "[tool.x]\n[tool.mutmut]\n"  # still in place while the results are read
    assert calls == [
        ("touched", "origin/x", ["flathunter/filter.py"]),
        ("baseline", fake_root, ()),
        ("mutmut", ["flathunter/filter.py"], ["t::a"], 42, fake_root, section),
        ("collect", {"x": {"f"}}, ["t::a"], fake_root, "", "careful", config),
    ]
    assert mr.PYPROJECT.read_text(encoding="utf-8") == "[tool.x]\n"


def test_mutate_scripts_runs_in_a_copy_on_the_tests_there(fake_root, monkeypatch):
    calls = _fake_pass_steps(monkeypatch)
    paths = [".github/scripts/agent_ops.py"]
    assert mr.mutate_scripts("origin/x", paths, 42) == "group"
    (_, work, targets) = calls[1]
    assert work.name == "scripts"
    assert not work.is_relative_to(fake_root)
    section = mr.mutmut_section(
        ["agent_ops.py"],
        [],
        copy=["mutation_report.py", "test_mutation_report.py"],
        selection=["."],
    )
    assert calls == [
        ("touched", "origin/x", paths),
        ("baseline", work, (".",)),
        ("mutmut", ["agent_ops.py"], ["t::a"], 42, work, section),
        ("collect", {"x": {"f"}}, ["t::a"], work, ".github/scripts/", "careful", "[tool.mutmut]\n"),
    ]
    assert targets == (".",)


def test_main_requires_a_base(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["mr"])
    with pytest.raises(SystemExit) as exit_info:
        mr.main()
    assert exit_info.value.code == 2
    assert "--base" in capsys.readouterr().err


def test_main_help_describes_the_options(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["mr", "--help"])
    with pytest.raises(SystemExit):
        mr.main()
    text = " ".join(capsys.readouterr().out.split())
    assert "Mutation testing for the source files a branch changed (mutmut), as a report." in text
    assert "--base BASE base ref, e.g. origin/fork-main" in text
    assert "--out OUT output directory" in text
    assert "--timeout TIMEOUT seconds for mutmut run" in text


def _fake_passes(monkeypatch, sources, scripts, clock):
    """Fake the change detection, both passes and the clock; returns the recorded calls."""
    seen = []

    def change(kind, files):
        return lambda base: seen.append((kind, base)) or files

    def mutate(kind):
        def run_pass(base, paths, timeout):
            seen.append((kind, base, paths, timeout))
            return mr.Group({}, [], [], [], "")

        return run_pass

    monkeypatch.setattr(mr, "changed_sources", change("sources", sources))
    monkeypatch.setattr(mr, "changed_scripts", change("scripts", scripts))
    monkeypatch.setattr(mr, "mutate_flathunter", mutate("flathunter pass"))
    monkeypatch.setattr(mr, "mutate_scripts", mutate("scripts pass"))
    ticks = iter(clock)
    monkeypatch.setattr(mr, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    return seen


def _argv(monkeypatch, tmp_path, *extra):
    monkeypatch.setattr(sys, "argv", ["mr", "--base", "b", "--out", str(tmp_path), *extra])


def test_main_splits_timeout_budget_between_passes(monkeypatch, tmp_path):
    seen = _fake_passes(
        monkeypatch, ["flathunter/x.py"], [".github/scripts/a.py"], [1000.0, 1300.5]
    )
    _argv(monkeypatch, tmp_path, "--timeout", "1001")
    assert mr.main() == 0
    assert seen == [
        ("sources", "b"),
        ("scripts", "b"),
        ("flathunter pass", "b", ["flathunter/x.py"], 500),
        # the scripts pass gets what the first one left: 1001 - 300 s
        ("scripts pass", "b", [".github/scripts/a.py"], 701),
    ]


def test_main_gives_a_single_pass_the_whole_budget(monkeypatch, tmp_path):
    seen = _fake_passes(monkeypatch, ["flathunter/x.py"], [], [0.0])
    _argv(monkeypatch, tmp_path, "--timeout", "1001")
    assert mr.main() == 0
    assert seen[-1] == ("flathunter pass", "b", ["flathunter/x.py"], 1001)


def test_main_keeps_one_second_for_the_scripts_pass_when_the_budget_is_spent(monkeypatch, tmp_path):
    seen = _fake_passes(monkeypatch, ["flathunter/x.py"], [".github/scripts/a.py"], [0.0, 50.0])
    _argv(monkeypatch, tmp_path, "--timeout", "10")
    assert mr.main() == 0
    assert seen[-1] == ("scripts pass", "b", [".github/scripts/a.py"], 1)


def test_main_defaults_to_20_minutes_and_mutation_report_folder(fake_root, monkeypatch):
    seen = _fake_passes(monkeypatch, [], [".github/scripts/a.py"], [7.0, 7.0])
    monkeypatch.setattr(sys, "argv", ["mr", "--base", "b"])
    assert mr.main() == 0
    assert seen[-1] == ("scripts pass", "b", [".github/scripts/a.py"], 1200)
    assert (fake_root / "mutation-report" / "report.md").is_file()


def test_main_writes_report_and_summary_and_prints_the_report(monkeypatch, tmp_path, capsys):
    group = mr.Group({"a.py": Counter(killed=1)}, ["a.x_f__mutmut_1"], ["t::a"], ["D"], "")
    _fake_passes(monkeypatch, ["flathunter/a.py"], [], [0.0])
    monkeypatch.setattr(mr, "mutate_flathunter", lambda *_args: group)
    out = tmp_path / "new" / "nested"
    _argv(monkeypatch, out)
    assert mr.main() == 0
    report = "\n".join(["## Mutation testing", "", *mr.render([group])]) + "\n"
    assert (out / "report.md").read_text(encoding="utf-8") == report
    summary = {
        "files": {"a.py": {"killed": 1}},
        "changed_function_survivors": ["a.x_f__mutmut_1"],
        "deselected": ["t::a"],
    }
    assert (out / "summary.json").read_text(encoding="utf-8") == json.dumps(summary, indent=2)
    assert capsys.readouterr().out == report + "\n"


def test_main_without_changes_says_there_is_nothing_to_mutate(monkeypatch, tmp_path):
    _fake_passes(monkeypatch, [], [], [0.0])
    _argv(monkeypatch, tmp_path)
    assert mr.main() == 0
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == (
        "## Mutation testing\n\n"
        "No changed modules under `flathunter/` or scripts under `.github/scripts/`; "
        "nothing to mutate.\n"
    )
    assert (tmp_path / "summary.json").read_text(encoding="utf-8") == "{}"
