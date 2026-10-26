import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { AlertTriangle, ArrowRight, Check, ChevronRight, RefreshCw, Square, X } from 'lucide-react';

import { orchestrationApi } from '../../api/orchestration';
import { useStopEvalRun } from '../../hooks/useEvaluations';
import { EmptySlate, ScoreBar, Spinner, StatusBadge } from '../common/EvalUI';

import './RunHistoryTable.css';

// Runs saved before the rename store these two scores under their old names
// (same meaning: 1 = good)
const RENAMED_DIMENSIONS: Record<string, string> = {
  schema_hallucination: 'schema_validity',
  dialect_error: 'dialect_validity',
};

const dimensionLabel = (dim: string) => (RENAMED_DIMENSIONS[dim] ?? dim).replace(/_/g, ' ');

/** Pass rate over the questions finished so far, e.g. "40% so far" */
function livePassRate(perQuestion: { status: string }[] | undefined): string {
  const finished = (perQuestion ?? []).filter((q) => q.status !== 'pending');
  if (!finished.length) return 'Calculating…';
  const passed = finished.filter((q) => q.status === 'pass').length;
  return `${Math.round((passed / finished.length) * 100)}% so far`;
}

/** "Evaluating 36 of 58 questions" with a bar, shown while a run is in progress */
function RunProgress({ completed, total }: { completed: number; total: number }) {
  const pct = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  return (
    <div className="card" style={{ padding: '14px 18px' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 13,
          fontWeight: 600,
          marginBottom: 8,
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: '#f59e0b' }}>
          <Spinner size={13} color="#f59e0b" />
          Evaluating {completed} of {total} questions
        </span>
        <span style={{ color: 'var(--text-secondary)' }}>{pct}%</span>
      </div>
      <div
        role="progressbar"
        aria-valuenow={completed}
        aria-valuemin={0}
        aria-valuemax={total}
        style={{ height: 6, borderRadius: 3, background: 'var(--border)', overflow: 'hidden' }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: '100%',
            background: '#f59e0b',
            transition: 'width 0.4s ease',
          }}
        />
      </div>
    </div>
  );
}

