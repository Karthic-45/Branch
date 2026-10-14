import { Check, X, Minus, GitCommitHorizontal } from "lucide-react";
import type { ApplyResult } from "../../lib/types";
import { Badge, Field, FieldList } from "../ui";

type State = "passed" | "failed" | "skipped";

interface Step {
  label: string;
  state: State;
  detail: string;
}

/**
 * The safe-apply gate, as the sequence it actually is.
 *
 * Every step is derived from the ApplyResult -- there is no client-side
 * simulation of progress. A run that stopped at the base-SHA check reports
 * the later steps as skipped rather than failed: they were never attempted,
 * and saying they failed would blame the patch for a guard that fired first.
 */
function derive(result: ApplyResult): Step[] {
  const steps: Step[] = [];

  steps.push({
    label: "Repository",
    state: result.base_sha ? "passed" : "failed",
    detail: result.base_sha
      ? `Cloned at the commit this patch was scored against`
      : "No base commit was recorded for this decision",
  });

  const shaOk = Boolean(result.base_sha) && !result.base_changed;
  steps.push({
    label: "Base commit",
    state: result.base_sha ? (shaOk ? "passed" : "failed") : "skipped",
    detail: !result.base_sha
      ? "Not attempted"
      : shaOk
        ? `${result.base_sha.slice(0, 12)} still HEAD`
        : `Repository moved to ${result.current_head_sha?.slice(0, 12) ?? "an unknown commit"} — the score no longer describes this tree`,
  });

  // A malformed artifact and a real conflict both fail here, and saying
  // "conflicting file" for the first sends the reader to inspect a repository
  // that is fine. The count previously came from stderr lines, so one
  // diagnostic line read as one conflicting file.
  steps.push({
    label: "Patch",
    state: !shaOk ? "skipped" : result.patch_applied ? "passed" : "failed",
    detail: !shaOk
      ? "Not attempted"
      : result.patch_applied
        ? `Applied cleanly to ${result.branch ?? "an isolated branch"}`
        : result.patch_failure === "malformed"
          ? "The stored artifact is not a unified diff — nothing to apply"
          : result.patch_failure === "conflict"
            ? `Conflicts with ${result.conflicting_files.length} file${result.conflicting_files.length === 1 ? "" : "s"}: ${result.conflicting_files.slice(0, 3).join(", ")}`
            : "git apply rejected the patch",
  });

  const validated = result.validation_status === "passed";
  steps.push({
    label: "Validation",
    state: !result.patch_applied
      ? "skipped"
      : validated
        ? "passed"
        : result.validation_status === "skipped"
          ? "skipped"
          : "failed",
    detail: !result.patch_applied
      ? "Not attempted"
      : `${result.validation_steps.length} step${result.validation_steps.length === 1 ? "" : "s"} — ${result.validation_status.replace(/_/g, " ")}`,
  });

  steps.push({
    label: "Commit",
    state: result.commit_sha ? "passed" : "skipped",
    detail: result.commit_sha
      ? `${result.commit_sha.slice(0, 12)} on a local branch — not pushed`
      : "Not committed",
  });

  return steps;
}

export function ApplyStepper({ result }: { result: ApplyResult }) {
  const steps = derive(result);
  const ready = Boolean(result.commit_sha);

  return (
    <div className="mt-3 rounded-[6px] border border-hairline bg-surface">
      <ol className="divide-y divide-hairline">
        {steps.map((s, i) => (
          <li key={s.label} className="flex items-start gap-2.5 px-3 py-2">
            <span className="mt-0.5 flex-none" aria-hidden>
              {s.state === "passed" ? (
                <Check className="h-3.5 w-3.5 text-success" />
              ) : s.state === "failed" ? (
                <X className="h-3.5 w-3.5 text-error" />
              ) : (
                <Minus className="h-3.5 w-3.5 text-muted" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[10.5px] text-muted tabular">{i + 1}</span>
                <span className={`text-[12.5px] font-medium ${
                  s.state === "passed" ? "text-ink"
                  : s.state === "failed" ? "text-error"
                  : "text-muted"}`}>
                  {s.label}
                </span>
                <span className="sr-only">{s.state}</span>
              </div>
              <p className="mt-0.5 text-[12px] leading-relaxed text-ink-2">{s.detail}</p>
            </div>
          </li>
        ))}
      </ol>

      <div className="border-t border-hairline px-3 py-2.5">
        {ready ? (
          <>
            <div className="mb-1.5 flex items-center gap-1.5">
              <GitCommitHorizontal className="h-3.5 w-3.5 text-success" />
              <span className="text-[12.5px] font-medium text-ink">Ready for human review</span>
              <Badge tone="neutral">not pushed</Badge>
            </div>
            <p className="text-[12px] leading-relaxed text-ink-2">
              The change exists as a local commit on its own branch. Pushing it and opening a pull
              request is a human step — this deployment holds no write credential for any
              repository.
            </p>
          </>
        ) : (
          <p className="text-[12px] leading-relaxed text-ink-2">
            The gate stopped before a commit was made. Nothing was written to any repository.
            {result.patch_failure === "malformed" && (
              <> The artifact came from a stand-in explorer, not a model — there is no patch here
              for the repository to reject.</>
            )}
          </p>
        )}
      </div>

      {result.validation_steps.length > 0 && (
        <FieldList>
          {result.validation_steps.map((v) => (
            <Field
              key={v.name}
              k={v.name}
              v={v.status}
              tone={v.status === "passed" ? "success" : "error"}
            />
          ))}
        </FieldList>
      )}
    </div>
  );
}
