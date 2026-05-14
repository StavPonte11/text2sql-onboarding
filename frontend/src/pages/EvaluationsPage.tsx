import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PlayCircle, Database, ChevronDown, ListChecks, CalendarClock, History, Check } from "lucide-react";
import { App } from "antd";
import { tablesApi } from "../api/client";
import { orchestrationApi } from "../api/orchestration";
import { RunHistoryTable } from "../components/monitoring/RunHistoryTable";
import { ScheduleManager } from "../components/monitoring/ScheduleManager";
import { Spinner, SectionHeader, EmptySlate } from "../components/common/EvalUI";
import type { Table } from "../types";
import "./EvaluationsPage.css";

type Tab = "history" | "schedules" | "run";

// ── Run trigger panel ──────────────────────────────────────────────────────────
function RunTriggerPanel() {
  const qc = useQueryClient();
  const [selectedTableIds, setSelectedTableIds] = useState<string[]>([]);
  const [triggeredBy] = useState("user");
  const [launched, setLaunched] = useState(false);
  const { message } = App.useApp();

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
    onError: () => {
      message.error("Evaluation failed. Please go change the descriptions in Oasis platform.");
    }
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
          <div className="run-trigger-panel__actions">
            <button className="btn btn--ghost btn--sm" onClick={selectAll}>Select All</button>
            <button className="btn btn--ghost btn--sm" onClick={clearAll}>Clear</button>
          </div>
        }
      />

      {tablesLoading ? (
        <div className="run-trigger-panel__loading"><Spinner /></div>
      ) : !tables.length ? (
        <EmptySlate icon={<Database size={28} />} title="No tables found" sub="Add tables first via the Tables section" />
      ) : (
        <div className="run-trigger-panel__grid">
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
                className={`table-card${selected ? " table-card--selected" : ""}`}
              >
                <div className="table-card__header">
                  <div className="table-card__status-dot" style={{ background: sc }} />
                  <span className={`table-card__name${selected ? " table-card__name--selected" : ""}`}>
                    {t.name}
                  </span>
                </div>
                <div className="table-card__info">
                  {t.schema_name} · {t.status}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {launched && (
        <div className="launch-success-banner">
          <Check size={16} /> Evaluation run launched for {selectedTableIds.length} table{selectedTableIds.length > 1 ? "s" : ""} — results will appear in History below.
        </div>
      )}

      <button
        className="btn btn--primary launch-btn-full"
        disabled={selectedTableIds.length === 0 || triggerMut.isPending}
        onClick={() => triggerMut.mutate()}
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
      <div className="tabs evaluations-page__tabs">
        {TABS.map(tab => (
          <button
            key={tab.key}
            className={`tab-item evaluations-page__tab-item${activeTab === tab.key ? " tab-item--active" : ""}`}
            onClick={() => setActiveTab(tab.key)}
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