// ── Run detail drawer ──────────────────────────────────────────────────────────
function RunDetailDrawer({ runId, onClose }: { runId: string; onClose: () => void }) {
  const stopMut = useStopEvalRun();
  const { data: report, isLoading } = useQuery({
    queryKey: ['run-report', runId],
    queryFn: () => orchestrationApi.getRunReport(runId),
    enabled: !!runId,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 4000 : false),
  });

  const isRegressionRun = report?.triggered_by === 'promotion-regression';

  const { data: regressionDiff } = useQuery({
    queryKey: ['regression-diff', runId],
    queryFn: () => orchestrationApi.getRegressionDiff(runId),
    enabled: isRegressionRun,
  });

  return (
    <div
      className="run-detail-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {/* backdrop */}
      <div className="run-detail-backdrop" onClick={onClose} />

      {/* panel */}
      <div className="run-detail-panel">
        <div className="run-detail-header">
          <div className="run-detail-title">Run Details</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {report?.status === 'running' && (
              <button
                className="btn btn--danger btn--sm"
                disabled={stopMut.isPending}
                onClick={() => {
                  if (window.confirm('Are you sure you want to stop this evaluation run?')) {
                    stopMut.mutate(runId);
                  }
                }}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  background: '#ef4444',
                  color: '#fff',
                  border: '1px solid #dc2626',
                  borderRadius: 6,
                  padding: '4px 10px',
                  fontWeight: 600,
                  fontSize: 12,
                  cursor: 'pointer',
                }}
              >
                <Square size={12} fill="currentColor" />
                {stopMut.isPending ? 'Stopping…' : 'Stop Evaluation'}
              </button>
            )}
            <button className="btn btn--ghost btn--sm" onClick={onClose}>
              ✕ Close
            </button>
          </div>
        </div>

        {isLoading && (
          <div className="run-detail-loading">
            <Spinner size={28} />
          </div>
        )}

        {report && (
          <div className="run-detail-content">
            {report.status === 'running' && (
              <RunProgress
                completed={report.completed_questions ?? 0}
                total={report.total_questions}
              />
            )}

            {/* Summary */}
            <div className="card summary-grid">
              {[
                {
                  label: 'Score',
                  value:
                    report.status === 'running'
                      ? 'Evaluating…'
                      : `${Math.round(report.overall_score * 100)}%`,
                  color:
                    report.status === 'running'
                      ? '#f59e0b'
                      : report.overall_score >= 0.5
                        ? '#10b981'
                        : '#ef4444',
                },
                {
                  label: 'Pass Rate',
                  value:
                    report.status === 'running'
                      ? livePassRate(report.per_question)
                      : `${Math.round(report.pass_rate * 100)}%`,
                  color:
                    report.status === 'running'
                      ? '#f59e0b'
                      : report.pass_rate >= 0.5
                        ? '#10b981'
                        : '#ef4444',
                },
                {
                  label: 'Questions',
                  value:
                    report.status === 'running'
                      ? `${report.completed_questions ?? 0}/${report.total_questions}`
                      : report.total_questions,
                  color: 'var(--text-primary)',
                },
                {
                  label: 'Duration',
                  value: report.duration_seconds ? `${report.duration_seconds}s` : '—',
                  color: 'var(--text-secondary)',
                },
              ].map(({ label, value, color }) => (
                <div key={label}>
                  <div className="summary-metric-label">{label}</div>
                  <div className="summary-metric-value" style={{ color }}>
                    {value}
                  </div>
                </div>
              ))}
            </div>

            {/* Publishable status */}
            <div
              className={`publishable-status ${
                report.status === 'running'
                  ? 'publishable-status--running'
                  : report.is_publishable
                    ? 'publishable-status--ready'
                    : 'publishable-status--not-ready'
              }`}
            >
              <span>
                {report.status === 'running' ? (
                  <Spinner size={14} color="#f59e0b" />
                ) : report.is_publishable ? (
                  <Check size={14} />
                ) : (
                  <X size={14} />
                )}
              </span>
              {report.status === 'running'
                ? 'Evaluation running — publishability pending evaluation completion'
                : report.status === 'failed'
                  ? 'Not publishable — evaluation run failed'
                  : report.is_publishable
                    ? 'Ready to publish (score ≥ 50%)'
                    : 'Not publishable — score below 50%'}
            </div>

            {/* Regression */}
            {report.regression_detected && (
              <div className="regression-alert">
                <AlertTriangle size={15} style={{ color: '#ef4444', flexShrink: 0 }} />
                <span className="regression-alert__text">
                  Regression detected: score dropped{' '}
                  {Math.abs(report.regression_delta * 100).toFixed(1)}%
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
                      <span className="failure-item__label">{key.replace(/_/g, ' ')}</span>
                      <span className="failure-item__count">{count}</span>
                    </div>
                  ))}
              </div>
            )}

            {/* Dimension averages */}
            {report.dimension_averages && (
              <div className="card">
                <div className="dimension-scores-title">Dimension Scores</div>
                {Object.entries(report.dimension_averages as Record<string, number>).map(
                  ([dim, val]) => (
                    <div key={dim} className="dimension-item">
                      <div className="dimension-item__header">
                        <span className="dimension-item__label">{dimensionLabel(dim)}</span>
                        <span
                          className="dimension-item__score"
                          style={{ color: val >= 0.5 ? '#10b981' : '#ef4444' }}
                        >
                          {Math.round(val * 100)}%
                        </span>
                      </div>
                      <ScoreBar score={val} height={5} />
                    </div>
                  ),
                )}
              </div>
            )}

            {/* Per-question results */}
            {report.per_question?.length > 0 && (
              <div className="card question-results-card">
                <div className="question-results-header">
                  Question Results ({report.per_question.length})
                </div>
                <div className="question-results-list">
                  {report.per_question.map(
                    (q: {
                      question_id: string;
                      question: string;
                      score: number;
                      status: string;
                      failure_type: string | null;
                    }) => (
                      <div key={q.question_id} className="question-item">
                        <div
                          className="question-item__dot"
                          style={{
                            background:
                              q.status === 'pending'
                                ? '#f59e0b'
                                : q.status === 'pass'
                                  ? '#10b981'
                                  : '#ef4444',
                            opacity: q.status === 'pending' ? 0.7 : 1,
                            animation:
                              q.status === 'pending' ? 'pulse 1.4s ease-in-out infinite' : 'none',
                          }}
                        />
                        <div className="question-item__id" title={q.question ?? q.question_id}>
                          {(q.question ?? q.question_id).slice(0, 16)}…
                        </div>
                        <div
                          className="question-item__score"
                          style={{
                            color:
                              q.status === 'pending'
                                ? '#f59e0b'
                                : q.score >= 0.5
                                  ? '#10b981'
                                  : '#ef4444',
                          }}
                        >
                          {q.status === 'pending' ? '—' : q.score >= 0.5 ? '100%' : '0%'}
                        </div>
                        {q.status === 'pending' && (
                          <span
                            className="question-item__failure-type"
                            style={{ color: '#f59e0b' }}
                          >
                            evaluating…
                          </span>
                        )}
                        {q.status !== 'pending' && q.failure_type && (
                          <span className="question-item__failure-type">{q.failure_type}</span>
                        )}
                      </div>
                    ),
                  )}
                </div>
              </div>
            )}

            {/* Regression diff — only for promotion-regression runs */}
            {isRegressionRun && (
              <div className="card" style={{ border: '1px solid rgba(239,68,68,0.25)' }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    marginBottom: 14,
                  }}
                >
                  <AlertTriangle size={14} style={{ color: '#ef4444', flexShrink: 0 }} />
                  <span style={{ fontWeight: 700, fontSize: 13, color: 'var(--text-primary)' }}>
                    Regressed Questions
                  </span>
                  {regressionDiff && (
                    <span
                      style={{
                        marginLeft: 'auto',
                        fontSize: 11,
                        fontWeight: 700,
                        padding: '2px 8px',
                        borderRadius: 20,
                        background:
                          regressionDiff.total_regressions > 0
                            ? 'rgba(239,68,68,0.12)'
                            : 'rgba(16,185,129,0.12)',
                        color: regressionDiff.total_regressions > 0 ? '#ef4444' : '#10b981',
                      }}
                    >
                      {regressionDiff.total_regressions} regression
                      {regressionDiff.total_regressions !== 1 ? 's' : ''}
                    </span>
                  )}
                </div>

                <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 10 }}>
                  Questions that passed the production baseline but failed after adding the
                  candidate table.
                </div>

                {!regressionDiff ? (
                  <div style={{ display: 'flex', justifyContent: 'center', padding: 12 }}>
                    <Spinner size={18} />
                  </div>
                ) : regressionDiff.total_regressions === 0 ? (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      padding: '10px 12px',
                      background: 'rgba(16,185,129,0.06)',
                      borderRadius: 8,
                      fontSize: 12.5,
                      color: '#10b981',
                      fontWeight: 600,
                    }}
                  >
                    <Check size={14} />
                    No regressions detected — all baseline questions still pass
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {regressionDiff.regressions.map((item) => (
                      <div
                        key={item.question_id}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 10,
                          padding: '8px 10px',
                          borderRadius: 8,
                          background: 'rgba(239,68,68,0.05)',
                          border: '1px solid rgba(239,68,68,0.15)',
                        }}
                      >
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div
                            style={{
                              fontSize: 12.5,
                              color: 'var(--text-primary)',
                              fontWeight: 500,
                              whiteSpace: 'nowrap',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                            }}
                            title={item.question}
                          >
                            {item.question}
                          </div>
                        </div>
                        <div
                          style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}
                        >
                          <span style={{ fontSize: 12, fontWeight: 700, color: '#10b981' }}>
                            {item.baseline_score >= 0.5 ? 100 : 0}
                          </span>
                          <ArrowRight size={11} style={{ color: 'var(--text-muted)' }} />
                          <span style={{ fontSize: 12, fontWeight: 700, color: '#ef4444' }}>
                            {item.regression_score >= 0.5 ? 100 : 0}
                          </span>
                          <span
                            style={{
                              fontSize: 11,
                              fontWeight: 800,
                              padding: '1px 6px',
                              borderRadius: 6,
                              background: 'rgba(239,68,68,0.15)',
                              color: '#ef4444',
                            }}
                          >
                            −{item.score_drop >= 0.5 ? 100 : 0}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
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
  connectionId?: number;
  limit?: number;
  compact?: boolean;
  excludeTableIds?: Set<string>;
}

