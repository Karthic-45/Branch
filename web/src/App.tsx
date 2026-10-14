import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { AppShell, type Section } from "./components/layout/AppShell";
import { CommandPalette } from "./components/layout/CommandPalette";
import { OverviewPage } from "./components/overview/OverviewPage";
import { DecisionsPage } from "./components/decisions/DecisionsPage";
import { BranchInspector } from "./components/branches/BranchInspector";

/* Split out of the entry chunk: none of these is the first screen anyone
   sees, and Proof Lab and Judge Mode in particular pull in visualisation code
   that most sessions never touch. Overview and Decisions stay eager -- they
   are where every session starts, and a spinner on first paint would cost
   more than the bytes save. */
const SystemSection = lazy(() =>
  import("./components/SystemSection").then((m) => ({ default: m.SystemSection })));
const ProofsLab = lazy(() =>
  import("./components/proofs/ProofsLab").then((m) => ({ default: m.ProofsLab })));
const ReportsPage = lazy(() =>
  import("./components/reports/ReportsPage").then((m) => ({ default: m.ReportsPage })));
const JudgeMode = lazy(() =>
  import("./components/JudgeMode").then((m) => ({ default: m.JudgeMode })));
const BeliefsPage = lazy(() =>
  import("./components/memory/BeliefsPage").then((m) => ({ default: m.BeliefsPage })));
const DeploymentsPage = lazy(() =>
  import("./components/delivery/DeploymentsPage").then((m) => ({ default: m.DeploymentsPage })));
const AuditPage = lazy(() =>
  import("./components/audit/AuditPage").then((m) => ({ default: m.AuditPage })));
const AwsStoragePage = lazy(() =>
  import("./components/aws/AwsStoragePage").then((m) => ({ default: m.AwsStoragePage })));
const BranchesPage = lazy(() =>
  import("./components/branches/BranchesPage").then((m) => ({ default: m.BranchesPage })));
import { NewFromRepoForm } from "./components/NewFromRepoForm";
import { MigrationForm } from "./components/MigrationForm";
import {
  Drawer, Field, FieldList, Status, Button, ToastStack,
} from "./components/ui";
import { useEventStream } from "./hooks/useEventStream";
import { useMerges } from "./hooks/useMerges";
import { useTheme } from "./hooks/useTheme";
import { useReadiness } from "./hooks/useReadiness";
import { useRoute } from "./hooks/useRoute";
import { useToasts } from "./hooks/useToasts";
import { fetchDecisionGraph, fetchProjectDeployments, fetchProjectStats } from "./lib/api";
import type { BranchNode, DecisionNode, ProjectDeployment } from "./lib/types";
import { InitialLoader, ViewLoader } from "./components/common/InitialLoader";

const initialRoot = () => new URLSearchParams(window.location.search).get("root") ?? "";

export type { Section };

