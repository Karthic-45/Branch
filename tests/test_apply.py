"""bmem.apply's offline-testable pieces: the validation-profile contract and
the command runner. The full apply_winning_patch() flow needs a database, a
real git checkout, and S3, so it's exercised live (see demo/script.md) rather
than mocked here -- a mocked git apply would test the mock, not the patch
logic that actually matters.
"""

from __future__ import annotations

import pathlib

from bmem.apply import (
    ApplyResult,
    ValidationProfile,
    ValidationStep,
    _classify_apply_failure,
    _run_validation,
)


class TestValidationProfile:
    def test_empty_profile_has_every_field_unset(self) -> None:
        profile = ValidationProfile()
        assert profile.install is None
        assert profile.test is None
        assert profile.lint is None
        assert profile.typecheck is None
        assert profile.build is None

    def test_round_trips_through_json(self) -> None:
        profile = ValidationProfile(test="pytest -q", lint="ruff check .")
        restored = ValidationProfile.model_validate_json(profile.model_dump_json())
        assert restored == profile

    def test_validates_from_a_partial_dict_without_raising(self) -> None:
        # This is exactly what the apply engine does with a row straight out
        # of branches.validation_profile: whatever subset of commands was
        # configured, no assumption that all five are present.
        profile = ValidationProfile.model_validate({"test": "pytest"})
        assert profile.test == "pytest"
        assert profile.lint is None


class TestRunValidation:
    def test_unconfigured_profile_runs_nothing(self, tmp_path: pathlib.Path) -> None:
        steps = _run_validation(tmp_path, ValidationProfile(), timeout=5.0)
        assert steps == []

    def test_passing_command_is_recorded_as_passed(self, tmp_path: pathlib.Path) -> None:
        steps = _run_validation(tmp_path, ValidationProfile(test="true"), timeout=5.0)
        assert len(steps) == 1
        assert steps[0].name == "test"
        assert steps[0].status == "passed"

    def test_failing_command_is_recorded_as_failed_not_raised(self, tmp_path: pathlib.Path) -> None:
        steps = _run_validation(tmp_path, ValidationProfile(lint="false"), timeout=5.0)
        assert steps[0].status == "failed"

    def test_missing_executable_is_recorded_as_error_not_raised(
        self, tmp_path: pathlib.Path
    ) -> None:
        steps = _run_validation(
            tmp_path, ValidationProfile(build="this-command-does-not-exist-anywhere"), timeout=5.0
        )
        assert steps[0].status == "error"

    def test_runs_steps_in_a_fixed_cheap_to_expensive_order(self, tmp_path: pathlib.Path) -> None:
        marker = tmp_path / "order.txt"
        profile = ValidationProfile(
            test=f"sh -c 'echo test >> {marker}'",
            install=f"sh -c 'echo install >> {marker}'",
            build=f"sh -c 'echo build >> {marker}'",
        )
        _run_validation(tmp_path, profile, timeout=5.0)
        assert marker.read_text().split() == ["install", "build", "test"]

    def test_a_timeout_does_not_crash_the_whole_run(self, tmp_path: pathlib.Path) -> None:
        steps = _run_validation(
            tmp_path, ValidationProfile(test="sleep 5"), timeout=0.1
        )
        assert steps[0].status == "error"
        assert "timed out" in steps[0].output

    def test_only_configured_steps_run(self, tmp_path: pathlib.Path) -> None:
        steps = _run_validation(tmp_path, ValidationProfile(test="true", lint="true"), timeout=5.0)
        assert [s.name for s in steps] == ["lint", "test"]


class TestApplyResultDefaults:
    def test_a_freshly_constructed_result_is_unvalidated_and_unapplied(self) -> None:
        result = ApplyResult(merge_id="abc123")
        assert result.patch_applied is False
        assert result.validation_status == "skipped"
        assert result.conflicts == []
        assert result.validation_steps == []

    def test_validation_step_round_trips_through_dict(self) -> None:
        step = ValidationStep(name="test", command="pytest", status="passed", output="")
        assert ValidationStep.model_validate(step.model_dump()) == step


class TestClassifyApplyFailure:
    """`git apply` reports a malformed artifact and a genuine conflict through
    the same non-zero exit code. Telling them apart matters: a conflict sends
    a reader to look at the repository, a malformed artifact sends them to
    look at what produced the patch.
    """

    def test_not_a_patch_is_malformed_and_names_no_file(self) -> None:
        # Exactly what git says about the stand-in explorer's placeholder,
        # which the UI previously reported as "1 conflicting file".
        reason, files = _classify_apply_failure(
            'error: No valid patches in input (allow with "--allow-empty")'
        )
        assert reason == "malformed"
        assert files == []

    def test_real_conflict_names_the_file_once(self) -> None:
        reason, files = _classify_apply_failure(
            "error: patch failed: src/app.py:12\nerror: src/app.py: patch does not apply"
        )
        assert reason == "conflict"
        assert files == ["src/app.py"]

    def test_missing_file_is_a_conflict_against_the_tree(self) -> None:
        reason, files = _classify_apply_failure("error: src/gone.py: does not exist in index")
        assert reason == "conflict"
        assert files == ["src/gone.py"]

    def test_unrecognised_stderr_is_not_forced_into_a_bucket(self) -> None:
        reason, files = _classify_apply_failure("fatal: something else entirely")
        assert reason == "unknown"
        assert files == []
