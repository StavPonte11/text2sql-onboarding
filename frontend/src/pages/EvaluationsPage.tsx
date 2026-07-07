import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { App } from 'antd';
import { CalendarClock, Check, Database, History, Loader2, PlayCircle } from 'lucide-react';

import { orchestrationApi } from '../api/orchestration';
import { EmptySlate, SectionHeader, Spinner } from '../components/common/EvalUI';
import { RunHistoryTable } from '../components/monitoring/RunHistoryTable';
import { ScheduleManager } from '../components/monitoring/ScheduleManager';
import {
  useEvalReadiness,
  useTriggerDatasetRun,
  useTriggerOrchestrationRun,
} from '../hooks/useEvaluations';
import { useTables } from '../hooks/useTables';

import type { Table } from '../types';

import './EvaluationsPage.css';

type Tab = 'history' | 'schedules' | 'run';

// ── Run trigger panel ──────────────────────────────────────────────────────────
function RunTriggerPanel({ onLaunch }: { onLaunch?: () => void }) {
  const [selectedTableIds, setSelectedTableIds] = useState<string[]>([]);
  const [triggeredBy] = useState('user');
  const [launched, setLaunched] = useState(false);
  const { message } = App.useApp();

  const { data: tables = [], isLoading: tablesLoading } = useTables();

  const { data: readiness = {} } = useEvalReadiness();

  const triggerMut = useTriggerOrchestrationRun();

  const handleLaunch = () => {
    triggerMut.mutate(
      { tableIds: selectedTableIds, triggeredBy },
      {
        onSuccess: () => {
          setLaunched(true);
          setTimeout(() => setLaunched(false), 4000);
          onLaunch?.();
        },
        onError: (err: any) => {
          const detail = err?.response?.data?.detail;
          message.error(
            detail || 'Evaluation failed. Please check table descriptions in Oasis platform.',
            10,
          );
        },
      },
    );
  };

  const toggleTable = (id: string) => {
    // Prevent selecting incomplete tables
    if (!readiness[id]?.ready && readiness[id] !== undefined) return;
    setSelectedTableIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  const selectAll = () =>
    setSelectedTableIds(
      tables.filter((t: Table) => readiness[t.id]?.ready !== false).map((t: Table) => t.id),
    );

  const clearAll = () => setSelectedTableIds([]);

  return (
    <div className="card">
      <SectionHeader
        title="Trigger Evaluation Run"
        sub="Select tables then launch an evaluation"
        action={
          <div className="run-trigger-panel__actions">
            <button className="btn btn--ghost btn--sm" onClick={selectAll}>
              Select All
            </button>
            <button className="btn btn--ghost btn--sm" onClick={clearAll}>
              Clear
            </button>
          </div>
        }
      />

      {tablesLoading ? (
        <div className="run-trigger-panel__loading">
          <Spinner />
        </div>
      ) : !tables.length ? (
        <EmptySlate
          icon={<Database size={28} />}
          title="No tables found"
          sub="Add tables first via the Tables section"
        />
      ) : (
        <div className="run-trigger-panel__grid">
          {tables.map((t: Table) => {
            const selected = selectedTableIds.includes(t.id);
            const tableReadiness = readiness[t.id];
            const isIncomplete = tableReadiness !== undefined && !tableReadiness.ready;
            const statusColor: Record<string, string> = {
              production: '#10b981',
              sandbox: '#f59e0b',
              verified: '#3b82f6',
              draft: '#64748b',
              degraded: '#ef4444',
            };
            const sc = statusColor[t.status] ?? '#64748b';
            return (
              <div
                key={t.id}
                onClick={() => toggleTable(t.id)}
                className={`table-card${selected ? ' table-card--selected' : ''}${isIncomplete ? ' table-card--incomplete' : ''}`}
                title={isIncomplete ? `Missing: ${tableReadiness.missing.join(', ')}` : undefined}
              >
                <div className="table-card__header">
                  <div className="table-card__status-dot" style={{ background: sc }} />
                  <span
                    className={`table-card__name${selected ? ' table-card__name--selected' : ''}`}
                  >
                    {t.name}
                  </span>
                  {isIncomplete && (
                    <span
                      className="table-card__incomplete-badge"
                      title={`Missing: ${tableReadiness.missing.join(', ')}`}
                    >
                      ⚠ Incomplete
                    </span>
                  )}
                </div>
                <div className="table-card__info">
                  {t.schema_name} · {t.status}
                </div>
                {isIncomplete && (
                  <div className="table-card__missing">
                    Missing: {tableReadiness.missing.join(', ')}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {launched && (
        <div className="launch-success-banner">
          <Check size={16} /> Evaluation run launched for {selectedTableIds.length} table
          {selectedTableIds.length > 1 ? 's' : ''} — results will appear in History below.
        </div>
      )}

      <button
        className="btn btn--primary launch-btn-full"
        disabled={selectedTableIds.length === 0 || triggerMut.isPending}
        onClick={handleLaunch}
      >
        {triggerMut.isPending ? (
          <>
            <Spinner size={15} color="#fff" /> Running…
          </>
        ) : (
          <>
            <PlayCircle size={16} /> Launch Evaluation ({selectedTableIds.length} table
            {selectedTableIds.length !== 1 ? 's' : ''})
          </>
        )}
      </button>
    </div>
  );
}

// ── DatasetRunPanel ─────────────────────────────────────────────────────────────
interface DatasetRunPanelProps {
  runningDataset: string | null;
  setRunningDataset: (val: string | null) => void;
  setRunningRunId: (val: string | null) => void;
  onLaunch?: () => void;
}

function DatasetRunPanel({
  runningDataset,
  setRunningDataset,
  setRunningRunId,
  onLaunch,
}: DatasetRunPanelProps) {
  const triggerDatasetMut = useTriggerDatasetRun();

  const handleLaunch = (datasetName: string) => {
    setRunningDataset(datasetName);
    triggerDatasetMut.mutate(datasetName, {
      onSuccess: (run) => {
        setRunningRunId(run.id);
        // Switch to history tab so the user can watch progress
        onLaunch?.();
      },
      onError: () => {
        setRunningDataset(null);
        setRunningRunId(null);
      },
    });
  };

  return (
    <div className="card">
      <SectionHeader
        title="Dataset Evaluation Controls"
        sub="Execute complete dataset-level evaluations across all production tables"
      />

      <div style={{ display: 'flex', gap: '16px', marginTop: '16px' }}>
        <button
          className="btn btn--secondary"
          style={{ flex: 1, justifyContent: 'center' }}
          disabled={triggerDatasetMut.isPending || !!runningDataset}
          onClick={() => handleLaunch('spider2')}
        >
          {triggerDatasetMut.isPending && runningDataset === 'spider2' ? (
            <>
              <Loader2 size={15} style={{ animation: 'spin 1s linear infinite' }} /> Launching…
            </>
          ) : (
            <>
              <PlayCircle size={16} /> Run Spider2 Dataset
            </>
          )}
        </button>

        <button
          className="btn btn--secondary"
          style={{ flex: 1, justifyContent: 'center' }}
          disabled={triggerDatasetMut.isPending || !!runningDataset}
          onClick={() => handleLaunch('text2sql_production')}
        >
          {triggerDatasetMut.isPending && runningDataset === 'text2sql_production' ? (
            <>
              <Loader2 size={15} style={{ animation: 'spin 1s linear infinite' }} /> Launching…
            </>
          ) : (
            <>
              <PlayCircle size={16} /> Run Production Dataset
            </>
          )}
        </button>
      </div>
    </div>
  );
}

// ── EvaluationsPage ────────────────────────────────────────────────────────────
export function EvaluationsPage() {
  const [activeTab, setActiveTab] = useState<Tab>('history');
  const [runningDataset, setRunningDataset] = useState<string | null>(null);
  const [runningRunId, setRunningRunId] = useState<string | null>(null);

  // Poll the specific run's details if we have a running run
  useQuery({
    queryKey: ['running-run-status', runningRunId],
    queryFn: () => {
      if (!runningRunId) return null;
      return orchestrationApi.getRun(runningRunId);
    },
    enabled: !!runningRunId,
    refetchInterval: (query) => {
      const data = query.state.data as any;
      if (data && (data.status === 'completed' || data.status === 'failed')) {
        // Run has finished, clear states
        setRunningDataset(null);
        setRunningRunId(null);
        return false;
      }
      return 3000; // poll every 3 seconds
    },
  });

  const switchToHistory = () => setActiveTab('history');

  const TABS: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: 'history', label: 'Execution History', icon: <History size={14} /> },
    { key: 'schedules', label: 'Scheduled Runs', icon: <CalendarClock size={14} /> },
    { key: 'run', label: 'Run Controls', icon: <PlayCircle size={14} /> },
  ];

  const datasetLabel = runningDataset === 'text2sql_production' ? 'Production Dataset' : 'Spider2';

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Evaluations</h1>
          <p className="page__subtitle">Manage evaluation runs, schedules, and execution history</p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="tabs evaluations-page__tabs">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            className={`tab-item evaluations-page__tab-item${activeTab === tab.key ? ' tab-item--active' : ''}`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {/* Persistent running notice — stays visible at the top of the content across all tabs while the background job executes */}
      {runningDataset && (
        <div className="dataset-running-notice" style={{ marginBottom: '20px' }}>
          <Loader2 size={16} style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }} />
          <div>
            <strong>{datasetLabel} evaluation is currently running</strong>
            <span>
              {' '}
              — Results will appear in the History table below as the run completes (this may take
              several minutes).
            </span>
          </div>
        </div>
      )}

      {/* Tab content */}
      {activeTab === 'history' && (
        <div>
          <SectionHeader
            title="Execution History"
            sub="All evaluation runs across tables — click any row to view full report"
          />
          <RunHistoryTable limit={100} />
        </div>
      )}

      {activeTab === 'schedules' && <ScheduleManager />}

      {activeTab === 'run' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <RunTriggerPanel onLaunch={switchToHistory} />
          <DatasetRunPanel
            runningDataset={runningDataset}
            setRunningDataset={setRunningDataset}
            setRunningRunId={setRunningRunId}
            onLaunch={switchToHistory}
          />
        </div>
      )}
    </div>
  );
}