export function RunHistoryTable({
  tableId,
  connectionId,
  limit = 50,
  compact = false,
  excludeTableIds,
}: RunHistoryTableProps) {
  const stopMut = useStopEvalRun();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const pageSize = compact ? 5 : 10;

  const {
    data: runs = [],
    isLoading,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ['eval-runs', tableId, connectionId, limit],
    queryFn: () =>
      orchestrationApi.listRuns({ limit, table_id: tableId, connection_id: connectionId }),
    refetchInterval: (query) => {
      const data = query.state.data as typeof runs;
      const hasRunning = data?.some((r) => r.status === 'running');
      return hasRunning ? 5_000 : 15_000;
    },
  });

  const visibleRuns = excludeTableIds?.size
    ? runs.filter((r) => !r.table_id || !excludeTableIds.has(r.table_id))
    : runs;

  const paged = visibleRuns.slice(page * pageSize, (page + 1) * pageSize);
  const totalPages = Math.ceil(visibleRuns.length / pageSize);

  if (isLoading)
    return (
      <div className="run-history-loading">
        <Spinner size={24} />
      </div>
    );

  if (!runs.length)
    return (
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
          <RefreshCw
            size={12}
            style={{ animation: isFetching ? 'spin 0.8s linear infinite' : 'none' }}
          />
          {isFetching ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      <div className="card run-history-table-card">
        <table className="data-table">
          <thead>
            <tr>
              {!compact && <th>Run ID</th>}
              {!tableId && <th>Target / Connection</th>}
              <th>Score</th>
              <th>Pass Rate</th>
              {!compact && <th>Questions</th>}
              <th>Status</th>
              <th>Triggered By</th>
              {!compact && <th>Catalog</th>}
              <th>Date</th>
              <th style={{ width: 36 }} />
            </tr>
          </thead>
          <tbody>
            {paged.map((run) => (
              <tr key={run.id} onClick={() => setSelectedRunId(run.id)} className="run-history-row">
                {!compact && (
                  <td>
                    <code className="run-id-text" title={run.id}>
                      {run.id.slice(0, 8)}…
                    </code>
                  </td>
                )}
                {!tableId && (
                  <td className="table-link-cell">
                    {run.connection_id ? (
                      <span
                        className="table-link run-table-name"
                        style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}
                        title={run.connection_name || run.table_name}
                      >
                        <span
                          style={{
                            fontSize: '11px',
                            padding: '2px 6px',
                            background: 'rgba(59, 130, 246, 0.1)',
                            color: '#2563eb',
                            borderRadius: '4px',
                            fontWeight: 600,
                          }}
                        >
                          Connection
                        </span>
                        {run.connection_name || run.table_name || `ID: ${run.connection_id}`}
                      </span>
                    ) : run.table_id ? (
                      <Link
                        to={`/tables/${run.table_id}`}
                        onClick={(e) => e.stopPropagation()}
                        className="table-link hover:underline run-table-name"
                        title={run.table_name || run.table_id}
                      >
                        {run.table_name || run.table_id.slice(0, 8)}
                      </Link>
                    ) : (
                      <span className="table-link run-table-name" title={run.table_name}>
                        {run.table_name}
                      </span>
                    )}
                  </td>
                )}
                <td>
                  <div className="score-cell-content">
                    {run.status === 'running' ? (
                      <span
                        className="score-text score-text--running"
                        style={{
                          color: '#f59e0b',
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 5,
                          fontSize: 12,
                        }}
                      >
                        <Spinner size={12} color="#f59e0b" />
                        Evaluating…
                        {run.completed_questions != null && run.total_questions > 0 && (
                          <span title="Questions finished so far">
                            {run.completed_questions}/{run.total_questions}
                          </span>
                        )}
                      </span>
                    ) : (
                      <span
                        className="score-text"
                        style={{ color: run.score >= 0.5 ? '#10b981' : '#ef4444' }}
                      >
                        {Math.round(run.score * 100)}%
                      </span>
                    )}
                    {run.regression_detected && (
                      <span title="Regression detected">
                        <AlertTriangle size={12} style={{ color: '#ef4444' }} />
                      </span>
                    )}
                  </div>
                </td>
                <td className="pass-rate-cell">
                  {run.status === 'running' ? (
                    <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Calculating…</span>
                  ) : (
                    <ScoreBar score={run.pass_rate} height={5} showLabel />
                  )}
                </td>
                {!compact && <td className="questions-count-cell">{run.total_questions}</td>}
                <td>
                  <StatusBadge status={run.status} size="sm" />
                </td>
                <td>
                  <span
                    className={`triggered-by-text run-triggered-by ${run.triggered_by === 'scheduler' ? ' triggered-by-text--scheduler' : ''}`}
                    title={run.triggered_by}
                  >
                    {run.triggered_by}
                  </span>
                </td>
                {!compact && (
                  <td>
                    {(run as any).connection_id != null ? (
                      <code
                        style={{
                          fontSize: 11,
                          padding: '2px 6px',
                          background: 'rgba(99,102,241,0.1)',
                          borderRadius: 4,
                          color: 'var(--accent)',
                        }}
                      >
                        #{(run as any).connection_id}
                      </code>
                    ) : (
                      <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>default</span>
                    )}
                  </td>
                )}
                <td className="date-cell">{dayjs(run.created_at).format('MMM D, HH:mm')}</td>
                <td style={{ textAlign: 'right' }}>
                  {run.status === 'running' ? (
                    <button
                      className="btn btn--danger btn--xs"
                      title="Stop this evaluation run"
                      disabled={stopMut.isPending && stopMut.variables === run.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (window.confirm('Are you sure you want to stop this evaluation run?')) {
                          stopMut.mutate(run.id);
                        }
                      }}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4,
                        padding: '3px 8px',
                        fontSize: '11px',
                        fontWeight: 600,
                        borderRadius: '6px',
                        background: 'rgba(239, 68, 68, 0.12)',
                        color: '#ef4444',
                        border: '1px solid rgba(239, 68, 68, 0.3)',
                        cursor: 'pointer',
                      }}
                    >
                      <Square size={10} fill="currentColor" />
                      {stopMut.isPending && stopMut.variables === run.id ? 'Stopping…' : 'Stop'}
                    </button>
                  ) : (
                    <ChevronRight size={14} style={{ color: 'var(--text-muted)' }} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="run-history-pagination">
          <span className="pagination-info">
            Showing {page * pageSize + 1}–{Math.min((page + 1) * pageSize, visibleRuns.length)} of{' '}
            {visibleRuns.length} runs
          </span>
          <div className="pagination-btns">
            <button
              className="btn btn--ghost btn--sm"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
            >
              ← Prev
            </button>
            <button
              className="btn btn--ghost btn--sm"
              disabled={page >= totalPages - 1}
              onClick={() => setPage((p) => p + 1)}
            >
              Next →
            </button>
          </div>
        </div>
      )}

      {selectedRunId && (
        <RunDetailDrawer runId={selectedRunId} onClose={() => setSelectedRunId(null)} />
      )}
    </div>
  );
}
