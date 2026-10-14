# Branchable Memory — UI structure

Handoff document for anyone (human or model) proposing changes to this
frontend. It describes what exists today, the rules the UI is built on, and
the places that are genuinely unfinished.

**Read the Non-negotiables section before proposing anything.** Several
obvious-looking "improvements" are things this project has deliberately
refused, and a change that breaks one of them is a regression no matter how
good it looks.

---

## 1. What this app is

A **decision control plane** for an agent memory system where beliefs live on
branches that fork, compete, merge and revert. The user is an engineer or a
technical evaluator asking *"what did the agent decide, and why should I
believe it?"*

It is **not** a marketing site, not a consumer dashboard, and not a card grid.
Density and legibility beat visual impact.

Stack: **React 19 + TypeScript + Vite 7 + Tailwind v4 + framer-motion + D3
(hierarchy/shape only) + lucide-react**. No component library, no CSS-in-JS,
no router.

---

## 2. Non-negotiables

These are load-bearing. Breaking one is a bug even if nothing crashes.

1. **Never invent a value.** If the database has no record of something, the
   UI says *"Not recorded"* — never `0`, never `—` where a number is implied,
   never an estimate. `Metric` does this automatically for `null`.
2. **A pending fetch is not an absent value.** Loading renders `Skeleton`.
   Rendering "Not recorded" while a request is in flight is a lie about what
   the database contains.
3. **Colour means status, never decoration.** No gradients, no glow shadows,
   no brand-coloured chrome. `success/warning/error/info/accent` carry
   meaning. This was rebuilt once already to remove a gradient-and-glass
   theme; do not reintroduce it.
4. **Stand-ins are labelled, always.** When `BMEM_FAKE_*` is set the topbar
   and sidebar say "Stand-ins active" and readiness reports `SIMULATED` as a
   state distinct from `READY`. Never present stand-in output as real.
5. **The system never pushes code, opens a PR, or deploys.** There is no
   credential for it. Do not add buttons implying otherwise. "Record a
   deployment" logs what a human already did.
6. **Ephemeral is labelled ephemeral.** The Audit page shows a durable record
   (from CockroachDB) and a session feed (in-process, lost on restart) as two
   separate panels, explicitly captioned. Never merge them.
7. **Components reference tokens by role, never by hue.** `text-accent`, not
   `text-blue-600`. This is what makes dark mode a token remap rather than a
   second component set.

---

## 3. File tree

```
web/src/
├── main.tsx                     entry
├── App.tsx                      section state, shared data fetching, drawers
├── index.css                    ALL design tokens (light + dark) — single source
├── lib/
│   ├── api.ts                   ~35 typed fetch wrappers, one per endpoint
│   ├── types.ts                 mirrors the backend Pydantic models by hand
│   ├── stages.ts                Stage union, STAGE_LABEL, STAGE_CATEGORY, pct()
│   └── flow.ts                  helpers for the (currently orphaned) SystemFlow
├── hooks/
│   ├── useEventStream.ts        SSE → { nodes, events, branchStage, connState }
│   ├── useMerges.ts             merge list, refetched on a key change
│   ├── useReadiness.ts          60s poll, gated on document visibility
│   ├── useTheme.ts              [theme, toggle], persists, sets data-theme
│   └── useConfig.ts             which backends are stand-ins
└── components/
    ├── ui/index.tsx             THE design system — 20 components + statusTone(), 541 lines
    ├── layout/
    │   ├── AppShell.tsx         sidebar + topbar + main; owns NAV_GROUPS
    │   └── CommandPalette.tsx   ⌘K over real decisions/branches/actions
    ├── overview/
    │   ├── OverviewPage.tsx     split-pane home
    │   └── ExecutionTrace.tsx   9-stage pipeline, derived from events only
    ├── decisions/DecisionsPage.tsx    master-detail, 6 tabs
    ├── memory/BeliefsPage.tsx        3-pane belief browser
    ├── delivery/DeploymentsPage.tsx  deployment history
    ├── audit/AuditPage.tsx           durable record + session feed
    ├── reports/PdfPreview.tsx        modal PDF viewer with checksum
    ├── BranchTree.tsx           D3 tree in SVG (see §7 caveat)
    ├── EvidencePanel.tsx        \
    ├── CounterfactualPanel.tsx   |
    ├── MemoryDiffPanel.tsx       |  the six Decision tabs
    ├── ManifestPanel.tsx         |
    ├── DeploymentPanel.tsx       |
    ├── DecisionGraph.tsx        /
    ├── SystemSection.tsx        Observability + Reports (5 internal panels)
    ├── ProofsSection.tsx        4 live proofs against the cluster
    ├── JudgeMode.tsx            13-scene guided tour
    ├── BranchCards.tsx          per-branch stage trackers
    ├── MigrationForm.tsx        start a run
    └── NewFromRepoForm.tsx      ingest a repo
```

