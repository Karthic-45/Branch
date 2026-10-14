# Branchable Memory — 3 minute demo

Version control for what an agent believes. Four hypotheses explored in
parallel, each in its own isolated memory branch, scored against a real test
suite, and the winner merged — reversibly.

**Target repo:** [CTFd/CTFd](https://github.com/CTFd/CTFd), Apache-2.0. Measured
at the commit ingested: **346 Python files, 48,541 lines**, producing **1,512
embedded chunks** in the trunk branch. The migration task is scoped to the API
layer; the whole repository is ingested.

---

## Setup, before recording

The trunk has to be **populated** before any of this reads correctly. A fresh
`create_root` is an empty branch, and forks of an empty branch inherit nothing —
the tree renders, the merge works, and the demo shows nothing, because there are
no beliefs in it to isolate. Ingest first.

```bash
# 1. Clone the target repo (gitignored; ~45s)
mkdir -p .work && git clone --depth 1 https://github.com/CTFd/CTFd.git .work/CTFd

# 2. Create the trunk and ingest the repo into it (~4min from outside us-east-1)
BMEM_FAKE_EMBEDDINGS=1 .venv/bin/python -c "
from bmem.branch import create_root
from bmem.db import transaction
from bmem.embed import get_embedder
from bmem.ingest import ingest_repository
with transaction() as cur:
    root = create_root(cur, hypothesis='main')
print('root =', root)
print(ingest_repository('.work/CTFd', root, embed=get_embedder()))
"

# 3. The control plane (backend)
BMEM_FAKE_EMBEDDINGS=1 BMEM_FAKE_PLANNER=1 BMEM_FAKE_CODEBUILD=1 \
  .venv/bin/uvicorn bmem.api:app --app-dir src --port 8000

# 4. The UI (frontend), in a second terminal
cd web && npm install && npm run dev
```

Open <http://localhost:5173/?root=THE_ID>. Leave the merge log scrolled into
view. Drop the fake flags to run the same thing on live Bedrock and CodeBuild.

### Beat 2 needs real Titan embeddings — verified, not assumed

`FakeEmbedder` is deterministic but **not semantic**, by construction. It only
guarantees that identical text yields an identical vector. Against a 1,512-chunk
corpus a *paraphrased* query therefore ranks arbitrarily: asking "how is session
state stored" for a belief worded differently returns six unrelated CTFd chunks
and the belief does not appear at all — measured, not predicted.

That makes a "not found" result meaningless under fakes, which is the exact
assertion beat 2 rests on. Two options:

- **Record beat 2 with real embeddings** (`BMEM_FAKE_EMBEDDINGS` unset, Titan
  live). This is the one beat that genuinely requires Bedrock.
- **Or query with the belief's exact text**, which pins it at distance 0 so a
  miss can only mean lineage excluded it. Correct, but it looks like a keyword
  search on camera and undersells the semantics.

Beats 1 and 3 — forking, scoring, merge, time travel, revert — are unaffected
and fully real under fakes.

---

## Beat 1 — four branches, in parallel (0:00–1:10)

On the **Overview** tab: paste the trunk id, set the goal to *"migrate the
CTFd API layer off Flask to FastAPI"*, hit **Start migration** — then switch
to the **Branch tree** tab immediately. The Activity rail on the right is
visible from every tab, so it keeps narrating without needing to switch back.

> "The agent doesn't pick a strategy. It forks four, and explores all of them at
> once — each in its own branch of memory."

The tree fans out to four violet `exploring` nodes. Point at the **Activity**
panel on the right — it's narrating each branch's real progress line by line
(forked → exploring → evaluating → CodeBuild's actual build phases → scored),
and each branch card's five-step tracker is following along. Say what a branch
*is* while they run:

> "A branch isn't a copy. Nothing is duplicated. Each branch sees its own
> conclusions, plus everything its parent knew **frozen at the instant it
> forked** — enforced by a hybrid logical clock watermark on every row, and one
> vector-indexed query. So a branch can be wrong in private. And nothing here
> is a black box — every stage each branch passes through is on screen as it
> happens, not just a final score."

Scores land; three go grey, one goes green.

## Beat 2 — isolation, asked in natural language (1:10–2:00)

Switch to Claude with the `bmem-audit` MCP server connected. Ask:

> **"Which hypothesis did the shim branch abandon, and what beat it?"**

`explain_branch` returns the shim's hypothesis, its score, and the sibling that
won. Then:

> **"Search the shim branch's memory for how routes are handled."**

`search_branch_memory` returns the shim's own conclusion and the trunk facts it
inherited — each tagged `inherited: true/false` — and **nothing from the branch
that won**. Verified against the populated trunk: the querying branch sees its
own belief at rank 1 plus five inherited CTFd chunks; its sibling, on the
identical query, sees six inherited chunks and no trace of the belief. That
absence is the whole claim:

> "The winning branch concluded something completely different, and the shim
> branch cannot see it. Sibling beliefs never leak. That's the test in
> `test_isolation.py`, and it's the reason four wrong answers can be explored
> at once without poisoning each other."

Note the surface is read-only: five tools, none of which can write.

## Beat 3 — undo a conclusion (2:00–2:45)

Back to the browser, on the **Merge log** tab. It shows one entry, *in effect*.

> **"Ask what it believed before the merge."**

`beliefs_before_merge` — run it **both ways**:

- `via="aost"` — a CockroachDB `AS OF SYSTEM TIME` query against the recorded
  hybrid logical clock. The same table, read as it was at a past instant.
- `via="snapshot"` — the state materialized at merge time.

They agree. Then hit **Revert**.

> "The merge is undone: the trunk is back to exactly what it believed before,
> re-embedded and queryable. The log entry stays — marked reverted, not
> deleted. The agent's history of being wrong is preserved."

The tree repaints. The green node goes grey.

## Close (2:45–3:00)

> "Agent memory today is append-only. A wrong conclusion is permanent, and it
> contaminates everything downstream. This gives beliefs the thing source code
> has had for forty years: branches, isolation, evidence, merges, and an undo
> button — on CockroachDB, with Bedrock doing the reasoning."

---

## Notes for the recording

- **Latency:** run the demo from us-east-1, or pre-warm. From a distant machine
  a merge spans enough round trips to look sluggish on camera.
- **Have a fallback tree already built** in a second browser tab, in case a live
  run stalls.
- Say **"branch"**, not "version" — the git analogy is doing the explaining.
- Don't narrate the schema. Show the absence in beat 2; that's the proof.
