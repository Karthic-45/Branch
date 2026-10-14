import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Loader2, Trophy, XCircle, GitCommitHorizontal, Scale, RotateCcw } from "lucide-react";
import type { DecisionNode, StrategyComparison } from "../lib/types";
import { fetchDecisionGraph } from "../lib/api";

/** The sequence of choices that produced the tree, rather than the tree
 * itself: each problem, the strategies that competed, which won, and whether
 * it reached a real commit. Successive migrations on one lineage chain
 * downward -- each decision starts from the state the last one left. */
export function DecisionGraph({ rootId, refreshKey }: { rootId: string | null; refreshKey?: unknown }) {
  const [nodes, setNodes] = useState<DecisionNode[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!rootId) { setNodes([]); return; }
    let cancelled = false;
    fetchDecisionGraph(rootId)
      .then((n) => { if (!cancelled) setNodes(n); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [rootId, refreshKey]);

  if (error) return <p className="font-mono text-xs text-rose">{error}</p>;
  if (!nodes) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading decisions…
      </div>
    );
  }
  if (!nodes.length) {
    return (
      <div className="card rounded-xl p-6 text-center">
        <p className="text-[13px] text-ink-2">No decisions recorded yet.</p>
        <p className="mt-1 text-[12px] text-muted">
          Run a migration — each completed run adds a decision here.
        </p>
      </div>
    );
  }

  return (
    <ol className="relative space-y-4 pl-6">
      {/* The spine: successive decisions on one lineage, oldest at the top. */}
      <span className="absolute left-[7px] top-2 bottom-2 w-px bg-hairline-strong" aria-hidden />
      {nodes.map((node, i) => (
        <motion.li
          key={node.merge_id}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.05, duration: 0.2 }}
          className="relative"
        >
          <span
            className={`absolute -left-6 top-3 h-3.5 w-3.5 rounded-full border-2 ${
              node.reverted_at
                ? "border-muted bg-plane"
                : "border-emerald bg-emerald/30"
            }`}
            aria-hidden
          />
          <DecisionCard node={node} />
        </motion.li>
      ))}
    </ol>
  );
}

function DecisionCard({ node }: { node: DecisionNode }) {
  const undone = Boolean(node.reverted_at);
  return (
    <div className="card rounded-xl p-4">
      <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <Trophy className={`h-3.5 w-3.5 ${undone ? "text-muted" : "text-emerald"}`} />
            <span className={`text-[13.5px] font-semibold ${undone ? "text-muted line-through" : "text-ink"}`}>
              {node.winner_hypothesis ?? node.winner_branch_id.slice(0, 8)}
            </span>
          </div>
          <div className="mt-0.5 font-mono text-[11px] text-muted">
            {node.merged_at ? new Date(node.merged_at).toLocaleString() : "—"}
            {node.run_id && <> · run {node.run_id.slice(0, 8)}</>}
          </div>
        </div>
        <div className="flex flex-none items-center gap-1.5">
          {node.tie_detected && (
            <span className="flex items-center gap-1 rounded-full border border-amber/50 bg-amber/10 px-2 py-0.5 font-mono text-[10.5px] text-amber">
              <Scale className="h-3 w-3" /> tie-broken
            </span>
          )}
          {undone && (
            <span className="flex items-center gap-1 rounded-full border border-hairline-strong px-2 py-0.5 font-mono text-[10.5px] text-muted">
              <RotateCcw className="h-3 w-3" /> reverted
            </span>
          )}
          <span className="font-mono text-[13px] font-semibold tabular-nums text-emerald">
            {node.winner_score === null ? "—" : `${Math.round(node.winner_score * 100)}%`}
          </span>
        </div>
      </div>

      {node.rationale && (
        <p className="mb-2.5 border-l-2 border-hairline-strong pl-2.5 text-[12px] leading-relaxed text-ink-2">
          {node.rationale}
        </p>
      )}

      {node.alternatives.length > 0 && (
        <div className="mb-2.5">
          <div className="mb-1 font-mono text-[10px] font-semibold uppercase tracking-wider text-muted">
            Rejected ({node.alternatives.length})
          </div>
          <ul className="space-y-1">
            {node.alternatives.map((alt) => <RejectedRow key={alt.branch_id} alt={alt} />)}
          </ul>
        </div>
      )}

      {node.applied_commit_sha ? (
        <div className="flex items-center gap-1.5 text-[11.5px] text-emerald">
          <GitCommitHorizontal className="h-3.5 w-3.5" />
          <span className="font-mono">{node.applied_commit_sha.slice(0, 12)}</span>
          <span className="text-muted">
            applied{node.applied_validation_status ? ` · validation ${node.applied_validation_status}` : ""}
          </span>
        </div>
      ) : (
        <div className="text-[11.5px] text-muted">
          {node.applied_validation_status
            ? `Apply attempted — ${node.applied_validation_status}`
            : "Not applied to a checkout yet"}
        </div>
      )}
    </div>
  );
}

function RejectedRow({ alt }: { alt: StrategyComparison }) {
  return (
    <li className="flex items-start gap-1.5 text-[11.5px]">
      <XCircle className="mt-0.5 h-3 w-3 flex-none text-muted" />
      <div className="min-w-0">
        <span className="text-ink-2">{alt.hypothesis ?? alt.strategy_name}</span>
        <span className="ml-1.5 whitespace-nowrap font-mono text-[10.5px] text-muted">
          {alt.score === null ? "—" : `${Math.round(alt.score * 100)}%`}
          {alt.reason && alt.reason !== "ok" ? ` · ${alt.reason}` : ""}
        </span>
      </div>
    </li>
  );
}
