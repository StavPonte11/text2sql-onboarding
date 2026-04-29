import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Activity } from "lucide-react";
import { auditApi } from "../../api/client";
import { SkeletonTable } from "../common/Skeleton";
import { ErrorState } from "../common/ErrorState";
import dayjs from "dayjs";

export function MonitoringPage() {
  const { t } = useTranslation();

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["audit-all"],
    queryFn: () => auditApi.queries({ limit: 100 }),
  });

  if (isLoading) return <div className="page"><SkeletonTable rows={8} cols={5} /></div>;
  if (isError) return <div className="page"><ErrorState onRetry={refetch} /></div>;

  const total = data?.length ?? 0;
  const successCount = data?.filter((r) => r.status === "success").length ?? 0;
  const avgLatency = data?.length
    ? Math.round(data.filter((r) => r.execution_time_ms).reduce((s, r) => s + (r.execution_time_ms ?? 0), 0) / (data.filter((r) => r.execution_time_ms).length || 1))
    : 0;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">{t("audit.title")}</h1>
          <p className="page__subtitle">Platform-wide query audit log</p>
        </div>
      </div>

      {/* Stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 24 }}>
        {[
          { label: "Total Queries", value: total, color: "var(--accent-hover)" },
          { label: "Success Rate", value: total ? `${Math.round((successCount / total) * 100)}%` : "—", color: "var(--status-production)" },
          { label: "Avg Latency", value: avgLatency ? `${avgLatency}ms` : "—", color: "var(--status-sandbox)" },
        ].map(({ label, value, color }) => (
          <div key={label} className="card" style={{ textAlign: "center" }}>
            <div style={{ fontSize: 28, fontWeight: 800, color }}>{value}</div>
            <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginTop: 4 }}>{label}</div>
          </div>
        ))}
      </div>

      {!data?.length ? (
        <div className="card">
          <div className="empty-state">
            <Activity size={36} className="empty-state__icon" />
            <div className="empty-state__text">{t("common.noData")}</div>
            <div className="empty-state__sub">No audit entries yet</div>
          </div>
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("audit.query")}</th>
                <th>Table</th>
                <th>{t("audit.user")}</th>
                <th>{t("audit.executed")}</th>
                <th>{t("audit.latency")}</th>
                <th>{t("audit.success")}</th>
              </tr>
            </thead>
            <tbody>
              {data.map((row) => (
                <tr key={row.id}>
                  <td style={{ maxWidth: 280 }}>
                    <code style={{ fontSize: 12, color: "var(--text-muted)", whiteSpace: "normal" }}>
                      {row.raw_question.length > 70 ? row.raw_question.slice(0, 70) + "…" : row.raw_question}
                    </code>
                  </td>
                  <td style={{ fontSize: 12, color: "var(--text-secondary)" }}>{row.table_id ?? "—"}</td>
                  <td style={{ fontSize: 13 }}>{row.user_id}</td>
                  <td style={{ fontSize: 12, color: "var(--text-muted)" }}>
                    {dayjs(row.created_at).format("MMM D, HH:mm:ss")}
                  </td>
                  <td>{row.execution_time_ms ?? "—"}</td>
                  <td>
                    <span style={{ color: row.status === "success" ? "var(--status-production)" : "var(--status-degraded)", fontWeight: 700 }}>
                      {row.status === "success" ? "✓" : "✗"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
