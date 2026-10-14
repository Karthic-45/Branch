import { useState } from "react";
import { motion } from "framer-motion";
import { GitFork, Play, RotateCcw } from "lucide-react";
import { fetchMerges, revertMerge, startMigration } from "../lib/api";

interface Props {
  rootId: string;
  onRootIdChange: (id: string) => void;
  onStarted: () => void;
}

export function MigrationForm({ rootId, onRootIdChange, onStarted }: Props) {
  const [goal, setGoal] = useState("");
  const [n, setN] = useState(4);
  const [busy, setBusy] = useState(false);
  const [reverting, setReverting] = useState(false);
  const [error, setError] = useState("");

  const mergeConflictMatch = error.match(/branch\s+([0-9a-f-]+)\s+is already merged into\s+([0-9a-f-]+)/i);
  const mergedBranchId = mergeConflictMatch ? mergeConflictMatch[1] : null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await startMigration(rootId.trim(), goal.trim(), n);
      // Only after the server accepted it: calling this first closed the
      // drawer and toasted "Migration started" before a 409 could be shown,
      // so a rejected run looked like a successful one.
      onStarted();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleRevertActiveMerge() {
    setReverting(true);
    try {
      const merges = await fetchMerges(rootId.trim());
      const active = merges.find((m) => !m.reverted_at);
      if (active) {
        await revertMerge(active.merge_id);
        setError("");
      } else {
        setError("No active un-reverted merge found for this branch.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setReverting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3 p-4">
      <Field label="Root branch">
        <input
          value={rootId}
          onChange={(e) => onRootIdChange(e.target.value)}
          placeholder="uuid of the trunk branch"
          required
          className="w-72"
        />
      </Field>
      <Field label="Goal">
        <input value={goal} onChange={(e) => setGoal(e.target.value)} required placeholder="Describe the change, e.g. migrate the API layer to FastAPI" className="w-80" />
      </Field>
      <Field label="Branches">
        <input
          type="number" min={2} max={8} value={n}
          onChange={(e) => setN(Number(e.target.value) || 4)}
          className="w-16"
        />
      </Field>

      <motion.button
        type="submit"
        disabled={busy}
        whileHover={busy ? {} : { y: -1 }}
        whileTap={busy ? {} : { scale: 0.98 }}
        className="relative flex items-center gap-1.5 overflow-hidden rounded-[4px] border border-accent bg-accent px-3 py-1.5 text-[13px] font-medium text-white transition-colors hover:bg-accent-hover hover:border-accent-hover disabled:opacity-50"
      >
        {busy && (
          <motion.span
            className="absolute inset-0 bg-white/20"
            initial={{ x: "-100%" }}
            animate={{ x: "100%" }}
            transition={{ repeat: Infinity, duration: 1.1, ease: "linear" }}
          />
        )}
        <Play className="relative h-4 w-4" strokeWidth={2.5} fill="currentColor" />
        <span className="relative">{busy ? "Starting…" : "Start migration"}</span>
      </motion.button>

      {error && (
        <div className="basis-full rounded-[4px] border border-error/40 bg-error-soft px-3 py-2 font-mono text-[11.5px] text-error flex flex-col gap-2">
          <div>{error}</div>
          {mergedBranchId && (
            <div className="flex flex-wrap items-center gap-2 pt-1 font-sans">
              <button
                type="button"
                disabled={reverting}
                onClick={handleRevertActiveMerge}
                className="inline-flex items-center gap-1.5 rounded-[4px] border border-error/50 bg-error/15 px-2.5 py-1 text-[11.5px] font-medium text-error hover:bg-error/25 transition-colors disabled:opacity-50"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                <span>{reverting ? "Reverting merge…" : "Revert previous merge"}</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  onRootIdChange(mergedBranchId);
                  setError("");
                }}
                className="inline-flex items-center gap-1.5 rounded-[4px] border border-hairline bg-surface px-2.5 py-1 text-[11.5px] font-medium text-ink hover:bg-surface-raised transition-colors"
              >
                <GitFork className="h-3.5 w-3.5" />
                <span>Use merged branch as root</span>
              </button>
            </div>
          )}
        </div>
      )}
    </form>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[10.5px] font-semibold uppercase tracking-wider text-muted">{label}</span>
      {children}
    </label>
  );
}