---

## 4. Navigation

`Section` is a string union in `AppShell.tsx`; `App.tsx` switches on it. **No
router** — state only, so deep links other than `?root=<uuid>` don't exist.

| Group | Section | Component | Purpose |
|---|---|---|---|
| — | `overview` | OverviewPage | what is happening now |
| — | `decisions` | DecisionsPage | what was decided and why |
| Memory | `branches` | BranchTree + BranchCards | shape of the competition |
| Memory | `memory` | BeliefsPage | what a branch believes |
| Delivery | `deployments` | DeploymentsPage | what shipped |
| Verify | `observability` | SystemSection | perf, cost, CockroachDB, security |
| Verify | `proofs` | ProofsSection | executable claims |
| Verify | `reports` | SystemSection (reports only) | PDFs + archive |
| Verify | `audit` | AuditPage | durable + session timelines |
| — | `judge` | JudgeMode | guided tour, entry point for newcomers |

---

## 5. Design tokens

All in `index.css`. Light under `@theme`, dark under
`:root[data-theme="dark"]`. **Never hardcode a colour in a component.**

```
Surfaces   plane #f7f8fa · surface #ffffff · surface-2 #f1f3f5 · surface-3 #e8ebee
Text       ink #17191c · ink-2 #626b76 · muted #8b949e
Lines      hairline #e2e5e9 · hairline-strong #d0d5db
Accent     accent #2563eb · accent-hover #1d4ed8 · accent-soft #eff4ff
Semantic   success #15803d · warning #b45309 · error #b91c1c · info #0369a1
           each has a matching *-soft background
Vendor     aws #c2610a · crdb #a63232   (only for vendor marks)
Sidebar    sidebar, sidebar-2, sidebar-active, sidebar-ink, sidebar-muted
```

**Radii:** 4px (controls), 6px (panels), 8px (modals). Nothing rounder.
**Type:** Inter, 10.5–15px. Monospace for ids, hashes, HLCs, counts, stages.
**Spacing:** 3-unit gap between panels, 4-unit padding inside them.

There are compat aliases (`--color-violet`, `--color-emerald`, `--color-rose`,
`--color-amber`, `--color-cyan`, `--color-fuchsia`) left over from the old
palette, all remapped onto the new semantic values. **They are debt.** New code
must not use them; removing them from the ~6 older components that still do is
a welcome cleanup.

---

## 6. Primitives (`components/ui/index.tsx`)

Use these. Do not hand-roll a button, badge, panel or empty state.

| Primitive | Signature (abbreviated) |
|---|---|
| `Button` | `{variant?: primary\|secondary\|ghost\|danger, size?: sm\|md, icon?, busy?}` |
| `Badge` | `{tone?: Tone, children}` |
| `Status` | `{status: string, label?}` — maps via `statusTone()` |
| `Metric` | `{label, value, sub?, tone?, mono?}` — **null ⇒ "Not recorded"** |
| `Panel` | `{title, actions?, dense?, className?}` |
| `Table/Th/Td/Tr` | thin wrappers, hairline borders |
| `Drawer` | `{open, onClose, title, subtitle?, width=420}` |
| `Dialog` | `{open, onClose, title, consequences?, confirmLabel?, onConfirm, tone?, busy?}` |
| `Tabs` | `{tabs: {id,label}[], active, onChange}` — generic over the id union |
| `Field`/`FieldList` | `{k, v, mono?, tone?}` — definition-list rows |
| `EmptyState` | `{title, body, action?}` |
| `ErrorState` | `{title, detail, remediation?, onRetry?}` |
| `Skeleton` | `{rows?, className?}` |
| `Tooltip` | `{tip, children}` — 350ms hover delay |
| `Freshness` | `{at: number\|null, source: string}` — "updated 8s ago · CockroachDB" |
| `ToastStack` | `{toasts, onDismiss}` |
| `statusTone()` | `string → "success"\|"running"\|"warning"\|"error"\|"neutral"` |

`Tone = "success" | "running" | "warning" | "error" | "neutral" | "info"`.

---

## 7. Known gaps — good places to work

Ranked by value. These are real, verified as of this document.

1. **7 orphaned components, ~1,100 lines, referenced by nothing:**
   `ActivityFeed`, `StageRail`, `StatsRow`, `Topbar`, `SystemStatusBar`,
   `MergeLog`, `SystemFlow`. They survived the shell rewrite. `SystemFlow` is
   the notable loss — it is the animated AWS/CockroachDB architecture diagram,
   and nothing renders it any more. Either wire it into Observability or
   delete all seven.
2. **No routing.** Section is React state, so the browser back button does
   nothing and no view except the root is linkable. A hash router would be a
   contained, high-value change.
