import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { ArrowDown, ArrowUp, BarChart2, GitCompare, Minus, Table2, TrendingUp } from 'lucide-react';

import { orchestrationApi } from '../api/orchestration';
import {
  EmptySlate,
  MetricCard,
  ScoreBar,
  SectionHeader,
  Spinner,
  StatusBadge,
} from '../components/common/EvalUI';
import { ComparisonView } from '../components/monitoring/ComparisonView';
import { FailureBarChart, RunSparkline, TrendChart } from '../components/monitoring/TrendChart';

type Tab = 'trends' | 'failure' | 'tables' | 'compare';

// ── Trend tab ──────────────────────────────────────────────────────────────────
function TrendTab() {
  const [days, setDays] = useState(30);

  const { data: trends, isLoading } = useQuery({
    queryKey: ['eval-trends', days],
    queryFn: () => orchestrationApi.getTrends(days),
    refetchInterval: 60_000,
  });

  // Aggregate failure types from all runs

  // Stats from trend data
  const totalRuns = trends?.total_runs ?? 0;
  const avgScore = trends?.daily.length
    ? trends.daily.reduce((s, d) => s + d.avg_score, 0) / trends.daily.length
    : null;
  const regressions = trends?.runs.filter((r) => r.regression_detected).length ?? 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Controls */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <SectionHeader
          title="Score & Pass Rate Trend"
          sub={`Last ${days} days · ${totalRuns} runs`}
        />
        <div style={{ display: 'flex', gap: 6 }}>
          {[7, 14, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`btn btn--sm ${days === d ? 'btn--primary' : 'btn--ghost'}`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* Summary metrics */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
        <MetricCard
          label="Avg Score"
          value={avgScore !== null ? `${Math.round(avgScore * 100)}%` : '—'}
          color={
            avgScore !== null ? (avgScore >= 0.5 ? '#10b981' : '#ef4444') : 'var(--text-muted)'
          }
        />
        <MetricCard label="Total Runs" value={totalRuns} color="var(--accent-hover)" />
        <MetricCard
          label="Regressions"
          value={regressions}
          color={regressions > 0 ? '#ef4444' : '#10b981'}
          sub={regressions > 0 ? 'Score drops detected' : 'No regressions'}
        />
      </div>

      {/* Area chart */}
      <div className="card">
        <div
          style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 16 }}
        >
          Average Score &amp; Pass Rate Over Time
        </div>
        {isLoading ? (
          <div
            style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          >
            <Spinner size={28} />
          </div>
        ) : (
          <TrendChart data={trends?.daily ?? []} height={240} />
        )}
      </div>

      {/* Per-run sparkline */}
      {trends?.runs && trends.runs.length > 0 && (
        <div className="card">
          <div
            style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}
          >
            Individual Run Scores
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14 }}>
            Last 20 runs · ● purple = score, ○ green = pass rate · dashed lines = thresholds
          </div>
          <RunSparkline runs={trends.runs} height={160} />
        </div>
      )}
    </div>
  );
}

// ── Failure analysis tab ───────────────────────────────────────────────────────
function FailureTab() {
  const { data: tableData = [], isLoading } = useQuery({
    queryKey: ['table-analytics'],
    queryFn: orchestrationApi.getTableAnalytics,
  });

  // Aggregate failure counts across all tables
  const totalFailures = tableData.reduce(
    (acc, t) => {
      Object.entries(t.failure_breakdown ?? {}).forEach(([k, v]) => {
        acc[k] = (acc[k] ?? 0) + v;
      });
      return acc;
    },
    {} as Record<string, number>,
  );

  const totalQuestions = Object.values(totalFailures).reduce((s, v) => s + v, 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <SectionHeader
        title="Failure Analysis"
        sub="Aggregated failure breakdown across all tables and runs"
      />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* Aggregated chart */}
        <div className="card">
          <div
            style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}
          >
            Global Failure Distribution
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14 }}>
            {totalQuestions} total failure instances across all runs
          </div>
          {isLoading ? (
            <div
              style={{
                height: 200,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Spinner />
            </div>
          ) : (
            <FailureBarChart data={totalFailures} height={200} />
          )}
        </div>

        {/* Failure type legend */}
        <div className="card">
          <div
            style={{
              fontSize: 14,
              fontWeight: 700,
              color: 'var(--text-primary)',
              marginBottom: 14,
            }}
          >
            Failure Type Reference
          </div>
          {[
            {
              type: 'wrong_table',
              color: '#ef4444',
              desc: 'Agent selected wrong table for the query',
            },
            {
              type: 'wrong_join',
              color: '#f59e0b',
              desc: 'Incorrect JOIN condition or missing JOIN',
            },
            { type: 'wrong_filter', color: '#f97316', desc: 'WHERE clause filters are incorrect' },
            {
              type: 'hallucination',
              color: '#a855f7',
              desc: 'Agent referenced non-existent columns/tables',
            },
            {
              type: 'execution_error',
              color: '#ef4444',
              desc: 'SQL syntax or runtime execution error',
            },
          ].map(({ type, color, desc }) => (
            <div key={type} style={{ display: 'flex', gap: 10, marginBottom: 10 }}>
              <div
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: 2,
                  background: color,
                  flexShrink: 0,
                  marginTop: 4,
                }}
              />
              <div>
                <div
                  style={{
                    fontSize: 12.5,
                    fontWeight: 600,
                    color: 'var(--text-primary)',
                    textTransform: 'capitalize',
                  }}
                >
                  {type.replace(/_/g, ' ')}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{desc}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Per-table failure breakdown */}
      {tableData
        .filter((t) => Object.values(t.failure_breakdown).some((v) => v > 0))
        .map((t) => (
          <div key={t.table_id} className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
                  {t.table_name}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                  Score: {t.latest_score ? `${Math.round(t.latest_score * 100)}%` : '—'} ·{' '}
                  {t.run_count} runs
                </div>
              </div>
              <StatusBadge status={t.status} size="sm" />
            </div>
            <FailureBarChart data={t.failure_breakdown} height={120} />
          </div>
        ))}
    </div>
  );
}

