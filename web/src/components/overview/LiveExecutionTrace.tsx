import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  CheckCircle2, Database, Download, FileCode, GitBranch, Layers,
  Play, Sparkles, Square, Terminal, Trophy, XCircle,
} from "lucide-react";
import type { BranchNode, FeedEvent, Stage } from "../../lib/types";
import { STAGE_LABEL, codeBuildPhase } from "../../lib/stages";

/* A live view of the run that is happening *now*, driven entirely by the SSE
   event feed (pushed ~once a second). Nothing here advances on a timer: a
   step is active only because an event says some branch is on it, so a
   stalled CodeBuild stays visibly stuck on Build instead of drifting ahead. */

const STEPS = [
  { key: "ingest", label: "Ingest", icon: Download },
  { key: "plan", label: "Plan", icon: Sparkles },
  { key: "fork", label: "Fork", icon: GitBranch },
  { key: "retrieve", label: "Retrieve", icon: Database },
  { key: "reason", label: "Reason", icon: Terminal },
  { key: "upload", label: "Upload", icon: FileCode },
  { key: "build", label: "Build", icon: Layers },
  { key: "score", label: "Score", icon: Trophy },
  { key: "merge", label: "Merge", icon: CheckCircle2 },
] as const;

const INGEST = 0, PLAN = 1, FORK = 2, SCORE = 7, MERGE = 8;

/** Step a per-branch stage puts that branch on. */
const BRANCH_STEP: Partial<Record<Stage, number>> = {
  forked: 2,
  exploring: 3,      // searching the branch's inherited memory
  explored: 4,       // reasoning produced a belief + patch
  evaluating: 5,     // submitting the patch
  uploading: 5,
  building: 6,       // CodeBuild running the suite
  scored: 7,
  merged: 8,
  abandoned: 8,
};

/** Stages at which a branch is actively doing work (vs. waiting its turn). */
const WORKING: ReadonlySet<Stage> = new Set(["exploring", "explored", "evaluating", "uploading", "building", "forked"]);

const RUN_START: ReadonlySet<Stage> = new Set(["started", "ingesting"]);

type StepState = "pending" | "active" | "partial" | "done" | "skipped" | "failed";

interface BranchLane {
  id: string;
  label: string;
  stage: Stage;
  step: number;
  message: string;
  ts: number;
}

function useTicker(active: boolean, ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [active, ms]);
  return now;
}

function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

function forkedLabel(message: string): string | null {
  const m = /^(?:Replay fork|Forked) for: (.+)$/.exec(message);
  return m ? m[1] : null;
}

export type Trace = NonNullable<ReturnType<typeof computeTrace>>;

/** Pure: the state of the current run, from the event feed alone. Exported
 * for tests -- this is the logic that decides what the rail claims. */
