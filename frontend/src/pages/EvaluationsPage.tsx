import { useEffect, useMemo, useState, useTransition } from 'react';
import { useQuery } from '@tanstack/react-query';
import { App, Select } from 'antd';
import {
  CalendarClock,
  Check,
  Database,
  Eye,
  EyeOff,
  History,
  Loader2,
  PlayCircle,
  Search,
  X,
} from 'lucide-react';

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

const ALL_STATUSES = ['production', 'sandbox', 'verified', 'draft', 'degraded'];

// ── Table filters ───────────────────────────────────────────────────────────
function TableFilters({
  search,
  setSearch,
  statusOptions,
  activeStatuses,
  setActiveStatuses,
  ownerOptions,
  activeOwners,
  setActiveOwners,
  onClear,
  showSpider2,
  onToggleSpider2,
  spider2Pending,
}: {
  search: string;
  setSearch: (v: string) => void;
  statusOptions: string[];
  activeStatuses: string[];
  setActiveStatuses: (v: string[]) => void;
  ownerOptions: string[];
  activeOwners: string[];
  setActiveOwners: (v: string[]) => void;
  onClear: () => void;
  showSpider2: boolean;
  onToggleSpider2: () => void;
  spider2Pending: boolean;
}) {
  return (
    <div className="table-filters">
      <div className="table-filters__search">
        <Search size={14} />
        <input
          type="text"
          placeholder="Search by name, schema or service catalog…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {search && (
          <button className="table-filters__clear-icon" onClick={() => setSearch('')}>
            <X size={14} />
          </button>
        )}
      </div>

      <div className="table-filters__group">
        <span className="table-filters__group-label">Status</span>
        <Select
          mode="multiple"
          allowClear
          className="table-filters__select"
          popupClassName="table-filters__select-dropdown"
          placeholder="All statuses"
          value={activeStatuses}
          onChange={setActiveStatuses}
          options={statusOptions.map((status) => ({ label: status, value: status }))}
          maxTagCount={2}
        />
      </div>

      <div className="table-filters__group">
        <span className="table-filters__group-label">Owner</span>
        <Select
          mode="multiple"
          allowClear
          className="table-filters__select"
          popupClassName="table-filters__select-dropdown"
          placeholder={ownerOptions.length ? 'All owners' : 'No owner data'}
          value={activeOwners}
          onChange={setActiveOwners}
          options={ownerOptions.map((owner) => ({ label: owner, value: owner }))}
          disabled={!ownerOptions.length}
          maxTagCount={2}
        />
      </div>

      <button
        className={`btn btn--sm table-filters__spider2-toggle${showSpider2 ? ' table-filters__spider2-toggle--active' : ''}`}
        onClick={onToggleSpider2}
        disabled={spider2Pending}
        title={showSpider2 ? 'Hide Spider2 tables' : 'Show Spider2 tables'}
      >
        {spider2Pending ? (
          <Loader2 size={13} style={{ animation: 'spin 0.8s linear infinite' }} />
        ) : showSpider2 ? (
          <Eye size={13} />
        ) : (
          <EyeOff size={13} />
        )}
        Spider2
      </button>

      <button className="btn btn--ghost btn--sm" onClick={onClear}>
        Clear Filters
      </button>
    </div>
  );
}

