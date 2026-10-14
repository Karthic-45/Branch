import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  LayoutDashboard, GitBranch, Network, Database, Rocket, Activity, ShieldCheck,
  FileText, ScrollText, PanelLeftClose, PanelLeft, Sun, Moon, Search, Command,
  Bell, HelpCircle, ChevronRight, GitFork, Boxes, Cloud
} from "lucide-react";
import type { Theme } from "../../hooks/useTheme";
import type { ConnState, ReadinessReport } from "../../lib/types";
import { Tooltip } from "../ui";
import { TopProgress } from "../common/Loading";

export type Section =
  | "overview" | "decisions" | "branches" | "memory"
  | "deployments" | "observability" | "aws" | "proofs" | "reports" | "audit" | "judge";

const NAV_ITEMS: { key: Section; label: string; icon: typeof GitBranch }[] = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "decisions", label: "Decisions", icon: Network },
  { key: "branches", label: "Branches", icon: GitBranch },
  { key: "memory", label: "Beliefs (Memory)", icon: Database },
  { key: "deployments", label: "Deployments", icon: Rocket },
  { key: "observability", label: "System / Observability", icon: Activity },
  { key: "aws", label: "AWS Storage", icon: Cloud },
  { key: "proofs", label: "Proofs", icon: ShieldCheck },
  { key: "reports", label: "Reports & Exports", icon: FileText },
  { key: "audit", label: "Audit & Judge Mode", icon: ScrollText },
];

const COLLAPSE_KEY = "bmem-sidebar-collapsed";

type PillTone = "ok" | "warn" | "bad" | "idle";

const PILL_TONES: Record<PillTone, { pill: string; dot: string }> = {
  ok: {
    pill: "border-emerald-200 dark:border-emerald-800/60 bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400",
    dot: "bg-emerald-500",
  },
  warn: {
    pill: "border-amber-200 dark:border-amber-800/60 bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-400",
    dot: "bg-amber-500",
  },
  bad: {
    pill: "border-rose-200 dark:border-rose-800/60 bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-400",
    dot: "bg-rose-500",
  },
  idle: {
    pill: "border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/60 text-slate-500 dark:text-slate-400",
    dot: "bg-slate-400",
  },
};

/** What the header pill should claim. "Connected" only when the live stream
 * is up AND every dependency is real -- a green pill over simulated scores
 * reads as "these numbers are measurements", which readiness says they aren't. */
function statusPill(conn: ConnState, readiness: ReadinessReport | null): { label: string; tone: PillTone } {
  if (conn === "disconnected") return { label: "OFFLINE", tone: "bad" };
  if (conn === "connecting") return { label: "CONNECTING", tone: "idle" };
  if (conn === "idle") return { label: "NO ROOT", tone: "idle" };
  if (!readiness) return { label: "CONNECTED", tone: "ok" };
  switch (readiness.overall) {
    case "ready": return { label: "CONNECTED", tone: "ok" };
    case "simulated": return { label: "SIMULATED", tone: "warn" };
    case "degraded": return { label: "DEGRADED", tone: "warn" };
    case "not_ready": return { label: "NOT READY", tone: "bad" };
  }
}

