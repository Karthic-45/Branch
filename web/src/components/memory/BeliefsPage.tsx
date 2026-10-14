import { useState, useEffect, useMemo } from "react";
import { Search, Eye, Copy, Check, ChevronDown, Database } from "lucide-react";
import type { BranchNode, BeliefPage } from "../../lib/types";
import { fetchBeliefs } from "../../lib/api";
import { TableRowsSkeleton } from "../common/Loading";

interface Props {
  rootId: string | null;
  nodes: BranchNode[];
}

export function BeliefsPage({ rootId, nodes }: Props) {
  const branchOptions = useMemo(() => {
    return nodes.map((n) => ({
      id: n.id,
      label: `${n.id.slice(0, 8)} (${n.hypothesis || n.status})`,
    }));
  }, [nodes]);

  const [selectedBranchId, setSelectedBranchId] = useState<string>(branchOptions[0]?.id ?? rootId ?? "");
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedType, setSelectedType] = useState("All Types");
  const [showTombstones, setShowTombstones] = useState(true);
  const [showInherited, setShowInherited] = useState(true);
  const [copied, setCopied] = useState(false);
  const [realBeliefs, setRealBeliefs] = useState<BeliefPage | null>(null);
  const [beliefsLoading, setBeliefsLoading] = useState(false);
  const [queryTime, setQueryTime] = useState<string>("—");

  // Keep selected branch valid if nodes change
  useEffect(() => {
    if (branchOptions.length > 0 && !branchOptions.some((b) => b.id === selectedBranchId)) {
      setSelectedBranchId(branchOptions[0].id);
    } else if (branchOptions.length === 0 && rootId) {
      setSelectedBranchId(rootId);
    }
  }, [branchOptions, selectedBranchId, rootId]);

  // Fetch live beliefs if connected to a real branch
  useEffect(() => {
    const branchToFetch = selectedBranchId || rootId;
    if (!branchToFetch) {
      setRealBeliefs(null);
      return;
    }
    let cancelled = false;
    const start = performance.now();
    setBeliefsLoading(true);
    fetchBeliefs(branchToFetch, {
      q: searchQuery.trim() || undefined,
      includeTombstoned: showTombstones,
    })
      .then((page) => {
        if (!cancelled) {
          setRealBeliefs(page);
          const dur = (performance.now() - start) / 1000;
          setQueryTime(`${dur.toFixed(2)}s`);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRealBeliefs(null);
          setQueryTime("—");
        }
      })
      .finally(() => {
        if (!cancelled) setBeliefsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedBranchId, rootId, searchQuery, showTombstones]);

  const beliefsData = useMemo(() => {
    if (realBeliefs && realBeliefs.beliefs.length > 0) {
      return realBeliefs.beliefs.map((b) => ({
        key: b.key,
        value: b.content ?? "",
        source: b.tombstone ? "Tombstoned" : b.inherited ? "Inherited" : "Own",
        type: b.key.includes("prompt") ? "Prompt" : b.key.includes("test") || b.key.includes("hook") ? "Feature" : "Config",
        hlc: b.created_at_hlc || "—",
        s3_uri: b.s3_uri,
      }));
    }
    return [];
  }, [realBeliefs]);

  const filteredBeliefs = useMemo(() => {
    return beliefsData.filter((b) => {
      if (!showTombstones && b.source === "Tombstoned") return false;
      if (!showInherited && b.source === "Inherited") return false;
      if (selectedType !== "All Types" && b.type !== selectedType) return false;
      if (searchQuery && !b.key.toLowerCase().includes(searchQuery.toLowerCase())) return false;
      return true;
    });
  }, [beliefsData, showTombstones, showInherited, selectedType, searchQuery]);

  const [activeKey, setActiveKey] = useState<string>("");

  const activeBelief = useMemo(() => {
    if (!filteredBeliefs.length) return null;
    return filteredBeliefs.find((b) => b.key === activeKey) ?? filteredBeliefs[0];
  }, [filteredBeliefs, activeKey]);

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const selectedOption =
    branchOptions.find((b) => b.id === selectedBranchId) ??
    (selectedBranchId ? { id: selectedBranchId, label: selectedBranchId.slice(0, 8) } : null);

  const visibleCount = realBeliefs?.total_visible ?? 0;
  const ownCount = realBeliefs?.own ?? 0;
  const tombstonedCount = realBeliefs?.tombstoned ?? 0;

  if (branchOptions.length === 0 && !rootId) {
    return (
      <div className="p-5 sm:p-6 lg:p-7 w-full">
        <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-12 text-center shadow-xs">
          <Database className="h-10 w-10 text-slate-300 dark:text-slate-600 mx-auto mb-3" />
          <h2 className="text-base font-bold text-slate-800 dark:text-slate-200">No Branches Available</h2>
          <p className="text-xs text-slate-400 mt-1 max-w-md mx-auto">
            Ingest a repository or connect to a project root above to explore copy-on-write memory and branch beliefs.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-5 sm:p-6 lg:p-7 w-full space-y-6">
      {/* ── Screen 5: Branch Beliefs & Memory Card ── */}
      <div className="rounded-2xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-[#11141c] p-6 shadow-xs space-y-6">
        {/* Header with Branch Selector */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-slate-100 dark:border-slate-800">
          <div>
            <h1 className="text-xl font-bold text-slate-900 dark:text-white">
              Branch Beliefs &amp; Memory
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              Copy-on-write resolution: visible memories vs. branch-local writes.
            </p>
          </div>

          {branchOptions.length > 0 && selectedOption && (
            <div className="relative">
              <button
                onClick={() => setDropdownOpen((o) => !o)}
                className="flex items-center gap-2 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 px-3.5 py-1.5 text-xs font-semibold text-slate-700 dark:text-slate-300 shadow-2xs hover:border-indigo-300 transition-colors cursor-pointer"
              >
                <span className="text-slate-400 font-normal">Branch:</span>
                <span className="font-mono text-indigo-600 dark:text-indigo-400">
                  {selectedOption.label}
                </span>
                <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
              </button>

              {dropdownOpen && (
                <div className="absolute right-0 top-full mt-1.5 z-30 w-72 rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-1.5 shadow-lg space-y-0.5">
                  {branchOptions.map((opt) => (
                    <button
                      key={opt.id}
                      onClick={() => {
                        setSelectedBranchId(opt.id);
                        setDropdownOpen(false);
                      }}
                      className={`w-full text-left rounded-lg px-2.5 py-1.5 text-xs font-mono transition-colors cursor-pointer ${
                        selectedBranchId === opt.id
                          ? "bg-indigo-50 dark:bg-indigo-950/50 text-indigo-600 dark:text-indigo-400 font-bold"
                          : "text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800"
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 4 Stat Badges */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3.5">
          <div className="rounded-xl border border-indigo-100 dark:border-indigo-950/60 bg-indigo-50/50 dark:bg-indigo-950/30 p-3.5 text-center">
            <div className="text-2xl font-extrabold text-indigo-700 dark:text-indigo-300">
              {visibleCount}
            </div>
            <div className="text-xs font-semibold text-indigo-900/80 dark:text-indigo-200 mt-0.5">
              Visible Beliefs
            </div>
            <div className="text-[10px] text-indigo-500 font-medium">(Inherited + own)</div>
          </div>

          <div className="rounded-xl border border-emerald-100 dark:border-emerald-950/60 bg-emerald-50/50 dark:bg-emerald-950/30 p-3.5 text-center">
            <div className="text-2xl font-extrabold text-emerald-700 dark:text-emerald-300">
              {ownCount}
            </div>
            <div className="text-xs font-semibold text-emerald-900/80 dark:text-emerald-200 mt-0.5">
              Own
            </div>
            <div className="text-[10px] text-emerald-500 font-medium">(Wrote by this branch)</div>
          </div>

          <div className="rounded-xl border border-amber-100 dark:border-amber-950/60 bg-amber-50/50 dark:bg-amber-950/30 p-3.5 text-center">
            <div className="text-2xl font-extrabold text-amber-700 dark:text-amber-300">
              {tombstonedCount}
            </div>
            <div className="text-xs font-semibold text-amber-900/80 dark:text-amber-200 mt-0.5">
              Tombstoned
            </div>
            <div className="text-[10px] text-amber-500 font-medium">(Shadowed)</div>
          </div>

          <div className="rounded-xl border border-slate-100 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-900/40 p-3.5 text-center">
            <div className="text-2xl font-extrabold text-slate-800 dark:text-slate-200 font-mono">
              {queryTime}
            </div>
            <div className="text-xs font-semibold text-slate-600 dark:text-slate-400 mt-0.5">
              Query Time
            </div>
            <div className="text-[10px] text-slate-400 font-medium">HLC resolved</div>
          </div>
        </div>

        {/* Filters Row */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
          <div className="flex flex-wrap items-center gap-3">
            {/* Search */}
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400" />
              <input
                type="text"
                placeholder="Search beliefs..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-8 pr-3 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 w-52 text-slate-800 dark:text-slate-200"
              />
            </div>

            {/* Type selector */}
            <select
              value={selectedType}
              onChange={(e) => setSelectedType(e.target.value)}
              className="text-xs rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 px-3 py-1.5 font-medium cursor-pointer"
            >
              <option>All Types</option>
              <option>Config</option>
              <option>Feature</option>
              <option>Prompt</option>
            </select>
          </div>

          {/* Checkboxes */}
          <div className="flex items-center gap-4 text-xs font-medium text-slate-600 dark:text-slate-400">
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={showTombstones}
                onChange={(e) => setShowTombstones(e.target.checked)}
                className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
              />
              <span>Show tombstones</span>
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={showInherited}
                onChange={(e) => setShowInherited(e.target.checked)}
                className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
              />
              <span>Show inherited</span>
            </label>
          </div>
        </div>

        {/* Memory Data Table */}
        <div className="overflow-x-auto rounded-xl border border-slate-100 dark:border-slate-800">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 dark:bg-slate-900/60 text-slate-400 font-semibold border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className="py-2.5 px-3">Key</th>
                <th className="py-2.5 px-3">Value (Preview)</th>
                <th className="py-2.5 px-3">Source</th>
                <th className="py-2.5 px-3">Type</th>
                <th className="py-2.5 px-3">Timestamp (HLC)</th>
                <th className="py-2.5 px-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800 font-medium">
              {beliefsLoading && !realBeliefs ? (
                <TableRowsSkeleton cols={6} rows={8} />
              ) : filteredBeliefs.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-slate-400">
                    {beliefsData.length === 0 ? "No beliefs recorded for this branch yet." : "No beliefs match your filters"}
                  </td>
                </tr>
              ) : (
                filteredBeliefs.map((b) => (
                  <tr
                    key={b.key}
                    onClick={() => setActiveKey(b.key)}
                    className={`hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors cursor-pointer ${
                      activeBelief?.key === b.key ? "bg-indigo-50/40 dark:bg-indigo-950/20" : ""
                    }`}
                  >
                    <td className="py-2.5 px-3 font-mono font-bold text-slate-800 dark:text-slate-200">
                      {b.key}
                    </td>
                    <td className="py-2.5 px-3 font-mono text-slate-600 dark:text-slate-400 max-w-xs truncate">
                      {b.value}
                    </td>
                    <td className="py-2.5 px-3">
                      <span
                        className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-bold ${
                          b.source === "Own"
                            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-400"
                            : b.source === "Tombstoned"
                            ? "bg-rose-100 text-rose-700 dark:bg-rose-950/60 dark:text-rose-400"
                            : "bg-blue-100 text-blue-700 dark:bg-blue-950/60 dark:text-blue-400"
                        }`}
                      >
                        {b.source}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-slate-500">{b.type}</td>
                    <td className="py-2.5 px-3 font-mono text-slate-400 text-[11px]">{b.hlc}</td>
                    <td className="py-2.5 px-3 text-right">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setActiveKey(b.key);
                        }}
                        className="p-1 text-slate-400 hover:text-indigo-600 transition-colors cursor-pointer"
                        title="Inspect belief"
                      >
                        <Eye className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* ── Bottom Expandable Belief Details Panel ── */}
        {activeBelief ? (
          <div className="rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-900/40 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500">
                Belief Details
              </h3>
              <span className="font-mono text-[11px] text-slate-400">ID: {activeBelief.key}</span>
            </div>

            <div className="flex items-center justify-between rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-2.5">
              <div className="space-y-0.5 min-w-0 flex-1 mr-3">
                <div className="font-mono text-xs font-bold text-slate-900 dark:text-white truncate">
                  {activeBelief.key}
                </div>
                <div className="font-mono text-[11.5px] text-indigo-600 dark:text-indigo-400 break-all">
                  {activeBelief.value}
                </div>
              </div>
              <button
                onClick={() => handleCopy(activeBelief.value ?? "")}
                className="flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-2 py-1 text-xs text-slate-600 dark:text-slate-300 hover:bg-slate-100 cursor-pointer flex-none"
              >
                {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
                <span>{copied ? "Copied" : "Copy"}</span>
              </button>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs pt-1">
              <div>
                <span className="text-[10px] text-slate-400 block uppercase">Type</span>
                <span className="font-semibold text-slate-700 dark:text-slate-300">{activeBelief.type}</span>
              </div>
              <div>
                <span className="text-[10px] text-slate-400 block uppercase">Source</span>
                <span
                  className={`font-semibold ${
                    activeBelief.source === "Own"
                      ? "text-emerald-600 dark:text-emerald-400"
                      : activeBelief.source === "Tombstoned"
                      ? "text-rose-600 dark:text-rose-400"
                      : "text-blue-600 dark:text-blue-400"
                  }`}
                >
                  {activeBelief.source}
                </span>
              </div>
              <div>
                <span className="text-[10px] text-slate-400 block uppercase">Timestamp (HLC)</span>
                <span className="font-mono text-slate-700 dark:text-slate-300">{activeBelief.hlc}</span>
              </div>
              <div>
                <span className="text-[10px] text-slate-400 block uppercase">S3 Artifact URI</span>
                <span className="font-mono text-slate-400 truncate block text-[10.5px]">
                  {activeBelief.s3_uri || "Stored directly in CockroachDB"}
                </span>
              </div>
            </div>
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-slate-200 dark:border-slate-800 p-6 text-center text-xs text-slate-400 flex items-center justify-center gap-2">
            <Database className="h-4 w-4 text-slate-400" />
            <span>Select a belief above to inspect its details and copy its content.</span>
          </div>
        )}
      </div>
    </div>
  );
}
