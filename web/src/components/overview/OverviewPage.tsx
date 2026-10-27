import { useEffect, useMemo, useState } from "react";
import { PageSkeleton } from "../common/Loading";
import { CountUp } from "../common/Motion";
import { motion } from "framer-motion";
import {
  GitBranch, Trophy, Clock, Plus, Download,
  Sparkles, Layers, Activity,
  ShieldCheck, ShieldAlert, Play, AlertTriangle,
} from "lucide-react";
import type {
  BranchNode, DecisionNode, FeedEvent, MergeRow, ReadinessReport, Stage,
} from "../../lib/types";
import type { Theme } from "../../hooks/useTheme";
import { LiveExecutionTrace } from "./LiveExecutionTrace";

interface Props {
  rootId: string | null;
  repository: string | null;
  nodes: BranchNode[];
  events: FeedEvent[];
  branchStage: Map<string, Stage>;
  merges: MergeRow[];
  decisions: DecisionNode[];
  readiness: ReadinessReport | null;
  theme: Theme;
  onRunMigration: () => void;
  onOpenDecision: (mergeId: string) => void;
  onViewBranches: () => void;
  /** The live tree has not sent its first snapshot yet. */
  loading?: boolean;
}

/* Every number on this page is derived from props -- the live tree, the
   event feed, the merge log and the decision graph. Nothing is a placeholder:
   an empty project shows zeros and empty states, not sample data, because a
   plausible fake number is worse than an honest blank on a page whose whole
   job is to show what the agent actually did. */

const DAY_MS = 86_400_000;

type Tone = "ok" | "info" | "warn" | "bad" | "muted";

const TAG_TONE: Record<Tone, string> = {
  ok: "bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-400",
  info: "bg-indigo-100 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300",
  warn: "bg-amber-100 dark:bg-amber-950/60 text-amber-700 dark:text-amber-400",
  bad: "bg-rose-100 dark:bg-rose-950/60 text-rose-700 dark:text-rose-400",
  muted: "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300",
};

const DOT_TONE: Record<Tone, string> = {
  ok: "bg-emerald-500", info: "bg-indigo-500", warn: "bg-amber-500", bad: "bg-rose-500", muted: "bg-slate-400",
};

const NOTE_TONE: Record<Tone, string> = {
  ok: "text-emerald-600 dark:text-emerald-400",
  info: "text-blue-600 dark:text-blue-400",
  warn: "text-amber-600 dark:text-amber-400",
  bad: "text-rose-600 dark:text-rose-400",
  muted: "text-slate-500 dark:text-slate-400",
};

function stageTone(stage: Stage): Tone {
  if (stage === "error") return "bad";
  if (stage === "merged" || stage === "ingested") return "ok";
  if (stage === "abandoned" || stage === "reverted") return "warn";
  if (stage === "scored" || stage === "merging") return "info";
  return "muted";
}

