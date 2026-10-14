import { useEffect, useMemo, useState } from "react";
import { Folder, FileText, FileJson, Download, ChevronRight } from "lucide-react";
import type { ArchiveEntry } from "../../lib/types";
import { fetchArchiveListing } from "../../lib/api";
import { Button, ErrorState, Skeleton } from "../ui";

interface TreeNode {
  name: string;
  bytes: number;
  children: Map<string, TreeNode>;
  isFile: boolean;
}

function buildTree(entries: ArchiveEntry[]): TreeNode {
  const root: TreeNode = { name: "", bytes: 0, children: new Map(), isFile: false };
  for (const entry of entries) {
    const parts = entry.path.split("/");
    let node = root;
    root.bytes += entry.bytes;
    parts.forEach((part, i) => {
      const leaf = i === parts.length - 1;
      let child = node.children.get(part);
      if (!child) {
        child = { name: part, bytes: 0, children: new Map(), isFile: leaf };
        node.children.set(part, child);
      }
      // Every ancestor accumulates, not just the immediate parent: crediting
      // only the leaf's parent left "decisions/" reporting 0 B while the
      // folder inside it reported 12 KB.
      child.bytes += entry.bytes;
      node = child;
    });
  }
  return root;
}

function kb(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}

/**
 * The archive's contents, read from the archive itself.
 *
 * The listing is the ZIP's own directory rather than a description of the
 * layout maintained alongside it -- a manifest that can disagree with the
 * file it documents is worse than none, and this one cannot.
 */
export function ArchiveTree({ rootId }: { rootId: string }) {
  const [entries, setEntries] = useState<ArchiveEntry[] | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open || entries) return;
    let cancelled = false;
    setError("");
    fetchArchiveListing(rootId)
      .then((e) => { if (!cancelled) setEntries(e); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [open, rootId, entries]);

  const tree = useMemo(() => (entries ? buildTree(entries) : null), [entries]);

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex items-center gap-1 text-[12px] text-ink-2 hover:text-accent"
      >
        <ChevronRight className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-90" : ""}`} />
        {open ? "Hide contents" : "Show contents"}
      </button>

      {open && (
        <div className="mt-2 rounded-[4px] border border-hairline bg-surface-2 p-2">
          {error ? (
            <ErrorState title="Could not read the archive" detail={error} />
          ) : !tree ? (
            <>
              <p className="mb-2 text-[11.5px] text-muted">
                Building the archive to read its table of contents…
              </p>
              <Skeleton rows={6} />
            </>
          ) : (
            <>
              <ul className="font-mono text-[11.5px]">
                {[...tree.children.values()].map((n) => (
                  <Node key={n.name} node={n} depth={0} />
                ))}
              </ul>
              <div className="mt-2 flex items-center justify-between border-t border-hairline pt-2">
                <span className="font-mono text-[11px] text-muted">
                  {entries!.length} files · {kb(tree.bytes)}
                </span>
                <a href={`/api/reports/${encodeURIComponent(rootId)}/archive.zip`} download>
                  <Button size="sm" icon={<Download className="h-3.5 w-3.5" />}>
                    Download archive
                  </Button>
                </a>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Node({ node, depth }: { node: TreeNode; depth: number }) {
  const Icon = node.isFile
    ? node.name.endsWith(".json") ? FileJson : FileText
    : Folder;
  return (
    <li>
      <div
        className="flex items-center gap-1.5 py-0.5"
        style={{ paddingLeft: `${depth * 14}px` }}
      >
        <Icon className={`h-3.5 w-3.5 flex-none ${node.isFile ? "text-muted" : "text-accent"}`} />
        <span className={node.isFile ? "text-ink-2" : "text-ink"}>{node.name}</span>
        <span className="ml-auto flex-none text-[10.5px] text-muted tabular">{kb(node.bytes)}</span>
      </div>
      {node.children.size > 0 && (
        <ul>
          {[...node.children.values()].map((c) => (
            <Node key={c.name} node={c} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}
