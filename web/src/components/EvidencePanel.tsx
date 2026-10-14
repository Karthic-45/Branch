import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { GitCommitHorizontal, Loader2, FileText, Play } from "lucide-react";
import type { ApplyResult, DecisionEvidence, ValidationStep } from "../lib/types";
import { ApplyStepper } from "./patch/ApplyStepper";
import { pct } from "../lib/stages";
import { applyMerge, fetchEvidence, prDescriptionUrl } from "../lib/api";


/** Evidence and reproducibility for one merge, as two tabs over the same
 * decision -- "why did this win" and "could this be re-run" are different
 * questions and shouldn't compete for the same vertical space. */
export function EvidencePanel({
  mergeId, onWinnerResolved,
}: { mergeId: string; onWinnerResolved?: (winnerId: string) => void }) {
  const [evidence, setEvidence] = useState<DecisionEvidence | null>(null);
  const [error, setError] = useState("");
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null);
  const [applyError, setApplyError] = useState("");

  // Held in a ref rather than listed as a dependency: the callback is a
  // notification, not an input to the fetch. Depending on it would refetch
  // the whole evidence trail on every parent render for any caller that
  // passes an inline arrow.
  const notifyWinner = useRef(onWinnerResolved);
  notifyWinner.current = onWinnerResolved;

  useEffect(() => {
    let cancelled = false;
    fetchEvidence(mergeId)
      .then((e) => {
        if (cancelled) return;
        setEvidence(e);
        notifyWinner.current?.(e.winner_id);
      })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
    return () => { cancelled = true; };
  }, [mergeId]);

  async function handleApply(allowBaseChanged: boolean) {
    setApplying(true);
    setApplyError("");
    try {
      const result = await applyMerge(mergeId, true, allowBaseChanged);
      setApplyResult(result);
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : String(err));
    } finally {
      setApplying(false);
    }
  }

  if (error) {
    return <div className="p-4 font-mono text-xs text-error">{error}</div>;
  }
  if (!evidence) {
    return (
      <div className="flex items-center gap-2 p-4 text-xs text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading evidence…
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-5 p-4 lg:grid-cols-2">
      <div>
        <SectionLabel>Belief</SectionLabel>
        <p className="text-[13px] leading-relaxed text-ink-2">
          {evidence.belief ?? "(no belief recorded for this branch)"}
        </p>

        <SectionLabel className="mt-4">
          Alternatives considered ({evidence.alternatives.length})
        </SectionLabel>
        {evidence.alternatives.length === 0 ? (
          <p className="text-[12.5px] text-muted">No sibling branches on record for this merge.</p>
        ) : (
          <ul className="space-y-2">
            {evidence.alternatives.map((alt) => (
              <li key={alt.branch_id} className="rounded-lg border border-hairline bg-surface-2 px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[12.5px] font-medium text-ink">
                    {alt.hypothesis ?? alt.branch_id.slice(0, 8)}
                  </span>
                  <span className="font-mono text-[11px] text-muted">
                    {pct(alt.score) === null ? "—" : `${pct(alt.score)}%`} · {alt.status}
                  </span>
                </div>
                {alt.belief && <p className="mt-1 text-[11.5px] text-ink-2">{alt.belief}</p>}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <SectionLabel>Provenance</SectionLabel>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-[11.5px]">
          <dt className="text-muted">decision</dt>
          <dd className="text-ink-2">{evidence.merge_id}</dd>
          <dt className="text-muted">winner</dt>
          <dd className="text-ink-2">{evidence.winner_id}</dd>
          <dt className="text-muted">base commit</dt>
          <dd className="text-ink-2">{evidence.base_sha ? evidence.base_sha.slice(0, 12) : "not recorded"}</dd>
        </dl>

        <div className="mt-4 flex flex-wrap gap-2">
          <motion.button
            whileHover={{ y: -1 }}
            whileTap={{ scale: 0.97 }}
            disabled={applying || !evidence.patch_available}
            onClick={() => handleApply(false)}
            title={evidence.patch_available ? undefined : "No patch on record for this merge"}
            className="flex items-center gap-1.5 rounded-[4px] border border-accent bg-accent px-3 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-accent-hover hover:border-accent-hover disabled:opacity-50"
          >
            {applying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            {applying ? "Applying…" : "Apply winning patch"}
          </motion.button>
          <a
            href={prDescriptionUrl(mergeId)}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 rounded-[4px] border border-hairline-strong px-3 py-1.5 text-[12px] text-ink-2 transition-colors hover:border-accent hover:text-accent"
          >
            <FileText className="h-3.5 w-3.5" /> PR description
          </a>
        </div>

        {applyError && (
          <div className="mt-3 rounded-[4px] border border-error/40 bg-error-soft px-3 py-2 font-mono text-[11px] text-error">
            {applyError}
          </div>
        )}

        {applyResult && (
          <>
            <ApplyStepper result={applyResult} />
            <ApplyOutcome result={applyResult} onRetryAnyway={() => handleApply(true)} />
          </>
        )}
      </div>
    </div>
  );
}

function ApplyOutcome({ result, onRetryAnyway }: { result: ApplyResult; onRetryAnyway: () => void }) {
  if (result.validation_status === "base_changed") {
    return (
      <div className="mt-3 rounded-[4px] border border-warning/40 bg-warning-soft px-3 py-2 text-[12px] text-warning">
        <p className="font-semibold">The repository has moved since this patch was tested.</p>
        <p className="mt-1 font-mono text-[11px]">
          tested against {result.base_sha?.slice(0, 12)}, now at {result.current_head_sha?.slice(0, 12)}
        </p>
        <button
          onClick={onRetryAnyway}
          className="mt-2 rounded-md border border-warning/50 px-2.5 py-1 text-[11px] font-medium hover:bg-warning-soft"
        >
          Attempt application anyway
        </button>
      </div>
    );
  }

  if (result.validation_status === "patch_failed") {
    return (
      <div className="mt-3 rounded-[4px] border border-error/40 bg-error-soft px-3 py-2 text-[12px] text-error">
        <p className="font-semibold">Patch did not apply cleanly.</p>
        {result.conflicts.length > 0 && (
          <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[10.5px]">
            {result.conflicts.join("\n")}
          </pre>
        )}
      </div>
    );
  }

  return (
    <div className="mt-3 space-y-2">
      <div className="flex items-center gap-1.5 text-[12px]">
        <GitCommitHorizontal className="h-3.5 w-3.5 text-success" />
        <span className="text-ink-2">
          Applied on branch <span className="font-mono text-ink">{result.branch}</span>
          {result.commit_sha && (
            <> as <span className="font-mono text-ink">{result.commit_sha.slice(0, 12)}</span></>
          )}
        </span>
      </div>
      {result.validation_steps.length > 0 && <ValidationMatrix steps={result.validation_steps} />}
      {result.validation_steps.length === 0 && (
        <p className="text-[11.5px] text-muted">
          No validation profile configured for this repository -- committed without running any checks.
        </p>
      )}
    </div>
  );
}

function ValidationMatrix({ steps }: { steps: ValidationStep[] }) {
  return (
    <table className="w-full border-collapse text-[11.5px]">
      <tbody>
        {steps.map((s) => (
          <tr key={s.name} className="border-b border-hairline last:border-none">
            <td className="py-1 pr-3 font-mono text-muted">{s.name}</td>
            <td className={`py-1 text-right font-mono font-medium ${
              s.status === "passed" ? "text-success" : s.status === "failed" ? "text-error" : "text-warning"
            }`}>
              {s.status.toUpperCase()}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SectionLabel({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <h4 className={`mb-1.5 font-mono text-[10.5px] font-semibold uppercase tracking-wider text-muted ${className}`}>
      {children}
    </h4>
  );
}