export function computeTrace(events: FeedEvent[], nodes: BranchNode[]) {
  // Only the current run: everything since the last run-start event.
  let start = -1;
  for (let i = events.length - 1; i >= 0; i--) {
    if (RUN_START.has(events[i].stage)) { start = i; break; }
  }
  const run = start >= 0 ? events.slice(start) : events;
  if (!run.length) return null;

  const hypothesis = new Map(nodes.map((n) => [n.id, n.hypothesis]));
  const lanes = new Map<string, BranchLane>();
  const runHit = new Set<number>();
  const branchHit = new Map<number, Set<string>>();
  let furthest = 0;

  for (const e of run) {
    if (e.branch_id) {
      const step = BRANCH_STEP[e.stage];
      if (step === undefined) continue;
      const prev = lanes.get(e.branch_id);
      lanes.set(e.branch_id, {
        id: e.branch_id,
        label: prev?.label ?? (forkedLabel(e.message) || hypothesis.get(e.branch_id) || e.branch_id.slice(0, 8)),
        stage: e.stage,
        step,
        message: e.message,
        ts: e.ts,
      });
      if (!branchHit.has(step)) branchHit.set(step, new Set());
      branchHit.get(step)!.add(e.branch_id);
      furthest = Math.max(furthest, step);
    } else if (e.stage === "ingesting" || e.stage === "ingested") {
      runHit.add(INGEST);
    } else if (e.stage === "started" || e.stage === "planning" || e.stage === "planned") {
      runHit.add(PLAN); furthest = Math.max(furthest, PLAN);
    } else if (e.stage === "merging") {
      runHit.add(MERGE); furthest = MERGE;
    }
  }

  const last = run[run.length - 1];
  const failed = last.stage === "error";
  const merged = [...lanes.values()].some((l) => l.stage === "merged");
  const ingestOnly = last.stage === "ingested";
  const done = merged || ingestOnly;
  const branches = [...lanes.values()];
  const n = branches.length;

  const reachedAtLeast = (i: number) => branches.filter((b) => b.step >= i).length;

  const states: StepState[] = STEPS.map((_, i) => {
    const touched = runHit.has(i) || (branchHit.get(i)?.size ?? 0) > 0;
    if (done) return touched ? "done" : i <= furthest ? "skipped" : "pending";
    if (failed) {
      if (i === furthest) return "failed";
      if (i < furthest) return touched ? "done" : "skipped";
      return "pending";
    }
    // Running.
    if (i === INGEST) {
      if (last.stage === "ingesting") return "active";
      return runHit.has(INGEST) ? "done" : furthest > INGEST ? "skipped" : "pending";
    }
    if (i === PLAN) {
      if (!runHit.has(PLAN)) return furthest > PLAN ? "skipped" : "pending";
      return n === 0 && !runHit.has(MERGE) ? "active" : "done";
    }
    if (i === MERGE) return runHit.has(MERGE) ? "active" : "pending";
    // Per-branch steps.
    if (n === 0) return "pending";
    const working = branches.some((b) => b.step === i && WORKING.has(b.stage) && i !== SCORE);
    if (working) return "active";
    const k = reachedAtLeast(i);
    if (k === 0) return furthest > i ? "skipped" : "pending";
    // Every branch is past this step: done if any actually stopped on it,
    // skipped if they all went straight through (e.g. no CodeBuild in
    // simulated mode) -- claiming "done" there would invent a build.
    if (k === n) return touched ? "done" : "skipped";
    return "partial";
  });

  // "Where it is now": the furthest step with live work on it.
  let current = -1;
  states.forEach((s, i) => { if (s === "active" || s === "failed") current = i; });

  return {
    run, last, failed, done, states, current, n,
    startIndex: Math.max(0, start),
    started: run[0].ts,
    ended: done || failed ? last.ts : null,
    counts: STEPS.map((_, i) => (i >= FORK && i <= SCORE ? reachedAtLeast(i) : null)),
    branches: branches.sort((a, b) => b.step - a.step || a.label.localeCompare(b.label)),
  };
}

/** Delay before showing the next event in a replay: the run's own pacing,
 * clamped so an instant (simulated) run is still watchable and a real
 * multi-minute CodeBuild wait doesn't stall the replay. */
function replayDelay(gapSeconds: number): number {
  return Math.min(1500, Math.max(600, gapSeconds * 1000));
}

