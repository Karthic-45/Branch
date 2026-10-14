"""The MCP surface's defining property: it can read everything and change nothing.

These run offline. They inspect the tool registry rather than the cluster,
because what is being asserted is the *shape* of the surface -- which tools
exist, and that none of them writes. `tests/test_mcp_tools.py` exercises the
answers themselves against real data.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap

import pytest

from bmem import mcp_server

# Everything the audit surface is meant to answer. Written out rather than
# derived from the registry so that deleting a tool fails a test instead of
# quietly shrinking the expected set.
EXPECTED_TOOLS = {
    "list_branches",
    "explain_branch",
    "search_branch_memory",
    "list_merges",
    "beliefs_before_merge",
    "explain_decision",
    "get_project_timeline",
    "get_reproducibility_manifest",
    "get_known_failures",
}

# Anything in bmem.branch that changes state. A tool must never wrap one.
MUTATING_OPERATIONS = ("fork", "merge", "revert", "create_root")


async def _tools() -> dict[str, object]:
    return {tool.name: tool for tool in await mcp_server.mcp.list_tools()}


@pytest.mark.asyncio
async def test_exposes_exactly_the_audit_tools() -> None:
    assert set(await _tools()) == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_every_tool_is_annotated_read_only() -> None:
    """The annotation is the machine-readable promise, so it has to be on all of them.

    A client that filters on ``read_only_hint`` to decide what an untrusted
    model may call gets the wrong answer if even one tool omits it.
    """
    for name, tool in (await _tools()).items():
        annotations = tool.annotations
        assert annotations is not None, f"{name} has no annotations"
        assert annotations.read_only_hint is True, f"{name} is not marked read-only"
        assert annotations.destructive_hint is False, f"{name} is not marked non-destructive"


def _called_names(function: object) -> set[str]:
    """Every name this function actually calls.

    Parsed rather than grepped: ``beliefs_before_merge`` contains the substring
    ``merge(``, and a naive text search reports it as calling merge. Only the
    ``Call`` nodes count.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


@pytest.mark.asyncio
async def test_no_tool_reaches_a_mutating_operation() -> None:
    """The stronger form of the same claim: read-only in the code, not just the label.

    An annotation is a promise about behaviour; this checks the behaviour. If
    someone adds a revert button to the audit surface, the label might still say
    read-only, but the call it dispatches to will not be in this set.
    """
    for name in await _tools():
        called = _called_names(getattr(mcp_server, name))
        offenders = called & set(MUTATING_OPERATIONS)
        assert not offenders, f"{name} calls {', '.join(sorted(offenders))}()"


@pytest.mark.asyncio
async def test_no_tool_issues_a_writing_statement() -> None:
    """No tool may contain SQL that writes, whatever it calls to get there.

    Matched on word boundaries, for the same reason ``_called_names`` parses
    instead of grepping: a plain substring search reports "alternatives" as
    an ALTER and "inserted" as an INSERT, and a check that cries wolf on
    ordinary English gets weakened or deleted the first time it does.
    """
    writes = ("INSERT", "UPDATE", "DELETE", "UPSERT", "DROP", "TRUNCATE", "ALTER")
    pattern = re.compile(rf"\b({'|'.join(writes)})\b")
    for name in await _tools():
        source = inspect.getsource(getattr(mcp_server, name)).upper()
        found = pattern.findall(source)
        assert not found, f"{name} contains a {found[0]}"


@pytest.mark.asyncio
async def test_every_tool_documents_itself_for_the_model() -> None:
    """Descriptions are load-bearing here -- they are how the model picks a tool."""
    for name, tool in (await _tools()).items():
        assert tool.description, f"{name} has no description"
        assert len(tool.description) > 40, f"{name}'s description is too thin to choose by"


def test_server_advertises_instructions() -> None:
    """Without these a client sees five tool names and no notion of branches."""
    assert mcp_server.mcp.instructions
    assert "branch" in mcp_server.mcp.instructions.lower()
