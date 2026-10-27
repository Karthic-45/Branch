import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Terminal, FileCode2, ListChecks, ScrollText, Braces, ExternalLink, RefreshCw, CheckCircle2,
  XCircle, MinusCircle, Clock, Trophy, Cloud, AlertTriangle, Boxes, Timer,
} from "lucide-react";
import type { AwsBranch, AwsBuild, AwsBuilds, AwsRun, AwsStorage, S3Object } from "../../lib/types";
import { fetchAwsBuilds, fetchAwsObject, fetchAwsStorage } from "../../lib/api";
import { DiffView, parseUnifiedDiff } from "../patch/DiffView";
import { LoadingCaption, PageSkeleton, Shimmer } from "../common/Loading";
import { CountUp, GrowBar, Ring } from "../common/Motion";

/* AWS Outputs: what each CodeBuild evaluation actually produced.

   AWS Storage answers "what is stored where". This page answers "what did the
   evaluation say": every number here is parsed from the files the build
   container wrote to S3 -- results.xml (JUnit), pytest.log, apply.log,
   result.json and the candidate patch -- fetched through the backend's
   read-only /api/aws/object. Nothing is summarised by a model and nothing is
   estimated: a file that is missing or empty is shown as missing or empty. */

interface Props {
  rootId: string | null;
}

interface JUnit {
  tests: number;
  failures: number;
  errors: number;
  skipped: number;
  seconds: number | null;
  failing: { name: string; classname: string; message: string; kind: "failure" | "error" }[];
}

/** Parse JUnit XML as written by `pytest --junitxml`. Sums every <testsuite>. */
export function parseJUnit(xml: string): JUnit | null {
  if (!xml.trim()) return null;
  const doc = new DOMParser().parseFromString(xml, "application/xml");
  if (doc.getElementsByTagName("parsererror").length) return null;
  const suites = Array.from(doc.getElementsByTagName("testsuite"));
  if (!suites.length) return null;
  const num = (el: Element, a: string) => Number(el.getAttribute(a) ?? 0) || 0;
  const out: JUnit = { tests: 0, failures: 0, errors: 0, skipped: 0, seconds: 0, failing: [] };
  for (const s of suites) {
    out.tests += num(s, "tests");
    out.failures += num(s, "failures");
    out.errors += num(s, "errors");
    out.skipped += num(s, "skipped");
    out.seconds = (out.seconds ?? 0) + (Number(s.getAttribute("time")) || 0);
  }
  for (const tc of Array.from(doc.getElementsByTagName("testcase"))) {
    for (const kind of ["failure", "error"] as const) {
      const f = tc.getElementsByTagName(kind)[0];
      if (f) {
        out.failing.push({
          name: tc.getAttribute("name") ?? "?",
          classname: tc.getAttribute("classname") ?? "",
          message: f.getAttribute("message") ?? f.textContent?.trim().split("\n")[0] ?? "",
          kind,
        });
      }
    }
  }
  return out;
}

type Tab = "tests" | "log" | "patch" | "apply" | "result";
const TABS: { id: Tab; label: string; file: string; icon: typeof Terminal }[] = [
  { id: "tests", label: "Test results", file: "results.xml", icon: ListChecks },
  { id: "log", label: "Test log", file: "pytest.log", icon: Terminal },
  { id: "patch", label: "Patch", file: "candidate.patch", icon: FileCode2 },
  { id: "apply", label: "Apply log", file: "apply.log", icon: ScrollText },
  { id: "result", label: "result.json", file: "result.json", icon: Braces },
];

const CARD = "rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] shadow-xs";

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
function duration(s: number | null): string {
  if (s === null || !Number.isFinite(s)) return "—";
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return m ? `${m}m ${String(r).padStart(2, "0")}s` : `${r}s`;
}
function buildSeconds(b?: AwsBuild): number | null {
  if (!b?.start_time || !b.end_time) return null;
  return (Date.parse(b.end_time) - Date.parse(b.start_time)) / 1000;
}