export function LiveExecutionTrace({ events, nodes }: { events: FeedEvent[]; nodes: BranchNode[] }) {
  const live = useMemo(() => computeTrace(events, nodes), [events, nodes]);
  const liveRunning = !!live && !live.done && !live.failed;

  // Replay: step back through the latest run's *recorded* events. Nothing is
  // invented -- it is the same feed, revealed one event at a time.
  const [cursor, setCursor] = useState<number | null>(null);
  const replaying = cursor !== null && !!live;
  const replayRun = live?.run ?? [];

  useEffect(() => {
    if (cursor === null || !live) return;
    if (cursor >= replayRun.length) {
      const id = window.setTimeout(() => setCursor(null), 1200);
      return () => window.clearTimeout(id);
    }
    const gap = cursor > 0 ? replayRun[cursor]?.ts - replayRun[cursor - 1]?.ts : 0;
    const id = window.setTimeout(() => setCursor((c) => (c === null ? null : c + 1)), replayDelay(gap || 0));
    return () => window.clearTimeout(id);
  }, [cursor, live, replayRun]);

  // A real run starting takes precedence over a replay.
  const liveStart = live?.startIndex;
  const lastStart = useRef(liveStart);
  useEffect(() => {
    if (lastStart.current !== liveStart) { lastStart.current = liveStart; setCursor(null); }
  }, [liveStart]);

  const replayView = useMemo(
    () => (replaying && live
      ? computeTrace(events.slice(0, live.startIndex + Math.max(1, cursor!)), nodes)
      : null),
    [replaying, live, events, nodes, cursor],
  );
  const trace = replayView ?? live;

  const running = !!trace && !trace.done && !trace.failed;
  const now = useTicker(liveRunning);

  if (!trace) {
    return (
      <Shell status={<span className="text-slate-400">No run this session</span>}>
        <Rail states={STEPS.map(() => "pending")} counts={STEPS.map(() => null)} n={0} current={-1} />
        <p className="mt-3 text-[12px] text-slate-400">
          Start a migration and each step lights up here as the run reaches it.
        </p>
      </Shell>
    );
  }

  const elapsed = (trace.ended ?? now / 1000) - trace.started;
  const shown = Math.min(cursor ?? 0, replayRun.length);

  const action = replaying ? (
    <button
      onClick={() => setCursor(null)}
      className="flex items-center gap-1 rounded-md border border-slate-200 dark:border-slate-700 px-2 py-0.5 text-[11px] font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800"
    >
      <Square className="h-3 w-3 fill-current" /> Stop
    </button>
  ) : !liveRunning ? (
    <button
      onClick={() => setCursor(1)}
      title="Play back this run's recorded events step by step"
      className="flex items-center gap-1 rounded-md border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-950/40 px-2 py-0.5 text-[11px] font-semibold text-indigo-700 dark:text-indigo-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/40"
    >
      <Play className="h-3 w-3 fill-current" /> Replay run
    </button>
  ) : null;
  const sinceLast = now / 1000 - trace.last.ts;
  const phase = trace.last.stage === "building" ? codeBuildPhase(trace.last.message) : null;

  const status = replaying ? (
    <span className="flex items-center gap-1.5 font-semibold text-indigo-600 dark:text-indigo-400">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-indigo-500" />
      </span>
      Replaying · event {shown}/{replayRun.length}
    </span>
  ) : trace.failed ? (
    <span className="font-semibold text-rose-600 dark:text-rose-400">
      Failed at {STEPS[trace.current]?.label ?? "start"} · {duration(elapsed)}
    </span>
  ) : trace.done ? (
    <span className="font-semibold text-emerald-600 dark:text-emerald-400">Completed in {duration(elapsed)}</span>
  ) : (
    <span className="flex items-center gap-1.5 font-semibold text-indigo-600 dark:text-indigo-400">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-indigo-500" />
      </span>
      Running · {duration(elapsed)}
    </span>
  );

  return (
    <Shell status={status} action={action}>
      <Rail states={trace.states} counts={trace.counts} n={trace.n} current={trace.current} flowing={running} />

      {/* What is happening right now */}
      <div
        className={`mt-4 flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-[12px] ${
          trace.failed
            ? "border-rose-200 dark:border-rose-900/60 bg-rose-50 dark:bg-rose-950/30 text-rose-700 dark:text-rose-300"
            : trace.done
              ? "border-emerald-200 dark:border-emerald-900/60 bg-emerald-50 dark:bg-emerald-950/30 text-emerald-800 dark:text-emerald-300"
              : "border-indigo-100 dark:border-indigo-900/60 bg-indigo-50/60 dark:bg-indigo-950/30 text-indigo-900 dark:text-indigo-200"
        }`}
      >
        {trace.failed ? <XCircle className="mt-0.5 h-4 w-4 flex-none" /> : trace.done ? <CheckCircle2 className="mt-0.5 h-4 w-4 flex-none" /> : <Spinner />}
        <div className="min-w-0 flex-1">
          <div className="font-semibold">
            {trace.failed ? "Run failed" : trace.done ? "Run complete" : `Now: ${STEPS[trace.current]?.label ?? STAGE_LABEL[trace.last.stage]}`}
            {phase && !trace.done && !trace.failed && <span className="font-normal opacity-80"> · CodeBuild {phase}</span>}
          </div>
          <div className="mt-0.5 break-words opacity-90">{trace.last.message || STAGE_LABEL[trace.last.stage]}</div>
        </div>
        {liveRunning && !replaying && (
          <span className="flex-none whitespace-nowrap font-mono text-[11px] opacity-70" title="Time since the last event">
            {sinceLast < 2 ? "live" : `${duration(sinceLast)} ago`}
          </span>
        )}
      </div>

      {/* Per-branch lanes */}
      {trace.branches.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {trace.branches.map((b) => (
            <BranchRow key={b.id} lane={b} runFailed={trace.failed} now={now} running={running} showTimer={liveRunning && !replaying} />
          ))}
        </div>
      )}
    </Shell>
  );
}

