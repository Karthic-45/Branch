import { useState } from "react";
import { motion } from "framer-motion";
import { ChevronLeft, ChevronRight, RotateCcw, ExternalLink } from "lucide-react";
import type { Section } from "../App";

interface Scene {
  n: string;
  title: string;
  claim: string;
  detail: string;
  /** Where in the app this scene is demonstrated, if anywhere. */
  goto?: { section: Section; label: string };
}

const SCENES: Scene[] = [
  {
    n: "01",
    title: "The problem",
    claim: "Agent memory is append-only, so wrong beliefs never actually go away.",
    detail:
      "When an agent reaches a bad conclusion it does not delete it — it appends a correction and hopes the model attends to the newer text. The discredited note stays semantically relevant, so retrieval keeps surfacing it, and a wrong belief formed in step 4 quietly poisons steps 5 through 40.",
  },
  {
    n: "02",
    title: "Memory that forks",
    claim: "A branch is one row: a parent pointer and a timestamp. No memory is copied.",
    detail:
      "Forking fifty hypotheses costs fifty small inserts, whatever the parent holds. Run the copy-on-write proof to watch a 200-memory parent get forked five times and add exactly zero memory rows.",
    goto: { section: "proofs", label: "Run the copy-on-write proof" },
  },
  {
    n: "03",
    title: "Isolation is real",
    claim: "Branch B cannot retrieve Branch A's belief. Same database, same index, same query.",
    detail:
      "The vector index is declared (branch_id, embedding) — branch_id is a prefix column, which is what lets CockroachDB scope an approximate-nearest-neighbour search to one lineage at the index level rather than post-filtering the corpus. This is the part no external vector store reproduces.",
    goto: { section: "proofs", label: "Run the isolation proof" },
  },
  {
    n: "04",
    title: "Competing hypotheses",
    claim: "The trunk forks into N branches, each exploring a different strategy in parallel.",
    detail:
      "Each branch searches only its own lineage, reasons about its assigned strategy, and produces a real unified diff. Four workers reason at once and none can see another's conclusions.",
    goto: { section: "branches", label: "Open the branch tree" },
  },
  {
    n: "05",
    title: "Real evaluation",
    claim: "CI grades the agent, not the other way round.",
    detail:
      "Every candidate patch is applied to a clean checkout inside a throwaway CodeBuild container — no database credentials, no Bedrock access, write access to exactly one S3 prefix — and the repository's own test suite decides that branch's score.",
    goto: { section: "overview", label: "Watch the execution trace" },
  },
  {
    n: "06",
    title: "The decision",
    claim: "The winner merges in one serializable transaction. Losers are kept, not deleted.",
    detail:
      "Promoting a winner and retiring its siblings happen together or not at all — there is no window in which two winners exist. When scores are within one point the system says so and records which tie-breaker decided it, rather than presenting a coin flip as evidence.",
    goto: { section: "decisions", label: "Open the decision graph" },
  },
  {
    n: "07",
    title: "Why, not just what",
    claim: "Every decision carries the evidence that produced it.",
    detail:
      "Belief, retrieved memories with real retrieval distances, competing strategies, test results, patch statistics, and the recorded rationale. Nothing is regenerated after the fact — the explanation is read back from what was written at decision time, so it cannot drift.",
    goto: { section: "decisions", label: "Open the decision log" },
  },
  {
    n: "08",
    title: "Time travel",
    claim: "The parent's earlier belief state is still readable after a merge.",
    detail:
      "Every merge records both the pre-merge hybrid logical clock and a materialized snapshot. The snapshot works forever; the AS OF SYSTEM TIME path is the more striking demonstration but only reaches back as far as the garbage-collection window, which this cluster raises to 24 hours.",
    goto: { section: "proofs", label: "Run the temporal proof" },
  },
  {
    n: "09",
    title: "Revert",
    claim: "A promoted conclusion can be withdrawn, exactly.",
    detail:
      "Reverting restores the parent's precise pre-merge state and leaves the log entry in place marked reverted. The record that a conclusion was reached and later undone survives — merge history is never deleted.",
    goto: { section: "decisions", label: "Open the decision log" },
  },
  {
    n: "10",
    title: "From decision to code",
    claim: "The winning patch applies to a real checkout, validated, on its own branch.",
    detail:
      "The apply engine clones at the exact commit the patch was scored against, refuses to proceed if the repository has moved since, applies to an isolated branch, runs the repository's configured validation profile, and commits locally. It never pushes and never opens a PR — this deployment has no write credential, and pretending otherwise would be the one thing this project refuses.",
    goto: { section: "decisions", label: "Open a decision to apply its patch" },
  },
  {
    n: "11",
    title: "Reproducible",
    claim: "Every branch records what it actually ran against.",
    detail:
      "Repository commit, model, prompt version, embedding model, test selector, and the exact memories retrieved. A replay re-runs a strategy against that pinned commit on a fresh sibling, leaving the original untouched. Fields a stand-in backend could not supply are recorded as absent, never guessed.",
    goto: { section: "decisions", label: "Open a decision\u2019s reproducibility tab" },
  },
  {
    n: "12",
    title: "The audit trail",
    claim: "Ask the store why, in natural language — read-only, by construction.",
    detail:
      "Nine MCP tools expose the whole belief history to a model. None of them can fork, merge, revert or write a row, and a test fails the build if any tool's source ever contains a writing statement. An audit surface that can mutate what it audits is not an audit surface.",
    goto: { section: "observability", label: "See the security report" },
  },
  {
    n: "13",
    title: "The record",
    claim: "Export the whole decision history as a professional report.",
    detail:
      "Decision, executive and engineering PDFs plus a complete ZIP archive, each carrying a SHA-256 of its own contents. Every value is read from recorded data; anything never measured appears as 'not recorded' rather than being estimated into existence.",
    goto: { section: "reports", label: "Download the reports" },
  },
];

