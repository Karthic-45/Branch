"""Assembles the full evidence trail behind one merge -- belief, retrieved
memory, competing strategies, validation, provenance -- entirely by reading
what explore.py, evaluate.py, branch.py, and apply.py already recorded.

Nothing here calls a model or invents a number. If a fact was never written
down (a merge scored by a fake evaluator, a patch nobody applied yet), the
corresponding field is None or an empty list -- never a guess, per this
project's own non-negotiable: never fabricate evidence.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from bmem.db import transaction
from bmem.patchstats import PatchStats

__all__ = [
    "AlternativeStrategy",
    "Counterfactual",
    "DecisionEvidence",
    "DecisionNode",
    "MemoryDelta",
    "MemoryDiff",
    "StrategyComparison",
    "decision_graph",
    "get_counterfactual",
    "get_decision_evidence",
    "historical_failures",
    "memory_diff",
    "pr_description",
]


class AlternativeStrategy(BaseModel):
    branch_id: str
    hypothesis: str | None
    status: str
    score: float | None
    belief: str | None


class LatestApplication(BaseModel):
    validation_status: str
    branch: str | None
    commit_sha: str | None
    conflicts: list[str]
    created_at: str | None


class DecisionEvidence(BaseModel):
    merge_id: str
    parent_id: str
    winner_id: str
    winning_hypothesis: str | None
    winning_score: float | None
    belief: str | None
    merged_at: str | None
    reverted_at: str | None
    patch_available: bool
    base_sha: str | None
    alternatives: list[AlternativeStrategy]
    latest_application: LatestApplication | None


def get_decision_evidence(merge_id: str) -> DecisionEvidence:
    """Everything known about one merge, assembled in a handful of indexed
    reads -- this is what backs both the Evidence Graph UI and the PR/decision
    report text, so the two can never disagree about what happened.
    """
    with transaction() as cur:
        cur.execute(
            """
            SELECT m.id, m.parent_id, m.winner_id, m.merged_at, m.reverted_at,
                   b.hypothesis, b.score, b.fork_point
            FROM merge_log m JOIN branches b ON b.id = m.winner_id
            WHERE m.id = %(id)s
            """,
            {"id": merge_id},
        )
        merge_row = cur.fetchone()
        if merge_row is None:
            raise LookupError(f"no such merge: {merge_id}")

        cur.execute(
            "SELECT content, s3_uri, base_sha FROM memory "
            "WHERE branch_id = %(b)s AND key LIKE %(pat)s ORDER BY created_at_hlc DESC LIMIT 1",
            {"b": merge_row["winner_id"], "pat": "%-belief"},
        )
        belief_row = cur.fetchone()

        cur.execute(
            """
            SELECT id, hypothesis, status, score
            FROM branches
            WHERE parent_id = %(p)s AND fork_point = %(fp)s AND id != %(winner)s
            ORDER BY score DESC NULLS LAST
            """,
            {
                "p": merge_row["parent_id"],
                "fp": merge_row["fork_point"],
                "winner": merge_row["winner_id"],
            },
        )
        sibling_rows = cur.fetchall()

        alternatives: list[AlternativeStrategy] = []
        for sib in sibling_rows:
            cur.execute(
                "SELECT content FROM memory WHERE branch_id = %(b)s AND key LIKE %(pat)s "
                "ORDER BY created_at_hlc DESC LIMIT 1",
                {"b": sib["id"], "pat": "%-belief"},
            )
            sib_belief = cur.fetchone()
            alternatives.append(
                AlternativeStrategy(
                    branch_id=str(sib["id"]),
                    hypothesis=sib["hypothesis"],
                    status=sib["status"],
                    score=sib["score"],
                    belief=sib_belief["content"] if sib_belief else None,
                )
            )

        cur.execute(
            """
            SELECT validation_status, branch, commit_sha, conflicts, created_at
            FROM decision_applications
            WHERE merge_id = %(id)s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            {"id": merge_id},
        )
        app_row = cur.fetchone()

    latest_application = None
    if app_row is not None:
        latest_application = LatestApplication(
            validation_status=app_row["validation_status"],
            branch=app_row["branch"],
            commit_sha=app_row["commit_sha"],
            conflicts=app_row["conflicts"] or [],
            created_at=app_row["created_at"].isoformat() if app_row["created_at"] else None,
        )

    return DecisionEvidence(
        merge_id=merge_id,
        parent_id=str(merge_row["parent_id"]),
        winner_id=str(merge_row["winner_id"]),
        winning_hypothesis=merge_row["hypothesis"],
        winning_score=merge_row["score"],
        belief=belief_row["content"] if belief_row else None,
        merged_at=merge_row["merged_at"].isoformat() if merge_row["merged_at"] else None,
        reverted_at=merge_row["reverted_at"].isoformat() if merge_row["reverted_at"] else None,
        patch_available=bool(belief_row and belief_row["s3_uri"]),
        base_sha=belief_row["base_sha"] if belief_row else None,
        alternatives=alternatives,
        latest_application=latest_application,
    )