3. **`BranchTree` cannot zoom or pan.** It scales to fit via `viewBox`, which
   is fine for 3–4 branches and unreadable at 20.
4. **`BranchTree` duplicates the palette by hand.** SVG cannot read CSS custom
   properties, so `PALETTE` in that file mirrors token values as literals for
   both themes. Any token change must be mirrored there or the tree drifts.
5. **Reports take ~7s to generate** (measured: 6.5s executive, 7.0s decision).
   They are rendered on request against a cluster ~450ms away. Currently
   covered by a "Generating…" message; caching or a progress signal would be
   better.
6. **No toasts are wired.** `ToastStack` exists and is used by nothing;
   mutations report success/failure inline or not at all.
7. **Bundle is ~500KB** (150KB gzipped), single chunk, no code splitting.
8. **No tests of any kind on the frontend.** No Vitest, no Testing Library, no
   Playwright. Backend has 315.
9. **Accessibility is partial.** `aria-current`, `aria-label` and dialog roles
   are present; focus trapping in modals and a skip link are not.

---

## 8. Data flow

```
CockroachDB ──> FastAPI ──> lib/api.ts ──> App.tsx ──> pages
                    │
                    └── SSE /api/branches/{root}/stream ──> useEventStream
```

`App.tsx` owns and shares: `nodes`, `events`, `branchStage`, `connState`,
`merges`, `decisions`, `deployments`, `readiness`, `repository`,
`selectedDecision`. Pages receive data as props — **do not add a second fetch
of something App already has** (Deployments and Audit were consolidated onto
one fetch for exactly this reason).

Pages that fetch their own data do so because it is page-specific and
parameterised: `BeliefsPage` (per branch, per query), `PdfPreview` (per
report), the Decision tab panels (per merge).

**Conventions for every fetch:** a `cancelled` flag in the effect cleanup;
`.catch()` that sets an error string rather than throwing; `Skeleton` while
pending; `ErrorState` on failure; `EmptyState` when the response is
legitimately empty. All four states, every time.

---

## 9. Backend endpoints the UI uses

```
GET  /api/config                             which backends are stand-ins
GET  /api/readiness                          per-component probes
GET  /api/branches/{root}                    tree snapshot
GET  /api/branches/{root}/stream             SSE
GET  /api/events/{root}                      event backlog
GET  /api/branches/{id}/beliefs?q=&limit=    belief browser
GET  /api/branches/{id}/manifest             reproducibility
POST /api/branches/{id}/replay
GET  /api/merges/{root}                      merge list
GET  /api/decisions/{root}                   decision graph
GET  /api/merges/{id}/evidence|counterfactual|memory-diff
GET  /api/merges/{id}/patch                  unified diff
GET  /api/merges/{id}/pr-description         markdown
POST /api/merges/{id}/apply | /revert
GET  /api/merges/{id}/report.pdf?disposition=inline|attachment
GET  /api/reports/{root}/executive.pdf | engineering.pdf | archive.zip
GET  /api/projects/{root}/deployments
POST /api/merges/{id}/deployments · /api/deployments/{id}/rollback|observations
GET  /api/stats/{root} · /api/cost/{root} · /api/cluster · /api/security
POST /api/proofs/isolation|copy-on-write|temporal|resilience
POST /api/repos · /api/migrations
```

PDF endpoints expose `X-Report-SHA256` and `X-Report-Generated-At` via
`Access-Control-Expose-Headers`; `PdfPreview` reads them through `fetch()` so
it can state the checksum of the exact bytes it renders.

---

## 10. Build

```bash
cd web && npm install
npm run dev      # :5173, proxies /api → :8000
npm run build    # tsc -b && vite build
```

`vite.config.ts` sets `base: command === "build" ? "/static/" : "/"`. This is
deliberate and previously caused two separate bugs — production serves from a
StaticFiles mount at `/static`, dev serves from `/`. Do not make it
unconditional in either direction.

`tsc` runs with `noUnusedLocals`, so an unused import fails the build.

---

## 11. If you are proposing changes

Please:

- Say which of §7 you are addressing, or why a new problem outranks them.
- Use the §6 primitives; extend `ui/index.tsx` rather than adding one-offs.
- Give complete file contents for anything you touch, not fragments.
- Keep all four states (loading / empty / error / loaded) on any data surface.
- Verify both themes — dark is `:root[data-theme="dark"]`, toggled in the topbar.

Please do not:

- Introduce a component library, a CSS framework swap, or a state manager.
- Add gradients, glows, or decorative colour.
- Replace "Not recorded" with a zero, a dash, or a placeholder.
- Add anything implying the system can push code, open a PR, or deploy.
- Use a hue-named class (`text-blue-500`) instead of a role token.
