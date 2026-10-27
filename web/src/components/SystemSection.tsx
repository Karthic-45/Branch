import { useCallback, useEffect, useState } from "react";
import { Database, DollarSign, Cloud, ArrowRight, RefreshCw } from "lucide-react";
import type {
  AwsBuilds, AwsStorage, ClusterStats, ComponentCheck, CostReport, FeedEvent, ReadinessReport, Stage,
} from "../lib/types";
import type { Theme } from "../hooks/useTheme";
import { fetchAwsBuilds, fetchAwsStorage, fetchClusterStats, fetchCostReport } from "../lib/api";
import { LoadingCaption, Shimmer } from "./common/Loading";
import { motion } from "framer-motion";

/* Every value here is fetched from the backend -- cluster introspection,
   the measured cost report, live S3/CodeBuild listings and the readiness
   probes. Where a source failed or returned nothing, the page says so
   instead of substituting a plausible value. */

interface Props {
  rootId: string | null;
  readiness?: ReadinessReport | null;
  events?: FeedEvent[];
  branchStage?: Map<string, Stage>;
  theme?: Theme;
  initial?: string;
  only?: string[];
}

type SourceKey = "cluster" | "cost" | "storage" | "builds";

export function SystemSection({ rootId, readiness }: Props) {
  const [cluster, setCluster] = useState<ClusterStats | null>(null);
  const [cost, setCost] = useState<CostReport | null>(null);
  const [storage, setStorage] = useState<AwsStorage | null>(null);
  const [builds, setBuilds] = useState<AwsBuilds | null>(null);
  const [failed, setFailed] = useState<Partial<Record<SourceKey, string>>>({});
  const [loading, setLoading] = useState(true);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const errs: Partial<Record<SourceKey, string>> = {};
    const grab = async <T,>(key: SourceKey, p: Promise<T>): Promise<T | null> => {
      try { return await p; } catch (e) { errs[key] = e instanceof Error ? e.message : String(e); return null; }
    };
    const [c, co, s, b] = await Promise.all([
      grab("cluster", fetchClusterStats()),
      rootId ? grab("cost", fetchCostReport(rootId)) : Promise.resolve(null),
      rootId ? grab("storage", fetchAwsStorage(rootId)) : Promise.resolve(null),
      rootId ? grab("builds", fetchAwsBuilds(rootId)) : Promise.resolve(null),
    ]);
    setCluster(c); setCost(co); setStorage(s); setBuilds(b);
    if (s?.error) errs.storage = s.error;
    if (b?.error) errs.builds = b.error;
    setFailed(errs);
    setFetchedAt(new Date());
    setLoading(false);
  }, [rootId]);

  useEffect(() => { void load(); }, [load]);

  const totalDbRows = cluster
    ? cluster.branches_total + cluster.memory_total + cluster.merges_total +
      cluster.manifests_total + cluster.outcomes_total + cluster.applications_total
    : null;
  const totalBuilds = builds ? builds.builds.length : null;
  const passedBuilds = builds ? builds.builds.filter((b) => b.status === "SUCCEEDED").length : null;
  const failures = Object.keys(failed).length;
  const dash = (v: number | null | undefined, fmt: (n: number) => string = (n) => n.toLocaleString()) =>
    loading ? "…" : v === null || v === undefined ? "—" : fmt(v);

  const check = (name: string) => readiness?.checks.find((c) => c.name.startsWith(name));

  return (
    <div className="p-5 sm:p-6 lg:p-7 w-full space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-900 dark:text-white">System &amp; Observability</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Read from CockroachDB, S3, CodeBuild and the readiness probes{fetchedAt ? ` · fetched ${fetchedAt.toLocaleTimeString()}` : ""}.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-bold ${
            loading
              ? "border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-slate-500"
              : failures === 0
                ? "border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-400"
                : "border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/60 text-amber-700 dark:text-amber-400"
          }`}>
            <span className={`h-2 w-2 rounded-full ${loading ? "bg-slate-400" : failures === 0 ? "bg-emerald-500" : "bg-amber-500"}`} />
            {loading ? "Fetching…" : failures === 0 ? "All sources answered" : `${failures} source${failures === 1 ? "" : "s"} failed`}
          </span>
          <button
            onClick={() => void load()}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-1.5 text-xs font-semibold text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
          </button>
        </div>
      </div>

      {failures > 0 && (
        <div className="rounded-xl border border-amber-200 dark:border-amber-900/60 bg-amber-50 dark:bg-amber-950/30 px-4 py-3 text-[12px] text-amber-800 dark:text-amber-300 space-y-0.5">
          {Object.entries(failed).map(([k, v]) => <div key={k} className="break-words"><b>{k}:</b> {v}</div>)}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        <Card icon={Database} tone="rose" title="CockroachDB" subtitle={cluster?.database ? `database ${cluster.database}` : "—"}>
          <Num label="Total rows" value={dash(totalDbRows)} />
          <Num label="Branches" value={dash(cluster?.branches_total)} />
          <Num label="Memories" value={dash(cluster?.memory_total)} />
          <Num label="Vector index" value={loading ? "…" : cluster?.vector_index_present == null ? "—" : cluster.vector_index_present ? "present" : "missing"} />
          <Num label="GC window" value={dash(cluster?.gc_ttl_seconds, (n) => `${(n / 3600).toFixed(n % 3600 ? 1 : 0)} h`)} />
          <Num label="Active queries" value={dash(cluster?.active_queries)} />
        </Card>

        <Card icon={Cloud} tone="amber" title="AWS" subtitle={storage ? `${storage.bucket ?? "—"} · ${storage.region}` : "—"}>
          <Num label="S3 objects" value={dash(storage?.totals.objects)} />
          <Num label="S3 storage" value={dash(storage?.totals.bytes, bytes)} />
          <Num label="CodeBuild runs" value={dash(totalBuilds)} />
          <Num label="Succeeded" value={dash(passedBuilds)} />
          <Num label="Project" value={loading ? "…" : builds?.project ?? "—"} wide />
        </Card>

        <Card icon={DollarSign} tone="emerald" title="Cost report" subtitle={cost ? (cost.pricing_configured ? "priced with configured rates" : "usage measured, no prices configured") : "—"}>
          <Num label="Estimated total" value={loading ? "…" : cost?.total_estimated_usd != null ? `$${cost.total_estimated_usd.toFixed(2)}` : "not priced"} wide />
          <Num label="Line items" value={dash(cost?.lines.length)} />
        </Card>
      </div>

      {cost && cost.lines.length > 0 && (
        <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 shadow-xs">
          <h2 className="mb-3 text-sm font-bold text-slate-800 dark:text-slate-200">Measured usage</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-slate-400">
                <tr>
                  <th className="py-1.5 pr-3 font-semibold">Resource</th>
                  <th className="py-1.5 pr-3 font-semibold">Usage</th>
                  <th className="py-1.5 pr-3 font-semibold">Source</th>
                  <th className="py-1.5 text-right font-semibold">Est. USD</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {cost.lines.map((l) => (
                  <tr key={l.resource} title={l.note}>
                    <td className="py-1.5 pr-3 font-medium text-slate-700 dark:text-slate-200">{l.resource}</td>
                    <td className="py-1.5 pr-3 font-mono text-slate-600 dark:text-slate-300">{l.usage}</td>
                    <td className="py-1.5 pr-3 text-slate-500">{l.measured ? "measured" : "estimated"}</td>
                    <td className="py-1.5 text-right font-mono">{l.estimated_usd != null ? `$${l.estimated_usd.toFixed(4)}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <BuildTimeChart builds={builds} loading={loading} />
        <ProbeLatencyChart readiness={readiness ?? null} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 shadow-xs space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">CodeBuild evaluations</h2>
            <span className="text-[11px] font-mono text-slate-500">{builds?.project ?? "—"}</span>
          </div>
          {loading ? (
            <div className="space-y-2">
              <LoadingCaption text="Listing CodeBuild evaluations…" />
              {Array.from({ length: 3 }).map((_, i) => <Shimmer key={i} h={40} round={12} />)}
            </div>
          ) : !builds || builds.builds.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 dark:border-slate-800 p-8 text-center text-xs text-slate-400">
              {builds ? "No CodeBuild evaluations for this project yet." : "CodeBuild could not be read."}
            </div>
          ) : (
            <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
              {builds.builds.map((b) => (
                <div key={b.id} className="rounded-xl border border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/40 p-3 flex items-center justify-between gap-3 text-xs">
                  <div className="min-w-0">
                    <div className="font-mono font-bold text-slate-800 dark:text-slate-200">
                      #{b.build_number ?? "?"} <span className="font-sans font-medium text-slate-500">{b.strategy ?? ""}</span>
                    </div>
                    <div className="text-[11px] text-slate-400">{b.start_time ? new Date(b.start_time).toLocaleString() : "—"}</div>
                  </div>
                  <span className={`flex-none rounded-full px-2 py-0.5 text-[10px] font-bold ${
                    b.status === "SUCCEEDED" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-400"
                      : b.status === "IN_PROGRESS" ? "bg-blue-100 text-blue-700 dark:bg-blue-950/60 dark:text-blue-400"
                      : "bg-rose-100 text-rose-700 dark:bg-rose-950/60 dark:text-rose-400"
                  }`}>
                    {(b.status ?? "unknown").replace(/_/g, " ").toLowerCase()}
                  </span>
                </div>
              ))}
            </div>
          )}
          <div className="border-t border-slate-100 dark:border-slate-800 pt-3 flex items-center justify-between text-xs text-slate-400">
            <span>Build success rate: {totalBuilds ? `${Math.round(((passedBuilds ?? 0) / totalBuilds) * 100)}%` : "—"}</span>
            <span>Region: {builds?.region ?? "—"}</span>
          </div>
          <p className="text-[10.5px] text-slate-400">
            "Succeeded" means the build ran to completion; a branch's test score is separate and shown on Decisions.
          </p>
        </div>

        <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 shadow-xs space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">Components</h2>
            <span className="text-[11px] text-slate-400">
              {readiness ? `readiness: ${readiness.overall.replace(/_/g, " ")}` : "readiness not checked yet"}
            </span>
          </div>
          <div className="flex flex-col items-center gap-3 py-2">
            <div className="flex w-full items-stretch gap-2">
              <Node name="FastAPI" sub="this backend" status={readiness ? "ready" : undefined} />
              <ArrowRight className="h-4 w-4 flex-none self-center text-slate-300" />
              <Node name="CockroachDB" check={check("CockroachDB")} />
              <ArrowRight className="h-4 w-4 flex-none self-center text-slate-300" />
              <Node name="Bedrock · reasoning" check={check("Bedrock")} />
            </div>
            <div className="flex w-full items-stretch gap-2">
              <Node name="Bedrock · Titan" check={check("Titan")} />
              <ArrowRight className="h-4 w-4 flex-none self-center text-slate-300" />
              <Node name="CodeBuild" check={check("CodeBuild")} />
              <ArrowRight className="h-4 w-4 flex-none self-center text-slate-300" />
              <Node name="S3" check={check("S3")} />
            </div>
          </div>
          <p className="text-[10.5px] text-slate-400">Each box is coloured by its latest readiness probe; hover for the probe's own detail.</p>
        </div>
      </div>
    </div>
  );
}

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const CARD_TONE = {
  rose: "bg-rose-50 dark:bg-rose-950/60 text-rose-600 dark:text-rose-400",
  amber: "bg-amber-50 dark:bg-amber-950/60 text-amber-600 dark:text-amber-400",
  emerald: "bg-emerald-50 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400",
};

