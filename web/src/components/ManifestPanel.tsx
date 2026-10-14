import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Loader2, Repeat, FlaskConical } from "lucide-react";
import type { ExecutionManifest, ReplayResponse } from "../lib/types";
import { fetchManifest, replayBranch } from "../lib/api";

/** The reproducibility record for one branch: what it retrieved, which model
 * answered, what it was scored against -- plus the button to run it again
 * against the same pinned commit. A branch explored before manifests existed
 * 404s, and this says so rather than rendering an empty shell. */
export function ManifestPanel({ branchId }: { branchId: string }) {
  const [manifest, setManifest] = useState<ExecutionManifest | null>(null);
  const [error, setError] = useState("");
  const [replaying, setReplaying] = useState(false);
  const [replay, setReplay] = useState<ReplayResponse | null>(null);
  const [replayError, setReplayError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setManifest(null);
    setError("");
    fetchManifest(branchId)
      .then((m) => { if (!cancelled) setManifest(m); })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
    return () => { cancelled = true; };
  }, [branchId]);

  async function handleReplay() {
    setReplaying(true);
    setReplayError("");
    try {
      setReplay(await replayBranch(branchId));
    } catch (err) {
      setReplayError(err instanceof Error ? err.message : String(err));
    } finally {
      setReplaying(false);
    }
  }

  if (error) return <p className="p-4 font-mono text-[11.5px] text-muted">{error}</p>;
  if (!manifest) {
    return (
      <div className="flex items-center gap-2 p-4 text-xs text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading manifest…
      </div>
    );
  }

  return (
    <div className="p-4">
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div>
          <Label>Execution manifest</Label>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-[11.5px]">
            <Row k="run" v={manifest.run_id} />
            <Row k="strategy" v={manifest.strategy_name} />
            <Row k="reasoning" v={manifest.reasoning_model_id} />
            <Row k="prompt" v={manifest.explore_prompt_version} />
            <Row k="embedding" v={manifest.embedding_model_id} />
            <Row k="repo" v={manifest.target_repo} />
            <Row k="base sha" v={manifest.base_sha?.slice(0, 12) ?? null} />
            <Row k="tests" v={manifest.test_selector || (manifest.target_repo ? "full suite" : null)} />
            <Row k="codebuild" v={manifest.codebuild_project} />
          </dl>
        </div>

        <div>
          <Label>Memory retrieved ({manifest.retrieved_memories.length})</Label>
          {manifest.retrieved_memories.length === 0 ? (
            <p className="text-[12px] text-muted">
              Nothing was retrievable from this branch's lineage at explore time.
            </p>
          ) : (
            <ul className="space-y-1">
              {manifest.retrieved_memories.map((m) => (
                <li key={m.key} className="flex items-baseline justify-between gap-2 font-mono text-[11px]">
                  <span className="truncate text-ink-2">{m.key}</span>
                  <span className="whitespace-nowrap text-muted">
                    d={m.distance.toFixed(3)} {m.inherited ? "· inherited" : "· own"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <motion.button
          whileHover={{ y: -1 }}
          whileTap={{ scale: 0.97 }}
          disabled={replaying || !manifest.target_repo}
          onClick={handleReplay}
          title={manifest.target_repo ? undefined : "No target repository recorded to replay against"}
          className="flex items-center gap-1.5 rounded-lg border border-hairline-strong px-3 py-1.5 text-xs text-ink-2 transition-colors hover:border-cyan hover:text-cyan disabled:opacity-50"
        >
          {replaying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Repeat className="h-3.5 w-3.5" />}
          {replaying ? "Replaying…" : "Replay evaluation"}
        </motion.button>
        <span className="text-[11px] text-muted">
          Forks a new sibling and re-runs against the same pinned commit. The original is never modified.
        </span>
      </div>

      {replayError && (
        <div className="mt-3 rounded-lg border border-rose/40 bg-rose/10 px-3 py-2 font-mono text-[11px] text-rose">
          {replayError}
        </div>
      )}

      {replay && (
        <div className="mt-3 rounded-lg border border-cyan/40 bg-cyan/[0.07] px-3 py-2.5">
          <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold text-cyan">
            <FlaskConical className="h-3.5 w-3.5" /> Replay complete
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-[11.5px]">
            <Row k="original" v={fmtPct(replay.original_score)} />
            <Row k="replay" v={`${fmtPct(replay.replay_score)} — ${replay.replay_reason}`} />
            <Row
              k="delta"
              v={replay.score_delta === null ? null : `${replay.score_delta >= 0 ? "+" : ""}${(replay.score_delta * 100).toFixed(1)} pts`}
            />
            <Row k="new branch" v={replay.replay_branch_id.slice(0, 8)} />
          </dl>
        </div>
      )}
    </div>
  );
}

function fmtPct(score: number | null): string | null {
  return score === null ? null : `${Math.round(score * 100)}%`;
}

function Row({ k, v }: { k: string; v: string | null }) {
  return (
    <>
      <dt className="text-muted">{k}</dt>
      <dd className={v ? "break-all text-ink-2" : "text-muted italic"}>{v ?? "not recorded"}</dd>
    </>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="mb-1.5 font-mono text-[10.5px] font-semibold uppercase tracking-wider text-muted">
      {children}
    </h4>
  );
}
