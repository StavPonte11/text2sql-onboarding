import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FlaskConical } from "lucide-react";
import { Link } from "react-router-dom";
import dayjs from "dayjs";
import { evalApi } from "../api/client";
import { SkeletonTable } from "../components/common/Skeleton";
import { ErrorState } from "../components/common/ErrorState";

function ScoreRing({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const cls = pct >= 70 ? "score-ring--high" : pct >= 40 ? "score-ring--mid" : "score-ring--low";
  return <div className={`score-ring ${cls}`}>{pct}%</div>;
}

export function SandboxPage() {
  const { t } = useTranslation();

  const { data: runs, isLoading, isError, refetch } = useQuery({
    queryKey: ["eval-runs-all"],
    queryFn: () => evalApi.listAllRuns(),
  });

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">{t("nav.sandbox")}</h1>
          <p className="page__subtitle">Platform-wide sandbox evaluations</p>
        </div>
      </div>
      
      {isLoading ? (
        <div className="card"><SkeletonTable rows={5} cols={5} /></div>
      ) : isError ? (
        <div className="card"><ErrorState onRetry={refetch} /></div>
      ) : !runs || runs.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <FlaskConical size={40} className="empty-state__icon" />
            <div className="empty-state__text">No sandbox evaluations yet</div>
            <div className="empty-state__sub">Navigate to Tables → Table Details → Evaluations tab to trigger a run</div>
          </div>
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Run ID</th>
                <th>Table</th>
                <th>Score</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td><code style={{ fontSize: 11, color: "var(--text-muted)" }}>{run.id.slice(0,8)}…</code></td>
                  <td>
                    <Link to={`/tables/${run.table_id}/overview`} style={{ fontSize: 12, color: "var(--accent-hover)", fontWeight: 600, textDecoration: "none" }}>
                      {run.table_name || run.table_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td><ScoreRing score={run.score} /></td>
                  <td>
                    <span style={{
                      color: run.status === "completed" ? "var(--status-production)"
                        : run.status === "failed" ? "var(--status-degraded)"
                        : "var(--status-sandbox)",
                      fontWeight: 600, fontSize: 12,
                    }}>
                      {run.status}
                    </span>
                  </td>
                  <td style={{ fontSize: 12, color: "var(--text-muted)" }}>
                    {dayjs(run.created_at).format("MMM D, YYYY HH:mm")}
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
