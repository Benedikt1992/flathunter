"""Tests for the ratchet baselines (run with `python -m pytest .github/scripts`)."""

import json

import agent_guard as guard
import ratchet


def dumps(data):
    return json.dumps(data)


def test_ruff_baseline_may_only_shrink():
    base = dumps({"a.py::F401::x": 2, "b.py::D103::y": 1})
    assert ratchet.is_shrink("ruff", base, dumps({"a.py::F401::x": 1}))
    assert ratchet.is_shrink("ruff", base, dumps({}))
    assert not ratchet.is_shrink("ruff", base, dumps({"a.py::F401::x": 3}))
    assert not ratchet.is_shrink("ruff", base, dumps({"c.py::S307::z": 1}))


def test_format_baseline_may_only_shrink():
    base = dumps(["a.py", "b.py"])
    assert ratchet.is_shrink("format", base, dumps(["a.py"]))
    assert not ratchet.is_shrink("format", base, dumps(["a.py", "c.py"]))


def test_pyrefly_baseline_may_only_shrink():
    err = {"path": "a.py", "name": "bad-assignment", "concise_description": "x", "column": 3}
    moved = {**err, "column": 9}  # columns are ignored: moving code is not new debt
    new = {**err, "concise_description": "y"}
    base = dumps({"errors": [err, err]})
    assert ratchet.is_shrink("pyrefly", base, dumps({"errors": [moved]}))
    assert not ratchet.is_shrink("pyrefly", base, dumps({"errors": [err, err, err]}))
    assert not ratchet.is_shrink("pyrefly", base, dumps({"errors": [new]}))


def test_complexity_snapshot_may_only_shrink():
    base = dumps([{"path": "a.py", "functions": [{"name": "f", "complexity": 20}]}])
    lower = dumps([{"path": "a.py", "functions": [{"name": "f", "complexity": 18}]}])
    higher = dumps([{"path": "a.py", "functions": [{"name": "f", "complexity": 21}]}])
    other = dumps([{"path": "a.py", "functions": [{"name": "g", "complexity": 16}]}])
    assert ratchet.is_shrink("complexipy", base, lower)
    assert ratchet.is_shrink("complexipy", base, dumps([]))
    assert not ratchet.is_shrink("complexipy", base, higher)
    assert not ratchet.is_shrink("complexipy", base, other)


def test_new_or_deleted_baseline_needs_maintainer():
    assert not ratchet.is_shrink("ruff", None, dumps({}))
    assert not ratchet.is_shrink("ruff", dumps({"a.py::F401::x": 1}), None)


def test_ruff_fingerprints_ignore_line_numbers():
    violation = {
        "filename": str(ratchet.ROOT / "flathunter" / "x.py"),
        "code": "F401",
        "message": "`os` imported but unused",
        "location": {"row": 3, "column": 1},
    }
    moved = {**violation, "location": {"row": 30, "column": 1}}
    assert ratchet.ruff_fingerprints([violation]) == ratchet.ruff_fingerprints([moved])


def test_guard_lets_ratchet_files_shrink_but_not_grow():
    diff = (
        "diff --git a/quality/ruff-baseline.json b/quality/ruff-baseline.json\n"
        "--- a/quality/ruff-baseline.json\n+++ b/quality/ruff-baseline.json\n"
        "@@ -1 +1 @@\n-a\n+b\n"
    )
    files = guard.parse_diff(diff)
    assert guard.find_violations(files, shrink_ok=lambda _path: True) == []
    blocked = guard.find_violations(files, shrink_ok=lambda _path: False)
    assert [f.label for f in blocked] == [guard.LABEL_SUPPRESSION]


def test_guard_flags_new_suppression_styles():
    diff = (
        "diff --git a/flathunter/x.py b/flathunter/x.py\n"
        "--- a/flathunter/x.py\n+++ b/flathunter/x.py\n@@ -0,0 +1,4 @@\n"
        "+# ruff: noqa\n+x = f()  # pyrefly: ignore[bad-assignment]\n+# fmt: off\n"
        "+y = 1  # pragma: no mutate\n"
    )
    messages = " ".join(f.message for f in guard.find_violations(guard.parse_diff(diff)))
    for name in ("ruff: noqa", "pyrefly: ignore", "fmt: off/skip", "pragma: no mutate"):
        assert name in messages
