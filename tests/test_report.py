"""Report generation's offline-testable surface.

The PDF builders need a cluster (they read real decisions), so what is pinned
here is ReportBundle's integrity contract and the PDF scaffolding that every
report shares -- the parts where a silent regression would produce a
plausible-looking file that cannot be tied back to anything.
"""

from __future__ import annotations

import hashlib

from reportlab.lib.units import mm
from reportlab.platypus import Paragraph

from bmem.report import ReportBundle, _build, _grid_table, _kv_table, _pct, _styles


class TestReportBundle:
    def test_sha256_matches_the_content(self) -> None:
        content = b"%PDF-1.4 pretend"
        bundle = ReportBundle(content, title="Decision Report", subject_id="abc12345-0000")
        assert bundle.sha256 == hashlib.sha256(content).hexdigest()

    def test_differing_content_gives_a_differing_digest(self) -> None:
        a = ReportBundle(b"one", title="T", subject_id="x")
        b = ReportBundle(b"two", title="T", subject_id="x")
        assert a.sha256 != b.sha256

    def test_filename_is_slugged_and_carries_the_subject(self) -> None:
        bundle = ReportBundle(b"x", title="Engineering Report", subject_id="deadbeef-1111-2222")
        assert bundle.filename == "engineering-report-deadbeef.pdf"

    def test_generated_at_is_iso_utc(self) -> None:
        bundle = ReportBundle(b"x", title="T", subject_id="x")
        assert bundle.generated_at.endswith("+00:00")


class TestFormatting:
    def test_missing_score_says_not_recorded_rather_than_zero(self) -> None:
        # A None score means the branch was never evaluated. Rendering it as
        # "0.0%" would silently turn an absence of evidence into evidence of
        # failure -- the exact substitution these reports must not make.
        assert _pct(None) == "not recorded"

    def test_real_score_is_a_percentage(self) -> None:
        # Deliberately not a value ending in an exact half: Python's format
        # spec uses banker's rounding, so 0.8725 renders "87.2%", not "87.3%".
        # Pinning a tie-breaking case here would test CPython, not this code.
        assert _pct(0.873) == "87.3%"

    def test_zero_is_rendered_as_zero_not_as_missing(self) -> None:
        assert _pct(0.0) == "0.0%"


class TestPdfScaffolding:
    def test_a_minimal_report_produces_a_real_pdf(self) -> None:
        st = _styles()
        bundle = _build(
            [Paragraph("Hello", st["title"])], title="Decision Report", subject_id="abc12345"
        )
        assert bundle.content.startswith(b"%PDF")
        assert len(bundle.content) > 500

    def test_kv_table_renders_missing_values_as_a_dash(self) -> None:
        table = _kv_table([("Key", ""), ("Other", "value")])
        # Second column of the first row is the empty value, rendered as an
        # em dash rather than blank so a reader can tell the field exists.
        assert "—" in table._cellvalues[0][1].text

    def test_grid_table_keeps_the_header_row(self) -> None:
        table = _grid_table(["A", "B"], [["1", "2"]], [20 * mm, 20 * mm])
        assert table._nrows == 2
        assert table.repeatRows == 1

    def test_report_is_deterministic_enough_to_be_non_empty(self) -> None:
        st = _styles()
        story = [Paragraph("Body text", st["body"]), _kv_table([("a", "b")])]
        first = _build(list(story), title="T", subject_id="s")
        assert first.content.count(b"%%EOF") >= 1


def test_styles_cover_every_role_the_builders_use() -> None:
    # The builders index this dict by name; a missing key is a KeyError at
    # generation time rather than a layout glitch.
    st = _styles()
    for role in ("title", "subtitle", "h2", "h3", "body", "mono", "muted"):
        assert role in st