class StrategyComparison(BaseModel):
    branch_id: str
    strategy_name: str | None
    hypothesis: str | None
    outcome: str
    score: float | None
    passed: int | None
    total: int | None
    reason: str | None
    score_gap: float | None
    patch_stats: PatchStats | None
    # Bullet points, each derived from one recorded number. Never generated
    # prose -- see _comparison_points.
    points: list[str]


class Counterfactual(BaseModel):
    merge_id: str
    rationale: str
    tie_detected: bool
    discriminator: str | None
    margin: float | None
    winner: StrategyComparison | None
    alternatives: list[StrategyComparison]
    strategy_similarity: list[dict[str, Any]]


def _comparison_points(row: dict[str, Any], winner: dict[str, Any] | None) -> list[str]:
    """Turn one branch's recorded numbers into plain statements.

    Every bullet traces to a stored value. Nothing is inferred about *why* a
    test failed or whether an approach was sound -- only what was measured.
    A missing measurement produces no bullet rather than a hedge.
    """
    points: list[str] = []
    stats = PatchStats.model_validate(row["patch_stats"]) if row.get("patch_stats") else None
    is_winner = row["outcome"] == "won"

    if row.get("reason") and row["reason"] != "ok":
        points.append(f"evaluation reported: {row['reason']}")
    if row.get("total"):
        points.append(f"passed {row['passed']}/{row['total']} tests")

    if not is_winner and winner is not None and row.get("score") is not None:
        gap = (winner.get("score") or 0.0) - row["score"]
        if gap > 0:
            points.append(f"{gap:.1%} behind the winner")
        elif gap == 0:
            points.append("scored level with the winner")

    if stats:
        if stats.files_changed == 0:
            points.append("produced no applicable file changes")
        else:
            points.append(
                f"changed {stats.files_changed} files "
                f"(+{stats.lines_added}/-{stats.lines_removed}), risk {stats.risk}"
            )
        if stats.touches_dependencies:
            points.append("modifies dependency manifests")
        if stats.touches_schema:
            points.append("modifies database schema")
    return points


def _to_comparison(row: dict[str, Any], winner: dict[str, Any] | None) -> StrategyComparison:
    return StrategyComparison(
        branch_id=str(row["branch_id"]),
        strategy_name=row["strategy_name"],
        hypothesis=row["hypothesis"],
        outcome=row["outcome"],
        score=row["score"],
        passed=row["passed"],
        total=row["total"],
        reason=row["reason"],
        score_gap=row["score_gap"],
        patch_stats=(
            PatchStats.model_validate(row["patch_stats"]) if row.get("patch_stats") else None
        ),
        points=_comparison_points(row, winner),
    )


