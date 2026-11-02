import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { App, Select } from 'antd';
import dayjs from 'dayjs';
import { AlertTriangle, BarChart2, CheckCircle, Play } from 'lucide-react';

import { agentApi } from '../../api/agent';
import { enrichmentApi, evalApi, questionsApi } from '../../api/client';
import { useTableDetails } from '../../hooks/useTables';
import { ErrorState } from '../common/ErrorState';
import { ScoreRing } from '../common/EvalUI';
import { SkeletonTable } from '../common/Skeleton';

import './EvaluationTab.css';

interface Props {
  tableId: string;
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
    refetchInterval: (query) => {
      const data = query.state.data;
      const hasRunning = Array.isArray(data) && data.some((r) => r.status === 'running');
      return hasRunning ? 3000 : false;
    },
  });

  const { data: enrichment } = useQuery({
    queryKey: ['enrichment', tableId],
    queryFn: () => enrichmentApi.getLatest(tableId),
  });

  const { data: questions } = useQuery({
    queryKey: ['questions', tableId],
    queryFn: () => questionsApi.list(tableId),
  });

  const { data: table } = useTableDetails(tableId);

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

  // Connections for catalog selector
  const [connections, setConnections] = useState<any[]>([]);
  const [loadingConnections, setLoadingConnections] = useState(false);
  const [selectedConnection, setSelectedConnection] = useState<number | undefined>(undefined);

  useEffect(() => {
    setLoadingConnections(true);
    agentApi
      .listConnections()
      .then((res) => {
        const conns = res.connections || [];
        setConnections(conns);
        if (table?.catalog) {
          const match = conns.find(
            (c: any) => c.name?.toLowerCase() === table.catalog?.toLowerCase(),
          );
          if (match) {
            setSelectedConnection(match.connection_id);
          } else if (conns.length > 0) {
            setSelectedConnection(conns[0].connection_id);
          }
        } else if (conns.length > 0) {
          setSelectedConnection(conns[0].connection_id);
        }
        setLoadingConnections(false);
      })
      .catch(() => setLoadingConnections(false));
  }, [table?.catalog]);

  const triggerMutation = useMutation({
    mutationFn: () => evalApi.triggerRun(tableId, selectedConnection),
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

  // Deduplicate runs so triggered run never appears twice
  const allRuns = useMemo(() => {
    const map = new Map<string, any>();
    if (triggerMutation.data) {
      map.set(triggerMutation.data.id, triggerMutation.data);
    }
    (runs ?? []).forEach((r: any) => {
      map.set(r.id, r);
    });
    return Array.from(map.values())
      .filter((run) => run.triggered_by !== 'promotion')
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  }, [triggerMutation.data, runs]);

  if (isLoading) return <SkeletonTable rows={3} cols={4} />;
  if (isError) return <ErrorState onRetry={refetch} />;

  return (
    <div>
      <div className="evaluation-tab__header">
        <h2 className="evaluation-tab__title">{t('evaluations.title')}</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
            Catalog
          </span>
          <Select
            value={selectedConnection}
            onChange={setSelectedConnection}
            style={{ minWidth: 180 }}
            placeholder="Default catalog"
            loading={loadingConnections}
            allowClear
            size="small"
            options={connections.map((c: any) => ({
              value: c.connection_id,
              label: c.name,
            }))}
          />
          <button
            className="btn btn--primary btn--sm"
            onClick={() => triggerMutation.mutate()}
            disabled={triggerMutation.isPending || !isReady || !selectedConnection}
            title={
              !selectedConnection
                ? 'Please select a catalog connection before running'
                : !isReady
                  ? 'Complete all requirements below before running'
                  : undefined
            }
          >
            <Play size={14} />
            {triggerMutation.isPending ? 'Running...' : t('evaluations.runEval')}
          </button>
        </div>
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
          <ScoreRing score={triggerMutation.data.score} status={triggerMutation.data.status} />
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

      {allRuns.length === 0 ? (
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
                <th>Catalog</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {allRuns.map((run) => (
                <tr key={run.id}>
                  <td>
                    <code className="run-id-code">{run.id.slice(0, 8)}…</code>
                  </td>
                  <td>
                    <div style={{ display: 'flex', justifyContent: 'center' }}>
                      <ScoreRing score={run.score} status={run.status} />
                    </div>
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
                  <td className="run-date-label">{dayjs(run.created_at).format('MMM D, HH:mm')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
