import { useEffect, useState } from "react";
import { Layers, GitBranch } from "lucide-react";
import type { BeliefPage, BranchNode, BranchVerdict, ExecutionManifest } from "../../lib/types";
import { fetchBeliefs, fetchBranchVerdict, fetchManifest } from "../../lib/api";
import { VerdictBox } from "./VerdictBox";
import {
  Badge, Drawer, EmptyState, ErrorState, Field, FieldList, Metric, Skeleton, Status,
} from "../ui";

/**
 * Everything recorded about one branch, opened from the tree.
 *
 * Two independent requests, reported independently: a branch explored before
 * execution manifests existed 404s on the manifest while its beliefs still
 * read fine, and collapsing both into one failure would hide that. Absent
 * provenance is a fact about the run, not an error in this panel.
 */
export function BranchInspector({
  branch, open, onClose,
}: { branch: BranchNode | null; open: boolean; onClose: () => void }) {
  const [beliefs, setBeliefs] = useState<BeliefPage | null>(null);
  const [manifest, setManifest] = useState<ExecutionManifest | null>(null);
  const [manifestMissing, setManifestMissing] = useState(false);
  const [verdict, setVerdict] = useState<BranchVerdict | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !branch) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    setBeliefs(null);
    setManifest(null);
    setManifestMissing(false);
    setVerdict(null);

    if (branch.parent_id !== null) {
      fetchBranchVerdict(branch.id)
        .then((v) => { if (!cancelled) setVerdict(v); })
        .catch(() => { /* older backend or no record: the box is simply not shown */ });
    }

    fetchBeliefs(branch.id, { limit: 8 })
      .then((p) => { if (!cancelled) setBeliefs(p); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });

    fetchManifest(branch.id)
      .then((m) => { if (!cancelled) setManifest(m); })
      .catch(() => { if (!cancelled) setManifestMissing(true); });

    return () => { cancelled = true; };
  }, [open, branch]);

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={branch?.hypothesis ?? branch?.id.slice(0, 12) ?? "Branch"}
      subtitle={branch ? `${branch.status} · depth ${branch.depth}` : undefined}
      width={460}
    >
      {!branch ? null : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <Status status={branch.status} />
            {branch.parent_id === null && <Badge tone="neutral">trunk</Badge>}
          </div>

          {verdict && <VerdictBox verdict={verdict} />}

          <div className="grid grid-cols-2 gap-3 border-y border-hairline py-3">
            <Metric
              label="Score"
              value={branch.score === null ? null : `${Math.round(branch.score * 100)}%`}
              tone={branch.status === "merged" ? "success" : "neutral"}
            />
            <Metric label="Depth" value={branch.depth} />
          </div>

          <section>
            <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
              Why this branch exists
            </h3>
            <p className="text-[12.5px] leading-relaxed text-ink-2">
              {branch.hypothesis ?? <span className="italic text-muted">Not recorded</span>}
            </p>
          </section>

          <section>
            <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
              Memory
            </h3>
            {error ? (
              <ErrorState title="Could not read this branch's memory" detail={error} />
            ) : loading || !beliefs ? (
              <Skeleton rows={4} />
            ) : (
              <>
                <div className="mb-2 flex flex-wrap items-center gap-4">
                  <Metric label="Visible" value={beliefs.total_visible} />
                  <Metric label="Own" value={beliefs.own} tone="success" />
                  <Metric label="Inherited" value={beliefs.inherited} />
                </div>
                {beliefs.beliefs.length === 0 ? (
                  <EmptyState
                    title="No memory visible"
                    body="This branch has written nothing and inherits nothing."
                  />
                ) : (
                  <ol className="divide-y divide-hairline rounded-[4px] border border-hairline">
                    {beliefs.beliefs.slice(0, 8).map((b) => (
                      <li key={`${b.origin_branch_id}:${b.key}`} className="px-2.5 py-1.5">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate font-mono text-[11px] text-ink">{b.key}</span>
                          {b.inherited ? (
                            <Badge tone="neutral"><Layers className="h-3 w-3" />·{b.origin_depth}</Badge>
                          ) : (
                            <Badge tone="success"><GitBranch className="h-3 w-3" />own</Badge>
                          )}
                        </div>
                        <p className="mt-0.5 truncate text-[11.5px] text-ink-2">
                          {b.content ?? "(empty)"}
                        </p>
                      </li>
                    ))}
                  </ol>
                )}
                {beliefs.total_visible > 8 && (
                  <p className="mt-1.5 text-[11px] text-muted">
                    Showing 8 of {beliefs.total_visible}. The Beliefs page lists them all.
                  </p>
                )}
              </>
            )}
          </section>

          <section>
            <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
              Reproducibility
            </h3>
            {manifestMissing ? (
              <p className="text-[12px] leading-relaxed text-muted">
                No execution manifest was recorded for this branch. It was explored before
                provenance capture existed — a fact about the run, not a missing value.
              </p>
            ) : !manifest ? (
              <Skeleton rows={3} />
            ) : (
              <FieldList>
                <Field k="base sha" v={manifest.base_sha?.slice(0, 12) ?? null} />
                <Field k="model" v={manifest.reasoning_model_id ?? null} />
                <Field k="prompt" v={manifest.explore_prompt_version ?? null} />
                <Field k="embedder" v={manifest.embedding_model_id ?? null} />
                <Field k="tests" v={manifest.test_selector ?? null} />
                <Field k="retrieved" v={String(manifest.retrieved_memories.length)} />
              </FieldList>
            )}
          </section>

          <FieldList>
            <Field k="branch" v={branch.id} />
            <Field k="parent" v={branch.parent_id ?? "—"} />
          </FieldList>
        </div>
      )}
    </Drawer>
  );
}
