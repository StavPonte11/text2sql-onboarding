import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { App, Empty, Modal, Select, Tag } from 'antd';
import {
  Check,
  ClipboardList,
  Code2,
  Copy,
  Database,
  Loader2,
  PlayCircle,
  Plus,
  Search,
  Server,
  Trash2,
  X,
} from 'lucide-react';

import { agentApi } from '../api/agent';
import { Spinner } from '../components/common/EvalUI';
import { RunHistoryTable } from '../components/monitoring/RunHistoryTable';
import {
  useConnectionQuestions,
  useCreateConnectionQuestion,
  useDeleteConnectionQuestion,
  useTriggerConnectionRun,
} from '../hooks/useEvaluations';

import './ConnectionsPage.css';

// ─── Tab types ─────────────────────────────────────────────────────────────────
type DetailTab = 'tables' | 'questions' | 'evaluation';

// ─── Difficulty badge colours ──────────────────────────────────────────────────
const DIFF_COLOR = { challenging: 'red', moderate: 'orange', simple: 'blue' } as const;

// ══════════════════════════════════════════════════════════════════════════════
// Tables tab
// ══════════════════════════════════════════════════════════════════════════════
function TablesTab({ connectionId }: { connectionId: number }) {
  const [search, setSearch] = useState('');

  const { data: allTables = [], isLoading } = useQuery({
    queryKey: ['jeen-connection-tables', connectionId],
    queryFn: () => agentApi.listConnectionTables(connectionId),
    staleTime: 60_000,
    enabled: !!connectionId,
  });

  const tables = useMemo(() => {
    if (!search.trim()) return allTables;
    const q = search.toLowerCase();
    return allTables.filter(
      (t: any) =>
        t.name?.toLowerCase().includes(q) ||
        t.schema_name?.toLowerCase().includes(q) ||
        t.description?.toLowerCase().includes(q),
    );
  }, [allTables, search]);

  return (
    <div className="conn-tab-body">
      <div className="conn-tab-toolbar">
        <div className="conn-search-wrap">
          <Search size={14} />
          <input
            type="text"
            placeholder="Search tables…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search && (
            <button className="conn-clear-btn" onClick={() => setSearch('')}>
              <X size={13} />
            </button>
          )}
        </div>
        <span className="conn-count-badge">
          {allTables.length} table{allTables.length !== 1 ? 's' : ''}
        </span>
      </div>

      {isLoading ? (
        <div className="conn-center-msg">
          <Spinner size={22} />
          <span>Loading tables from Jeen…</span>
        </div>
      ) : tables.length === 0 ? (
        <Empty
          description={
            <span style={{ color: '#64748b', fontSize: 13 }}>
              {allTables.length === 0
                ? 'No tables found for this connection in Jeen'
                : 'No tables match your search'}
            </span>
          }
        />
      ) : (
        <div className="conn-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Schema</th>
                <th>Rows</th>
                <th>Columns</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {tables.map((t: any, i: number) => (
                <tr key={t.name ?? i}>
                  <td>
                    <span className="table-name-cell" title={t.name}>
                      <Database size={13} style={{ flexShrink: 0, color: '#6366f1' }} />
                      {t.name}
                    </span>
                  </td>
                  <td>
                    {t.schema_name ? (
                      <code className="table-schema-code">{t.schema_name}</code>
                    ) : (
                      <span style={{ color: '#475569' }}>—</span>
                    )}
                  </td>
                  <td style={{ fontSize: 12, color: '#94a3b8' }}>
                    {t.row_count != null ? t.row_count.toLocaleString() : '—'}
                  </td>
                  <td style={{ fontSize: 12, color: '#94a3b8' }}>
                    {t.column_count != null ? t.column_count : '—'}
                  </td>
                  <td
                    style={{
                      fontSize: 12,
                      color: '#64748b',
                      maxWidth: 260,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                    title={t.description}
                  >
                    {t.description || '—'}
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

// ══════════════════════════════════════════════════════════════════════════════
// Golden Questions tab
// ══════════════════════════════════════════════════════════════════════════════
function QuestionsTab({
  connectionId,
  connectionName,
  dialect,
}: {
  connectionId: number;
  connectionName: string;
  dialect?: string;
}) {
  const { message } = App.useApp();
  const [search, setSearch] = useState('');
  const [diffFilter, setDiffFilter] = useState('all');
  const [showForm, setShowForm] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // Form state
  const [newQ, setNewQ] = useState('');
  const [newSql, setNewSql] = useState('');
  const [newDiff, setNewDiff] = useState('simple');
  const [newType, setNewType] = useState('count');

  const { data: questions = [], isLoading } = useConnectionQuestions(connectionId);
  const createMut = useCreateConnectionQuestion();
  const deleteMut = useDeleteConnectionQuestion();

  const filtered = useMemo(() => {
    return questions.filter((q: any) => {
      const matchSearch =
        !search.trim() ||
        q.question?.toLowerCase().includes(search.toLowerCase()) ||
        q.expected_sql?.toLowerCase().includes(search.toLowerCase());
      const matchDiff = diffFilter === 'all' || q.difficulty === diffFilter;
      return matchSearch && matchDiff;
    });
  }, [questions, search, diffFilter]);

  const handleCopy = (id: string, sql: string) => {
    navigator.clipboard.writeText(sql);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleAdd = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newQ.trim() || !newSql.trim()) {
      message.error('Please fill in both the question and the expected SQL.');
      return;
    }
    createMut.mutate(
      {
        connectionId,
        payload: {
          question: newQ.trim(),
          expected_sql: newSql.trim(),
          difficulty: newDiff,
          question_type: newType,
        },
      },
      {
        onSuccess: () => {
          message.success('Golden question added!');
          setNewQ('');
          setNewSql('');
          setShowForm(false);
        },
        onError: (err: any) => {
          message.error(err?.response?.data?.detail || 'Failed to create question.');
        },
      },
    );
  };

  const handleDelete = (qid: string) => {
    Modal.confirm({
      title: 'Delete this golden question?',
      okText: 'Delete',
      okType: 'danger',
      onOk: () =>
        deleteMut.mutate(
          { connectionId, questionId: qid },
          {
            onSuccess: () => message.success('Deleted.'),
            onError: () => message.error('Failed to delete.'),
          },
        ),
    });
  };

  return (
    <div className="conn-tab-body">
      {/* Toolbar */}
      <div className="conn-tab-toolbar">
        <div className="conn-search-wrap">
          <Search size={14} />
          <input
            type="text"
            placeholder="Search questions or SQL…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search && (
            <button className="conn-clear-btn" onClick={() => setSearch('')}>
              <X size={13} />
            </button>
          )}
        </div>
        <Select
          value={diffFilter}
          onChange={setDiffFilter}
          size="small"
          style={{ width: 145 }}
          options={[
            { value: 'all', label: 'All Difficulties' },
            { value: 'simple', label: 'Simple' },
            { value: 'moderate', label: 'Moderate' },
            { value: 'challenging', label: 'Challenging' },
          ]}
        />
        <span className="conn-count-badge">
          {questions.length} question{questions.length !== 1 ? 's' : ''}
        </span>
        <button
          className="btn btn--primary btn--sm"
          onClick={() => setShowForm(!showForm)}
          style={{ display: 'flex', alignItems: 'center', gap: 5 }}
        >
          {showForm ? <X size={13} /> : <Plus size={13} />}
          {showForm ? 'Cancel' : 'Add Question'}
        </button>
      </div>

      {/* Add form */}
      {showForm && (
        <form className="add-question-box" onSubmit={handleAdd}>
          <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--accent)' }}>
            New Golden Question — {connectionName}
          </div>

          <input
            type="text"
            className="form-input"
            placeholder="Natural language question…"
            value={newQ}
            onChange={(e) => setNewQ(e.target.value)}
          />

          <textarea
            className="form-input"
            rows={3}
            placeholder={`Expected ${dialect?.toUpperCase() || 'SQL'} query…`}
            value={newSql}
            onChange={(e) => setNewSql(e.target.value)}
            style={{ fontFamily: 'monospace', fontSize: 12.5 }}
          />

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              flexWrap: 'wrap',
              justifyContent: 'space-between',
            }}
          >
            <div style={{ display: 'flex', gap: 10 }}>
              <div>
                <label
                  style={{
                    display: 'block',
                    fontSize: 11,
                    color: '#64748b',
                    marginBottom: 3,
                    fontWeight: 600,
                  }}
                >
                  Difficulty
                </label>
                <Select
                  value={newDiff}
                  onChange={setNewDiff}
                  size="small"
                  style={{ width: 120 }}
                  options={[
                    { value: 'simple', label: 'Simple' },
                    { value: 'moderate', label: 'Moderate' },
                    { value: 'challenging', label: 'Challenging' },
                  ]}
                />
              </div>
              <div>
                <label
                  style={{
                    display: 'block',
                    fontSize: 11,
                    color: '#64748b',
                    marginBottom: 3,
                    fontWeight: 600,
                  }}
                >
                  Type
                </label>
                <Select
                  value={newType}
                  onChange={setNewType}
                  size="small"
                  style={{ width: 120 }}
                  options={[
                    { value: 'count', label: 'Count' },
                    { value: 'aggregation', label: 'Aggregation' },
                    { value: 'filter', label: 'Filter' },
                    { value: 'join', label: 'Join' },
                    { value: 'groupby', label: 'GroupBy' },
                  ]}
                />
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setShowForm(false)}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn--primary btn--sm"
                disabled={createMut.isPending}
              >
                {createMut.isPending ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </form>
      )}

      {/* Questions list */}
      {isLoading ? (
        <div className="conn-center-msg">
          <Spinner size={22} />
          <span>Loading questions…</span>
        </div>
      ) : filtered.length === 0 ? (
        <Empty
          description={
            <div style={{ textAlign: 'center' }}>
              <p style={{ color: '#475569', fontWeight: 600, marginBottom: 4 }}>
                {questions.length === 0 ? 'No golden questions yet' : 'No matches'}
              </p>
              {questions.length === 0 && (
                <button
                  className="btn btn--primary btn--sm"
                  onClick={() => setShowForm(true)}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 8 }}
                >
                  <Plus size={13} /> Add First Question
                </button>
              )}
            </div>
          }
        />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {filtered.map((q: any) => (
            <div key={q.id} className="question-card">
              <div className="question-card-top">
                <span className="question-prompt">{q.question}</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                  <Tag color={DIFF_COLOR[q.difficulty as keyof typeof DIFF_COLOR] ?? 'blue'}>
                    {q.difficulty?.toUpperCase() || 'SIMPLE'}
                  </Tag>
                  {q.question_type && <Tag>{q.question_type}</Tag>}
                  <button
                    onClick={() => handleDelete(q.id)}
                    style={{
                      background: 'none',
                      border: 'none',
                      color: '#ef4444',
                      cursor: 'pointer',
                      padding: 4,
                      borderRadius: 4,
                    }}
                    title="Delete"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              <div className="question-sql-box">
                <code>{q.expected_sql}</code>
                <button
                  className="question-sql-copy-btn"
                  onClick={() => handleCopy(q.id, q.expected_sql)}
                >
                  {copiedId === q.id ? <Check size={12} /> : <Copy size={12} />}
                  {copiedId === q.id ? 'Copied' : 'Copy'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Evaluation tab
// ══════════════════════════════════════════════════════════════════════════════
function EvaluationTab({
  connectionId,
  connectionName,
  questionCount,
}: {
  connectionId: number;
  connectionName: string;
  questionCount: number;
}) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const triggerMut = useTriggerConnectionRun();

  const handleLaunch = () => {
    if (questionCount === 0) {
      message.warning('Add at least one golden question before running evaluation.');
      return;
    }
    triggerMut.mutate(
      { connectionId, connectionName },
      {
        onSuccess: (run: any) => {
          message.success(`Evaluation triggered! (Run ID: ${run.id?.slice(0, 8)}…)`);
          navigate('/evaluations');
        },
        onError: (err: any) => {
          message.error(err?.response?.data?.detail || 'Failed to trigger evaluation.');
        },
      },
    );
  };

  return (
    <div className="conn-tab-body">
      {/* Launch card */}
      <div className="eval-launch-card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div className="eval-launch-icon">
            <PlayCircle size={26} />
          </div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15 }}>Run Evaluation</div>
            <div style={{ fontSize: 13, color: '#64748b', marginTop: 2 }}>
              Evaluates all {questionCount} golden question{questionCount !== 1 ? 's' : ''} for{' '}
              <strong>{connectionName}</strong>
            </div>
          </div>
        </div>
        <button
          className="btn btn--primary"
          style={{ display: 'flex', alignItems: 'center', gap: 8 }}
          onClick={handleLaunch}
          disabled={triggerMut.isPending || questionCount === 0}
          title={questionCount === 0 ? 'Add golden questions first' : undefined}
        >
          {triggerMut.isPending ? (
            <>
              <Loader2 size={15} style={{ animation: 'spin 1s linear infinite' }} />
              Launching…
            </>
          ) : (
            <>
              <PlayCircle size={15} />
              Launch ({questionCount} Qs)
            </>
          )}
        </button>
      </div>

      {questionCount === 0 && (
        <div
          style={{
            padding: '12px 16px',
            borderRadius: 8,
            background: 'rgba(245,158,11,0.08)',
            border: '1px solid rgba(245,158,11,0.2)',
            fontSize: 13,
            color: '#f59e0b',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <ClipboardList size={15} /> No golden questions configured. Switch to the{' '}
          <strong>Golden Questions</strong> tab to add some before running evaluation.
        </div>
      )}

      {/* Run history */}
      <div style={{ marginTop: 4 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginBottom: 14,
            paddingBottom: 10,
            borderBottom: '1px solid var(--border, #1e293b)',
          }}
        >
          <ClipboardList size={16} style={{ color: 'var(--accent, #22c55e)' }} />
          <span style={{ fontWeight: 700, fontSize: 14 }}>Evaluation History</span>
        </div>
        <RunHistoryTable connectionId={connectionId} limit={30} />
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Main page
// ══════════════════════════════════════════════════════════════════════════════
export default function ConnectionsPage() {
  const { id } = useParams<{ id?: string }>();
  const navigate = useNavigate();

  const [searchConn, setSearchConn] = useState('');
  const [activeTab, setActiveTab] = useState<DetailTab>('tables');

  // Fetch connections
  const { data: connectionsData, isLoading: loadingConnections } = useQuery({
    queryKey: ['agent-connections'],
    queryFn: () => agentApi.listConnections(),
    staleTime: 60_000,
  });

  const connections: any[] = connectionsData?.connections || [];

  // Active connection derived from URL param
  const activeConnectionId = useMemo(() => {
    if (id) {
      const n = parseInt(id, 10);
      if (!isNaN(n)) return n;
    }
    return connections.length > 0 ? connections[0].connection_id : undefined;
  }, [id, connections]);

  const activeConnection = useMemo(
    () => connections.find((c) => c.connection_id === activeConnectionId),
    [connections, activeConnectionId],
  );

  // Connection question count for the badge & launch guard
  const { data: questions = [] } = useConnectionQuestions(activeConnectionId);

  // Filtered sidebar list
  const filteredConns = useMemo(() => {
    if (!searchConn.trim()) return connections;
    const term = searchConn.toLowerCase();
    return connections.filter(
      (c) =>
        c.name?.toLowerCase().includes(term) ||
        c.service_type?.toLowerCase().includes(term) ||
        c.dialect?.toLowerCase().includes(term) ||
        String(c.connection_id).includes(term),
    );
  }, [connections, searchConn]);

  // Table count for the active connection (from Jeen)
  const { data: connTables = [] } = useQuery({
    queryKey: ['jeen-connection-tables', activeConnectionId],
    queryFn: () => agentApi.listConnectionTables(activeConnectionId!),
    staleTime: 60_000,
    enabled: !!activeConnectionId,
  });
  const connTableCount = connTables.length;

  const TABS: { key: DetailTab; label: string; count?: number; icon: React.ReactNode }[] = [
    { key: 'tables', icon: <Database size={14} />, label: 'Tables', count: connTableCount },
    {
      key: 'questions',
      icon: <Code2 size={14} />,
      label: 'Golden Questions',
      count: questions.length,
    },
    { key: 'evaluation', icon: <PlayCircle size={14} />, label: 'Evaluation' },
  ];

  return (
    <div className="page connections-page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Catalog Connections</h1>
          <p className="page__subtitle">
            Browse Jeen MCP catalog connections, manage ground-truth golden questions, and run
            connection-scoped evaluations.
          </p>
        </div>
      </div>

      <div className="connections-layout">
        {/* ── Left panel: connection list ── */}
        <div className="connections-sidebar">
          <div className="connections-search">
            <Search size={14} />
            <input
              type="text"
              placeholder="Search connections…"
              value={searchConn}
              onChange={(e) => setSearchConn(e.target.value)}
            />
            {searchConn && (
              <button
                className="conn-clear-btn"
                style={{ position: 'absolute', right: 10 }}
                onClick={() => setSearchConn('')}
              >
                <X size={13} />
              </button>
            )}
          </div>

          <div className="connections-list">
            {loadingConnections ? (
              <div className="conn-center-msg" style={{ padding: 24 }}>
                <Spinner size={18} />
                <span>Loading…</span>
              </div>
            ) : filteredConns.length === 0 ? (
              <div style={{ padding: 20, textAlign: 'center', color: '#64748b', fontSize: 13 }}>
                No connections found
              </div>
            ) : (
              filteredConns.map((c) => {
                const isActive = c.connection_id === activeConnectionId;
                return (
                  <button
                    key={c.connection_id}
                    className={`connection-card-item${isActive ? ' connection-card-item--active' : ''}`}
                    onClick={() => {
                      navigate(`/connections/${c.connection_id}`);
                      setActiveTab('tables');
                    }}
                  >
                    <div className="connection-card-header">
                      <span className="connection-card-title" title={c.name}>
                        {c.name}
                      </span>
                      <code
                        style={{
                          fontSize: 10,
                          padding: '1px 5px',
                          borderRadius: 4,
                          background: 'rgba(99,102,241,0.12)',
                          color: '#6366f1',
                          fontWeight: 600,
                        }}
                      >
                        #{c.connection_id}
                      </code>
                    </div>
                    <div className="connection-card-meta">
                      <span className="connection-meta-pill">{c.service_type || 'Trino'}</span>
                      <span className="connection-meta-pill connection-meta-pill--primary">
                        {c.dialect || 'sql'}
                      </span>
                    </div>
                    {c.description && (
                      <div
                        style={{
                          fontSize: 11,
                          color: '#64748b',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          marginTop: 2,
                        }}
                      >
                        {c.description}
                      </div>
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* ── Right panel: connection details ── */}
        <div className="connection-detail-pane">
          {activeConnection ? (
            <>
              {/* Connection hero */}
              <div className="connection-hero-card">
                <div className="connection-hero-top">
                  <div className="connection-hero-title-group">
                    <div className="connection-hero-icon">
                      <Server size={20} />
                    </div>
                    <div>
                      <h2 className="connection-hero-title">{activeConnection.name}</h2>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          marginTop: 4,
                          flexWrap: 'wrap',
                        }}
                      >
                        <code
                          style={{
                            fontSize: 11,
                            padding: '2px 7px',
                            borderRadius: 4,
                            background: 'rgba(59,130,246,0.1)',
                            color: '#60a5fa',
                            fontWeight: 600,
                          }}
                        >
                          ID: {activeConnection.connection_id}
                        </code>
                        <span className="connection-meta-pill connection-meta-pill--primary">
                          {activeConnection.dialect || 'trino'}
                        </span>
                        <span className="connection-meta-pill">
                          {activeConnection.service_type || 'Trino'}
                        </span>
                        <span
                          className="connection-meta-pill"
                          style={{ color: '#10b981', borderColor: 'rgba(16,185,129,0.25)' }}
                        >
                          {connTableCount} tables
                        </span>
                        <span
                          className="connection-meta-pill"
                          style={{ color: '#f59e0b', borderColor: 'rgba(245,158,11,0.25)' }}
                        >
                          {questions.length} questions
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
                {activeConnection.ai_domain_context && (
                  <div className="connection-hero-desc">
                    <strong style={{ color: '#94a3b8', marginRight: 6 }}>AI Context:</strong>
                    {activeConnection.ai_domain_context}
                  </div>
                )}
              </div>

              {/* Tab bar */}
              <div className="conn-tab-bar">
                {TABS.map((t) => (
                  <button
                    key={t.key}
                    className={`conn-tab-btn${activeTab === t.key ? ' conn-tab-btn--active' : ''}`}
                    onClick={() => setActiveTab(t.key)}
                  >
                    {t.icon}
                    {t.label}
                    {t.count !== undefined && (
                      <span
                        className={`conn-tab-badge${activeTab === t.key ? ' conn-tab-badge--active' : ''}`}
                      >
                        {t.count}
                      </span>
                    )}
                  </button>
                ))}
              </div>

              {/* Tab content */}
              {activeTab === 'tables' && (
                <TablesTab connectionId={activeConnection.connection_id} />
              )}
              {activeTab === 'questions' && (
                <QuestionsTab
                  connectionId={activeConnection.connection_id}
                  connectionName={activeConnection.name}
                  dialect={activeConnection.dialect}
                />
              )}
              {activeTab === 'evaluation' && (
                <EvaluationTab
                  connectionId={activeConnection.connection_id}
                  connectionName={activeConnection.name}
                  questionCount={questions.length}
                />
              )}
            </>
          ) : (
            <div style={{ padding: '80px', textAlign: 'center', color: '#475569' }}>
              <Server size={44} style={{ opacity: 0.3, marginBottom: 12 }} />
              <div>Select a catalog connection to view details.</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
