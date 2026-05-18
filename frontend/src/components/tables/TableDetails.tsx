import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { tablesApi, enrichmentApi, evalApi } from "../../api/client";
import { StatusBadge } from "../common/StatusBadge";
import { SkeletonCard } from "../common/Skeleton";
import { ErrorState } from "../common/ErrorState";
import { EnrichmentEditor } from "./EnrichmentEditor";
import { GoldenQuestions } from "./GoldenQuestions";
import { EvaluationTab } from "./EvaluationTab";
import { AuditTab } from "./AuditTab";
import { PublishModal } from "./PublishModal";
import { ProfilingTab } from "./ProfilingTab";
import { HealthDashboard } from "./HealthDashboard";
import { ArrowLeft, Rocket, ShieldCheck, FlaskConical, CornerUpLeft } from "lucide-react";
import { App } from "antd";
import { useQueryClient, useMutation } from "@tanstack/react-query";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";

dayjs.extend(relativeTime);

type TabKey = "overview" | "enrichment" | "questions" | "evaluations" | "audit" | "profiling" | "health";

export function TableDetails() {
  const { id, tab } = useParams<{ id: string; tab: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const activeTab = (tab as TabKey) || "overview";
  const [showPublish, setShowPublish] = useState(false);
  const [showRegressionModal, setShowRegressionModal] = useState(false);

  const setActiveTab = (newTab: TabKey) => {
    navigate(`/tables/${id}/${newTab}`);
  };

  const { data: table, isLoading, isError, refetch } = useQuery({
    queryKey: ["table", id],
    queryFn: () => tablesApi.get(id!),
    enabled: !!id,
  });

  const { data: enrichment } = useQuery({
    queryKey: ["enrichment", id],
    queryFn: () => enrichmentApi.getLatest(id!),
    enabled: !!id,
  });

  const { data: runs } = useQuery({
    queryKey: ["eval-runs", id],
    queryFn: () => evalApi.listRuns(id!),
    enabled: !!id,
    refetchInterval: (query) => 
      (query.state.data as any[])?.some((r: any) => r.status === "running") ? 500 : 30000,
  });

  const latestPromotion = runs?.find(r => r.triggered_by === "promotion");

  const { data: batchRuns } = useQuery({
    queryKey: ["eval-batch", latestPromotion?.id],
    queryFn: () => evalApi.listBatchRuns(latestPromotion!.id),
    enabled: !!latestPromotion?.id,
    refetchInterval: (query) => 
      (query.state.data as any[])?.some((r: any) => r.status === "running") ? 500 : 30000,
  });

  const qc = useQueryClient();
  const { message } = App.useApp();

  const statusMutation = useMutation({
    mutationFn: (newStatus: string) => tablesApi.updateStatus(id!, newStatus),
    onSuccess: (updated) => {
      qc.setQueryData(["table", id], updated);
      message.success(`Status updated to ${updated.status}`);
    },
  });

  const activePromotion = latestPromotion?.status === "running" ? latestPromotion : undefined;

  // Precise filter: only regression runs that were created by THIS promotion run
  const currentBatchRegressions = batchRuns?.filter(r => r.triggered_by === "regression") || [];

  const activeRegressions = currentBatchRegressions.filter(r => r.status === "running");
  
  // Keep banner visible while running, or up to 5 minutes after completion so users can review
  const isRecentPromotion = latestPromotion && dayjs().diff(dayjs(latestPromotion.created_at), 'minute') < 5;
  const isPromoting = !!activePromotion || activeRegressions.length > 0 || (isRecentPromotion && currentBatchRegressions.length > 0);
  
  const phase = activePromotion ? "Phase 1: Evaluating Target Table" : 
                activeRegressions.length > 0 ? "Phase 2: Running Regression Suite" : "Promotion Complete";

  const tabs: Array<{ key: TabKey; label: string }> = [
    { key: "overview",     label: t("tabs.overview") },
    { key: "enrichment",   label: t("tabs.enrichment") },
    { key: "questions",    label: t("tabs.questions") },
    { key: "evaluations",  label: t("tabs.evaluations") },
    { key: "profiling",    label: "Profiling" },
    { key: "health",       label: "Health" },
    { key: "audit",        label: t("tabs.audit") },
  ];

  if (isLoading) return <div className="page"><SkeletonCard /></div>;
  if (isError || !table) return <div className="page"><ErrorState onRetry={refetch} /></div>;

  return (
    <div className="page">
      {/* Header */}
      <div className="page__header">
        <div className="flex items-center gap-3">
          <button className="btn btn--ghost btn--sm" onClick={() => navigate("/tables")}>
            <ArrowLeft size={14} />
          </button>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="page__title">{table.name}</h1>
              <StatusBadge status={table.status} />
            </div>
            <p className="page__subtitle">
              <code style={{ color: "var(--text-muted)" }}>{table.schema_name}</code>
              {" · "}Owner: {table.owner_id}
              {" · "}Updated {dayjs(table.updated_at).fromNow()}
            </p>
          </div>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          {table.status === "draft" && (
            <button className="btn btn--ghost" onClick={() => statusMutation.mutate("sandbox")}>
              <FlaskConical size={14} /> Send to Sandbox
            </button>
          )}
          {table.status === "sandbox" && (
            <button className="btn btn--ghost" onClick={() => statusMutation.mutate("verified")}>
              <ShieldCheck size={14} /> Verify Table
            </button>
          )}
          {table.status === "production" && (
            <button className="btn btn--ghost" onClick={() => statusMutation.mutate("sandbox")}>
              <CornerUpLeft size={14} /> Demote to Sandbox
            </button>
          )}
          {table.status !== "production" && (
            <button className="btn btn--primary" onClick={() => setShowPublish(true)} disabled={!!activePromotion}>
              <Rocket size={15} />
              {activePromotion ? "Publishing..." : t("publish.btn")}
            </button>
          )}
        </div>
      </div>

      {isPromoting && (
        <div style={{ 
          margin: "0 0 20px 0", padding: "12px 16px", borderRadius: 8, 
          background: "rgba(14,165,233,0.08)", border: "1px solid rgba(14,165,233,0.25)",
          display: "flex", alignItems: "center", justifyContent: "space-between"
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--accent)" }}>
            <div className="spinner-mini" style={{ width: 14, height: 14, border: "2px solid var(--accent)", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 1s linear infinite" }} />
            <div style={{ display: "flex", flexDirection: "column" }}>
              <span style={{ fontWeight: 600 }}>{phase}...</span>
              {!activePromotion && currentBatchRegressions.length > 0 && (
                <span style={{ fontSize: 11, opacity: 0.8 }}>
                  {currentBatchRegressions.filter(r => r.status === "completed" || r.status === "failed").length} / {currentBatchRegressions.length} Production Tables Validated
                </span>
              )}
            </div>
          </div>
          <button 
            className="btn btn--primary btn--sm" 
            onClick={() => activePromotion ? setActiveTab("evaluations") : setShowRegressionModal(true)}
          >
            View {activePromotion ? "Progress" : "Regression Suite"}
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="tabs">
        {tabs.map((tab) => (
          <div
            key={tab.key}
            className={`tab-item${activeTab === tab.key ? " tab-item--active" : ""}`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </div>
        ))}
      </div>

      {/* Content */}
      {activeTab === "overview" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <div className="card">
            <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 16, color: "var(--text-secondary)" }}>
              Table Metadata
            </h3>
            {[
              { label: "ID",           value: table.id },
              { label: "Name",         value: table.name },
              { label: "Schema",       value: table.schema_name },
              { label: "Status",       value: <StatusBadge status={table.status} /> },
              { label: "Owner",        value: table.owner_id },
              { label: "Created",      value: dayjs(table.created_at).format("MMM D, YYYY HH:mm") },
              { label: "Last Updated", value: dayjs(table.updated_at).format("MMM D, YYYY HH:mm") },
            ].map(({ label, value }) => (
              <div key={label} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                <span style={{ color: "var(--text-muted)", fontSize: 13 }}>{label}</span>
                <span style={{ fontWeight: 500, fontSize: 13 }}>{value}</span>
              </div>
            ))}
          </div>

          <div className="card">
            <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 16, color: "var(--text-secondary)" }}>
              Health &amp; Lifecycle
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {(["draft","sandbox","verified","production","degraded"] as const).map((s) => (
                <div key={s} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <div style={{
                    width: 28, height: 28, borderRadius: "50%",
                    background: table.status === s ? "var(--accent-dim)" : "var(--bg-base)",
                    border: `2px solid ${table.status === s ? "var(--accent)" : "var(--border)"}`,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 11, fontWeight: 700, color: table.status === s ? "var(--accent-hover)" : "var(--text-muted)",
                  }}>
                    {table.status === s ? "●" : "○"}
                  </div>
                  <StatusBadge status={s} />
                  {table.status === s && (
                    <span style={{ fontSize: 11, color: "var(--accent-hover)", fontWeight: 600 }}>← current</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        {enrichment?.data && (
            <div className="card">
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 16, color: "var(--text-secondary)" }}>
                Table Description & Schema
              </h3>
              <p style={{ marginBottom: 16, fontSize: 13, color: "var(--text)" }}>
                {enrichment.data.table_description || "No description provided."}
              </p>
              {enrichment.data.columns && enrichment.data.columns.length > 0 && (
                <div style={{ overflowX: "auto" }}>
                  <table className="data-table" style={{ marginTop: 8 }}>
                    <thead>
                      <tr>
                        <th>Column</th>
                        <th>Description</th>
                        <th>Tags</th>
                      </tr>
                    </thead>
                    <tbody>
                      {enrichment.data.columns.map((col: any) => (
                        <tr key={col.name}>
                          <td style={{ fontWeight: 500, fontSize: 13 }}>{col.name}</td>
                          <td style={{ fontSize: 13, color: "var(--text-muted)" }}>{col.description || "-"}</td>
                          <td>
                            <div style={{ display: "flex", gap: 4 }}>
                              {col.is_time && <span className="badge badge--neutral" style={{ fontSize: 11, padding: "2px 6px" }}>Time</span>}
                              {col.is_geo && <span className="badge badge--neutral" style={{ fontSize: 11, padding: "2px 6px" }}>Geo</span>}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {activeTab === "enrichment"  && <EnrichmentEditor tableId={id!} />}
      {activeTab === "questions"   && <GoldenQuestions tableId={id!} />}
      {activeTab === "evaluations" && <EvaluationTab tableId={id!} />}
      {activeTab === "profiling"   && <ProfilingTab tableId={id!} />}
      {activeTab === "health"      && <HealthDashboard tableId={id!} />}
      {activeTab === "audit"       && <AuditTab tableId={id!} />}

      {showPublish && (
        <PublishModal 
          tableId={id!} 
          onClose={() => setShowPublish(false)} 
          onTrackProgress={() => { setActiveTab("evaluations"); setShowPublish(false); }}
        />
      )}
      {showRegressionModal && (
        <div className="modal-overlay" onClick={() => setShowRegressionModal(false)}>
          <div className="modal" style={{ maxWidth: 600 }} onClick={e => e.stopPropagation()}>
            <h2 className="modal__title">Production Regression Suite</h2>
            <p style={{ color: "var(--text-muted)", fontSize: 13, marginBottom: 16 }}>
              Verifying all tables currently in production to ensure no performance degradation.
            </p>
            <div className="card" style={{ padding: 0, overflow: "hidden" }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Table</th>
                    <th>Score</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {currentBatchRegressions.map(run => (
                    <tr key={run.id}>
                      <td style={{ fontWeight: 600, fontSize: 13 }}>{run.table_name || run.table_id.slice(0,8)}</td>
                      <td>
                        <div style={{ fontWeight: 700, color: run.score >= 0.8 ? "var(--status-production)" : "var(--status-degraded)" }}>
                          {Math.round(run.score * 100)}%
                        </div>
                      </td>
                      <td>
                        <span style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase" }}>
                          {run.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="modal__actions">
              <button className="btn btn--primary" onClick={() => setShowRegressionModal(false)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
