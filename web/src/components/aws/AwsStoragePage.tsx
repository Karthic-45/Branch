import { useCallback, useEffect, useMemo, useState } from "react";
import { LoadingCaption, Shimmer } from "../common/Loading";
import {
  AlertTriangle, Archive, ChevronDown, ChevronRight, Cloud, Database, ExternalLink,
  FileCode, FileJson, FileText, HardDrive, Hammer, RefreshCw, Trophy,
  CheckCircle2, XCircle, Loader2, Clock,
} from "lucide-react";
import { Drawer } from "../ui";
import { fetchAwsBuilds, fetchAwsObject, fetchAwsStorage } from "../../lib/api";
import type { AwsBranch, AwsBuild, AwsBuilds, AwsRun, AwsStorage, S3Object } from "../../lib/types";

/* Everything on this page is read live from AWS on load (and on Refresh):
   S3 object listings and CodeBuild build records come from the AWS APIs, and
   CockroachDB only supplies the index -- which runs and branches belong to
   this project. Nothing here is cached or sampled. */

interface Props {
  rootId: string | null;
}

const OBJECT_INFO: Record<string, { label: string; icon: typeof FileText; what: string }> = {
  "candidate.patch": { label: "Patch", icon: FileCode, what: "The code change Claude wrote for this strategy" },
  "score_results.py": { label: "Scorer", icon: FileCode, what: "Script CodeBuild runs to turn the test report into a score" },
  "result.json": { label: "Score", icon: FileJson, what: "Passed / total tests and the reason, written by CodeBuild" },
  "results.xml": { label: "JUnit", icon: FileText, what: "Full per-test report from pytest" },
  "apply.log": { label: "Apply log", icon: FileText, what: "Output of git apply — why a patch did or didn't apply" },
  "pytest.log": { label: "Test log", icon: FileText, what: "Everything pytest printed" },
  "applied.bundle": { label: "Git bundle", icon: Archive, what: "The applied, validated commit — clone or pull from it" },
};

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—";
}

function secs(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n < 60 ? `${n}s` : `${Math.floor(n / 60)}m ${String(n % 60).padStart(2, "0")}s`;
}

