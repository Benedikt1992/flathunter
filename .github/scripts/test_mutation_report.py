"""Tests for the mutation report helpers (run with `python -m pytest .github/scripts`)."""

import json
import subprocess
import sys
from collections import Counter

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
        ],
    )
    assert mr.changed_scripts("base") == []
    assert mr.changed_sources("base") == []


@given(st.lists(st.text(alphabet="abcdt_./", max_size=30), max_size=10))
def test_changed_scripts_property(files):
    with pytest.MonkeyPatch.context() as monkeypatch:
        _fake_git(monkeypatch, files)
        found = mr.changed_scripts("base")
    for path in found:
        assert path in files
        name = path.removeprefix(".github/scripts/")
        assert path.startswith(".github/scripts/")
        assert path.endswith(".py")
        assert "/" not in name
        assert not name.startswith("test_")


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


def test_scripts_workspace_copies_scripts_and_cleans_up():
    with mr.scripts_workspace() as (work, write):
        assert (work / "mutation_report.py").exists()
        assert (work / "test_mutation_report.py").exists()
        write("[tool.mutmut]\n")
        assert (work / "pyproject.toml").read_text(encoding="utf-8") == "[tool.mutmut]\n"
        assert work != mr.ROOT
    assert not work.exists()


class FakeMutmut:
    """Stand-in for `run`: git reports `files`, mutmut reports canned results."""

    def __init__(self, files, results):
        self.files, self.results, self.calls = files, results, []

    def __call__(self, cmd, cwd=mr.ROOT, **_kwargs):
        self.calls.append((cmd, cwd))
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
    fake = FakeMutmut(files, results)
    monkeypatch.setattr(mr, "run", fake)
    monkeypatch.setattr(mr, "PYPROJECT", tmp_path / "pyproject.toml")
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n", encoding="utf-8")
    monkeypatch.setattr(mr, "functions_touching", lambda _source, _lines: {"f"})
    monkeypatch.setattr(sys, "argv", ["mr", "--base", "b", "--out", str(tmp_path / "out")])
    code = mr.main()
    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    return code, report, summary, fake


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
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == "[tool.x]\n"


def test_main_only_script_tests_changed_mutates_nothing(monkeypatch, tmp_path):
    code, report, summary, fake = _main(
        monkeypatch, tmp_path, [".github/scripts/test_agent_ops.py"], ""
    )
    assert code == 0
    assert "nothing to mutate" in report
    assert summary == {}
    assert fake.mutmut_calls("run") == []


def test_main_flathunter_only_unchanged(monkeypatch, tmp_path):
    results = "flathunter.filter.x_f__mutmut_1: survived\n"
    _code, report, summary, fake = _main(monkeypatch, tmp_path, ["flathunter/filter.py"], results)
    assert "| `flathunter/filter.py` | 0 | 1 | 0 | 0 | 0% |" in report
    assert summary["changed_function_survivors"] == ["flathunter.filter.x_f__mutmut_1"]
    assert fake.mutmut_calls("run") == [mr.ROOT]
    assert fake.mutmut_calls("results") == [mr.ROOT]
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == "[tool.x]\n"


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


def test_render_reports_deselected_and_timeout():
    group = mr.Group({}, [], ["t::a"], [], timed_out=True)
    text = "\n".join(mr.render([group], 99))
    assert "1 test(s) failing before mutation were deselected" in text
    assert "stopped after 99s" in text
