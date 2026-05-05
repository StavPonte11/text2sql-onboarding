import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PlayCircle, Database, ChevronDown, ListChecks, CalendarClock, History, Check } from "lucide-react";
import { tablesApi } from "../api/client";
import { orchestrationApi } from "../api/orchestration";
import { RunHistoryTable } from "../components/monitoring/RunHistoryTable";
import { ScheduleManager } from "../components/monitoring/ScheduleManager";
import { Spinner, SectionHeader, EmptySlate } from "../components/common/EvalUI";
import type { Table } from "../types";

type Tab = "history" | "schedules" | "run";

// ── Run trigger panel ──────────────────────────────────────────────────────────
function RunTriggerPanel() {
  const qc = useQueryClient();
  const [selectedTableIds, setSelectedTableIds] = useState<string[]>([]);
  const [triggeredBy] = useState("user");
  const [launched, setLaunched] = useState(false);

  const { data: tables = [], isLoading: tablesLoading } = useQuery({
    queryKey: ["tables-all"],
    queryFn: () => tablesApi.list(),
  });

  const triggerMut = useMutation({
    mutationFn: () => orchestrationApi.triggerRun(selectedTableIds, triggeredBy),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval-runs"] });
      qc.invalidateQueries({ queryKey: ["system-health"] });
      setLaunched(true);
      setTimeout(() => setLaunched(false), 4000);
    },
  });

  const toggleTable = (id: string) => {
    setSelectedTableIds(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    );
  };

  const selectAll = () => setSelectedTableIds(tables.map(t => t.id));
  const clearAll = () => setSelectedTableIds([]);

  return (
    <div className="card">
      <SectionHeader
        title="Trigger Evaluation Run"
        sub="Select tables then launch an evaluation"
        action={
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--ghost btn--sm" onClick={selectAll}>Select All</button>
            <button className="btn btn--ghost btn--sm" onClick={clearAll}>Clear</button>
          </div>
        }
      />

      {tablesLoading ? (
        <div style={{ display: "flex", justifyContent: "center", padding: 24 }}><Spinner /></div>
      ) : !tables.length ? (
        <EmptySlate icon={<Database size={28} />} title="No tables found" sub="Add tables first via the Tables section" />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 8, marginBottom: 16 }}>
          {tables.map((t: Table) => {
            const selected = selectedTableIds.includes(t.id);
            const statusColor: Record<string, string> = {
              production: "#10b981", sandbox: "#f59e0b", verified: "#3b82f6",
              draft: "#64748b", degraded: "#ef4444",
            };
            const sc = statusColor[t.status] ?? "#64748b";
            return (
              <div
                key={t.id}
                onClick={() => toggleTable(t.id)}
                style={{
                  padding: "10px 12px", borderRadius: 8, cursor: "pointer",
                  background: selected ? "var(--accent-dim)" : "var(--bg-base)",
                  border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                  transition: "all 0.18s ease",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
                  <div style={{ width: 7, height: 7, borderRadius: "50%", background: sc }} />
                  <span style={{ fontSize: 13, fontWeight: 600, color: selected ? "var(--accent-hover)" : "var(--text-primary)" }}>
                    {t.name}
                  </span>
                </div>
                <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
                  {t.schema_name} · {t.status}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {launched && (
        <div style={{
          padding: "10px 14px", borderRadius: 8, background: "rgba(16,185,129,0.08)",
          border: "1px solid rgba(16,185,129,0.25)", color: "#10b981",
          fontSize: 13, fontWeight: 600, marginBottom: 12,
          display: "flex", alignItems: "center", gap: 6,
        }}>
          <Check size={16} /> Evaluation run launched for {selectedTableIds.length} table{selectedTableIds.length > 1 ? "s" : ""} — results will appear in History below.
        </div>
      )}

      <button
        className="btn btn--primary"
        disabled={selectedTableIds.length === 0 || triggerMut.isPending}
        onClick={() => triggerMut.mutate()}
        style={{ width: "100%", justifyContent: "center" }}
      >
        {triggerMut.isPending
          ? <><Spinner size={15} color="#fff" /> Running…</>
          : <><PlayCircle size={16} /> Launch Evaluation ({selectedTableIds.length} table{selectedTableIds.length !== 1 ? "s" : ""})</>}
      </button>
    </div>
  );
}

// ── EvaluationsPage ────────────────────────────────────────────────────────────
export function EvaluationsPage() {
  const [activeTab, setActiveTab] = useState<Tab>("history");

  const TABS: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: "history",   label: "Execution History", icon: <History size={14} /> },
    { key: "schedules", label: "Scheduled Runs",    icon: <CalendarClock size={14} /> },
    { key: "run",       label: "Run Controls",       icon: <PlayCircle size={14} /> },
  ];

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Evaluations</h1>
          <p className="page__subtitle">Manage evaluation runs, schedules, and execution history</p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="tabs" style={{ marginBottom: 24 }}>
        {TABS.map(tab => (
          <button
            key={tab.key}
            className={`tab-item${activeTab === tab.key ? " tab-item--active" : ""}`}
            onClick={() => setActiveTab(tab.key)}
            style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: "none", cursor: "pointer" }}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === "history" && (
        <div>
          <SectionHeader
            title="Execution History"
            sub="All evaluation runs across tables — click any row to view full report"
          />
          <RunHistoryTable limit={100} />
        </div>
      )}

      {activeTab === "schedules" && <ScheduleManager />}

      {activeTab === "run" && <RunTriggerPanel />}
    </div>
  );
}
