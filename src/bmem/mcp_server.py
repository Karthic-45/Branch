"""A read-only MCP surface over the branch store: the audit story, queryable.

Every tool here reads. None of them fork, merge, revert, or write a single row.
That is the point rather than an oversight -- an audit surface that can also
mutate the thing it audits is not an audit surface, and "the model can inspect
the whole belief history but cannot alter it" is a property worth being able to
state plainly. The write path stays in ``bmem.api``, behind HTTP, where the
demo drives it deliberately.

This is deliberately *not* a SQL passthrough. The CockroachDB Managed MCP Server
configured in ``.mcp.json`` already answers "run this query"; it cannot answer
"which hypothesis did the shim branch abandon, and what did it score" without
the caller knowing the schema, the ancestry CTE, and the horizon rules. These
tools answer the domain question directly, which is what makes the natural
language beat of the demo work.

Run it::

    BMEM_FAKE_EMBEDDINGS=1 python -m bmem.mcp_server

or register it with Claude Code::

    claude mcp add bmem-audit -- /path/to/.venv/bin/python -m bmem.mcp_server
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from bmem.branch import reconstruct_pre_merge_state, resolve_subtree
from bmem.db import transaction
from bmem.decision import decision_graph, get_counterfactual, historical_failures
from bmem.embed import get_embedder
from bmem.manifest import get_manifest
from bmem.retrieval import search

# read_only_hint is the machine-readable half of this module's docstring: a
# client that wants to refuse mutating tools outright can see that none are
# offered here. open_world_hint=False because every answer comes from one
# cluster, not from the internet.
_AUDIT = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

mcp = MCPServer(
    name="bmem-audit",
    instructions=(
        "Read-only audit access to a Branchable Memory store, where an agent's "
        "beliefs live on branches that fork, compete, merge, and revert. Use "
        "list_branches to see a run's tree, explain_branch to find out why a "
        "branch won or was abandoned, search_branch_memory to ask what a "
        "particular branch believed (and what it inherited rather than "
        "concluded), and list_merges plus beliefs_before_merge to inspect and "
        "time-travel the audit trail. For the decision record specifically: "
        "explain_decision says why one strategy beat the others and whether a "
        "tie-breaker rather than the test suite chose it, get_project_timeline "
        "walks every decision in order, get_reproducibility_manifest shows what "
        "a branch's attempt actually ran against, and get_known_failures lists "
        "approaches already tried here and measured as losing."
    ),
)


@mcp.tool(annotations=_AUDIT)
def list_branches(root_id: str) -> list[dict[str, Any]]:
    """List every branch descending from a root, with status and score.

    Args:
        root_id: UUID of the branch to treat as the root of the tree.
    """
    with transaction() as cur:
        nodes = resolve_subtree(cur, root_id)
    return [
        {
            "branch_id": n.id,
            "parent_id": n.parent_id,
            "hypothesis": n.hypothesis,
            "status": n.status,
            "score": n.score,
            "depth": n.depth,
        }
        for n in nodes
    ]


@mcp.tool(annotations=_AUDIT)
def explain_branch(branch_id: str) -> dict[str, Any]:
    """Explain a branch's fate: its hypothesis, its score, and how its rivals did.

    Answers questions of the form "which hypothesis did this branch abandon, and
    what beat it" -- the sibling comparison is the part that makes an abandoned
    branch intelligible, so it is returned alongside rather than left to a
    follow-up call.

    Args:
        branch_id: UUID of the branch to explain.
    """
    with transaction() as cur:
        cur.execute(
            """
            SELECT id, parent_id, hypothesis, status, score, created_at
            FROM branches WHERE id = %(id)s
            """,
            {"id": branch_id},
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"no such branch: {branch_id}")

        siblings: list[dict[str, Any]] = []
        if row["parent_id"] is not None:
            cur.execute(
                """
                SELECT id, hypothesis, status, score FROM branches
                WHERE parent_id = %(p)s AND id != %(id)s
                ORDER BY score DESC NULLS LAST
                """,
                {"p": row["parent_id"], "id": branch_id},
            )
            siblings = [
                {
                    "branch_id": str(r["id"]),
                    "hypothesis": r["hypothesis"],
                    "status": r["status"],
                    "score": r["score"],
                }
                for r in cur.fetchall()
            ]

    return {
        "branch_id": str(row["id"]),
        "parent_id": str(row["parent_id"]) if row["parent_id"] else None,
        "hypothesis": row["hypothesis"],
        "status": row["status"],
        "score": row["score"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "competed_against": siblings,
    }


@mcp.tool(annotations=_AUDIT)
def search_branch_memory(branch_id: str, query: str, k: int = 8) -> list[dict[str, Any]]:
    """Search what one branch believes, in its own isolated view of memory.

    Each result says which branch it actually came from and whether it was
    inherited from an ancestor or concluded by this branch itself. That
    distinction is the whole isolation claim made visible: a sibling's
    conclusions are absent, and an ancestor's are frozen as of the fork.

    Args:
        branch_id: UUID of the branch whose belief state to search.
        query: Natural language query, embedded and matched by vector distance.
        k: Maximum number of results.
    """
    vector = get_embedder()([query])[0]
    with transaction() as cur:
        hits = search(cur, branch_id, vector, k=k)
    return [
        {
            "key": h.key,
            "content": h.content,
            "distance": h.distance,
            "source_branch_id": h.source_branch_id,
            "inherited": h.inherited,
        }
        for h in hits
    ]


@mcp.tool(annotations=_AUDIT)
def list_merges(parent_id: str) -> list[dict[str, Any]]:
    """List every merge into a branch, newest first, including undone ones.

    The log is append-only: a reverted merge stays in it with ``reverted_at``
    set, so the history of what the agent concluded and then un-concluded
    remains inspectable.

    Args:
        parent_id: UUID of the branch that was merged into.
    """
    with transaction() as cur:
        cur.execute(
            """
            SELECT id, winner_id, pre_merge_hlc, merged_at, reverted_at,
                   jsonb_array_length(snapshot) AS snapshot_rows
            FROM merge_log WHERE parent_id = %(p)s ORDER BY merged_at DESC
            """,
            {"p": parent_id},
        )
        rows = cur.fetchall()

    return [
        {
            "merge_id": str(r["id"]),
            "winner_id": str(r["winner_id"]),
            "pre_merge_hlc": str(r["pre_merge_hlc"]),
            "merged_at": r["merged_at"].isoformat() if r["merged_at"] else None,
            "reverted_at": r["reverted_at"].isoformat() if r["reverted_at"] else None,
            "snapshot_rows": r["snapshot_rows"],
        }
        for r in rows
    ]


@mcp.tool(annotations=_AUDIT)
def beliefs_before_merge(merge_id: str, via: str = "snapshot") -> dict[str, Any]:
    """Reconstruct what the parent believed immediately before a merge landed.

    Two independent paths to the same answer. ``snapshot`` reads the state
    materialized at merge time and always works. ``aost`` runs a CockroachDB
    ``AS OF SYSTEM TIME`` query against the recorded hybrid logical clock --
    the same table, read as it existed at a past instant -- which is the more
    striking demonstration but only reaches back as far as the garbage
    collection window.

    Args:
        merge_id: UUID from the merge log.
        via: Either "snapshot" (durable) or "aost" (time travel).
    """
    rows = reconstruct_pre_merge_state(merge_id, via=via)
    return {
        "merge_id": merge_id,
        "via": via,
        "beliefs": [{"key": r.key, "content": r.content, "tombstone": r.tombstone} for r in rows],
    }


@mcp.tool(annotations=_AUDIT)
def explain_decision(merge_id: str) -> dict[str, Any]:
    """Why this strategy won and the others did not, from recorded evidence.

    Reads back the rationale written when the decision was actually made --
    including whether the scores were close enough that a tie-breaker, not
    the test suite, picked the winner. A merge that predates rationale
    tracking says so rather than having an explanation reconstructed for it.

    Args:
        merge_id: UUID from the merge log.
    """
    cf = get_counterfactual(merge_id)
    return {
        "merge_id": cf.merge_id,
        "rationale": cf.rationale,
        "tie_detected": cf.tie_detected,
        "discriminator": cf.discriminator,
        "margin": cf.margin,
        "winner": cf.winner.model_dump() if cf.winner else None,
        "rejected": [a.model_dump() for a in cf.alternatives],
        "strategy_similarity": cf.strategy_similarity,
    }


@mcp.tool(annotations=_AUDIT)
def get_project_timeline(root_id: str) -> list[dict[str, Any]]:
    """Every decision made in this root's subtree, oldest first.

    Each entry is one complete choice: the problem, the strategies that
    competed, which won, and whether it reached a real commit.

    Args:
        root_id: UUID of the root branch.
    """
    return [node.model_dump() for node in decision_graph(root_id)]


@mcp.tool(annotations=_AUDIT)
def get_reproducibility_manifest(branch_id: str) -> dict[str, Any]:
    """What one branch's attempt actually ran against.

    Which memories it retrieved (with retrieval distances and whether each
    was inherited), which model answered, and which commit and test selector
    scored it. Fields the run never recorded come back null rather than
    guessed -- a branch explored with a stand-in backend genuinely has no
    model name to report.

    Args:
        branch_id: UUID of the branch.
    """
    return get_manifest(branch_id).model_dump()


@mcp.tool(annotations=_AUDIT)
def get_known_failures(parent_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Strategies already tried against this branch and lost, with their scores.

    The same failure memory the planner is given before proposing new
    strategies, so an agent can check whether an approach has already been
    measured here before suggesting it again.

    Args:
        parent_id: UUID of the branch strategies were evaluated against.
        limit: Maximum number of past failures to return.
    """
    return [f.model_dump() for f in historical_failures(parent_id, limit=limit)]


def main() -> None:
    """Serve over stdio, the transport MCP clients launch a local server with."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
