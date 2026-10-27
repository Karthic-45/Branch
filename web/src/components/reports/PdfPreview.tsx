import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, Download, ExternalLink, ShieldCheck, ZoomIn, ZoomOut, Maximize2 } from "lucide-react";
import { Button, ErrorState, Skeleton } from "../ui";

/* pdf.js (Apache-2.0, v4.10.38) ships with the app under public/vendor/pdfjs
   and is loaded on first use, so the dashboard needs no extra npm install and
   the 1.7 MB library is only fetched by someone who opens a preview. The
   types below cover just the calls this file makes. */
interface PdfRenderTask { promise: Promise<void>; cancel: () => void }
interface PdfViewport { width: number; height: number }
interface PdfPageProxy {
  getViewport(o: { scale: number }): PdfViewport;
  render(o: {
    canvasContext: CanvasRenderingContext2D;
    viewport: PdfViewport;
    transform?: number[];
  }): PdfRenderTask;
}
export interface PDFDocumentProxy {
  numPages: number;
  getPage(n: number): Promise<PdfPageProxy>;
  destroy(): Promise<void>;
}
interface PdfJs {
  GlobalWorkerOptions: { workerSrc: string };
  getDocument(src: { data: Uint8Array }): { promise: Promise<PDFDocumentProxy> };
}

const PDFJS_BASE = `${import.meta.env.BASE_URL}vendor/pdfjs/`;
let pdfjsPromise: Promise<PdfJs> | null = null;

export function loadPdfJs(): Promise<PdfJs> {
  pdfjsPromise ??= import(/* @vite-ignore */ `${PDFJS_BASE}pdf.min.mjs`).then((mod: PdfJs) => {
    mod.GlobalWorkerOptions.workerSrc = `${PDFJS_BASE}pdf.worker.min.mjs`;
    return mod;
  });
  pdfjsPromise.catch(() => { pdfjsPromise = null; });
  return pdfjsPromise;
}

/**
 * Read a report before committing to a file on disk.
 *
 * The pages are drawn here with pdf.js rather than handed to the browser's
 * built-in PDF viewer in an <iframe>. That viewer is optional: it is missing
 * or disabled in some browsers, policies and mobile views, where the frame
 * comes up blank or silently turns into a download -- a Preview button that
 * works only on some machines. Rendering ourselves works wherever canvas does.
 *
 * Fetched (not linked) so the response's X-Report-SHA256 is readable and the
 * preview can state the checksum of the exact bytes it shows, and so a failed
 * generation surfaces the server's own reason instead of a bare status code.
 */
