import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Rocket } from "lucide-react";
import { tablesApi, enrichmentApi } from "../../api/client";
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
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";

dayjs.extend(relativeTime);

type TabKey = "overview" | "enrichment" | "questions" | "evaluations" | "audit" | "profiling" | "health";

export function TableDetails() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [showPublish, setShowPublish] = useState(false);

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
        {table.status !== "production" && (
          <button className="btn btn--primary" onClick={() => setShowPublish(true)}>
            <Rocket size={15} />
            {t("publish.btn")}
          </button>
        )}
      </div>

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
        <PublishModal tableId={id!} onClose={() => setShowPublish(false)} />
      )}
    </div>
  );
}
