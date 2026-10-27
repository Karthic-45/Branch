import { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Search, CornerDownLeft, Play, Network, GitBranch, Database, ShieldCheck,
  FileText, ScrollText, Activity, Rocket, Presentation, Cloud, Terminal,
} from "lucide-react";
import type { BranchNode, DecisionNode, MergeRow } from "../../lib/types";
import type { Section } from "./AppShell";

interface Command {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon: typeof Play;
  run: () => void;
}

/**
 * ⌘K surface. Two kinds of entry, deliberately in one list:
 *
 *   - navigation and actions, which are static and always available
 *   - live objects (decisions, branches) matched against data already in
 *     memory from the SSE stream and the decision graph
 *
 * Nothing here fabricates a result: if no decisions exist, the Decisions
 * group is simply absent rather than showing a placeholder row.
 */
export function CommandPalette({
  open, onClose, onNavigate, decisions, branches, merges, onRunMigration, onOpenDecision,
}: {
  open: boolean;
  onClose: () => void;
  onNavigate: (s: Section) => void;
  decisions: DecisionNode[];
  branches: BranchNode[];
  merges: MergeRow[];
  onRunMigration: () => void;
  onOpenDecision: (mergeId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setCursor(0);
      // rAF rather than a bare focus(): the input does not exist until the
      // enter animation has committed a frame.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const commands = useMemo<Command[]>(() => {
    const nav: Command[] = [
      { id: "nav-overview", label: "Go to Overview", group: "Navigate", icon: Activity, hint: "G O", run: () => onNavigate("overview") },
      { id: "nav-decisions", label: "Go to Decisions", group: "Navigate", icon: Network, hint: "G D", run: () => onNavigate("decisions") },
      { id: "nav-branches", label: "Go to Branches", group: "Navigate", icon: GitBranch, hint: "G B", run: () => onNavigate("branches") },
      { id: "nav-memory", label: "Go to Beliefs", group: "Navigate", icon: Database, hint: "G M", run: () => onNavigate("memory") },
      { id: "nav-deployments", label: "Go to Deployments", group: "Navigate", icon: Rocket, run: () => onNavigate("deployments") },
      { id: "nav-observability", label: "Go to Observability", group: "Navigate", icon: Activity, run: () => onNavigate("observability") },
      { id: "nav-aws", label: "Go to AWS Storage", group: "Navigate", icon: Cloud, run: () => onNavigate("aws") },
      { id: "nav-outputs", label: "Go to AWS Outputs", group: "Navigate", icon: Terminal, run: () => onNavigate("outputs") },
      { id: "nav-proofs", label: "Go to Proofs", group: "Navigate", icon: ShieldCheck, run: () => onNavigate("proofs") },
      { id: "nav-reports", label: "Go to Reports", group: "Navigate", icon: FileText, run: () => onNavigate("reports") },
      { id: "nav-audit", label: "Go to Audit", group: "Navigate", icon: ScrollText, hint: "G A", run: () => onNavigate("audit") },
      { id: "nav-judge", label: "Go to Guided tour", group: "Navigate", icon: Presentation, run: () => onNavigate("judge") },
    ];

    const actions: Command[] = [
      { id: "act-migrate", label: "Run migration", group: "Actions", icon: Play, run: onRunMigration },
      { id: "act-proofs", label: "Run system proofs", group: "Actions", icon: ShieldCheck, run: () => onNavigate("proofs") },
      { id: "act-reports", label: "Generate report", group: "Actions", icon: FileText, run: () => onNavigate("reports") },
      { id: "act-tour", label: "Start the guided tour", group: "Actions", icon: Presentation, run: () => onNavigate("judge") },
    ];

    const decisionCommands: Command[] = decisions.map((d) => ({
      id: `dec-${d.merge_id}`,
      label: d.winner_hypothesis ?? d.winner_branch_id.slice(0, 12),
      hint: `${d.merge_id.slice(0, 8)}${d.winner_score !== null ? ` · ${Math.round(d.winner_score * 100)}%` : ""}`,
      group: "Decisions",
      icon: Network,
      run: () => onOpenDecision(d.merge_id),
    }));

    // Merges without a decision-graph entry still deserve to be findable.
    const known = new Set(decisions.map((d) => d.merge_id));
    const mergeCommands: Command[] = merges
      .filter((m) => !known.has(m.merge_id))
      .map((m) => ({
        id: `mrg-${m.merge_id}`,
        label: m.winner_hypothesis ?? m.winner_id.slice(0, 12),
        hint: m.merge_id.slice(0, 8),
        group: "Decisions",
        icon: Network,
        run: () => onOpenDecision(m.merge_id),
      }));

    const branchCommands: Command[] = branches
      .filter((b) => b.parent_id !== null)
      .map((b) => ({
        id: `br-${b.id}`,
        label: b.hypothesis ?? b.id.slice(0, 12),
        hint: `${b.id.slice(0, 8)} · ${b.status}`,
        group: "Branches",
        icon: GitBranch,
        run: () => onNavigate("branches"),
      }));

    return [...actions, ...decisionCommands, ...mergeCommands, ...branchCommands, ...nav];
  }, [decisions, branches, merges, onNavigate, onRunMigration, onOpenDecision]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands.slice(0, 40);
    return commands
      .filter((c) => `${c.label} ${c.hint ?? ""} ${c.group}`.toLowerCase().includes(q))
      .slice(0, 40);
  }, [commands, query]);

  const grouped = useMemo(() => {
    const out: { group: string; items: Command[] }[] = [];
    for (const c of filtered) {
      const last = out[out.length - 1];
      if (last && last.group === c.group) last.items.push(c);
      else out.push({ group: c.group, items: [c] });
    }
    return out;
  }, [filtered]);

  const flat = useMemo(() => grouped.flatMap((g) => g.items), [grouped]);

  useEffect(() => { setCursor(0); }, [query]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { onClose(); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(flat.length - 1, c + 1)); }
      if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(0, c - 1)); }
      if (e.key === "Enter") {
        e.preventDefault();
        const cmd = flat[cursor];
        if (cmd) { cmd.run(); onClose(); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, flat, cursor, onClose]);

  // Keep the highlighted row in view during arrow navigation.
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-idx="${cursor}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-[70] flex items-start justify-center pt-[12vh]">
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.12 }}
            onClick={onClose}
            className="absolute inset-0 bg-ink/25"
          />
          <motion.div
            role="dialog"
            aria-label="Command palette"
            initial={{ opacity: 0, y: -8, scale: 0.99 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.99 }}
            transition={{ duration: 0.14 }}
            className="relative flex max-h-[62vh] w-full max-w-[560px] flex-col overflow-hidden
              rounded-[8px] border border-hairline bg-surface shadow-[var(--shadow-modal)]"
          >
            <div className="flex flex-none items-center gap-2 border-b border-hairline px-3.5 py-2.5">
              <Search className="h-4 w-4 flex-none text-muted" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search decisions, branches, or run a command…"
                className="w-full border-0 bg-transparent p-0 text-[13.5px] focus:shadow-none"
                style={{ outline: "none" }}
              />
              <kbd className="flex-none rounded-[3px] border border-hairline px-1.5 py-0.5
                font-mono text-[10px] text-muted">esc</kbd>
            </div>

            <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto py-1.5">
              {flat.length === 0 ? (
                <div className="px-3.5 py-8 text-center text-[13px] text-muted">
                  No matches for “{query}”.
                </div>
              ) : (
                grouped.map((g) => (
                  <div key={g.group} className="mb-1">
                    <div className="px-3.5 pb-1 pt-2 text-[10px] font-semibold uppercase
                      tracking-wider text-muted">
                      {g.group}
                    </div>
                    {g.items.map((c) => {
                      const idx = flat.indexOf(c);
                      const Icon = c.icon;
                      return (
                        <button
                          key={c.id}
                          data-idx={idx}
                          onMouseMove={() => setCursor(idx)}
                          onClick={() => { c.run(); onClose(); }}
                          className={`flex w-full items-center gap-2.5 px-3.5 py-1.5 text-left
                            text-[13px] transition-colors
                            ${idx === cursor ? "bg-accent-soft text-ink" : "text-ink-2"}`}
                        >
                          <Icon className="h-4 w-4 flex-none text-muted" strokeWidth={1.9} />
                          <span className="min-w-0 flex-1 truncate">{c.label}</span>
                          {c.hint && (
                            <span className="flex-none font-mono text-[11px] text-muted">{c.hint}</span>
                          )}
                          {idx === cursor && (
                            <CornerDownLeft className="h-3.5 w-3.5 flex-none text-muted" />
                          )}
                        </button>
                      );
                    })}
                  </div>
                ))
              )}
            </div>

            <div className="flex flex-none items-center gap-3 border-t border-hairline
              px-3.5 py-2 text-[11px] text-muted">
              <span className="flex items-center gap-1"><kbd className="font-mono">↑↓</kbd> navigate</span>
              <span className="flex items-center gap-1"><kbd className="font-mono">↵</kbd> select</span>
              <span className="ml-auto">{flat.length} result{flat.length === 1 ? "" : "s"}</span>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
