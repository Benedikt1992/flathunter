"""Tests for the agent guard (run with `python -m pytest .github/scripts`)."""

import agent_guard as guard

DIFF = """\
diff --git a/flathunter/crawler/subito.py b/flathunter/crawler/subito.py
--- a/flathunter/crawler/subito.py
+++ b/flathunter/crawler/subito.py
@@ -10,0 +11,2 @@
+    value = parse(x)  # type: ignore
+    other = 1  # pylint: disable=invalid-name
diff --git a/test/test_hunter.py b/test/test_hunter.py
--- a/test/test_hunter.py
+++ b/test/test_hunter.py
@@ -5,3 +5,2 @@
-def test_hunt_flats():
-def test_kept():
+def test_kept():
+@pytest.mark.skip(reason="flaky")
diff --git a/test/test_old.py b/test/test_old.py
deleted file mode 100644
--- a/test/test_old.py
+++ /dev/null
@@ -1 +0,0 @@
-def test_old():
diff --git a/.github/workflows/tests.yml b/.github/workflows/tests.yml
--- a/.github/workflows/tests.yml
+++ b/.github/workflows/tests.yml
@@ -1 +1 @@
-a
+b
diff --git a/flathunter/hunter.py b/flathunter/hunter.py
--- a/flathunter/hunter.py
+++ b/flathunter/hunter.py
@@ -1 +1 @@
-x = 1
+x = 2
"""


def labels(findings):
    return sorted({f.label for f in findings})


def test_detects_every_violation_kind():
    files = guard.parse_diff(DIFF)
    findings = guard.find_violations(files)
    messages = "\n".join(f.message for f in findings)
    assert labels(findings) == sorted([guard.LABEL_SUPPRESSION, guard.LABEL_TEST_REMOVAL])
    assert "type: ignore" in messages
    assert "pylint disable" in messages
    assert "skipped/xfail test" in messages
    assert "`test_hunt_flats` is removed" in messages
    assert "test_kept" not in messages
    assert "test file `test/test_old.py` is deleted" in messages
    assert ".github/workflows/tests.yml" not in messages
    assert "flathunter/hunter.py" not in messages
    assert guard.protected_changes(files) == [".github/workflows/tests.yml"]


def test_clean_change_has_no_findings():
    diff = DIFF.split("diff --git a/flathunter/hunter.py")[1]
    findings = guard.find_violations(guard.parse_diff("diff --git a/flathunter/hunter.py" + diff))
    assert not findings


def test_protected_patterns():
    assert guard.is_protected(".github/workflows/agent-review.yml")
    assert guard.is_protected("docs/adr/0001-x.md")
    assert guard.is_protected("Pipfile.lock")
    assert guard.is_protected("requirements-test.txt")
    assert not guard.is_protected("flathunter/config.py")
    assert not guard.is_protected("docs/agent-workflow.md")


def test_only_maintainer_labels_count():
    events = [
        {
            "event": "labeled",
            "label": {"name": "allow-suppression"},
            "actor": {"login": "claude[bot]"},
        },
        {
            "event": "labeled",
            "label": {"name": "allow-protected-change"},
            "actor": {"login": "Owner"},
        },
        {"event": "labeled", "label": {"name": "allow-test-removal"}, "actor": {"login": "owner"}},
        {
            "event": "labeled",
            "label": {"name": "allow-test-removal"},
            "actor": {"login": "some-bot"},
        },
    ]
    current = {"allow-suppression", "allow-protected-change", "allow-test-removal"}
    assert guard.labels_granted_by(events, current, "owner") == {"allow-protected-change"}


def test_removed_label_does_not_count():
    events = [
        {"event": "labeled", "label": {"name": "allow-suppression"}, "actor": {"login": "owner"}}
    ]
    assert guard.labels_granted_by(events, set(), "owner") == set()


def test_audit_diff_only_fails_on_new_vulns(tmp_path):
    import json

    import audit_diff

    def report(*pairs):
        return {
            "dependencies": [
                {"name": name, "version": "1.0", "vulns": [{"id": vid, "fix_versions": ["2.0"]}]}
                for name, vid in pairs
            ]
        }

    base, head = tmp_path / "base.json", tmp_path / "head.json"
    base.write_text(json.dumps(report(("werkzeug", "PYSEC-1"))))
    head.write_text(json.dumps(report(("werkzeug", "PYSEC-1"))))
    assert audit_diff.main(str(base), str(head)) == 0
    head.write_text(json.dumps(report(("werkzeug", "PYSEC-1"), ("Requests", "CVE-2"))))
    assert audit_diff.main(str(base), str(head)) == 1
    head.write_text(json.dumps(report()))
    assert audit_diff.main(str(base), str(head)) == 0