export function AwsOutputsPage({ rootId }: Props) {
  const [storage, setStorage] = useState<AwsStorage | null>(null);
  const [builds, setBuilds] = useState<AwsBuilds | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [branchId, setBranchId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!rootId) return;
    setLoading(true);
    setError("");
    try {
      const [s, b] = await Promise.all([fetchAwsStorage(rootId), fetchAwsBuilds(rootId)]);
      setStorage(s);
      setBuilds(b);
      if (s.error) setError(s.error);
      else if (b.error) setError(b.error);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [rootId]);

  useEffect(() => { void load(); }, [load]);

  const run: AwsRun | null = useMemo(
    () => storage?.runs.find((r) => r.run_id === runId) ?? storage?.runs[0] ?? null,
    [storage, runId],
  );
  const buildByBranch = useMemo(() => {
    const m = new Map<string, AwsBuild>();
    for (const b of builds?.builds ?? []) if (b.branch_id && !m.has(b.branch_id)) m.set(b.branch_id, b);
    return m;
  }, [builds]);

  // Winner first, then by score.
  const branches = useMemo(() => {
    const list = [...(run?.branches ?? [])];
    return list.sort((a, b) =>
      (a.outcome === "won" ? -1 : 0) - (b.outcome === "won" ? -1 : 0) || (b.score ?? -1) - (a.score ?? -1));
  }, [run]);
  const branch = branches.find((b) => b.branch_id === branchId) ?? branches[0] ?? null;

  if (!rootId) {
    return <Empty title="No project selected" text="Open a project (?root=…) to see what its AWS evaluations produced." />;
  }
  if (loading && !storage) {
    return <PageSkeleton variant="split" caption="Reading evaluation outputs from Amazon S3 and AWS CodeBuild…" />;
  }

  const evaluated = branches.filter((b) => b.total);
  const testsRun = evaluated.reduce((n, b) => n + (b.total ?? 0), 0);
  const testsPassed = evaluated.reduce((n, b) => n + (b.passed ?? 0), 0);
  const runBuilds = (builds?.builds ?? []).filter((b) => b.run_id === run?.run_id);
  const succeeded = runBuilds.filter((b) => b.status === "SUCCEEDED").length;
  const runBytes = branches.reduce((n, b) => n + b.objects.reduce((m, o) => m + o.size, 0), 0);

  return (
    <div className="p-5 sm:p-6 lg:p-7 w-full space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-amber-400 to-orange-500 text-white shadow-md shadow-orange-500/25">
              <Cloud className="h-4.5 w-4.5" />
            </span>
            <h1 className="text-xl sm:text-2xl font-extrabold text-slate-900 dark:text-white">AWS Outputs</h1>
          </div>
          <p className="mt-1.5 text-xs text-slate-500">
            What each CodeBuild evaluation produced — parsed from the files it wrote to S3
            {storage?.bucket ? <> in <span className="font-mono">{storage.bucket}</span></> : null}.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {storage && storage.runs.length > 1 && (
            <select
              value={run?.run_id ?? ""}
              onChange={(e) => { setRunId(e.target.value); setBranchId(null); }}
              className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 font-mono text-xs dark:border-slate-700 dark:bg-slate-900"
            >
              {storage.runs.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  run {r.run_id.slice(0, 8)} · {r.started_at ? new Date(r.started_at).toLocaleString() : "—"}
                </option>
              ))}
            </select>
          )}
          <button
            onClick={() => void load()}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[12px] text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" /> <span className="break-words">{error}</span>
        </div>
      )}

      {!run ? (
        <Empty
          title="No evaluation outputs yet"
          text="Outputs appear here after a migration run: each strategy's CodeBuild container writes its test report, logs and patch to S3."
        />
      ) : (
        <>
          {/* KPI strip */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Kpi icon={Boxes} tone="indigo" label="Strategies" value={branches.length} sub={`${evaluated.length} produced test results`} />
            <Kpi icon={CheckCircle2} tone="emerald" label="Builds succeeded" value={succeeded} sub={`of ${runBuilds.length} CodeBuild builds`} />
            <Kpi icon={ListChecks} tone="blue" label="Tests executed" value={testsRun} sub={testsRun ? `${((testsPassed / testsRun) * 100).toFixed(1)}% passed overall` : "no test reports"} />
            <Kpi icon={Cloud} tone="amber" label="Output stored" value={runBytes} format={bytes} sub={`${branches.reduce((n, b) => n + b.objects.length, 0)} S3 objects`} />
          </div>

          {/* Run facts */}
          <div className={`${CARD} flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3 text-[11.5px] text-slate-500`}>
            <Fact k="Run" v={run.run_id.slice(0, 12)} mono />
            <Fact k="Started" v={run.started_at ? new Date(run.started_at).toLocaleString() : "—"} />
            <Fact k="Repository" v={run.target_repo?.replace("https://github.com/", "") ?? "—"} />
            <Fact k="Tested commit" v={run.base_sha?.slice(0, 10) ?? "—"} mono />
            <Fact k="Model" v={run.reasoning_model ?? "—"} mono />
            <Fact k="CodeBuild" v={run.codebuild_project ?? builds?.project ?? "—"} mono />
          </div>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[340px_1fr] items-start">
            {/* Strategy list */}
            <div className={`${CARD} p-3 space-y-2`}>
              <div className="px-2 pb-1 pt-1 text-[10.5px] font-bold uppercase tracking-wider text-slate-400">Strategies in this run</div>
              {branches.map((b) => (
                <StrategyRow
                  key={b.branch_id}
                  b={b}
                  build={buildByBranch.get(b.branch_id)}
                  active={branch?.branch_id === b.branch_id}
                  onClick={() => setBranchId(b.branch_id)}
                />
              ))}
            </div>

            {/* Detail */}
            <AnimatePresence mode="wait">
              {branch && (
                <motion.div
                  key={branch.branch_id}
                  initial={{ opacity: 0, x: 12 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -12 }}
                  transition={{ duration: 0.22 }}
                >
                  <StrategyDetail b={branch} build={buildByBranch.get(branch.branch_id)} />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </>
      )}
    </div>
  );
}

const TONE = {
  indigo: "bg-indigo-50 text-indigo-600 dark:bg-indigo-950/60 dark:text-indigo-400",
  emerald: "bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400",
  blue: "bg-blue-50 text-blue-600 dark:bg-blue-950/60 dark:text-blue-400",
  amber: "bg-amber-50 text-amber-600 dark:bg-amber-950/60 dark:text-amber-400",
};

function Kpi({
  icon: Icon, tone, label, value, sub, format,
}: {
  icon: typeof Cloud; tone: keyof typeof TONE; label: string; value: number; sub: string; format?: (n: number) => string;
}) {
  return (
    <div className={`${CARD} p-4`}>
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-medium text-slate-500">{label}</span>
        <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${TONE[tone]}`}><Icon className="h-4 w-4" /></span>
      </div>
      <CountUp value={value} format={format} className="mt-1 block text-2xl font-extrabold text-slate-900 dark:text-white" />
      <span className="text-[11px] text-slate-400">{sub}</span>
    </div>
  );
}

function Fact({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <span>
      <span className="font-semibold text-slate-400">{k}</span>{" "}
      <span className={`text-slate-700 dark:text-slate-300 ${mono ? "font-mono" : ""}`}>{v}</span>
    </span>
  );
}

function outcomeOf(b: AwsBranch): { label: string; cls: string; dot: string } {
  if (b.outcome === "won" || b.status === "merged") return { label: "Winner", cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-400", dot: "bg-emerald-500" };
  if (!b.total) return { label: "No tests", cls: "bg-rose-100 text-rose-700 dark:bg-rose-950/60 dark:text-rose-400", dot: "bg-rose-500" };
  if (b.passed === b.total) return { label: "All passed", cls: "bg-blue-100 text-blue-700 dark:bg-blue-950/60 dark:text-blue-400", dot: "bg-blue-500" };
  return { label: "Failing tests", cls: "bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-400", dot: "bg-amber-500" };
}

function StrategyRow({ b, build, active, onClick }: { b: AwsBranch; build?: AwsBuild; active: boolean; onClick: () => void }) {
  const o = outcomeOf(b);
  const pct = b.total ? (b.passed ?? 0) / b.total : 0;
  return (
    <button
      onClick={onClick}
      className={`w-full rounded-xl border p-3 text-left transition-all ${
        active
          ? "border-indigo-400 bg-indigo-50/60 shadow-sm ring-1 ring-indigo-400/30 dark:border-indigo-600 dark:bg-indigo-950/30"
          : "border-slate-100 bg-slate-50/40 hover:border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900/30 dark:hover:bg-slate-800/50"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2">
          <span className={`h-2 w-2 flex-none rounded-full ${o.dot}`} />
          <span className="truncate text-[12.5px] font-semibold text-slate-800 dark:text-slate-100">{b.strategy ?? b.branch_id.slice(0, 8)}</span>
          {b.outcome === "won" && <Trophy className="h-3.5 w-3.5 flex-none text-amber-500" />}
        </span>
        <span className={`flex-none rounded-full px-2 py-0.5 text-[9.5px] font-bold ${o.cls}`}>{o.label}</span>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <GrowBar pct={pct} height={5} className={b.total ? (pct === 1 ? "bg-emerald-500" : "bg-amber-500") : "bg-rose-400"} />
        <span className="flex-none font-mono text-[10.5px] text-slate-500">{b.total ? `${b.passed}/${b.total}` : "—"}</span>
      </div>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-slate-400">
        <span>{build ? `build #${build.build_number}` : "no build"}</span>
        <span>{duration(buildSeconds(build))}</span>
      </div>
    </button>
  );
}

function StrategyDetail({ b, build }: { b: AwsBranch; build?: AwsBuild }) {
  const [tab, setTab] = useState<Tab>(b.total ? "tests" : "apply");
  const [files, setFiles] = useState<Record<string, string | { error: string }>>({});
  const objByName = useMemo(() => new Map(b.objects.map((o) => [o.name, o])), [b.objects]);

  const requested = useRef(new Set<string>());
  const load = useCallback(async (name: string) => {
    const obj = objByName.get(name);
    if (!obj || requested.current.has(name)) return;
    requested.current.add(name);
    if (obj.size === 0) { setFiles((f) => ({ ...f, [name]: "" })); return; }
    try {
      const text = await fetchAwsObject(obj.key);
      setFiles((f) => ({ ...f, [name]: text }));
    } catch (e) {
      setFiles((f) => ({ ...f, [name]: { error: e instanceof Error ? e.message : String(e) } }));
    }
  }, [objByName]);

  // results.xml drives the headline numbers, so it loads with the panel;
  // everything else loads when its tab is opened.
  useEffect(() => { void load("results.xml"); void load("result.json"); }, [load]);
  useEffect(() => {
    const t = TABS.find((x) => x.id === tab);
    if (t) void load(t.file);
  }, [tab, load]);

  const xml = typeof files["results.xml"] === "string" ? files["results.xml"] : "";
  const junit = useMemo(() => (xml ? parseJUnit(xml) : null), [xml]);
  const failed = junit ? junit.failures + junit.errors : (b.total ?? 0) - (b.passed ?? 0);
  const skipped = junit?.skipped ?? 0;
  const total = junit?.tests ?? b.total ?? 0;
  const passed = junit ? total - failed - skipped : (b.passed ?? 0);
  const rate = total ? passed / total : 0;
  const o = outcomeOf(b);

  return (
    <div className={`${CARD} overflow-hidden`}>
      {/* Head */}
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 p-5 dark:border-slate-800">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-bold text-slate-900 dark:text-white">{b.strategy ?? b.branch_id.slice(0, 8)}</h2>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${o.cls}`}>{o.label}</span>
            <span className="rounded-md border border-slate-200 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-500 dark:border-slate-700">{b.branch_id.slice(0, 8)}</span>
          </div>
          {b.hypothesis && b.hypothesis !== b.strategy && (
            <p className="mt-1 max-w-2xl text-[12px] text-slate-500">{b.hypothesis}</p>
          )}
          {b.reason && b.reason !== "ok" && (
            <p className="mt-1.5 font-mono text-[11px] text-amber-700 dark:text-amber-400">evaluation reported: {b.reason}</p>
          )}
        </div>
        {build?.logs_url && (
          <a href={build.logs_url} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-indigo-600 hover:bg-indigo-50 dark:border-slate-700 dark:text-indigo-400 dark:hover:bg-indigo-950/40">
            CloudWatch logs <ExternalLink className="h-3.5 w-3.5" />
          </a>
        )}
      </div>

      {/* Results */}
      <div className="grid grid-cols-1 gap-5 border-b border-slate-100 p-5 md:grid-cols-[auto_1fr] dark:border-slate-800">
        <div className="flex items-center gap-5">
          <Ring pct={rate} size={112} stroke={11} color={rate === 1 ? "#10b981" : total ? "#f59e0b" : "#f43f5e"}>
            <span className="text-xl font-extrabold text-slate-900 dark:text-white">{total ? `${(rate * 100).toFixed(1)}%` : "—"}</span>
            <span className="text-[9.5px] font-semibold uppercase tracking-wide text-slate-400">{total ? "passed" : "no tests"}</span>
          </Ring>
          <div className="space-y-1.5 text-[12px]">
            <Legend icon={CheckCircle2} cls="text-emerald-500" label="Passed" value={passed} />
            <Legend icon={XCircle} cls="text-rose-500" label="Failed" value={failed} />
            <Legend icon={MinusCircle} cls="text-slate-400" label="Skipped" value={skipped} />
            <div className="border-t border-slate-100 pt-1.5 font-semibold text-slate-700 dark:border-slate-800 dark:text-slate-200">
              Total <span className="float-right ml-6 font-mono">{total.toLocaleString()}</span>
            </div>
          </div>
        </div>
        <div className="min-w-0 space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <Mini icon={Timer} label="Build time" value={duration(buildSeconds(build))} />
            <Mini icon={Clock} label="Test time" value={junit?.seconds ? duration(junit.seconds) : "—"} />
            <Mini icon={Boxes} label="Build" value={build ? `#${build.build_number} ${build.status?.toLowerCase() ?? ""}` : "—"} />
          </div>
          {build && build.phases.length > 0 && <Phases build={build} />}
          {!junit && b.total ? (
            <p className="text-[11px] text-slate-400">results.xml not readable; counts come from result.json.</p>
          ) : null}
        </div>
      </div>

      {/* Files */}
      <div className="flex gap-1 overflow-x-auto border-b border-slate-100 px-3 pt-2 dark:border-slate-800">
        {TABS.map((t) => {
          const obj = objByName.get(t.file);
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              disabled={!obj}
              className={`relative flex items-center gap-1.5 whitespace-nowrap rounded-t-lg px-3 py-2 text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                tab === t.id ? "text-indigo-600 dark:text-indigo-400" : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
              }`}
            >
              <t.icon className="h-3.5 w-3.5" />
              {t.label}
              {obj && <span className="font-mono text-[9.5px] font-normal text-slate-400">{bytes(obj.size)}</span>}
              {tab === t.id && <motion.span layoutId="outputs-tab" className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-indigo-600 dark:bg-indigo-400" />}
            </button>
          );
        })}
      </div>
      <div className="p-5">
        <FileView
          tab={tab}
          obj={objByName.get(TABS.find((t) => t.id === tab)!.file)}
          content={files[TABS.find((t) => t.id === tab)!.file]}
          junit={junit}
        />
      </div>
    </div>
  );
}

function Legend({ icon: Icon, cls, label, value }: { icon: typeof CheckCircle2; cls: string; label: string; value: number }) {
  return (
    <div className="flex items-center gap-2 text-slate-600 dark:text-slate-300">
      <Icon className={`h-3.5 w-3.5 ${cls}`} /> {label}
      <span className="ml-auto pl-6 font-mono font-semibold"><CountUp value={value} /></span>
    </div>
  );
}

function Mini({ icon: Icon, label, value }: { icon: typeof Clock; label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50/60 px-3 py-2.5 dark:border-slate-800 dark:bg-slate-900/40">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400"><Icon className="h-3 w-3" /> {label}</div>
      <div className="mt-0.5 truncate font-mono text-[13px] font-bold text-slate-800 dark:text-slate-100" title={value}>{value}</div>
    </div>
  );
}

const PHASE_COLOR: Record<string, string> = {
  SUBMITTED: "bg-slate-400", QUEUED: "bg-slate-400", PROVISIONING: "bg-sky-400", DOWNLOAD_SOURCE: "bg-cyan-500",
  INSTALL: "bg-indigo-500", PRE_BUILD: "bg-violet-500", BUILD: "bg-emerald-500", POST_BUILD: "bg-teal-500",
  UPLOAD_ARTIFACTS: "bg-purple-500", FINALIZING: "bg-slate-500",
};

function Phases({ build }: { build: AwsBuild }) {
  const phases = build.phases.filter((p) => (p.seconds ?? 0) > 0);
  const total = phases.reduce((n, p) => n + (p.seconds ?? 0), 0) || 1;
  return (
    <div>
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">CodeBuild phases</div>
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        {phases.map((p, i) => (
          <motion.div
            key={`${p.phase}-${i}`}
            initial={{ width: 0 }}
            animate={{ width: `${((p.seconds ?? 0) / total) * 100}%` }}
            transition={{ duration: 0.7, delay: i * 0.08, ease: "easeOut" }}
            className={`h-full ${PHASE_COLOR[p.phase ?? ""] ?? "bg-slate-400"}`}
            title={`${p.phase}: ${duration(p.seconds)}`}
          />
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[10.5px] text-slate-500">
        {phases.map((p, i) => (
          <span key={`${p.phase}-l-${i}`} className="flex items-center gap-1">
            <span className={`h-2 w-2 rounded-full ${PHASE_COLOR[p.phase ?? ""] ?? "bg-slate-400"}`} />
            {(p.phase ?? "?").toLowerCase().replace(/_/g, " ")} <b className="font-mono">{duration(p.seconds)}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

function FileView({
  tab, obj, content, junit,
}: {
  tab: Tab; obj?: S3Object; content: string | { error: string } | undefined; junit: JUnit | null;
}) {
  if (!obj) return <p className="text-xs text-slate-400">This file was not written for this strategy.</p>;
  if (content === undefined) {
    return (
      <div className="space-y-2">
        <LoadingCaption text={`Reading ${obj.name} from S3…`} />
        {Array.from({ length: 5 }).map((_, i) => <Shimmer key={i} w={`${90 - i * 9}%`} h={10} />)}
      </div>
    );
  }
  if (typeof content !== "string") return <p className="text-xs text-rose-600">Could not read {obj.name}: {content.error}</p>;

  const raw = (
    <a
      href={`/api/aws/object?key=${encodeURIComponent(obj.key)}`}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 font-mono text-[10.5px] text-slate-400 hover:text-indigo-600"
    >
      s3://…/{obj.key.split("/").slice(-2).join("/")} <ExternalLink className="h-3 w-3" />
    </a>
  );

  if (!content.trim()) {
    return (
      <div className="space-y-2">
        <div className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-xs text-slate-400 dark:border-slate-700">
          {obj.name} is empty (0 bytes).
          {tab === "patch" && " The agent wrote no patch for this strategy, so nothing could be applied or tested."}
        </div>
        {raw}
      </div>
    );
  }

  if (tab === "tests") {
    if (!junit) return <Pre text={content} footer={raw} />;
    return (
      <div className="space-y-3">
        {junit.failing.length === 0 ? (
          <div className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-[12.5px] font-medium text-emerald-800 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-300">
            <CheckCircle2 className="h-4 w-4" /> No failing or erroring tests in this report.
          </div>
        ) : (
          <>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              {junit.failing.length} failing test{junit.failing.length === 1 ? "" : "s"}
              {junit.failing.length > 200 ? " (first 200 shown)" : ""}
            </div>
            <div className="max-h-96 divide-y divide-slate-100 overflow-auto rounded-xl border border-slate-100 dark:divide-slate-800 dark:border-slate-800">
              {junit.failing.slice(0, 200).map((f, i) => (
                <div key={i} className="px-3 py-2">
                  <div className="flex items-center gap-2 font-mono text-[11.5px]">
                    <XCircle className="h-3.5 w-3.5 flex-none text-rose-500" />
                    <span className="truncate text-slate-800 dark:text-slate-100">{f.classname ? `${f.classname}::` : ""}{f.name}</span>
                    <span className="ml-auto flex-none rounded bg-rose-50 px-1.5 text-[9.5px] font-bold uppercase text-rose-600 dark:bg-rose-950/50 dark:text-rose-400">{f.kind}</span>
                  </div>
                  {f.message && <div className="mt-0.5 truncate pl-5 font-mono text-[10.5px] text-slate-500" title={f.message}>{f.message}</div>}
                </div>
              ))}
            </div>
          </>
        )}
        {raw}
      </div>
    );
  }

  if (tab === "patch") {
    const parsed = parseUnifiedDiff(content);
    return (
      <div className="space-y-2">
        {parsed.length ? <DiffView files={parsed} filter="" /> : <Pre text={content} />}
        {raw}
      </div>
    );
  }

  if (tab === "result") {
    let pretty = content;
    try { pretty = JSON.stringify(JSON.parse(content), null, 2); } catch { /* show as stored */ }
    return <Pre text={pretty} footer={raw} />;
  }

  if (tab === "log") {
    const lines = content.split("\n");
    const tail = lines.slice(-120);
    const summary = [...lines].reverse().find((l) => /=+ .*(passed|failed|error).* =+/.test(l));
    return (
      <div className="space-y-2">
        {summary && (
          <div className={`rounded-lg px-3 py-2 font-mono text-[11.5px] font-semibold ${/failed|error/.test(summary) ? "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300" : "bg-emerald-50 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300"}`}>
            {summary.replace(/=/g, "").trim()}
          </div>
        )}
        <Pre text={tail.join("\n")} note={lines.length > tail.length ? `last ${tail.length} of ${lines.length} lines` : undefined} footer={raw} />
      </div>
    );
  }

  return <Pre text={content} footer={raw} />;
}

function Pre({ text, note, footer }: { text: string; note?: string; footer?: React.ReactNode }) {
  return (
    <div className="space-y-2">
      {note && <div className="text-[10.5px] text-slate-400">{note}</div>}
      <pre className="max-h-[28rem] overflow-auto rounded-xl bg-slate-950 p-4 font-mono text-[11.5px] leading-relaxed text-slate-200">{text}</pre>
      {footer}
    </div>
  );
}

function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="p-5 sm:p-6 lg:p-7 w-full">
      <div className={`${CARD} p-12 text-center`}>
        <Cloud className="mx-auto mb-3 h-10 w-10 text-slate-300 dark:text-slate-600" />
        <h2 className="text-base font-bold text-slate-800 dark:text-slate-200">{title}</h2>
        <p className="mx-auto mt-1 max-w-md text-xs text-slate-400">{text}</p>
      </div>
    </div>
  );
}
