"""Why each branch was merged or abandoned, stated plainly.

A branch's status says *what* happened to it ("merged", "abandoned"); this
module says *why*, as one headline sentence plus supporting facts. Every
sentence is assembled from values recorded when the decision was made --
``branch_outcomes`` (each strategy's measured test result and failure
reason), ``decision_rationales`` (how the winner was chosen, including tie
detection) and ``merge_log`` (merges and reverts). Nothing is inferred: where
a measurement is missing the verdict says it is missing.

The rule being explained lives in :func:`bmem.orchestrate.select_winner`:
the highest test-suite score wins; when the top scores are within one
percentage point, the tied strategy with the smallest patch wins; every other
branch in the run is abandoned.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel

__all__ = ["BranchVerdict", "branch_verdict", "branch_verdicts", "explain"]

# Mirrors orchestrate.TIE_EPSILON (+ float slack). Duplicated rather than
# imported so this read-only module does not pull in the pipeline's deps.
_TIE = 0.01 + 1e-9

Kind = Literal[
    "merged",
    "reverted",
    "lost_on_score",
    "lost_tie_break",
    "lost_inert",
    "declined",
    "failed",
    "won_not_merged",
    "abandoned_unscored",
    "in_progress",
]


class BranchVerdict(BaseModel):
    branch_id: str
    parent_id: str | None
    strategy: str | None
    hypothesis: str | None
    status: str
    kind: Kind
    decision: Literal["merged", "abandoned", "pending"]
    headline: str
    reasons: list[str]
    score: float | None
    passed: int | None
    total: int | None
    evaluation_reason: str | None
    run_id: str | None
    merge_id: str | None
    winner_branch_id: str | None
    winner_strategy: str | None


# ── Plain-language failure reasons ───────────────────────────────────────────


def _failure_sentence(reason: str) -> str:
    head, _, detail = reason.partition(":")
    detail = detail.strip()
    known = {
        "patch_did_not_apply": "its patch did not apply cleanly to the repository, so the "
        "test suite could not be run against it",
        "no_report_produced": "the test run finished without producing a test report, so "
        "no test result could be measured",
        "explore_failed": "the agent did not produce a usable patch",
        "build_failed": "the evaluation build failed before the tests ran",
        "timeout": "the evaluation exceeded its time limit",
    }
    sentence = known.get(head, head.replace("_", " "))
    return f"{sentence} ({detail})" if detail else sentence


def _is_declined(row: dict[str, Any]) -> bool:
    """The model wrote no patch.

    Recorded directly as ``agent_declined`` since that case is detected
    before evaluation. Runs from before that detection recorded it as
    ``patch_did_not_apply`` -- the build failed on an empty diff -- and are
    recognised by the patch having changed no files at all.
    """
    reason = row.get("reason") or ""
    if reason.startswith("agent_declined"):
        return True
    ps = row.get("patch_stats")
    return (
        reason.startswith("patch_did_not_apply")
        and isinstance(ps, dict)
        and not ps.get("files_changed")
    )


def _is_inert(row: dict[str, Any] | None) -> bool:
    """Only new files, no existing file changed (see orchestrate.is_inert_patch)."""
    ps = (row or {}).get("patch_stats")
    if not isinstance(ps, dict):
        return False
    changed = int(ps.get("files_changed") or 0)
    return changed > 0 and int(ps.get("files_created") or 0) >= changed


def _declined_sentence(row: dict[str, Any], belief: str | None) -> str:
    reason = row.get("reason") or ""
    why = reason.partition(":")[2].strip() if reason.startswith("agent_declined") else ""
    why = why or (belief or "").strip()
    if why:
        why = " ".join(why.split())
        if len(why) > 420:
            why = why[:419].rstrip() + "…"
        return f'the agent declined to write a patch, saying: "{why}"'
    return "the agent declined to write a patch and gave no reason"


def _is_failure(reason: str | None) -> bool:
    """A reason that means the strategy could not be measured properly.

    ``ok`` and ``"<n> failing"`` are normal results -- the suite ran and the
    score reflects it -- so those lose on score, not as a failure.
    """
    if not reason or reason == "ok":
        return False
    return not reason.split(":", 1)[0].strip().endswith("failing")


# ── Formatting ───────────────────────────────────────────────────────────────


def _pct(v: float | None) -> str:
    return f"{v:.1%}" if v is not None else "no score"


def _tests(row: dict[str, Any]) -> str:
    if row.get("total"):
        return f"{row['passed']}/{row['total']} tests"
    return "no tests measured"


def _name(row: dict[str, Any] | None, branch: dict[str, Any] | None = None) -> str:
    if row and row.get("strategy_name"):
        return str(row["strategy_name"])
    src = branch or row or {}
    hyp = src.get("hypothesis")
    if hyp:
        return hyp if len(hyp) <= 60 else hyp[:59].rstrip() + "…"
    bid = src.get("branch_id") or src.get("id")
    return f"branch {str(bid)[:8]}" if bid else "another branch"


def _patch(row: dict[str, Any] | None) -> tuple[int, int] | None:
    ps = (row or {}).get("patch_stats")
    if not isinstance(ps, dict):
        return None
    return int(ps.get("files_changed") or 0), int(
        (ps.get("lines_added") or 0) + (ps.get("lines_removed") or 0)
    )


def _patch_text(p: tuple[int, int] | None) -> str:
    return f"{p[0]} files, {p[1]} lines" if p else "patch size not recorded"


def _date(v: Any) -> str:
    if v is None:
        return "an unrecorded time"
    try:
        return v.strftime("%d %b %Y %H:%M UTC")
    except AttributeError:
        return str(v)[:16].replace("T", " ") + " UTC"


# ── The rule, applied to recorded rows ───────────────────────────────────────


def explain(
    branches: Iterable[dict[str, Any]],
    outcomes: Iterable[dict[str, Any]],
    merges: Iterable[dict[str, Any]],
    rationales: Iterable[dict[str, Any]],
    manifests: Iterable[dict[str, Any]] = (),
    beliefs: dict[str, str] | None = None,
) -> list[BranchVerdict]:
    """Pure: turn recorded rows into one verdict per non-root branch.

    ``branches``: id, parent_id, hypothesis, status, score.
    ``outcomes``: branch_outcomes rows. ``merges``: merge_log rows (id,
    parent_id, winner_id, merged_at, reverted_at). ``rationales``:
    decision_rationales rows. ``manifests``: branch_id, run_id, strategy_name.
    ``beliefs``: branch_id -> the belief that branch wrote (the agent's own
    account, used to say why it declined).
    """
    beliefs = beliefs or {}
    by_branch = {str(o["branch_id"]): o for o in outcomes}
    by_run: dict[str, list[dict[str, Any]]] = {}
    for o in by_branch.values():
        by_run.setdefault(str(o["run_id"]), []).append(o)
    merge_rows = sorted(merges, key=lambda m: str(m.get("merged_at") or ""))
    merged_as_winner = {str(m["winner_id"]): m for m in merge_rows}
    rationale_by_merge = {str(r["merge_id"]): r for r in rationales}
    rationale_by_run = {str(r["run_id"]): r for r in rationale_by_merge.values()}
    manifest = {str(m["branch_id"]): m for m in manifests}
    branch_rows = {str(b["id"]): b for b in branches}

    out: list[BranchVerdict] = []
    for bid, b in branch_rows.items():
        if b.get("parent_id") is None:
            continue
        o = by_branch.get(bid)
        man = manifest.get(bid) or {}
        run_id = str(o["run_id"]) if o else (str(man["run_id"]) if man.get("run_id") else None)
        siblings = by_run.get(run_id or "", [])
        winner_row = next((s for s in siblings if s.get("outcome") == "won"), None)
        rat = rationale_by_run.get(run_id or "")
        base = {
            "branch_id": bid,
            "parent_id": str(b["parent_id"]),
            "strategy": (o or man).get("strategy_name") if (o or man) else None,
            "hypothesis": b.get("hypothesis"),
            "status": b["status"],
            "score": o.get("score") if o else b.get("score"),
            "passed": o.get("passed") if o else None,
            "total": o.get("total") if o else None,
            "evaluation_reason": o.get("reason") if o else None,
            "run_id": run_id,
            "merge_id": None,
            "winner_branch_id": str(winner_row["branch_id"]) if winner_row else None,
            "winner_strategy": _name(winner_row) if winner_row else None,
        }
        reasons: list[str] = []
        me = _name(o or man or None, b)

        if o:
            reasons.append(
                f"Measured result: {_tests(o)} passed ({_pct(o.get('score'))})."
                if o.get("total")
                else "No test result was measured for this branch."
            )
            if o.get("reason") and o["reason"] != "ok":
                reasons.append(f"Evaluation reported: {o['reason']}.")
        if len(siblings) > 1:
            reasons.append(
                f"Compared against {len(siblings) - 1} other strategies in run "
                f"{(run_id or '')[:12]}."
            )

        # 1. This branch was merged as a winner.
        m = merged_as_winner.get(bid)
        if m is not None:
            base["merge_id"] = str(m["id"])
            r = rationale_by_merge.get(str(m["id"]))
            why = (
                r["rationale"]
                if r and r.get("rationale")
                else (
                    "it was promoted as the winner of its run. No decision rationale was "
                    "recorded for this merge, so the comparison behind it is not available."
                )
            )
            if r and r.get("tie_detected"):
                reasons.append(
                    "The top scores were within 1 percentage point, so the test suite did not "
                    "separate them; the tie was broken by the smallest patch "
                    f"({_patch_text(_patch(o))})."
                )
            elif r and r.get("margin") is not None:
                reasons.append(f"Won by a margin of {r['margin']:.1%} over the runner-up.")
            if m.get("reverted_at") is not None:
                reasons.append(
                    f"Merged {_date(m.get('merged_at'))}; reverted {_date(m.get('reverted_at'))}."
                )
                later = [
                    x
                    for x in merge_rows
                    if str(x["parent_id"]) == base["parent_id"]
                    and str(x["id"]) != str(m["id"])
                    and str(x.get("merged_at") or "") >= str(m.get("merged_at") or "")
                ]
                replaced = ""
                if later:
                    lid = str(later[0]["winner_id"])
                    replaced = (
                        f" {_name(by_branch.get(lid), branch_rows.get(lid))} "
                        "was merged in its place."
                    )
                out.append(
                    BranchVerdict(
                        **base,
                        kind="reverted",
                        decision="abandoned",
                        headline=(
                            f"Abandoned by a revert: {me} had been merged because {why} "
                            "The merge was later reverted, which restored the parent's earlier "
                            f"memory and retired this branch.{replaced}"
                        ),
                        reasons=reasons,
                    )
                )
            else:
                reasons.append(
                    f"Merged {_date(m.get('merged_at'))}; every other branch in the "
                    "run was abandoned."
                )
                out.append(
                    BranchVerdict(
                        **base,
                        kind="merged",
                        decision="merged",
                        headline=f"Merged because {why}"
                        + (
                            ""
                            if not _is_inert(o) or "changes no existing code" in why
                            else " Caution: its patch only adds new files and changes no "
                            "existing code, so passing the suite shows it broke nothing, not "
                            "that the goal was implemented."
                        ),
                        reasons=reasons,
                    )
                )
            continue

        # 2. Still running.
        if b["status"] == "exploring":
            out.append(
                BranchVerdict(
                    **base,
                    kind="in_progress",
                    decision="pending",
                    headline="No decision yet: this branch is still being explored or evaluated.",
                    reasons=reasons,
                )
            )
            continue

        # 3. Evaluated, and its run had a winner.
        if o and winner_row is not None and str(winner_row["branch_id"]) != bid:
            w = _name(winner_row)
            w_result = f"{w} passed {_tests(winner_row)} ({_pct(winner_row.get('score'))})"
            gap = (winner_row.get("score") or 0.0) - (o.get("score") or 0.0)
            if _is_declined(o):
                kind: Kind = "declined"
                headline = (
                    f"Abandoned because {_declined_sentence(o, beliefs.get(bid))}. "
                    "No patch means nothing could be tested. "
                    f"{w_result} and was merged instead."
                )
            elif _is_failure(o.get("reason")):
                kind = "failed"
                headline = (
                    f"Abandoned because {_failure_sentence(o['reason'])}. "
                    f"{w_result} and was merged instead."
                )
            else:
                if (
                    rat
                    and rat.get("tie_detected")
                    and gap <= _TIE
                    and _is_inert(o)
                    and not _is_inert(winner_row)
                ):
                    kind = "lost_inert"
                    headline = (
                        f"Abandoned although it passed {_tests(o)} "
                        f"({_pct(o.get('score'))}): its patch only adds new files that "
                        "nothing in the application uses, so passing the suite shows it "
                        f"broke nothing, not that it implemented the goal. {w} scored level "
                        "and changed existing code, so it was merged."
                    )
                elif rat and rat.get("tie_detected") and gap <= _TIE:
                    kind = "lost_tie_break"
                    headline = (
                        f"Abandoned on the tie-break: it passed {_tests(o)} "
                        f"({_pct(o.get('score'))}), level with {w} within 1 point, so the "
                        f"smaller change was preferred — {w} changed "
                        f"{_patch_text(_patch(winner_row))} against "
                        f"{_patch_text(_patch(o))} here."
                    )
                else:
                    kind = "lost_on_score"
                    headline = (
                        f"Abandoned because it scored lower: {_tests(o)} passed "
                        f"({_pct(o.get('score'))}), {gap:.1%} behind {w}, which passed "
                        f"{_tests(winner_row)} ({_pct(winner_row.get('score'))})."
                    )
            out.append(
                BranchVerdict(
                    **base, kind=kind, decision="abandoned", headline=headline, reasons=reasons
                )
            )
            continue

        # 4. Recorded as the run's winner but never merged (merge conflict).
        if o and o.get("outcome") == "won":
            out.append(
                BranchVerdict(
                    **base,
                    kind="won_not_merged",
                    decision="abandoned",
                    headline=(
                        "Abandoned although it had the best score in its run: the parent "
                        "already had a merged winner, and a parent can hold only one."
                    ),
                    reasons=reasons,
                )
            )
            continue

        # 5. Abandoned with no recorded evaluation.
        other = next(
            (
                x
                for x in reversed(merge_rows)
                if str(x["parent_id"]) == base["parent_id"] and str(x["winner_id"]) != bid
            ),
            None,
        )
        if other is not None:
            wid = str(other["winner_id"])
            w = _name(by_branch.get(wid), branch_rows.get(wid))
            base["winner_branch_id"], base["winner_strategy"] = wid, w
            base["merge_id"] = str(other["id"])
            headline = (
                f"Abandoned without a score: {w} was merged into the same parent "
                f"({_date(other.get('merged_at'))}), and a parent keeps only one "
                "merged winner, so this branch was retired before its result was "
                "recorded."
            )
        else:
            headline = (
                "Abandoned without a recorded result: the run stopped before this "
                "branch was scored, so there is no measurement to compare."
            )
        out.append(
            BranchVerdict(
                **base,
                kind="abandoned_unscored",
                decision="abandoned",
                headline=headline,
                reasons=reasons,
            )
        )
    return out


# ── Database access ──────────────────────────────────────────────────────────


def branch_verdicts(root_id: str) -> list[BranchVerdict]:
    """Verdicts for every branch under ``root_id``."""
    from bmem.db import transaction

    with transaction() as cur:
        cur.execute(
            """
            WITH RECURSIVE subtree(id, parent_id, hypothesis, status, score) AS (
                    SELECT id, parent_id, hypothesis, status, score
                    FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id, b.parent_id, b.hypothesis, b.status, b.score
                    FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            SELECT * FROM subtree
            """,
            {"root": root_id},
        )
        branches = cur.fetchall()
        if not branches:
            raise LookupError(f"project {root_id} not found")
        ids = [str(b["id"]) for b in branches]
        cur.execute(
            """
            SELECT branch_id, run_id, parent_id, strategy_name, hypothesis, outcome, score,
                   passed, total, reason, score_gap, patch_stats
            FROM branch_outcomes WHERE branch_id = ANY(%(ids)s::UUID[])
            """,
            {"ids": ids},
        )
        outcomes = cur.fetchall()
        cur.execute(
            """
            SELECT id, parent_id, winner_id, merged_at, reverted_at FROM merge_log
            WHERE parent_id = ANY(%(ids)s::UUID[])
            """,
            {"ids": ids},
        )
        merges = cur.fetchall()
        cur.execute(
            """
            SELECT merge_id, run_id, winner_branch_id, margin, tie_detected, discriminator,
                   rationale
            FROM decision_rationales
            WHERE merge_id = ANY(%(ids)s::UUID[])
            """,
            {"ids": [str(m["id"]) for m in merges]},
        )
        rationales = cur.fetchall()
        cur.execute(
            """
            SELECT branch_id, run_id, strategy_name FROM execution_manifests
            WHERE branch_id = ANY(%(ids)s::UUID[])
            """,
            {"ids": ids},
        )
        manifests = cur.fetchall()
        cur.execute(
            """
            SELECT branch_id, content FROM memory
            WHERE branch_id = ANY(%(ids)s::UUID[]) AND key LIKE '%%-belief' AND NOT tombstone
            """,
            {"ids": ids},
        )
        beliefs = {str(r["branch_id"]): r["content"] for r in cur.fetchall() if r["content"]}
    return explain(branches, outcomes, merges, rationales, manifests, beliefs)


def branch_verdict(branch_id: str) -> BranchVerdict:
    """The verdict for one branch, found via its root."""
    from bmem.db import transaction

    with transaction() as cur:
        cur.execute(
            """
            WITH RECURSIVE up(id, parent_id) AS (
                    SELECT id, parent_id FROM branches WHERE id = %(b)s
                UNION ALL
                    SELECT b.id, b.parent_id FROM branches b JOIN up ON b.id = up.parent_id
            )
            SELECT id FROM up WHERE parent_id IS NULL
            """,
            {"b": branch_id},
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"branch {branch_id} not found")
    if str(row["id"]) == branch_id:
        raise LookupError("the project root is not a candidate; it is never merged or abandoned")
    for v in branch_verdicts(str(row["id"])):
        if v.branch_id == branch_id:
            return v
    raise LookupError(f"branch {branch_id} not found")
