import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { GitBranch, ChevronDown } from "lucide-react";
import { createRepo } from "../lib/api";

interface Props {
  onIngestStarted: (rootId: string) => void;
}

export function NewFromRepoForm({ onIngestStarted }: Props) {
  const [repoUrl, setRepoUrl] = useState("");
  const [ref, setRef] = useState("");
  const [testSelector, setTestSelector] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const rootId = await createRepo(repoUrl.trim(), ref.trim() || null, "main", testSelector.trim());
      onIngestStarted(rootId);
      setRepoUrl("");
      setRef("");
      setTestSelector("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="p-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Repository URL" className="min-w-72 flex-1">
          <input
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            placeholder="https://github.com/owner/repo.git"
            required
            className="w-full"
          />
        </Field>

        <motion.button
          type="submit"
          disabled={busy || !repoUrl.trim()}
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
          <GitBranch className="relative h-4 w-4" strokeWidth={2.5} />
          <span className="relative">{busy ? "Cloning…" : "Ingest repo"}</span>
        </motion.button>
      </div>

      <button
        type="button"
        onClick={() => setAdvanced((a) => !a)}
        className="mt-3 flex items-center gap-1 text-[11.5px] text-muted transition-colors hover:text-ink-2"
      >
        <motion.span animate={{ rotate: advanced ? 180 : 0 }} transition={{ duration: 0.15 }}>
          <ChevronDown className="h-3.5 w-3.5" strokeWidth={2} />
        </motion.span>
        Advanced options
      </button>

      <AnimatePresence initial={false}>
        {advanced && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="mt-3 flex flex-wrap gap-3">
              <Field label="Ref (branch, tag, or commit)">
                <input
                  value={ref}
                  onChange={(e) => setRef(e.target.value)}
                  placeholder="default branch"
                  className="w-56"
                />
              </Field>
              <Field label="Test selector">
                <input
                  value={testSelector}
                  onChange={(e) => setTestSelector(e.target.value)}
                  placeholder="full suite"
                  className="w-56"
                />
              </Field>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {error && (
        <div className="mt-3 rounded-[4px] border border-error/40 bg-error-soft px-3 py-2 font-mono text-[11.5px] text-error">
          {error}
        </div>
      )}
    </form>
  );
}

function Field({ label, children, className = "" }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={`flex flex-col gap-1.5 ${className}`}>
      <span className="text-[10.5px] font-semibold uppercase tracking-wider text-muted">{label}</span>
      {children}
    </label>
  );
}