/** A guided walkthrough for someone seeing this for the first time. Each
 * scene states a claim and points at where in the app it can be checked --
 * the point is that nothing here has to be taken on faith. */
export function JudgeMode({ onNavigate }: { onNavigate: (s: Section) => void }) {
  const [i, setI] = useState(0);
  const scene = SCENES[i];
  const atEnd = i === SCENES.length - 1;

  return (
    <div className="w-full space-y-4">
      <div className="mb-4 flex items-center gap-1.5">
        {SCENES.map((s, idx) => (
          <button
            key={s.n}
            onClick={() => setI(idx)}
            aria-label={`Scene ${s.n}: ${s.title}`}
            className={`h-1.5 flex-1 rounded-full transition-colors ${
              idx === i ? "bg-violet" : idx < i ? "bg-violet/40" : "bg-hairline-strong"
            }`}
          />
        ))}
      </div>

      {/* Keyed motion.div without AnimatePresence: React remounts on key
          change, so the entrance animation replays on every scene. Wrapping
          this in AnimatePresence mode="wait" instead made the panel freeze on
          scene 1 -- the state advanced correctly but the exiting child never
          finished exiting, so the incoming one never mounted. A transition
          that can hang is worse than no exit animation. */}
      <div>
        <motion.div
          key={scene.n}
          initial={{ opacity: 0, x: 12 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.18 }}
          className="card rounded-2xl p-7"
        >
          <div className="mb-1 font-mono text-[11px] font-semibold tracking-wider text-violet">
            SCENE {scene.n} / {SCENES.length}
          </div>
          <h2 className="mb-3 text-[26px] font-semibold leading-tight text-ink">{scene.title}</h2>
          <p className="mb-4 border-l-2 border-violet pl-3 text-[15px] font-medium leading-relaxed text-ink">
            {scene.claim}
          </p>
          <p className="text-[13.5px] leading-relaxed text-ink-2">{scene.detail}</p>

          {scene.goto && (
            <button
              onClick={() => onNavigate(scene.goto!.section)}
              className="mt-5 flex items-center gap-1.5 rounded-lg border border-violet/50 bg-violet/10 px-3.5 py-2 text-[12.5px] font-medium text-violet transition-colors hover:bg-violet/20"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              {scene.goto.label}
            </button>
          )}
        </motion.div>
      </div>

      <div className="mt-4 flex items-center justify-between">
        <button
          onClick={() => setI((n) => Math.max(0, n - 1))}
          disabled={i === 0}
          className="flex items-center gap-1.5 rounded-lg border border-hairline-strong px-3.5 py-2 text-[12.5px] text-ink-2 transition-colors hover:text-ink disabled:opacity-40"
        >
          <ChevronLeft className="h-4 w-4" /> Back
        </button>

        <span className="font-mono text-[11.5px] text-muted">
          {scene.title}
        </span>

        {atEnd ? (
          <button
            onClick={() => setI(0)}
            className="flex items-center gap-1.5 rounded-lg border border-hairline-strong px-3.5 py-2 text-[12.5px] text-ink-2 transition-colors hover:text-ink"
          >
            <RotateCcw className="h-4 w-4" /> Start over
          </button>
        ) : (
          <button
            onClick={() => setI((n) => Math.min(SCENES.length - 1, n + 1))}
            className="flex items-center gap-1.5 rounded-lg bg-violet px-3.5 py-2 text-[12.5px] font-semibold text-white transition-colors hover:bg-violet/90"
          >
            Next <ChevronRight className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}
