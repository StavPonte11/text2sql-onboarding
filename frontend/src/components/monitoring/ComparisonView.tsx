import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { GitCompare, ArrowRight, TrendingUp, TrendingDown, Minus, AlertTriangle } from "lucide-react";
import dayjs from "dayjs";
import { orchestrationApi, type CompareResult } from "../../api/orchestration";
import { StatusBadge, Spinner } from "../common/EvalUI";
import { CompareBarChart } from "./TrendChart";

// ── Run selector ───────────────────────────────────────────────────────────────
function RunSelector({ value, onChange, exclude }: { value: string; onChange: (id: string) => void; exclude?: string }) {
  const { data: runs = [] } = useQuery({
    queryKey: ["eval-runs-compare"],
    queryFn: () => orchestrationApi.listRuns({ limit: 100 }),
  });

  const available = exclude ? runs.filter(r => r.id !== exclude && r.status === "completed") : runs.filter(r => r.status === "completed");

  return (
    <select
      className="form-select"
      value={value}
      onChange={e => onChange(e.target.value)}
      style={{ fontSize: 13 }}
    >
      <option value="">— Select a run —</option>
      {available.map(r => (
        <option key={r.id} value={r.id}>
          {r.id.slice(0, 8)}… · {Math.round(r.score * 100)}% · {dayjs(r.created_at).format("MMM D HH:mm")} · {r.table_id.slice(0, 8)}
        </option>
      ))}
    </select>
  );
}

// ── Verdict banner ─────────────────────────────────────────────────────────────
function VerdictBanner({ verdict, scoreDelta }: { verdict: string; scoreDelta: number }) {
  const styles: Record<string, { color: string; bg: string; border: string; icon: React.ReactNode; text: string }> = {
    regression: {
      color: "#ef4444", bg: "rgba(239,68,68,0.08)", border: "rgba(239,68,68,0.3)",
      icon: <TrendingDown size={18} />,
      text: `Score dropped ${Math.abs(scoreDelta * 100).toFixed(1)}% — regression detected`,
    },
    improvement: {
      color: "#10b981", bg: "rgba(16,185,129,0.08)", border: "rgba(16,185,129,0.3)",
      icon: <TrendingUp size={18} />,
      text: `Score improved by ${Math.abs(scoreDelta * 100).toFixed(1)}%`,
    },
    stable: {
      color: "#6366f1", bg: "rgba(99,102,241,0.08)", border: "rgba(99,102,241,0.3)",
      icon: <Minus size={18} />,
      text: "Scores are stable — no significant change detected",
    },
  };
  const s = styles[verdict] ?? styles.stable;

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "14px 18px", borderRadius: 10,
      background: s.bg, border: `1px solid ${s.border}`,
    }}>
      <div style={{ color: s.color }}>{s.icon}</div>
      <div>
        <div style={{ fontSize: 14, fontWeight: 700, color: s.color, textTransform: "capitalize" }}>
          {verdict}
        </div>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 2 }}>{s.text}</div>
      </div>
    </div>
  );
}

// ── Side-by-side metric ────────────────────────────────────────────────────────
function SideBySide({
  label, val1, val2, format = (v: number) => `${Math.round(v * 100)}%`
}: {
  label: string; val1: number; val2: number; format?: (v: number) => string
}) {
  const delta = val2 - val1;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "10px 14px", borderRadius: 8, background: "var(--bg-base)",
      border: "1px solid var(--border-subtle)",
    }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4 }}>{label}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 18, fontWeight: 800, color: val1 >= 0.5 ? "#10b981" : "#ef4444" }}>
            {format(val1)}
          </span>
          <ArrowRight size={14} style={{ color: "var(--text-muted)" }} />
          <span style={{ fontSize: 18, fontWeight: 800, color: val2 >= 0.5 ? "#10b981" : "#ef4444" }}>
            {format(val2)}
          </span>
        </div>
      </div>
      <div style={{
        fontSize: 13, fontWeight: 700,
        color: delta > 0.01 ? "#10b981" : delta < -0.01 ? "#ef4444" : "#94a3b8",
        background: delta > 0.01 ? "rgba(16,185,129,0.12)" : delta < -0.01 ? "rgba(239,68,68,0.12)" : "rgba(148,163,184,0.08)",
        padding: "4px 8px", borderRadius: 6,
      }}>
        {delta > 0 ? "+" : ""}{Math.round(delta * 100)}%
      </div>
    </div>
  );
}