export function PdfPreview({
  open, onClose, title, url, filename,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  /** Endpoint serving the PDF. `?disposition=inline` is appended here. */
  url: string;
  filename: string;
}) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [meta, setMeta] = useState<{ sha: string | null; at: string | null; bytes: number } | null>(null);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState<number | "fit">("fit");
  const [width, setWidth] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);

  // Fetch + parse.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    let made: string | null = null;
    let loaded: PDFDocumentProxy | null = null;
    setError(""); setBlobUrl(null); setDoc(null); setMeta(null); setZoom("fit");

    const sep = url.includes("?") ? "&" : "?";
    (async () => {
      try {
        const r = await fetch(`${url}${sep}disposition=inline`);
        if (!r.ok) {
          let detail = `${r.status} ${r.statusText}`;
          try {
            const body = (await r.json()) as { detail?: unknown };
            if (typeof body.detail === "string") detail = body.detail;
          } catch { /* not JSON -- keep the status line */ }
          throw new Error(detail);
        }
        const blob = await r.blob();
        const bytes = new Uint8Array(await blob.arrayBuffer());
        const pdfjs = await loadPdfJs();
        loaded = await pdfjs.getDocument({ data: bytes }).promise;
        if (cancelled) { void loaded.destroy(); return; }
        made = URL.createObjectURL(blob);
        setBlobUrl(made);
        setDoc(loaded);
        setMeta({
          sha: r.headers.get("X-Report-SHA256"),
          at: r.headers.get("X-Report-Generated-At"),
          bytes: blob.size,
        });
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();

    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
      if (loaded) void loaded.destroy();
    };
  }, [open, url]);

  // Track the available width so "fit" follows the dialog size.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, [doc]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const step = (dir: 1 | -1) =>
    setZoom((z) => {
      const current = z === "fit" ? 1 : z;
      return Math.min(3, Math.max(0.5, Math.round((current + dir * 0.25) * 100) / 100));
    });

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-3 sm:p-6"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${title} preview`}
        onClick={(e) => e.stopPropagation()}
        className="flex h-full max-h-[940px] w-full max-w-[920px] flex-col overflow-hidden
          rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-[#11141c] shadow-2xl"
      >
        <header className="flex flex-none flex-wrap items-center justify-between gap-2 border-b
          border-slate-200 dark:border-slate-800 px-4 py-2.5">
          <div className="min-w-0">
            <h2 className="truncate text-[13px] font-semibold text-slate-900 dark:text-white">{title}</h2>
            <div className="mt-0.5 flex flex-wrap items-center gap-2 font-mono text-[10.5px] text-slate-500">
              {meta && doc ? (
                <>
                  <span>{doc.numPages} page{doc.numPages === 1 ? "" : "s"}</span>
                  <span>· {(meta.bytes / 1024).toFixed(1)} KB</span>
                  {meta.at && <span>· generated {new Date(meta.at).toLocaleString()}</span>}
                </>
              ) : error ? <span>failed</span> : <span>generating…</span>}
            </div>
          </div>
          <div className="flex flex-none items-center gap-1.5">
            {doc && (
              <div className="mr-1 flex items-center gap-0.5 rounded-lg border border-slate-200 dark:border-slate-700 p-0.5">
                <IconBtn label="Zoom out" onClick={() => step(-1)}><ZoomOut className="h-3.5 w-3.5" /></IconBtn>
                <span className="w-11 text-center font-mono text-[10.5px] text-slate-500">
                  {zoom === "fit" ? "Fit" : `${Math.round(zoom * 100)}%`}
                </span>
                <IconBtn label="Zoom in" onClick={() => step(1)}><ZoomIn className="h-3.5 w-3.5" /></IconBtn>
                <IconBtn label="Fit to width" onClick={() => setZoom("fit")}><Maximize2 className="h-3.5 w-3.5" /></IconBtn>
              </div>
            )}
            {blobUrl && (
              <>
                <a href={blobUrl} download={filename}>
                  <Button size="sm" icon={<Download className="h-3.5 w-3.5" />}>Download</Button>
                </a>
                <a href={blobUrl} target="_blank" rel="noreferrer">
                  <Button size="sm" icon={<ExternalLink className="h-3.5 w-3.5" />}>New tab</Button>
                </a>
              </>
            )}
            <Button size="sm" variant="ghost" aria-label="Close preview" onClick={onClose}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        </header>

        <div ref={scroller} className="min-h-0 flex-1 overflow-auto bg-slate-200/70 dark:bg-slate-900">
          {error ? (
            <div className="p-4">
              <ErrorState
                title="Could not generate this report"
                detail={error}
                remediation={
                  /not found/i.test(error)
                    ? "This project id is not in the database. Open the dashboard with ?root= set to an existing project."
                    : "Reports are generated on request from the database; check the backend log for the cause."
                }
              />
            </div>
          ) : !doc ? (
            <div className="p-6">
              <p className="mb-3 text-[12.5px] text-slate-500">
                Generating from the database, S3 and CodeBuild — this can take a few seconds…
              </p>
              <Skeleton rows={10} />
            </div>
          ) : (
            <div className="flex flex-col items-center gap-4 p-4">
              {Array.from({ length: doc.numPages }, (_, i) => (
                <PdfPage
                  key={i}
                  doc={doc}
                  pageNumber={i + 1}
                  zoom={zoom}
                  availableWidth={Math.max(200, width - 32)}
                />
              ))}
            </div>
          )}
        </div>

        {meta?.sha && (
          <footer className="flex flex-none items-center gap-1.5 border-t border-slate-200 dark:border-slate-800
            px-4 py-2 text-[11px] text-slate-500">
            <ShieldCheck className="h-3.5 w-3.5 flex-none text-emerald-600" />
            <span className="flex-none">SHA-256</span>
            <code className="truncate font-mono text-[10.5px] text-slate-700 dark:text-slate-300">{meta.sha}</code>
          </footer>
        )}
      </div>
    </div>,
    document.body,
  );
}

function IconBtn({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="flex h-6 w-6 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-slate-800 dark:hover:text-slate-200"
    >
      {children}
    </button>
  );
}

/** One page, drawn to a canvas at device resolution, only once it scrolls near view. */
export function PdfPage({
  doc, pageNumber, zoom, availableWidth,
}: {
  doc: PDFDocumentProxy;
  pageNumber: number;
  zoom: number | "fit";
  availableWidth: number;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const holder = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [visible, setVisible] = useState(pageNumber <= 2);
  // pdf.js refuses two renders into one canvas; a zoom change cancels the old one.
  const task = useRef<{ cancel: () => void } | null>(null);

  useEffect(() => {
    const el = holder.current;
    if (!el || visible) return;
    const io = new IntersectionObserver(
      (entries) => { if (entries.some((e) => e.isIntersecting)) setVisible(true); },
      { rootMargin: "600px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [visible]);

  const draw = useCallback(async (signal: { cancelled: boolean }) => {
    const page = await doc.getPage(pageNumber);
    const base = page.getViewport({ scale: 1 });
    const scale = zoom === "fit" ? availableWidth / base.width : zoom * (96 / 72);
    const viewport = page.getViewport({ scale });
    if (signal.cancelled) return;
    setSize({ w: viewport.width, h: viewport.height });
    if (!visible || !canvas.current) return;
    const dpr = window.devicePixelRatio || 1;
    const c = canvas.current;
    c.width = Math.floor(viewport.width * dpr);
    c.height = Math.floor(viewport.height * dpr);
    const ctx = c.getContext("2d");
    if (!ctx) return;
    task.current?.cancel();
    const render = page.render({
      canvasContext: ctx,
      viewport,
      transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
    });
    task.current = render;
    try { await render.promise; } catch { /* cancelled by a newer render */ }
  }, [doc, pageNumber, zoom, availableWidth, visible]);

  useEffect(() => {
    const signal = { cancelled: false };
    void draw(signal);
    return () => { signal.cancelled = true; task.current?.cancel(); };
  }, [draw]);

  return (
    <div
      ref={holder}
      className="flex-none bg-white shadow-md ring-1 ring-black/5"
      style={size ? { width: size.w, height: size.h } : { width: availableWidth, height: availableWidth * 1.414 }}
    >
      <canvas
        ref={canvas}
        aria-label={`Page ${pageNumber}`}
        style={size ? { width: size.w, height: size.h, display: "block" } : undefined}
      />
    </div>
  );
}