export function AppShell({
  section, onSelect, theme, onToggleTheme, conn, readiness, rootId, repository,
  onOpenPalette, onOpenStatus, children, loading = false,
}: {
  section: Section;
  onSelect: (s: Section) => void;
  theme: Theme;
  onToggleTheme: () => void;
  conn: ConnState;
  readiness: ReadinessReport | null;
  rootId: string | null;
  repository: string | null;
  onOpenPalette: () => void;
  onOpenStatus: () => void;
  children: React.ReactNode;
  /** Any page data still being fetched for this project. */
  loading?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSE_KEY) === "1",
  );
  useEffect(() => {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  // No invented fallbacks: a root ingested before self-service ingest has no
  // recorded repo, and saying so is more useful than a plausible fake name.
  const displayRepo = repository
    ? repository.replace(/^https:\/\/github\.com\//, "").replace(/\.git$/, "")
    : "not recorded";

  const displayRoot = rootId ? rootId.slice(0, 8) : "none";

  const pill = statusPill(conn, readiness);
  const readyCount = readiness ? readiness.checks.filter((c) => c.status === "ready").length : 0;
  const notReady = readiness ? readiness.checks.filter((c) => c.status !== "ready").length : 0;

  return (
    <div className="flex h-svh overflow-hidden bg-slate-50 dark:bg-[#0c0e14] text-slate-800 dark:text-slate-100 font-sans">
      {/* Sidebar */}
      <nav
        aria-label="Primary"
        className={`flex flex-none flex-col border-r border-slate-200/80 dark:border-slate-800 bg-white/95 dark:bg-[#11141c]/95 backdrop-blur-md
          transition-[width] duration-200 shadow-sm z-20 ${collapsed ? "w-[58px]" : "w-[228px]"}`}
      >
        {/* Brand / Logo */}
        <div className={`flex flex-none items-center gap-2.5 border-b border-slate-100 dark:border-slate-800/80 px-3.5 py-3.5
          ${collapsed ? "justify-center px-0" : ""}`}>
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-tr from-indigo-600 via-blue-600 to-cyan-400 text-white shadow-md shadow-indigo-500/25">
            <Boxes className="h-4.5 w-4.5" />
          </div>
          {!collapsed && (
            <div className="min-w-0 leading-tight">
              <div className="truncate text-[14px] font-bold tracking-tight bg-gradient-to-r from-slate-900 to-slate-700 dark:from-white dark:to-slate-300 bg-clip-text text-transparent">
                Branchable Memory
              </div>
              <div className="truncate text-[10px] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">Agent Belief OS</div>
            </div>
          )}
        </div>

        {/* Navigation list */}
        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3 space-y-1">
          {NAV_ITEMS.map((item) => (
            <NavItem
              key={item.key}
              item={item}
              active={section === item.key || (item.key === "audit" && section === "judge")}
              collapsed={collapsed}
              onSelect={onSelect}
            />
          ))}
        </div>

        {/* Footer: backend health from the real readiness probes */}
        <div className="flex-none border-t border-slate-100 dark:border-slate-800/80 p-2 space-y-1 bg-slate-50/50 dark:bg-slate-900/30">
          <button
            onClick={onOpenStatus}
            title={readiness ? readiness.note : "Readiness probes have not answered yet"}
            className={`flex w-full items-center gap-2.5 rounded-xl border border-slate-200/60 dark:border-slate-800 bg-white dark:bg-slate-900/80 p-1.5 text-left shadow-xs hover:bg-slate-50 dark:hover:bg-slate-800/60
              ${collapsed ? "justify-center" : "px-2.5 py-2"}`}
          >
            <div className="relative flex h-7 w-7 flex-none items-center justify-center rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500">
              <Activity className="h-3.5 w-3.5" />
              <span className={`absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full ring-1 ring-white dark:ring-slate-900 ${PILL_TONES[pill.tone].dot}`} />
            </div>
            {!collapsed && (
              <>
                <div className="min-w-0 flex-1 leading-tight">
                  <div className="truncate text-[12px] font-semibold text-slate-800 dark:text-slate-200">Backend</div>
                  <div className="truncate text-[10px] text-slate-400 dark:text-slate-500">
                    {readiness
                      ? `${readyCount}/${readiness.checks.length} components ready`
                      : "not probed yet"}
                  </div>
                </div>
                <ChevronRight className="h-3.5 w-3.5 text-slate-400 flex-none" />
              </>
            )}
          </button>

          {/* Collapse sidebar toggle */}
          <button
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="flex w-full items-center justify-center rounded-lg py-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 text-[11px]"
          >
            {collapsed ? <PanelLeft className="h-3.5 w-3.5" /> : <PanelLeftClose className="h-3.5 w-3.5" />}
          </button>
        </div>
      </nav>

      {/* Main Area */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* Top Header Bar */}
        <header className="flex flex-none items-center gap-3 border-b border-slate-200/80 dark:border-slate-800
          bg-white/95 dark:bg-[#11141c]/95 backdrop-blur-md px-5 py-2.5 z-10">
          
          {/* Search Bar matching screenshot */}
          <div className="relative min-w-0 flex-1 max-w-md">
            <button
              onClick={onOpenPalette}
              className="group flex w-full items-center gap-2.5 rounded-lg border border-slate-200 dark:border-slate-800
                bg-slate-50/80 dark:bg-slate-900/60 px-3 py-1.5 text-[12.5px] text-slate-400 transition-all
                hover:border-indigo-300 dark:hover:border-indigo-700 hover:bg-white dark:hover:bg-slate-900 shadow-2xs"
            >
              <Search className="h-3.5 w-3.5 text-slate-400 group-hover:text-indigo-500 transition-colors" />
              <span className="truncate">Search branches, decisions, or anything...</span>
              <kbd className="ml-auto flex items-center gap-0.5 rounded border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] font-medium text-slate-400 shadow-2xs">
                <Command className="h-2.5 w-2.5" />K
              </kbd>
            </button>
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-2.5">
            {/* Repo Badge */}
            <div className="hidden xl:flex items-center gap-1.5 rounded-full border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900/60 px-2.5 py-1 text-[11.5px] font-medium text-slate-600 dark:text-slate-300 shadow-2xs">
              <GitFork className="h-3 w-3 text-indigo-500" />
              <span>Repo: <span className="font-mono text-slate-800 dark:text-slate-200">{displayRepo}</span></span>
            </div>

            {/* Root Branch Badge */}
            <div className="hidden md:flex items-center gap-1.5 rounded-full border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900/60 px-2.5 py-1 text-[11.5px] font-medium text-slate-600 dark:text-slate-300 shadow-2xs">
              <GitBranch className="h-3 w-3 text-blue-500" />
              <span>Root: <span className="font-mono text-slate-800 dark:text-slate-200">{displayRoot}</span></span>
            </div>

            {/* Status pill: stream connection first, then dependency readiness */}
            <button
              onClick={onOpenStatus}
              title={readiness ? `Status: ${readiness.overall} - Click for system diagnostics` : "Click for system diagnostics"}
              className={`flex items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1 text-[11px] font-bold tracking-wider shadow-2xs transition-transform hover:scale-102 ${PILL_TONES[pill.tone].pill}`}
            >
              <span className="relative flex h-2 w-2">
                {pill.tone === "ok" && (
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                )}
                <span className={`relative inline-flex h-2 w-2 rounded-full ${PILL_TONES[pill.tone].dot}`} />
              </span>
              <span>{pill.label}</span>
            </button>

            {/* Notification Bell */}
            <button
              onClick={() => onOpenStatus()}
              aria-label={notReady > 0 ? `${notReady} components need attention` : "System status"}
              className="relative flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 shadow-2xs transition-colors"
            >
              <Bell className="h-3.5 w-3.5" />
              {notReady > 0 && (
                <span
                  title={`${notReady} component${notReady === 1 ? "" : "s"} not ready`}
                  className="absolute top-1.5 right-1.5 h-1.5 w-1.5 rounded-full bg-rose-500 ring-1 ring-white dark:ring-slate-900"
                />
              )}
            </button>

            {/* Help / Guide button */}
            <button
              onClick={() => onSelect("judge")}
              aria-label="Help & Guide"
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 shadow-2xs transition-colors"
            >
              <HelpCircle className="h-3.5 w-3.5" />
            </button>

            {/* Theme Toggle */}
            <button
              onClick={onToggleTheme}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 shadow-2xs transition-colors"
            >
              {theme === "dark" ? <Sun className="h-3.5 w-3.5 text-amber-400" /> : <Moon className="h-3.5 w-3.5 text-slate-600" />}
            </button>
          </div>
        </header>
        <TopProgress active={loading} label="Loading project data" />

        {/* Reconnecting banner if disconnected */}
        {conn === "disconnected" && (
          <div className="flex-none border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-[12px] font-medium text-amber-800 dark:bg-amber-950/60 dark:text-amber-300">
            Live connection lost — reconnecting. Values below are from the last received snapshot.
          </div>
        )}

        <main className="min-h-0 flex-1 overflow-auto bg-slate-50/60 dark:bg-[#0c0e14]">
          {children}
        </main>
      </div>
    </div>
  );
}

function NavItem({
  item, active, collapsed, onSelect,
}: {
  item: { key: Section; label: string; icon: typeof GitBranch };
  active: boolean;
  collapsed: boolean;
  onSelect: (s: Section) => void;
}) {
  const Icon = item.icon;
  const button = (
    <button
      onClick={() => onSelect(item.key)}
      aria-current={active ? "page" : undefined}
      className={`group relative flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[12.5px] font-medium
        transition-all duration-150 ${collapsed ? "justify-center px-0" : ""}
        ${active
          ? "bg-indigo-50/90 text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300 shadow-2xs font-semibold"
          : "text-slate-600 dark:text-slate-400 hover:bg-slate-100/80 dark:hover:bg-slate-800/50 hover:text-slate-900 dark:hover:text-slate-100"}`}
    >
      {active && (
        <motion.span
          layoutId="activeNavIndicator"
          className="absolute inset-y-1.5 left-0 w-1 rounded-r-full bg-indigo-600 dark:bg-indigo-400"
          transition={{ type: "spring", stiffness: 350, damping: 30 }}
        />
      )}
      <Icon
        className={`h-4 w-4 flex-none transition-transform group-hover:scale-105 ${active ? "text-indigo-600 dark:text-indigo-400" : "text-slate-400 dark:text-slate-500 group-hover:text-slate-600 dark:group-hover:text-slate-300"}`}
        strokeWidth={active ? 2.2 : 1.8}
      />
      {!collapsed && <span className="truncate">{item.label}</span>}
    </button>
  );
  return collapsed ? <Tooltip tip={item.label}>{button}</Tooltip> : button;
}