// ── Run trigger panel ──────────────────────────────────────────────────────────
function RunTriggerPanel({
  onLaunch,
  showSpider2,
  onToggleSpider2,
  spider2Pending,
}: {
  onLaunch?: () => void;
  showSpider2: boolean;
  onToggleSpider2: () => void;
  spider2Pending: boolean;
}) {
  const [selectedTableIds, setSelectedTableIds] = useState<string[]>([]);
  const [triggeredBy] = useState('user');
  const [launched, setLaunched] = useState(false);
  const [search, setSearch] = useState('');
  const [activeStatuses, setActiveStatuses] = useState<string[]>([]);
  const [activeOwners, setActiveOwners] = useState<string[]>([]);
  const [visibleCount, setVisibleCount] = useState(50);
  const [sentinel, setSentinel] = useState<HTMLDivElement | null>(null);
  const { message } = App.useApp();

  useEffect(() => {
    setVisibleCount(50);
  }, [search, activeStatuses, activeOwners, showSpider2]);

  useEffect(() => {
    if (!sentinel) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setVisibleCount((prev) => prev + 50);
        }
      },
      { rootMargin: '100px' },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [sentinel]);

  const { data: tables = [], isLoading: tablesLoading } = useTables();

  const { data: readiness = {} } = useEvalReadiness();

  const triggerMut = useTriggerOrchestrationRun();

  const statusOptions = useMemo(
    () => Array.from(new Set([...ALL_STATUSES, ...tables.map((t: Table) => t.status)])),
    [tables],
  );

  const ownerOptions = useMemo(
    () =>
      Array.from(
        new Set(tables.map((t: Table) => (t as any).owner_id).filter(Boolean)),
      ).sort() as string[],
    [tables],
  );

  const filteredTables = useMemo(() => {
    const q = search.trim().toLowerCase();
    return tables.filter((t: Table) => {
      if (!showSpider2 && (t as any).owner_id === 'spider2') return false;
      if (activeStatuses.length > 0 && !activeStatuses.includes(t.status)) return false;
      const owner = (t as any).owner_id;
      if (activeOwners.length > 0 && !activeOwners.includes(owner)) return false;
      if (!q) return true;
      const serviceCatalog = (t as any).service_catalog ?? (t as any).serviceCatalog ?? '';
      const haystack = `${t.name} ${t.schema_name} ${serviceCatalog}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [tables, search, activeStatuses, activeOwners, showSpider2]);

  const displayedTables = useMemo(() => {
    return filteredTables.slice(0, visibleCount);
  }, [filteredTables, visibleCount]);

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

  const clearFilters = () => {
    setSearch('');
    setActiveStatuses([]);
    setActiveOwners([]);
  };

  const selectAll = () =>
    setSelectedTableIds(
      filteredTables.filter((t: Table) => readiness[t.id]?.ready !== false).map((t: Table) => t.id),
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

      {!tablesLoading && tables.length > 0 && (
        <TableFilters
          search={search}
          setSearch={setSearch}
          statusOptions={statusOptions}
          activeStatuses={activeStatuses}
          setActiveStatuses={setActiveStatuses}
          ownerOptions={ownerOptions}
          activeOwners={activeOwners}
          setActiveOwners={setActiveOwners}
          onClear={clearFilters}
          showSpider2={showSpider2}
          onToggleSpider2={onToggleSpider2}
          spider2Pending={spider2Pending}
        />
      )}

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
      ) : !filteredTables.length ? (
        <EmptySlate
          icon={<Database size={28} />}
          title="No tables match your filters"
          sub="Try adjusting the search or filters above"
        />
      ) : (
        <>
          <div className="run-trigger-panel__count">
            Showing {filteredTables.length} of {tables.length} tables
          </div>
          <div className="run-trigger-panel__grid">
            {displayedTables.map((t: Table) => {
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
            {filteredTables.length > visibleCount && (
              <div
                ref={setSentinel}
                style={{
                  gridColumn: '1 / -1',
                  height: '50px',
                  display: 'flex',
                  justifyContent: 'center',
                  alignItems: 'center',
                }}
              >
                <Loader2
                  size={18}
                  style={{ animation: 'spin 0.8s linear infinite' }}
                  className="text-muted"
                />
              </div>
            )}
          </div>
        </>
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
  const [showSpider2, setShowSpider2] = useState(false);
  const [spider2Pending, startSpider2Transition] = useTransition();

  const handleToggleSpider2 = () => {
    startSpider2Transition(() => {
      setShowSpider2((v) => !v);
    });
  };

  // All tables — used to build spider2 table ID set for run history filtering
  const { data: allTables = [] } = useTables();
  const spider2TableIds = useMemo(
    () => new Set(allTables.filter((t: any) => t.owner_id === 'spider2').map((t: any) => t.id)),
    [allTables],
  );

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
          <RunHistoryTable
            limit={100}
            excludeTableIds={showSpider2 ? undefined : spider2TableIds}
          />
        </div>
      )}

      {activeTab === 'schedules' && <ScheduleManager />}

      {activeTab === 'run' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <RunTriggerPanel
            onLaunch={switchToHistory}
            showSpider2={showSpider2}
            onToggleSpider2={handleToggleSpider2}
            spider2Pending={spider2Pending}
          />
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
