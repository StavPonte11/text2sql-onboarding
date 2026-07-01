import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  AlignLeft,
  BarChart2,
  ChevronDown,
  Layers,
  LayoutGrid,
  Link2,
  Maximize2,
  RefreshCw,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { profilingApi } from '../../api/client';

import type { ColumnProfile, CrossTableProfile, RowField } from '../../types';

// ── helpers ───────────────────────────────────────────────────────────────────
function pct(v?: number) {
  if (v === undefined || v === null) return '—';
  return `${(v * 100).toFixed(1)}%`;
}
function fmt(v?: number | null) {
  if (v === undefined || v === null) return '—';
  return v.toLocaleString();
}
function fmtDate(ts: any) {
  if (!ts) return '—';
  if (typeof ts === 'number') {
    let ms = ts;
    if (ts < 1e11) ms = ts * 1000;
    else if (ts > 1e14) ms = Math.floor(ts / 1000); // Microseconds
    const d = new Date(ms);
    if (!isNaN(d.getTime())) {
      return d.toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    }
    return String(ts);
  }

  const d = new Date(ts);
  if (!isNaN(d.getTime())) {
    return d.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
  return String(ts).split(' ')[0];
}
function fmtNum(n: number): string {
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (Math.abs(n) >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  if (Number.isInteger(n)) return String(n);
  const s = n.toPrecision(3);
  return s.replace(/\.?0+$/, '');
}
function buildNumericBins(
  topValues: { value: string | number; count: number }[],
  totalRows?: number,
  nullCount?: number,
  numBins = 8,
) {
  let nanCount = 0;
  const pairs: { v: number; count: number }[] = [];
  for (const tv of topValues) {
    const v = parseFloat(String(tv.value));
    if (isNaN(v)) nanCount += tv.count;
    else pairs.push({ v, count: tv.count });
  }
  pairs.sort((a, b) => a.v - b.v);

  let bins: any[] = [];
  if (pairs.length > 0) {
    const minV = pairs[0].v,
      maxV = pairs[pairs.length - 1].v;
    if (minV === maxV) {
      bins = [
        { label: fmtNum(minV), range: String(minV), count: pairs.reduce((s, x) => s + x.count, 0) },
      ];
    } else {
      const step = (maxV - minV) / numBins;
      const rawBins = Array.from({ length: numBins }, (_, i) => ({
        lo: minV + i * step,
        hi: minV + (i + 1) * step,
        count: 0,
      }));
      for (const { v, count } of pairs) {
        let idx = Math.floor((v - minV) / step);
        if (idx < 0) idx = 0;
        if (idx >= numBins) idx = numBins - 1;
        rawBins[idx].count += count;
      }
      bins = rawBins
        .filter((b: any) => b.count > 0)
        .map((b: any) => ({
          label: fmtNum(b.lo),
          range: `${fmtNum(b.lo)} – ${fmtNum(b.hi)}`,
          count: b.count,
        }));
    }
  }

  const binnedCount = pairs.reduce((s, x) => s + x.count, 0);
  const totalAvailable = binnedCount + nanCount + (nullCount || 0);

  if (totalRows && totalRows > totalAvailable) {
    bins.push({ label: 'Other', range: 'Other values', count: totalRows - totalAvailable });
  }
  if (nanCount > 0 || (nullCount && nullCount > 0)) {
    bins.push({ label: 'Unknown', range: 'Null / Unknown', count: nanCount + (nullCount || 0) });
  }

  return bins;
}

function buildDateBins(
  topValues: { value: string | number; count: number }[],
  totalRows?: number,
  nullCount?: number,
  numBins = 8,
) {
  let nanCount = 0;
  const pairs: { t: number; count: number }[] = [];
  for (const tv of topValues) {
    const t = new Date(String(tv.value)).getTime();
    if (isNaN(t)) nanCount += tv.count;
    else pairs.push({ t, count: tv.count });
  }
  pairs.sort((a, b) => a.t - b.t);

  let bins: any[] = [];
  if (pairs.length > 0) {
    const tMin = pairs[0].t,
      tMax = pairs[pairs.length - 1].t;
    if (tMin === tMax) {
      bins = [
        {
          label: fmtDate(tMin / 1000),
          range: fmtDate(tMin / 1000),
          count: pairs.reduce((s, x) => s + x.count, 0),
        },
      ];
    } else {
      const step = (tMax - tMin) / numBins;
      const rawBins = Array.from({ length: numBins }, (_, i) => ({
        lo: tMin + i * step,
        hi: tMin + (i + 1) * step,
        count: 0,
      }));
      for (const { t, count } of pairs) {
        let idx = Math.floor((t - tMin) / step);
        if (idx < 0) idx = 0;
        if (idx >= numBins) idx = numBins - 1;
        rawBins[idx].count += count;
      }
      bins = rawBins
        .filter((b: any) => b.count > 0)
        .map((b: any) => ({
          label: fmtDate(b.lo / 1000),
          range: `${fmtDate(b.lo / 1000)} – ${fmtDate(b.hi / 1000)}`,
          count: b.count,
        }));
    }
  }

  const binnedCount = pairs.reduce((s, x) => s + x.count, 0);
  const totalAvailable = binnedCount + nanCount + (nullCount || 0);

  if (totalRows && totalRows > totalAvailable) {
    bins.push({ label: 'Other', range: 'Other dates', count: totalRows - totalAvailable });
  }
  if (nanCount > 0 || (nullCount && nullCount > 0)) {
    bins.push({ label: 'Unknown', range: 'Null / Unknown', count: nanCount + (nullCount || 0) });
  }

  return bins;
}

const SEMANTIC_COLORS: Record<string, string> = {
  row: '#818cf8',
  categorical: '#34d399',
  continuous: '#60a5fa',
  time: '#f59e0b',
  geo: '#fb923c',
  complex: '#94a3b8',
};

// ── NestedFieldExplorer ────────────────────────────────────────────────────────
function NestedFieldExplorer({
  rootChildren,
  rootDataType,
}: {
  rootChildren: RowField[];
  rootDataType?: string;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const toggle = (i: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  const DOT_COLOR: Record<string, string> = {
    time: '#a78bfa',
    continuous: '#60a5fa',
    categorical: '#34d399',
    geo: '#f59e0b',
    boolean: '#ec4899',
    row: '#f97316',
    complex: '#6b7280',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {rootDataType && (
        <div
          style={{
            fontSize: 11,
            color: 'var(--text-muted)',
            fontFamily: 'monospace',
            marginBottom: 10,
            opacity: 0.8,
          }}
        >
          {rootDataType.length > 120 ? rootDataType.slice(0, 120) + '…' : rootDataType}
        </div>
      )}
      {rootChildren.length === 0 && (
        <div style={{ color: 'var(--text-muted)', fontStyle: 'italic', fontSize: 13 }}>
          No fields found at this level.
        </div>
      )}
      {rootChildren.map((f, i) => {
        const isExpanded = expanded.has(i);
        const isRow =
          f.semantic_type === 'row' ||
          f.semantic_type === 'complex' ||
          (f.stats?.children && f.stats.children.length > 0);
        const dotColor = DOT_COLOR[f.semantic_type ?? 'categorical'] ?? 'var(--accent-hover)';

        const mappedCol: ColumnProfile = {
          id: f.name,
          table_id: '',
          profile_id: '',
          column_name: f.name,
          data_type: f.data_type,
          semantic_type: f.semantic_type,
          null_count: f.null_count,
          null_rate: f.null_rate,
          distinct_count: f.distinct_count,
          min_value: f.min_value,
          max_value: f.max_value,
          avg_value: f.stats?.avg ?? null,
          median_value: f.stats?.median ?? null,
          top_values: f.top_values,
          is_categorical: f.semantic_type === 'categorical',
          is_geo: f.is_geo ?? false,
          is_time: f.is_time ?? f.semantic_type === 'time',
          stats_json: f.stats as any,
          created_at: '',
        };

        return (
          <div
            key={i}
            style={{
              borderLeft: '2px solid var(--border)',
              marginLeft: 8,
              borderRadius: '0 6px 6px 0',
              overflow: 'hidden',
              background: 'var(--bg-card)',
            }}
          >
            {/* Compact header – always visible */}
            <div
              onClick={() => toggle(i)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '10px 14px',
                cursor: 'pointer',
                userSelect: 'none',
                borderBottom: isExpanded ? '1px solid var(--border)' : 'none',
              }}
              onMouseEnter={(e) => {
                if (!isRow) (e.currentTarget as HTMLDivElement).style.background = 'var(--bg-base)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLDivElement).style.background = '';
              }}
            >
              {!isRow && (
                <ChevronDown
                  size={14}
                  color="var(--text-muted)"
                  style={{
                    transform: isExpanded ? 'rotate(0deg)' : 'rotate(-90deg)',
                    transition: 'transform 0.2s',
                    flexShrink: 0,
                  }}
                />
              )}
              {isRow && (
                <ChevronDown
                  size={14}
                  color="var(--text-muted)"
                  style={{
                    transform: isExpanded ? 'rotate(0deg)' : 'rotate(-90deg)',
                    transition: 'transform 0.2s',
                    flexShrink: 0,
                  }}
                />
              )}

              <div
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: dotColor,
                  flexShrink: 0,
                }}
              />

              <span
                style={{ fontWeight: 600, fontSize: 14, color: 'var(--text)', flex: '0 0 auto' }}
              >
                {f.name}
              </span>
              <span
                style={{
                  fontSize: 11,
                  fontFamily: 'monospace',
                  background: 'var(--bg-base)',
                  padding: '1px 6px',
                  borderRadius: 4,
                  color: 'var(--text-muted)',
                  border: '1px solid var(--border)',
                }}
              >
                {f.data_type}
              </span>

              {/* Mini stats inline when collapsed */}
              {!isExpanded && (
                <div
                  style={{
                    marginLeft: 'auto',
                    display: 'flex',
                    gap: 16,
                    fontSize: 12,
                    color: 'var(--text-muted)',
                  }}
                >
                  {isRow && <span>{(f.stats?.children ?? []).length} fields</span>}
                  {!isRow && f.null_rate !== undefined && (
                    <span>
                      <span
                        style={{
                          color:
                            f.null_rate > 0.1
                              ? 'var(--status-degraded)'
                              : 'var(--status-production)',
                        }}
                      >
                        {pct(f.null_rate)}
                      </span>{' '}
                      nulls
                    </span>
                  )}
                  {!isRow && f.distinct_count !== undefined && (
                    <span>{fmt(f.distinct_count)} unique</span>
                  )}
                  {!isRow &&
                    (f.semantic_type === 'time' || f.is_time) &&
                    f.min_value &&
                    f.max_value && (
                      <span>
                        {fmtDate(f.min_value)} → {fmtDate(f.max_value)}
                      </span>
                    )}
                  {!isRow && f.semantic_type === 'continuous' && f.min_value && f.max_value && (
                    <span>
                      {f.min_value} – {f.max_value}
                    </span>
                  )}
                  {!isRow &&
                    f.top_values &&
                    f.top_values.length > 0 &&
                    f.semantic_type === 'categorical' && (
                      <span
                        style={{
                          maxWidth: 160,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                        title={String(f.top_values[0].value)}
                      >
                        top: {String(f.top_values[0].value)}
                      </span>
                    )}
                </div>
              )}
            </div>

            {/* Full detail when expanded */}
            {isExpanded && !isRow && (
              <div style={{ padding: '0 16px 16px 16px' }}>
                <ColumnReportRow col={mappedCol} isNested={true} />
              </div>
            )}

            {/* ROW type: show children when expanded */}
            {isRow && isExpanded && (
              <div style={{ padding: '8px 8px 8px 16px' }}>
                <NestedFieldExplorer
                  rootChildren={(f.stats?.children ?? []) as RowField[]}
                  rootDataType={f.data_type}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── MiniMetric (Table Headers) ────────────────────────────────────────────────
function MiniMetric({ col, totalRows }: { col: ColumnProfile; totalRows?: number }) {
  const isRow =
    col.semantic_type === 'row' ||
    col.semantic_type === 'complex' ||
    col.data_type?.toLowerCase().includes('row') ||
    col.data_type?.toLowerCase().includes('array') ||
    col.data_type?.toLowerCase().includes('map') ||
    col.data_type?.toLowerCase().includes('json') ||
    (col.stats_json?.children && col.stats_json.children.length > 0);
  if (isRow)
    return (
      <div style={{ fontSize: 12, color: 'var(--text-muted)', fontStyle: 'italic', marginTop: 8 }}>
        Nested object
      </div>
    );

  // Time: sparkline area with min/max labels
  if (col.semantic_type === 'time' || col.is_time) {
    return (
      <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
        <div style={{ height: 24 }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={[{ v: 0 }, { v: 1 }]}>
              <YAxis hide />
              <Area
                type="monotone"
                dataKey="v"
                stroke="var(--accent-hover)"
                fill="var(--accent-hover)"
                fillOpacity={0.2}
                strokeWidth={2}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 2 }}>
          <span>{fmtDate(col.min_value)}</span>
          <span>{fmtDate(col.max_value)}</span>
        </div>
      </div>
    );
  }

  // Boolean: two-segment bar
  if (col.data_type?.toLowerCase() === 'boolean' && col.top_values) {
    const t = col.top_values.find((v) => String(v.value).toLowerCase() === 'true')?.count || 0;
    const f = col.top_values.find((v) => String(v.value).toLowerCase() === 'false')?.count || 0;
    const nulls = col.null_count || 0;
    const total = totalRows && totalRows > 0 ? totalRows : t + f + nulls;
    const otherCount = Math.max(0, total - t - f - nulls);

    if (total > 0)
      return (
        <div style={{ display: 'flex', gap: 2, height: 14, marginTop: 10 }}>
          <div
            style={{ flex: t, background: 'var(--status-production)', borderRadius: '2px 0 0 2px' }}
            title={`True: ${pct(t / total)}`}
          />
          <div
            style={{ flex: f, background: 'var(--status-degraded)' }}
            title={`False: ${pct(f / total)}`}
          />
          {(otherCount > 0 || nulls > 0) && (
            <div
              style={{
                flex: otherCount + nulls,
                background: 'var(--bg-base)',
                borderRadius: '0 2px 2px 0',
              }}
              title={`Other/Null: ${pct((otherCount + nulls) / total)}`}
            />
          )}
        </div>
      );
  }

  // Numeric: mini histogram bins
  const NUMERIC_DTYPES = [
    'integer',
    'bigint',
    'double',
    'real',
    'float',
    'decimal',
    'numeric',
    'smallint',
    'tinyint',
  ];
  if (
    col.semantic_type === 'continuous' &&
    NUMERIC_DTYPES.includes(col.data_type?.toLowerCase() || '')
  ) {
    const bins = col.stats_json?.histogram
      ? col.stats_json.histogram.map((b) => ({
          label: b.label,
          range: b.lo !== null && b.hi !== null ? `${fmtNum(b.lo)} – ${fmtNum(b.hi)}` : b.label,
          count: b.count,
        }))
      : col.top_values && col.top_values.length > 0
        ? buildNumericBins(col.top_values, totalRows, col.null_count, 6)
        : [
            { label: fmtNum(Number(col.min_value) || 0), range: '', count: 1 },
            {
              label: '',
              range: '',
              count: (Number(col.stats_json?.q25) || 0) - (Number(col.min_value) || 0),
            },
            {
              label: '',
              range: '',
              count: (Number(col.stats_json?.median) || 0) - (Number(col.stats_json?.q25) || 0),
            },
            {
              label: '',
              range: '',
              count: (Number(col.stats_json?.q75) || 0) - (Number(col.stats_json?.median) || 0),
            },
            {
              label: fmtNum(Number(col.max_value) || 0),
              range: '',
              count: (Number(col.max_value) || 0) - (Number(col.stats_json?.q75) || 0),
            },
          ].filter((b) => b.count > 0);
    return (
      <div style={{ height: 40, marginTop: 8 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={bins} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
            <XAxis
              dataKey="label"
              tick={{ fontSize: 8, fill: 'var(--text-muted)' }}
              tickLine={false}
              axisLine={false}
              interval={bins.length > 4 ? 'preserveStartEnd' : 0}
            />
            <Tooltip
              cursor={{ fill: 'var(--bg-base)' }}
              content={({ active, payload }) =>
                active && payload?.length ? (
                  <div
                    style={{
                      background: 'var(--bg-card)',
                      border: '1px solid var(--border)',
                      borderRadius: 6,
                      padding: '4px 8px',
                      fontSize: 11,
                    }}
                  >
                    <div style={{ color: 'var(--text-muted)' }}>
                      {payload[0].payload.range || payload[0].payload.label}
                    </div>
                    <div style={{ fontWeight: 600 }}>{fmt(payload[0].payload.count)}</div>
                  </div>
                ) : null
              }
            />
            <Bar
              dataKey="count"
              fill="var(--accent-hover)"
              radius={[2, 2, 0, 0]}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // Time: mini histogram bins
  if (col.semantic_type === 'time' || col.is_time) {
    const bins = col.stats_json?.histogram
      ? col.stats_json.histogram.map((b) => ({
          label: b.label,
          range: b.lo !== null && b.hi !== null ? `${b.label} – ${fmtDate(b.hi)}` : b.label,
          count: b.count,
        }))
      : col.top_values && col.top_values.length > 0
        ? buildDateBins(col.top_values, totalRows, col.null_count, 6)
        : [
            { label: fmtDate(col.min_value), range: '', count: 1 },
            { label: '', range: '', count: 1 },
            { label: '', range: '', count: 1 },
            { label: '', range: '', count: 1 },
            { label: fmtDate(col.max_value), range: '', count: 1 },
          ].filter((b) => b.label && b.label !== '—');

    return (
      <div style={{ height: 40, marginTop: 8 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={bins} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
            <XAxis
              dataKey="label"
              tick={{ fontSize: 8, fill: 'var(--text-muted)' }}
              tickLine={false}
              axisLine={false}
              interval={bins.length > 4 ? 'preserveStartEnd' : 0}
            />
            <Tooltip
              cursor={{ fill: 'var(--bg-base)' }}
              content={({ active, payload }) =>
                active && payload?.length ? (
                  <div
                    style={{
                      background: 'var(--bg-card)',
                      border: '1px solid var(--border)',
                      borderRadius: 6,
                      padding: '4px 8px',
                      fontSize: 11,
                    }}
                  >
                    <div style={{ color: 'var(--text-muted)' }}>
                      {payload[0].payload.range || payload[0].payload.label}
                    </div>
                    <div style={{ fontWeight: 600 }}>{fmt(payload[0].payload.count)}</div>
                  </div>
                ) : null
              }
            />
            <Bar
              dataKey="count"
              fill="var(--accent-hover)"
              radius={[2, 2, 0, 0]}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // Categorical: top 3 + Other mini bars
  if (col.top_values && col.top_values.length > 0) {
    const binnedCount = col.top_values.reduce((s, tv) => s + tv.count, 0);
    const nulls = col.null_count || 0;
    const total = totalRows && totalRows > 0 ? totalRows : binnedCount + nulls;
    const top3 = col.top_values.slice(0, 3);
    const otherCount = Math.max(0, total - top3.reduce((s, tv) => s + tv.count, 0));

    const bars = [
      ...top3.map((tv) => ({
        name: String(tv.value).length > 8 ? String(tv.value).slice(0, 7) + '…' : String(tv.value),
        count: tv.count,
        full: String(tv.value),
      })),
      ...(otherCount > 0 ? [{ name: `Other`, count: otherCount, full: `Other/Missing` }] : []),
    ];
    return (
      <div style={{ height: 44, marginTop: 8 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={bars} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
            <XAxis
              dataKey="name"
              tick={{ fontSize: 8, fill: 'var(--text-muted)' }}
              tickLine={false}
              axisLine={false}
              interval={0}
            />
            <Tooltip
              cursor={{ fill: 'var(--bg-base)' }}
              content={({ active, payload }) =>
                active && payload?.length ? (
                  <div
                    style={{
                      background: 'var(--bg-card)',
                      border: '1px solid var(--border)',
                      borderRadius: 6,
                      padding: '4px 8px',
                      fontSize: 11,
                    }}
                  >
                    <div style={{ color: 'var(--text-muted)' }}>{payload[0].payload.full}</div>
                    <div style={{ fontWeight: 600 }}>
                      {fmt(payload[0].payload.count)} (
                      {pct(payload[0].payload.count / (total || 1))})
                    </div>
                  </div>
                ) : null
              }
            />
            <Bar
              dataKey="count"
              fill="var(--accent-hover)"
              radius={[2, 2, 0, 0]}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // Varchar high-cardinality: unique count
  if (col.distinct_count) {
    return (
      <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', marginTop: 8 }}>
        {fmt(col.distinct_count)}
        <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--text-muted)', marginLeft: 4 }}>
          unique
        </span>
      </div>
    );
  }

  return <div style={{ height: 16, marginTop: 12 }} />;
}

function ColumnReportRow({
  col,
  isNested = false,
  totalRows,
}: {
  col: ColumnProfile;
  isNested?: boolean;
  totalRows?: number;
}) {
  const isRow =
    col.semantic_type === 'row' ||
    col.semantic_type === 'complex' ||
    col.data_type?.toLowerCase().includes('row') ||
    col.data_type?.toLowerCase().includes('array') ||
    col.data_type?.toLowerCase().includes('map') ||
    col.data_type?.toLowerCase().includes('json') ||
    (col.stats_json?.children && col.stats_json.children.length > 0);
  const rowChildren = col.stats_json?.children ?? [];
  const rowDataType = col.stats_json?.data_type as string | undefined;

  const missingPct = col.null_rate ?? 0;
  const validPct = 1 - missingPct;
  const mismatchPct = 0; // Conceptual placeholder

  return (
    <div
      style={{
        borderBottom: isNested ? 'none' : '1px solid var(--border)',
        padding: isNested ? '16px 0 16px 0' : '32px 0',
        marginLeft: isNested ? 16 : 0,
        paddingLeft: isNested ? 16 : 0,
        borderLeft: isNested ? '2px solid var(--border)' : 'none',
      }}
    >
      <div
        style={{
          display: 'flex',
          gap: 48,
        }}
      >
        {/* Left side: Identifiers + Primary Vis */}
        <div style={{ flex: '1 1 50%', minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
            {isRow && <Layers size={18} color={SEMANTIC_COLORS.row} />}
            <h3 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: 'var(--text)' }}>
              {col.column_name}
            </h3>
            <span
              style={{
                fontSize: 12,
                color: 'var(--text-muted)',
                fontFamily: 'monospace',
                background: 'var(--bg-base)',
                padding: '2px 6px',
                borderRadius: 4,
              }}
            >
              {col.data_type}
            </span>
          </div>

          {!isRow && (
            <div style={{ marginTop: 24 }}>
              {(() => {
                const NUMERIC_DTYPES = [
                  'integer',
                  'bigint',
                  'double',
                  'real',
                  'float',
                  'decimal',
                  'numeric',
                  'smallint',
                  'tinyint',
                ];
                const isNumeric =
                  col.semantic_type === 'continuous' &&
                  NUMERIC_DTYPES.includes(col.data_type?.toLowerCase() || '');
                const isTime = col.semantic_type === 'time' || col.is_time;
                const isCat =
                  col.semantic_type === 'categorical' ||
                  (col.top_values && col.top_values.length > 0 && !isNumeric && !isTime);

                const tooltipStyle = {
                  background: 'var(--bg-card)',
                  border: '1px solid var(--border)',
                  borderRadius: 8,
                  padding: '8px 12px',
                  fontSize: 12,
                };

                if (isNumeric) {
                  const hasDatums =
                    (col.stats_json?.histogram && col.stats_json.histogram.length > 0) ||
                    (col.top_values && col.top_values.length > 0);
                  const bins = col.stats_json?.histogram
                    ? col.stats_json.histogram.map((b) => ({
                        label: b.label,
                        range:
                          b.lo !== null && b.hi !== null
                            ? `${fmtNum(b.lo)} – ${fmtNum(b.hi)}`
                            : b.label,
                        count: b.count,
                      }))
                    : col.top_values && col.top_values.length > 0
                      ? buildNumericBins(col.top_values, totalRows, col.null_count)
                      : (() => {
                          const vMin = fmtNum(Number(col.min_value) || 0);
                          const v25 = fmtNum(Number(col.stats_json?.q25) || 0);
                          const v50 = fmtNum(Number(col.stats_json?.median) || 0);
                          const v75 = fmtNum(Number(col.stats_json?.q75) || 0);
                          const vMax = fmtNum(Number(col.max_value) || 0);
                          return [
                            { label: vMin, range: `${vMin} – ${v25}`, count: 1 },
                            { label: v25, range: `${v25} – ${v50}`, count: 1 },
                            { label: v50, range: `${v50} – ${v75}`, count: 1 },
                            { label: v75, range: `${v75} – ${vMax}`, count: 1 },
                          ].filter((b) => Number(b.label) || b.label !== '0');
                        })();
                  return (
                    <div style={{ height: 160 }}>
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={bins} margin={{ top: 8, right: 12, left: 0, bottom: 36 }}>
                          <XAxis
                            dataKey="label"
                            tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                            tickLine={false}
                            axisLine={{ stroke: 'var(--border)' }}
                            angle={-35}
                            textAnchor="end"
                            interval={0}
                          />
                          <YAxis
                            tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                            tickLine={false}
                            axisLine={{ stroke: 'var(--border)' }}
                            width={48}
                            tickFormatter={(v) => fmt(v) ?? ''}
                            hide={!hasDatums}
                          />
                          <Tooltip
                            cursor={{ fill: 'var(--bg-base)' }}
                            content={({ active, payload }) =>
                              active && payload?.length ? (
                                <div style={tooltipStyle}>
                                  <div style={{ color: 'var(--text-muted)' }}>
                                    {payload[0].payload.range}
                                  </div>
                                  {hasDatums && (
                                    <div style={{ fontWeight: 600, marginTop: 4 }}>
                                      {fmt(payload[0].payload.count)} values
                                    </div>
                                  )}
                                </div>
                              ) : null
                            }
                          />
                          <Bar
                            dataKey="count"
                            fill="var(--accent-hover)"
                            radius={[4, 4, 0, 0]}
                            isAnimationActive={false}
                            maxBarSize={40}
                          />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  );
                }

                if (isTime) {
                  const hasDatums =
                    (col.stats_json?.histogram && col.stats_json.histogram.length > 0) ||
                    (col.top_values && col.top_values.length > 0);
                  const bins = col.stats_json?.histogram
                    ? col.stats_json.histogram.map((b) => ({
                        label: b.label,
                        range:
                          b.lo !== null && b.hi !== null
                            ? `${b.label} – ${fmtDate(b.hi)}`
                            : b.label,
                        count: b.count,
                      }))
                    : col.top_values && col.top_values.length > 0
                      ? buildDateBins(col.top_values!, totalRows, col.null_count)
                      : (() => {
                          const vMin = fmtDate(col.min_value);
                          const v25 = fmtDate(col.stats_json?.q25);
                          const v50 = fmtDate(col.stats_json?.median);
                          const v75 = fmtDate(col.stats_json?.q75);
                          const vMax = fmtDate(col.max_value);
                          const pts = [
                            { label: vMin, range: `${vMin} – ${v25}`, count: 1 },
                            { label: v25, range: `${v25} – ${v50}`, count: 1 },
                            { label: v50, range: `${v50} – ${v75}`, count: 1 },
                            { label: v75, range: `${v75} – ${vMax}`, count: 1 },
                          ];
                          return pts.filter((p) => p.label && p.label !== '—');
                        })();
                  return (
                    <div style={{ height: 160 }}>
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={bins} margin={{ top: 8, right: 12, left: 0, bottom: 40 }}>
                          <XAxis
                            dataKey="label"
                            tick={{ fontSize: 9, fill: 'var(--text-muted)' }}
                            tickLine={false}
                            axisLine={{ stroke: 'var(--border)' }}
                            angle={-35}
                            textAnchor="end"
                            interval={0}
                          />
                          <YAxis
                            tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                            tickLine={false}
                            axisLine={{ stroke: 'var(--border)' }}
                            width={48}
                            tickFormatter={(v) => fmt(v) ?? ''}
                            hide={!hasDatums}
                          />
                          <Tooltip
                            cursor={{ fill: 'var(--bg-base)' }}
                            content={({ active, payload }) =>
                              active && payload?.length ? (
                                <div style={tooltipStyle}>
                                  <div style={{ color: 'var(--text-muted)' }}>
                                    {payload[0].payload.range}
                                  </div>
                                  {hasDatums && (
                                    <div style={{ fontWeight: 600, marginTop: 4 }}>
                                      {fmt(payload[0].payload.count)} values
                                    </div>
                                  )}
                                </div>
                              ) : null
                            }
                          />
                          <Bar
                            dataKey="count"
                            fill="var(--accent-hover)"
                            radius={[4, 4, 0, 0]}
                            isAnimationActive={false}
                            maxBarSize={40}
                          />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  );
                }

                if (isCat && col.top_values && col.top_values.length > 0) {
                  const binnedCount = col.top_values.reduce((acc, tv) => acc + tv.count, 0);
                  const nulls = col.null_count || 0;
                  const total = totalRows && totalRows > 0 ? totalRows : binnedCount + nulls;
                  const otherCount = Math.max(0, total - binnedCount - nulls);

                  const renderCats = [...col.top_values];
                  if (otherCount > 0) renderCats.push({ value: 'Other', count: otherCount });
                  if (nulls > 0) renderCats.push({ value: 'Null/Missing', count: nulls });

                  return (
                    <div
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 8,
                        maxHeight: 300,
                        overflowY: 'auto',
                        paddingRight: 8,
                      }}
                    >
                      {renderCats.map((tv, i) => {
                        const pctVal = tv.count / (total || 1);
                        return (
                          <div
                            key={i}
                            style={{ display: 'flex', alignItems: 'center', gap: 16, fontSize: 13 }}
                            title={`${tv.count} occurrences`}
                          >
                            <div
                              style={{
                                width: 140,
                                textOverflow: 'ellipsis',
                                overflow: 'hidden',
                                whiteSpace: 'nowrap',
                                fontWeight: 500,
                              }}
                            >
                              {String(tv.value)}
                            </div>
                            <div
                              style={{
                                flex: 1,
                                height: 10,
                                background: 'var(--bg-base)',
                                borderRadius: 5,
                                overflow: 'hidden',
                              }}
                            >
                              <div
                                style={{
                                  width: `${pctVal * 100}%`,
                                  height: '100%',
                                  background: 'var(--accent-hover)',
                                }}
                              />
                            </div>
                            <div
                              style={{ width: 48, textAlign: 'right', color: 'var(--text-muted)' }}
                            >
                              {pct(pctVal)}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  );
                }

                // Varchar / high-cardinality: show unique count
                return (
                  <div
                    style={{
                      fontSize: 40,
                      fontWeight: 700,
                      color: 'var(--text)',
                      display: 'flex',
                      alignItems: 'baseline',
                      gap: 10,
                      paddingTop: 24,
                    }}
                  >
                    {fmt(col.distinct_count)}{' '}
                    <span style={{ fontSize: 15, fontWeight: 400, color: 'var(--text-muted)' }}>
                      unique values
                    </span>
                  </div>
                );
              })()}
            </div>
          )}
        </div>

        {/* Right side: Stats */}
        <div style={{ flex: '0 0 320px', display: 'flex', flexDirection: 'column', gap: 28 }}>
          <div>
            <div
              style={{
                fontSize: 13,
                fontWeight: 600,
                marginBottom: 8,
                display: 'flex',
                justifyContent: 'space-between',
              }}
            >
              <span>Data Quality</span>
              <span style={{ color: 'var(--status-production)' }}>{pct(validPct)} Valid</span>
            </div>
            <div style={{ display: 'flex', height: 10, borderRadius: 5, overflow: 'hidden' }}>
              <div
                style={{ flex: validPct, background: 'var(--status-production)' }}
                title={`Valid: ${pct(validPct)}`}
              />
              <div
                style={{ flex: mismatchPct, background: 'var(--text-muted)' }}
                title={`Mismatched: ${pct(mismatchPct)}`}
              />
              <div
                style={{ flex: missingPct, background: 'var(--status-degraded)' }}
                title={`Missing: ${pct(missingPct)}`}
              />
            </div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                fontSize: 12,
                color: 'var(--text-muted)',
                marginTop: 6,
              }}
            >
              <span>Valid</span>
              <span>Missing ({pct(missingPct)})</span>
            </div>
          </div>

          {!isRow && (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: '12px 24px',
                fontSize: 14,
              }}
            >
              {col.semantic_type === 'continuous' ? (
                <>
                  <div style={{ color: 'var(--text-muted)' }}>Mean</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>
                    {col.avg_value != null ? col.avg_value.toFixed(2) : '—'}
                  </div>
                  <div style={{ color: 'var(--text-muted)' }}>Std Dev</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>
                    {col.stats_json?.stddev ? Number(col.stats_json.stddev).toFixed(2) : '—'}
                  </div>
                  <div style={{ color: 'var(--text-muted)' }}>Min</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>{col.min_value ?? '—'}</div>
                  <div style={{ color: 'var(--text-muted)' }}>25%</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>
                    {col.stats_json?.q25 ? Number(col.stats_json.q25).toFixed(2) : '—'}
                  </div>
                  <div style={{ color: 'var(--text-muted)' }}>50%</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>
                    {col.stats_json?.median != null ? String(col.stats_json.median) : '—'}
                  </div>
                  <div style={{ color: 'var(--text-muted)' }}>75%</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>
                    {col.stats_json?.q75 ? Number(col.stats_json.q75).toFixed(2) : '—'}
                  </div>
                  <div style={{ color: 'var(--text-muted)' }}>Max</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>{col.max_value ?? '—'}</div>
                </>
              ) : col.semantic_type === 'time' || col.is_time ? (
                <>
                  <div style={{ color: 'var(--text-muted)' }}>Min (Earliest)</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>
                    {fmtDate(col.min_value)}
                  </div>
                  <div style={{ color: 'var(--text-muted)' }}>25%</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>
                    {fmtDate(col.stats_json?.q25)}
                  </div>
                  <div style={{ color: 'var(--text-muted)' }}>50% (Median)</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>
                    {fmtDate(col.stats_json?.median)}
                  </div>
                  <div style={{ color: 'var(--text-muted)' }}>75%</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>
                    {fmtDate(col.stats_json?.q75)}
                  </div>
                  <div style={{ color: 'var(--text-muted)' }}>Max (Latest)</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>
                    {fmtDate(col.max_value)}
                  </div>
                  <div style={{ color: 'var(--text-muted)' }}>Std Dev (Secs)</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>
                    {col.stats_json?.stddev ? fmt(Math.round(Number(col.stats_json.stddev))) : '—'}
                  </div>
                </>
              ) : (
                <>
                  <div style={{ color: 'var(--text-muted)' }}>Unique</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>
                    {fmt(col.distinct_count)}
                  </div>
                  <div style={{ color: 'var(--text-muted)' }}>Most Common</div>
                  <div
                    style={{
                      fontWeight: 500,
                      textAlign: 'right',
                      textOverflow: 'ellipsis',
                      overflow: 'hidden',
                      whiteSpace: 'nowrap',
                    }}
                    title={col.top_values?.[0] ? String(col.top_values[0].value) : ''}
                  >
                    {col.top_values?.[0] ? String(col.top_values[0].value) : '—'}
                  </div>
                  <div style={{ color: 'var(--text-muted)' }}>Nulls</div>
                  <div style={{ fontWeight: 500, textAlign: 'right' }}>{fmt(col.null_count)}</div>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {isRow && (
        <div style={{ marginTop: 24, paddingLeft: 8 }}>
          {rowChildren.length > 0 ? (
            <NestedFieldExplorer rootChildren={rowChildren} rootDataType={rowDataType} />
          ) : (
            <i style={{ fontSize: 13, color: 'var(--text-muted)' }}>No inner fields</i>
          )}
        </div>
      )}
    </div>
  );
}

// ── CrossTableCard ────────────────────────────────────────────────────────────
function CrossTableCard({ profile }: { profile: CrossTableProfile }) {
  const isStrong = profile.match_strength === 'strong';
  return (
    <div
      style={{
        border: `1px solid ${isStrong ? 'var(--status-production)' : 'var(--border)'}`,
        borderRadius: 8,
        padding: '12px 14px',
        background: isStrong ? 'rgba(34,197,94,0.04)' : 'var(--bg-card)',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Link2 size={14} color={isStrong ? 'var(--status-production)' : 'var(--text-muted)'} />
          <code style={{ fontSize: 11, color: 'var(--text)' }}>
            {profile.target_table_id.slice(0, 8)}…
          </code>
        </div>
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            borderRadius: 4,
            padding: '2px 8px',
            background: isStrong ? 'var(--status-production)' : 'var(--border)',
            color: isStrong ? '#fff' : 'var(--text-muted)',
          }}
        >
          {isStrong ? 'Strong Match' : 'Weak Match'}
        </span>
      </div>
      {profile.join_suggestion && (
        <code
          style={{
            fontSize: 11,
            color: 'var(--text-muted)',
            background: 'var(--bg-base)',
            borderRadius: 4,
            padding: '4px 8px',
          }}
        >
          {profile.join_suggestion}
        </code>
      )}
      {profile.common_columns && profile.common_columns.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {profile.common_columns.map((c) => (
            <span
              key={c}
              className="badge badge--neutral"
              style={{ fontSize: 10, padding: '2px 6px' }}
            >
              {c}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main ProfilingTab ─────────────────────────────────────────────────────────
export function ProfilingTab({ tableId }: { tableId: string }) {
  const qc = useQueryClient();
  const [activeView, setActiveView] = useState<
    'detail' | 'column' | 'overview' | 'cross' | 'insights'
  >('detail');
  const [expandedRowKeys, setExpandedRowKeys] = useState<Set<string>>(new Set());

  const profileQ = useQuery({
    queryKey: ['profile', tableId],
    queryFn: () => profilingApi.get(tableId),
    retry: false,
  });

  const columnsQ = useQuery({
    queryKey: ['profile-columns', tableId],
    queryFn: () => profilingApi.getColumns(tableId),
  });

  const crossQ = useQuery({
    queryKey: ['cross-profiles', tableId],
    queryFn: () => profilingApi.getCrossProfiles(tableId),
  });

  const runMutation = useMutation({
    mutationFn: () => profilingApi.run(tableId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['profile', tableId] });
    },
  });

  const profile = profileQ.data;
  const isRunning = profile?.status === 'running' || runMutation.isPending;

  useEffect(() => {
    let poll: ReturnType<typeof setInterval>;
    if (profile?.status === 'running') {
      poll = setInterval(async () => {
        await qc.invalidateQueries({ queryKey: ['profile', tableId] });
        await qc.invalidateQueries({ queryKey: ['profile-columns', tableId] });
      }, 2000);
    }
    return () => clearInterval(poll);
  }, [profile?.status, tableId, qc]);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 24,
        maxWidth: '100%',
        margin: '0 auto',
      }}
    >
      {/* Header Area */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2
            style={{
              margin: 0,
              fontSize: 24,
              fontWeight: 700,
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              color: 'var(--text)',
            }}
          >
            <BarChart2 size={24} color="var(--accent-hover)" />
            Data Profiling
          </h2>
          {profile?.cached_until && (
            <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 6 }}>
              Cached until {new Date(profile.cached_until).toLocaleString()}
            </div>
          )}
        </div>
        <button
          className={`btn btn--primary${isRunning ? ' btn--loading' : ''}`}
          onClick={() => runMutation.mutate()}
          disabled={isRunning}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 16px',
            borderRadius: 8,
          }}
        >
          {isRunning ? (
            <div className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />
          ) : (
            <RefreshCw size={16} />
          )}
          {isRunning ? 'Running…' : profile ? 'Re-profile' : 'Run Profiling'}
        </button>
      </div>

      {!profile && !isRunning && (
        <div
          style={{
            padding: 64,
            textAlign: 'center',
            background: 'var(--bg-card)',
            borderRadius: 12,
            border: '1px dashed var(--border)',
          }}
        >
          <BarChart2 size={48} color="var(--border)" style={{ margin: '0 auto 16px' }} />
          <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>No profile yet</div>
          <div style={{ color: 'var(--text-muted)' }}>
            Click "Run Profiling" to analyze this table with full queries.
          </div>
        </div>
      )}

      {isRunning && profile?.row_count === undefined && (
        <div
          style={{
            padding: 64,
            textAlign: 'center',
            background: 'var(--bg-card)',
            borderRadius: 12,
            border: '1px solid var(--border)',
          }}
        >
          <div className="spinner" style={{ margin: '0 auto 16px', width: 32, height: 32 }} />
          <div style={{ fontSize: 15, color: 'var(--text)' }}>Profiling in progress...</div>
          <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 8 }}>
            Running full analytics queries via Trino
          </div>
        </div>
      )}

      {profile?.status === 'failed' && profile?.row_count === undefined && (
        <div
          style={{
            padding: 16,
            background: 'var(--bg-card)',
            border: '1px solid var(--status-degraded)',
            borderRadius: 8,
            display: 'flex',
            gap: 12,
            color: 'var(--status-degraded)',
            alignItems: 'center',
          }}
        >
          <AlertTriangle size={20} />
          <span style={{ fontSize: 14, fontWeight: 500 }}>
            Profiling job failed. Check Trino connection and try again.
          </span>
        </div>
      )}

      {profile?.row_count !== undefined && (
        <>
          {/* Dual-View Switcher Navigation + Extra Tabs */}
          <div
            style={{
              display: 'flex',
              gap: 32,
              borderBottom: '1px solid var(--border)',
              marginBottom: 8,
            }}
          >
            <div
              onClick={() => setActiveView('detail')}
              style={{
                padding: '16px 4px',
                cursor: 'pointer',
                fontWeight: activeView === 'detail' ? 600 : 500,
                color: activeView === 'detail' ? 'var(--text)' : 'var(--text-muted)',
                borderBottom:
                  activeView === 'detail'
                    ? '2px solid var(--accent-hover)'
                    : '2px solid transparent',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                transition: 'all 0.2s',
              }}
            >
              <LayoutGrid size={18} />
              Detail (Table)
            </div>
            <div
              onClick={() => setActiveView('column')}
              style={{
                padding: '16px 4px',
                cursor: 'pointer',
                fontWeight: activeView === 'column' ? 600 : 500,
                color: activeView === 'column' ? 'var(--text)' : 'var(--text-muted)',
                borderBottom:
                  activeView === 'column'
                    ? '2px solid var(--accent-hover)'
                    : '2px solid transparent',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                transition: 'all 0.2s',
              }}
            >
              <AlignLeft size={18} />
              Column (Report)
            </div>
            <div
              onClick={() => setActiveView('overview')}
              style={{
                padding: '16px 4px',
                cursor: 'pointer',
                fontWeight: activeView === 'overview' ? 600 : 500,
                color: activeView === 'overview' ? 'var(--text)' : 'var(--text-muted)',
                borderBottom:
                  activeView === 'overview'
                    ? '2px solid var(--accent-hover)'
                    : '2px solid transparent',
              }}
            >
              Overview
            </div>
            <div
              onClick={() => setActiveView('cross')}
              style={{
                padding: '16px 4px',
                cursor: 'pointer',
                fontWeight: activeView === 'cross' ? 600 : 500,
                color: activeView === 'cross' ? 'var(--text)' : 'var(--text-muted)',
                borderBottom:
                  activeView === 'cross'
                    ? '2px solid var(--accent-hover)'
                    : '2px solid transparent',
              }}
            >
              Cross-Table
            </div>
          </div>

          {/* View: Overview */}
          {activeView === 'overview' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
              {[
                { label: 'Row Count', value: fmt(profile.row_count), icon: '📊' },
                { label: 'Columns', value: fmt(profile.column_count), icon: '🗂' },
                { label: 'Avg Null Rate', value: pct(profile.null_rate_avg), icon: '🕳' },
                { label: 'Duplicate Rate', value: pct(profile.duplicate_rate), icon: '♻️' },
                {
                  label: 'Size',
                  value: profile.size_bytes ? `${(profile.size_bytes / 1e6).toFixed(1)} MB` : '—',
                  icon: '💾',
                },
                { label: 'Status', value: profile.status, icon: '✅' },
              ].map(({ label, value, icon }) => (
                <div
                  key={label}
                  style={{
                    background: 'var(--bg-card)',
                    border: '1px solid var(--border)',
                    borderRadius: 12,
                    textAlign: 'center',
                    padding: '16px 12px',
                  }}
                >
                  <div style={{ fontSize: 22, marginBottom: 4 }}>{icon}</div>
                  <div style={{ fontSize: 20, fontWeight: 800, color: 'var(--accent-hover)' }}>
                    {value}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                    {label}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* View: Detail (Data Table) */}
          {activeView === 'detail' && (
            <div
              style={{
                background: 'var(--bg-card)',
                borderRadius: 12,
                border: '1px solid var(--border)',
                overflow: 'hidden',
              }}
            >
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
                  <thead>
                    <tr>
                      {columnsQ.data?.map((col) => (
                        <th
                          key={col.column_name}
                          style={{
                            padding: '20px 24px',
                            borderBottom: '1px solid var(--border)',
                            borderRight: '1px solid var(--border)',
                            background: 'var(--bg-base)',
                            verticalAlign: 'top',
                            minWidth: 220,
                            maxWidth: 320,
                          }}
                        >
                          <div
                            style={{
                              fontWeight: 600,
                              fontSize: 14,
                              color: 'var(--text)',
                              marginBottom: 4,
                            }}
                          >
                            {col.column_name}
                          </div>
                          <div
                            style={{
                              fontSize: 12,
                              color: 'var(--text-muted)',
                              fontFamily: 'monospace',
                              marginBottom: 12,
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                            title={col.data_type ?? ''}
                          >
                            {col.data_type && col.data_type.length > 35
                              ? col.data_type.slice(0, 32) + '...'
                              : col.data_type}
                          </div>
                          <MiniMetric col={col} totalRows={profile.row_count} />
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {profile.sample_data?.length ? (
                      profile.sample_data.map((row, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                          {columnsQ.data?.map((col) => {
                            const val = row[col.column_name];
                            return (
                              <td
                                key={col.column_name}
                                style={{
                                  padding: '12px 24px',
                                  fontSize: 13,
                                  color: 'var(--text)',
                                  borderRight: '1px solid var(--border)',
                                  whiteSpace: 'nowrap',
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  maxWidth: 320,
                                  verticalAlign: 'top',
                                }}
                              >
                                {(() => {
                                  let parsedVal = val;
                                  if (
                                    typeof val === 'string' &&
                                    (val.startsWith('{') || val.startsWith('['))
                                  ) {
                                    try {
                                      parsedVal = JSON.parse(val);
                                    } catch (e) {
                                      // ignore parsing errors
                                    }
                                  } else if (
                                    typeof val === 'string' &&
                                    val.includes('=') &&
                                    val.startsWith('{')
                                  ) {
                                    // Hack for Trino ROW string format: {kind=active, count=5}
                                    try {
                                      const pairs = val.slice(1, -1).split(', ');
                                      parsedVal = Object.fromEntries(
                                        pairs.map((p) => p.split('=')),
                                      );
                                    } catch (e) {
                                      // ignore parsing errors
                                    }
                                  }

                                  if (parsedVal === null || parsedVal === undefined) {
                                    return <span style={{ color: 'var(--text-muted)' }}>null</span>;
                                  }
                                  if (typeof parsedVal === 'object') {
                                    return expandedRowKeys.has(`${i}-${col.column_name}`) ? (
                                      <div style={{ position: 'relative' }}>
                                        <button
                                          onClick={() => {
                                            const s = new Set(expandedRowKeys);
                                            s.delete(`${i}-${col.column_name}`);
                                            setExpandedRowKeys(s);
                                          }}
                                          style={{
                                            position: 'absolute',
                                            top: -8,
                                            right: -8,
                                            background: 'var(--bg-base)',
                                            border: '1px solid var(--border)',
                                            borderRadius: 4,
                                            cursor: 'pointer',
                                            padding: 2,
                                            display: 'flex',
                                            alignItems: 'center',
                                          }}
                                        >
                                          ✕
                                        </button>
                                        <div
                                          style={{
                                            maxHeight: 200,
                                            overflow: 'auto',
                                            background: 'var(--bg-base)',
                                            padding: 8,
                                            borderRadius: 6,
                                            border: '1px solid var(--border)',
                                          }}
                                        >
                                          <pre
                                            style={{
                                              fontSize: 11,
                                              margin: 0,
                                              fontFamily: 'monospace',
                                              whiteSpace: 'pre-wrap',
                                            }}
                                          >
                                            {JSON.stringify(parsedVal, null, 2)}
                                          </pre>
                                        </div>
                                      </div>
                                    ) : (
                                      <div
                                        style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                                      >
                                        <span
                                          style={{
                                            fontSize: 11,
                                            background: 'var(--bg-base)',
                                            padding: '2px 6px',
                                            borderRadius: 4,
                                            color: 'var(--text-muted)',
                                            border: '1px solid var(--border)',
                                          }}
                                        >
                                          [Nested Object]
                                        </span>
                                        <button
                                          onClick={() => {
                                            const s = new Set(expandedRowKeys);
                                            s.add(`${i}-${col.column_name}`);
                                            setExpandedRowKeys(s);
                                          }}
                                          style={{
                                            background: 'transparent',
                                            border: 'none',
                                            cursor: 'pointer',
                                            display: 'flex',
                                            alignItems: 'center',
                                            padding: 4,
                                            borderRadius: 4,
                                            color: 'var(--accent-hover)',
                                          }}
                                          title="Expand Object"
                                        >
                                          <Maximize2 size={14} />
                                        </button>
                                      </div>
                                    );
                                  }
                                  return String(parsedVal);
                                })()}
                              </td>
                            );
                          })}
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td
                          colSpan={columnsQ.data?.length || 1}
                          style={{ padding: 48, textAlign: 'center', color: 'var(--text-muted)' }}
                        >
                          No sample data available.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* View: Column (Linear Report) */}
          {activeView === 'column' && (
            <div
              style={{
                background: 'var(--bg-card)',
                padding: '0 32px',
                borderRadius: 12,
                border: '1px solid var(--border)',
              }}
            >
              {columnsQ.data?.map((col) => (
                <ColumnReportRow key={col.column_name} col={col} totalRows={profile.row_count} />
              ))}
              {!columnsQ.data?.length && (
                <div style={{ padding: 48, textAlign: 'center', color: 'var(--text-muted)' }}>
                  No columns profiled.
                </div>
              )}
            </div>
          )}

          {/* View: Cross Table */}
          {activeView === 'cross' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {crossQ.data && crossQ.data.length > 0 ? (
                crossQ.data.map((cp) => <CrossTableCard key={cp.id} profile={cp} />)
              ) : (
                <div style={{ padding: 48, textAlign: 'center', color: 'var(--text-muted)' }}>
                  No join candidates found.
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
