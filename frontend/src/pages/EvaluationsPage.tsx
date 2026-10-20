import { useEffect, useMemo, useState, useTransition } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { App, Select } from 'antd';
import {
  CalendarClock,
  Check,
  Code2,
  Database,
  ExternalLink,
  Eye,
  EyeOff,
  History,
  Loader2,
  PlayCircle,
  Plus,
  Search,
  Server,
  Square,
  Trash2,
  X,
} from 'lucide-react';

import { agentApi } from '../api/agent';
import { orchestrationApi } from '../api/orchestration';
import { EmptySlate, SectionHeader, Spinner } from '../components/common/EvalUI';
import { RunHistoryTable } from '../components/monitoring/RunHistoryTable';
import { ScheduleManager } from '../components/monitoring/ScheduleManager';
import { isSpider2Table } from '../config/constants';
import {
  useConnectionQuestions,
  useCreateConnectionQuestion,
  useDeleteConnectionQuestion,
  useEvalReadiness,
  useStopEvalRun,
  useTriggerConnectionRun,
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
  connections,
  loadingConnections,
}: {
  onLaunch?: () => void;
  showSpider2: boolean;
  onToggleSpider2: () => void;
  spider2Pending: boolean;
  connections: any[];
  loadingConnections: boolean;
}) {
  const [selectedTableIds, setSelectedTableIds] = useState<string[]>([]);
  const [triggeredBy] = useState('user');
  const [launched, setLaunched] = useState(false);
  const [search, setSearch] = useState('');
  const [activeStatuses, setActiveStatuses] = useState<string[]>([]);
  const [activeOwners, setActiveOwners] = useState<string[]>([]);
  const [visibleCount, setVisibleCount] = useState(50);
  const [sentinel, setSentinel] = useState<HTMLDivElement | null>(null);
  const [selectedConnection, setSelectedConnection] = useState<number | undefined>(undefined);
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

  useEffect(() => {
    if (!selectedConnection && connections.length > 0) {
      setSelectedConnection(connections[0].connection_id);
    }
  }, [connections, selectedConnection]);

  const filteredTables = useMemo(() => {
    const q = search.trim().toLowerCase();
    const selectedCatalog = selectedConnection
      ? connections.find((c: any) => c.connection_id === selectedConnection)?.name?.toLowerCase()
      : undefined;

    return tables.filter((t: Table) => {
      if (selectedCatalog && t.catalog?.toLowerCase() !== selectedCatalog) return false;
      if (!showSpider2 && isSpider2Table(t)) return false;
      if (activeStatuses.length > 0 && !activeStatuses.includes(t.status)) return false;
      const owner = (t as any).owner_id;
      if (activeOwners.length > 0 && !activeOwners.includes(owner)) return false;
      if (!q) return true;
      const serviceCatalog = (t as any).service_catalog ?? (t as any).serviceCatalog ?? '';
      const haystack = `${t.name} ${t.schema_name} ${serviceCatalog}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [tables, search, activeStatuses, activeOwners, showSpider2, selectedConnection, connections]);

  const displayedTables = useMemo(() => {
    return filteredTables.slice(0, visibleCount);
  }, [filteredTables, visibleCount]);

  const handleLaunch = () => {
    if (!selectedConnection) {
      message.error('Please select a catalog connection before launching evaluation.');
      return;
    }
    triggerMut.mutate(
      { tableIds: selectedTableIds, triggeredBy, connectionId: selectedConnection },
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
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                Catalog
              </span>
              <Select
                value={selectedConnection}
                onChange={(val) => {
                  setSelectedConnection(val);
                  setSelectedTableIds([]);
                }}
                style={{ minWidth: 180 }}
                placeholder="Select catalog"
                loading={loadingConnections}
                options={connections.map((c: any) => ({
                  value: c.connection_id,
                  label: c.name,
                }))}
              />
            </div>
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
        disabled={selectedTableIds.length === 0 || triggerMut.isPending || !selectedConnection}
        title={!selectedConnection ? 'Please select a catalog connection' : undefined}
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

// ── Connection-First Evaluation Panel ──────────────────────────────────────────
function ConnectionRunPanel({
  onLaunch,
  connections,
  loadingConnections,
  setRunningRunId,
}: {
  onLaunch?: () => void;
  connections: any[];
  loadingConnections: boolean;
  setRunningRunId: (val: string | null) => void;
}) {
  const [selectedConnection, setSelectedConnection] = useState<number | undefined>(undefined);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newQuestion, setNewQuestion] = useState('');
  const [newExpectedSql, setNewExpectedSql] = useState('');
  const [newDifficulty, setNewDifficulty] = useState<string>('simple');
  const { message } = App.useApp();

  const triggerConnectionMut = useTriggerConnectionRun();
  const { data: questions = [], isLoading: questionsLoading } =
    useConnectionQuestions(selectedConnection);
  const createQuestionMut = useCreateConnectionQuestion();
  const deleteQuestionMut = useDeleteConnectionQuestion();

  useEffect(() => {
    if (!selectedConnection && connections.length > 0) {
      setSelectedConnection(connections[0].connection_id);
    }
  }, [connections, selectedConnection]);

  const activeConn = connections.find((c: any) => c.connection_id === selectedConnection);

  const handleLaunch = () => {
    if (!selectedConnection) {
      message.error('Please select a catalog connection before launching evaluation.');
      return;
    }
    triggerConnectionMut.mutate(
      {
        connectionId: selectedConnection,
        connectionName: activeConn?.name,
      },
      {
        onSuccess: (run) => {
          message.success(`Evaluation launched for ${activeConn?.name || selectedConnection}`);
          setRunningRunId(run.id);
          onLaunch?.();
        },
        onError: (err: any) => {
          const detail = err?.response?.data?.detail;
          message.error(detail || 'Evaluation launch failed.', 8);
        },
      },
    );
  };

  const handleAddQuestion = (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedConnection || !newQuestion.trim() || !newExpectedSql.trim()) {
      message.error('Please provide both question text and expected SQL.');
      return;
    }
    createQuestionMut.mutate(
      {
        connectionId: selectedConnection,
        payload: {
          question: newQuestion.trim(),
          expected_sql: newExpectedSql.trim(),
          difficulty: newDifficulty,
        },
      },
      {
        onSuccess: () => {
          message.success('Golden question added for this connection.');
          setNewQuestion('');
          setNewExpectedSql('');
          setShowAddForm(false);
        },
        onError: (err: any) => {
          message.error(err?.response?.data?.detail || 'Failed to save question.');
        },
      },
    );
  };

  const handleDeleteQuestion = (qId: string) => {
    if (!selectedConnection) return;
    deleteQuestionMut.mutate(
      { connectionId: selectedConnection, questionId: qId },
      {
        onSuccess: () => message.success('Question removed.'),
        onError: () => message.error('Failed to remove question.'),
      },
    );
  };

  return (
    <div
      className="card connection-eval-card"
      style={{ display: 'flex', flexDirection: 'column', gap: 18 }}
    >
      <SectionHeader
        title="Catalog Connection Evaluation"
        sub="Evaluate the Text-to-SQL agent against all tables and schema context of a connection"
        action={
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
              Connection
            </span>
            <Select
              value={selectedConnection}
              onChange={setSelectedConnection}
              style={{ minWidth: 240 }}
              placeholder="Select catalog connection"
              loading={loadingConnections}
              options={connections.map((c: any) => ({
                value: c.connection_id,
                label: `${c.name} (#${c.connection_id})`,
              }))}
            />
          </div>
        }
      />

      {activeConn && (
        <div
          style={{
            padding: '14px 18px',
            borderRadius: 8,
            background: 'rgba(255, 255, 255, 0.02)',
            border: '1px solid var(--border)',
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
              gap: 8,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Server size={18} style={{ color: 'var(--accent)' }} />
              <strong style={{ fontSize: 15 }}>{activeConn.name}</strong>
              <code
                style={{
                  fontSize: 11,
                  padding: '2px 6px',
                  background: 'rgba(99,102,241,0.1)',
                  borderRadius: 4,
                  color: 'var(--accent)',
                }}
              >
                ID: {activeConn.connection_id}
              </code>
              <span className="badge badge--neutral" style={{ fontSize: 11 }}>
                Dialect: {activeConn.dialect || 'trino'}
              </span>
              <span className="badge badge--neutral" style={{ fontSize: 11 }}>
                Service: {activeConn.service_type || 'Trino'}
              </span>
            </div>

            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              {questions.length} Golden Question{questions.length !== 1 ? 's' : ''} Configured
            </span>
          </div>

          {activeConn.ai_domain_context && (
            <div
              style={{
                fontSize: 12.5,
                color: 'var(--text-secondary)',
                background: 'rgba(0,0,0,0.15)',
                padding: '8px 12px',
                borderRadius: 6,
              }}
            >
              <span style={{ color: 'var(--text-muted)', marginRight: 6 }}>Domain Context:</span>
              {activeConn.ai_domain_context}
            </div>
          )}
        </div>
      )}

      {/* Main CTA */}
      <button
        className="btn btn--primary"
        style={{
          width: '100%',
          justifyContent: 'center',
          padding: '12px',
          fontSize: 14,
          fontWeight: 600,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
        disabled={triggerConnectionMut.isPending || !selectedConnection}
        onClick={handleLaunch}
      >
        {triggerConnectionMut.isPending ? (
          <>
            <Spinner size={16} color="#fff" /> Running Evaluation…
          </>
        ) : (
          <>
            <PlayCircle size={17} /> Launch Evaluation for{' '}
            {activeConn?.name || 'Selected Connection'} ({questions.length} Question
            {questions.length !== 1 ? 's' : ''})
          </>
        )}
      </button>

      {/* Golden Questions Suite */}
      <div style={{ marginTop: 8, borderTop: '1px solid var(--border)', paddingTop: 16 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 12,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Code2 size={16} style={{ color: 'var(--accent)' }} />
            <span style={{ fontWeight: 600, fontSize: 14 }}>Connection Golden Questions</span>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>({questions.length})</span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {selectedConnection && (
              <Link
                to={`/connections/${selectedConnection}`}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 5,
                  fontSize: 12,
                  color: 'var(--color-primary, #3b82f6)',
                  fontWeight: 600,
                  textDecoration: 'none',
                  padding: '4px 8px',
                  borderRadius: 6,
                  background: 'rgba(59, 130, 246, 0.08)',
                }}
              >
                <ExternalLink size={13} /> Open Connections Page
              </Link>
            )}
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => setShowAddForm(!showAddForm)}
              style={{ display: 'flex', alignItems: 'center', gap: 5 }}
            >
              {showAddForm ? <X size={13} /> : <Plus size={13} />}
              {showAddForm ? 'Cancel' : 'Add Question'}
            </button>
          </div>
        </div>

        {/* Add Question Form */}
        {showAddForm && (
          <form
            onSubmit={handleAddQuestion}
            style={{
              padding: '14px',
              borderRadius: 8,
              background: 'rgba(99,102,241,0.04)',
              border: '1px solid rgba(99,102,241,0.2)',
              marginBottom: 16,
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
          >
            <div>
              <label
                style={{
                  display: 'block',
                  fontSize: 12,
                  fontWeight: 500,
                  marginBottom: 4,
                  color: 'var(--text-secondary)',
                }}
              >
                User Question (Natural Language)
              </label>
              <input
                type="text"
                placeholder="e.g. How many passengers are registered in the passengers table?"
                value={newQuestion}
                onChange={(e) => setNewQuestion(e.target.value)}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  borderRadius: 6,
                  background: 'rgba(255,255,255,0.05)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-primary)',
                  fontSize: 13,
                }}
              />
            </div>

            <div>
              <label
                style={{
                  display: 'block',
                  fontSize: 12,
                  fontWeight: 500,
                  marginBottom: 4,
                  color: 'var(--text-secondary)',
                }}
              >
                Expected Trino SQL Query
              </label>
              <textarea
                rows={3}
                placeholder='e.g. SELECT COUNT(DISTINCT passenger_id) FROM "postgres"."public"."passengers_table";'
                value={newExpectedSql}
                onChange={(e) => setNewExpectedSql(e.target.value)}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  borderRadius: 6,
                  background: 'rgba(255,255,255,0.05)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-primary)',
                  fontFamily: 'monospace',
                  fontSize: 12.5,
                }}
              />
            </div>

            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Difficulty:</span>
                <select
                  value={newDifficulty}
                  onChange={(e) => setNewDifficulty(e.target.value)}
                  style={{
                    padding: '4px 8px',
                    borderRadius: 4,
                    background: 'var(--bg-secondary)',
                    border: '1px solid var(--border)',
                    color: 'var(--text-primary)',
                    fontSize: 12,
                  }}
                >
                  <option value="simple">Simple</option>
                  <option value="moderate">Moderate</option>
                  <option value="complex">Complex</option>
                </select>
              </div>

              <button
                type="submit"
                className="btn btn--primary btn--sm"
                disabled={createQuestionMut.isPending}
                style={{ display: 'flex', alignItems: 'center', gap: 6 }}
              >
                {createQuestionMut.isPending ? <Spinner size={12} /> : <Check size={13} />}
                Save Golden Question
              </button>
            </div>
          </form>
        )}

        {/* Questions List */}
        {questionsLoading ? (
          <div style={{ padding: '20px', display: 'flex', justifyContent: 'center' }}>
            <Spinner size={20} />
          </div>
        ) : !questions.length ? (
          <EmptySlate
            icon={<Code2 size={24} />}
            title="No golden questions for this connection yet"
            sub="Click 'Add Question' above to add benchmark questions for this database connection"
          />
        ) : (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              maxHeight: 380,
              overflowY: 'auto',
            }}
          >
            {questions.map((q: any) => (
              <div
                key={q.id}
                style={{
                  padding: '10px 14px',
                  borderRadius: 6,
                  background: 'rgba(255, 255, 255, 0.02)',
                  border: '1px solid var(--border)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8,
                  }}
                >
                  <span style={{ fontWeight: 500, fontSize: 13, color: 'var(--text-primary)' }}>
                    {q.question}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span
                      style={{
                        fontSize: 10.5,
                        textTransform: 'uppercase',
                        padding: '1px 6px',
                        borderRadius: 3,
                        background: 'rgba(255,255,255,0.06)',
                        color: 'var(--text-muted)',
                      }}
                    >
                      {q.difficulty || 'simple'}
                    </span>
                    <button
                      className="btn btn--ghost btn--sm"
                      onClick={() => handleDeleteQuestion(q.id)}
                      title="Delete question"
                      style={{ padding: '2px 5px', color: '#ef4444' }}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>

                <code
                  style={{
                    fontSize: 11.5,
                    color: '#93c5fd',
                    background: 'rgba(0,0,0,0.2)',
                    padding: '4px 8px',
                    borderRadius: 4,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                  }}
                >
                  {q.expected_sql}
                </code>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── DatasetRunPanel ─────────────────────────────────────────────────────────────
interface DatasetRunPanelProps {
  runningDataset: string | null;
  setRunningDataset: (val: string | null) => void;
  setRunningRunId: (val: string | null) => void;
  onLaunch?: () => void;
  connections: any[];
  loadingConnections: boolean;
}

function DatasetRunPanel({
  runningDataset,
  setRunningDataset,
  setRunningRunId,
  onLaunch,
  connections,
  loadingConnections,
}: DatasetRunPanelProps) {
  const triggerDatasetMut = useTriggerDatasetRun();
  const { message } = App.useApp();
  const [selectedConnection, setSelectedConnection] = useState<number | undefined>(undefined);

  useEffect(() => {
    if (!selectedConnection && connections.length > 0) {
      setSelectedConnection(connections[0].connection_id);
    }
  }, [connections, selectedConnection]);

  const handleLaunch = (datasetName: string) => {
    if (!selectedConnection) {
      message.error('Please select a catalog connection before launching dataset evaluation.');
      return;
    }
    setRunningDataset(datasetName);
    const connObj = connections.find((c: any) => c.connection_id === selectedConnection);
    triggerDatasetMut.mutate(
      {
        datasetName,
        connectionId: selectedConnection,
        connectionName: connObj?.name,
      },
      {
        onSuccess: (run) => {
          setRunningRunId(run.id);
          // Switch to history tab so the user can watch progress
          onLaunch?.();
        },
        onError: (err: any) => {
          setRunningDataset(null);
          setRunningRunId(null);
          const detail = err?.response?.data?.detail;
          message.error(detail || 'Failed to launch dataset evaluation. Please try again.', 10);
        },
      },
    );
  };

  return (
    <div className="card">
      <SectionHeader
        title="Dataset Evaluation Controls"
        sub="Execute complete dataset-level evaluations across all production tables"
        action={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
              Catalog
            </span>
            <Select
              value={selectedConnection}
              onChange={setSelectedConnection}
              style={{ minWidth: 200 }}
              placeholder="Select catalog"
              loading={loadingConnections}
              options={connections.map((c: any) => ({
                value: c.connection_id,
                label: c.name,
              }))}
            />
          </div>
        }
      />

      <div style={{ display: 'flex', gap: '16px', marginTop: '16px' }}>
        <button
          className="btn btn--secondary"
          style={{ flex: 1, justifyContent: 'center' }}
          disabled={triggerDatasetMut.isPending || !!runningDataset || !selectedConnection}
          title={!selectedConnection ? 'Please select a catalog connection' : undefined}
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
          disabled={triggerDatasetMut.isPending || !!runningDataset || !selectedConnection}
          title={!selectedConnection ? 'Please select a catalog connection' : undefined}
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
  const stopRunMut = useStopEvalRun();
  const [showSpider2, setShowSpider2] = useState(false);
  const [spider2Pending, startSpider2Transition] = useTransition();

  const handleToggleSpider2 = () => {
    startSpider2Transition(() => {
      setShowSpider2((v) => !v);
    });
  };

  // Shared connections state (same pattern as Agent Sandbox)
  const [connections, setConnections] = useState<any[]>([]);
  const [loadingConnections, setLoadingConnections] = useState(false);

  useEffect(() => {
    setLoadingConnections(true);
    agentApi
      .listConnections()
      .then((res) => {
        setConnections(res.connections || []);
        setLoadingConnections(false);
      })
      .catch((err) => {
        console.error('Failed to fetch connections:', err);
        setLoadingConnections(false);
      });
  }, []);

  // All tables — used to build spider2 table ID set for run history filtering
  const { data: allTables = [] } = useTables();
  const spider2TableIds = useMemo(
    () => new Set(allTables.filter(isSpider2Table).map((t: any) => t.id)),
    [allTables],
  );

  // Poll the specific run's details if we have a running run
  const { data: runningRunData } = useQuery({
    queryKey: ['running-run-status', runningRunId],
    queryFn: () => {
      if (!runningRunId) return null;
      return orchestrationApi.getRun(runningRunId);
    },
    enabled: !!runningRunId,
    refetchInterval: (query) => {
      const data = query.state.data as any;
      if (data && (data.status === 'completed' || data.status === 'failed')) {
        return false;
      }
      return 3000; // poll every 3 seconds
    },
  });

  useEffect(() => {
    if (
      runningRunData &&
      (runningRunData.status === 'completed' ||
        runningRunData.status === 'failed' ||
        runningRunData.status === 'cancelled')
    ) {
      setRunningDataset(null);
      setRunningRunId(null);
    }
  }, [runningRunData]);

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
      {(runningDataset || runningRunId) && (
        <div
          className="dataset-running-notice"
          style={{
            marginBottom: '20px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '16px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
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
          {runningRunId && (
            <button
              className="btn btn--danger btn--sm"
              disabled={stopRunMut.isPending}
              onClick={() => {
                if (window.confirm('Are you sure you want to stop this evaluation run?')) {
                  stopRunMut.mutate(runningRunId, {
                    onSuccess: () => {
                      setRunningDataset(null);
                      setRunningRunId(null);
                    },
                  });
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
                padding: '6px 14px',
                fontWeight: 600,
                fontSize: 12.5,
                whiteSpace: 'nowrap',
                flexShrink: 0,
                cursor: 'pointer',
              }}
            >
              <Square size={12} fill="currentColor" />
              {stopRunMut.isPending ? 'Stopping…' : 'Stop Evaluation'}
            </button>
          )}
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
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          {/* Connection-Centric Evaluation (Primary) */}
          <ConnectionRunPanel
            onLaunch={switchToHistory}
            connections={connections}
            loadingConnections={loadingConnections}
            setRunningRunId={setRunningRunId}
          />

          <DatasetRunPanel
            runningDataset={runningDataset}
            setRunningDataset={setRunningDataset}
            setRunningRunId={setRunningRunId}
            onLaunch={switchToHistory}
            connections={connections}
            loadingConnections={loadingConnections}
          />

          {/* Legacy Table Selection Evaluation */}
          <details
            style={{
              background: '#f8fafc',
              padding: '16px',
              borderRadius: '10px',
              border: '1px solid #e2e8f0',
            }}
          >
            <summary style={{ cursor: 'pointer', fontWeight: 600, color: '#64748b' }}>
              Legacy: Table-based Evaluation Controls (Click to expand)
            </summary>
            <div style={{ marginTop: '16px' }}>
              <RunTriggerPanel
                onLaunch={switchToHistory}
                showSpider2={showSpider2}
                onToggleSpider2={handleToggleSpider2}
                spider2Pending={spider2Pending}
                connections={connections}
                loadingConnections={loadingConnections}
              />
            </div>
          </details>
        </div>
      )}
    </div>
  );
}