// ── Table performance tab ──────────────────────────────────────────────────────
function TablesTab() {
  const { data: tableData = [], isLoading } = useQuery({
    queryKey: ['table-analytics'],
    queryFn: orchestrationApi.getTableAnalytics,
    refetchInterval: 30_000,
  });

  const TREND_ICON: Record<string, React.ReactNode> = {
    improving: <ArrowUp size={13} style={{ color: '#10b981' }} />,
    stable: <Minus size={13} style={{ color: '#6366f1' }} />,
    declining: <ArrowDown size={13} style={{ color: '#ef4444' }} />,
  };

  if (isLoading)
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
        <Spinner size={28} />
      </div>
    );

  if (!tableData.length)
    return (
      <EmptySlate
        icon={<Table2 size={36} />}
        title="No table data yet"
        sub="Run evaluations to see per-table analytics"
      />
    );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <SectionHeader
        title="Table Performance Ranking"
        sub={`${tableData.length} tables · sorted by score ascending (lowest first)`}
      />

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Table</th>
              <th>Status</th>
              <th>Latest Score</th>
              <th>Avg Score</th>
              <th>Pass Rate</th>
              <th>Runs</th>
              <th>Trend</th>
              <th>Last Run</th>
            </tr>
          </thead>
          <tbody>
            {tableData.map((t, i) => (
              <tr key={t.table_id}>
                <td style={{ fontSize: 12, color: 'var(--text-muted)', fontWeight: 700 }}>
                  {i + 1}
                </td>
                <td>
                  <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)' }}>
                    {t.table_name}
                  </div>
                  <div
                    style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'monospace' }}
                  >
                    {t.table_id.slice(0, 12)}…
                  </div>
                </td>
                <td>
                  <StatusBadge status={t.status} size="sm" />
                </td>
                <td>
                  {t.latest_score !== null ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 120 }}>
                      <span
                        style={{
                          fontSize: 15,
                          fontWeight: 800,
                          color: t.latest_score >= 0.5 ? '#10b981' : '#ef4444',
                        }}
                      >
                        {Math.round(t.latest_score * 100)}%
                      </span>
                    </div>
                  ) : (
                    <span style={{ color: 'var(--text-muted)' }}>—</span>
                  )}
                </td>
                <td style={{ width: 150 }}>
                  {t.avg_score !== null ? (
                    <ScoreBar score={t.avg_score} height={5} showLabel />
                  ) : (
                    '—'
                  )}
                </td>
                <td>
                  {t.pass_rate !== null ? (
                    <span
                      style={{
                        fontSize: 13,
                        fontWeight: 700,
                        color: t.pass_rate >= 0.5 ? '#10b981' : '#ef4444',
                      }}
                    >
                      {Math.round(t.pass_rate * 100)}%
                    </span>
                  ) : (
                    '—'
                  )}
                </td>
                <td style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{t.run_count}</td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    {TREND_ICON[t.trend]}
                    <StatusBadge status={t.trend} size="sm" />
                  </div>
                </td>
                <td style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {t.last_run_at ? dayjs(t.last_run_at).format('MMM D') : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── AnalyticsPage ──────────────────────────────────────────────────────────────
export function AnalyticsPage() {
  const [activeTab, setActiveTab] = useState<Tab>('trends');

  const TABS: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: 'trends', label: 'Trend Analysis', icon: <TrendingUp size={14} /> },
    { key: 'failure', label: 'Failure Analysis', icon: <BarChart2 size={14} /> },
    { key: 'tables', label: 'Table Performance', icon: <Table2 size={14} /> },
    { key: 'compare', label: 'Run Comparison', icon: <GitCompare size={14} /> },
  ];

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Analytics</h1>
          <p className="page__subtitle">
            Deep analysis · regression detection · table performance · run comparison
          </p>
        </div>
      </div>

      <div className="tabs" style={{ marginBottom: 24 }}>
        {TABS.map((tab) => (
          <button
            key={tab.key}
            className={`tab-item${activeTab === tab.key ? ' tab-item--active' : ''}`}
            onClick={() => setActiveTab(tab.key)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              background: 'none',
              border: 'none',
              cursor: 'pointer',
            }}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'trends' && <TrendTab />}
      {activeTab === 'failure' && <FailureTab />}
      {activeTab === 'tables' && <TablesTab />}
      {activeTab === 'compare' && <ComparisonView />}
    </div>
  );
}