def get_counterfactual(merge_id: str) -> Counterfactual:
    """"Why this one, and why not the others" -- from recorded evidence only.

    Reads the rationale written at merge time (so the answer cannot drift
    from the decision that was actually made) plus each branch's own
    measured outcome. A merge from before this feature existed has no
    rationale row; that is reported as such rather than reconstructed, since
    a reconstruction would be a new opinion wearing an old decision's clothes.
    """
    with transaction() as cur:
        cur.execute(
            """
            SELECT r.run_id, r.winner_branch_id, r.margin, r.tie_detected,
                   r.discriminator, r.rationale, r.strategy_similarity, m.parent_id
            FROM merge_log m LEFT JOIN decision_rationales r ON r.merge_id = m.id
            WHERE m.id = %(id)s
            """,
            {"id": merge_id},
        )
        head = cur.fetchone()
        if head is None:
            raise LookupError(f"no such merge: {merge_id}")

        if head["run_id"] is None:
            return Counterfactual(
                merge_id=merge_id,
                rationale=(
                    "No decision rationale was recorded for this merge -- it predates "
                    "rationale tracking. The scores remain in the branch tree, but why "
                    "the winner was chosen over the alternatives was not written down."
                ),
                tie_detected=False,
                discriminator=None,
                margin=None,
                winner=None,
                alternatives=[],
                strategy_similarity=[],
            )

        cur.execute(
            """
            SELECT branch_id, strategy_name, hypothesis, outcome, score, passed, total,
                   reason, score_gap, patch_stats
            FROM branch_outcomes
            WHERE run_id = %(run_id)s
            ORDER BY score DESC NULLS LAST
            """,
            {"run_id": head["run_id"]},
        )
        rows = cur.fetchall()

    winner_row = next((r for r in rows if r["outcome"] == "won"), None)
    return Counterfactual(
        merge_id=merge_id,
        rationale=head["rationale"],
        tie_detected=head["tie_detected"],
        discriminator=head["discriminator"],
        margin=head["margin"],
        winner=_to_comparison(winner_row, winner_row) if winner_row else None,
        alternatives=[_to_comparison(r, winner_row) for r in rows if r["outcome"] != "won"],
        strategy_similarity=head["strategy_similarity"] or [],
    )


def historical_failures(parent_id: str, limit: int = 10) -> list[StrategyComparison]:
    """Strategies already tried against this parent and lost.

    Fed back into planning (see :mod:`bmem.orchestrate`) so a later run can
    see that an approach has been measured here before rather than
    rediscovering the same dead end. Scoped to one parent because "this
    failed" is a claim about a specific codebase state, not a universal one.
    """
    with transaction() as cur:
        cur.execute(
            """
            SELECT branch_id, strategy_name, hypothesis, outcome, score, passed, total,
                   reason, score_gap, patch_stats
            FROM branch_outcomes
            WHERE parent_id = %(p)s AND outcome = 'lost'
            ORDER BY created_at DESC
            LIMIT %(limit)s
            """,
            {"p": parent_id, "limit": limit},
        )
        rows = cur.fetchall()
    return [_to_comparison(r, None) for r in rows]


class DecisionNode(BaseModel):
    merge_id: str
    run_id: str | None
    parent_id: str
    winner_branch_id: str
    winner_hypothesis: str | None
    winner_score: float | None
    merged_at: str | None
    reverted_at: str | None
    tie_detected: bool
    rationale: str | None
    alternatives: list[StrategyComparison]
    applied_commit_sha: str | None
    applied_validation_status: str | None