function Shell({
  status, action, children,
}: {
  status: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: 0.2 }}
      className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 shadow-xs"
    >
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">Execution Trace</h2>
        <div className="flex items-center gap-2.5">
          <span className="text-[11px]">{status}</span>
          {action}
        </div>
      </div>
      {children}
    </motion.div>
  );
}

const NODE: Record<StepState, string> = {
  pending: "border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-300 dark:text-slate-600",
  skipped: "border-dashed border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-300 dark:text-slate-600",
  partial: "border-indigo-300 dark:border-indigo-700 bg-indigo-50/60 dark:bg-indigo-950/40 text-indigo-500 dark:text-indigo-400",
  active: "border-indigo-500 bg-indigo-600 text-white shadow-lg shadow-indigo-500/30",
  done: "border-emerald-500 bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400",
  failed: "border-rose-500 bg-rose-50 text-rose-600 dark:bg-rose-950/60 dark:text-rose-400",
};

// Nodes are 56px wide, so their centres sit 28px in from each edge (plus
// the rail's 4px padding); the connector runs centre to centre.
const EDGE = 32;
const CENTER_Y = 28; // 8px top padding + half of a 40px node

const FLOW_CSS = `
@keyframes bmem-flow { 0% { left: 0; opacity: 0 } 12% { opacity: 1 } 88% { opacity: 1 } 100% { left: 100%; opacity: 0 } }
@media (prefers-reduced-motion: reduce) { .bmem-flow-dot { animation: none !important; opacity: 0 !important } }
`;

