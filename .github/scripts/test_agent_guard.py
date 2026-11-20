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
    findings = guard.find_violations(guard.parse_diff(DIFF))
    messages = "\n".join(f.message for f in findings)
    assert labels(findings) == sorted(
        [guard.LABEL_PROTECTED, guard.LABEL_SUPPRESSION, guard.LABEL_TEST_REMOVAL]
    )
    assert "type: ignore" in messages
    assert "pylint disable" in messages
    assert "skipped/xfail test" in messages
    assert "`test_hunt_flats` is removed" in messages
    assert "test_kept" not in messages
    assert "test file `test/test_old.py` is deleted" in messages
    assert "`.github/workflows/tests.yml` is a protected file" in messages
    assert "flathunter/hunter.py" not in messages


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