export function AwsStoragePage({ rootId }: Props) {
  const [storage, setStorage] = useState<AwsStorage | null>(null);
  const [builds, setBuilds] = useState<AwsBuilds | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);
  const [preview, setPreview] = useState<{ obj: S3Object; text: string | null; error: string | null } | null>(null);

  const load = useCallback(async () => {
    if (!rootId) return;
    setLoading(true);
    setError(null);
    try {
      const [s, b] = await Promise.all([fetchAwsStorage(rootId), fetchAwsBuilds(rootId)]);
      setStorage(s);
      setBuilds(b);
      setFetchedAt(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [rootId]);

  useEffect(() => { void load(); }, [load]);

  const openObject = async (obj: S3Object) => {
    setPreview({ obj, text: null, error: null });
    try {
      const text = await fetchAwsObject(obj.key);
      setPreview({ obj, text, error: null });
    } catch (e) {
      setPreview({ obj, text: null, error: e instanceof Error ? e.message : String(e) });
    }
  };

  const buildsByBranch = useMemo(() => {
    const m = new Map<string, AwsBuild>();
    for (const b of builds?.builds ?? []) if (b.branch_id && !m.has(b.branch_id)) m.set(b.branch_id, b);
    return m;
  }, [builds]);

  const branchCount = storage?.runs.reduce((n, r) => n + r.branches.length, 0) ?? 0;
  const liveMerges = (storage?.merges ?? []).filter((m) => !m.reverted_at).length;

  if (!rootId) {
    return <div className="p-7 text-sm text-slate-500">Open a project (?root=…) to see its AWS storage.</div>;
  }

  return (
    <div className="flex min-h-full flex-col gap-6 p-5 sm:p-6 lg:p-7 w-full">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold text-slate-900 dark:text-white">
            <Cloud className="h-5 w-5 text-orange-500" /> AWS Storage
          </h1>
          <p className="mt-1 text-xs text-slate-500">
            Live from Amazon S3 and AWS CodeBuild{storage ? ` · ${storage.region}` : ""}. Click any file to open it straight from S3.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {fetchedAt && <span className="font-mono text-[11px] text-slate-400">fetched {fetchedAt.toLocaleTimeString()}</span>}
          <button
            onClick={() => void load()}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-1.5 text-xs font-semibold text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh from AWS
          </button>
        </div>
      </div>

      {error && <Notice tone="bad">{error}</Notice>}
      {storage?.error && <Notice tone="warn">{storage.error}</Notice>}
      {builds?.error && <Notice tone="warn">{builds.error}</Notice>}

      {/* Where each piece lives */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <StoreCard
          icon={Database}
          tone="indigo"
          title="CockroachDB"
          subtitle="The index & decisions"
          lines={[
            `${storage?.runs.length ?? "…"} runs · ${branchCount} branches`,
            `${liveMerges} live decision${liveMerges === 1 ? "" : "s"} (merge_log)`,
            "Beliefs + code embeddings (memory)",
          ]}
        />
        <StoreCard
          icon={HardDrive}
          tone="orange"
          title={`S3 · ${storage?.bucket ?? "…"}`}
          subtitle="Files: patches, scores, logs, bundles"
          lines={[
            `${storage ? storage.totals.objects : "…"} objects · ${storage ? bytes(storage.totals.bytes) : "…"}`,
            "runs/<run>/<branch>/…",
            "applied/<merge>/applied.bundle",
          ]}
        />
        <StoreCard
          icon={Hammer}
          tone="emerald"
          title={`CodeBuild · ${builds?.project ?? "…"}`}
          subtitle="Runs each patch against the real tests"
          lines={[
            `${builds ? builds.builds.length : "…"} builds for this project`,
            `${(builds?.builds ?? []).filter((b) => b.status === "SUCCEEDED").length} succeeded · ${(builds?.builds ?? []).filter((b) => b.status === "IN_PROGRESS").length} running`,
            "Reads patch from S3, writes results back",
          ]}
        />
      </div>

      {/* Runs → branches → S3 objects */}
      <section className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 shadow-xs">
        <h2 className="mb-1 text-sm font-bold text-slate-800 dark:text-slate-200">What is stored for each run</h2>
        <p className="mb-4 text-[11.5px] text-slate-500">
          Each migration run writes one folder per branch to <span className="font-mono">s3://{storage?.bucket ?? "bucket"}/runs/&lt;run_id&gt;/&lt;branch_id&gt;/</span>. The database row for the branch stores the link back to it.
        </p>
        {!storage && loading && (
          <div className="space-y-3">
            <LoadingCaption text="Listing the project's objects in Amazon S3…" />
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="space-y-2 rounded-xl border border-slate-100 dark:border-slate-800 p-4">
                <Shimmer w="45%" h={12} />
                <Shimmer w="70%" h={9} />
                <Shimmer w="60%" h={9} />
              </div>
            ))}
          </div>
        )}
        {storage && storage.runs.length === 0 && (
          <p className="text-xs text-slate-400">No runs with stored artifacts for this project yet.</p>
        )}
        <div className="space-y-3">
          {storage?.runs.map((run, i) => (
            <RunCard
              key={run.run_id}
              run={run}
              bucket={storage.bucket}
              defaultOpen={i === 0}
              buildsByBranch={buildsByBranch}
              onOpen={openObject}
            />
          ))}
        </div>
      </section>

      {/* Applied bundles */}
      <section className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 shadow-xs">
        <h2 className="mb-1 text-sm font-bold text-slate-800 dark:text-slate-200">Applied commits</h2>
        <p className="mb-3 text-[11.5px] text-slate-500">
          When a decision is applied, the validated commit is saved as a git bundle at <span className="font-mono">applied/&lt;merge_id&gt;/applied.bundle</span> — download it and <span className="font-mono">git pull applied.bundle HEAD</span>.
        </p>
        {storage && storage.applied.length === 0 ? (
          <p className="text-xs text-slate-400">No decision has been applied yet.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {storage?.applied.map((o) => (
              <ObjectChip key={o.key} obj={o} onOpen={undefined} extra={`merge ${o.merge_id.slice(0, 8)}`} />
            ))}
          </div>
        )}
      </section>

      {/* CodeBuild */}
      <section className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 sm:p-6 shadow-xs space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-tr from-amber-500/15 via-orange-500/10 to-amber-500/5 border border-amber-500/20 text-amber-600 dark:text-amber-400 shadow-2xs">
              <Hammer className="h-4.5 w-4.5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-bold text-slate-900 dark:text-slate-100 tracking-tight">
                  CodeBuild Evaluations
                </h2>
                <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 border border-slate-200/60 dark:border-slate-700/60">
                  {builds?.builds.length ?? 0} Builds
                </span>
              </div>
              <p className="text-[11.5px] text-slate-500 dark:text-slate-400 mt-0.5">
                One ephemeral container per branch: download patch from S3, run test suite on clean checkout, upload score
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-slate-800/80 px-2.5 py-1 rounded-lg border border-slate-200/60 dark:border-slate-700/60">
              {builds?.project ?? "branchable-memory-eval"}
            </span>
            <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-800/40 px-2 py-1 rounded-lg border border-slate-200/50 dark:border-slate-700/50">
              {builds?.region ?? "us-east-1"}
            </span>
          </div>
        </div>

        {builds && builds.builds.length === 0 && (
          <div className="rounded-xl border border-dashed border-slate-200 dark:border-slate-800 p-8 text-center text-xs text-slate-400">
            No builds found for this project yet.
          </div>
        )}

        <div className="space-y-3">
          {builds?.builds.slice(0, 24).map((b) => <BuildRow key={b.id} build={b} />)}
        </div>
      </section>

      <Drawer
        open={preview !== null}
        onClose={() => setPreview(null)}
        title={preview ? `${OBJECT_INFO[preview.obj.name]?.label ?? preview.obj.name}` : ""}
        subtitle={preview ? <span className="break-all font-mono text-[11px]">s3://{storage?.bucket}/{preview.obj.key}</span> : undefined}
        width={640}
      >
        {preview && (
          <div className="space-y-3">
            <p className="text-[12px] text-slate-500">
              {OBJECT_INFO[preview.obj.name]?.what ?? "Stored object"} · {bytes(preview.obj.size)} · {when(preview.obj.last_modified)}
            </p>
            {preview.error && <Notice tone="bad">{preview.error}</Notice>}
            {!preview.text && !preview.error && <p className="text-xs text-slate-400">Reading from S3…</p>}
            {preview.text !== null && (
              <pre style={{ maxHeight: "70vh" }} className="overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-800 dark:text-slate-200">
                {preview.text || "(empty file)"}
              </pre>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
}

function Notice({ tone, children }: { tone: "warn" | "bad"; children: React.ReactNode }) {
  const cls = tone === "bad"
    ? "border-rose-200 dark:border-rose-900/60 bg-rose-50 dark:bg-rose-950/30 text-rose-700 dark:text-rose-300"
    : "border-amber-200 dark:border-amber-900/60 bg-amber-50 dark:bg-amber-950/30 text-amber-800 dark:text-amber-300";
  return (
    <div className={`flex items-start gap-2 rounded-xl border px-3.5 py-2.5 text-[12px] ${cls}`}>
      <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
      <span className="break-words">{children}</span>
    </div>
  );
}

const STORE_TONE = {
  indigo: "bg-indigo-50 dark:bg-indigo-950/60 text-indigo-600",
  orange: "bg-orange-50 dark:bg-orange-950/50 text-orange-600",
  emerald: "bg-emerald-50 dark:bg-emerald-950/50 text-emerald-600",
};

function StoreCard({
  icon: Icon, tone, title, subtitle, lines,
}: {
  icon: typeof Database;
  tone: keyof typeof STORE_TONE;
  title: string;
  subtitle: string;
  lines: string[];
}) {
  return (
    <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-4 shadow-xs">
      <div className="flex items-center gap-2.5">
        <div className={`flex h-8 w-8 flex-none items-center justify-center rounded-xl ${STORE_TONE[tone]}`}>
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <div className="truncate text-[13px] font-bold text-slate-800 dark:text-slate-100" title={title}>{title}</div>
          <div className="text-[11px] text-slate-500">{subtitle}</div>
        </div>
      </div>
      <ul className="mt-3 space-y-1 text-[11.5px] text-slate-600 dark:text-slate-300">
        {lines.map((l) => <li key={l} className="truncate font-mono" title={l}>{l}</li>)}
      </ul>
    </div>
  );
}

function RunCard({
  run, bucket, defaultOpen, buildsByBranch, onOpen,
}: {
  run: AwsRun;
  bucket: string | null;
  defaultOpen: boolean;
  buildsByBranch: Map<string, AwsBuild>;
  onOpen: (o: S3Object) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const files = run.branches.reduce((n, b) => n + b.objects.length, 0);
  const size = run.branches.reduce((n, b) => n + b.objects.reduce((m, o) => m + o.size, 0), 0);
  const winner = run.branches.find((b) => b.outcome === "won" || b.status === "merged");

  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-800">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left hover:bg-slate-50 dark:hover:bg-slate-900/50"
      >
        {open ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 text-[12.5px] font-semibold text-slate-800 dark:text-slate-100">
            <span>Run {run.run_id.slice(0, 8)}</span>
            <span className="font-normal text-slate-400">· {when(run.started_at)}</span>
            {winner && (
              <span className="flex items-center gap-1 rounded-full bg-amber-50 dark:bg-amber-950/40 px-2 py-0.5 text-[10px] font-bold text-amber-700 dark:text-amber-400">
                <Trophy className="h-3 w-3" /> {winner.strategy}
              </span>
            )}
          </div>
          <div className="truncate font-mono text-[10.5px] text-slate-400">
            s3://{bucket}/runs/{run.run_id}/ · {files} files · {bytes(size)}
            {run.reasoning_model ? ` · ${run.reasoning_model}` : ""}
            {run.base_sha ? ` · tested @ ${run.base_sha.slice(0, 7)}` : ""}
          </div>
        </div>
      </button>
      {open && (
        <div className="divide-y divide-slate-100 dark:divide-slate-800 border-t border-slate-100 dark:border-slate-800">
          {run.branches.map((b) => (
            <BranchRow key={b.branch_id} branch={b} build={buildsByBranch.get(b.branch_id)} onOpen={onOpen} />
          ))}
        </div>
      )}
    </div>
  );
}

function BranchRow({ branch, build, onOpen }: { branch: AwsBranch; build?: AwsBuild; onOpen: (o: S3Object) => void }) {
  const won = branch.outcome === "won" || branch.status === "merged";
  const score = branch.total ? `${branch.passed}/${branch.total} tests` : branch.score !== null ? `${Math.round(branch.score * 100)}%` : "not scored";
  return (
    <div className="px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`h-2 w-2 rounded-full ${won ? "bg-emerald-500" : branch.status === "abandoned" ? "bg-amber-500" : "bg-slate-400"}`} />
        <span className="text-[12.5px] font-semibold text-slate-800 dark:text-slate-100">{branch.strategy ?? branch.branch_id.slice(0, 8)}</span>
        <span className="font-mono text-[10.5px] text-slate-400">{branch.branch_id.slice(0, 8)}</span>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${won ? "bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-400" : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300"}`}>
          {won ? "Winner" : branch.status}
        </span>
        <span className="text-[11px] text-slate-500">{score}{branch.reason && branch.reason !== "ok" ? ` · ${branch.reason}` : ""}</span>
        {build && (
          <span className="ml-auto font-mono text-[10.5px] text-slate-400">
            CodeBuild #{build.build_number ?? "?"} · {build.status?.toLowerCase()}
          </span>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5 pl-4">
        {branch.objects.length === 0 && <span className="text-[11px] text-slate-400">No objects in S3 for this branch.</span>}
        {branch.objects.map((o) => <ObjectChip key={o.key} obj={o} onOpen={onOpen} />)}
      </div>
    </div>
  );
}

function ObjectChip({ obj, onOpen, extra }: { obj: S3Object; onOpen?: (o: S3Object) => void; extra?: string }) {
  const info = OBJECT_INFO[obj.name];
  const Icon = info?.icon ?? FileText;
  const content = (
    <>
      <Icon className="h-3.5 w-3.5 flex-none text-slate-400" />
      <span className="font-mono">{obj.name}</span>
      <span className="text-slate-400">{bytes(obj.size)}</span>
      {extra && <span className="text-slate-400">· {extra}</span>}
    </>
  );
  const cls = "flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/60 px-2 py-1 text-[11px] text-slate-700 dark:text-slate-200";
  return onOpen ? (
    <button onClick={() => onOpen(obj)} title={info?.what} className={`${cls} hover:border-indigo-300 hover:bg-indigo-50 dark:hover:bg-indigo-950/40`}>
      {content}
    </button>
  ) : (
    <span title={info?.what} className={cls}>{content}</span>
  );
}

const PHASE_STYLES: Record<string, { bg: string; dot: string; label: string }> = {
  SUBMITTED: { bg: "bg-slate-400", dot: "bg-slate-400", label: "submitted" },
  QUEUED: { bg: "bg-slate-400", dot: "bg-slate-400", label: "queued" },
  PROVISIONING: { bg: "bg-gradient-to-r from-sky-400 to-sky-500", dot: "bg-sky-400", label: "provisioning" },
  DOWNLOAD_SOURCE: { bg: "bg-gradient-to-r from-sky-500 to-blue-500", dot: "bg-sky-500", label: "download source" },
  INSTALL: { bg: "bg-gradient-to-r from-indigo-400 to-indigo-500", dot: "bg-indigo-400", label: "install" },
  PRE_BUILD: { bg: "bg-gradient-to-r from-indigo-500 to-violet-500", dot: "bg-indigo-500", label: "pre-build" },
  BUILD: { bg: "bg-gradient-to-r from-emerald-500 via-teal-400 to-emerald-400 shadow-[0_0_8px_rgba(16,185,129,0.35)]", dot: "bg-emerald-500", label: "build / test" },
  POST_BUILD: { bg: "bg-gradient-to-r from-teal-500 to-cyan-500", dot: "bg-teal-500", label: "post-build" },
  UPLOAD_ARTIFACTS: { bg: "bg-gradient-to-r from-purple-400 to-purple-500", dot: "bg-purple-400", label: "artifacts" },
  FINALIZING: { bg: "bg-slate-400", dot: "bg-slate-400", label: "finalizing" },
};

function BuildRow({ build }: { build: AwsBuild }) {
  const total = build.phases.reduce((n, p) => n + (p.seconds ?? 0), 0);
  const isSucceeded = build.status === "SUCCEEDED";
  const isInProgress = build.status === "IN_PROGRESS";
  const isFailed = build.status === "FAILED";

  const statusBadge = isSucceeded ? (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 font-bold text-[10px] uppercase tracking-wider border border-emerald-200 dark:border-emerald-800/60 shadow-2xs">
      <CheckCircle2 className="h-3 w-3 text-emerald-500" />
      Succeeded
    </span>
  ) : isInProgress ? (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-blue-100 dark:bg-blue-950/60 text-blue-700 dark:text-blue-300 font-bold text-[10px] uppercase tracking-wider border border-blue-200 dark:border-blue-800/60 shadow-2xs">
      <Loader2 className="h-3 w-3 animate-spin text-blue-500" />
      In Progress
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-rose-100 dark:bg-rose-950/60 text-rose-700 dark:text-rose-300 font-bold text-[10px] uppercase tracking-wider border border-rose-200 dark:border-rose-800/60 shadow-2xs">
      <XCircle className="h-3 w-3 text-rose-500" />
      {build.status?.replace(/_/g, " ").toLowerCase() ?? "failed"}
    </span>
  );

  const activePhases = build.phases.filter((p) => (p.seconds ?? 0) > 0);

  return (
    <div className={`group rounded-xl border p-4 transition-all duration-200 space-y-3 ${
      isSucceeded
        ? "border-slate-200/90 dark:border-slate-800 bg-white dark:bg-slate-900/40 hover:border-emerald-400/80 dark:hover:border-emerald-800/60 hover:shadow-sm"
        : isInProgress
          ? "border-blue-300 dark:border-blue-800 bg-blue-50/20 dark:bg-blue-950/20 shadow-xs"
          : "border-slate-200/90 dark:border-slate-800 bg-white dark:bg-slate-900/40 hover:border-slate-300"
    }`}>
      {/* Top Header Row */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2.5 min-w-0">
          <span className="font-mono text-xs font-black text-slate-800 dark:text-slate-200 bg-slate-100 dark:bg-slate-800 px-2 py-0.5 rounded-md border border-slate-200/80 dark:border-slate-700 shadow-2xs">
            #{build.build_number ?? "?"}
          </span>
          <span className="font-bold text-xs sm:text-[13px] text-slate-900 dark:text-slate-100 truncate max-w-sm" title={build.strategy ?? build.branch_id ?? ""}>
            {build.strategy ?? build.branch_id?.slice(0, 8) ?? "—"}
          </span>
          {build.branch_id && (
            <span className="font-mono text-[10.5px] text-slate-400">
              {build.branch_id.slice(0, 8)}
            </span>
          )}
          {statusBadge}
        </div>

        <div className="flex items-center gap-3 ml-auto flex-none">
          <div className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400 font-medium">
            <Clock className="h-3.5 w-3.5 text-slate-400" />
            <span>{when(build.start_time)}</span>
            <span>·</span>
            <span className="font-mono font-semibold text-slate-700 dark:text-slate-300">{secs(total)}</span>
          </div>

          {build.logs_url && (
            <a
              href={build.logs_url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-50 dark:bg-slate-800/80 hover:bg-indigo-50 dark:hover:bg-indigo-950/50 text-indigo-600 dark:text-indigo-400 border border-slate-200/80 dark:border-slate-700/80 text-xs font-semibold shadow-2xs transition-all hover:scale-102"
            >
              <span>CloudWatch logs</span>
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      </div>

      {/* Segmented Phase Progress Pipeline */}
      {total > 0 && (
        <div className="space-y-2 pt-0.5">
          {/* Progress track */}
          <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800 p-0.5 gap-0.5 shadow-inner">
            {activePhases.map((p, i) => {
              const widthPct = ((p.seconds ?? 0) / total) * 100;
              const style = PHASE_STYLES[p.phase ?? ""] ?? { bg: "bg-slate-400", dot: "bg-slate-400", label: p.phase };
              return (
                <div
                  key={`${p.phase}-${i}`}
                  className={`h-full rounded-full ${style.bg} transition-all duration-300 hover:brightness-110 cursor-pointer`}
                  style={{ width: `${widthPct}%` }}
                  title={`${p.phase}: ${secs(p.seconds)} (${p.status?.toLowerCase() ?? "done"})`}
                />
              );
            })}
          </div>

          {/* Phase timing chips underneath */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px]">
            {activePhases.map((p, i) => {
              const style = PHASE_STYLES[p.phase ?? ""] ?? { bg: "bg-slate-400", dot: "bg-slate-400", label: p.phase?.replace(/_/g, " ").toLowerCase() };
              return (
                <span
                  key={`${p.phase}-l-${i}`}
                  className="inline-flex items-center gap-1.5 text-slate-600 dark:text-slate-400 font-medium"
                >
                  <span className={`h-2 w-2 rounded-full ${style.dot} shadow-2xs`} />
                  <span className="capitalize">{style.label}:</span>
                  <span className="font-mono font-semibold text-slate-800 dark:text-slate-200">{secs(p.seconds)}</span>
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
