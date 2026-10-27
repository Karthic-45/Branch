import { useState } from "react";
import { motion } from "framer-motion";
import { FileText, Download, Eye, Archive, FileCode, Loader2, CheckCircle2, XCircle } from "lucide-react";
import { engineeringReportUrl, executiveReportUrl, projectArchiveUrl } from "../../lib/api";
import { PdfPreview } from "./PdfPreview";
import { ArchiveTree } from "./ArchiveTree";
import { InlinePdf } from "./InlinePdf";

/* Reports are rendered by the backend on request from the current database
   state. This page only ever states what a real request returned: the bytes
   received, the server's SHA-256 and generation time, or the error. */

interface Props {
  rootId: string | null;
  onToast?: (text: string, tone?: "success" | "error" | "neutral") => void;
}

interface DownloadRecord {
  name: string;
  ok: boolean;
  bytes: number | null;
  sha: string | null;
  generatedAt: string | null;
  error: string | null;
  at: Date;
}

export function ReportsPage({ rootId, onToast }: Props) {
  const [preview, setPreview] = useState<{ title: string; url: string; filename: string } | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [last, setLast] = useState<DownloadRecord | null>(null);
  const [inlineId, setInlineId] = useState("executive");

  if (!rootId) {
    return (
      <div className="p-5 sm:p-6 lg:p-7 w-full">
        <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-12 text-center shadow-xs">
          <FileText className="h-10 w-10 text-slate-300 dark:text-slate-600 mx-auto mb-3" />
          <h2 className="text-base font-bold text-slate-800 dark:text-slate-200">No project selected</h2>
          <p className="text-xs text-slate-400 mt-1 max-w-md mx-auto">
            Open a project (?root=…) or ingest a repository to generate its reports.
          </p>
        </div>
      </div>
    );
  }

  const reports = [
    {
      id: "executive",
      title: "Executive Report",
      filename: `executive_report_${rootId.slice(0, 8)}.pdf`,
      desc: "Decisions, winning strategies and their measured scores for this project.",
      url: executiveReportUrl(rootId),
      icon: FileText,
      iconColor: "text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-950/60",
      pdf: true,
    },
    {
      id: "engineering",
      title: "Engineering Report",
      filename: `engineering_report_${rootId.slice(0, 8)}.pdf`,
      desc: "Per-branch test results, patch statistics and reproducibility manifests.",
      url: engineeringReportUrl(rootId),
      icon: FileCode,
      iconColor: "text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/60",
      pdf: true,
    },
    {
      id: "archive",
      title: "Full Project Archive (ZIP)",
      filename: `branchable_memory_${rootId.slice(0, 8)}.zip`,
      desc: "Machine-readable export of this project's records. Its exact contents are listed below.",
      url: projectArchiveUrl(rootId),
      icon: Archive,
      iconColor: "text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-950/60",
      pdf: false,
    },
  ];

  const download = async (name: string, url: string) => {
    setDownloading(name);
    try {
      const r = await fetch(url);
      if (!r.ok) {
        const body = await r.json().catch(() => ({}) as { detail?: string });
        throw new Error(body.detail || `HTTP ${r.status}`);
      }
      const blob = await r.blob();
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(href);
      const rec: DownloadRecord = {
        name, ok: true, bytes: blob.size,
        sha: r.headers.get("X-Report-SHA256"),
        generatedAt: r.headers.get("X-Report-Generated-At"),
        error: null, at: new Date(),
      };
      setLast(rec);
      onToast?.(`${name} — ${kb(blob.size)} received`, "success");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setLast({ name, ok: false, bytes: null, sha: null, generatedAt: null, error: msg, at: new Date() });
      onToast?.(`${name} could not be generated: ${msg}`, "error");
    } finally {
      setDownloading(null);
    }
  };

  return (
    <div className="p-5 sm:p-6 lg:p-7 w-full space-y-6">
      <div>
        <h1 className="text-xl sm:text-2xl font-extrabold text-slate-900 dark:text-white">Reports &amp; Exports</h1>
        <p className="text-xs text-slate-500 mt-1">
          Generated on request from the current database state for project <span className="font-mono">{rootId.slice(0, 8)}</span>.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
        <div className="space-y-3">
          {reports.map((r) => {
            const Icon = r.icon;
            const busy = downloading === r.filename;
            return (
              <motion.div
                key={r.id}
                whileHover={{ y: -2 }}
                className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-4 shadow-xs"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <div className={`flex h-10 w-10 flex-none items-center justify-center rounded-xl ${r.iconColor} shadow-2xs`}>
                      <Icon className="h-5 w-5" />
                    </div>
                    <div>
                      <h3 className="text-sm font-bold text-slate-900 dark:text-white">{r.title}</h3>
                      <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5 max-w-sm">{r.desc}</p>
                    </div>
                  </div>
                  <div className="flex flex-none items-center gap-2">
                    {r.pdf && (
                      <button
                        onClick={() => setPreview({ title: r.title, url: r.url, filename: r.filename })}
                        className="flex items-center gap-1 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1 text-xs font-semibold text-slate-700 dark:text-slate-300 hover:bg-slate-50"
                      >
                        <Eye className="h-3.5 w-3.5" /> <span>Preview</span>
                      </button>
                    )}
                    <button
                      onClick={() => void download(r.filename, r.url)}
                      disabled={busy}
                      className="flex items-center gap-1 rounded-lg bg-indigo-600 hover:bg-indigo-700 px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-60"
                    >
                      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                      <span>{busy ? "Generating…" : "Download"}</span>
                    </button>
                  </div>
                </div>
              </motion.div>
            );
          })}
          <ArchiveTree rootId={rootId} />
        </div>

        <div className="space-y-6">
        <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-5 shadow-xs space-y-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">PDF Preview</h2>
            <div className="flex rounded-lg border border-slate-200 p-0.5 dark:border-slate-700">
              {reports.filter((r) => r.pdf).map((r) => (
                <button
                  key={r.id}
                  onClick={() => setInlineId(r.id)}
                  className={`rounded-md px-2.5 py-1 text-[11px] font-semibold transition-colors ${inlineId === r.id ? "bg-indigo-600 text-white" : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`}
                >
                  {r.title.replace(" Report", "")}
                </button>
              ))}
            </div>
          </div>
          {(() => {
            const r = reports.find((x) => x.id === inlineId) ?? reports[0];
            return (
              <InlinePdf
                key={r.id}
                url={r.url}
                title={r.filename}
                onExpand={() => setPreview({ title: r.title, url: r.url, filename: r.filename })}
              />
            );
          })()}
        </div>
        <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-6 shadow-xs space-y-4">
          <h2 className="text-sm font-bold text-slate-800 dark:text-slate-200">Last download</h2>
          {!last ? (
            <p className="text-xs text-slate-400">
              Nothing downloaded in this session yet. Use Preview to read a PDF in place, or Download to save it.
            </p>
          ) : (
            <div className="space-y-3 text-xs">
              <div className={`flex items-center gap-2 font-semibold ${last.ok ? "text-emerald-600" : "text-rose-600"}`}>
                {last.ok ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
                {last.ok ? "Received from the server" : "Generation failed"}
              </div>
              <Meta k="File" v={last.name} />
              {last.ok ? (
                <>
                  <Meta k="Size" v={last.bytes !== null ? kb(last.bytes) : "—"} />
                  <Meta k="SHA-256" v={last.sha ?? "not provided by the server"} />
                  <Meta k="Generated" v={last.generatedAt ? new Date(last.generatedAt).toLocaleString() : "not provided by the server"} />
                </>
              ) : (
                <Meta k="Error" v={last.error ?? "—"} />
              )}
              <Meta k="Requested" v={last.at.toLocaleString()} />
            </div>
          )}
        </div>
        </div>
      </div>

      {preview && (
        <PdfPreview
          open
          onClose={() => setPreview(null)}
          title={preview.title}
          url={preview.url}
          filename={preview.filename}
        />
      )}
    </div>
  );
}

function kb(bytes: number): string {
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function Meta({ k, v }: { k: string; v: string }) {
  return (
    <div className="grid grid-cols-[90px_1fr] gap-2">
      <span className="text-[10.5px] uppercase text-slate-400">{k}</span>
      <span className="break-all font-mono text-slate-700 dark:text-slate-300">{v}</span>
    </div>
  );
}