function pct(x: number | null | undefined): string {
  return x === null || x === undefined ? "—" : `${Math.round(x * 100)}%`;
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function relativeTime(ms: number, now: number): string {
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** Re-render periodically so "x min ago" doesn't freeze. */
function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function OverviewPage({
  nodes, events, merges, decisions, readiness,
  onRunMigration, onOpenDecision, onViewBranches, loading = false,
}: Props) {
  const [activeTab, setActiveTab] = useState<"all" | "merges">("all");
  const [hoveredDay, setHoveredDay] = useState<number | null>(null);
  const now = useNow();
  const simulated = readiness?.overall === "simulated";

  // ── Branch counts, straight from the live tree (root excluded) ──
  const branchCounts = useMemo(() => {
    const children = nodes.filter((n) => n.parent_id !== null);
    const merged = children.filter((n) => n.status === "merged").length;
    const abandoned = children.filter((n) => n.status === "abandoned").length;
    // 'exploring' covers two very different states: still running, and
    // scored-but-never-merged. Splitting them is what makes a stuck run
    // visible instead of looking like live work.
    const evaluated = children.filter((n) => n.status === "exploring" && n.score !== null).length;
    const inProgress = children.filter((n) => n.status === "exploring" && n.score === null).length;
    const scored = children.filter((n) => n.score !== null).map((n) => n.score as number);
    const forkedThisWeek = children.filter(
      (n) => n.created_at && Date.parse(n.created_at) >= now - 7 * DAY_MS,
    ).length;
    return {
      total: children.length, merged, abandoned, evaluated, inProgress, forkedThisWeek,
      bestScore: scored.length ? Math.max(...scored) : null,
      avgScore: scored.length ? scored.reduce((a, b) => a + b, 0) / scored.length : null,
      scoredCount: scored.length,
    };
  }, [nodes, now]);

  // ── Decisions ──
  const liveDecisions = decisions.filter((d) => !d.reverted_at).length;
  const revertedDecisions = decisions.length - liveDecisions;
  const winnerScores = decisions
    .map((d) => d.winner_score)
    .filter((s): s is number => s !== null);
  const bestScore = winnerScores.length ? Math.max(...winnerScores) : branchCounts.bestScore;

  // ── Most recent thing that actually happened, across every source ──
  const lastActivity = useMemo(() => {
    const candidates: { ms: number; label: string }[] = [];
    const lastEvent = events[events.length - 1];
    if (lastEvent) {
      candidates.push({
        ms: lastEvent.ts * 1000,
        label: lastEvent.stage === "error" ? "Run failed" : capitalize(lastEvent.stage),
      });
    }
    for (const m of merges) {
      if (m.merged_at) candidates.push({ ms: Date.parse(m.merged_at), label: "Merge completed" });
      if (m.reverted_at) candidates.push({ ms: Date.parse(m.reverted_at), label: "Merge reverted" });
    }
    for (const n of nodes) {
      if (n.parent_id && n.created_at) candidates.push({ ms: Date.parse(n.created_at), label: "Branch forked" });
    }
    const valid = candidates.filter((c) => Number.isFinite(c.ms));
    return valid.length ? valid.reduce((a, b) => (b.ms > a.ms ? b : a)) : null;
  }, [events, merges, nodes]);

  // ── Feed, newest first ──
  const feed = useMemo(() => {
    const filtered = activeTab === "merges"
      ? events.filter((e) => e.stage === "merging" || e.stage === "merged" || e.stage === "reverted")
      : events;
    return filtered.slice(-6).reverse();
  }, [events, activeTab]);

  // ── Last 7 days, ending today, from real timestamps ──
  const activity = useMemo(() => {
    const today = startOfDay(now);
    const days = Array.from({ length: 7 }, (_, i) => {
      const start = today - (6 - i) * DAY_MS;
      const d = new Date(start);
      return {
        start,
        isToday: i === 6,
        label: d.toLocaleDateString([], { month: "short", day: "numeric" }),
        weekday: d.toLocaleDateString([], { weekday: "short" }),
        fullDate: d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }),
        forks: 0,
        merges: 0,
        reverts: 0,
      };
    });
    const bucket = (ms: number) => {
      if (!Number.isFinite(ms)) return null;
      const idx = Math.round((startOfDay(ms) - days[0].start) / DAY_MS);
      return idx >= 0 && idx < 7 ? days[idx] : null;
    };
    for (const n of nodes) {
      if (n.parent_id && n.created_at) { const b = bucket(Date.parse(n.created_at)); if (b) b.forks++; }
    }
    for (const m of merges) {
      if (m.merged_at) { const b = bucket(Date.parse(m.merged_at)); if (b) b.merges++; }
      if (m.reverted_at) { const b = bucket(Date.parse(m.reverted_at)); if (b) b.reverts++; }
    }
    const totalForks = days.reduce((sum, d) => sum + d.forks, 0);
    const totalMerges = days.reduce((sum, d) => sum + d.merges, 0);
    const totalReverts = days.reduce((sum, d) => sum + d.reverts, 0);
    const totalEvents = totalForks + totalMerges + totalReverts;
    const max = Math.max(1, ...days.flatMap((d) => [d.forks, d.merges, d.reverts]));
    const empty = totalEvents === 0;

    let peakDay = days[0];
    let peakTotal = 0;
    for (const d of days) {
      const total = d.forks + d.merges + d.reverts;
      if (total > peakTotal) {
        peakTotal = total;
        peakDay = d;
      }
    }

    return {
      days,
      max,
      empty,
      totalForks,
      totalMerges,
      totalReverts,
      totalEvents,
      peakDay,
      peakTotal,
    };
  }, [nodes, merges, now]);

  const statusSegments = [
    { label: "Merged", value: branchCounts.merged, color: "#10b981", dot: "bg-emerald-500" },
    { label: "In progress", value: branchCounts.inProgress, color: "#3b82f6", dot: "bg-blue-500" },
    { label: "Scored, not merged", value: branchCounts.evaluated, color: "#8b5cf6", dot: "bg-purple-500" },
    { label: "Abandoned", value: branchCounts.abandoned, color: "#f59e0b", dot: "bg-amber-500" },
  ];

  const dbCheck = readiness?.checks.find((c) => c.name === "CockroachDB");
  const latestMerge = merges[0]; // /api/merges returns newest first

  if (loading && nodes.length === 0) {
    return (
      <PageSkeleton
        variant="cards"
        caption="Connecting to the live branch stream and reading the project from CockroachDB…"
      />
    );
  }

  return (
    <div className="flex min-h-full flex-col gap-6 p-5 sm:p-6 lg:p-7 w-full">
      {simulated && (
        <div className="flex items-start gap-2.5 rounded-xl border border-amber-200 dark:border-amber-900/60 bg-amber-50 dark:bg-amber-950/30 px-4 py-3 text-[12.5px] text-amber-800 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
          <div>
            <span className="font-semibold">Simulated mode.</span>{" "}
            {readiness?.note ?? "Some dependencies are running as stand-ins."}
          </div>
        </div>
      )}

      {/* ── Hero ── */}
      <motion.div
        initial={{ opacity: 0, y: 15 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
        className="relative overflow-hidden rounded-2xl border border-indigo-100 dark:border-indigo-950/60 bg-gradient-to-br from-indigo-50/70 via-white to-blue-50/50 dark:from-indigo-950/20 dark:via-[#11141c] dark:to-blue-950/20 p-6 sm:p-8 shadow-sm"
      >
        <div className="absolute inset-0 opacity-[0.03] dark:opacity-[0.06] pointer-events-none bg-[radial-gradient(#4f46e5_1px,transparent_1px)] [background-size:16px_16px]" />

        <div className="relative z-10 flex flex-col lg:flex-row lg:items-center justify-between gap-8">
          <div className="max-w-3xl flex-1 space-y-3.5">
            <div className="inline-flex items-center gap-2 rounded-full border border-indigo-200/80 dark:border-indigo-800 bg-white/80 dark:bg-indigo-950/50 px-3 py-1 text-[11px] font-semibold text-indigo-700 dark:text-indigo-300 shadow-2xs backdrop-blur-xs">
              <Sparkles className="h-3.5 w-3.5 text-indigo-500 animate-pulse" />
              <span>Next-Gen Agent Memory Architecture</span>
            </div>

            <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight text-slate-900 dark:text-white leading-tight">
              Branchable Memory
            </h1>

            <p className="text-lg font-semibold text-indigo-600 dark:text-indigo-400">
              Version control for what an AI agent believes.
            </p>

            <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">
              Fork ideas. Test in parallel. Merge what works. Rollback anytime. Genuine copy-on-write isolation across CockroachDB vector indices.
            </p>

            <div className="flex flex-wrap items-center gap-3 pt-2">
              <button
                onClick={onRunMigration}
                className="group flex items-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 to-blue-600 hover:from-indigo-700 hover:to-blue-700 text-white font-semibold px-4.5 py-2.5 text-sm shadow-md shadow-indigo-500/25 transition-all hover:scale-102 hover:shadow-indigo-500/35"
              >
                <Plus className="h-4 w-4 transition-transform group-hover:rotate-90" />
                <span>New Migration</span>
              </button>

              <button
                onClick={onRunMigration}
                className="flex items-center gap-2 rounded-xl border border-slate-200 dark:border-slate-800 bg-white/90 dark:bg-slate-900/80 hover:bg-slate-50 dark:hover:bg-slate-800 text-slate-700 dark:text-slate-200 font-semibold px-4.5 py-2.5 text-sm shadow-2xs transition-colors"
              >
                <Download className="h-4 w-4 text-slate-400" />
                <span>Ingest Repository</span>
              </button>
            </div>
          </div>

          <div className="flex-none flex items-center justify-center lg:justify-end">
            <IsometricHeroGraphic />
          </div>
        </div>
      </motion.div>

      {/* ── KPI cards ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          icon={GitBranch}
          iconColor="text-indigo-600"
          iconBg="bg-indigo-50 dark:bg-indigo-950/60"
          label="Total Branches"
          value={branchCounts.total}
          note={`${branchCounts.forkedThisWeek} forked in the last 7 days`}
          tone="muted"
          delay={0.05}
        />
        <StatCard
          icon={Layers}
          iconColor="text-blue-600"
          iconBg="bg-blue-50 dark:bg-blue-950/60"
          label="Decisions"
          value={decisions.length}
          note={
            branchCounts.inProgress > 0
              ? `${branchCounts.inProgress} branch${branchCounts.inProgress === 1 ? "" : "es"} running now`
              : `${liveDecisions} live · ${revertedDecisions} reverted`
          }
          tone={branchCounts.inProgress > 0 ? "info" : "muted"}
          delay={0.1}
        />
        <StatCard
          icon={Trophy}
          iconColor="text-amber-500"
          iconBg="bg-amber-50 dark:bg-amber-950/60"
          label="Best Winner Score"
          value={pct(bestScore)}
          note={
            simulated
              ? "Simulated — not a test result"
              : branchCounts.scoredCount
                ? `avg ${pct(branchCounts.avgScore)} across ${branchCounts.scoredCount} scored`
                : "No branches scored yet"
          }
          tone={simulated ? "warn" : "muted"}
          delay={0.15}
        />
        <StatCard
          icon={Clock}
          iconColor="text-purple-600"
          iconBg="bg-purple-50 dark:bg-purple-950/60"
          label="Last Activity"
          value={lastActivity ? relativeTime(lastActivity.ms, now) : "—"}
          note={lastActivity?.label ?? "Nothing recorded yet"}
          tone={lastActivity?.label === "Run failed" ? "bad" : "muted"}
          delay={0.2}
        />
      </div>

      {/* ── Execution trace (live, follows the current run) ── */}
      <LiveExecutionTrace events={events} nodes={nodes} />

      {/* ── Feed + quick actions ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.25 }}
          className="lg:col-span-2 rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 shadow-xs"
        >
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">Live Activity Feed</h2>
            <div className="flex items-center gap-1 rounded-lg bg-slate-100 dark:bg-slate-800 p-0.5 text-[11px] font-medium text-slate-500">
              {(["all", "merges"] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`rounded-md px-2.5 py-0.5 capitalize transition-colors ${activeTab === tab ? "bg-white dark:bg-slate-700 text-slate-800 dark:text-white shadow-2xs font-semibold" : ""}`}
                >
                  {tab}
                </button>
              ))}
            </div>
          </div>

          {feed.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-slate-200 dark:border-slate-800 py-8 text-center">
              <Activity className="h-5 w-5 text-slate-300 dark:text-slate-600" />
              <p className="text-xs font-medium text-slate-500">
                {activeTab === "merges" ? "No merge events this session" : "No activity this session"}
              </p>
              <p className="text-[11px] text-slate-400">The feed fills in as soon as a migration or ingest runs.</p>
            </div>
          ) : (
            <div className="space-y-3">
              {feed.map((evt, idx) => {
                const tone = stageTone(evt.stage);
                return (
                  <div
                    key={`${evt.ts}-${idx}`}
                    className="flex items-center justify-between gap-3 rounded-xl border border-slate-100 dark:border-slate-800/80 bg-slate-50/50 dark:bg-slate-900/40 px-3.5 py-2.5 text-xs transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/60"
                  >
                    <div className="flex min-w-0 items-center gap-3">
                      <span className="whitespace-nowrap font-mono text-slate-400 text-[11px]">
                        {new Date(evt.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                      </span>
                      <span className={`h-2 w-2 flex-none rounded-full ${DOT_TONE[tone]}`} />
                      <span className={`min-w-0 break-words font-medium ${tone === "bad" ? "text-rose-700 dark:text-rose-400" : "text-slate-700 dark:text-slate-300"}`}>
                        {evt.message || `Branch ${evt.branch_id ? evt.branch_id.slice(0, 8) : "main"}: ${evt.stage}`}
                      </span>
                    </div>
                    <span className={`flex-none whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-bold ${TAG_TONE[tone]}`}>
                      {evt.stage === "error" ? "Error" : capitalize(evt.stage)}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.3 }}
          className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 shadow-xs flex flex-col justify-between"
        >
          <div>
            <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200 mb-4">Quick Actions</h2>
            <div className="space-y-2.5">
              <QuickAction
                primary
                title="Start New Migration"
                subtitle="Explore hypotheses in parallel"
                icon={<Play className="h-3.5 w-3.5 fill-current" />}
                onClick={onRunMigration}
              />
              <QuickAction
                title="Ingest GitHub Repository"
                subtitle="Clone, chunk and embed codebase"
                icon={<Download className="h-3.5 w-3.5" />}
                onClick={onRunMigration}
              />
              <QuickAction
                title="View Branch Tree"
                subtitle="Explore all branches and lineage"
                icon={<GitBranch className="h-3.5 w-3.5" />}
                onClick={onViewBranches}
              />
              {latestMerge && (
                <QuickAction
                  title="Open Latest Decision"
                  subtitle={latestMerge.winner_hypothesis ?? "Why the winner won"}
                  icon={<Trophy className="h-3.5 w-3.5" />}
                  onClick={() => onOpenDecision(latestMerge.merge_id)}
                />
              )}
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between text-[11px] text-slate-400">
            <span className="flex items-center gap-1.5" title={dbCheck?.detail}>
              {dbCheck?.status === "ready" ? (
                <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" />
              ) : (
                <ShieldAlert className="h-3.5 w-3.5 text-amber-500" />
              )}
              CockroachDB {dbCheck ? dbCheck.status.replace(/_/g, " ") : "checking…"}
            </span>
            <span className="font-mono">v0.1.0</span>
          </div>
        </motion.div>
      </div>

      {/* ── Charts ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.35 }}
          className="lg:col-span-2 rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 sm:p-6 shadow-xs relative overflow-hidden flex flex-col justify-between"
        >
          {/* Subtle decorative background glow */}
          <div className="absolute -top-16 -right-16 w-52 h-52 bg-gradient-to-br from-indigo-500/10 via-blue-500/5 to-transparent rounded-full blur-3xl pointer-events-none" />

          <div>
            {/* Header */}
            <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-tr from-indigo-500/15 via-blue-500/10 to-indigo-500/5 border border-indigo-500/20 text-indigo-600 dark:text-indigo-400 shadow-2xs">
                  <Activity className="h-4.5 w-4.5" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-bold text-slate-900 dark:text-slate-100 tracking-tight">
                      Branch Activity
                    </h2>
                    <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800/80 text-slate-500 dark:text-slate-400 border border-slate-200/60 dark:border-slate-700/60">
                      Last 7 Days
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                    Copy-on-write forks, serializable merges, and AOST rollbacks
                  </p>
                </div>
              </div>

              {/* Legend with live counts */}
              <div className="flex items-center gap-2">
                <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-indigo-50/80 dark:bg-indigo-950/40 border border-indigo-200/60 dark:border-indigo-800/40 text-xs font-medium text-indigo-700 dark:text-indigo-300">
                  <span className="h-2 w-2 rounded-full bg-indigo-500 shadow-xs shadow-indigo-500/50" />
                  <span>Forks</span>
                  <span className="font-bold ml-1 text-indigo-900 dark:text-indigo-100 font-mono text-[11px]">{activity.totalForks}</span>
                </div>
                <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-cyan-50/80 dark:bg-cyan-950/40 border border-cyan-200/60 dark:border-cyan-800/40 text-xs font-medium text-cyan-700 dark:text-cyan-300">
                  <span className="h-2 w-2 rounded-full bg-cyan-500 shadow-xs shadow-cyan-500/50" />
                  <span>Merges</span>
                  <span className="font-bold ml-1 text-cyan-900 dark:text-cyan-100 font-mono text-[11px]">{activity.totalMerges}</span>
                </div>
                <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-amber-50/80 dark:bg-amber-950/40 border border-amber-200/60 dark:border-amber-800/40 text-xs font-medium text-amber-700 dark:text-amber-300">
                  <span className="h-2 w-2 rounded-full bg-amber-500 shadow-xs shadow-amber-500/50" />
                  <span>Reverts</span>
                  <span className="font-bold ml-1 text-amber-900 dark:text-amber-100 font-mono text-[11px]">{activity.totalReverts}</span>
                </div>
              </div>
            </div>

            {/* Chart Area */}
            <div className="relative pt-6 pb-2">
              {/* Background horizontal dashed gridlines */}
              <div className="absolute inset-x-0 top-6 bottom-11 flex flex-col justify-between pointer-events-none select-none px-2">
                <div className="w-full flex items-center justify-between border-b border-dashed border-slate-200/70 dark:border-slate-800/70">
                  <span className="text-[10px] font-mono text-slate-400/80 dark:text-slate-600 -translate-y-2.5">{activity.max}</span>
                </div>
                <div className="w-full flex items-center justify-between border-b border-dashed border-slate-200/50 dark:border-slate-800/50">
                  <span className="text-[10px] font-mono text-slate-400/60 dark:text-slate-600 -translate-y-2.5">{Math.max(1, Math.round(activity.max / 2))}</span>
                </div>
                <div className="w-full flex items-center justify-between border-b border-slate-200 dark:border-slate-800">
                  <span className="text-[10px] font-mono text-slate-400/50 dark:text-slate-600 -translate-y-2.5">0</span>
                </div>
              </div>

              {/* Day Columns */}
              <div className="relative h-44 flex items-end justify-between gap-1.5 sm:gap-2.5 z-10 px-2 sm:px-4">
                {activity.days.map((d, idx) => {
                  const dayTotal = d.forks + d.merges + d.reverts;
                  const isHovered = hoveredDay === idx;
                  return (
                    <div
                      key={d.start}
                      onMouseEnter={() => setHoveredDay(idx)}
                      onMouseLeave={() => setHoveredDay(null)}
                      className="group/col relative flex-1 flex flex-col items-center justify-end h-full cursor-pointer"
                    >
                      {/* Floating Glassmorphic Tooltip */}
                      {isHovered && (
                        <div className="absolute bottom-full mb-3 left-1/2 -translate-x-1/2 z-30 pointer-events-none whitespace-nowrap">
                          <motion.div
                            initial={{ opacity: 0, y: 6, scale: 0.95 }}
                            animate={{ opacity: 1, y: 0, scale: 1 }}
                            className="rounded-xl border border-slate-700/80 bg-slate-900/95 dark:bg-slate-950/95 p-2.5 text-white shadow-xl shadow-slate-950/30 backdrop-blur-md"
                          >
                            <div className="flex items-center justify-between gap-2 border-b border-slate-800 pb-1.5 mb-1.5">
                              <span className="font-semibold text-xs text-slate-200">{d.fullDate}</span>
                              {d.isToday && (
                                <span className="text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                                  Today
                                </span>
                              )}
                            </div>
                            <div className="space-y-1 text-[11px]">
                              <div className="flex items-center justify-between gap-3 text-slate-300">
                                <span className="flex items-center gap-1.5">
                                  <span className="h-2 w-2 rounded-full bg-indigo-500" /> Forks
                                </span>
                                <span className="font-mono font-bold text-white">{d.forks}</span>
                              </div>
                              <div className="flex items-center justify-between gap-3 text-slate-300">
                                <span className="flex items-center gap-1.5">
                                  <span className="h-2 w-2 rounded-full bg-cyan-500" /> Merges
                                </span>
                                <span className="font-mono font-bold text-white">{d.merges}</span>
                              </div>
                              <div className="flex items-center justify-between gap-3 text-slate-300">
                                <span className="flex items-center gap-1.5">
                                  <span className="h-2 w-2 rounded-full bg-amber-500" /> Reverts
                                </span>
                                <span className="font-mono font-bold text-white">{d.reverts}</span>
                              </div>
                              <div className="border-t border-slate-800 pt-1 mt-1 flex items-center justify-between text-slate-400">
                                <span>Total events</span>
                                <span className="font-mono font-bold text-indigo-400">{dayTotal}</span>
                              </div>
                            </div>
                          </motion.div>
                        </div>
                      )}

                      {/* Day Column Hover Capsule / Track */}
                      <div className={`w-full max-w-[56px] h-32 flex items-end justify-center gap-1 sm:gap-1.5 px-1 py-1 rounded-xl transition-all duration-200 relative ${
                        isHovered
                          ? "bg-indigo-500/[0.08] dark:bg-indigo-500/[0.12] ring-1 ring-indigo-500/20"
                          : "hover:bg-slate-100/60 dark:hover:bg-slate-800/40"
                      }`}>
                        {/* Background track slots for structured look */}
                        <div className="absolute inset-x-1 bottom-1 top-1 bg-slate-100/50 dark:bg-slate-800/30 rounded-lg pointer-events-none" />

                        {/* Bars */}
                        <ActivityBar
                          value={d.forks}
                          max={activity.max}
                          gradient="from-indigo-600 via-indigo-500 to-violet-400"
                          glow="shadow-[0_2px_8px_rgba(99,102,241,0.4)]"
                          title={`Forks: ${d.forks}`}
                        />
                        <ActivityBar
                          value={d.merges}
                          max={activity.max}
                          gradient="from-blue-600 via-cyan-500 to-sky-300"
                          glow="shadow-[0_2px_8px_rgba(6,182,212,0.4)]"
                          title={`Merges: ${d.merges}`}
                        />
                        <ActivityBar
                          value={d.reverts}
                          max={activity.max}
                          gradient="from-rose-600 via-amber-500 to-amber-300"
                          glow="shadow-[0_2px_8px_rgba(245,158,11,0.4)]"
                          title={`Reverts: ${d.reverts}`}
                        />
                      </div>

                      {/* X-axis date label */}
                      <div className="mt-2.5 flex flex-col items-center">
                        <span className={`text-[10px] uppercase font-bold tracking-wider ${
                          d.isToday ? "text-indigo-600 dark:text-indigo-400" : "text-slate-400 dark:text-slate-500"
                        }`}>
                          {d.weekday}
                        </span>
                        <span className={`whitespace-nowrap text-[11px] font-semibold transition-colors ${
                          d.isToday
                            ? "text-indigo-600 dark:text-indigo-400 font-bold"
                            : isHovered
                              ? "text-slate-900 dark:text-slate-100"
                              : "text-slate-500 dark:text-slate-400"
                        }`}>
                          {d.label}
                        </span>
                        {d.isToday && (
                          <span className="h-1 w-1 rounded-full bg-indigo-500 mt-0.5" />
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Card Footer Insight Strip */}
          <div className="mt-4 pt-3.5 border-t border-slate-100 dark:border-slate-800/80 flex flex-wrap items-center justify-between gap-3 text-xs">
            <div className="flex items-center gap-2 text-slate-600 dark:text-slate-400">
              <Sparkles className="h-4 w-4 text-indigo-500 flex-none" />
              <span>
                {activity.peakTotal > 0 ? (
                  <>
                    Peak velocity on <strong className="text-slate-900 dark:text-slate-100">{activity.peakDay.label}</strong> with <strong className="text-indigo-600 dark:text-indigo-400 font-semibold">{activity.peakTotal} events</strong> ({activity.peakDay.forks} forks, {activity.peakDay.merges} merges).
                  </>
                ) : (
                  "No branch activity recorded yet."
                )}
              </span>
            </div>
            <div className="flex items-center gap-2 text-[11px] font-medium text-slate-400 dark:text-slate-500">
              <span className="inline-flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                Live Ingestion
              </span>
              <span>·</span>
              <span className="font-mono text-slate-700 dark:text-slate-300 font-semibold">
                {activity.totalEvents} Total Events
              </span>
            </div>
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.4 }}
          className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 sm:p-6 shadow-xs flex flex-col justify-between relative overflow-hidden"
        >
          {/* Subtle background glow */}
          <div className="absolute -top-16 -right-16 w-40 h-40 bg-gradient-to-br from-emerald-500/10 via-teal-500/5 to-transparent rounded-full blur-3xl pointer-events-none" />

          <div>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2.5">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
                  <Layers className="h-4 w-4" />
                </div>
                <div>
                  <h2 className="text-sm font-bold text-slate-900 dark:text-slate-100 tracking-tight">Status Distribution</h2>
                  <p className="text-[11px] text-slate-500 dark:text-slate-400">Total lineage breakdown</p>
                </div>
              </div>
              <span className="text-[11px] font-mono px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 font-semibold">
                {branchCounts.total} total
              </span>
            </div>

            <div className="flex items-center justify-center my-4">
              <Donut segments={statusSegments} total={branchCounts.total} />
            </div>

            <div className="space-y-2 text-xs">
              {statusSegments.map((s) => (
                <div key={s.label} className="flex items-center justify-between p-1.5 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors">
                  <span className="flex items-center gap-2 text-slate-600 dark:text-slate-400">
                    <span className={`h-2.5 w-2.5 rounded-full ${s.dot} shadow-xs`} /> {s.label}
                  </span>
                  <span className="font-semibold font-mono text-slate-900 dark:text-slate-100">{s.value}</span>
                </div>
              ))}
            </div>
          </div>

          {branchCounts.evaluated > 0 && branchCounts.inProgress === 0 && (
            <p className="mt-3 pt-3 border-t border-slate-100 dark:border-slate-800/80 text-[11px] leading-relaxed text-slate-400">
              {branchCounts.evaluated} scored branch{branchCounts.evaluated === 1 ? " was" : "es were"} never merged, usually from a run that failed at the merge step.
            </p>
          )}
        </motion.div>
      </div>
    </div>
  );
}

function ActivityBar({
  value,
  max,
  gradient,
  glow,
  title,
}: {
  value: number;
  max: number;
  gradient: string;
  glow: string;
  title: string;
}) {
  const heightPct = value ? Math.max(8, (value / max) * 100) : 0;
  return (
    <div className="relative h-full flex items-end justify-center w-2.5 sm:w-3.5 z-10" title={title}>
      {value > 0 ? (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: `${heightPct}%`, opacity: 1 }}
          transition={{ duration: 0.4, ease: "easeOut" }}
          className={`w-full rounded-t-md rounded-b-xs bg-gradient-to-t ${gradient} ${glow} transition-all duration-200 hover:brightness-110`}
        />
      ) : (
        <div className="h-1 w-2 rounded-full bg-slate-200/80 dark:bg-slate-700/80 mb-0.5" />
      )}
    </div>
  );
}

function Donut({
  segments, total,
}: {
  segments: { label: string; value: number; color: string }[];
  total: number;
}) {
  const r = 38;
  const circumference = 2 * Math.PI * r;
  const visible = segments.filter((s) => s.value > 0);
  const gap = visible.length > 1 ? 1.5 : 0;
  let offset = 0;
  return (
    <div className="relative flex items-center justify-center">
      <svg className="w-36 h-36 -rotate-90" viewBox="0 0 100 100" role="img" aria-label={`${total} branches by status`}>
        <circle cx="50" cy="50" r={r} fill="transparent" stroke="currentColor" strokeWidth="11" className="text-slate-100 dark:text-slate-800" />
        {total > 0 && visible.map((s) => {
          const len = (s.value / total) * circumference;
          const el = (
            <circle
              key={s.label}
              cx="50" cy="50" r={r} fill="transparent" stroke={s.color} strokeWidth="11"
              strokeDasharray={`${Math.max(0, len - gap)} ${circumference}`}
              strokeDashoffset={-offset}
            >
              <title>{`${s.label}: ${s.value}`}</title>
            </circle>
          );
          offset += len;
          return el;
        })}
      </svg>
      <div className="absolute flex flex-col items-center justify-center text-center">
        <span className="text-2xl font-extrabold text-slate-900 dark:text-white leading-none">{total}</span>
        <span className="text-[10px] font-medium text-slate-400 uppercase tracking-wider mt-0.5">Total</span>
      </div>
    </div>
  );
}

function QuickAction({
  title, subtitle, icon, onClick, primary = false,
}: {
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  onClick: () => void;
  primary?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full group flex items-center justify-between gap-3 rounded-xl border p-3 text-left transition-all ${
        primary
          ? "border-indigo-100 dark:border-indigo-950 bg-indigo-50/50 dark:bg-indigo-950/30 hover:bg-indigo-50 dark:hover:bg-indigo-950/60 hover:border-indigo-300"
          : "border-slate-200 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-900/40 hover:bg-slate-100 dark:hover:bg-slate-800 hover:border-slate-300"
      }`}
    >
      <div className="min-w-0">
        <div className={`text-xs font-bold ${primary ? "text-indigo-950 dark:text-indigo-200" : "text-slate-800 dark:text-slate-200"}`}>{title}</div>
        <div className={`truncate text-[11px] ${primary ? "text-indigo-600/80 dark:text-indigo-400" : "text-slate-500"}`}>{subtitle}</div>
      </div>
      <div
        className={`flex h-7 w-7 flex-none items-center justify-center rounded-lg shadow-2xs group-hover:translate-x-0.5 transition-transform ${
          primary
            ? "bg-indigo-600 text-white"
            : "border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-300"
        }`}
      >
        {icon}
      </div>
    </button>
  );
}

function StatCard({
  icon: Icon, iconColor, iconBg, label, value, note, tone, delay,
}: {
  icon: typeof GitBranch;
  iconColor: string;
  iconBg: string;
  label: string;
  value: string | number;
  note: string;
  tone: Tone;
  delay: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay }}
      className="group relative rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-4.5 shadow-xs hover:shadow-md hover:border-indigo-200 dark:hover:border-indigo-900 transition-all duration-200"
    >
      <div className="flex items-center justify-between mb-2.5">
        <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">{label}</span>
        <div className={`flex h-8 w-8 items-center justify-center rounded-xl ${iconBg} ${iconColor} shadow-2xs transition-transform group-hover:scale-110`}>
          <Icon className="h-4 w-4" />
        </div>
      </div>
      <div className="text-2xl font-extrabold text-slate-900 dark:text-white tracking-tight">
        {typeof value === "number" ? <CountUp value={value} /> : value}
      </div>
      <div className={`mt-1 text-[11px] font-semibold ${NOTE_TONE[tone]}`}>{note}</div>
    </motion.div>
  );
}

function IsometricHeroGraphic() {
  return (
    <div className="relative w-64 h-56 flex items-center justify-center">
      <div className="absolute inset-0 bg-gradient-to-tr from-indigo-500/20 via-blue-500/15 to-cyan-400/20 rounded-full blur-2xl" />
      <svg viewBox="0 0 280 240" className="w-full h-full relative z-10 drop-shadow-xl" fill="none">
        <path d="M70 120 L140 60 L210 120 L140 180 Z" stroke="rgba(99, 102, 241, 0.4)" strokeWidth="1.5" strokeDasharray="3 3" style={{ animation: "bm-dash 1.6s linear infinite" }} />
        <path d="M140 60 L140 180" stroke="rgba(59, 130, 246, 0.4)" strokeWidth="1.5" strokeDasharray="3 3" style={{ animation: "bm-dash 1.6s linear infinite" }} />
        <path d="M70 120 L210 120" stroke="rgba(16, 185, 129, 0.4)" strokeWidth="1.5" strokeDasharray="3 3" style={{ animation: "bm-dash 1.6s linear infinite" }} />

        <g transform="translate(140, 50)">
          <g className="bm-float" style={{ animationDelay: "0.0s" }}>
          <polygon points="0,-18 20,-7 0,4 -20,-7" fill="#6366f1" />
          <polygon points="-20,-7 0,4 0,26 -20,15" fill="#4f46e5" />
          <polygon points="0,4 20,-7 20,15 0,26" fill="#4338ca" />
          <text x="0" y="-23" textAnchor="middle" fill="#4f46e5" fontSize="10" fontWeight="bold">Explore</text>
          </g>
        </g>

        <g transform="translate(65, 115)">
          <g className="bm-float" style={{ animationDelay: "0.4s" }}>
          <polygon points="0,-18 20,-7 0,4 -20,-7" fill="#3b82f6" />
          <polygon points="-20,-7 0,4 0,26 -20,15" fill="#2563eb" />
          <polygon points="0,4 20,-7 20,15 0,26" fill="#1d4ed8" />
          <text x="-12" y="-23" textAnchor="middle" fill="#2563eb" fontSize="10" fontWeight="bold">Fork</text>
          </g>
        </g>

        <g transform="translate(140, 115)">
          <g className="bm-float" style={{ animationDelay: "0.8s" }}>
          <polygon points="0,-22 25,-8 0,6 -25,-8" fill="#818cf8" />
          <polygon points="-25,-8 0,6 0,34 -25,20" fill="#6366f1" />
          <polygon points="0,6 25,-8 25,20 0,34" fill="#4f46e5" />
          <text x="0" y="50" textAnchor="middle" fill="#4338ca" fontSize="10" fontWeight="bold">Score</text>
          </g>
        </g>

        <g transform="translate(215, 115)">
          <g className="bm-float" style={{ animationDelay: "1.2s" }}>
          <polygon points="0,-18 20,-7 0,4 -20,-7" fill="#10b981" />
          <polygon points="-20,-7 0,4 0,26 -20,15" fill="#059669" />
          <polygon points="0,4 20,-7 20,15 0,26" fill="#047857" />
          <text x="12" y="-23" textAnchor="middle" fill="#059669" fontSize="10" fontWeight="bold">Merge</text>
          </g>
        </g>

        <g transform="translate(140, 180)">
          <g className="bm-float" style={{ animationDelay: "1.6s" }}>
          <polygon points="0,-18 20,-7 0,4 -20,-7" fill="#06b6d4" />
          <polygon points="-20,-7 0,4 0,26 -20,15" fill="#0891b2" />
          <polygon points="0,4 20,-7 20,15 0,26" fill="#0e7490" />
          <text x="0" y="42" textAnchor="middle" fill="#0891b2" fontSize="10" fontWeight="bold">Revert</text>
          </g>
        </g>
      </svg>
    </div>
  );
}
