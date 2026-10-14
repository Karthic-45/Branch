import { useEffect, useState, useMemo } from "react";
import { motion } from "framer-motion";
import { Layers } from "lucide-react";
import type { BranchNode, BranchVerdict, Stage } from "../../lib/types";
import { fetchBranchVerdicts } from "../../lib/api";
import { VerdictBox } from "./VerdictBox";
import { PageSkeleton } from "../common/Loading";
import type { Theme } from "../../hooks/useTheme";
import { BranchTree } from "../BranchTree";

interface Props {
  rootId?: string | null;
  nodes: BranchNode[];
  /** The live tree has not sent its first snapshot yet. */
  loading?: boolean;
  branchStage: Map<string, Stage>;
  theme: Theme;
  onSelectBranch?: (node: BranchNode) => void;
}

interface CardItem {
  id: string;
  fullId: string;
  title: string;
  status: string;
  statusColor: string;
  step: number;
  score: string;
  files: number;
  diff: string;
  reason?: string | null;
  passed?: number | null;
  total?: number | null;
}

export function BranchesPage({ rootId, nodes, branchStage, theme, onSelectBranch, loading = false }: Props) {
  const [viewMode, setViewMode] = useState<"tree" | "cards">("tree");
  const [verdicts, setVerdicts] = useState<Map<string, BranchVerdict>>(new Map());

  // Re-read whenever a branch changes status: a merge or abandon is exactly
  // when a new "why" exists to show.
  const statusKey = nodes.map((n) => `${n.id}:${n.status}:${n.score ?? ""}`).join("|");
  useEffect(() => {
    if (!rootId) { setVerdicts(new Map()); return; }
    let cancelled = false;
    fetchBranchVerdicts(rootId)
      .then((vs) => { if (!cancelled) setVerdicts(new Map(vs.map((v) => [v.branch_id, v]))); })
      .catch(() => { if (!cancelled) setVerdicts(new Map()); });
    return () => { cancelled = true; };
  }, [rootId, statusKey]);
  const [searchQuery, setSearchQuery] = useState("");

  const handleSelect = (id: string) => {
    const found = nodes.find((n) => n.id === id || n.id.slice(0, 8) === id);
    if (found && onSelectBranch) {
      onSelectBranch(found);
    }
  };

  const cards = useMemo<CardItem[]>(() => {
    const children = nodes.filter((n) => n.parent_id !== null);
    if (children.length > 0) {
      return children.map((n) => ({
        id: n.id.slice(0, 8),
        fullId: n.id,
        title: n.hypothesis || `Branch ${n.id.slice(0, 8)}`,
        status: n.status.charAt(0).toUpperCase() + n.status.slice(1),
        statusColor:
          n.status === "merged"
            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800"
            : n.status === "exploring"
            ? "bg-blue-100 text-blue-700 dark:bg-blue-950/60 dark:text-blue-400 border-blue-200 dark:border-blue-800"
            : n.status === "abandoned"
            ? "bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-400 border-amber-200 dark:border-amber-800"
            : n.status === "failed"
            ? "bg-rose-100 text-rose-700 dark:bg-rose-950/60 dark:text-rose-400 border-rose-200 dark:border-rose-800"
            : "bg-purple-100 text-purple-700 dark:bg-purple-950/60 dark:text-purple-400 border-purple-200 dark:border-purple-800",
        step: n.status === "merged" ? 5 : n.score !== null ? 4 : n.status === "exploring" ? 2 : 3,
        score: n.score !== null ? `${(n.score * 100).toFixed(1)}%` : "—",
        files: n.total ?? 0,
        diff: n.passed != null && n.total != null ? `${n.passed}/${n.total} tests` : "—",
        reason: n.reason,
        passed: n.passed,
        total: n.total,
      }));
    }
    return [];
  }, [nodes]);

  const filteredCards = useMemo(() => {
    if (!searchQuery.trim()) return cards;
    const q = searchQuery.toLowerCase();
    return cards.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.id.toLowerCase().includes(q) ||
        c.status.toLowerCase().includes(q)
    );
  }, [cards, searchQuery]);

  if (loading && nodes.length === 0) {
    return <PageSkeleton variant="cards" caption="Reading the branch tree from the live stream…" />;
  }

  return (
    <div className="p-5 sm:p-6 lg:p-7 w-full space-y-6">
      {/* ── Screen 4: Branch Lineage Tree Container ── */}
      <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-6 shadow-xs space-y-5">
        {/* Header Controls */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-slate-100 dark:border-slate-800">
          <div>
            <h1 className="text-xl font-bold text-slate-900 dark:text-white">
              Branch Lineage {viewMode === "tree" ? "Tree" : "Cards"}
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              Copy-on-write isolation: sibling branches cannot observe rival conclusions.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {/* Search (active in cards view) */}
            {viewMode === "cards" && (
              <input
                type="text"
                placeholder="Search branches..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="px-3 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 w-44 text-slate-800 dark:text-slate-200"
              />
            )}

            {/* View Mode Toggle */}
            <div className="flex items-center rounded-lg bg-slate-100 dark:bg-slate-800 p-0.5 text-xs font-semibold">
              <button
                onClick={() => setViewMode("tree")}
                className={`rounded-md px-2.5 py-1 transition-colors cursor-pointer ${
                  viewMode === "tree"
                    ? "bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-300 shadow-2xs"
                    : "text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
                }`}
              >
                Tree
              </button>
              <button
                onClick={() => setViewMode("cards")}
                className={`rounded-md px-2.5 py-1 transition-colors cursor-pointer ${
                  viewMode === "cards"
                    ? "bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-300 shadow-2xs"
                    : "text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
                }`}
              >
                Cards
              </button>
            </div>
          </div>
        </div>

        {/* Legend */}
        <div className="flex flex-wrap items-center gap-3 text-xs font-medium">
          <span className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" /> Merged
          </span>
          <span className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
            <span className="h-2.5 w-2.5 rounded-full bg-blue-500" /> Exploring
          </span>
          <span className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
            <span className="h-2.5 w-2.5 rounded-full bg-purple-500" /> Evaluated
          </span>
          <span className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
            <span className="h-2.5 w-2.5 rounded-full bg-amber-500" /> Abandoned
          </span>
          <span className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
            <span className="h-2.5 w-2.5 rounded-full bg-rose-500" /> Failed
          </span>
        </div>

        {/* View Mode: Live Branch Tree or Cards */}
        {viewMode === "tree" ? (
          <div className="rounded-xl border border-slate-100 dark:border-slate-800/80 bg-slate-50/50 dark:bg-slate-900/30 p-2 min-h-[360px]">
            <BranchTree
              nodes={nodes}
              branchStage={branchStage}
              theme={theme}
              onSelect={onSelectBranch}
            />
          </div>
        ) : filteredCards.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 dark:border-slate-800 p-8 text-center text-xs text-slate-400">
            {cards.length === 0 ? "No branches forked yet. Start a migration to fork branches." : `No branches match "${searchQuery}"`}
          </div>
        ) : (
          /* Cards View Mode */
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 pt-2">
            {filteredCards.map((c) => (
              <motion.div
                key={c.id}
                whileHover={{ y: -3 }}
                onClick={() => handleSelect(c.fullId || c.id)}
                className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-900/40 p-4 shadow-xs space-y-3 cursor-pointer hover:border-indigo-300 dark:hover:border-indigo-700 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs font-bold text-slate-700 dark:text-slate-300">
                    {c.id}
                  </span>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold border ${c.statusColor}`}>
                    {c.status}
                  </span>
                </div>

                <div>
                  <div className="text-xs font-bold text-slate-900 dark:text-white truncate">
                    {c.title}
                  </div>
                </div>

                {/* 5-Step Stepper */}
                <div className="flex items-center justify-between gap-1 py-1">
                  {["Fork", "Explore", "Eval", "Score", "Merge"].map((st, i) => (
                    <div key={st} className="flex flex-col items-center gap-1">
                      <span
                        className={`h-2 w-2 rounded-full ${
                          i < c.step ? "bg-indigo-600" : "bg-slate-200 dark:bg-slate-700"
                        }`}
                      />
                      <span className="text-[8.5px] text-slate-400">{st}</span>
                    </div>
                  ))}
                </div>

                <div className="border-t border-slate-100 dark:border-slate-800/80 pt-2.5 flex items-center justify-between text-xs font-mono">
                  <div>
                    <span className="text-[10px] text-slate-400 block font-sans">Score</span>
                    <span className="font-bold text-indigo-600 dark:text-indigo-400">{c.score}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 block font-sans">Tests</span>
                    <span className="text-slate-700 dark:text-slate-300">{c.diff}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-400 block font-sans">Status</span>
                    <span className="text-slate-500 text-[11px]">{c.status}</span>
                  </div>
                </div>

                {verdicts.get(c.fullId) ? (
                  <VerdictBox verdict={verdicts.get(c.fullId)!} compact />
                ) : c.reason ? (
                  <div className="rounded-lg bg-slate-100/80 dark:bg-slate-800/60 px-2.5 py-1 text-[11px] font-mono text-slate-600 dark:text-slate-300 flex items-center justify-between border border-slate-200/50 dark:border-slate-700/50">
                    <span className="text-slate-400 font-sans text-[10px] font-medium uppercase tracking-wider">Reason</span>
                    <span className="font-semibold text-slate-800 dark:text-slate-200">
                      {c.passed != null && c.total != null && c.total > 0
                        ? `${c.passed}/${c.total} (${c.reason})`
                        : c.reason}
                    </span>
                  </div>
                ) : null}
              </motion.div>
            ))}
          </div>
        )}
      </div>

      {/* ── Bottom Branch Cards (Shown below Tree View) ── */}
      {viewMode === "tree" && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Layers className="h-4 w-4 text-indigo-500" />
              <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">Branch Cards</h2>
              <span className="rounded-full bg-slate-100 dark:bg-slate-800 px-2 py-0.5 text-[11px] font-semibold text-slate-600 dark:text-slate-400">
                {cards.length}
              </span>
            </div>
            <div className="flex items-center gap-3 text-xs text-slate-500">
              <span>Sort: <strong className="text-slate-800 dark:text-slate-200">Recent</strong></span>
            </div>
          </div>

          {cards.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 dark:border-slate-800 p-8 text-center text-xs text-slate-400">
              No branches forked yet. Start a migration to fork branches.
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {cards.map((c) => (
                <motion.div
                  key={c.id}
                  whileHover={{ y: -3 }}
                  onClick={() => handleSelect(c.fullId || c.id)}
                  className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-4 shadow-xs space-y-3 cursor-pointer hover:border-indigo-300 dark:hover:border-indigo-700 transition-colors"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs font-bold text-slate-700 dark:text-slate-300">
                      {c.id}
                    </span>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold border ${c.statusColor}`}>
                      {c.status}
                    </span>
                  </div>

                  <div>
                    <div className="text-xs font-bold text-slate-900 dark:text-white truncate">
                      {c.title}
                    </div>
                  </div>

                  {/* 5-Step Stepper */}
                  <div className="flex items-center justify-between gap-1 py-1">
                    {["Fork", "Explore", "Eval", "Score", "Merge"].map((st, i) => (
                      <div key={st} className="flex flex-col items-center gap-1">
                        <span
                          className={`h-2 w-2 rounded-full ${
                            i < c.step ? "bg-indigo-600" : "bg-slate-200 dark:bg-slate-700"
                          }`}
                        />
                        <span className="text-[8.5px] text-slate-400">{st}</span>
                      </div>
                    ))}
                  </div>

                  <div className="border-t border-slate-100 dark:border-slate-800/80 pt-2.5 flex items-center justify-between text-xs font-mono">
                    <div>
                      <span className="text-[10px] text-slate-400 block font-sans">Score</span>
                      <span className="font-bold text-indigo-600 dark:text-indigo-400">{c.score}</span>
                    </div>
                    <div>
                      <span className="text-[10px] text-slate-400 block font-sans">Tests</span>
                      <span className="text-slate-700 dark:text-slate-300">{c.diff}</span>
                    </div>
                    <div>
                      <span className="text-[10px] text-slate-400 block font-sans">Status</span>
                      <span className="text-slate-500 text-[11px]">{c.status}</span>
                    </div>
                  </div>

                  {verdicts.get(c.fullId) ? (
                    <VerdictBox verdict={verdicts.get(c.fullId)!} compact />
                  ) : c.reason ? (
                    <div className="rounded-lg bg-slate-100/80 dark:bg-slate-800/60 px-2.5 py-1 text-[11px] font-mono text-slate-600 dark:text-slate-300 flex items-center justify-between border border-slate-200/50 dark:border-slate-700/50">
                      <span className="text-slate-400 font-sans text-[10px] font-medium uppercase tracking-wider">Reason</span>
                      <span className="font-semibold text-slate-800 dark:text-slate-200">
                        {c.passed != null && c.total != null && c.total > 0
                          ? `${c.passed}/${c.total} (${c.reason})`
                          : c.reason}
                      </span>
                    </div>
                  ) : null}
                </motion.div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
