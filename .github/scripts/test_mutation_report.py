"""Tests for the mutation report helpers (run with `python -m pytest .github/scripts`)."""

from collections import Counter

import mutation_report as mr

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
