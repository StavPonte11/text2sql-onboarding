import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Activity, Check, X } from "lucide-react";
import { auditApi } from "../../api/client";
import { SkeletonTable } from "../common/Skeleton";
import { ErrorState } from "../common/ErrorState";
import dayjs from "dayjs";

interface Props { tableId: string }

export function AuditTab({ tableId }: Props) {
  const { t } = useTranslation();

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["audit", tableId],
    queryFn: () => auditApi.queries({ table_id: tableId }),
  });

  if (isLoading) return <SkeletonTable rows={5} cols={5} />;
  if (isError) return <ErrorState onRetry={refetch} />;

  return (
    <div>
      <h2 style={{ fontSize: 17, fontWeight: 700, marginBottom: 20 }}>{t("audit.title")}</h2>

      {!data?.length ? (
        <div className="card">
          <div className="empty-state">
            <Activity size={36} className="empty-state__icon" />
            <div className="empty-state__text">{t("common.noData")}</div>
            <div className="empty-state__sub">No queries have been logged for this table yet</div>
          </div>
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("audit.query")}</th>
                <th>{t("audit.user")}</th>
                <th>{t("audit.executed")}</th>
                <th>{t("audit.latency")}</th>
                <th>{t("audit.success")}</th>
              </tr>
            </thead>
            <tbody>
              {data.map((row) => (
                <tr key={row.id}>
                  <td style={{ maxWidth: 340 }}>
                    <code style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      {row.raw_question.length > 80 ? row.raw_question.slice(0, 80) + "…" : row.raw_question}
                    </code>
                  </td>
                  <td style={{ fontSize: 13, color: "var(--text-secondary)" }}>{row.user_id}</td>
                  <td style={{ fontSize: 12, color: "var(--text-muted)" }}>
                    {dayjs(row.created_at).format("MMM D, HH:mm:ss")}
                  </td>
                  <td style={{ fontSize: 13 }}>{row.execution_time_ms ?? "—"}</td>
                  <td>
                    <span style={{ color: row.status === "success" ? "var(--status-production)" : "var(--status-degraded)", fontWeight: 600, fontSize: 12, display: "flex", alignItems: "center" }}>
                      {row.status === "success" ? <Check size={14} /> : <X size={14} />}
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
