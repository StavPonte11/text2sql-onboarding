import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Play, BarChart2 } from "lucide-react";
import { App } from "antd";
import { evalApi } from "../../api/client";
import { SkeletonTable } from "../common/Skeleton";
import { ErrorState } from "../common/ErrorState";
import dayjs from "dayjs";
import "./EvaluationTab.css";

interface Props { tableId: string }

function ScoreRing({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const cls = pct >= 70 ? "score-ring--high" : pct >= 40 ? "score-ring--mid" : "score-ring--low";
  return <div className={`score-ring ${cls}`}>{pct}%</div>;
}

export function EvaluationTab({ tableId }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { message } = App.useApp();

  const { data: runs, isLoading, isError, refetch } = useQuery({
    queryKey: ["eval-runs", tableId],
    queryFn: () => evalApi.listRuns(tableId),
  });

  const triggerMutation = useMutation({
    mutationFn: () => evalApi.triggerRun(tableId),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["eval-runs", tableId] });
      qc.setQueryData(["eval-runs", tableId], (old: any[]) => [run, ...(old ?? [])]);
      message.success("Evaluation run triggered");
    },
    onError: () => {
      message.error("Evaluation failed. Please go change the descriptions in Oasis platform.");
    }
  });

  if (isLoading) return <SkeletonTable rows={3} cols={4} />;
  if (isError) return <ErrorState onRetry={refetch} />;

  return (
    <div>
      <div className="evaluation-tab__header">
        <h2 className="evaluation-tab__title">{t("evaluations.title")}</h2>
        <button
          className="btn btn--primary btn--sm"
          onClick={() => triggerMutation.mutate()}
          disabled={triggerMutation.isPending}
        >
          <Play size={14} />
          {triggerMutation.isPending ? "Running..." : t("evaluations.runEval")}
        </button>
      </div>

      {triggerMutation.data && (
        <div className="card latest-run-card">
          <ScoreRing score={triggerMutation.data.score} />
          <div>
            <div className="latest-run-card__title">Latest Run: {triggerMutation.data.table_name || tableId.slice(0,8)}</div>
            <div className="latest-run-card__info">
              Run ID: {triggerMutation.data.id.slice(0,8)}… · Status: {triggerMutation.data.status}
            </div>
          </div>
        </div>
      )}

      {(!runs || runs.length === 0) && !triggerMutation.data ? (
        <div className="card">
          <div className="empty-state">
            <BarChart2 size={36} className="empty-state__icon" />
            <div className="empty-state__text">{t("evaluations.noData")}</div>
            <div className="empty-state__sub">Run an evaluation to see scores and results</div>
          </div>
        </div>
      ) : (
        <div className="card evaluations-table-card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Run ID</th>
                <th>{t("evaluations.score")}</th>
                <th>{t("evaluations.status")}</th>
                <th>Type</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {[...(triggerMutation.data ? [triggerMutation.data] : []), ...(runs ?? [])].map((run) => (
                <tr key={run.id}>
                  <td><code className="run-id-code">{run.id.slice(0,8)}…</code></td>
                  <td><ScoreRing score={run.score} /></td>
                  <td>
                    <span className="run-status-badge" style={{
                      color: run.status === "completed" ? "var(--status-production)"
                        : run.status === "failed" ? "var(--status-degraded)"
                        : "var(--status-sandbox)"
                    }}>
                      {run.status}
                    </span>
                  </td>
                  <td>
                    <span className="run-type-label">
                      {run.triggered_by}
                    </span>
                  </td>
                  <td className="run-date-label">
                    {dayjs(run.created_at).format("MMM D, HH:mm")}
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
