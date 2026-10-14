"""Professional reports: one decision, one project, or the whole archive.

Three levels, all built from the same recorded data so they cannot disagree:
a decision report (why this strategy won), an executive report (what the
project did and where it stands), and an engineering report (the same plus
run configuration, patches, CodeBuild and S3 detail). The ZIP archive bundles
all of them with the raw JSON.

Every figure in these documents is read from CockroachDB, S3 or CodeBuild at
generation time. Nothing is sample data and nothing is estimated: a value
that was never measured is printed as "—" or "not recorded", and a section
with no data says so in one line instead of being padded with prose.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from xml.sax.saxutils import escape

from pydantic import BaseModel
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

from bmem.decision import decision_graph, get_counterfactual, get_decision_evidence, memory_diff
from bmem.manifest import get_manifest
from bmem.observability import cost_report, project_stats

__all__ = [
    "ArchiveEntry",
    "ReportBundle",
    "archive_listing",
    "decision_pdf",
    "engineering_pdf",
    "executive_pdf",
    "project_archive",
]

# ── Palette ──────────────────────────────────────────────────────────────────
# Restrained and legible in greyscale: one accent, one dark header tone, and
# three status colours used only for short status words.
_INK = colors.HexColor("#111827")
_INK2 = colors.HexColor("#374151")
_MUTED = colors.HexColor("#6b7280")
_ACCENT = colors.HexColor("#1d4ed8")
_HEAD = colors.HexColor("#1e293b")
_RULE = colors.HexColor("#e5e7eb")
_RULE_STRONG = colors.HexColor("#cbd5e1")
_BAND = colors.HexColor("#f3f4f6")
_ZEBRA = colors.HexColor("#f8fafc")
_GOOD = "#047857"
_BAD = "#b91c1c"
_WARN = "#b45309"

_PAGE_W, _PAGE_H = A4
_MARGIN_X = 18 * mm
_CONTENT_W = _PAGE_W - 2 * _MARGIN_X


class ReportBundle:
    """A generated report plus the integrity metadata that travels with it."""

    def __init__(self, content: bytes, *, title: str, subject_id: str) -> None:
        self.content = content
        self.title = title
        self.subject_id = subject_id
        self.sha256 = hashlib.sha256(content).hexdigest()
        self.generated_at = datetime.now(UTC).isoformat()

    @property
    def filename(self) -> str:
        slug = self.title.lower().replace(" ", "-")
        return f"{slug}-{self.subject_id[:8]}.pdf"


# ── Typography ───────────────────────────────────────────────────────────────

_STYLE_CACHE: dict[str, ParagraphStyle] = {}


def _styles() -> dict[str, ParagraphStyle]:
    if _STYLE_CACHE:
        return _STYLE_CACHE
    base = getSampleStyleSheet()
    normal = base["Normal"]

    def s(name: str, **kw: Any) -> ParagraphStyle:
        kw.setdefault("fontName", "Helvetica")
        return ParagraphStyle(f"bm_{name}", parent=normal, **kw)

    _STYLE_CACHE.update(
        {
            "wordmark": s(
                "wordmark", fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=_ACCENT
            ),
            "masthead": s("masthead", fontSize=7.5, leading=10, textColor=_MUTED),
            "kind": s(
                "kind",
                fontName="Helvetica-Bold",
                fontSize=9,
                leading=12,
                textColor=_MUTED,
                spaceAfter=2 * mm,
            ),
            "title": s(
                "title",
                fontName="Helvetica-Bold",
                fontSize=24,
                leading=29,
                textColor=_INK,
                spaceAfter=2 * mm,
                alignment=TA_LEFT,
            ),
            "subtitle": s(
                "subtitle", fontSize=10.5, leading=15, textColor=_INK2, spaceAfter=6 * mm
            ),
            "h2": s(
                "h2",
                fontName="Helvetica-Bold",
                fontSize=13,
                leading=17,
                textColor=_INK,
                spaceBefore=6 * mm,
                spaceAfter=1 * mm,
                keepWithNext=1,
            ),
            "h3": s(
                "h3",
                fontName="Helvetica-Bold",
                fontSize=10,
                leading=13.5,
                textColor=_INK,
                spaceBefore=4 * mm,
                spaceAfter=1.5 * mm,
                keepWithNext=1,
            ),
            "body": s("body", fontSize=9.5, leading=14, textColor=_INK, spaceAfter=2 * mm),
            "bullet": s(
                "bullet",
                fontSize=9.5,
                leading=14,
                textColor=_INK,
                leftIndent=10,
                bulletIndent=0,
                spaceAfter=1.6 * mm,
            ),
            "muted": s("muted", fontSize=8.5, leading=12, textColor=_MUTED),
            "mono": ParagraphStyle(
                "bm_mono",
                parent=base["Code"],
                fontName="Courier",
                fontSize=8,
                leading=11,
                textColor=_INK,
            ),
            "cell": s("cell", fontSize=8.5, leading=11.5, textColor=_INK),
            "cell_r": s("cell_r", fontSize=8.5, leading=11.5, textColor=_INK, alignment=TA_RIGHT),
            "cell_mono": s(
                "cell_mono", fontName="Courier", fontSize=8, leading=11.5, textColor=_INK
            ),
            "cell_sub": s("cell_sub", fontSize=7.5, leading=10, textColor=_MUTED),
            "th": s(
                "th", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=colors.white
            ),
            "th_r": s(
                "th_r",
                fontName="Helvetica-Bold",
                fontSize=7.5,
                leading=10,
                textColor=colors.white,
                alignment=TA_RIGHT,
            ),
            "key": s("key", fontName="Helvetica-Bold", fontSize=8, leading=11.5, textColor=_MUTED),
            "kpi_value": s(
                "kpi_value", fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=_INK
            ),
            "kpi_label": s(
                "kpi_label", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=_MUTED
            ),
            "kpi_sub": s("kpi_sub", fontSize=7.5, leading=10, textColor=_MUTED),
            "toc": s("toc", fontSize=10, leading=17, textColor=_INK2),
            "note": s("note", fontSize=9, leading=13, textColor=_INK2),
        }
    )
    return _STYLE_CACHE


def _esc(value: Any) -> str:
    return escape("" if value is None else str(value))


def _cell(value: Any, style: str = "cell") -> Paragraph | Flowable:
    if isinstance(value, Flowable):
        return value
    text = _esc(value) if value not in (None, "") else "—"
    return Paragraph(text, _styles()[style])


def _status(word: str, tone: str | None) -> Paragraph:
    """A short status word in its tone colour -- the only colour in a table."""
    colour = {"good": _GOOD, "bad": _BAD, "warn": _WARN}.get(tone or "", "#374151")
    return Paragraph(f'<font color="{colour}"><b>{_esc(word)}</b></font>', _styles()["cell"])


def _two_line(main: Any, sub: Any) -> Paragraph:
    """Primary text with a muted second line, in one cell."""
    st = _styles()
    text = f"<b>{_esc(main) or '—'}</b>"
    if sub:
        text += f'<br/><font size="7.5" color="#6b7280">{_esc(sub)}</font>'
    return Paragraph(text, st["cell"])


# ── Building blocks ──────────────────────────────────────────────────────────


def _kv_table(rows: list[tuple[Any, ...]], *, width: float = _CONTENT_W) -> Table:
    """Label / value pairs. A third tuple element ``True`` sets the value in mono."""
    st = _styles()
    data = []
    for row in rows:
        k, v = row[0], row[1]
        mono = len(row) > 2 and bool(row[2])
        value = (
            v
            if isinstance(v, Flowable)
            else Paragraph(
                _esc(v) if v not in (None, "") else "—", st["cell_mono" if mono else "cell"]
            )
        )
        data.append([Paragraph(_esc(k), st["key"]), value])
    table = Table(data, colWidths=[46 * mm, width - 46 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, _RULE),
                ("LINEABOVE", (0, 0), (-1, 0), 0.8, _RULE_STRONG),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _grid_table(
    header: list[str],
    rows: list[list[Any]],
    widths: list[float],
    *,
    right: tuple[int, ...] = (),
    mono: tuple[int, ...] = (),
) -> Table:
    """A data table with a dark header row, zebra body and right-aligned numbers."""
    st = _styles()
    data: list[list[Any]] = [
        [Paragraph(f"{_esc(h)}", st["th_r" if i in right else "th"]) for i, h in enumerate(header)]
    ]
    for row in rows:
        data.append(
            [
                _cell(c, "cell_r" if i in right else "cell_mono" if i in mono else "cell")
                for i, c in enumerate(row)
            ]
        )
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style: list[tuple[Any, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), _HEAD),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, _RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    for r in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, r), (-1, r), _ZEBRA))
    table.setStyle(TableStyle(style))
    return table


def _note(text: str) -> Table:
    """A one-line statement for a section that has no data -- clear, not padded."""
    t = Table([[Paragraph(text, _styles()["note"])]], colWidths=[_CONTENT_W], hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _BAND),
                ("LINEBEFORE", (0, 0), (0, -1), 2.2, _RULE_STRONG),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return t


def _kpis(items: list[tuple[str, str, str]]) -> Table:
    """A row of headline figures: (label, value, sub-line)."""
    st = _styles()
    gap = 4 * mm
    n = len(items)
    w = (_CONTENT_W - gap * (n - 1)) / n
    cells: list[Any] = []
    widths: list[float] = []
    for i, (label, value, sub) in enumerate(items):
        cells.append(
            [
                Paragraph(_esc(label).upper(), st["kpi_label"]),
                Spacer(1, 1.2 * mm),
                Paragraph(_esc(value), st["kpi_value"]),
                Paragraph(_esc(sub), st["kpi_sub"]),
            ]
        )
        widths.append(w)
        if i < n - 1:
            cells.append("")
            widths.append(gap)
    t = Table([cells], colWidths=widths, hAlign="LEFT")
    style: list[tuple[Any, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]
    for i in range(0, len(cells), 2):
        style += [
            ("BACKGROUND", (i, 0), (i, 0), _BAND),
            ("LINEABOVE", (i, 0), (i, 0), 2, _ACCENT),
        ]
    t.setStyle(TableStyle(style))
    return t


def _side_by_side(left: Any, right: Any) -> Table:
    gap = 6 * mm
    t = Table(
        [[left, "", right]],
        colWidths=[(_CONTENT_W - gap) / 2, gap, (_CONTENT_W - gap) / 2],
        hAlign="LEFT",
    )
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return t


def _accent_rule(width: float = 22 * mm) -> HRFlowable:
    return HRFlowable(
        width=width,
        thickness=2,
        color=_ACCENT,
        hAlign="LEFT",
        spaceBefore=1 * mm,
        spaceAfter=5 * mm,
    )


# ── Page furniture ───────────────────────────────────────────────────────────


def _canvas_maker(doc_title: str, subject: str, generated: str) -> type[rl_canvas.Canvas]:
    """A canvas that knows the page count, so every page reads "Page x of y".

    Continuation pages carry a running header naming the document, and every
    page a footer naming the project, so a printed page that is separated from
    the rest still says what it is.
    """

    class _NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._saved: list[dict[str, Any]] = []

        def showPage(self) -> None:  # noqa: N802 -- reportlab API
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self._furniture(total)
                super().showPage()
            super().save()

        def _furniture(self, total: int) -> None:
            page = self._pageNumber
            self.saveState()
            left, right = _MARGIN_X, _PAGE_W - _MARGIN_X
            if page > 1:
                self.setFont("Helvetica-Bold", 7.5)
                self.setFillColor(_ACCENT)
                self.drawString(left, _PAGE_H - 12 * mm, "BRANCHABLE MEMORY")
                self.setFont("Helvetica", 7.5)
                self.setFillColor(_MUTED)
                self.drawString(
                    left + self.stringWidth("BRANCHABLE MEMORY", "Helvetica-Bold", 7.5) + 3 * mm,
                    _PAGE_H - 12 * mm,
                    f"·  {doc_title}",
                )
                self.drawRightString(right, _PAGE_H - 12 * mm, subject)
                self.setStrokeColor(_RULE)
                self.setLineWidth(0.5)
                self.line(left, _PAGE_H - 14.5 * mm, right, _PAGE_H - 14.5 * mm)
            self.setStrokeColor(_RULE)
            self.setLineWidth(0.5)
            self.line(left, 14 * mm, right, 14 * mm)
            self.setFont("Helvetica", 7)
            self.setFillColor(_MUTED)
            self.drawString(left, 9.5 * mm, f"{doc_title}  ·  {subject}  ·  generated {generated}")
            self.setFont("Helvetica-Bold", 7)
            self.drawRightString(right, 9.5 * mm, f"Page {page} of {total}")
            self.restoreState()

    return _NumberedCanvas


def _build(story: list[Any], *, title: str, subject_id: str) -> ReportBundle:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_MARGIN_X,
        rightMargin=_MARGIN_X,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=title,
        author="Branchable Memory",
        subject=subject_id,
    )
    generated = datetime.now(UTC).strftime("%d %b %Y %H:%M UTC")
    subject = (
        f"Project {subject_id[:8]}" if "Decision" not in title else f"Decision {subject_id[:8]}"
    )
    doc.build(story, canvasmaker=_canvas_maker(title, subject, generated))
    return ReportBundle(buffer.getvalue(), title=title, subject_id=subject_id)


class _Sections:
    """Numbered sections, collected first so the cover can list them."""

    def __init__(self) -> None:
        self.items: list[tuple[str, list[Any]]] = []

    def add(self, title: str, body: list[Any]) -> None:
        self.items.append((title, body))

    def render(self) -> list[Any]:
        st = _styles()
        out: list[Any] = []
        for n, (title, body) in enumerate(self.items, 1):
            out += [
                CondPageBreak(45 * mm),
                Paragraph(
                    f'<font color="#1d4ed8">{n}</font>&nbsp;&nbsp;&nbsp;{_esc(title)}', st["h2"]
                ),
                HRFlowable(
                    width="100%",
                    thickness=0.6,
                    color=_RULE_STRONG,
                    spaceBefore=1,
                    spaceAfter=3.5 * mm,
                ),
                *body,
            ]
        return out

    def contents(self) -> list[Any]:
        st = _styles()
        rows = [
            [
                Paragraph(f'<font color="#1d4ed8"><b>{n}</b></font>', st["toc"]),
                Paragraph(_esc(t), st["toc"]),
            ]
            for n, (t, _) in enumerate(self.items, 1)
        ]
        table = Table(rows, colWidths=[9 * mm, _CONTENT_W - 9 * mm], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, _RULE),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        return [Paragraph("CONTENTS", st["kind"]), table]


# ── Formatting ───────────────────────────────────────────────────────────────


def _pct(v: float | None) -> str:
    return f"{v:.1%}" if v is not None else "not recorded"


def _score(v: float | None) -> str:
    return f"{v:.1%}" if v is not None else "—"


def _tests(passed: int | None, total: int | None) -> str:
    if total is None or passed is None:
        return "—"
    return f"{passed:,} / {total:,}"


def _dt(value: Any, *, date_only: bool = False) -> str:
    if value in (None, ""):
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return value.strftime("%d %b %Y") if date_only else value.strftime("%d %b %Y, %H:%M UTC")
    return str(value)


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _bytes(n: int | None) -> str:
    if n is None:
        return "—"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _repo_name(url: str | None) -> str | None:
    if not url:
        return None
    return url.replace("https://github.com/", "").removesuffix(".git").strip("/")


_REASONS = {
    "ok": "Completed",
    "patch_did_not_apply": "Patch did not apply",
    "no_report_produced": "No test report produced",
    "explore_failed": "Exploration failed",
    "build_failed": "Build failed",
    "timeout": "Timed out",
    "agent_declined": "Agent declined to write a patch",
}


def _reason(reason: str | None) -> str:
    if not reason:
        return "—"
    head, _, tail = reason.partition(":")
    label = _REASONS.get(head, head.replace("_", " ").capitalize())
    tail = " ".join(tail.split())
    if len(tail) > 140:
        tail = tail[:139].rstrip() + "…"
    return f"{label}: {tail}" if tail else label


def _clip(text: str | None, n: int) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# ── Data ─────────────────────────────────────────────────────────────────────


@dataclass
class _Branch:
    id: str
    parent_id: str | None
    depth: int
    hypothesis: str | None
    status: str
    score: float | None
    created_at: str | None
    strategy: str | None = None
    run_id: str | None = None
    outcome: str | None = None
    passed: int | None = None
    total: int | None = None
    reason: str | None = None
    patch: dict[str, Any] | None = None
    reasoning_model: str | None = None
    embedding_model: str | None = None
    base_sha: str | None = None
    target_ref: str | None = None
    codebuild_project: str | None = None
    retrieved: int | None = None
    beliefs_written: int = 0

    @property
    def label(self) -> str:
        return self.strategy or _clip(self.hypothesis, 60) or self.id[:8]

    @property
    def evaluated(self) -> bool:
        return self.outcome is not None

    @property
    def full_pass(self) -> bool:
        return bool(self.total) and self.passed == self.total


@dataclass
class _Project:
    root_id: str
    repo: str | None
    ref: str | None
    created_at: str | None
    branches: list[_Branch]
    root_chunks: int
    files: list[tuple[str, int]]
    decisions: list[Any]
    deployments: list[Any]
    stats: Any
    costs: Any
    storage: dict[str, Any] | None = None
    builds: dict[str, Any] | None = None
    aws_errors: dict[str, str] = field(default_factory=dict)
    verdicts: dict[str, Any] = field(default_factory=dict)

    @property
    def evaluated(self) -> list[_Branch]:
        return [b for b in self.branches if b.evaluated]

    @property
    def runs(self) -> list[str]:
        seen: list[str] = []
        for b in self.branches:
            if b.run_id and b.run_id not in seen:
                seen.append(b.run_id)
        return seen

    @property
    def best(self) -> _Branch | None:
        scored = [b for b in self.evaluated if b.total]
        if not scored:
            return None
        return max(scored, key=lambda b: ((b.passed or 0) / (b.total or 1), b.passed or 0))


def _collect(root_id: str, *, aws: bool = True) -> _Project:
    """Read everything a project report states, in one place.

    Raises ``LookupError`` for an id that is not a project root, so the API
    can answer 404 instead of producing a report about nothing.
    """
    from bmem.branch import resolve_subtree
    from bmem.db import transaction
    from bmem.deploy import list_project_deployments
    from bmem.verdict import branch_verdicts

    try:
        root_id = str(uuid.UUID(root_id))
    except ValueError as exc:
        raise LookupError(f"{root_id!r} is not a project id") from exc

    with transaction() as cur:
        cur.execute(
            "SELECT id, created_at, source_repo, source_ref FROM branches "
            "WHERE id = %(root)s AND parent_id IS NULL",
            {"root": root_id},
        )
        root = cur.fetchone()
        if root is None:
            raise LookupError(f"project {root_id} not found")

        nodes = resolve_subtree(cur, root_id)
        ids = [n.id for n in nodes]

        cur.execute(
            """
            SELECT branch_id, run_id, strategy_name, outcome, passed, total, reason, patch_stats
            FROM branch_outcomes WHERE branch_id = ANY(%(ids)s::UUID[])
            """,
            {"ids": ids},
        )
        outcomes = {str(r["branch_id"]): r for r in cur.fetchall()}

        cur.execute(
            """
            SELECT branch_id, run_id, strategy_name, reasoning_model_id, embedding_model_id,
                   base_sha, target_ref, codebuild_project,
                   CASE WHEN jsonb_typeof(retrieved_memories) = 'array'
                        THEN jsonb_array_length(retrieved_memories) END AS retrieved
            FROM execution_manifests WHERE branch_id = ANY(%(ids)s::UUID[])
            """,
            {"ids": ids},
        )
        manifests = {str(r["branch_id"]): r for r in cur.fetchall()}

        cur.execute(
            """
            SELECT branch_id, count(*) AS n FROM memory
            WHERE branch_id = ANY(%(ids)s::UUID[]) AND NOT tombstone
            GROUP BY branch_id
            """,
            {"ids": ids},
        )
        written = {str(r["branch_id"]): int(r["n"]) for r in cur.fetchall()}

        cur.execute(
            """
            SELECT split_part(key, '::', 1) AS path, count(*) AS chunks FROM memory
            WHERE branch_id = %(root)s AND NOT tombstone
            GROUP BY 1
            """,
            {"root": root_id},
        )
        files = [(r["path"], int(r["chunks"])) for r in cur.fetchall()]

    branches: list[_Branch] = []
    for n in nodes:
        if n.id == root_id:
            continue
        o = outcomes.get(n.id) or {}
        m = manifests.get(n.id) or {}
        patch = o.get("patch_stats")
        if isinstance(patch, str):
            try:
                patch = json.loads(patch)
            except ValueError:
                patch = None
        branches.append(
            _Branch(
                id=n.id,
                parent_id=n.parent_id,
                depth=n.depth,
                hypothesis=n.hypothesis,
                status=n.status,
                score=n.score,
                created_at=n.created_at,
                strategy=m.get("strategy_name") or o.get("strategy_name"),
                run_id=m.get("run_id") or o.get("run_id"),
                outcome=o.get("outcome"),
                passed=o.get("passed"),
                total=o.get("total"),
                reason=o.get("reason"),
                patch=patch if isinstance(patch, dict) else None,
                reasoning_model=m.get("reasoning_model_id"),
                embedding_model=m.get("embedding_model_id"),
                base_sha=m.get("base_sha"),
                target_ref=m.get("target_ref"),
                codebuild_project=m.get("codebuild_project"),
                retrieved=m.get("retrieved"),
                beliefs_written=written.get(n.id, 0),
            )
        )

    project = _Project(
        root_id=root_id,
        repo=root["source_repo"],
        ref=root["source_ref"],
        created_at=root["created_at"].isoformat() if root["created_at"] else None,
        branches=branches,
        root_chunks=written.get(root_id, 0),
        files=files,
        decisions=decision_graph(root_id),
        deployments=list_project_deployments(root_id),
        stats=project_stats(root_id),
        costs=cost_report(root_id),
        verdicts={v.branch_id: v for v in branch_verdicts(root_id)},
    )

    if aws:
        from bmem.aws_inventory import codebuild_builds, storage_inventory

        for key, fn in (("storage", storage_inventory), ("builds", codebuild_builds)):
            try:
                result = fn(root_id)
                setattr(project, key, result)
                if result.get("error"):
                    project.aws_errors[key] = result["error"]
            except Exception as exc:  # noqa: BLE001 -- stated in the report
                project.aws_errors[key] = f"{type(exc).__name__}: {exc}"
    return project


# ── Shared project sections ──────────────────────────────────────────────────


def _cover(kind: str, p: _Project, sections: _Sections, purpose: str) -> list[Any]:
    st = _styles()
    title = _repo_name(p.repo) or f"Project {p.root_id[:8]}"
    best = p.best
    details: list[tuple[Any, ...]] = [
        ("Repository", p.repo or "not recorded"),
        ("Ref", p.ref or "not recorded", True),
        ("Project ID", p.root_id, True),
        ("Project created", _dt(p.created_at)),
        ("Report generated", _dt(datetime.now(UTC))),
    ]
    kpis = [
        ("Memory chunks", f"{p.root_chunks:,}", f"from {len(p.files):,} files"),
        (
            "Strategies evaluated",
            f"{len(p.evaluated)}",
            f"{len(p.branches)} branches · {len(p.runs)} run{'s' if len(p.runs) != 1 else ''}",
        ),
        (
            "Best test result",
            _tests(best.passed, best.total) if best else "—",
            (best.label if best else "no evaluation yet"),
        ),
        (
            "Decisions",
            f"{len(p.decisions)}",
            f"{sum(1 for d in p.decisions if not d.reverted_at)} in effect",
        ),
    ]
    return [
        Spacer(1, 14 * mm),
        Paragraph("BRANCHABLE MEMORY", st["wordmark"]),
        _accent_rule(),
        Paragraph(kind.upper(), st["kind"]),
        Paragraph(_esc(title), st["title"]),
        Paragraph(_esc(purpose), st["subtitle"]),
        _kv_table(details),
        Spacer(1, 7 * mm),
        _kpis(kpis),
        Spacer(1, 10 * mm),
        *sections.contents(),
        PageBreak(),
    ]


def _summary_points(p: _Project) -> list[Any]:
    st = _styles()
    pts: list[str] = []
    repo = _repo_name(p.repo)
    if repo:
        pts.append(
            f"<b>{_esc(repo)}</b>"
            + (f" at ref <font face='Courier'>{_esc(p.ref)}</font>" if p.ref else "")
            + f" was ingested on {_dt(p.created_at, date_only=True)} into "
            f"<b>{p.root_chunks:,}</b> memory chunks from <b>{len(p.files):,}</b> files."
        )
    else:
        pts.append(
            f"The source repository was not recorded for this project. Its memory holds "
            f"<b>{p.root_chunks:,}</b> chunks from <b>{len(p.files):,}</b> files."
        )

    ev = p.evaluated
    if ev:
        full = sum(1 for b in ev if b.full_pass)
        pts.append(
            f"<b>{len(ev)}</b> strateg{'y was' if len(ev) == 1 else 'ies were'} evaluated across "
            f"<b>{len(p.runs)}</b> run{'s' if len(p.runs) != 1 else ''}; "
            f"<b>{full}</b> passed the complete test suite."
        )
        best = p.best
        if best:
            pts.append(
                f"Best result: <b>{_esc(best.label)}</b> — {_tests(best.passed, best.total)} "
                f"tests passed ({_score(best.score)})."
            )
        failures = Counter(_reason(b.reason) for b in ev if b.reason and b.reason != "ok")
        if failures:
            pts.append(
                "Unsuccessful evaluations: "
                + ", ".join(
                    f"{n} × {_esc(r[:1].lower() + r[1:])}" for r, n in failures.most_common()
                )
                + "."
            )
    elif p.branches:
        pts.append(
            f"{len(p.branches)} strategy branch{'es were' if len(p.branches) != 1 else ' was'} "
            "created; none has a recorded evaluation result yet."
        )
    else:
        pts.append("No migration run has been started for this project yet.")

    if p.decisions:
        live = sum(1 for d in p.decisions if not d.reverted_at)
        applied = sum(1 for d in p.decisions if d.applied_commit_sha)
        pts.append(
            f"<b>{len(p.decisions)}</b> decision{'s' if len(p.decisions) != 1 else ''} merged: "
            f"{live} in effect, {len(p.decisions) - live} reverted, {applied} applied to a commit."
        )
    else:
        pts.append("No decision has been merged yet.")

    if p.deployments:
        latest = p.deployments[0].deployment
        pts.append(
            f"<b>{len(p.deployments)}</b> deployment{'s' if len(p.deployments) != 1 else ''} "
            f"recorded; the latest is {_esc(latest.environment)} — {_esc(latest.status)} "
            f"({_dt(latest.started_at)})."
        )
    else:
        pts.append("No deployments have been recorded.")

    if p.storage and not p.aws_errors.get("storage"):
        totals = p.storage["totals"]
        bucket = f"<font face='Courier'>{_esc(p.storage.get('bucket'))}</font>"
        pts.append(
            f"<b>{totals['objects']:,}</b> artifact objects ({_bytes(totals['bytes'])}) are "
            f"stored in S3 bucket {bucket}."
            if totals["objects"]
            else f"No artifacts for this project are stored in S3 bucket {bucket} yet."
        )
    if p.builds and not p.aws_errors.get("builds"):
        b = p.builds["builds"]
        ok = sum(1 for x in b if x["status"] == "SUCCEEDED")
        pts.append(
            f"<b>{len(b)}</b> CodeBuild evaluation build{'s' if len(b) != 1 else ''} ran for "
            f"this project; {ok} completed successfully."
            if b
            else "No CodeBuild evaluation builds have run for this project yet."
        )
    return [Paragraph(text, st["bullet"], bulletText="•") for text in pts]


def _source_section(p: _Project) -> list[Any]:
    by_status = Counter(b.status for b in p.branches)
    return [
        _kv_table(
            [
                ("Repository", p.repo or "not recorded"),
                ("Ref", p.ref or "not recorded", True),
                (
                    "Base commit",
                    next((b.base_sha for b in p.branches if b.base_sha), None) or "not recorded",
                    True,
                ),
                ("Project created", _dt(p.created_at)),
                ("Memory chunks (root)", f"{p.root_chunks:,}"),
                ("Files ingested", f"{len(p.files):,}"),
                (
                    "Strategy branches",
                    f"{len(p.branches)}"
                    + (
                        " (" + ", ".join(f"{n} {s}" for s, n in sorted(by_status.items())) + ")"
                        if by_status
                        else ""
                    ),
                ),
                ("Pipeline runs", f"{len(p.runs)}"),
            ]
        )
    ]


def _codebase_section(p: _Project) -> list[Any]:
    st = _styles()
    if not p.files:
        return [_note("No source files are stored in this project's memory.")]
    total_chunks = sum(c for _, c in p.files) or 1
    dirs_files: Counter[str] = Counter()
    dirs_chunks: Counter[str] = Counter()
    ext_files: Counter[str] = Counter()
    ext_chunks: Counter[str] = Counter()
    for path, chunks in p.files:
        top = path.split("/", 1)[0] + "/" if "/" in path else "(repository root)"
        ext = os.path.splitext(path)[1].lower() or "(no extension)"
        dirs_files[top] += 1
        dirs_chunks[top] += chunks
        ext_files[ext] += 1
        ext_chunks[ext] += chunks

    def table(title: str, files: Counter[str], chunks: Counter[str], limit: int) -> list[Any]:
        half = (_CONTENT_W - 6 * mm) / 2
        ordered = chunks.most_common()
        rows = [
            [name, f"{files[name]:,}", f"{c:,}", f"{c / total_chunks:.0%}"]
            for name, c in ordered[:limit]
        ]
        rest = ordered[limit:]
        if rest:
            rf = sum(files[n] for n, _ in rest)
            rc = sum(c for _, c in rest)
            rows.append([f"{len(rest)} others", f"{rf:,}", f"{rc:,}", f"{rc / total_chunks:.0%}"])
        return [
            Paragraph(title, st["h3"]),
            _grid_table(
                ["Name", "Files", "Chunks", "Share"],
                rows,
                [half - 48 * mm, 14 * mm, 17 * mm, 17 * mm],
                right=(1, 2, 3),
            ),
        ]

    largest = sorted(p.files, key=lambda f: f[1], reverse=True)[:8]
    return [
        Paragraph(
            f"The repository is held as <b>{total_chunks:,}</b> searchable chunks — one per "
            f"function, class or module section — across <b>{len(p.files):,}</b> files. "
            "Each chunk is embedded once and inherited by every strategy branch without copying.",
            st["body"],
        ),
        _side_by_side(
            table("By top-level directory", dirs_files, dirs_chunks, 10),
            table("By file type", ext_files, ext_chunks, 10),
        ),
        Paragraph("Largest files by chunk count", st["h3"]),
        _grid_table(
            ["File", "Chunks"],
            [[f, f"{c:,}"] for f, c in largest],
            [_CONTENT_W - 22 * mm, 22 * mm],
            right=(1,),
            mono=(0,),
        ),
    ]


def _branch_tone(b: _Branch) -> tuple[str, str | None]:
    if b.outcome == "won" or b.status == "merged":
        return "Won", "good"
    if b.status == "exploring" and not b.evaluated:
        return "In progress", "warn"
    if b.evaluated and b.full_pass:
        return "Passed", "good"
    if b.evaluated:
        return "Lost" if b.outcome == "lost" and (b.reason in (None, "ok")) else "Failed", "bad"
    return b.status.capitalize(), None


def _strategy_section(p: _Project) -> list[Any]:
    if not p.branches:
        return [
            _note(
                "No strategies have been explored for this project. Start a migration "
                "run from the dashboard to populate this section."
            )
        ]
    ordered = sorted(
        p.branches,
        key=lambda b: (-(b.passed or 0) / (b.total or 1) if b.total else 1, b.created_at or ""),
    )
    rows = []
    for b in ordered:
        v = p.verdicts.get(b.id)
        if v is not None:
            word, tone = {
                "merged": ("Merged", "good"),
                "abandoned": ("Abandoned", "bad"),
                "pending": ("Pending", "warn"),
            }[v.decision]
            why: Any = v.headline
        else:
            word, tone = _branch_tone(b)
            why = _reason(b.reason)
        rows.append(
            [
                _two_line(b.label, _clip(b.hypothesis, 80) if b.strategy else b.id[:8]),
                _status(word, tone),
                _tests(b.passed, b.total),
                _score(b.score),
                why,
            ]
        )
    return [
        Paragraph(
            "<b>Decision rule.</b> The strategy that passes the largest share of the "
            "repository's test suite is merged. If the top scores are within one percentage "
            "point, the suite has not separated them and the smaller patch is merged. Every "
            "other strategy in the run is abandoned. The last column states, for each branch, "
            "which recorded measurement decided it.",
            _styles()["body"],
        ),
        _grid_table(
            ["Strategy", "Decision", "Tests passed", "Score", "Why merged / abandoned"],
            rows,
            [44 * mm, 21 * mm, 21 * mm, 15 * mm, 73 * mm],
            right=(2, 3),
        ),
        Spacer(1, 2 * mm),
        Paragraph(
            "Score is the share of the repository's own test suite that passed in an "
            "isolated CodeBuild container after applying the strategy's patch.",
            _styles()["muted"],
        ),
    ]


def _decisions_table(p: _Project) -> list[Any]:
    if not p.decisions:
        return [
            _note(
                "No decision has been merged. A decision is recorded when the best "
                "strategy of a run is merged into its parent branch."
            )
        ]
    rows = []
    for d in p.decisions:
        state, tone = (
            ("Reverted", "bad")
            if d.reverted_at
            else ("Applied", "good")
            if d.applied_commit_sha
            else ("Merged", "good")
        )
        rows.append(
            [
                _dt(d.merged_at),
                _two_line(
                    d.winner_hypothesis or d.winner_branch_id[:8],
                    f"decision {d.merge_id[:8]}" + (" · tie-broken" if d.tie_detected else ""),
                ),
                _score(d.winner_score),
                str(len(d.alternatives)),
                _status(state, tone),
            ]
        )
    return [
        _grid_table(
            ["Merged", "Winning strategy", "Score", "Alternatives", "State"],
            rows,
            [34 * mm, 78 * mm, 18 * mm, 22 * mm, 22 * mm],
            right=(2, 3),
        )
    ]


def _deployments_section(p: _Project) -> list[Any]:
    if not p.deployments:
        return [_note("No deployments have been recorded for this project.")]
    rows = []
    for pd in p.deployments:
        d = pd.deployment
        tone = {"healthy": "good", "failed": "bad", "rolled_back": "warn"}.get(d.status)
        rows.append(
            [
                d.environment,
                _status(d.status.replace("_", " "), tone),
                (d.commit_sha or "—")[:12],
                d.target or "not recorded",
                _dt(d.started_at),
            ]
        )
    return [
        _grid_table(
            ["Environment", "Status", "Commit", "Target", "Started"],
            rows,
            [26 * mm, 24 * mm, 28 * mm, 50 * mm, 46 * mm],
            mono=(2,),
        )
    ]


def _aws_section(p: _Project, *, detailed: bool) -> list[Any]:
    st = _styles()
    out: list[Any] = []

    out.append(Paragraph("Amazon S3 artifacts", st["h3"]))
    if p.aws_errors.get("storage") or not p.storage:
        out.append(
            _note(f"S3 could not be read: {_esc(p.aws_errors.get('storage', 'not queried'))}")
        )
    else:
        s = p.storage
        out.append(
            _kv_table(
                [
                    ("Bucket", s.get("bucket"), True),
                    ("Region", s.get("region"), True),
                    ("Objects for this project", f"{s['totals']['objects']:,}"),
                    ("Stored size", _bytes(s["totals"]["bytes"])),
                    ("Applied patch bundles", f"{len(s.get('applied', []))}"),
                ]
            )
        )
        if detailed:
            rows = []
            for run in s.get("runs", []):
                for br in run["branches"]:
                    if not br["objects"]:
                        continue
                    rows.append(
                        [
                            run["run_id"][:12],
                            br.get("strategy") or br["branch_id"][:8],
                            ", ".join(o["name"] for o in br["objects"]),
                            _bytes(sum(o["size"] for o in br["objects"])),
                        ]
                    )
            if rows:
                out += [
                    Spacer(1, 3 * mm),
                    _grid_table(
                        ["Run", "Strategy", "Objects", "Size"],
                        rows,
                        [26 * mm, 40 * mm, 86 * mm, 22 * mm],
                        right=(3,),
                        mono=(0,),
                    ),
                ]

    out.append(Paragraph("AWS CodeBuild evaluations", st["h3"]))
    if p.aws_errors.get("builds") or not p.builds:
        out.append(
            _note(f"CodeBuild could not be read: {_esc(p.aws_errors.get('builds', 'not queried'))}")
        )
        return out
    builds = p.builds["builds"]
    if not builds:
        out.append(
            _note(
                f"No evaluation builds in CodeBuild project "
                f"<font face='Courier'>{_esc(p.builds.get('project'))}</font> "
                "belong to this project."
            )
        )
        return out

    def secs(b: dict[str, Any]) -> float | None:
        if not (b.get("start_time") and b.get("end_time")):
            return None
        return (
            datetime.fromisoformat(b["end_time"]) - datetime.fromisoformat(b["start_time"])
        ).total_seconds()

    durations = [d for d in (secs(b) for b in builds) if d is not None]
    ok = sum(1 for b in builds if b["status"] == "SUCCEEDED")
    out.append(
        _kv_table(
            [
                ("Project", p.builds.get("project"), True),
                ("Builds", f"{len(builds)} ({ok} succeeded, {len(builds) - ok} other)"),
                (
                    "Average build time",
                    _duration(sum(durations) / len(durations)) if durations else "—",
                ),
            ]
        )
    )
    if detailed:
        rows = []
        for b in builds:
            tone = {"SUCCEEDED": "good", "IN_PROGRESS": "warn"}.get(b["status"] or "", "bad")
            rows.append(
                [
                    f"#{b.get('build_number') or '?'}",
                    b.get("strategy") or (b.get("branch_id") or "")[:8] or "—",
                    _status((b["status"] or "unknown").replace("_", " ").lower(), tone),
                    _duration(secs(b)),
                    _dt(b.get("start_time")),
                ]
            )
        out += [
            Spacer(1, 3 * mm),
            _grid_table(
                ["Build", "Strategy", "Status", "Duration", "Started"],
                rows,
                [16 * mm, 62 * mm, 26 * mm, 22 * mm, 48 * mm],
                right=(3,),
            ),
        ]
        phase_secs: dict[str, list[int]] = {}
        for b in builds:
            for ph in b.get("phases", []):
                if ph.get("phase") and ph.get("seconds") is not None:
                    phase_secs.setdefault(ph["phase"], []).append(ph["seconds"])
        if phase_secs:
            out += [
                Paragraph("Average time per build phase", st["h3"]),
                _grid_table(
                    ["Phase", "Builds", "Average", "Longest"],
                    [
                        [
                            ph.replace("_", " ").capitalize(),
                            str(len(v)),
                            _duration(sum(v) / len(v)),
                            _duration(max(v)),
                        ]
                        for ph, v in phase_secs.items()
                    ],
                    [74 * mm, 30 * mm, 35 * mm, 35 * mm],
                    right=(1, 2, 3),
                ),
            ]
    return out


def _cost_section(p: _Project) -> list[Any]:
    lines = p.costs.lines
    if not lines:
        return [_note("No usage has been measured for this project.")]
    rows = [
        [
            line.resource,
            line.usage,
            "measured" if line.measured else "estimated",
            f"${line.estimated_usd:,.4f}" if line.estimated_usd is not None else "no rate set",
        ]
        for line in lines
    ]
    out: list[Any] = [
        _grid_table(
            ["Resource", "Usage", "Basis", "Cost (USD)"],
            rows,
            [52 * mm, 64 * mm, 26 * mm, 32 * mm],
            right=(3,),
        )
    ]
    total = p.costs.total_estimated_usd
    out += [
        Spacer(1, 2 * mm),
        Paragraph(
            f"Total: <b>${total:,.2f}</b>"
            if total is not None
            else "No unit prices are configured on the server (BMEM_PRICE_* settings), so usage is "
            "reported without a dollar figure rather than with an assumed one.",
            _styles()["muted"],
        ),
    ]
    return out


def _about_section(p: _Project) -> list[Any]:
    sources = [
        "CockroachDB (branches, memory, branch_outcomes, execution_manifests, "
        "merge_log, decision_rationales, deployments)"
    ]
    if p.storage and not p.aws_errors.get("storage"):
        sources.append(f"Amazon S3 ({p.storage.get('bucket')})")
    if p.builds and not p.aws_errors.get("builds"):
        sources.append(f"AWS CodeBuild ({p.builds.get('project')})")
    return [
        _kv_table(
            [
                ("Project ID", p.root_id, True),
                ("Generated", _dt(datetime.now(UTC))),
                ("Data sources", "; ".join(sources)),
                (
                    "Unavailable sources",
                    "; ".join(f"{k}: {v}" for k, v in p.aws_errors.items()) or "none",
                ),
                (
                    "Integrity",
                    "The SHA-256 of this file is returned with the download in the "
                    "X-Report-SHA256 header.",
                ),
            ]
        ),
        Spacer(1, 2 * mm),
        Paragraph(
            "Every figure in this report was read from the sources above when the report "
            "was generated. Values shown as “—” or “not recorded” were never measured; "
            "nothing is estimated or illustrative.",
            _styles()["muted"],
        ),
    ]


# ── Project reports ──────────────────────────────────────────────────────────


def executive_pdf(root_id: str, *, data: _Project | None = None) -> ReportBundle:
    """Project-level summary: what was tried, what won, where things stand."""
    p = data or _collect(root_id)
    sec = _Sections()
    sec.add("Summary", _summary_points(p))
    sec.add("Project and source", _source_section(p))
    sec.add("Ingested codebase", _codebase_section(p))
    sec.add("Strategy evaluation", _strategy_section(p))
    sec.add("Decisions", _decisions_table(p))
    sec.add("Deployments", _deployments_section(p))
    sec.add("AWS resources", _aws_section(p, detailed=False))
    sec.add("Usage and cost", _cost_section(p))
    sec.add("About this report", _about_section(p))
    story = [
        *_cover(
            "Executive report",
            p,
            sec,
            "What this project explored, which strategy performed best, and where its "
            "decisions stand — read from the recorded pipeline data.",
        ),
        *sec.render(),
    ]
    return _build(story, title="Executive Report", subject_id=root_id)


def _runs_section(p: _Project) -> list[Any]:
    if not p.runs:
        return [_note("No pipeline run has recorded an execution manifest for this project.")]
    st = _styles()
    out: list[Any] = []
    for run_id in p.runs:
        bs = [b for b in p.branches if b.run_id == run_id]
        first = bs[0]
        started = min((b.created_at for b in bs if b.created_at), default=None)
        out.append(
            KeepTogether(
                [
                    Paragraph(f"Run <font face='Courier'>{_esc(run_id)}</font>", st["h3"]),
                    _kv_table(
                        [
                            ("Started", _dt(started)),
                            (
                                "Strategies",
                                f"{len(bs)} ({sum(1 for b in bs if b.evaluated)} evaluated)",
                            ),
                            ("Reasoning model", first.reasoning_model or "not recorded", True),
                            ("Embedding model", first.embedding_model or "not recorded", True),
                            ("Target ref", first.target_ref or "not recorded", True),
                            ("Base commit", first.base_sha or "not recorded", True),
                            ("CodeBuild project", first.codebuild_project or "not recorded", True),
                        ]
                    ),
                ]
            )
        )
    return out


def _patches_section(p: _Project) -> list[Any]:
    with_patch = [b for b in p.branches if b.patch]
    if not with_patch:
        return [_note("No patch statistics have been recorded for this project's strategies.")]
    rows = []
    for b in with_patch:
        ps = b.patch or {}
        touches = [
            name
            for flag, name in (
                ("touches_tests", "tests"),
                ("touches_dependencies", "dependencies"),
                ("touches_schema", "schema"),
            )
            if ps.get(flag)
        ]
        risk = ps.get("risk") or "—"
        rows.append(
            [
                _two_line(b.label, b.id[:8]),
                f"{ps.get('files_changed', 0):,}",
                f"+{ps.get('lines_added', 0):,} / −{ps.get('lines_removed', 0):,}",
                ", ".join(touches) or "—",
                _status(
                    risk,
                    {"high": "bad", "moderate": "warn", "low": "good", "none": "good"}.get(risk),
                ),
                f"{b.retrieved}" if b.retrieved is not None else "—",
            ]
        )
    return [
        _grid_table(
            ["Strategy", "Files", "Lines", "Touches", "Risk", "Memories used"],
            rows,
            [58 * mm, 14 * mm, 30 * mm, 30 * mm, 18 * mm, 24 * mm],
            right=(1, 2, 5),
        )
    ]


def _decision_details(p: _Project) -> list[Any]:
    if not p.decisions:
        return _decisions_table(p)
    st = _styles()
    out: list[Any] = []
    for d in p.decisions:
        block: list[Any] = [
            Paragraph(_esc(d.winner_hypothesis or d.winner_branch_id[:8]), st["h3"]),
            _kv_table(
                [
                    ("Decision", d.merge_id, True),
                    ("Run", d.run_id or "not recorded", True),
                    ("Merged", _dt(d.merged_at)),
                    ("State", "reverted " + _dt(d.reverted_at) if d.reverted_at else "in effect"),
                    ("Score", _pct(d.winner_score)),
                    ("Tie-broken", "yes" if d.tie_detected else "no"),
                    ("Rationale", d.rationale or "not recorded"),
                    ("Applied commit", d.applied_commit_sha or "not applied", True),
                    ("Validation", d.applied_validation_status or "—"),
                ]
            ),
        ]
        if d.alternatives:
            block += [
                Spacer(1, 2 * mm),
                _grid_table(
                    ["Alternative strategy", "Tests passed", "Score", "Outcome"],
                    [
                        [
                            a.strategy_name or _clip(a.hypothesis, 70) or a.branch_id[:8],
                            _tests(a.passed, a.total),
                            _score(a.score),
                            _reason(a.reason),
                        ]
                        for a in d.alternatives
                    ],
                    [72 * mm, 26 * mm, 18 * mm, 58 * mm],
                    right=(1, 2),
                ),
            ]
        block.append(Spacer(1, 3 * mm))
        out.append(KeepTogether(block))
    return out


def engineering_pdf(
    root_id: str, *, include_patch: bool = False, data: _Project | None = None
) -> ReportBundle:
    """The executive content plus run configuration, patches, builds and artifacts."""
    del include_patch
    p = data or _collect(root_id)
    sec = _Sections()
    sec.add("Summary", _summary_points(p))
    sec.add("Project and source", _source_section(p))
    sec.add("Ingested codebase", _codebase_section(p))
    sec.add("Run configuration", _runs_section(p))
    sec.add("Strategy evaluation", _strategy_section(p))
    sec.add("Patches", _patches_section(p))
    sec.add("Decisions", _decision_details(p))
    sec.add("Deployments", _deployments_section(p))
    sec.add("AWS resources", _aws_section(p, detailed=True))
    sec.add("Usage and cost", _cost_section(p))
    sec.add("About this report", _about_section(p))
    story = [
        *_cover(
            "Engineering report",
            p,
            sec,
            "Run configuration, per-strategy test results, patches, CodeBuild evaluations "
            "and stored artifacts for this project.",
        ),
        *sec.render(),
    ]
    return _build(story, title="Engineering Report", subject_id=root_id)


# ── Decision report ──────────────────────────────────────────────────────────


def decision_pdf(merge_id: str, *, include_patch: bool = False) -> ReportBundle:
    """One decision, end to end: what won, why, what it changed, what followed."""
    del include_patch
    st = _styles()
    evidence = get_decision_evidence(merge_id)
    cf = get_counterfactual(merge_id)
    diff = memory_diff(merge_id)

    winner = cf.winner
    rivals = cf.alternatives

    sec = _Sections()
    sec.add(
        "Outcome",
        [
            _kv_table(
                [
                    ("Winning strategy", evidence.winning_hypothesis or "not recorded"),
                    ("Evaluation score", _pct(evidence.winning_score)),
                    ("Tests passed", _tests(winner.passed, winner.total) if winner else "—"),
                    ("Merged", _dt(evidence.merged_at)),
                    (
                        "State",
                        "reverted " + _dt(evidence.reverted_at)
                        if evidence.reverted_at
                        else "in effect",
                    ),
                    ("Base commit", evidence.base_sha or "not recorded", True),
                    ("Patch stored", "yes" if evidence.patch_available else "no"),
                ]
            )
        ],
    )
    sec.add(
        "What the agent concluded",
        [
            Paragraph(
                _esc(evidence.belief)
                if evidence.belief
                else "No conclusion was recorded for this branch.",
                st["body"],
            ),
        ],
    )
    why: list[Any] = [Paragraph(_esc(cf.rationale), st["body"])]
    if cf.tie_detected:
        why.append(
            _note(
                "The scores did not separate these strategies. The winner was chosen by an "
                f"explicit tie-breaker ({_esc(cf.discriminator or 'unspecified')}), not by the "
                "test suite."
            )
        )
    sec.add("Why this strategy won", why)
    if rivals:
        sec.add(
            "Alternatives considered",
            [
                _grid_table(
                    ["Strategy", "Tests passed", "Score", "Outcome", "Evidence"],
                    [
                        [
                            a.strategy_name or _clip(a.hypothesis, 60) or a.branch_id[:8],
                            _tests(a.passed, a.total),
                            _score(a.score),
                            _reason(a.reason),
                            "; ".join(a.points) or "—",
                        ]
                        for a in rivals
                    ],
                    [44 * mm, 22 * mm, 16 * mm, 32 * mm, 60 * mm],
                    right=(1, 2),
                )
            ],
        )
    sec.add(
        "Effect on memory",
        [
            _kv_table(
                [
                    ("Parent beliefs before", f"{diff.parent_before_count:,}"),
                    ("Parent beliefs after", f"{diff.parent_after_count:,}"),
                    ("Contributed by winner", f"{diff.branch_count:,}"),
                    (
                        "Added / changed / removed",
                        f"{len(diff.added):,} / {len(diff.changed):,} / {len(diff.tombstoned):,}",
                    ),
                    ("Untouched", f"{diff.unchanged_count:,}"),
                ]
            )
        ],
    )
    if evidence.latest_application:
        a = evidence.latest_application
        applied: list[Any] = [
            _kv_table(
                [
                    ("Validation", a.validation_status),
                    ("Branch", a.branch or "—", True),
                    ("Commit", a.commit_sha or "not committed", True),
                    ("Attempted", _dt(a.created_at)),
                ]
            )
        ]
    else:
        applied = [_note("This decision has not been applied to a working checkout.")]
    sec.add("Applied change", applied)
    sec.add(
        "Review status",
        [
            Paragraph(
                "This patch was written by an AI agent and scored by the repository's own "
                "test suite. The system holds no record of a human review; a passing score "
                "means the suite "
                "passed, not that the approach has been approved.",
                st["body"],
            )
        ],
    )
    sec.add(
        "About this report",
        [
            _kv_table(
                [
                    ("Decision", merge_id, True),
                    ("Winning branch", evidence.winner_id, True),
                    ("Parent branch", evidence.parent_id, True),
                    ("Generated", _dt(datetime.now(UTC))),
                    (
                        "Integrity",
                        "SHA-256 of this file is returned in the X-Report-SHA256 header.",
                    ),
                ]
            )
        ],
    )

    story: list[Any] = [
        Paragraph("BRANCHABLE MEMORY", st["wordmark"]),
        _accent_rule(),
        Paragraph("DECISION REPORT", st["kind"]),
        Paragraph(_esc(evidence.winning_hypothesis or f"Decision {merge_id[:8]}"), st["title"]),
        Spacer(1, 3 * mm),
        _kpis(
            [
                ("Score", _score(evidence.winning_score), "winning strategy"),
                (
                    "Tests passed",
                    _tests(winner.passed, winner.total) if winner else "—",
                    "repository test suite",
                ),
                ("Alternatives", str(len(rivals)), "strategies compared"),
                (
                    "State",
                    "Reverted" if evidence.reverted_at else "In effect",
                    _dt(evidence.merged_at, date_only=True),
                ),
            ]
        ),
        *sec.render(),
    ]
    return _build(story, title="Decision Report", subject_id=merge_id)


# ── Archive ──────────────────────────────────────────────────────────────────


def project_archive(root_id: str) -> tuple[bytes, str]:
    """Everything about a project in one ZIP: PDFs, raw JSON, and a manifest.

    Returns ``(zip_bytes, sha256)``. The archive's own checksum covers every
    file inside it, so a reader can verify they have the same bundle that was
    generated.
    """
    data = _collect(root_id)
    stats = data.stats
    graph = data.decisions
    costs = data.costs
    executive = executive_pdf(root_id, data=data)
    engineering = engineering_pdf(root_id, data=data)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("executive-report.pdf", executive.content)
        zf.writestr("engineering-report.pdf", engineering.content)
        zf.writestr(
            "project.json",
            json.dumps(
                {
                    "root_id": root_id,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "stats": stats.model_dump(),
                    "cost": costs.model_dump(),
                },
                indent=2,
            ),
        )

        contents = {
            "executive-report.pdf": executive.sha256,
            "engineering-report.pdf": engineering.sha256,
        }

        for node in graph:
            short = node.merge_id[:8]
            report = decision_pdf(node.merge_id)
            zf.writestr(f"decisions/{short}/decision-report.pdf", report.content)
            contents[f"decisions/{short}/decision-report.pdf"] = report.sha256

            zf.writestr(
                f"decisions/{short}/decision.json",
                json.dumps(
                    {
                        "decision": node.model_dump(),
                        "counterfactual": get_counterfactual(node.merge_id).model_dump(),
                        "memory_diff": memory_diff(node.merge_id).model_dump(),
                    },
                    indent=2,
                    default=str,
                ),
            )

            try:
                manifest = get_manifest(node.winner_branch_id)
                zf.writestr(
                    f"manifests/{short}.json",
                    json.dumps(manifest.model_dump(), indent=2, default=str),
                )
            except LookupError:
                pass

        zf.writestr(
            "MANIFEST.json",
            json.dumps(
                {
                    "root_id": root_id,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "decisions": len(graph),
                    "sha256": contents,
                    "note": (
                        "Every value in this archive is read from recorded decision data. "
                        "Fields marked 'not recorded' were never measured."
                    ),
                },
                indent=2,
            ),
        )

    content = buffer.getvalue()
    return content, hashlib.sha256(content).hexdigest()


class ArchiveEntry(BaseModel):
    path: str
    bytes: int


def archive_listing(root_id: str) -> list[ArchiveEntry]:
    """What the archive contains, without handing over the archive.

    Built by generating the real ZIP and reading its own directory rather than
    describing the layout from memory -- a listing that can disagree with the
    file it describes is worse than no listing, and this one cannot, because
    it is the file's own table of contents.
    """
    content, _ = project_archive(root_id)
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return [ArchiveEntry(path=i.filename, bytes=i.file_size) for i in zf.infolist()]
