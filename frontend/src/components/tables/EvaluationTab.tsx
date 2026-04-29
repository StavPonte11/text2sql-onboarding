import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Play, BarChart2 } from "lucide-react";
import { App } from "antd";
import { evalApi } from "../../api/client";
import { SkeletonTable } from "../common/Skeleton";
import { ErrorState } from "../common/ErrorState";
import dayjs from "dayjs";

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
  });

  if (isLoading) return <SkeletonTable rows={3} cols={4} />;
  if (isError) return <ErrorState onRetry={refetch} />;

  return (
    <div>
      <div className="flex items-center" style={{ justifyContent: "space-between", marginBottom: 20 }}>
        <h2 style={{ fontSize: 17, fontWeight: 700 }}>{t("evaluations.title")}</h2>
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
        <div className="card" style={{ marginBottom: 16, display: "flex", alignItems: "center", gap: 20 }}>
          <ScoreRing score={triggerMutation.data.score} />
          <div>
            <div style={{ fontWeight: 600, fontSize: 15 }}>Latest Run</div>
            <div style={{ color: "var(--text-muted)", fontSize: 12.5 }}>
              ID: {triggerMutation.data.id} · Status: {triggerMutation.data.status}
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
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Run ID</th>
                <th>{t("evaluations.score")}</th>
                <th>{t("evaluations.status")}</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {[...(triggerMutation.data ? [triggerMutation.data] : []), ...(runs ?? [])].map((run) => (
                <tr key={run.id}>
                  <td><code style={{ fontSize: 11, color: "var(--text-muted)" }}>{run.id}</code></td>
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