PROTECTED_DIFF = """\
diff --git a/Pipfile.lock b/Pipfile.lock
--- a/Pipfile.lock
+++ b/Pipfile.lock
@@ -1 +1 @@
-a
+b
diff --git a/pyproject.toml b/pyproject.toml
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -1 +1 @@
-a
+b
diff --git a/.importlinter b/docs/importlinter.ini
similarity index 90%
rename from .importlinter
rename to docs/importlinter.ini
diff --git a/quality/ruff-baseline.json b/quality/ruff-baseline.json
--- a/quality/ruff-baseline.json
+++ b/quality/ruff-baseline.json
@@ -1 +1 @@
-a
+b
diff --git a/complexipy-snapshot.json b/complexipy-snapshot.json
--- a/complexipy-snapshot.json
+++ b/complexipy-snapshot.json
@@ -1 +1 @@
-a
+b
diff --git a/flathunter/hunter.py b/flathunter/hunter.py
--- a/flathunter/hunter.py
+++ b/flathunter/hunter.py
@@ -1 +1 @@
-x = 1
+x = 2
"""


def test_protected_files_are_reported_for_review_not_blocked():
    files = guard.parse_diff(PROTECTED_DIFF)
    findings = guard.find_violations(files, shrink_ok=lambda _path: True)
    assert findings == []
    assert guard.protected_changes(files) == [".importlinter", "Pipfile.lock", "pyproject.toml"]


def test_growing_baselines_block_as_suppression():
    files = guard.parse_diff(PROTECTED_DIFF)
    findings = guard.find_violations(
        files, shrink_ok=lambda path: path == "complexipy-snapshot.json"
    )
    assert findings == [
        guard.Finding(
            guard.LABEL_SUPPRESSION,
            "`quality/ruff-baseline.json` grows a ratchet baseline (accepts new violations)",
        )
    ]


def test_override_labels_allow_only_their_own_kind():
    findings = guard.find_violations(guard.parse_diff(DIFF))
    suppressions = [f for f in findings if f.label == guard.LABEL_SUPPRESSION]
    removals = [f for f in findings if f.label == guard.LABEL_TEST_REMOVAL]
    assert guard.split_by_grant(findings, set()) == (findings, [])
    assert guard.split_by_grant(findings, {"allow-protected-change"}) == (findings, [])
    assert guard.split_by_grant(findings, {guard.LABEL_SUPPRESSION}) == (removals, suppressions)
    assert guard.split_by_grant(findings, {guard.LABEL_SUPPRESSION, guard.LABEL_TEST_REMOVAL}) == (
        [],
        findings,
    )


def test_report_lists_blocking_allowed_and_protected():
    blocking = [guard.Finding(guard.LABEL_TEST_REMOVAL, "test `test_a` is removed from `t.py`")]
    allowed = [guard.Finding(guard.LABEL_SUPPRESSION, "`x.py` adds a suppression (noqa)")]
    report = guard.render_report(
        blocking, allowed, ["Pipfile.lock", "pyproject.toml"], "owner", by_maintainer=False
    )
    assert report.startswith("## Agent guard\n")
    assert (
        "- ❌ test `test_a` is removed from `t.py` — needs label `allow-test-removal` from @owner"
        in report
    )
    assert "- ✅ `x.py` adds a suppression (noqa) — allowed by `allow-suppression`" in report
    assert "- `Pipfile.lock`\n- `pyproject.toml`" in report
    assert "reviewer" in report
    assert "No suppressions" not in report
    assert "authored by the maintainer" not in report
    assert "authored by the maintainer" in guard.render_report(
        blocking, [], [], "owner", by_maintainer=True
    )


def test_report_without_findings():
    clean = guard.render_report([], [], [], "owner", by_maintainer=False)
    assert "No suppressions, removed tests or grown baselines. ✅" in clean
    assert "Protected files" not in clean
    only_protected = guard.render_report([], [], ["Pipfile.lock"], "owner", by_maintainer=False)
    assert "No suppressions, removed tests or grown baselines. ✅" in only_protected
    assert "- `Pipfile.lock`" in only_protected


def test_exit_code_blocks_only_unallowed_findings_of_others():
    finding = guard.Finding(guard.LABEL_SUPPRESSION, "x")
    assert guard.exit_code([finding], by_maintainer=False) == 1
    assert guard.exit_code([finding], by_maintainer=True) == 0
    assert guard.exit_code([], by_maintainer=False) == 0
