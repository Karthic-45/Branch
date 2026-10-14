"""Project reports render from collected data without a cluster.

The data collection needs CockroachDB; the layout does not. These tests feed
the builders an explicitly empty project and check the document says so
plainly rather than failing or inventing figures.
"""

from __future__ import annotations

from types import SimpleNamespace

from bmem import report as R


def _empty_project() -> R._Project:
    return R._Project(
        root_id="dec2f5a7-7785-467a-b063-eb3636c1c1ad",
        repo=None,
        ref=None,
        created_at=None,
        branches=[],
        root_chunks=0,
        files=[],
        decisions=[],
        deployments=[],
        stats=None,
        costs=SimpleNamespace(lines=[], total_estimated_usd=None),
        aws_errors={"storage": "not queried", "builds": "not queried"},
    )


def test_executive_report_renders_for_an_empty_project() -> None:
    bundle = R.executive_pdf("dec2f5a7-7785-467a-b063-eb3636c1c1ad", data=_empty_project())
    assert bundle.content.startswith(b"%PDF")
    assert b"Page" in bundle.content or len(bundle.content) > 4000


def test_engineering_report_renders_for_an_empty_project() -> None:
    bundle = R.engineering_pdf("dec2f5a7-7785-467a-b063-eb3636c1c1ad", data=_empty_project())
    assert bundle.content.startswith(b"%PDF")


def test_summary_states_absence_instead_of_figures() -> None:
    points = [p.text for p in R._summary_points(_empty_project())]
    assert any("not recorded" in t for t in points)
    assert any("No migration run" in t for t in points)
    assert any("No decision" in t for t in points)


def test_reason_labels_are_readable() -> None:
    assert R._reason("ok") == "Completed"
    assert R._reason("patch_did_not_apply") == "Patch did not apply"
    assert R._reason("explore_failed: truncated JSON") == "Exploration failed: truncated JSON"
    assert R._reason(None) == "—"


def test_hypothesis_markup_is_escaped() -> None:
    # Model-written text can contain "<" or "&"; unescaped it breaks the
    # paragraph parser and the whole report fails to render.
    cell = R._two_line("a < b & c", "<script>")
    assert "&lt;" in cell.text or "<" not in cell.text.replace("<b>", "").replace("</b>", "")


def test_collect_rejects_a_non_uuid_project_id() -> None:
    try:
        R._collect("not-a-uuid")
    except LookupError:
        return
    raise AssertionError("expected LookupError")
