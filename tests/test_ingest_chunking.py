"""Chunking a source tree into memory rows.

Pure -- no cluster, no AWS. The interesting behavior lives in what a chunk's
boundaries are, not in any I/O around it.
"""

from __future__ import annotations

from bmem.ingest import chunk_python_source


def test_a_top_level_function_becomes_one_chunk() -> None:
    source = "def add(a, b):\n    return a + b\n"
    chunks = chunk_python_source("math.py", source)
    assert [c.key for c in chunks] == ["math.py::add"]
    assert "def add(a, b):" in chunks[0].content
    assert "return a + b" in chunks[0].content


def test_multiple_top_level_defs_become_separate_chunks_in_order() -> None:
    source = "def first():\n    pass\n\n\ndef second():\n    pass\n"
    chunks = chunk_python_source("m.py", source)
    assert [c.key for c in chunks] == ["m.py::first", "m.py::second"]


def test_a_class_is_one_chunk_including_all_its_methods() -> None:
    source = (
        "class Guard:\n"
        "    def check(self):\n"
        "        return True\n"
        "\n"
        "    def teardown(self):\n"
        "        return None\n"
    )
    chunks = chunk_python_source("auth/guard.py", source)
    assert [c.key for c in chunks] == ["auth/guard.py::Guard"]
    assert "def check(self):" in chunks[0].content
    assert "def teardown(self):" in chunks[0].content


def test_nested_functions_are_not_split_into_their_own_chunks() -> None:
    source = "def outer():\n    def inner():\n        return 1\n    return inner()\n"
    chunks = chunk_python_source("m.py", source)
    assert [c.key for c in chunks] == ["m.py::outer"]
    assert "def inner():" in chunks[0].content  # travels with its parent


def test_decorators_are_captured_with_the_node_they_decorate() -> None:
    # A Flask-RESTX resource declares its route only in the decorator -- the
    # class body never repeats it. Dropping the decorator would discard the
    # one fact that says what the class is for.
    source = (
        '@api.route("/challenges")\n'
        "class ChallengeList(Resource):\n"
        "    def get(self):\n"
        "        pass\n"
    )
    chunks = chunk_python_source("api/v1/challenges.py", source)
    assert len(chunks) == 1
    assert '@api.route("/challenges")' in chunks[0].content
    assert "class ChallengeList(Resource):" in chunks[0].content


def test_multiple_decorators_are_all_captured() -> None:
    source = "@app.before_request\n@log_call\ndef guard():\n    pass\n"
    chunks = chunk_python_source("m.py", source)
    assert "@app.before_request" in chunks[0].content
    assert "@log_call" in chunks[0].content


def test_content_is_prefixed_with_its_source_path() -> None:
    chunks = chunk_python_source("a/b/c.py", "def f():\n    pass\n")
    assert chunks[0].content.startswith("# a/b/c.py\n")


def test_module_level_statements_outside_any_def_or_class_produce_no_chunk() -> None:
    source = "import os\n\nVERSION = '1.0'\n\nprint('loaded')\n"
    assert chunk_python_source("m.py", source) == []


def test_a_file_with_only_imports_produces_no_chunks() -> None:
    assert chunk_python_source("__init__.py", "from .a import b\nfrom .c import d\n") == []


def test_invalid_syntax_yields_no_chunks_rather_than_raising() -> None:
    assert chunk_python_source("broken.py", "def f(:\n    pass\n") == []


def test_empty_file_yields_no_chunks() -> None:
    assert chunk_python_source("empty.py", "") == []


def test_async_function_is_chunked_like_a_regular_one() -> None:
    source = "async def fetch():\n    return await other()\n"
    chunks = chunk_python_source("m.py", source)
    assert [c.key for c in chunks] == ["m.py::fetch"]