function Rail({
  states, counts, n, current, flowing = false,
}: {
  states: StepState[];
  counts: (number | null)[];
  n: number;
  current: number;
  flowing?: boolean;
}) {
  // Fill the connector up to the last step that is done or in progress.
  let reach = -1;
  states.forEach((s, i) => { if (s !== "pending") reach = i; });
  const fill = reach <= 0 ? 0 : (reach / (STEPS.length - 1)) * 100;
  const failed = states.includes("failed");

  return (
    <div className="overflow-x-auto">
      <div className="relative flex items-start justify-between px-1 py-2" style={{ minWidth: 560 }}>
        <style>{FLOW_CSS}</style>
        <div
          className="absolute h-0.5 rounded-full bg-slate-200 dark:bg-slate-800"
          style={{ left: EDGE, right: EDGE, top: CENTER_Y - 1 }}
        />
        <motion.div
          className={`absolute rounded-full ${failed ? "bg-rose-400" : "bg-gradient-to-r from-emerald-400 to-indigo-500"}`}
          style={{ left: EDGE, top: CENTER_Y - 1.5, height: 3 }}
          initial={false}
          animate={{ width: `calc((100% - ${EDGE * 2}px) * ${fill / 100})` }}
          transition={{ duration: 0.6, ease: "easeOut" }}
        >
          {/* Flow: pulses travelling along the completed path into the active step. */}
          {flowing && fill > 0 && [0, 0.8].map((delay) => (
            <span
              key={delay}
              className="bmem-flow-dot absolute rounded-full bg-indigo-500"
              style={{
                width: 10, height: 10, top: -3.5, marginLeft: -5,
                boxShadow: "0 0 10px 3px rgba(99, 102, 241, 0.55)",
                animation: `bmem-flow 1.6s linear ${delay}s infinite`,
              }}
            />
          ))}
        </motion.div>
        {STEPS.map((step, i) => {
          const state = states[i];
          const Icon = step.icon;
          const count = counts[i];
          return (
            <div key={step.key} className="relative z-10 flex w-14 flex-col items-center">
              <div className="relative">
                {state === "active" && (
                  <span className="absolute animate-ping rounded-full bg-indigo-400/30" style={{ inset: -6 }} />
                )}
                <motion.div
                  initial={false}
                  animate={{ scale: i === current && state === "active" ? 1.12 : 1 }}
                  className={`relative flex h-10 w-10 items-center justify-center rounded-full border-2 transition-colors duration-300 ${NODE[state]}`}
                  title={`${step.label}: ${state}`}
                >
                  {state === "failed" ? <XCircle className="h-4.5 w-4.5" /> : <Icon className="h-4.5 w-4.5" />}
                </motion.div>
              </div>
              <span
                className={`mt-2 text-[11px] font-semibold ${
                  state === "active" ? "text-indigo-600 dark:text-indigo-400"
                    : state === "failed" ? "text-rose-600 dark:text-rose-400"
                      : state === "pending" || state === "skipped" ? "text-slate-400"
                        : "text-slate-600 dark:text-slate-300"
                }`}
              >
                {step.label}
              </span>
              <span className="mt-0.5 h-3.5 font-mono text-[10px] text-slate-400">
                {count !== null && n > 0 && state !== "pending" ? `${count}/${n}` : state === "skipped" ? "skipped" : ""}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function BranchRow({
  lane, runFailed, now, running, showTimer,
}: {
  lane: BranchLane;
  runFailed: boolean;
  now: number;
  running: boolean;
  showTimer: boolean;
}) {
  const resolved = lane.stage === "merged" || lane.stage === "abandoned";
  const working = running && WORKING.has(lane.stage);
  const tone = lane.stage === "merged"
    ? "text-emerald-700 dark:text-emerald-400 bg-emerald-100 dark:bg-emerald-950/60"
    : lane.stage === "abandoned"
      ? "text-amber-700 dark:text-amber-400 bg-amber-100 dark:bg-amber-950/60"
      : working
        ? "text-indigo-700 dark:text-indigo-300 bg-indigo-100 dark:bg-indigo-950/60"
        : "text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-slate-800";
  const phase = lane.stage === "building" ? codeBuildPhase(lane.message) : null;
  const detail = lane.stage === "scored" || resolved ? lane.message : phase ? `CodeBuild ${phase}` : lane.message;
  // Branch lanes cover Fork..Merge (steps 2..8).
  const segments = MERGE - FORK + 1;
  const progress = lane.step - FORK + (resolved ? 1 : 0);

  return (
    <div className="flex items-center gap-3 rounded-lg border border-slate-100 dark:border-slate-800/80 bg-slate-50/50 dark:bg-slate-900/40 px-3 py-2 text-[11.5px]">
      <span className={`h-2 w-2 flex-none rounded-full ${working ? "bg-indigo-500 animate-pulse" : lane.stage === "merged" ? "bg-emerald-500" : lane.stage === "abandoned" || runFailed ? "bg-amber-500" : "bg-slate-400"}`} />
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium text-slate-700 dark:text-slate-200" title={lane.label}>{lane.label}</div>
        <div className="truncate text-[10.5px] text-slate-500" title={detail}>{detail}</div>
      </div>
      <div className="hidden flex-none gap-0.5 sm:flex" style={{ width: 112 }} aria-hidden>
        {Array.from({ length: segments }, (_, s) => (
          <span
            key={s}
            className={`h-1.5 flex-1 rounded-full ${
              s < progress ? (lane.stage === "abandoned" ? "bg-amber-400" : "bg-emerald-500")
                : s === progress && working ? "bg-indigo-500 animate-pulse"
                  : "bg-slate-200 dark:bg-slate-700"
            }`}
          />
        ))}
      </div>
      <span className={`flex-none whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-bold ${tone}`}>
        {STAGE_LABEL[lane.stage]}
      </span>
      {working && showTimer && (
        <span className="w-10 flex-none text-right font-mono text-[10px] text-slate-400">
          {duration(now / 1000 - lane.ts)}
        </span>
      )}
    </div>
  );
}

function Spinner() {
  return (
    <span className="mt-0.5 inline-block h-4 w-4 flex-none animate-spin rounded-full border-2 border-current border-t-transparent opacity-80" />
  );
}
