"""Plan -> fork -> explore -> evaluate -> merge, tied together end to end.

Everything this module calls already exists and is independently tested:
:mod:`bmem.plan` proposes strategies, :func:`bmem.branch.fork` turns each into
a branch, :func:`bmem.explore.explore_branch` implements one in isolation,
:func:`bmem.evaluate.evaluate_branch` scores it, :func:`bmem.branch.merge`
promotes the winner. This module's only job is the sequencing -- and running
the explore+evaluate phase across branches in parallel, since that phase is
the demo's actual point: four workers reasoning about four different
hypotheses at once, each seeing only its own branch's memory.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from bmem.branch import MergeConflict, fork, merge, merged_child
from bmem.db import run_transaction, transaction
from bmem.decision import StrategyComparison, historical_failures
from bmem.embed import Embedder
from bmem.evaluate import EvaluationResult, Evaluator, evaluate_branch, get_evaluator
from bmem.explore import Exploration, Explorer, belief_key, explore_branch
from bmem.manifest import get_manifest
from bmem.patchstats import analyze_patch
from bmem.plan import Planner, Strategy
from bmem.source_context import SourceTree
from bmem.source_context import checkout as source_checkout

__all__ = [
    "DECLINED_REASON",
    "BranchOutcome",
    "MigrationResult",
    "ReplayResult",
    "WinnerSelection",
    "already_merged_message",
    "is_inert_patch",
    "replay_branch",
    "run_migration",
    "select_winner",
]


@dataclass(frozen=True, slots=True)
class BranchOutcome:
    branch_id: str
    strategy: Strategy
    exploration: Exploration
    evaluation: EvaluationResult


@dataclass(frozen=True, slots=True)
class MigrationResult:
    run_id: str
    parent_id: str
    outcomes: list[BranchOutcome]
    winner_branch_id: str
    merge_id: str


# Two strategies within one percentage point of each other have not been
# meaningfully distinguished by the test suite -- on a 320-test suite that is
# a three-test difference, which is noise-adjacent for scoring a whole
# migration approach. Picking the nominally-higher one and calling it a
# decision would be presenting a coin flip as evidence.
TIE_EPSILON = 0.01

# Scores are ratios of integers, so a margin that is mathematically exactly
# TIE_EPSILON routinely lands a few ulps above it (0.90 - 0.01 evaluates to a
# margin of 0.010000000000000009). Without this slack the "within one point"
# rule silently excludes the exact boundary it names, and two branches three
# tests apart on a 320-test suite would be treated as a decisive result.
_FLOAT_SLACK = 1e-9

# Above this cosine similarity, two hypotheses are close enough that running
# both probably wasted a branch. Reported, never acted on automatically:
# the planner is a model and re-prompting it in a loop to chase a number is a
# worse failure mode than one redundant branch.
SIMILARITY_WARN_THRESHOLD = 0.9


@dataclass(frozen=True, slots=True)
class WinnerSelection:
    winner: BranchOutcome
    runner_up: BranchOutcome | None
    margin: float | None
    tie_detected: bool
    discriminator: str | None
    rationale: str


def _patch_size_key(outcome: BranchOutcome) -> tuple[int, int]:
    stats = analyze_patch(outcome.exploration.patch)
    return (stats.files_changed, stats.lines_added + stats.lines_removed)


def is_inert_patch(patch: str) -> bool:
    """True when a patch only creates new files and changes no existing one.

    Nothing already in the repository imports a file that did not exist, so
    such a patch cannot change the application's behaviour -- and the test
    suite, which exercises existing code, passes it trivially. On the first
    real CTFd migration the winner was exactly this: one new ``asgi.py`` that
    nothing referenced, chosen by the smallest-patch tie-break *because* it
    did the least. An empty or unparseable patch is not "inert" here; it has
    no files at all and loses on score.
    """
    stats = analyze_patch(patch)
    return stats.files_changed > 0 and stats.files_created >= stats.files_changed


def select_winner(outcomes: Sequence[BranchOutcome]) -> WinnerSelection:
    """Pick the winner, and record why -- including when the score alone did
    not actually decide it.

    Pure, so the rule is testable without a cluster. When the top two are
    within :data:`TIE_EPSILON`, every branch tied with the leader is
    re-ranked by patch size (fewer files, then fewer changed lines) and the
    discriminator is recorded. Patch size is the tie-breaker because it is
    the only candidate this project measures directly and deterministically
    -- runtime and cost are not captured per branch, and inventing a
    comparison from data that was never collected is exactly what the
    rationale record exists to prevent.
    """
    if not outcomes:
        raise ValueError("cannot select a winner from zero outcomes")

    ranked = sorted(outcomes, key=lambda o: o.evaluation.score, reverse=True)
    leader = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    inert_note = ""
    if is_inert_patch(leader.exploration.patch):
        inert_note = (
            f" Note: {leader.strategy.name}'s patch only adds new files and changes no "
            "existing code, so passing the suite shows it broke nothing, not that the goal "
            "was implemented."
        )

    if runner_up is None:
        return WinnerSelection(
            winner=leader,
            runner_up=None,
            margin=None,
            tie_detected=False,
            discriminator=None,
            rationale=(
                f"{leader.strategy.name} was the only branch evaluated, scoring "
                f"{leader.evaluation.score:.1%}.{inert_note}"
            ),
        )

    margin = leader.evaluation.score - runner_up.evaluation.score
    if margin > TIE_EPSILON + _FLOAT_SLACK:
        return WinnerSelection(
            winner=leader,
            runner_up=runner_up,
            margin=margin,
            tie_detected=False,
            discriminator=None,
            rationale=(
                f"{leader.strategy.name} scored {leader.evaluation.score:.1%}, "
                f"{margin:.1%} ahead of {runner_up.strategy.name} at "
                f"{runner_up.evaluation.score:.1%}.{inert_note}"
            ),
        )

    tied = [
        o
        for o in ranked
        if leader.evaluation.score - o.evaluation.score <= TIE_EPSILON + _FLOAT_SLACK
    ]
    names = ", ".join(o.strategy.name for o in tied)
    head = (
        f"{len(tied)} strategies ({names}) scored within {TIE_EPSILON:.0%} of each other "
        f"({leader.evaluation.score:.1%} to {tied[-1].evaluation.score:.1%}), so the score "
        "did not decide this."
    )

    # A tie between a patch that changes existing code and one that only adds
    # unused files is not a tie on substance: the second cannot have changed
    # behaviour, which is why it passed. Substantive patches are ranked first;
    # the smallest-change rule then applies within whichever group is left.
    substantive = [o for o in tied if not is_inert_patch(o.exploration.patch)]
    inert = [o for o in tied if is_inert_patch(o.exploration.patch)]
    pool = substantive or tied
    winner = min(pool, key=_patch_size_key)
    winner_stats = analyze_patch(winner.exploration.patch)
    size = (
        f"{winner_stats.files_changed} files, "
        f"{winner_stats.lines_added + winner_stats.lines_removed} lines"
    )
    if substantive and inert:
        skipped = ", ".join(o.strategy.name for o in inert)
        rationale = (
            f"{head} {skipped} only added new files that nothing uses, so passing the suite "
            "did not show the goal was implemented; among the strategies that changed "
            f"existing code, {winner.strategy.name} made the smallest change: {size}."
        )
        discriminator = "changes_existing_code"
    elif not substantive:
        rationale = (
            f"{head} {winner.strategy.name} was chosen for the smallest change: {size}. "
            "Note: every tied patch only adds new files and changes no existing code, so "
            "this run shows nothing was broken, not that the goal was implemented."
        )
        discriminator = "smaller_patch"
    else:
        rationale = f"{head} {winner.strategy.name} was chosen for the smallest change: {size}."
        discriminator = "smaller_patch"
    return WinnerSelection(
        winner=winner,
        runner_up=runner_up,
        margin=margin,
        tie_detected=True,
        discriminator=discriminator,
        rationale=rationale,
    )


def _fork_all(
    cur: psycopg.Cursor[Any], parent_id: str, strategies: Sequence[Strategy]
) -> dict[str, str]:
    """One fork per strategy, in the caller's transaction.

    A batch of sibling forks sharing one transaction see a consistent view of
    the parent -- see fork()'s own docstring -- which matters here because all
    four really are siblings of the same fork instant, not four unrelated
    forks that happened to land close together.
    """
    return {strategy.name: fork(cur, parent_id, strategy.hypothesis) for strategy in strategies}


def already_merged_message(parent_id: str, merged_id: str) -> str:
    """The one wording for "this parent already has a winner", shared by the
    pre-flight check here and the API's 409, so the CLI and the UI tell the
    user the same two ways forward."""
    return (
        f"branch {merged_id} is already merged into {parent_id}. "
        f"Revert that merge first, or run the next migration on the merged "
        f"branch {merged_id} instead."
    )


def _abandon_branches(cur: psycopg.Cursor[Any], branch_ids: Sequence[str]) -> None:
    """Retire a run's branches that can no longer be merged. Only touches
    branches still 'exploring' -- never rewrites a merged or already-abandoned
    one."""
    cur.execute(
        "UPDATE branches SET status = 'abandoned' "
        "WHERE id = ANY(%(ids)s::UUID[]) AND status = 'exploring'",
        {"ids": list(branch_ids)},
    )


def _record_patch_provenance(
    branch_id: str, strategy_name: str, s3_uri: str, base_sha: str | None
) -> None:
    """Attach the evaluated patch's S3 location and tested-against commit to
    the belief row explore_branch already wrote. Lives here, not in
    evaluate.py or explore.py: this is the one module that knows both the
    memory key (explore's convention) and the evaluation result (evaluate's)
    for the same branch. merge() already carries both columns forward into
    the parent on promotion -- see branch.py -- so setting them once here is
    the only write this feature needs.
    """

    def _write(cur: Any) -> None:
        cur.execute(
            "UPDATE memory SET s3_uri = %(uri)s, base_sha = %(sha)s "
            "WHERE branch_id = %(b)s AND key = %(k)s",
            {"uri": s3_uri, "sha": base_sha, "b": branch_id, "k": belief_key(strategy_name)},
        )
        # Same fact, the manifest's copy -- see _record_evaluation_environment
        # for why this row already exists by the time evaluation finishes.
        cur.execute(
            "UPDATE execution_manifests SET base_sha = %(sha)s, updated_at = now() "
            "WHERE branch_id = %(b)s",
            {"sha": base_sha, "b": branch_id},
        )

    run_transaction(_write)


def _record_evaluation_environment(branch_id: str, evaluate: Evaluator) -> None:
    """Fill in the other half of this branch's reproducibility manifest --
    what it was actually tested against -- by introspecting the already-
    configured evaluator the same way explore.py reads model_id/provider off
    ``explore``. A FakeEvaluator has none of these attributes, so a
    fake-scored run's manifest honestly shows nulls here rather than a
    guessed repo/project name.
    """

    def _write(cur: Any) -> None:
        cur.execute(
            """
            UPDATE execution_manifests
            SET target_repo = %(target_repo)s, target_ref = %(target_ref)s,
                test_selector = %(test_selector)s, codebuild_project = %(project)s,
                updated_at = now()
            WHERE branch_id = %(branch_id)s
            """,
            {
                "branch_id": branch_id,
                "target_repo": getattr(evaluate, "target_repo", None),
                "target_ref": getattr(evaluate, "target_ref", None),
                "test_selector": getattr(evaluate, "test_selector", None),
                "project": getattr(evaluate, "project", None),
            },
        )

    run_transaction(_write)


def _failure_note(failure: StrategyComparison) -> str:
    """Render one past failure as planner context.

    Phrased as evidence with its measurement attached, not as a prohibition:
    a strategy that failed against an older state of the repository may be
    the right answer now, and the planner is better placed to weigh that than
    a hard filter here would be.
    """
    measured = f"scored {failure.score:.0%}" if failure.score is not None else "was not scored"
    reason = f" ({failure.reason})" if failure.reason and failure.reason != "ok" else ""
    return (
        f"PREVIOUSLY ATTEMPTED AND LOST on this branch: "
        f'"{failure.hypothesis or failure.strategy_name}" -- {measured}{reason}. '
        f"Propose something materially different unless the repository state has changed."
    )


def _strategy_similarity(strategies: Sequence[Strategy], embed: Embedder) -> list[dict[str, Any]]:
    """Pairwise cosine between the proposed hypotheses, from real embeddings.

    Both embedders in this project return unit vectors -- Titan is asked for
    ``normalize: True`` and FakeEmbedder divides by its own norm -- so the
    dot product *is* the cosine, with no renormalisation needed here.

    Under BMEM_FAKE_EMBEDDINGS the numbers are real arithmetic over vectors
    that carry no semantics, so near-zero similarity between genuinely
    similar hypotheses is expected there and means nothing. Reported either
    way, because inventing a "looks fine" result for a fake backend would be
    worse than a number the sandbox banner already tells you not to trust.
    """
    if len(strategies) < 2:
        return []
    vectors = embed([s.hypothesis for s in strategies])
    pairs: list[dict[str, Any]] = []
    for i in range(len(strategies)):
        for j in range(i + 1, len(strategies)):
            cosine = sum(a * b for a, b in zip(vectors[i], vectors[j], strict=True))
            pairs.append(
                {"a": strategies[i].name, "b": strategies[j].name, "cosine": round(cosine, 4)}
            )
    return pairs


def _record_decision(
    *,
    merge_id: str,
    run_id: str,
    parent_id: str,
    outcomes: Sequence[BranchOutcome],
    selection: WinnerSelection,
    similarity: list[dict[str, Any]],
) -> None:
    """Persist why each branch won or lost, and why the winner won.

    The losers' rows are the point, not a byproduct: a future run planning
    against this same parent can read them (see
    :func:`bmem.decision.historical_failures`) and avoid re-proposing an
    approach that has already been tried and measured against this repo.
    """
    winner_id = selection.winner.branch_id

    def _write(cur: Any) -> None:
        for outcome in outcomes:
            stats = analyze_patch(outcome.exploration.patch)
            cur.execute(
                """
                UPSERT INTO branch_outcomes
                    (branch_id, run_id, parent_id, strategy_name, hypothesis, outcome,
                     score, passed, total, reason, score_gap, patch_stats)
                VALUES
                    (%(branch_id)s, %(run_id)s, %(parent_id)s, %(strategy_name)s,
                     %(hypothesis)s, %(outcome)s, %(score)s, %(passed)s, %(total)s,
                     %(reason)s, %(score_gap)s, %(patch_stats)s)
                """,
                {
                    "branch_id": outcome.branch_id,
                    "run_id": run_id,
                    "parent_id": parent_id,
                    "strategy_name": outcome.strategy.name,
                    "hypothesis": outcome.strategy.hypothesis,
                    "outcome": "won" if outcome.branch_id == winner_id else "lost",
                    "score": outcome.evaluation.score,
                    "passed": outcome.evaluation.passed,
                    "total": outcome.evaluation.total,
                    "reason": outcome.evaluation.reason,
                    "score_gap": selection.winner.evaluation.score - outcome.evaluation.score,
                    "patch_stats": stats.model_dump_json(),
                },
            )

        cur.execute(
            """
            UPSERT INTO decision_rationales
                (merge_id, run_id, winner_branch_id, winner_score, runner_up_branch_id,
                 runner_up_score, margin, tie_detected, discriminator, rationale,
                 strategy_similarity)
            VALUES
                (%(merge_id)s, %(run_id)s, %(winner_branch_id)s, %(winner_score)s,
                 %(runner_up_branch_id)s, %(runner_up_score)s, %(margin)s, %(tie_detected)s,
                 %(discriminator)s, %(rationale)s, %(strategy_similarity)s)
            """,
            {
                "merge_id": merge_id,
                "run_id": run_id,
                "winner_branch_id": winner_id,
                "winner_score": selection.winner.evaluation.score,
                "runner_up_branch_id": (
                    selection.runner_up.branch_id if selection.runner_up else None
                ),
                "runner_up_score": (
                    selection.runner_up.evaluation.score if selection.runner_up else None
                ),
                "margin": selection.margin,
                "tie_detected": selection.tie_detected,
                "discriminator": selection.discriminator,
                "rationale": selection.rationale,
                "strategy_similarity": json.dumps(similarity),
            },
        )

    run_transaction(_write)


def _explore_and_evaluate(
    branch_id: str,
    strategy: Strategy,
    run_id: str,
    *,
    explore: Explorer,
    evaluate: Evaluator,
    embed: Embedder,
    on_event: Callable[[str, str], None] | None = None,
    source: SourceTree | None = None,
) -> BranchOutcome:
    try:
        exploration = explore_branch(
            branch_id,
            strategy,
            explore=explore,
            embed=embed,
            run_id=run_id,
            on_event=on_event,
            source=source,
        )
        if not exploration.patch.strip():
            # The model returned no diff -- it declined, and its belief says
            # why. Sending an empty patch to CodeBuild only buys a build that
            # fails with "No valid patches in input", which was then reported
            # as "patch_did_not_apply": true of the build, misleading about
            # the strategy. Record the actual event, without spending a build.
            return _declined_outcome(branch_id, strategy, exploration, on_event)
        _record_evaluation_environment(branch_id, evaluate)
        evaluation = evaluate_branch(
            branch_id, run_id, exploration.patch, evaluate=evaluate, on_event=on_event
        )
    except Exception as exc:  # noqa: BLE001 -- one branch must not sink the run
        # Found on the first real CTFd run: one branch's Claude response was
        # cut off mid-JSON, the exception escaped the thread pool, and the
        # whole run failed -- discarding two sibling branches that had
        # already passed 358/358 tests. A branch that cannot finish is a
        # branch that lost, with the reason on record, not a crashed run.
        return _failed_outcome(branch_id, strategy, exc, on_event)
    if evaluation.patch_s3_uri:
        _record_patch_provenance(
            branch_id, strategy.name, evaluation.patch_s3_uri, evaluation.base_sha
        )
    return BranchOutcome(
        branch_id=branch_id, strategy=strategy, exploration=exploration, evaluation=evaluation
    )


DECLINED_REASON = "agent_declined"


def _declined_outcome(
    branch_id: str,
    strategy: Strategy,
    exploration: Exploration,
    on_event: Callable[[str, str], None] | None,
) -> BranchOutcome:
    """A strategy whose model wrote no patch: scored 0, reason = its own words."""
    why = " ".join(exploration.belief.split()) or "no explanation given"
    reason = f"{DECLINED_REASON}: {why}"[:600]
    try:
        run_transaction(
            lambda cur: cur.execute(
                "UPDATE branches SET score = 0.0 WHERE id = %(id)s", {"id": branch_id}
            )
        )
    except Exception:  # noqa: BLE001 -- the outcome below is still reported
        pass
    if on_event:
        on_event("scored", f"0% -- agent declined to write a patch: {why[:200]}")
    return BranchOutcome(
        branch_id=branch_id,
        strategy=strategy,
        exploration=exploration,
        evaluation=EvaluationResult(passed=0, total=0, score=0.0, reason=reason),
    )


def _failed_outcome(
    branch_id: str,
    strategy: Strategy,
    exc: Exception,
    on_event: Callable[[str, str], None] | None,
) -> BranchOutcome:
    reason = f"branch_failed: {type(exc).__name__}: {exc}"[:300]
    evaluation = EvaluationResult(passed=0, total=0, score=0.0, reason=reason)
    try:
        run_transaction(
            lambda cur: cur.execute(
                "UPDATE branches SET score = 0.0 WHERE id = %(id)s", {"id": branch_id}
            )
        )
    except Exception:  # noqa: BLE001 -- the outcome below is still reported
        pass
    if on_event:
        on_event("scored", f"0% (0/0) -- {reason}")
    return BranchOutcome(
        branch_id=branch_id,
        strategy=strategy,
        exploration=Exploration(belief=f"Branch failed before scoring: {reason}", patch=""),
        evaluation=evaluation,
    )


def run_migration(
    parent_id: str,
    goal: str,
    *,
    plan: Planner,
    explore: Explorer,
    evaluate: Evaluator,
    embed: Embedder,
    n: int = 4,
    run_id: str | None = None,
    on_event: Callable[[str | None, str, str], None] | None = None,
    source_repo: str | None = None,
    source_ref: str | None = None,
) -> MigrationResult:
    """Run one complete migration: propose, fork, explore, evaluate, merge.

    ``source_repo``/``source_ref`` (defaulting to the evaluator's own target,
    when it has one) are checked out once per run so every strategy sees the
    files it edits whole -- see :mod:`bmem.source_context`. A failed checkout
    is reported and the run continues on search excerpts alone.

    The highest scorer is always merged, even if every branch scored poorly --
    picking "best available" and letting a human or a later run reconsider is
    the same choice a CI-gated promotion would make with only bad options on
    the table. There is no minimum-score floor here; that is a policy decision
    for the caller, not something this function should decide silently.

    Explore and evaluate run one thread per branch (``n`` workers): both
    involve real I/O latency (an LLM call, then a build-and-test cycle that
    can take minutes for real), and the four branches have nothing to
    coordinate with each other by design -- parallel is not an optimization
    here, it is the point being demonstrated.

    ``on_event(branch_id, stage, message)`` fires at every stage transition
    across the whole run, ``branch_id=None`` for run-level events (planning,
    merging) and the actual id for per-branch ones. Optional and inert by
    default -- existing callers, and every test, are unaffected.
    """
    run_id = run_id or uuid.uuid4().hex

    def _emit(branch_id: str | None, stage: str, message: str) -> None:
        if on_event:
            on_event(branch_id, stage, message)

    # Fail fast. merge() allows one live winner per parent, so a run against a
    # parent that already has one would plan, fork, explore and build every
    # branch -- minutes of work and real CodeBuild time -- only to hit
    # MergeConflict at the very last step and leave n branches stuck in
    # 'exploring' forever. merge() still re-checks inside its own transaction;
    # this only stops the obviously doomed run before it costs anything.
    existing = merged_child(parent_id)
    if existing is not None:
        raise MergeConflict(already_merged_message(parent_id, existing))

    with transaction() as cur:
        cur.execute(
            "SELECT key, content FROM memory WHERE branch_id = %(b)s",
            {"b": parent_id},
        )
        # A plain read of the parent's own rows for planning context -- not
        # retrieval.search(), which resolves an entire lineage and is
        # unnecessary work when the parent has no ancestors of its own yet
        # (it is typically the trunk). If that stops being true later, switch
        # this to search(cur, parent_id, embed(goal)[0], k=8).
        parent_memory = [row["content"] for row in cur.fetchall() if row["content"]]

    # Failure memory: what has already been tried against this exact parent
    # and lost, handed to the planner as context alongside the repository
    # excerpts. Without this the planner re-proposes known-dead approaches on
    # every run, because nothing in the branch tree tells it they were dead.
    failures = historical_failures(parent_id, limit=6)
    if failures:
        _emit(
            None,
            "planning",
            f"{len(failures)} previously-failed strateg"
            f"{'y' if len(failures) == 1 else 'ies'} on record for this branch",
        )
        parent_memory = [*parent_memory, *(_failure_note(f) for f in failures)]

    _emit(None, "planning", f"Proposing {n} migration strategies for: {goal}")
    strategies = plan(goal, parent_memory, n=n)
    _emit(None, "planned", "Proposed: " + ", ".join(s.name for s in strategies))

    similarity = _strategy_similarity(strategies, embed)
    redundant = [p for p in similarity if p["cosine"] >= SIMILARITY_WARN_THRESHOLD]
    if redundant:
        pairs = ", ".join(f"{p['a']}~{p['b']} ({p['cosine']:.0%})" for p in redundant)
        _emit(
            None,
            "planned",
            f"Strategy overlap: {pairs} -- near-identical hypotheses explore less "
            f"than the branch count suggests",
        )

    # run_transaction, not the bare context manager: this is n INSERTs in one
    # transaction, the exact shape that has hit CockroachDB's SQLSTATE 40001
    # transaction-retry error against this cluster before -- see
    # db.run_transaction's docstring, and the four_way fixture / 50-fork test
    # fixes earlier in this project's history for the same bug in miniature.
    branches = run_transaction(lambda cur: _fork_all(cur, parent_id, strategies))
    for strategy in strategies:
        _emit(branches[strategy.name], "forked", f"Forked for: {strategy.hypothesis}")

    repo = source_repo or getattr(evaluate, "target_repo", None)
    ref = source_ref if source_repo else (source_ref or getattr(evaluate, "target_ref", None))
    checkout_dir = None
    tree: SourceTree | None = None
    if repo:
        try:
            checkout_dir = source_checkout(repo, ref)
            tree = SourceTree(Path(checkout_dir.name))
            _emit(
                None,
                "planned",
                f"Checked out {repo}{'@' + ref if ref else ''} -- strategies see whole files "
                f"({len(tree.files)} Python files available)",
            )
        except Exception as exc:  # noqa: BLE001 -- excerpts still work
            _emit(None, "planned", f"Could not check out {repo} ({exc}); using excerpts only")

    try:
        with ThreadPoolExecutor(max_workers=n) as pool:
            outcomes = list(
                pool.map(
                    lambda strategy: _explore_and_evaluate(
                        branches[strategy.name],
                        strategy,
                        run_id,
                        explore=explore,
                        evaluate=evaluate,
                        embed=embed,
                        source=tree,
                        on_event=(
                            (
                                lambda stage, message, bid=branches[strategy.name]: _emit(
                                    bid, stage, message
                                )
                            )
                            if on_event
                            else None
                        ),
                    ),
                    strategies,
                )
            )
    finally:
        if checkout_dir is not None:
            checkout_dir.cleanup()

    selection = select_winner(outcomes)
    winner = selection.winner
    if selection.tie_detected:
        _emit(None, "merging", selection.rationale)
    _emit(None, "merging", f"Promoting {winner.strategy.name} into the parent")
    try:
        merge_id = merge(parent_id, winner.branch_id)
    except MergeConflict:
        # Someone else merged into this parent while this run was exploring
        # (the up-front check above is advisory, not a lock). Retire this
        # run's own branches so they don't sit in 'exploring' indefinitely and
        # inflate every "active" count, then let the caller report the error.
        run_branch_ids = [o.branch_id for o in outcomes]
        run_transaction(lambda cur: _abandon_branches(cur, run_branch_ids))
        for outcome in outcomes:
            _emit(
                outcome.branch_id,
                "abandoned",
                f"{outcome.strategy.name} abandoned -- parent already has a merged winner",
            )
        raise
    _emit(winner.branch_id, "merged", f"{winner.strategy.name} merged -- {selection.rationale}")
    for outcome in outcomes:
        if outcome.branch_id != winner.branch_id:
            _emit(
                outcome.branch_id,
                "abandoned",
                f"{outcome.strategy.name} abandoned -- scored "
                f"{outcome.evaluation.score:.1%} ({outcome.evaluation.reason})",
            )

    # After the merge, not before: a rationale for a merge that then failed
    # to commit would be a record of something that did not happen.
    _record_decision(
        merge_id=merge_id,
        run_id=run_id,
        parent_id=parent_id,
        outcomes=outcomes,
        selection=selection,
        similarity=similarity,
    )

    return MigrationResult(
        run_id=run_id,
        parent_id=parent_id,
        outcomes=outcomes,
        winner_branch_id=winner.branch_id,
        merge_id=merge_id,
    )


@dataclass(frozen=True, slots=True)
class ReplayResult:
    original_branch_id: str
    original_score: float | None
    replay_branch_id: str
    replay_run_id: str
    outcome: BranchOutcome


def replay_branch(
    branch_id: str,
    *,
    explore: Explorer,
    embed: Embedder,
    on_event: Callable[[str | None, str, str], None] | None = None,
) -> ReplayResult:
    """Re-run one branch's strategy from scratch, onto a new sibling branch,
    for comparison against the original -- never overwriting it.

    Pins what the manifest actually recorded: the same repository commit
    (``base_sha``, not a possibly-since-moved ref) and the same test
    selector, via :func:`bmem.evaluate.get_evaluator`. It does *not* force the
    same reasoning model -- ``explore`` is the caller's current live backend,
    same as :func:`run_migration`'s own ``explore`` parameter -- so a replay
    answers "does this still hold against the pinned code" rather than
    claiming a full bit-for-bit reproduction this project's Bedrock access
    can't guarantee anyway (model versions are not something callers pin).

    A fresh fork, not a re-run of ``branch_id`` in place: ``explore_branch``
    always UPSERTs under the same memory key for a given branch, so reusing
    the original id would silently overwrite the belief being compared
    against -- the one thing a replay must never do.
    """

    def _emit(bid: str | None, stage: str, message: str) -> None:
        if on_event:
            on_event(bid, stage, message)

    manifest = get_manifest(branch_id)
    if not manifest.target_repo:
        raise ValueError(
            f"branch {branch_id}'s manifest has no target repository to replay against"
        )

    with transaction() as cur:
        cur.execute(
            "SELECT parent_id, hypothesis FROM branches WHERE id = %(id)s", {"id": branch_id}
        )
        row = cur.fetchone()
    if row is None or row["parent_id"] is None:
        raise ValueError(f"branch {branch_id} has no parent to fork the replay from")

    strategy = Strategy(name=manifest.strategy_name, hypothesis=row["hypothesis"] or "")
    replay_run_id = f"replay-{uuid.uuid4().hex}"

    _emit(None, "planning", f"Replaying {manifest.strategy_name} from branch {branch_id[:8]}")
    new_branch_id = run_transaction(lambda cur: fork(cur, row["parent_id"], strategy.hypothesis))
    _emit(new_branch_id, "forked", f"Replay fork for: {strategy.hypothesis}")

    evaluate = get_evaluator(
        target_repo=manifest.target_repo,
        target_ref=manifest.base_sha or manifest.target_ref or "",
        test_selector=manifest.test_selector or "",
    )
    outcome = _explore_and_evaluate(
        new_branch_id,
        strategy,
        replay_run_id,
        explore=explore,
        evaluate=evaluate,
        embed=embed,
        on_event=lambda stage, message: _emit(new_branch_id, stage, message),
    )

    with transaction() as cur:
        cur.execute("SELECT score FROM branches WHERE id = %(id)s", {"id": branch_id})
        original = cur.fetchone()

    return ReplayResult(
        original_branch_id=branch_id,
        original_score=original["score"] if original else None,
        replay_branch_id=new_branch_id,
        replay_run_id=replay_run_id,
        outcome=outcome,
    )