function Card({
  icon: Icon, tone, title, subtitle, children,
}: {
  icon: typeof Database;
  tone: keyof typeof CARD_TONE;
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 shadow-xs space-y-4">
      <div className="flex items-center gap-2.5">
        <div className={`flex h-8 w-8 flex-none items-center justify-center rounded-xl ${CARD_TONE[tone]}`}>
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">{title}</h2>
          <div className="truncate text-[10.5px] text-slate-400 font-mono" title={subtitle}>{subtitle}</div>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 pt-1 border-t border-slate-100 dark:border-slate-800">{children}</div>
    </div>
  );
}

function Num({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={wide ? "col-span-2" : ""}>
      <span className="text-[9.5px] text-slate-400 uppercase font-medium block">{label}</span>
      {value === "…" ? (
        <Shimmer w="70%" h={16} className="mt-0.5" />
      ) : (
        <span className="block truncate text-sm font-extrabold text-slate-900 dark:text-white font-mono" title={value}>{value}</span>
      )}
    </div>
  );
}

const NODE_TONE: Record<string, string> = {
  ready: "border-emerald-200 dark:border-emerald-800 bg-emerald-50/60 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300",
  simulated: "border-amber-200 dark:border-amber-800 bg-amber-50/60 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
  degraded: "border-amber-200 dark:border-amber-800 bg-amber-50/60 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
  not_ready: "border-rose-200 dark:border-rose-800 bg-rose-50/60 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300",
  unknown: "border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-slate-500",
};

