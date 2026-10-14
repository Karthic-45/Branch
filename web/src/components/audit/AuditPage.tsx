import { useEffect, useState, useMemo } from "react";
import { motion } from "framer-motion";
import {
  ScrollText, ChevronRight,
  ChevronLeft, Filter, Lock,
  Sparkles
} from "lucide-react";
import type { DecisionNode, FeedEvent, McpTools, ProjectDeployment } from "../../lib/types";
import { fetchMcpTools } from "../../lib/api";
import { JudgeMode } from "../JudgeMode";
import { TableRowsSkeleton } from "../common/Loading";
import type { Section } from "../layout/AppShell";

interface Props {
  decisions: DecisionNode[];
  deployments: ProjectDeployment[];
  events: FeedEvent[];
  onNavigate?: (section: Section) => void;
  /** Decisions or deployments are still being fetched. */
  loading?: boolean;
}

export function AuditPage({ decisions, deployments, events, onNavigate, loading = false }: Props) {
  const [activeTab, setActiveTab] = useState<"audit" | "judge">("audit");
  const [filter, setFilter] = useState("all");
  const [walkthroughStep, setWalkthroughStep] = useState(2);

  const walkthroughClaims = [
    {
      step: 1,
      title: "Claim 0: Append-Only Traps",
      desc: "Traditional agent memory accumulates disproved beliefs that poison subsequent reasoning steps.",
      action: "Next",
    },
    {
      step: 2,
      title: "Claim 1: Isolation",
      desc: "Verify that branches cannot see each other's secrets. Same cluster, zero cross-lineage leaks.",
      action: "Next",
    },
    {
      step: 3,
      title: "Claim 2: Copy-on-Write",
      desc: "Forking 50 hypotheses costs 50 pointer inserts. Zero memory rows are copied.",
      action: "Next",
    },
    {
      step: 4,
      title: "Claim 3: Temporal Immutability",
      desc: "AS OF SYSTEM TIME lets auditors read historic belief states exactly as they were prior to promotion.",
      action: "Next",
    },
    {
      step: 5,
      title: "Claim 4: CI Ground Truth",
      desc: "Isolated CodeBuild containers run real pytests to score hypotheses before serializable merge.",
      action: "Finish Tour",
    },
  ];

  const currentClaim = walkthroughClaims[walkthroughStep - 1] ?? walkthroughClaims[1];

  const [mcp, setMcp] = useState<McpTools | null>(null);
  const [mcpError, setMcpError] = useState("");
  useEffect(() => {
    fetchMcpTools().then(setMcp).catch((e) => setMcpError(e instanceof Error ? e.message : String(e)));
  }, []);

  type Kind = "pipeline" | "decision" | "deployment";
  type Tone = "ok" | "warn" | "bad" | "info" | "muted";
  const realLogs = useMemo(() => {
    const list: Array<{ ts: number | null; kind: Kind; action: string; target: string; detail: string; status: string; tone: Tone }> = [];
    const at = (iso: string | null | undefined) => (iso ? new Date(iso).getTime() : null);

    // Live pipeline events: held in the backend's memory, cleared on restart.
    events.forEach((ev) => {
      list.push({
        ts: ev.ts * 1000,
        kind: "pipeline",
        action: ev.stage.replace(/_/g, " "),
        target: ev.branch_id ? ev.branch_id.slice(0, 8) : "run",
        detail: ev.message,
        status: ev.stage === "error" ? "Error" : ev.stage === "merged" ? "Merged" : ev.stage === "abandoned" ? "Abandoned" : "Recorded",
        tone: ev.stage === "error" ? "bad" : ev.stage === "merged" ? "ok" : ev.stage === "abandoned" ? "warn" : "muted",
      });
    });

    // Decisions: durable, from merge_log + decision_rationales.
    decisions.forEach((d) => {
      list.push({
        ts: at(d.merged_at),
        kind: "decision",
        action: d.tie_detected ? "merge (tie-break)" : "merge",
        target: d.merge_id.slice(0, 8),
        detail: `${d.winner_hypothesis ?? d.winner_branch_id.slice(0, 8)}${d.winner_score !== null ? ` · ${Math.round(d.winner_score * 100)}%` : ""}`,
        status: d.reverted_at ? "Later reverted" : "Live",
        tone: d.reverted_at ? "warn" : "ok",
      });
      if (d.reverted_at) {
        list.push({
          ts: at(d.reverted_at),
          kind: "decision",
          action: "revert",
          target: d.merge_id.slice(0, 8),
          detail: "Parent restored to its pre-merge belief snapshot",
          status: "Reverted",
          tone: "warn",
        });
      }
    });

    // Deployments: durable, from the deployments table.
    deployments.forEach((dep) => {
      const d = dep.deployment;
      list.push({
        ts: at(d.started_at),
        kind: "deployment",
        action: `deploy → ${d.environment}`,
        target: d.id.slice(0, 8),
        detail: d.detail ?? dep.winner_hypothesis ?? d.merge_id.slice(0, 8),
        status: d.status.replace(/_/g, " "),
        tone: d.status === "healthy" ? "ok" : d.status === "failed" ? "bad" : d.status === "rolled_back" ? "warn" : "info",
      });
      if (d.rolled_back_at) {
        list.push({
          ts: at(d.rolled_back_at),
          kind: "deployment",
          action: "rollback",
          target: d.id.slice(0, 8),
          detail: d.environment,
          status: "Rolled back",
          tone: "warn",
        });
      }
    });

    list.sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0));
    return list;
  }, [events, decisions, deployments]);

  const filteredLogs = realLogs.filter((l) => {
    if (filter === "errors") return l.tone === "bad";
    return filter === "all" || l.kind === filter;
  });

  return (
    <div className="p-5 sm:p-6 lg:p-7 w-full space-y-6">
      {/* Top Header with Tabs & Start Tour Button (Screen 10) */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-slate-200/80 dark:border-slate-800">
        <div>
          <h1 className="text-xl sm:text-2xl font-extrabold text-slate-900 dark:text-white">
            Audit Page &amp; Judge Mode
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Recorded pipeline events, decisions and deployments, plus a guided walkthrough of the system's claims.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {/* Subtabs matching Screen 10 */}
          <div className="flex items-center rounded-xl bg-slate-100 dark:bg-slate-800 p-1 text-xs font-semibold">
            <button
              onClick={() => setActiveTab("audit")}
              className={`rounded-lg px-3 py-1.5 transition-colors ${
                activeTab === "audit"
                  ? "bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-300 shadow-2xs font-bold"
                  : "text-slate-500 hover:text-slate-900 dark:hover:text-slate-100"
              }`}
            >
              Audit Page
            </button>
            <button
              onClick={() => setActiveTab("judge")}
              className={`rounded-lg px-3 py-1.5 transition-colors ${
                activeTab === "judge"
                  ? "bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-300 shadow-2xs font-bold"
                  : "text-slate-500 hover:text-slate-900 dark:hover:text-slate-100"
              }`}
            >
              Judge Mode
            </button>
          </div>

          <button
            onClick={() => setActiveTab("judge")}
            className="flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-indigo-600 to-blue-600 hover:from-indigo-700 hover:to-blue-700 text-white font-semibold px-3.5 py-1.5 text-xs shadow-md shadow-indigo-500/25 transition-transform active:scale-95"
          >
            <Sparkles className="h-3.5 w-3.5" />
            <span>Start Tour</span>
          </button>
        </div>
      </div>

      {activeTab === "audit" ? (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
          {/* Left 2 Cols: MCP Audit Log */}
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="lg:col-span-2 rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-6 shadow-xs space-y-4"
          >
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-100 dark:border-slate-800">
              <div className="flex items-center gap-2">
                <ScrollText className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />
                <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">
                  Audit Trail
                </h2>
                <span className="rounded-full bg-slate-100 dark:bg-slate-800 px-2 py-0.5 text-[10px] font-bold text-slate-600 dark:text-slate-300">
                  {realLogs.length} entries
                </span>
              </div>

              {/* Filter Dropdown */}
              <div className="flex items-center gap-2 text-xs">
                <Filter className="h-3.5 w-3.5 text-slate-400" />
                <select
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-2.5 py-1 text-xs font-semibold text-slate-700 dark:text-slate-300"
                >
                  <option value="all">All entries</option>
                  <option value="pipeline">Pipeline events</option>
                  <option value="decision">Decisions</option>
                  <option value="deployment">Deployments</option>
                  <option value="errors">Errors only</option>
                </select>
              </div>
            </div>

            {/* Log Entries Table matching Screen 10 */}
            <div className="overflow-x-auto rounded-xl border border-slate-100 dark:border-slate-800">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 dark:bg-slate-900/60 text-slate-400 font-semibold border-b border-slate-100 dark:border-slate-800">
                  <tr>
                    <th className="py-2.5 px-3">Time</th>
                    <th className="py-2.5 px-3">Event</th>
                    <th className="py-2.5 px-3">Subject</th>
                    <th className="py-2.5 px-3 text-right">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800 font-medium">
                  {loading && realLogs.length === 0 ? (
                    <TableRowsSkeleton cols={4} rows={7} />
                  ) : filteredLogs.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="py-8 text-center text-slate-400">
                        {realLogs.length === 0
                          ? "Nothing recorded yet. Decisions and deployments appear here permanently; pipeline events appear while the backend is running."
                          : "No entries match this filter."}
                      </td>
                    </tr>
                  ) : (
                    filteredLogs.map((l, i) => (
                      <tr
                        key={i}
                        className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors align-top"
                      >
                        <td className="py-2.5 px-3 font-mono text-slate-400 text-[11px] whitespace-nowrap">
                          {l.ts !== null ? new Date(l.ts).toLocaleString([], { dateStyle: "short", timeStyle: "medium" }) : "—"}
                        </td>
                        <td className="py-2.5 px-3 whitespace-nowrap">
                          <span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">{l.kind}</span>
                          <div className="font-mono text-indigo-600 dark:text-indigo-400 font-bold">{l.action}</div>
                        </td>
                        <td className="py-2.5 px-3 text-slate-600 dark:text-slate-300 text-[11px]">
                          <span className="font-mono">{l.target}</span>
                          <div className="max-w-md break-words text-slate-500">{l.detail}</div>
                        </td>
                        <td className="py-2.5 px-3 text-right whitespace-nowrap">
                          <span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-bold ${STATUS_TONE[l.tone]}`}>
                            {l.status}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <div className="pt-2 flex items-center justify-between text-[11.5px] text-slate-400">
              <span className="flex items-center gap-1.5" title={mcp?.tools.map((t) => t.name).join(", ")}>
                <Lock className="h-3.5 w-3.5 text-indigo-500" />
                {mcp
                  ? `MCP server "${mcp.server}": ${mcp.total} tools for external agents, ${mcp.read_only} read-only, ${mcp.may_write} able to write.`
                  : mcpError
                    ? `MCP tool list unavailable: ${mcpError}`
                    : "Reading MCP tool list…"}
              </span>
              <span className="font-mono text-[10.5px]">read from the server</span>
            </div>
          </motion.div>

          {/* Right 1 Col: Judge Mode Walkthrough Card (Screen 10) */}
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-6 shadow-xs space-y-4"
          >
            <div className="flex items-center justify-between pb-3 border-b border-slate-100 dark:border-slate-800">
              <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">
                Judge Mode Walkthrough
              </h2>
              <span className="rounded-full bg-indigo-50 dark:bg-indigo-950/60 px-2 py-0.5 font-mono text-[11px] font-bold text-indigo-700 dark:text-indigo-300">
                Step {walkthroughStep} of {walkthroughClaims.length}
              </span>
            </div>

            {/* Stepper Progress Dots */}
            <div className="flex items-center gap-1.5 py-1">
              {walkthroughClaims.map((c) => (
                <button
                  key={c.step}
                  onClick={() => setWalkthroughStep(c.step)}
                  className={`h-1.5 flex-1 rounded-full transition-all ${
                    c.step === walkthroughStep
                      ? "bg-indigo-600 dark:bg-indigo-400"
                      : c.step < walkthroughStep
                      ? "bg-indigo-300 dark:bg-indigo-800"
                      : "bg-slate-200 dark:bg-slate-800"
                  }`}
                />
              ))}
            </div>

            {/* Claim Card Content */}
            <div className="rounded-xl border border-indigo-100 dark:border-indigo-950/60 bg-indigo-50/50 dark:bg-indigo-950/30 p-5 space-y-3">
              <div className="text-xs font-bold uppercase tracking-wider text-indigo-700 dark:text-indigo-400">
                {currentClaim.title}
              </div>
              <p className="text-sm font-semibold text-slate-900 dark:text-white leading-relaxed">
                {currentClaim.desc}
              </p>
            </div>

            {/* Action Buttons */}
            <div className="flex items-center justify-between pt-2">
              <button
                onClick={() => setWalkthroughStep((s) => Math.max(1, s - 1))}
                disabled={walkthroughStep === 1}
                className="flex items-center gap-1 rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-600 dark:text-slate-400 disabled:opacity-40 hover:bg-slate-50"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                <span>Prev</span>
              </button>

              <button
                onClick={() => {
                  if (walkthroughStep < walkthroughClaims.length) {
                    setWalkthroughStep((s) => s + 1);
                  } else {
                    setActiveTab("judge");
                  }
                }}
                className="flex items-center gap-1 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-semibold px-4 py-2 text-xs shadow-xs transition-transform active:scale-95"
              >
                <span>{currentClaim.action}</span>
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </motion.div>
        </div>
      ) : (
        <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-6 shadow-xs">
          <JudgeMode onNavigate={(s) => onNavigate?.(s)} />
        </div>
      )}
    </div>
  );
}

const STATUS_TONE: Record<"ok" | "warn" | "bad" | "info" | "muted", string> = {
  ok: "bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-400",
  warn: "bg-amber-100 dark:bg-amber-950/60 text-amber-700 dark:text-amber-400",
  bad: "bg-rose-100 dark:bg-rose-950/60 text-rose-700 dark:text-rose-400",
  info: "bg-blue-100 dark:bg-blue-950/60 text-blue-700 dark:text-blue-400",
  muted: "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300",
};
