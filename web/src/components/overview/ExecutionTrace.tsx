import { useMemo } from "react";
import type { FeedEvent, Stage } from "../../lib/types";

/**
 * The pipeline a migration actually walks, derived entirely from the event
 * feed. A stage is only ever "running" or "done" because an event said so --
 * there is no timer advancing it optimistically, so a stalled CodeBuild
 * shows as still running rather than quietly completing.
 */
const STAGES: { id: string; label: string; matches: Stage[] }[] = [
  { id: "ingest", label: "Ingest", matches: ["ingesting", "ingested"] },
  { id: "plan", label: "Plan", matches: ["planning", "planned"] },
  { id: "fork", label: "Fork", matches: ["forked"] },
  { id: "retrieve", label: "Retrieve", matches: ["exploring"] },
  { id: "reason", label: "Reason", matches: ["explored"] },
  { id: "upload", label: "Upload", matches: ["uploading"] },
  { id: "build", label: "Build", matches: ["building"] },
  { id: "score", label: "Score", matches: ["scored"] },
  { id: "merge", label: "Merge", matches: ["merging", "merged"] },
];

type StageState = "pending" | "running" | "done" | "failed";

export function ExecutionTrace({ events }: { events: FeedEvent[] }) {
  const { states, timings } = useMemo(() => {
    const seen = new Map<Stage, number>();
    for (const e of events) if (!seen.has(e.stage)) seen.set(e.stage, e.ts);

    const errored = events.some((e) => e.stage === "error");
    const lastStage = events.length ? events[events.length - 1].stage : null;

    const states: Record<string, StageState> = {};
    const timings: Record<string, number | null> = {};

    let reachedIdx = -1;
    STAGES.forEach((s, i) => {
      const hit = s.matches.find((m) => seen.has(m));
      timings[s.id] = hit ? seen.get(hit)! : null;
      if (hit) reachedIdx = i;
    });

    STAGES.forEach((s, i) => {
      if (timings[s.id] === null) {
        states[s.id] = "pending";
      } else if (i < reachedIdx) {
        states[s.id] = "done";
      } else if (errored && lastStage === "error") {
        states[s.id] = "failed";
      } else if (s.matches.includes(lastStage as Stage)) {
        // The terminal stages are complete once reached; everything else is
        // still in flight while it is the most recent event.
        states[s.id] = lastStage === "merged" || lastStage === "scored" ? "done" : "running";
      } else {
        states[s.id] = "done";
      }
    });

    return { states, timings };
  }, [events]);

  if (!events.length) {
    return (
      <div className="px-4 py-3 text-[12.5px] text-muted">
        No run on this branch yet. The pipeline appears here as events arrive.
      </div>
    );
  }

  const first = events[0]?.ts ?? null;

  return (
    <ol className="flex items-stretch overflow-x-auto">
      {STAGES.map((s, i) => {
        const state = states[s.id];
        const at = timings[s.id];
        const offset = at && first ? at - first : null;
        return (
          <li key={s.id} className="flex min-w-0 flex-1 items-center">
            <div className="min-w-0 flex-1 px-3 py-2.5">
              <div className="flex items-center gap-1.5">
                <Glyph state={state} />
                <span className={`truncate text-[12.5px] ${
                  state === "pending" ? "text-muted"
                  : state === "failed" ? "text-error"
                  : state === "running" ? "font-medium text-accent"
                  : "text-ink"}`}>
                  {s.label}
                </span>
              </div>
              <div className="mt-0.5 pl-[18px] font-mono text-[10.5px] text-muted">
                {offset === null ? "—" : `+${offset < 1 ? "0" : Math.round(offset)}s`}
              </div>
            </div>
            {i < STAGES.length - 1 && (
              <span
                aria-hidden
                className={`h-px w-3 flex-none ${
                  states[STAGES[i + 1].id] === "pending" ? "bg-hairline" : "bg-hairline-strong"
                }`}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function Glyph({ state }: { state: StageState }) {
  if (state === "done") {
    return (
      <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 flex-none text-success" aria-label="completed">
        <circle cx="7" cy="7" r="6" fill="none" stroke="currentColor" strokeWidth="1.4" />
        <path d="M4.2 7.2l2 2 3.6-4" fill="none" stroke="currentColor" strokeWidth="1.6"
          strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (state === "running") {
    return (
      <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 flex-none text-accent running-pulse" aria-label="running">
        <circle cx="7" cy="7" r="6" fill="none" stroke="currentColor" strokeWidth="1.4" opacity="0.35" />
        <path d="M7 1a6 6 0 016 6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    );
  }
  if (state === "failed") {
    return (
      <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 flex-none text-error" aria-label="failed">
        <circle cx="7" cy="7" r="6" fill="none" stroke="currentColor" strokeWidth="1.4" />
        <path d="M5 5l4 4M9 5l-4 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 flex-none text-muted" aria-label="pending">
      <circle cx="7" cy="7" r="6" fill="none" stroke="currentColor" strokeWidth="1.2" opacity="0.5" />
    </svg>
  );
}
