import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, ExternalLink, RefreshCw, AlertTriangle, Check, X } from "lucide-react";
import dayjs from "dayjs";
import { orchestrationApi, type EvalRunFull } from "../../api/orchestration";
import { StatusBadge, ScoreBar, Spinner, EmptySlate } from "../common/EvalUI";

// ── Run detail drawer ──────────────────────────────────────────────────────────
function RunDetailDrawer({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: report, isLoading } = useQuery({
    queryKey: ["run-report", runId],
    queryFn: () => orchestrationApi.getRunReport(runId),
    enabled: !!runId,
  });

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 500,
        display: "flex", justifyContent: "flex-end",
      }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      {/* backdrop */}
      <div style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.55)", backdropFilter: "blur(4px)" }} onClick={onClose} />

      {/* panel */}
      <div style={{
        position: "relative", width: 520, height: "100%",
        background: "var(--bg-elevated)", borderLeft: "1px solid var(--border)",
        overflowY: "auto", padding: 24,
        animation: "slideInRight 0.25s ease",
      }}>
        <style>{`@keyframes slideInRight { from { transform: translateX(40px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }`}</style>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>Run Details</div>
          <button className="btn btn--ghost btn--sm" onClick={onClose}>✕ Close</button>
        </div>

        {isLoading && <div style={{ display: "flex", justifyContent: "center", padding: 40 }}><Spinner size={28} /></div>}

        {report && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {/* Summary */}
            <div className="card" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              {[
                { label: "Score", value: `${Math.round(report.overall_score * 100)}%`, color: report.overall_score >= 0.9 ? "#10b981" : report.overall_score >= 0.8 ? "#f59e0b" : "#ef4444" },
                { label: "Pass Rate", value: `${Math.round(report.pass_rate * 100)}%`, color: "#6366f1" },
                { label: "Questions", value: report.total_questions, color: "var(--text-primary)" },
                { label: "Duration", value: report.duration_seconds ? `${report.duration_seconds}s` : "—", color: "var(--text-secondary)" },
              ].map(({ label, value, color }) => (
                <div key={label}>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 3 }}>{label}</div>
                  <div style={{ fontSize: 22, fontWeight: 800, color }}>{value}</div>
                </div>
              ))}
            </div>

            {/* Publishable status */}
            <div style={{
              padding: "10px 14px", borderRadius: 8,
              background: report.is_publishable ? "rgba(16,185,129,0.08)" : "rgba(239,68,68,0.08)",
              border: `1px solid ${report.is_publishable ? "rgba(16,185,129,0.25)" : "rgba(239,68,68,0.25)"}`,
              display: "flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 600,
              color: report.is_publishable ? "#10b981" : "#ef4444",
            }}>
              <span>{report.is_publishable ? <Check size={14} /> : <X size={14} />}</span>
              {report.is_publishable ? "Ready to publish (score ≥ 80%)" : "Not publishable — score below 80%"}
            </div>

            {/* Regression */}
            {report.regression_detected && (
              <div style={{
                padding: "10px 14px", borderRadius: 8,
                background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.25)",
                display: "flex", alignItems: "center", gap: 8,
              }}>
                <AlertTriangle size={15} style={{ color: "#ef4444", flexShrink: 0 }} />
                <span style={{ fontSize: 13, color: "#ef4444", fontWeight: 500 }}>
                  Regression detected: score dropped {Math.abs(report.regression_delta * 100).toFixed(1)}%
                </span>
              </div>
            )}

            {/* Failure breakdown */}
            {report.failure_breakdown && Object.keys(report.failure_breakdown).length > 0 && (
              <div className="card">
                <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 12, color: "var(--text-primary)" }}>Failure Breakdown</div>
                {Object.entries(report.failure_breakdown as Record<string, number>)
                  .filter(([, v]) => v > 0)
                  .map(([key, count]) => (
                    <div key={key} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                      <span style={{ fontSize: 13, color: "var(--text-secondary)", textTransform: "capitalize" }}>{key.replace(/_/g, " ")}</span>
                      <span style={{ fontSize: 13, fontWeight: 700, color: "#ef4444" }}>{count}</span>
                    </div>
                  ))}
              </div>
            )}

            {/* Dimension averages */}
            {report.dimension_averages && (
              <div className="card">
                <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 12, color: "var(--text-primary)" }}>Dimension Scores</div>
                {Object.entries(report.dimension_averages as Record<string, number>).map(([dim, val]) => (
                  <div key={dim} style={{ marginBottom: 10 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                      <span style={{ fontSize: 12, color: "var(--text-secondary)", textTransform: "capitalize" }}>{dim.replace(/_/g, " ")}</span>
                      <span style={{ fontSize: 12, fontWeight: 700, color: val >= 0.9 ? "#10b981" : val >= 0.7 ? "#f59e0b" : "#ef4444" }}>{Math.round(val * 100)}%</span>
                    </div>
                    <ScoreBar score={val} height={5} />
                  </div>
                ))}
              </div>
            )}

            {/* Per-question results */}
            {report.per_question?.length > 0 && (
              <div className="card" style={{ padding: 0, overflow: "hidden" }}>
                <div style={{ fontSize: 13, fontWeight: 700, padding: "12px 16px", borderBottom: "1px solid var(--border)" }}>
                  Question Results ({report.per_question.length})
                </div>
                <div style={{ maxHeight: 300, overflowY: "auto" }}>
                  {report.per_question.map((q: { question_id: string; score: number; status: string; failure_type: string | null }) => (
                    <div key={q.question_id} style={{
                      display: "flex", alignItems: "center", gap: 10, padding: "8px 16px",
                      borderBottom: "1px solid var(--border-subtle)",
                    }}>
                      <div style={{
                        width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                        background: q.status === "pass" ? "#10b981" : "#ef4444",
                      }} />
                      <div style={{ flex: 1, fontSize: 11.5, color: "var(--text-muted)", fontFamily: "monospace" }}>
                        {q.question_id.slice(0, 16)}…
                      </div>
                      <div style={{ fontSize: 12, fontWeight: 700, color: q.score >= 0.85 ? "#10b981" : q.score >= 0.6 ? "#f59e0b" : "#ef4444" }}>
                        {Math.round(q.score * 100)}%
                      </div>
                      {q.failure_type && (
                        <span style={{ fontSize: 10.5, color: "#ef4444", background: "rgba(239,68,68,0.1)", padding: "2px 6px", borderRadius: 4 }}>
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
    <div style={{ display: "flex", justifyContent: "center", padding: 32 }}><Spinner size={24} /></div>
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
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
        <button className="btn btn--ghost btn--sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw size={12} style={{ animation: isFetching ? "spin 0.8s linear infinite" : "none" }} />
          {isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <table className="data-table">
          <thead>
            <tr>
              {!compact && <th>Run ID</th>}
              {!tableId && <th>Table</th>}
              <th>Score</th>
              <th>Pass Rate</th>
              {!compact && <th>Questions</th>}
              {!compact && <th>Duration</th>}
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
                style={{ cursor: "pointer" }}
              >
                {!compact && (
                  <td>
                    <code style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
                      {run.id.slice(0, 8)}…
                    </code>
                  </td>
                )}
                {!tableId && (
                  <td style={{ fontSize: 12.5, color: "var(--text-secondary)", fontWeight: 600 }}>
                    {run.table_name || run.table_id.slice(0, 8)}
                  </td>
                )}
                <td>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{
                      fontSize: 14, fontWeight: 800,
                      color: run.score >= 0.9 ? "#10b981" : run.score >= 0.8 ? "#f59e0b" : "#ef4444",
                    }}>
                      {Math.round(run.score * 100)}%
                    </span>
                    {run.regression_detected && (
                      <span title="Regression detected"><AlertTriangle size={12} style={{ color: "#ef4444" }} /></span>
                    )}
                  </div>
                </td>
                <td style={{ width: 120 }}>
                  <ScoreBar score={run.pass_rate} height={5} showLabel />
                </td>
                {!compact && <td style={{ fontSize: 13 }}>{run.total_questions}</td>}
                {!compact && (
                  <td style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
                    {run.duration_seconds ? `${run.duration_seconds}s` : "—"}
                  </td>
                )}
                <td><StatusBadge status={run.status} size="sm" /></td>
                <td>
                  <span style={{
                    fontSize: 11.5, color: run.triggered_by === "scheduler" ? "var(--accent-hover)" : "var(--text-secondary)",
                    fontWeight: run.triggered_by === "scheduler" ? 600 : 400,
                  }}>
                    {run.triggered_by}
                  </span>
                </td>
                <td style={{ fontSize: 12, color: "var(--text-muted)", whiteSpace: "nowrap" }}>
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
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 12 }}>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Showing {page * pageSize + 1}–{Math.min((page + 1) * pageSize, runs.length)} of {runs.length} runs
          </span>
          <div style={{ display: "flex", gap: 6 }}>
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
