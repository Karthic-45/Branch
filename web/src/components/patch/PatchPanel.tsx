import { useEffect, useMemo, useState } from "react";
import { Download, Copy, Check, Search } from "lucide-react";
import { Button, EmptyState, ErrorState, Metric, Skeleton } from "../ui";
import { DiffView, parseUnifiedDiff } from "./DiffView";

/**
 * The winning patch, as the reviewer will actually read it.
 *
 * The diff is fetched and parsed rather than shown as a blob of text, so the
 * per-file counts on screen are derived from the patch itself -- if they
 * disagree with what the decision recorded, that disagreement is visible
 * rather than papered over by trusting one source.
 */
export function PatchPanel({
  mergeId, baseSha, onToast,
}: {
  mergeId: string;
  baseSha?: string | null;
  onToast?: (text: string, tone?: "success" | "error" | "neutral") => void;
}) {
  const [patch, setPatch] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setPatch(null);
    setError("");
    fetch(`/api/merges/${encodeURIComponent(mergeId)}/patch`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.text();
      })
      .then((t) => { if (!cancelled) setPatch(t); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [mergeId]);

  const files = useMemo(() => (patch ? parseUnifiedDiff(patch) : []), [patch]);
  const totals = useMemo(
    () => files.reduce(
      (acc, f) => ({ added: acc.added + f.added, removed: acc.removed + f.removed }),
      { added: 0, removed: 0 },
    ),
    [files],
  );

  async function copy() {
    if (!patch) return;
    await navigator.clipboard.writeText(patch);
    setCopied(true);
    onToast?.("Patch copied to clipboard", "success");
    window.setTimeout(() => setCopied(false), 1600);
  }

  if (error) {
    return (
      <div className="p-3">
        <ErrorState
          title="Could not load the patch"
          detail={error}
          remediation="The diff is served from S3 via the control plane. A 404 here means no patch artifact was recorded for this decision."
        />
      </div>
    );
  }
  if (!patch) return <div className="p-4"><Skeleton rows={10} /></div>;

  // A patch that parses to nothing is not an error: the stand-in explorer
  // emits a placeholder rather than a diff, and saying so beats rendering an
  // empty file list as though the strategy changed nothing.
  const parsedNothing = files.length === 0;

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-4 border-b border-hairline px-4 py-2.5">
        <Metric label="Files" value={files.length} />
        <Metric label="Added" value={totals.added} tone={totals.added ? "success" : "neutral"} />
        <Metric label="Removed" value={totals.removed} tone={totals.removed ? "error" : "neutral"} />
        <Metric label="Base" value={baseSha ? baseSha.slice(0, 12) : null} />
        <div className="ml-auto flex items-center gap-2">
          {files.length > 1 && (
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5
                -translate-y-1/2 text-muted" />
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter files"
                aria-label="Filter files in this patch"
                className="w-40 rounded-[4px] border border-hairline-strong bg-surface py-1 pl-7 pr-2
                  text-[12px] text-ink placeholder:text-muted focus:border-accent focus:outline-none"
              />
            </div>
          )}
          <Button
            size="sm"
            icon={copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
            onClick={() => void copy()}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
          <a href={`/api/merges/${encodeURIComponent(mergeId)}/patch`} download={`decision-${mergeId.slice(0, 8)}.patch`}>
            <Button size="sm" icon={<Download className="h-3.5 w-3.5" />}>Download</Button>
          </a>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {parsedNothing ? (
          <EmptyState
            title="No file changes in this artifact"
            body={
              patch.trim().length
                ? "The stored artifact is not a unified diff. Its raw contents are below — this is what a stand-in explorer produces when no model was available to write a real patch."
                : "The stored patch artifact is empty."
            }
          />
        ) : (
          <DiffView files={files} filter={filter} />
        )}
        {parsedNothing && patch.trim().length > 0 && (
          <pre className="mx-4 mb-4 overflow-x-auto rounded-[4px] border border-hairline
            bg-surface-2 p-3 font-mono text-[11.5px] leading-relaxed text-ink-2">
            {patch}
          </pre>
        )}
      </div>
    </div>
  );
}
