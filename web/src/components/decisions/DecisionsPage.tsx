import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { RotateCcw, Play, Loader2, Scale, Trophy, CheckCircle2, AlertCircle, Check } from "lucide-react";
import type { ApplyResult, BranchVerdict, DecisionNode, MergeRow } from "../../lib/types";
import { applyMerge, fetchBranchVerdicts, revertMerge } from "../../lib/api";
import { VerdictBox } from "../branches/VerdictBox";
import { PageSkeleton, Shimmer } from "../common/Loading";
import { CounterfactualPanel } from "../CounterfactualPanel";
import { MemoryDiffPanel } from "../MemoryDiffPanel";
import { ManifestPanel } from "../ManifestPanel";
import { PatchPanel } from "../patch/PatchPanel";
import { ApplyStepper } from "../patch/ApplyStepper";

const TABS = [
  { id: "rationale", label: "Rationale" },
  { id: "counterfactuals", label: "Counterfactuals" },
  { id: "patch", label: "Patch / Code Diff" },
  { id: "memory", label: "Memory Diff" },
  { id: "reproducibility", label: "Reproducibility" },
  { id: "apply", label: "Apply & Validate" },
] as const;

type Tab = (typeof TABS)[number]["id"];

interface Props {
  rootId: string | null;
  merges: MergeRow[];
  decisions: DecisionNode[];
  selected: string | null;
  onSelect: (mergeId: string) => void;
  onReverted: () => void;
  onToast?: (text: string, tone?: "success" | "error" | "neutral") => void;
  /** Decisions are still being fetched for this project. */
  loading?: boolean;
}

