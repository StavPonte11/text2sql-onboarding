import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { App } from 'antd';
import dayjs from 'dayjs';
import { AlertTriangle, BarChart2, CheckCircle, Play } from 'lucide-react';

import { enrichmentApi, evalApi, questionsApi } from '../../api/client';
import { ErrorState } from '../common/ErrorState';
import { SkeletonTable } from '../common/Skeleton';

import './EvaluationTab.css';

interface Props {
  tableId: string;
}

function ScoreRing({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const cls = pct >= 50 ? 'score-ring--high' : 'score-ring--low';
  return <div className={`score-ring ${cls}`}>{pct}%</div>;
}

export function EvaluationTab({ tableId }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { message } = App.useApp();

  const {
    data: runs,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['eval-runs', tableId],
    queryFn: () => evalApi.listRuns(tableId),
  });

  const { data: enrichment } = useQuery({
    queryKey: ['enrichment', tableId],
    queryFn: () => enrichmentApi.getLatest(tableId),
  });

  const { data: questions } = useQuery({
    queryKey: ['questions', tableId],
    queryFn: () => questionsApi.list(tableId),
  });

  // Compute readiness
  const hasDescription = !!enrichment?.data?.table_description;
  const hasEnrichment = !!enrichment?.data;
  const hasQuestions = !!(questions && questions.length > 0);
  const isReady = hasEnrichment && hasDescription && hasQuestions;

  const readinessItems = [
    { label: 'Table enrichment / schema', ok: hasEnrichment },
    { label: 'Table description', ok: hasDescription },
    { label: 'At least 1 golden question', ok: hasQuestions },
  ];

  const triggerMutation = useMutation({
    mutationFn: () => evalApi.triggerRun(tableId),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ['eval-runs', tableId] });
      qc.setQueryData(['eval-runs', tableId], (old: any[]) => [run, ...(old ?? [])]);
      message.success('Evaluation run triggered');
    },
    onError: (err: any) => {
      const detail = err?.response?.data?.detail;
      message.error(
        detail || 'Evaluation failed. Please go change the descriptions in Oasis platform.',
        8,
      );
    },
  });

  if (isLoading) return <SkeletonTable rows={3} cols={4} />;
  if (isError) return <ErrorState onRetry={refetch} />;

  return (
    <div>
      <div className="evaluation-tab__header">
        <h2 className="evaluation-tab__title">{t('evaluations.title')}</h2>
        <button
          className="btn btn--primary btn--sm"
          onClick={() => triggerMutation.mutate()}
          disabled={triggerMutation.isPending || !isReady}
          title={!isReady ? 'Complete all requirements below before running' : undefined}
        >
          <Play size={14} />
          {triggerMutation.isPending ? 'Running...' : t('evaluations.runEval')}
        </button>
      </div>

      {/* Readiness checklist */}
      <div
        className={`eval-readiness-banner ${isReady ? 'eval-readiness-banner--ready' : 'eval-readiness-banner--incomplete'}`}
      >
        <div className="eval-readiness-banner__icon">
          {isReady ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}
        </div>
        <div className="eval-readiness-banner__content">
          <div className="eval-readiness-banner__title">
            {isReady ? 'Ready to evaluate' : 'Requirements not met — evaluation is disabled'}
          </div>
          <div className="eval-readiness-banner__items">
            {readinessItems.map((item) => (
              <span
                key={item.label}
                className={`eval-readiness-item ${item.ok ? 'eval-readiness-item--ok' : 'eval-readiness-item--missing'}`}
              >
                {item.ok ? '✓' : '✗'} {item.label}
              </span>
            ))}
          </div>
        </div>
      </div>

      {triggerMutation.data && (
        <div className="card latest-run-card">
          <ScoreRing score={triggerMutation.data.score} />
          <div>
            <div className="latest-run-card__title">
              Latest Run: {triggerMutation.data.table_name || tableId.slice(0, 8)}
            </div>
            <div className="latest-run-card__info">
              Run ID: {triggerMutation.data.id.slice(0, 8)}… · Status: {triggerMutation.data.status}
            </div>
          </div>
        </div>
      )}

      {(!runs || runs.length === 0) && !triggerMutation.data ? (
        <div className="card">
          <div className="empty-state">
            <BarChart2 size={36} className="empty-state__icon" />
            <div className="empty-state__text">{t('evaluations.noData')}</div>
            <div className="empty-state__sub">Run an evaluation to see scores and results</div>
          </div>
        </div>
      ) : (
        <div className="card evaluations-table-card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Run ID</th>
                <th>Contains Execution Accuracy</th>
                <th>{t('evaluations.status')}</th>
                <th>Type</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {[...(triggerMutation.data ? [triggerMutation.data] : []), ...(runs ?? [])]
                .filter((run) => run.triggered_by !== 'promotion')
                .map((run) => (
                  <tr key={run.id}>
                    <td>
                      <code className="run-id-code">{run.id.slice(0, 8)}…</code>
                    </td>
                    <td>
                      <ScoreRing score={run.score} />
                    </td>
                    <td>
                      <span
                        className="run-status-badge"
                        style={{
                          color:
                            run.status === 'completed'
                              ? 'var(--status-production)'
                              : run.status === 'failed'
                                ? 'var(--status-degraded)'
                                : 'var(--status-sandbox)',
                        }}
                      >
                        {run.status}
                      </span>
                    </td>
                    <td>
                      <span className="run-type-label">{run.triggered_by}</span>
                    </td>
                    <td className="run-date-label">
                      {dayjs(run.created_at).format('MMM D, HH:mm')}
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
