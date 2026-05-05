import { Check } from "lucide-react";
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from "recharts";
import type { DailyTrend, TrendPoint } from "../../api/orchestration";

// ── Shared tooltip style ───────────────────────────────────────────────────────
const tooltipStyle = {
  contentStyle: {
    background: "#161d35",
    border: "1px solid #1e2a45",
    borderRadius: 8,
    fontSize: 12,
    color: "#e2e8f0",
    boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
  },
  labelStyle: { color: "#94a3b8", fontWeight: 600, marginBottom: 4 },
};

// ── Score + Pass Rate dual-area chart ─────────────────────────────────────────
interface TrendChartProps {
  data: DailyTrend[];
  height?: number;
}

export function TrendChart({ data, height = 220 }: TrendChartProps) {
  if (!data.length) return (
    <div style={{ height, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: 13 }}>
      No trend data yet — run evaluations to see trends.
    </div>
  );

  const formatted = data.map(d => ({
    ...d,
    score_pct: Math.round(d.avg_score * 100),
    pass_pct: Math.round(d.avg_pass_rate * 100),
    label: d.date.slice(5),   // MM-DD
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={formatted} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
        <defs>
          <linearGradient id="scoreGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="passGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#10b981" stopOpacity={0.25} />
            <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
        <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#475569" }} axisLine={false} tickLine={false} />
        <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: "#475569" }} axisLine={false} tickLine={false} tickFormatter={v => `${v}%`} />
        <Tooltip
          {...tooltipStyle}
          formatter={(value: number, name: string) => [`${value}%`, name === "score_pct" ? "Avg Score" : "Pass Rate"]}
          labelFormatter={l => `Date: ${l}`}
        />
        <ReferenceLine y={90} stroke="#10b981" strokeDasharray="4 4" strokeOpacity={0.4} label={{ value: "90%", fill: "#10b981", fontSize: 10, position: "right" }} />
        <ReferenceLine y={80} stroke="#f59e0b" strokeDasharray="4 4" strokeOpacity={0.4} label={{ value: "80%", fill: "#f59e0b", fontSize: 10, position: "right" }} />
        <Area type="monotone" dataKey="score_pct" stroke="#6366f1" fill="url(#scoreGrad)" strokeWidth={2.5} dot={false} name="score_pct" />
        <Area type="monotone" dataKey="pass_pct" stroke="#10b981" fill="url(#passGrad)" strokeWidth={2} dot={false} name="pass_pct" />
        <Legend formatter={n => n === "score_pct" ? "Avg Score" : "Pass Rate"} wrapperStyle={{ fontSize: 11, color: "#94a3b8" }} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ── Per-run score sparkline ────────────────────────────────────────────────────
interface RunSparklineProps {
  runs: TrendPoint[];
  height?: number;
}

export function RunSparkline({ runs, height = 140 }: RunSparklineProps) {
  if (!runs.length) return null;

  const formatted = runs.slice(-20).map(r => ({
    label: r.date.slice(5),
    score: Math.round(r.score * 100),
    pass: Math.round(r.pass_rate * 100),
    regression: r.regression_detected ? Math.round(r.score * 100) : null,
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={formatted} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
        <XAxis dataKey="label" tick={{ fontSize: 10, fill: "#475569" }} axisLine={false} tickLine={false} />
        <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: "#475569" }} axisLine={false} tickLine={false} tickFormatter={v => `${v}%`} />
        <Tooltip {...tooltipStyle} formatter={(v: number, name: string) => [`${v}%`, name === "score" ? "Score" : "Pass Rate"]} />
        <ReferenceLine y={90} stroke="#10b981" strokeDasharray="3 3" strokeOpacity={0.4} />
        <ReferenceLine y={80} stroke="#f59e0b" strokeDasharray="3 3" strokeOpacity={0.4} />
        <Line type="monotone" dataKey="score" stroke="#6366f1" strokeWidth={2} dot={{ r: 3, fill: "#6366f1" }} activeDot={{ r: 5 }} />
        <Line type="monotone" dataKey="pass" stroke="#10b981" strokeWidth={1.5} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ── Failure breakdown bar chart ────────────────────────────────────────────────
interface FailureBarChartProps {
  data: Record<string, number>;
  height?: number;
}

const FAILURE_COLORS: Record<string, string> = {
  wrong_table:     "#ef4444",
  wrong_join:      "#f59e0b",
  wrong_filter:    "#f97316",
  hallucination:   "#a855f7",
  execution_error: "#ef4444",
  empty_result_bug:"#64748b",
};

export function FailureBarChart({ data, height = 180 }: FailureBarChartProps) {
  const formatted = Object.entries(data)
    .filter(([, v]) => v > 0)
    .map(([key, value]) => ({
      name: key.replace(/_/g, " "),
      key,
      value,
      fill: FAILURE_COLORS[key] ?? "#6366f1",
    }));

  if (!formatted.length) return (
    <div style={{ height, display: "flex", alignItems: "center", justifyContent: "center", gap: 6, color: "var(--text-muted)", fontSize: 13 }}>
      <Check size={14} style={{ color: "#10b981" }} /> No failures detected
    </div>
  );

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={formatted} layout="vertical" margin={{ top: 0, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" horizontal={false} />
        <XAxis type="number" tick={{ fontSize: 10, fill: "#475569" }} axisLine={false} tickLine={false} />
        <YAxis dataKey="name" type="category" tick={{ fontSize: 11, fill: "#94a3b8" }} width={100} axisLine={false} tickLine={false} />
        <Tooltip {...tooltipStyle} formatter={(v: number) => [v, "Count"]} />
        <Bar dataKey="value" radius={[0, 4, 4, 0]}>
          {formatted.map((entry) => (
            <rect key={entry.key} fill={entry.fill} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Score comparison bar ───────────────────────────────────────────────────────
interface CompareBarProps {
  run1Score: number;
  run2Score: number;
  run1Label?: string;
  run2Label?: string;
  height?: number;
}

export function CompareBarChart({ run1Score, run2Score, run1Label = "Run 1", run2Label = "Run 2", height = 140 }: CompareBarProps) {
  const data = [
    { name: "Score", run1: Math.round(run1Score * 100), run2: Math.round(run2Score * 100) },
  ];

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
        <XAxis dataKey="name" tick={{ fontSize: 11, fill: "#475569" }} axisLine={false} tickLine={false} />
        <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: "#475569" }} axisLine={false} tickLine={false} tickFormatter={v => `${v}%`} />
        <Tooltip {...tooltipStyle} formatter={(v: number) => [`${v}%`]} />
        <Legend wrapperStyle={{ fontSize: 11, color: "#94a3b8" }} />
        <Bar dataKey="run1" name={run1Label} fill="#6366f1" radius={[4, 4, 0, 0]} />
        <Bar dataKey="run2" name={run2Label} fill="#10b981" radius={[4, 4, 0, 0]} />
        <ReferenceLine y={90} stroke="#10b981" strokeDasharray="4 4" strokeOpacity={0.5} />
        <ReferenceLine y={80} stroke="#f59e0b" strokeDasharray="4 4" strokeOpacity={0.5} />
      </BarChart>
    </ResponsiveContainer>
  );
}
