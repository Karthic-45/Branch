import { useEffect, useRef, useState } from "react";
import { FileText, ShieldCheck, Maximize2 } from "lucide-react";
import { LoadingCaption, Shimmer } from "../common/Loading";
import { loadPdfJs, PdfPage, type PDFDocumentProxy } from "./PdfPreview";

/** A report rendered in place, with the facts the server sent alongside it. */
export function InlinePdf({
  url, title, onExpand,
}: {
  url: string;
  title: string;
  onExpand: () => void;
}) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [meta, setMeta] = useState<{ bytes: number; sha: string | null; at: string | null } | null>(null);
  const [error, setError] = useState("");
  const [width, setWidth] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    let loaded: PDFDocumentProxy | null = null;
    setDoc(null); setMeta(null); setError("");
    (async () => {
      try {
        const r = await fetch(`${url}${url.includes("?") ? "&" : "?"}disposition=inline`);
        if (!r.ok) {
          let detail = `${r.status} ${r.statusText}`;
          try { const b = (await r.json()) as { detail?: unknown }; if (typeof b.detail === "string") detail = b.detail; } catch { /* keep status */ }
          throw new Error(detail);
        }
        const blob = await r.blob();
        const pdfjs = await loadPdfJs();
        loaded = await pdfjs.getDocument({ data: new Uint8Array(await blob.arrayBuffer()) }).promise;
        if (cancelled) { void loaded.destroy(); return; }
        setDoc(loaded);
        setMeta({ bytes: blob.size, sha: r.headers.get("X-Report-SHA256"), at: r.headers.get("X-Report-Generated-At") });
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => { cancelled = true; if (loaded) void loaded.destroy(); };
  }, [url]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  return (
    <div className="space-y-4">
      <div ref={box} className="relative h-[460px] overflow-auto rounded-xl border border-slate-100 bg-slate-100/70 p-3 dark:border-slate-800 dark:bg-slate-900">
        {error ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <FileText className="h-8 w-8 text-slate-300" />
            <p className="max-w-xs text-xs text-rose-600">Could not generate this report: {error}</p>
          </div>
        ) : !doc ? (
          <div className="space-y-3 rounded-lg bg-white p-6 dark:bg-slate-950">
            <LoadingCaption text="Generating the report from the database…" />
            <Shimmer w="40%" h={18} />
            {Array.from({ length: 9 }).map((_, i) => <Shimmer key={i} w={`${92 - (i % 3) * 14}%`} h={9} />)}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3">
            {Array.from({ length: Math.min(doc.numPages, 3) }, (_, i) => (
              <PdfPage key={i} doc={doc} pageNumber={i + 1} zoom="fit" availableWidth={Math.max(200, width - 26)} />
            ))}
            {doc.numPages > 3 && (
              <button onClick={onExpand} className="rounded-lg bg-white px-3 py-1.5 text-xs font-semibold text-indigo-600 shadow-sm dark:bg-slate-950">
                + {doc.numPages - 3} more pages — open full preview
              </button>
            )}
          </div>
        )}
        {doc && (
          <button
            onClick={onExpand}
            aria-label="Open full preview"
            className="absolute right-5 top-5 flex items-center gap-1 rounded-lg bg-slate-900/80 px-2 py-1 text-[11px] font-semibold text-white backdrop-blur hover:bg-slate-900"
          >
            <Maximize2 className="h-3 w-3" /> Expand
          </button>
        )}
      </div>

      <div>
        <h3 className="mb-2 text-xs font-bold text-slate-700 dark:text-slate-200">File information</h3>
        <dl className="grid grid-cols-[96px_1fr] gap-x-3 gap-y-1.5 text-[11.5px]">
          <dt className="text-slate-400">Name</dt><dd className="truncate font-mono text-slate-700 dark:text-slate-300">{title}</dd>
          <dt className="text-slate-400">Pages</dt><dd className="font-mono text-slate-700 dark:text-slate-300">{doc ? doc.numPages : "—"}</dd>
          <dt className="text-slate-400">Size</dt><dd className="font-mono text-slate-700 dark:text-slate-300">{meta ? `${(meta.bytes / 1024).toFixed(1)} KB` : "—"}</dd>
          <dt className="text-slate-400">Generated</dt><dd className="font-mono text-slate-700 dark:text-slate-300">{meta?.at ? new Date(meta.at).toLocaleString() : "—"}</dd>
          <dt className="text-slate-400">SHA-256</dt>
          <dd className="flex min-w-0 items-center gap-1 font-mono text-slate-700 dark:text-slate-300">
            {meta?.sha && <ShieldCheck className="h-3.5 w-3.5 flex-none text-emerald-500" />}
            <span className="truncate" title={meta?.sha ?? undefined}>{meta?.sha ?? "—"}</span>
          </dd>
        </dl>
      </div>
    </div>
  );
}