// ── Question diff table ────────────────────────────────────────────────────────
function QuestionDiffTable({
  items, title, color
}: {
  items: { question_id: string; run1_score: number; run2_score: number; delta: number }[];
  title: string;
  color: string;
}) {
  if (!items.length) return null;
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "10px 14px", background: `${color}14`, borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ width: 8, height: 8, borderRadius: "50%", background: color }} />
        <span style={{ fontSize: 13, fontWeight: 700, color }}>{title} ({items.length})</span>
      </div>
      <div style={{ maxHeight: 220, overflowY: "auto" }}>
        {items.map(item => (
          <div key={item.question_id} style={{
            display: "flex", alignItems: "center", gap: 10, padding: "8px 14px",
            borderBottom: "1px solid var(--border-subtle)",
          }}>
            <code style={{ fontSize: 11.5, color: "var(--text-muted)", flex: 1, fontFamily: "monospace" }}>
              {item.question_id.slice(0, 20)}…
            </code>
            <span style={{ fontSize: 12.5, color: "var(--text-secondary)" }}>{Math.round(item.run1_score * 100)}%</span>
            <ArrowRight size={12} style={{ color: "var(--text-muted)" }} />
            <span style={{ fontSize: 12.5, fontWeight: 700, color }}>{Math.round(item.run2_score * 100)}%</span>
            <span style={{ fontSize: 12, fontWeight: 700, color, minWidth: 40, textAlign: "right" }}>
              {item.delta > 0 ? "+" : ""}{Math.round(item.delta * 100)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── ComparisonView ─────────────────────────────────────────────────────────────
export function ComparisonView() {
  const [run1, setRun1] = useState("");
  const [run2, setRun2] = useState("");
  const [submitted, setSubmitted] = useState<{ r1: string; r2: string } | null>(null);

  const { data: result, isLoading, isError } = useQuery<CompareResult>({
    queryKey: ["compare-runs", submitted?.r1, submitted?.r2],
    queryFn: () => orchestrationApi.compareRuns(submitted!.r1, submitted!.r2),
    enabled: !!submitted,
  });

  return (
    <div>
      {/* Selector panel */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: "var(--text-primary)", marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
          <GitCompare size={16} style={{ color: "var(--accent)" }} />
          Compare Two Runs
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr auto 1fr auto", gap: 10, alignItems: "end" }}>
          <div>
            <label className="form-label">Baseline Run</label>
            <RunSelector value={run1} onChange={setRun1} exclude={run2} />
          </div>
          <div style={{ paddingBottom: 8, color: "var(--text-muted)" }}>
            <ArrowRight size={18} />
          </div>
          <div>
            <label className="form-label">Comparison Run</label>
            <RunSelector value={run2} onChange={setRun2} exclude={run1} />
          </div>
          <button
            className="btn btn--primary"
            disabled={!run1 || !run2 || run1 === run2}
            onClick={() => setSubmitted({ r1: run1, r2: run2 })}
            style={{ marginBottom: 1 }}
          >
            <GitCompare size={14} /> Compare
          </button>
        </div>
      </div>

      {isLoading && (
        <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
          <Spinner size={32} />
        </div>
      )}

      {isError && (
        <div className="card" style={{ textAlign: "center", color: "var(--status-degraded)", padding: 32 }}>
          Failed to compare runs. Please try again.
        </div>
      )}

      {result && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Verdict */}
          <VerdictBanner verdict={result.verdict} scoreDelta={result.score_delta} />

          {/* Run metadata */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {[
              { label: "Baseline Run", run: result.run1, prefix: "Run 1" },
              { label: "Comparison Run", run: result.run2, prefix: "Run 2" },
            ].map(({ label, run, prefix }) => (
              <div key={prefix} className="card">
                <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 6 }}>{label}</div>
                <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4, fontFamily: "monospace" }}>{run.id.slice(0, 16)}…</div>
                <div style={{ fontSize: 28, fontWeight: 800, color: run.score >= 0.5 ? "#10b981" : "#ef4444" }}>
                  {Math.round(run.score * 100)}%
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
                  {dayjs(run.created_at).format("MMM D, YYYY HH:mm")}
                </div>
              </div>
            ))}
          </div>

          {/* Score delta chart */}
          <div className="card">
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 14, color: "var(--text-primary)" }}>Score Comparison</div>
            <CompareBarChart
              run1Score={result.run1.score}
              run2Score={result.run2.score}
              run1Label="Baseline"
              run2Label="Comparison"
              height={140}
            />
          </div>

          {/* Key metrics side by side */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <SideBySide label="Overall Score" val1={result.run1.score} val2={result.run2.score} />
            <SideBySide label="Pass Rate" val1={result.run1.pass_rate} val2={result.run2.pass_rate} />
          </div>

          {/* Summary counts */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div className="card" style={{ textAlign: "center", border: "1px solid rgba(239,68,68,0.2)" }}>
              <div style={{ fontSize: 32, fontWeight: 800, color: "#ef4444" }}>{result.regression_count}</div>
              <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginTop: 4 }}>Regressions</div>
            </div>
            <div className="card" style={{ textAlign: "center", border: "1px solid rgba(16,185,129,0.2)" }}>
              <div style={{ fontSize: 32, fontWeight: 800, color: "#10b981" }}>{result.improvement_count}</div>
              <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginTop: 4 }}>Improvements</div>
            </div>
          </div>

          {/* Question diffs */}
          <QuestionDiffTable items={result.regressions} title="Regressions" color="#ef4444" />
          <QuestionDiffTable items={result.improvements} title="Improvements" color="#10b981" />
        </div>
      )}
    </div>
  );
}
