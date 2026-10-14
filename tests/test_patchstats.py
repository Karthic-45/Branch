"""analyze_patch() -- pure diff arithmetic, fully offline.

Worth real coverage rather than a smoke test: this decides the tie-breaker
between two strategies that scored identically, so a miscount here silently
picks the wrong winner.
"""

from __future__ import annotations

from bmem.patchstats import analyze_patch

_TWO_FILE_DIFF = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
 import os
+import sys

-old_line
+new_line
--- a/src/util.py
+++ b/src/util.py
@@ -10,2 +10,3 @@
+added
"""

_NEW_FILE_DIFF = """\
--- /dev/null
+++ b/NOTES.md
@@ -0,0 +1,2 @@
+Applied by Branchable Memory.
+This proves the apply engine.
"""

_DELETED_FILE_DIFF = """\
--- a/obsolete.py
+++ /dev/null
@@ -1,2 +0,0 @@
-gone
-also gone
"""


class TestCounting:
    def test_counts_distinct_files(self) -> None:
        stats = analyze_patch(_TWO_FILE_DIFF)
        assert stats.files_changed == 2
        assert stats.files == ["src/app.py", "src/util.py"]

    def test_header_lines_are_not_counted_as_additions_or_removals(self) -> None:
        # The +++/--- headers start with the same characters as body lines.
        # Counting them would add one phantom addition and one phantom
        # removal per file -- and the tie-breaker reads these numbers.
        stats = analyze_patch(_TWO_FILE_DIFF)
        assert stats.lines_added == 3
        assert stats.lines_removed == 1

    def test_new_file_is_attributed_to_its_real_path(self) -> None:
        stats = analyze_patch(_NEW_FILE_DIFF)
        assert stats.files == ["NOTES.md"]
        assert stats.files_created == 1
        assert stats.lines_added == 2

    def test_deleted_file_is_attributed_to_its_source_path(self) -> None:
        stats = analyze_patch(_DELETED_FILE_DIFF)
        assert stats.files == ["obsolete.py"]
        assert stats.files_deleted == 1
        assert "/dev/null" not in stats.files

    def test_the_same_file_twice_counts_once(self) -> None:
        doubled = _NEW_FILE_DIFF + _NEW_FILE_DIFF
        assert analyze_patch(doubled).files_changed == 1


class TestDegradedInput:
    def test_empty_diff_is_zeroed_not_an_error(self) -> None:
        stats = analyze_patch("")
        assert stats.files_changed == 0
        assert stats.risk == "none"

    def test_garbage_is_zeroed_not_an_error(self) -> None:
        # FakeExplorer emits exactly this shape, so it is a real input.
        stats = analyze_patch("--- fake patch for: wrap it in a shim ---")
        assert stats.files_changed == 0
        assert stats.risk == "none"


class TestSurfaceDetection:
    def test_dependency_manifest_is_flagged(self) -> None:
        diff = "--- a/requirements.txt\n+++ b/requirements.txt\n@@\n+flask==3.0\n"
        stats = analyze_patch(diff)
        assert stats.touches_dependencies is True
        assert stats.risk == "high"

    def test_migration_directory_is_flagged_as_schema(self) -> None:
        diff = "--- /dev/null\n+++ b/migrations/003_add_col.sql\n@@\n+ALTER TABLE t ADD c INT;\n"
        stats = analyze_patch(diff)
        assert stats.touches_schema is True
        assert stats.risk == "high"

    def test_test_files_are_flagged_without_raising_risk(self) -> None:
        diff = "--- a/tests/test_app.py\n+++ b/tests/test_app.py\n@@\n+assert True\n"
        stats = analyze_patch(diff)
        assert stats.touches_tests is True
        assert stats.risk == "low"

    def test_a_plain_source_change_is_not_flagged(self) -> None:
        stats = analyze_patch(_TWO_FILE_DIFF)
        assert stats.touches_dependencies is False
        assert stats.touches_schema is False


class TestRiskBands:
    def _diff_over_files(self, count: int) -> str:
        return "".join(
            f"--- a/src/mod{i}.py\n+++ b/src/mod{i}.py\n@@\n+change\n" for i in range(count)
        )

    def test_small_change_is_low(self) -> None:
        assert analyze_patch(self._diff_over_files(2)).risk == "low"

    def test_medium_change_is_moderate(self) -> None:
        assert analyze_patch(self._diff_over_files(8)).risk == "moderate"

    def test_large_change_is_high(self) -> None:
        assert analyze_patch(self._diff_over_files(25)).risk == "high"

    def test_risk_basis_is_always_populated(self) -> None:
        # Rendered directly in the UI next to the band, so an empty string
        # would show a verdict with no stated reason.
        for n in (0, 2, 8, 25):
            assert analyze_patch(self._diff_over_files(n)).risk_basis