function Node({ name, sub, check, status }: { name: string; sub?: string; check?: ComponentCheck; status?: string }) {
  const s = check?.status ?? status ?? "unknown";
  const detail = check?.detail ?? sub ?? "not probed";
  return (
    <div className={`min-w-0 flex-1 rounded-xl border p-2.5 text-center ${NODE_TONE[s] ?? NODE_TONE.unknown}`} title={detail}>
      <div className="truncate text-[11px] font-bold">{name}</div>
      <div className="truncate text-[9.5px] opacity-80">
        {s.replace(/_/g, " ")}{check?.latency_ms != null ? ` · ${Math.round(check.latency_ms)} ms` : ""}
      </div>
    </div>
  );
}


/* ── Charts (real data only) ─────────────────────────────────────────────── */

const CHART_CARD = "rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 shadow-xs";

/** Duration of every CodeBuild evaluation for this project, oldest first. */
function BuildTimeChart({ builds, loading }: { builds: AwsBuilds | null; loading: boolean }) {
  const rows = (builds?.builds ?? [])
    .filter((b) => b.start_time && b.end_time)
    .map((b) => ({
      id: b.id,
      label: `#${b.build_number ?? "?"}`,
      name: b.strategy ?? "",
      secs: (Date.parse(b.end_time!) - Date.parse(b.start_time!)) / 1000,
      ok: b.status === "SUCCEEDED",
      start: Date.parse(b.start_time!),
    }))
    .sort((a, b) => a.start - b.start);
  const max = Math.max(1, ...rows.map((r) => r.secs));
  const avg = rows.length ? rows.reduce((n, r) => n + r.secs, 0) / rows.length : null;
  const fmt = (s: number) => `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
  return (
    <div className={CHART_CARD}>
      <div className="mb-4 flex items-baseline justify-between">
        <div>
          <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">CodeBuild build time</h2>
          <p className="text-[11px] text-slate-400">One bar per evaluation build, from CodeBuild's own start and end times.</p>
        </div>
        <span className="font-mono text-[11px] text-slate-500">{avg !== null ? `avg ${fmt(avg)}` : ""}</span>
      </div>
      {loading && !builds ? (
        <div className="flex h-40 items-end gap-3">{[60, 85, 70, 95, 50].map((h, i) => <Shimmer key={i} w="100%" h={h * 1.4} round={6} />)}</div>
      ) : rows.length === 0 ? (
        <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-slate-200 text-xs text-slate-400 dark:border-slate-800">
          No completed CodeBuild evaluations for this project yet.
        </div>
      ) : (
        <div className="flex h-44 items-end gap-2 border-b border-slate-100 pb-0 dark:border-slate-800">
          {rows.map((r, i) => (
            <div key={r.id} className="group flex h-full min-w-0 flex-1 flex-col items-center justify-end" title={`${r.label} ${r.name}: ${fmt(r.secs)}`}>
              <span className="mb-1 font-mono text-[9.5px] text-slate-500 opacity-0 transition-opacity group-hover:opacity-100">{fmt(r.secs)}</span>
              <motion.div
                initial={{ height: 0 }}
                animate={{ height: `${(r.secs / max) * 100}%` }}
                transition={{ duration: 0.7, delay: 0.05 * i, ease: "easeOut" }}
                className={`w-full max-w-10 rounded-t-md ${r.ok ? "bg-gradient-to-t from-indigo-500 to-blue-400" : "bg-gradient-to-t from-rose-500 to-rose-400"}`}
              />
            </div>
          ))}
        </div>
      )}
      {rows.length > 0 && (
        <div className="mt-1.5 flex gap-2">
          {rows.map((r) => (
            <span key={r.id} className="min-w-0 flex-1 truncate text-center font-mono text-[9.5px] text-slate-400">{r.label}</span>
          ))}
        </div>
      )}
    </div>
  );
}

/** How long each dependency took to answer its latest readiness probe. */
function ProbeLatencyChart({ readiness }: { readiness: ReadinessReport | null }) {
  const rows = (readiness?.checks ?? []).filter((c) => c.latency_ms != null);
  const max = Math.max(1, ...rows.map((c) => c.latency_ms ?? 0));
  return (
    <div className={CHART_CARD}>
      <div className="mb-4">
        <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">Dependency response time</h2>
        <p className="text-[11px] text-slate-400">Latency of each component's latest readiness probe (real calls, not pings).</p>
      </div>
      {!readiness ? (
        <div className="space-y-3">{Array.from({ length: 5 }).map((_, i) => <Shimmer key={i} h={14} />)}</div>
      ) : rows.length === 0 ? (
        <div className="flex h-40 items-center justify-center text-xs text-slate-400">No probe timings reported.</div>
      ) : (
        <div className="space-y-2.5">
          {rows.map((c, i) => (
            <div key={c.name} className="grid grid-cols-[150px_1fr_64px] items-center gap-3 text-[11.5px]">
              <span className="truncate font-medium text-slate-600 dark:text-slate-300" title={c.detail}>{c.name}</span>
              <div className="h-2.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${((c.latency_ms ?? 0) / max) * 100}%` }}
                  transition={{ duration: 0.7, delay: 0.06 * i, ease: "easeOut" }}
                  className={`h-full rounded-full ${c.status === "ready" ? "bg-emerald-500" : c.status === "not_ready" ? "bg-rose-500" : "bg-amber-500"}`}
                />
              </div>
              <span className="text-right font-mono text-slate-500">{Math.round(c.latency_ms ?? 0).toLocaleString()} ms</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