export function DecisionsPage({
  rootId, merges, decisions, selected, onSelect, onReverted, onToast, loading = false,
}: Props) {
  const [tab, setTab] = useState<Tab>("rationale");
  const [reverting, setReverting] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null);

  const activeId = selected ?? (decisions[0]?.merge_id ?? merges[0]?.merge_id ?? null);
  const activeNode = (activeId ? decisions.find((d) => d.merge_id === activeId) : null) ?? decisions[0] ?? null;

  // Per-branch "why merged / why abandoned", re-read when the decision list
  // changes (a new merge or a revert changes the verdicts).
  const [verdicts, setVerdicts] = useState<BranchVerdict[]>([]);
  const [verdictsLoading, setVerdictsLoading] = useState(false);
  const decisionsKey = decisions.map((d) => `${d.merge_id}:${d.reverted_at ?? ""}`).join("|");
  useEffect(() => {
    if (!rootId) { setVerdicts([]); return; }
    let cancelled = false;
    setVerdictsLoading(true);
    fetchBranchVerdicts(rootId)
      .then((vs) => { if (!cancelled) setVerdicts(vs); })
      .catch(() => { if (!cancelled) setVerdicts([]); })
      .finally(() => { if (!cancelled) setVerdictsLoading(false); });
    return () => { cancelled = true; };
  }, [rootId, decisionsKey]);

  const runVerdicts = activeNode
    ? verdicts
        .filter((v) =>
          activeNode.run_id
            ? v.run_id === activeNode.run_id || v.branch_id === activeNode.winner_branch_id
            : v.parent_id === activeNode.parent_id)
        .sort((a, b) =>
          (a.branch_id === activeNode.winner_branch_id ? -1 : 0) -
          (b.branch_id === activeNode.winner_branch_id ? -1 : 0) ||
          (b.score ?? -1) - (a.score ?? -1))
    : [];

  const handleRevert = async () => {
    if (!activeNode?.merge_id) return;
    setReverting(true);
    try {
      await revertMerge(activeNode.merge_id);
      onReverted();
      onToast?.("Merge reverted — parent restored to pre-merge belief snapshot", "success");
    } catch (err) {
      onToast?.(err instanceof Error ? err.message : "Revert failed", "error");
    } finally {
      setReverting(false);
    }
  };

  if (loading && decisions.length === 0 && merges.length === 0) {
    return (
      <PageSkeleton
        variant="split"
        caption="Reading decisions, scores and rationale from CockroachDB…"
      />
    );
  }

  if (decisions.length === 0 && merges.length === 0) {
    return (
      <div className="p-5 sm:p-6 lg:p-7 w-full">
        <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-12 text-center shadow-xs">
          <Scale className="h-10 w-10 text-slate-300 dark:text-slate-600 mx-auto mb-3" />
          <h2 className="text-base font-bold text-slate-800 dark:text-slate-200">No Decisions Recorded</h2>
          <p className="text-xs text-slate-400 mt-1 max-w-md mx-auto">
            No branches have been evaluated and merged into trunk yet. Start a migration run to evaluate competing hypotheses.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-5 sm:p-6 lg:p-7 w-full space-y-5">
      <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-5 items-start">
        {/* Left Column: Decision Timeline */}
        <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-4 shadow-xs">
          <div className="flex items-center justify-between pb-3 mb-2 border-b border-slate-100 dark:border-slate-800">
            <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">Decision Timeline</h2>
            <span className="rounded-full bg-slate-100 dark:bg-slate-800 px-2 py-0.5 text-[11px] font-semibold text-slate-500">
              {decisions.length || merges.length}
            </span>
          </div>

          <div className="space-y-2">
            {(decisions.length > 0 ? decisions : merges).map((d, idx) => {
              const mergeId = "merge_id" in d ? d.merge_id : (d as any).id;
              const hypothesis = "winner_hypothesis" in d ? d.winner_hypothesis : `Decision #${idx + 1}`;
              const score = "winner_score" in d && d.winner_score !== null ? `${Math.round(d.winner_score * 100)}%` : null;
              const date = "merged_at" in d && d.merged_at ? new Date(d.merged_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—";
              const isReverted = "reverted_at" in d && Boolean(d.reverted_at);
              const isSelected = activeNode?.merge_id === mergeId;

              return (
                <button
                  key={mergeId}
                  onClick={() => onSelect(mergeId)}
                  className={`w-full group text-left rounded-xl p-3 transition-all duration-150 border cursor-pointer
                    ${
                      isSelected
                        ? "border-indigo-500 bg-indigo-50/60 dark:bg-indigo-950/40 shadow-xs ring-1 ring-indigo-500/20"
                        : "border-slate-100 dark:border-slate-800/80 bg-slate-50/40 dark:bg-slate-900/30 hover:bg-slate-100/60 hover:border-slate-200"
                    }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className={`font-mono text-xs font-bold truncate max-w-[200px] ${isSelected ? "text-indigo-600 dark:text-indigo-400" : "text-slate-700 dark:text-slate-300"}`}>
                      #{mergeId.slice(0, 8)} {hypothesis || "Decision"}
                    </span>
                    {score && (
                      <span className="font-mono text-xs font-bold text-emerald-600 dark:text-emerald-400">
                        {score}
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-slate-400 dark:text-slate-500 flex items-center justify-between">
                    <span>{date}</span>
                    <span className={`rounded px-1.5 py-0.2 text-[9.5px] font-bold ${
                      isReverted
                        ? "bg-amber-100 dark:bg-amber-950/60 text-amber-700 dark:text-amber-400"
                        : "bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-400"
                    }`}>
                      {isReverted ? "REVERTED" : "MERGED"}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Right Main Column: Decision Details */}
        {activeNode ? (
          <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-6 shadow-xs space-y-6">
            {/* Header Row */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-slate-100 dark:border-slate-800">
              <div>
                <div className="flex items-center gap-2.5">
                  <h1 className="text-xl sm:text-2xl font-extrabold text-slate-900 dark:text-white font-mono">
                    Decision #{activeNode.merge_id.slice(0, 8)}
                  </h1>
                  <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold border ${
                    activeNode.reverted_at
                      ? "bg-amber-100 dark:bg-amber-950/60 text-amber-700 dark:text-amber-400 border-amber-200 dark:border-amber-800"
                      : "bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800"
                  }`}>
                    {activeNode.reverted_at ? "REVERTED" : "MERGED"}
                  </span>
                </div>
                <p className="mt-1 text-sm font-medium text-slate-500 dark:text-slate-400">
                  {activeNode.winner_hypothesis ?? `Winning branch ${activeNode.winner_branch_id.slice(0, 8)} (no hypothesis recorded)`}
                </p>
              </div>

              {!activeNode.reverted_at && (
                <button
                  onClick={handleRevert}
                  disabled={reverting}
                  className="flex items-center gap-2 rounded-xl border border-rose-200 dark:border-rose-900 bg-rose-50/60 dark:bg-rose-950/40 hover:bg-rose-100 text-rose-700 dark:text-rose-300 font-semibold px-4 py-2 text-xs shadow-2xs transition-colors self-start sm:self-center cursor-pointer"
                >
                  <RotateCcw className={`h-3.5 w-3.5 ${reverting ? "animate-spin" : ""}`} />
                  <span>{reverting ? "Reverting…" : "Revert"}</span>
                </button>
              )}
            </div>

            {/* 6 Tabs */}
            <div className="flex items-center gap-1 overflow-x-auto border-b border-slate-100 dark:border-slate-800 pb-2">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`relative rounded-lg px-3.5 py-1.5 text-xs font-semibold whitespace-nowrap transition-colors cursor-pointer
                    ${
                      tab === t.id
                        ? "text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-950/50"
                        : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/60"
                    }`}
                >
                  {t.label}
                  {tab === t.id && (
                    <motion.div
                      layoutId="activeDecisionTab"
                      className="absolute -bottom-2.5 left-2 right-2 h-0.5 rounded-full bg-indigo-600 dark:bg-indigo-400"
                    />
                  )}
                </button>
              ))}
            </div>

            {/* Tab 1: Rationale */}
            {tab === "rationale" && (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2 }}
                className="space-y-6"
              >
                {/* Callout box */}
                <div className="rounded-xl border border-indigo-100 dark:border-indigo-950/60 bg-indigo-50/50 dark:bg-indigo-950/30 p-4">
                  <div className="text-xs font-bold uppercase tracking-wider text-indigo-700 dark:text-indigo-400 mb-1">
                    Selection Rationale
                  </div>
                  <p className="text-sm text-slate-700 dark:text-slate-300 leading-relaxed font-medium">
                    {activeNode.rationale ?? "No rationale was recorded for this decision."}
                  </p>
                </div>

                {/* Stat highlight cards */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <div className="rounded-xl border border-slate-100 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-900/40 p-3.5 text-center">
                    <div className="text-[11px] font-medium text-slate-400">Winning Score</div>
                    <div className="text-xl font-extrabold text-slate-900 dark:text-white mt-1">
                      {activeNode.winner_score !== null ? `${(activeNode.winner_score * 100).toFixed(1)}%` : "—"}
                    </div>
                  </div>

                  <div className="rounded-xl border border-slate-100 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-900/40 p-3.5 text-center">
                    <div className="text-[11px] font-medium text-slate-400">Winning Branch</div>
                    <div className="text-xl font-extrabold text-slate-900 dark:text-white mt-1 font-mono">
                      {activeNode.winner_branch_id.slice(0, 8)}
                    </div>
                  </div>

                  <div className="rounded-xl border border-slate-100 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-900/40 p-3.5 text-center">
                    <div className="text-[11px] font-medium text-slate-400">Alternatives Evaluated</div>
                    <div className="text-xl font-extrabold text-slate-900 dark:text-white mt-1">
                      {activeNode.alternatives?.length ?? 0}
                    </div>
                  </div>

                  <div className="rounded-xl border border-slate-100 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-900/40 p-3.5 text-center">
                    <div className="text-[11px] font-medium text-slate-400">Tie Broken</div>
                    <div className="text-xl font-extrabold text-slate-900 dark:text-white mt-1">
                      {activeNode.tie_detected ? "Yes" : "No"}
                    </div>
                  </div>
                </div>

                {/* Comparison with Other Hypotheses */}
                <StrategyComparisonsSection activeNode={activeNode} />

                {/* Why each branch was merged or abandoned */}
                <div className="space-y-3 pt-2">
                  <div className="text-xs font-bold uppercase tracking-wider text-slate-400">
                    Why each strategy was merged or abandoned
                  </div>
                  {verdictsLoading && runVerdicts.length === 0 ? (
                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                      {Array.from({ length: 4 }).map((_, i) => (
                        <div key={i} className="space-y-2 rounded-lg border border-slate-100 dark:border-slate-800 p-3">
                          <Shimmer w="40%" h={9} />
                          <Shimmer h={10} />
                          <Shimmer w="80%" h={10} />
                        </div>
                      ))}
                    </div>
                  ) : runVerdicts.length === 0 ? (
                    <p className="text-xs text-slate-400">
                      No per-branch outcomes are recorded for this decision.
                    </p>
                  ) : (
                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                      {runVerdicts.map((v) => (
                        <VerdictBox
                          key={v.branch_id}
                          verdict={v}
                          title={`${v.decision === "merged" ? "Merged" : v.decision === "abandoned" ? "Abandoned" : "Pending"} · ${v.strategy ?? v.hypothesis ?? v.branch_id.slice(0, 8)}`}
                        />
                      ))}
                    </div>
                  )}
                </div>
              </motion.div>
            )}

            {/* Tab 2: Counterfactuals */}
            {tab === "counterfactuals" && (
              <CounterfactualPanel mergeId={activeNode.merge_id} />
            )}

            {/* Tab 3: Patch / Code Diff */}
            {tab === "patch" && (
              <PatchPanel
                mergeId={activeNode.merge_id}
                baseSha={activeNode.applied_commit_sha}
                onToast={onToast}
              />
            )}

            {/* Tab 4: Memory Diff */}
            {tab === "memory" && (
              <MemoryDiffPanel mergeId={activeNode.merge_id} />
            )}

            {/* Tab 5: Reproducibility */}
            {tab === "reproducibility" && (
              <ManifestPanel branchId={activeNode.winner_branch_id} />
            )}

            {/* Tab 6: Apply & Validate */}
            {tab === "apply" && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500">
                      Safe-Apply Validation Gate
                    </h3>
                    <p className="text-[12px] text-slate-400">
                      Clones target repository, verifies base SHA, tests patch cleanly on isolated branch.
                    </p>
                  </div>
                  <button
                    onClick={async () => {
                      setApplying(true);
                      try {
                        const res = await applyMerge(activeNode.merge_id, true, false);
                        setApplyResult(res);
                        const status = res.validation_status.replace(/_/g, " ");
                        onToast?.(
                          res.validation_status === "passed"
                            ? `Validation passed${res.commit_sha ? ` — committed ${res.commit_sha.slice(0, 8)}` : ""}`
                            : `Validation result: ${status}`,
                          res.validation_status === "passed" ? "success" : res.validation_status === "skipped" ? "neutral" : "error",
                        );
                      } catch (err) {
                        onToast?.(err instanceof Error ? err.message : "Validation run failed", "error");
                      } finally {
                        setApplying(false);
                      }
                    }}
                    disabled={applying}
                    className="flex items-center gap-1.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-semibold px-3.5 py-1.5 text-xs shadow-xs cursor-pointer"
                  >
                    {applying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                    <span>{applying ? "Validating…" : "Run Validation"}</span>
                  </button>
                </div>
                {applyResult ? (
                  <ApplyStepper result={applyResult} />
                ) : (
                  <div className="rounded-xl border border-dashed border-slate-200 dark:border-slate-800 p-8 text-center text-xs text-slate-400">
                    Click "Run Validation" to safe-apply and run the test suite against a clean branch checkout.
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-12 text-center text-xs text-slate-400">
            Select a decision from the timeline to view its details.
          </div>
        )}
      </div>
    </div>
  );
}

function StrategyComparisonsSection({ activeNode }: { activeNode: DecisionNode }) {
  const winnerPct = activeNode.winner_score !== null ? Math.round(activeNode.winner_score * 100) : 100;
  const alternatives = activeNode.alternatives ?? [];
  const tiedCount = alternatives.filter((a) => a.score !== null && Math.round(a.score * 100) === winnerPct).length;
  const failedCount = alternatives.filter((a) => a.score === null || a.score === 0).length;

  return (
    <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 sm:p-6 shadow-xs space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-slate-100 dark:border-slate-800">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20 shadow-2xs">
            <Scale className="h-4 w-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 tracking-tight">
                Strategy Comparisons
              </h3>
              <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 border border-slate-200/60 dark:border-slate-700/60">
                {alternatives.length + 1} Hypotheses
              </span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
              Evaluated against real test suites in isolated sandbox containers
            </p>
          </div>
        </div>

        {/* Status badges */}
        <div className="flex items-center gap-2">
          {activeNode.tie_detected && (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-amber-50 dark:bg-amber-950/40 border border-amber-200/60 dark:border-amber-800/40 text-amber-700 dark:text-amber-300 text-xs font-medium">
              <AlertCircle className="h-3.5 w-3.5 text-amber-500" />
              <span>Tie Broken by Planner</span>
            </span>
          )}
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-50 dark:bg-slate-800/60 border border-slate-200/60 dark:border-slate-700/60 text-slate-600 dark:text-slate-400 text-xs font-medium">
            <span>{tiedCount} Tied</span>
            <span>·</span>
            <span>{failedCount} Rejected</span>
          </span>
        </div>
      </div>

      {/* ── THE WINNER CARD (Champion) ── */}
      {activeNode.winner_score !== null && (
        <div className="group relative rounded-xl border-2 border-emerald-500/40 dark:border-emerald-500/50 bg-gradient-to-br from-emerald-500/[0.08] via-emerald-500/[0.02] to-teal-500/[0.06] p-4.5 sm:p-5 overflow-hidden shadow-xs hover:border-emerald-500/60 transition-all">
          {/* Subtle background glow */}
          <div className="absolute -top-12 -right-12 w-40 h-40 bg-emerald-500/15 rounded-full blur-2xl pointer-events-none" />

          {/* Top Row: Badges and Score */}
          <div className="flex items-center justify-between gap-3 mb-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500 text-white font-bold text-[10px] uppercase tracking-wider shadow-xs shadow-emerald-500/40">
                <Trophy className="h-3 w-3" />
                Winner · Merged
              </span>
              <span className="font-mono text-xs text-slate-500 dark:text-slate-400 bg-white/90 dark:bg-slate-900/90 px-2 py-0.5 rounded-md border border-slate-200/80 dark:border-slate-800">
                {activeNode.winner_branch_id.slice(0, 8)}
              </span>
              <span className="text-[11px] font-semibold text-emerald-700 dark:text-emerald-400">
                Promoted to trunk
              </span>
            </div>

            <div className="flex items-baseline gap-1 bg-emerald-500/10 dark:bg-emerald-500/20 px-3 py-1 rounded-lg border border-emerald-500/20">
              <span className="text-lg font-black text-emerald-600 dark:text-emerald-400 font-mono tracking-tight leading-none">
                {winnerPct}%
              </span>
              <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-600/80 dark:text-emerald-400/80">
                Pass
              </span>
            </div>
          </div>

          {/* Hypothesis Text */}
          <p className="text-xs sm:text-sm font-semibold text-slate-900 dark:text-slate-100 leading-relaxed mb-3">
            {activeNode.winner_hypothesis || activeNode.winner_branch_id}
          </p>

          {/* Glowing Progress Bar */}
          <div className="space-y-1.5">
            <div className="h-2.5 w-full rounded-full bg-slate-200/70 dark:bg-slate-800 overflow-hidden p-0.5">
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: `${winnerPct}%` }}
                transition={{ duration: 0.8, ease: "easeOut" }}
                className="h-full rounded-full bg-gradient-to-r from-emerald-500 via-teal-400 to-emerald-300 shadow-[0_0_12px_rgba(16,185,129,0.5)]"
              />
            </div>
            <div className="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
              <span className="flex items-center gap-1.5 text-emerald-700 dark:text-emerald-300 font-medium">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                Verified across test suite · Candidate diff stored in S3
              </span>
              <span className="font-mono text-xs font-bold text-slate-700 dark:text-slate-300">
                Rank #1
              </span>
            </div>
          </div>
        </div>
      )}

      {/* ── ALTERNATIVES LIST ── */}
      {alternatives.length > 0 ? (
        <div className="space-y-2.5 pt-1">
          <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500 flex items-center justify-between">
            <span>Competing Hypotheses ({alternatives.length})</span>
            <span>Outcome & Score</span>
          </div>

          {alternatives.map((alt) => {
            const altPct = alt.score !== null ? Math.round(alt.score * 100) : 0;
            const isTied = altPct === winnerPct && altPct > 0;
            const isFailed = altPct === 0 || alt.score === null;
            const displayName = alt.strategy_name || alt.hypothesis || alt.branch_id.slice(0, 8);
            const hasTests = Boolean(alt.total && alt.total > 0);

            // Clean reason string
            let formattedReason = alt.reason;
            if (formattedReason) {
              if (formattedReason.startsWith("agent_declined")) {
                formattedReason = "Agent declined (no patch written)";
              } else if (formattedReason === "patch_did_not_apply") {
                formattedReason = "Patch did not apply";
              } else if (formattedReason === "ok") {
                formattedReason = null;
              } else {
                formattedReason = formattedReason.replace(/_/g, " ");
              }
            }

            return (
              <div
                key={alt.branch_id}
                className={`group rounded-xl border p-3.5 transition-all duration-200 ${
                  isTied
                    ? "border-blue-200 dark:border-blue-900/50 bg-blue-50/30 dark:bg-blue-950/20 hover:border-blue-400 dark:hover:border-blue-700"
                    : isFailed
                      ? "border-slate-200/80 dark:border-slate-800/80 bg-slate-50/40 dark:bg-slate-900/20 hover:border-slate-300 dark:hover:border-slate-700"
                      : "border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900/40 hover:border-slate-300"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-mono text-xs font-semibold text-slate-500 dark:text-slate-400 bg-white dark:bg-slate-800 px-1.5 py-0.5 rounded border border-slate-200 dark:border-slate-700">
                      {alt.branch_id.slice(0, 8)}
                    </span>
                    <span className="font-semibold text-xs text-slate-800 dark:text-slate-200 truncate max-w-md" title={displayName}>
                      {displayName}
                    </span>
                  </div>

                  <div className="flex items-center gap-2 flex-none">
                    {isTied && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-950/60 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800">
                        <Check className="h-3 w-3" /> Tied
                      </span>
                    )}

                    {formattedReason && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-400 border border-amber-200/80 dark:border-amber-800/80">
                        <AlertCircle className="h-3 w-3 flex-none" />
                        <span className="capitalize">{formattedReason}</span>
                      </span>
                    )}

                    {hasTests && (
                      <span className="font-mono text-[11px] font-medium text-slate-500 dark:text-slate-400 bg-white dark:bg-slate-800/80 px-2 py-0.5 rounded border border-slate-200/60 dark:border-slate-700/60">
                        {alt.passed}/{alt.total} tests
                      </span>
                    )}

                    <span className={`font-mono text-xs font-extrabold px-2 py-0.5 rounded ${
                      isTied
                        ? "text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/50"
                        : isFailed
                          ? "text-slate-400 dark:text-slate-500 bg-slate-100 dark:bg-slate-800"
                          : "text-slate-700 dark:text-slate-300 bg-slate-100 dark:bg-slate-800"
                    }`}>
                      {altPct}%
                    </span>
                  </div>
                </div>

                {/* Progress bar */}
                <div className="h-1.5 w-full rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
                  <motion.div
                    initial={{ width: 0 }}
                    animate={{ width: `${altPct}%` }}
                    transition={{ duration: 0.6, ease: "easeOut" }}
                    className={`h-full rounded-full ${
                      isTied
                        ? "bg-gradient-to-r from-blue-500 to-indigo-500"
                        : isFailed
                          ? "bg-slate-300 dark:bg-slate-700"
                          : "bg-slate-500"
                    }`}
                  />
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-xs text-slate-400 py-2">No alternative hypothesis comparisons recorded.</p>
      )}
    </div>
  );
}
