import { useMemo } from "react";

export interface DiffFile {
  path: string;
  oldPath: string | null;
  added: number;
  removed: number;
  hunks: { header: string; lines: DiffLine[] }[];
  binary: boolean;
}

interface DiffLine {
  kind: "add" | "del" | "ctx" | "meta";
  text: string;
  oldNo: number | null;
  newNo: number | null;
}

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/**
 * Parse a unified diff.
 *
 * Deliberately not a syntax highlighter. A patch spans whatever languages the
 * repository contains, and guessing a grammar from a file extension in order
 * to colour tokens would be decoration bought with a risk of colouring
 * something wrong. What matters in review is which lines moved, so the only
 * colour here is add/remove, and it is reinforced by the +/- gutter rather
 * than carried by hue alone.
 */
export function parseUnifiedDiff(patch: string): DiffFile[] {
  const files: DiffFile[] = [];
  let file: DiffFile | null = null;
  let oldNo = 0;
  let newNo = 0;

  for (const raw of patch.split("\n")) {
    if (raw.startsWith("diff --git ")) {
      const m = raw.match(/ a\/(.+?) b\/(.+)$/);
      file = {
        path: m?.[2] ?? raw.slice(11),
        oldPath: m?.[1] ?? null,
        added: 0,
        removed: 0,
        hunks: [],
        binary: false,
      };
      files.push(file);
      continue;
    }
    // A patch that starts straight at ---/+++ with no "diff --git" header is
    // still a valid patch; git format-patch produces one, and so does `diff -u`.
    if (!file && (raw.startsWith("--- ") || raw.startsWith("+++ "))) {
      file = { path: raw.slice(4).replace(/^[ab]\//, ""), oldPath: null, added: 0, removed: 0, hunks: [], binary: false };
      files.push(file);
    }
    if (!file) continue;

    if (raw.startsWith("Binary files") || raw.startsWith("GIT binary patch")) {
      file.binary = true;
      continue;
    }
    const hunk = raw.match(HUNK);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      file.hunks.push({ header: raw, lines: [] });
      continue;
    }
    const current = file.hunks[file.hunks.length - 1];
    if (!current) continue;

    if (raw.startsWith("+")) {
      file.added++;
      current.lines.push({ kind: "add", text: raw.slice(1), oldNo: null, newNo: newNo++ });
    } else if (raw.startsWith("-")) {
      file.removed++;
      current.lines.push({ kind: "del", text: raw.slice(1), oldNo: oldNo++, newNo: null });
    } else if (raw.startsWith("\\")) {
      current.lines.push({ kind: "meta", text: raw, oldNo: null, newNo: null });
    } else {
      current.lines.push({ kind: "ctx", text: raw.slice(1), oldNo: oldNo++, newNo: newNo++ });
    }
  }
  // Drop entries that never produced a hunk. A bare "--- something" line is
  // enough to look like a file header, and the stand-in explorer's placeholder
  // artifact begins with exactly that -- without this, a 110-byte non-diff
  // rendered as "1 file changed, +0 -0" with the placeholder text as its
  // filename. A file with no hunks is not a changed file.
  return files.filter((f) => f.hunks.length > 0 || f.binary);
}

export function DiffView({ files, filter }: { files: DiffFile[]; filter: string }) {
  const needle = filter.trim().toLowerCase();
  const shown = useMemo(
    () => (needle ? files.filter((f) => f.path.toLowerCase().includes(needle)) : files),
    [files, needle],
  );

  if (!shown.length) {
    return (
      <p className="px-4 py-6 text-center text-[12.5px] text-muted">
        {needle ? "No file in this patch matches that filter." : "This patch changes no files."}
      </p>
    );
  }

  return (
    <div className="divide-y divide-hairline">
      {shown.map((f) => (
        <section key={f.path}>
          <header className="sticky top-0 z-10 flex items-center justify-between gap-3
            border-b border-hairline bg-surface-2 px-3 py-1.5">
            <span className="truncate font-mono text-[11.5px] text-ink">{f.path}</span>
            <span className="flex-none font-mono text-[11px] tabular">
              <span className="text-success">+{f.added}</span>{" "}
              <span className="text-error">−{f.removed}</span>
            </span>
          </header>
          {f.binary ? (
            <p className="px-3 py-2 text-[12px] text-muted">Binary file — not shown.</p>
          ) : (
            f.hunks.map((h, i) => (
              <div key={i}>
                <div className="bg-accent-soft px-3 py-1 font-mono text-[11px] text-info">
                  {h.header}
                </div>
                <table className="w-full border-collapse">
                  <tbody>
                    {h.lines.map((l, j) => (
                      <tr
                        key={j}
                        className={
                          l.kind === "add" ? "bg-success-soft"
                          : l.kind === "del" ? "bg-error-soft"
                          : ""
                        }
                      >
                        <td className="w-10 select-none border-r border-hairline px-1.5 text-right
                          font-mono text-[10.5px] text-muted tabular">
                          {l.oldNo ?? ""}
                        </td>
                        <td className="w-10 select-none border-r border-hairline px-1.5 text-right
                          font-mono text-[10.5px] text-muted tabular">
                          {l.newNo ?? ""}
                        </td>
                        {/* The marker, not just the row tint: add/remove has to
                            survive greyscale printing and colour blindness. */}
                        <td className={`w-4 select-none text-center font-mono text-[11px] ${
                          l.kind === "add" ? "text-success" : l.kind === "del" ? "text-error" : "text-muted"
                        }`}>
                          {l.kind === "add" ? "+" : l.kind === "del" ? "−" : ""}
                        </td>
                        <td className="whitespace-pre-wrap break-all px-2 font-mono text-[11.5px]
                          leading-[1.55] text-ink-2">
                          {l.text || " "}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))
          )}
        </section>
      ))}
    </div>
  );
}