def decision_graph(root_id: str) -> list[DecisionNode]:
    """Every decision made anywhere in this root's subtree, oldest first.

    The branch tree shows *structure*; this shows the sequence of choices
    that produced it -- each problem, the strategies that competed, which one
    won, and whether it reached a real commit. Successive migrations on the
    same lineage chain naturally: each node's parent is the state the
    previous decision left behind.
    """
    with transaction() as cur:
        cur.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM branches WHERE id = %(root)s
                UNION ALL
                    SELECT b.id FROM branches b JOIN subtree s ON b.parent_id = s.id
            )
            SELECT m.id AS merge_id, m.parent_id, m.winner_id, m.merged_at, m.reverted_at,
                   b.hypothesis AS winner_hypothesis, b.score AS winner_score,
                   r.run_id, r.tie_detected, r.rationale,
                   (SELECT a.commit_sha FROM decision_applications a
                     WHERE a.merge_id = m.id AND a.commit_sha IS NOT NULL
                     ORDER BY a.created_at DESC LIMIT 1) AS applied_commit_sha,
                   (SELECT a.validation_status FROM decision_applications a
                     WHERE a.merge_id = m.id
                     ORDER BY a.created_at DESC LIMIT 1) AS applied_validation_status
            FROM merge_log m
            JOIN branches b ON b.id = m.winner_id
            LEFT JOIN decision_rationales r ON r.merge_id = m.id
            WHERE m.parent_id IN (SELECT id FROM subtree)
            ORDER BY m.merged_at ASC
            """,
            {"root": root_id},
        )
        merges = cur.fetchall()

        nodes: list[DecisionNode] = []
        for m in merges:
            alternatives: list[StrategyComparison] = []
            if m["run_id"]:
                cur.execute(
                    """
                    SELECT branch_id, strategy_name, hypothesis, outcome, score, passed,
                           total, reason, score_gap, patch_stats
                    FROM branch_outcomes
                    WHERE run_id = %(run_id)s AND outcome != 'won'
                    ORDER BY score DESC NULLS LAST
                    """,
                    {"run_id": m["run_id"]},
                )
                alt_rows = cur.fetchall()
                alternatives = [_to_comparison(r, None) for r in alt_rows]

            nodes.append(
                DecisionNode(
                    merge_id=str(m["merge_id"]),
                    run_id=m["run_id"],
                    parent_id=str(m["parent_id"]),
                    winner_branch_id=str(m["winner_id"]),
                    winner_hypothesis=m["winner_hypothesis"],
                    winner_score=m["winner_score"],
                    merged_at=m["merged_at"].isoformat() if m["merged_at"] else None,
                    reverted_at=m["reverted_at"].isoformat() if m["reverted_at"] else None,
                    tie_detected=bool(m["tie_detected"]),
                    rationale=m["rationale"],
                    alternatives=alternatives,
                    applied_commit_sha=m["applied_commit_sha"],
                    applied_validation_status=m["applied_validation_status"],
                )
            )
    return nodes


class MemoryDelta(BaseModel):
    key: str
    change: str  # added | changed | unchanged | tombstoned
    before: str | None
    after: str | None


class MemoryDiff(BaseModel):
    """Three states of one belief set, and the deltas between them.

    ``parent_before`` and ``branch`` are the two inputs to the merge;
    ``added``/``changed``/``tombstoned`` describe what promoting the winner
    did to the parent.
    """

    merge_id: str
    parent_id: str
    winner_id: str
    parent_before_count: int
    branch_count: int
    parent_after_count: int
    added: list[MemoryDelta]
    changed: list[MemoryDelta]
    tombstoned: list[MemoryDelta]
    unchanged_count: int


def memory_diff(merge_id: str) -> MemoryDiff:
    """What promoting this winner actually did to the parent's beliefs.

    Computed from the pre-merge snapshot plus the winner's own rows rather
    than by reading the parent's current state, so the answer isolates *this*
    merge: a later migration on the same parent, or a revert, cannot smear
    into it. That also means this stays correct for a merge that has since
    been reverted -- which is exactly when someone wants to look.

    The merge itself is an UPSERT of the winner's rows into the parent (see
    branch.merge), so the arithmetic here mirrors that: a winner key absent
    from the parent is an addition, a key present with different content is a
    change, and a winner row carrying a tombstone removes the key from what
    the parent can retrieve.
    """
    from bmem.branch import reconstruct_pre_merge_state

    with transaction() as cur:
        cur.execute(
            "SELECT parent_id, winner_id FROM merge_log WHERE id = %(id)s", {"id": merge_id}
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"no such merge: {merge_id}")
        parent_id, winner_id = str(row["parent_id"]), str(row["winner_id"])

        cur.execute(
            "SELECT key, content, tombstone FROM memory WHERE branch_id = %(b)s",
            {"b": winner_id},
        )
        branch_rows = cur.fetchall()

    before = {r.key: r for r in reconstruct_pre_merge_state(merge_id, via="snapshot")}

    added: list[MemoryDelta] = []
    changed: list[MemoryDelta] = []
    tombstoned: list[MemoryDelta] = []
    unchanged = 0

    for r in branch_rows:
        key, content, is_tombstone = r["key"], r["content"], r["tombstone"]
        prior = before.get(key)
        prior_content = prior.content if prior else None

        if is_tombstone:
            tombstoned.append(
                MemoryDelta(key=key, change="tombstoned", before=prior_content, after=None)
            )
        elif prior is None:
            added.append(MemoryDelta(key=key, change="added", before=None, after=content))
        elif prior_content != content:
            changed.append(
                MemoryDelta(key=key, change="changed", before=prior_content, after=content)
            )
        else:
            unchanged += 1

    # Keys the parent already held and the winner never touched survive the
    # merge untouched -- counted, not listed, because on a real repository
    # that is thousands of ingested chunks and listing them would bury the
    # handful of rows the decision actually moved.
    untouched = len(before) - sum(1 for r in branch_rows if r["key"] in before)
    parent_after = len(before) + len(added)

    return MemoryDiff(
        merge_id=merge_id,
        parent_id=parent_id,
        winner_id=winner_id,
        parent_before_count=len(before),
        branch_count=len(branch_rows),
        parent_after_count=parent_after,
        added=added,
        changed=changed,
        tombstoned=tombstoned,
        unchanged_count=unchanged + untouched,
    )


def _fmt_score(score: float | None) -> str:
    return f"{score:.0%}" if score is not None else "no score on record"


def pr_description(evidence: DecisionEvidence) -> str:
    """Markdown PR body per the spec's own §8 shape: summary, motivation,
    winning strategy, evidence, alternatives, validation, risks, provenance.

    This is content generation only -- turning it into an actual GitHub PR
    needs a write-scoped token this deployment doesn't have (see api.py's
    apply_merge docstring). Returning the text is still useful on its own:
    it's exactly what a human would paste into a PR description by hand.
    """
    lines = [
        "## Summary",
        evidence.winning_hypothesis or "(no hypothesis on record)",
        "",
        "## Motivation",
        f"Branch {evidence.winner_id} won a {len(evidence.alternatives) + 1}-way competition "
        f"with a score of {_fmt_score(evidence.winning_score)}.",
        "",
        "## Winning strategy",
        evidence.belief or "(no belief recorded for this branch)",
        "",
        "## Alternatives",
    ]
    if evidence.alternatives:
        for alt in evidence.alternatives:
            lines.append(
                f"- **{alt.hypothesis or alt.branch_id}** -- {_fmt_score(alt.score)}, "
                f"{alt.status}. {alt.belief or ''}".rstrip()
            )
    else:
        lines.append("(no sibling branches on record for this merge)")

    lines += ["", "## Validation"]
    if evidence.latest_application:
        app = evidence.latest_application
        lines.append(f"Status: **{app.validation_status}**")
        if app.conflicts:
            lines.append(f"Conflicts: {len(app.conflicts)} lines of `git apply` output on record.")
        if app.commit_sha:
            lines.append(f"Committed locally as `{app.commit_sha[:12]}` on branch `{app.branch}`.")
    else:
        lines.append("Not yet applied to a checkout -- no validation run on record.")

    lines += [
        "",
        "## Risks",
        "This patch was written and evaluated by an AI agent against the repository's own "
        "test suite. It has not been human-reviewed. Treat it as a draft.",
        "",
        "## Provenance",
        f"- Decision ID: `{evidence.merge_id}`",
        f"- Winning branch: `{evidence.winner_id}`",
        f"- Base commit: `{evidence.base_sha or 'not recorded'}`",
        f"- Merged at: {evidence.merged_at or 'unknown'}",
    ]
    return "\n".join(lines)
