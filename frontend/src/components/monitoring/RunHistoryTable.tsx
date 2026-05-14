import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, ExternalLink, RefreshCw, AlertTriangle, Check, X } from "lucide-react";
import dayjs from "dayjs";
import { Link } from "react-router-dom";
import { orchestrationApi, type EvalRunFull } from "../../api/orchestration";
import { StatusBadge, ScoreBar, Spinner, EmptySlate } from "../common/EvalUI";
import "./RunHistoryTable.css";

// ── Run detail drawer ──────────────────────────────────────────────────────────
function RunDetailDrawer({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: report, isLoading } = useQuery({
    queryKey: ["run-report", runId],
    queryFn: () => orchestrationApi.getRunReport(runId),
    enabled: !!runId,
  });

  return (
    <div className="run-detail-overlay" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      {/* backdrop */}
      <div className="run-detail-backdrop" onClick={onClose} />

      {/* panel */}
      <div className="run-detail-panel">
        <div className="run-detail-header">
          <div className="run-detail-title">Run Details</div>
          <button className="btn btn--ghost btn--sm" onClick={onClose}>✕ Close</button>
        </div>

        {isLoading && <div className="run-detail-loading"><Spinner size={28} /></div>}

        {report && (
          <div className="run-detail-content">
            {/* Summary */}
            <div className="card summary-grid">
              {[
                { label: "Score", value: `${Math.round(report.overall_score * 100)}%`, color: report.overall_score >= 0.9 ? "#10b981" : report.overall_score >= 0.8 ? "#f59e0b" : "#ef4444" },
                { label: "Pass Rate", value: `${Math.round(report.pass_rate * 100)}%`, color: "#6366f1" },
                { label: "Questions", value: report.total_questions, color: "var(--text-primary)" },
                { label: "Duration", value: report.duration_seconds ? `${report.duration_seconds}s` : "—", color: "var(--text-secondary)" },
              ].map(({ label, value, color }) => (
                <div key={label}>
                  <div className="summary-metric-label">{label}</div>
                  <div className="summary-metric-value" style={{ color }}>{value}</div>
                </div>
              ))}
            </div>

            {/* Publishable status */}
            <div className={`publishable-status ${report.is_publishable ? "publishable-status--ready" : "publishable-status--not-ready"}`}>
              <span>{report.is_publishable ? <Check size={14} /> : <X size={14} />}</span>
              {report.is_publishable ? "Ready to publish (score ≥ 80%)" : "Not publishable — score below 80%"}
            </div>

            {/* Regression */}
            {report.regression_detected && (
              <div className="regression-alert">
                <AlertTriangle size={15} style={{ color: "#ef4444", flexShrink: 0 }} />
                <span className="regression-alert__text">
                  Regression detected: score dropped {Math.abs(report.regression_delta * 100).toFixed(1)}%
                </span>
              </div>
            )}

            {/* Failure breakdown */}
            {report.failure_breakdown && Object.keys(report.failure_breakdown).length > 0 && (
              <div className="card">
                <div className="failure-breakdown-title">Failure Breakdown</div>
                {Object.entries(report.failure_breakdown as Record<string, number>)
                  .filter(([, v]) => v > 0)
                  .map(([key, count]) => (
                    <div key={key} className="failure-item">
                      <span className="failure-item__label">{key.replace(/_/g, " ")}</span>
                      <span className="failure-item__count">{count}</span>
                    </div>
                  ))}
              </div>
            )}

            {/* Dimension averages */}
            {report.dimension_averages && (
              <div className="card">
                <div className="dimension-scores-title">Dimension Scores</div>
                {Object.entries(report.dimension_averages as Record<string, number>).map(([dim, val]) => (
                  <div key={dim} className="dimension-item">
                    <div className="dimension-item__header">
                      <span className="dimension-item__label">{dim.replace(/_/g, " ")}</span>
                      <span className="dimension-item__score" style={{ color: val >= 0.9 ? "#10b981" : val >= 0.7 ? "#f59e0b" : "#ef4444" }}>
                        {Math.round(val * 100)}%
                      </span>
                    </div>
                    <ScoreBar score={val} height={5} />
                  </div>
                ))}
              </div>
            )}

            {/* Per-question results */}
            {report.per_question?.length > 0 && (
              <div className="card question-results-card">
                <div className="question-results-header">
                  Question Results ({report.per_question.length})
                </div>
                <div className="question-results-list">
                  {report.per_question.map((q: { question_id: string; score: number; status: string; failure_type: string | null }) => (
                    <div key={q.question_id} className="question-item">
                      <div className="question-item__dot" style={{ background: q.status === "pass" ? "#10b981" : "#ef4444" }} />
                      <div className="question-item__id">
                        {q.question_id.slice(0, 16)}…
                      </div>
                      <div className="question-item__score" style={{ color: q.score >= 0.85 ? "#10b981" : q.score >= 0.6 ? "#f59e0b" : "#ef4444" }}>
                        {Math.round(q.score * 100)}%
                      </div>
                      {q.failure_type && (
                        <span className="question-item__failure-type">
                          {q.failure_type}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── RunHistoryTable ────────────────────────────────────────────────────────────
interface RunHistoryTableProps {
  tableId?: string;
  limit?: number;
  compact?: boolean;
}

export function RunHistoryTable({ tableId, limit = 50, compact = false }: RunHistoryTableProps) {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const pageSize = compact ? 5 : 10;

  const { data: runs = [], isLoading, refetch, isFetching } = useQuery({
    queryKey: ["eval-runs", tableId, limit],
    queryFn: () => orchestrationApi.listRuns({ limit, table_id: tableId }),
    refetchInterval: 15_000,
  });

  const paged = runs.slice(page * pageSize, (page + 1) * pageSize);
  const totalPages = Math.ceil(runs.length / pageSize);

  if (isLoading) return (
    <div className="run-history-loading"><Spinner size={24} /></div>
  );

  if (!runs.length) return (
    <EmptySlate
      icon={<RefreshCw size={36} />}
      title="No evaluation runs yet"
      sub="Trigger a run to start seeing execution history here"
    />
  );

  return (
    <div>
      <div className="run-history-controls">
        <button className="btn btn--ghost btn--sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw size={12} style={{ animation: isFetching ? "spin 0.8s linear infinite" : "none" }} />
          {isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      <div className="card run-history-table-card">
        <table className="data-table">
          <thead>
            <tr>
              {!compact && <th>Run ID</th>}
              {!tableId && <th>Table</th>}
              <th>Score</th>
              <th>Pass Rate</th>
              {!compact && <th>Questions</th>}
              <th>Status</th>
              <th>Triggered By</th>
              <th>Date</th>
              <th style={{ width: 36 }} />
            </tr>
          </thead>
          <tbody>
            {paged.map(run => (
              <tr
                key={run.id}
                onClick={() => setSelectedRunId(run.id)}
                className="run-history-row"
              >
                {!compact && (
                  <td>
                    <code className="run-id-text">
                      {run.id.slice(0, 8)}…
                    </code>
                  </td>
                )}
                {!tableId && (
                  <td className="table-link-cell">
                    <Link 
                      to={`/tables/${run.table_id}`} 
                      onClick={e => e.stopPropagation()}
                      className="table-link hover:underline"
                    >
                      {run.table_name || run.table_id.slice(0, 8)}
                    </Link>
                  </td>
                )}
                <td>
                  <div className="score-cell-content">
                    <span className="score-text" style={{ color: run.score >= 0.9 ? "#10b981" : run.score >= 0.8 ? "#f59e0b" : "#ef4444" }}>
                      {Math.round(run.score * 100)}%
                    </span>
                    {run.regression_detected && (
                      <span title="Regression detected"><AlertTriangle size={12} style={{ color: "#ef4444" }} /></span>
                    )}
                  </div>
                </td>
                <td className="pass-rate-cell">
                  <ScoreBar score={run.pass_rate} height={5} showLabel />
                </td>
                {!compact && <td className="questions-count-cell">{run.total_questions}</td>}
                <td><StatusBadge status={run.status} size="sm" /></td>
                <td>
                  <span className={`triggered-by-text${run.triggered_by === "scheduler" ? " triggered-by-text--scheduler" : ""}`}>
                    {run.triggered_by}
                  </span>
                </td>
                <td className="date-cell">
                  {dayjs(run.created_at).format("MMM D, HH:mm")}
                </td>
                <td>
                  <ChevronRight size={14} style={{ color: "var(--text-muted)" }} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="run-history-pagination">
          <span className="pagination-info">
            Showing {page * pageSize + 1}–{Math.min((page + 1) * pageSize, runs.length)} of {runs.length} runs
          </span>
          <div className="pagination-btns">
            <button className="btn btn--ghost btn--sm" disabled={page === 0} onClick={() => setPage(p => p - 1)}>← Prev</button>
            <button className="btn btn--ghost btn--sm" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>Next →</button>
          </div>
        </div>
      )}

      {selectedRunId && (
        <RunDetailDrawer runId={selectedRunId} onClose={() => setSelectedRunId(null)} />
      )}
    </div>
  );
}