export default function App() {
  // Section and the open decision live in the URL hash, so every view is
  // linkable and Back works. ?root= stays in the query string.
  const [route, navigate] = useRoute();
  const section = route.section;
  const selectedDecision = route.decisionId;
  const setSection = useCallback((s: Section) => navigate({ section: s }), [navigate]);

  const [rootId, setRootId] = useState(initialRoot());
  const [connectedRoot, setConnectedRoot] = useState<string | null>(initialRoot() || null);
  const [theme, toggleTheme] = useTheme();

  const { toasts, push: toast, dismiss: dismissToast } = useToasts();
  const [inspecting, setInspecting] = useState<BranchNode | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [statusOpen, setStatusOpen] = useState(false);
  const [startOpen, setStartOpen] = useState(false);

  const { nodes, events, branchStage, connState } = useEventStream(connectedRoot);
  const { merges, refresh: refreshMerges } = useMerges(connectedRoot, nodes);
  const { readiness, checkedAt, attempted: readinessAttempted, error: readinessError, refresh: refreshReadiness } = useReadiness();

  // "Loaded for this root" flags, so a page can tell "still fetching" from
  // "fetched, and there is nothing" -- the two used to look identical.
  const [decisions, setDecisions] = useState<DecisionNode[]>([]);
  const [decisionsFor, setDecisionsFor] = useState<string | null>(null);
  useEffect(() => {
    if (!connectedRoot) { setDecisions([]); setDecisionsFor(null); return; }
    let cancelled = false;
    fetchDecisionGraph(connectedRoot)
      .then((d) => { if (!cancelled) setDecisions(d); })
      .catch(() => { /* Decisions panel renders its own empty state */ })
      .finally(() => { if (!cancelled) setDecisionsFor(connectedRoot); });
    return () => { cancelled = true; };
  }, [connectedRoot, merges.length]);
  const decisionsLoading = Boolean(connectedRoot) && decisionsFor !== connectedRoot;

  const [deployments, setDeployments] = useState<ProjectDeployment[]>([]);
  const [deploymentsFor, setDeploymentsFor] = useState<string | null>(null);
  const [deploymentsVersion, setDeploymentsVersion] = useState(0);
  useEffect(() => {
    if (!connectedRoot) { setDeployments([]); setDeploymentsFor(null); return; }
    let cancelled = false;
    fetchProjectDeployments(connectedRoot)
      .then((d) => { if (!cancelled) setDeployments(d); })
      .catch(() => { /* both consumers render their own empty state */ })
      .finally(() => { if (!cancelled) setDeploymentsFor(connectedRoot); });
    return () => { cancelled = true; };
  }, [connectedRoot, decisions.length, deploymentsVersion]);
  const deploymentsLoading = Boolean(connectedRoot) && deploymentsFor !== connectedRoot;

  // The branch tree arrives on the live stream; until its first snapshot the
  // tree is unknown, not empty.
  const treeLoading = Boolean(connectedRoot) && connState === "connecting";
  const dataLoading = treeLoading || decisionsLoading || deploymentsLoading;

  // The repository this root was ingested from. Read from the backend's own
  // record of the ingest (branches.source_repo) rather than inferred here, so
  // a root created before self-service ingest honestly reads "Not recorded".
  const [repository, setRepository] = useState<string | null>(null);
  useEffect(() => {
    if (!connectedRoot) { setRepository(null); return; }
    let cancelled = false;
    fetchProjectStats(connectedRoot)
      .then((s) => { if (!cancelled) setRepository(s.source_repo); })
      .catch(() => { if (!cancelled) setRepository(null); });
    return () => { cancelled = true; };
  }, [connectedRoot]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Opening a decision is a single navigation: section and id together, so
  // the resulting URL is the thing you can paste to a colleague.
  const openDecision = useCallback(
    (mergeId: string) => navigate({ section: "decisions", decisionId: mergeId }),
    [navigate],
  );

  return (
    <>
      <InitialLoader
        ready={readinessAttempted}
        status={readinessError ? `Backend unreachable: ${readinessError}` : "Probing CockroachDB, Bedrock, Titan, S3 and CodeBuild…"}
      />
      <AppShell
        section={section}
        onSelect={setSection}
        theme={theme}
        onToggleTheme={toggleTheme}
        conn={connState}
        readiness={readiness}
        rootId={connectedRoot}
        repository={repository}
        onOpenPalette={() => setPaletteOpen(true)}
        onOpenStatus={() => setStatusOpen(true)}
        loading={dataLoading}
      >
        <Suspense fallback={<ViewLoader />}>
        {section === "overview" && (
          <OverviewPage
            rootId={connectedRoot}
            repository={repository}
            nodes={nodes}
            events={events}
            branchStage={branchStage}
            merges={merges}
            decisions={decisions}
            readiness={readiness}
            theme={theme}
            loading={treeLoading}
            onRunMigration={() => setStartOpen(true)}
            onOpenDecision={openDecision}
            onViewBranches={() => setSection("branches")}
          />
        )}

        {section === "decisions" && (
          <DecisionsPage
            rootId={connectedRoot}
            merges={merges}
            decisions={decisions}
            selected={selectedDecision}
            loading={decisionsLoading}
            onSelect={(id) => navigate({ decisionId: id })}
            onReverted={() => { refreshMerges(); toast("Merge reverted — trunk restored", "success"); }}
            onToast={toast}
          />
        )}

        {section === "branches" && (
          <BranchesPage
            rootId={connectedRoot}
            nodes={nodes}
            branchStage={branchStage}
            theme={theme}
            loading={treeLoading}
            onSelectBranch={setInspecting}
          />
        )}

        {section === "memory" && <BeliefsPage rootId={connectedRoot} nodes={nodes} />}

        {section === "deployments" && (
          <DeploymentsPage
            rootId={connectedRoot}
            rows={deployments}
            decisions={decisions}
            loading={deploymentsLoading || decisionsLoading}
            onOpenDecision={openDecision}
            onToast={toast}
            onChanged={() => setDeploymentsVersion((v) => v + 1)}
          />
        )}

        {section === "observability" && (
          <SystemSection
            rootId={connectedRoot}
            readiness={readiness}
            events={events}
            branchStage={branchStage}
            theme={theme}
          />
        )}
        {section === "aws" && <AwsStoragePage rootId={connectedRoot} />}
        {section === "proofs" && <ProofsLab onToast={toast} />}
        {section === "reports" && <ReportsPage rootId={connectedRoot} onToast={toast} />}
        {section === "audit" && (
          <AuditPage
            decisions={decisions}
            deployments={deployments}
            loading={decisionsLoading || deploymentsLoading}
            events={events}
            onNavigate={(s) => setSection(s)}
          />
        )}

        {section === "judge" && <div className="p-5 sm:p-6 lg:p-7 w-full"><JudgeMode onNavigate={(s) => setSection(s as Section)} /></div>}
        </Suspense>
      </AppShell>

      <ToastStack toasts={toasts} onDismiss={dismissToast} />

      <BranchInspector
        branch={inspecting}
        open={inspecting !== null}
        onClose={() => setInspecting(null)}
      />

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onNavigate={setSection}
        decisions={decisions}
        branches={nodes}
        merges={merges}
        onRunMigration={() => setStartOpen(true)}
        onOpenDecision={openDecision}
      />

      {/* System status: every row is a real probe result from /api/readiness */}
      <Drawer
        open={statusOpen}
        onClose={() => setStatusOpen(false)}
        title="System status"
        subtitle={readiness ? readiness.overall.replace(/_/g, " ") : "checking"}
      >
        {!readiness ? (
          <p className="text-[13px] text-muted">Running dependency probes…</p>
        ) : (
          <div className="space-y-3">
            <p className="text-[12.5px] leading-relaxed text-ink-2">{readiness.note}</p>
            <div className="divide-y divide-hairline rounded-[6px] border border-hairline">
              {readiness.checks.map((c) => (
                <div key={c.name} className="px-3 py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[13px] font-medium text-ink">{c.name}</span>
                    <Status status={c.status} />
                  </div>
                  <p className="mt-1 text-[12px] leading-relaxed text-ink-2">{c.detail}</p>
                  {c.remediation && (
                    <p className="mt-1 text-[11.5px] leading-relaxed text-muted">{c.remediation}</p>
                  )}
                  <div className="mt-1 font-mono text-[10.5px] text-muted">
                    probe: {c.probe}{c.latency_ms !== null ? ` · ${Math.round(c.latency_ms)}ms` : ""}
                  </div>
                </div>
              ))}
            </div>
            <Button size="sm" onClick={() => void refreshReadiness()}>Re-run checks</Button>
            {checkedAt && (
              <div className="font-mono text-[11px] text-muted">
                last probed {new Date(checkedAt).toLocaleTimeString()}
              </div>
            )}
          </div>
        )}
      </Drawer>

      {/* Start work: ingest a repo, or run a migration on the connected root */}
      <Drawer open={startOpen} onClose={() => setStartOpen(false)} title="Start work" width={460}>
        <div className="space-y-5">
          <section>
            <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ink-2">
              Ingest a repository
            </h3>
            <NewFromRepoForm
              onIngestStarted={(newRootId) => {
                setRootId(newRootId);
                setConnectedRoot(newRootId);
                setStartOpen(false);
                setSection("overview");
                toast("Ingest started — watch the execution trace", "info");
              }}
            />
          </section>
          <section className="border-t border-hairline pt-4">
            <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ink-2">
              Run a migration
            </h3>
            <MigrationForm
              rootId={rootId}
              onRootIdChange={setRootId}
              onStarted={() => {
                setConnectedRoot(rootId.trim());
                setStartOpen(false);
                setSection("overview");
                toast("Migration started", "info");
              }}
            />
          </section>
          <section className="border-t border-hairline pt-4">
            <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ink-2">
              Connected
            </h3>
            <FieldList>
              <Field k="root" v={connectedRoot} />
              <Field k="stream" v={connState} mono={false} />
            </FieldList>
          </section>
        </div>
      </Drawer>
    </>
  );
}
