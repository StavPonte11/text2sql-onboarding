
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useEffect, useRef } from "react";
import { Activity, AlertTriangle, CheckCircle, Clock, Database, TrendingUp, Zap, Bell, ShieldOff } from "lucide-react";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import { orchestrationApi } from "../api/orchestration";
import { MetricCard, StatusBadge, ScoreBar, AlertBanner, Spinner, EmptySlate } from "../components/common/EvalUI";
import { TrendChart } from "../components/monitoring/TrendChart";

dayjs.extend(relativeTime);

// ── System status badge ────────────────────────────────────────────────────────
function SystemStatusIndicator({ status }: { status: string }) {
  const map = {
    healthy:  { color: "#10b981", label: "All Systems Healthy",  pulse: "#10b981" },
    warning:  { color: "#f59e0b", label: "Warnings Detected",    pulse: "#f59e0b" },
    critical: { color: "#ef4444", label: "Critical Issues",       pulse: "#ef4444" },
  };
  const s = map[status as keyof typeof map] ?? map.healthy;

  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 8, padding: "6px 14px",
      background: `${s.color}12`, border: `1px solid ${s.color}30`, borderRadius: 20,
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: "50%", background: s.color, display: "inline-block",
        boxShadow: `0 0 0 0 ${s.pulse}`,
        animation: status !== "healthy" ? "pulse-ring 1.5s infinite" : "none",
      }} />
      <style>{`@keyframes pulse-ring { 0%,100%{box-shadow:0 0 0 0 ${s.pulse}60} 50%{box-shadow:0 0 0 6px ${s.pulse}00} }`}</style>
      <span style={{ fontSize: 12.5, fontWeight: 700, color: s.color }}>{s.label}</span>
    </div>
  );
}

// ── Failing table row ──────────────────────────────────────────────────────────
function FailingTableRow({ table }: { table: { table_id: string; table_name: string; avg_score: number; failure_rate: number } }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "10px 14px", borderBottom: "1px solid var(--border-subtle)",
    }}>
      <Database size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{table.table_name}</div>
        <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 2 }}>
          Failure rate: {Math.round(table.failure_rate * 100)}%
        </div>
      </div>
      <div style={{ width: 120 }}>
        <ScoreBar score={table.avg_score} height={5} showLabel />
      </div>
    </div>
  );
}

// ── Recent run row ─────────────────────────────────────────────────────────────
function RecentRunRow({ run }: {
  run: { run_id: string; table_id: string; score: number; status: string; created_at: string; regression_detected: boolean; triggered_by: string }
}) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "10px 14px", borderBottom: "1px solid var(--border-subtle)",
    }}>
      <div style={{
        width: 36, height: 36, borderRadius: 8, flexShrink: 0,
        background: run.score >= 0.5 ? "rgba(16,185,129,0.15)" : "rgba(239,68,68,0.15)",
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 13, fontWeight: 800,
        color: run.score >= 0.5 ? "#10b981" : "#ef4444",
      }}>
        {Math.round(run.score * 100)}%
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <code style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            {run.table_id ? `${run.table_id.slice(0, 10)}…` : "All prod tables"}
          </code>
          {run.regression_detected && (
            <span title="Regression"><AlertTriangle size={11} style={{ color: "#ef4444" }} /></span>
          )}
        </div>
        <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 2 }}>
          {run.triggered_by} · {dayjs(run.created_at).fromNow()}
        </div>
      </div>
      <StatusBadge status={run.status} size="sm" />
    </div>
  );
}

// ── ControlCenterPage ──────────────────────────────────────────────────────────
export function ControlCenterPage() {
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const noPermissions = searchParams.get('no_permissions') === '1';
  const noPermBannerRef = useRef<HTMLDivElement>(null);

  // Auto-dismiss the "no permissions" banner after 6 seconds
  useEffect(() => {
    if (!noPermissions) return;
    const timer = setTimeout(() => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.delete('no_permissions');
        return next;
      });
    }, 6000);
    return () => clearTimeout(timer);
  }, [noPermissions, setSearchParams]);

  const { data: health, isLoading: healthLoading } = useQuery({
    queryKey: ["system-health"],
    queryFn: orchestrationApi.getSystemHealth,
    refetchInterval: 20_000,
  });

  const { data: trends, isLoading: trendsLoading } = useQuery({
    queryKey: ["eval-trends", 14],
    queryFn: () => orchestrationApi.getTrends(14),
    refetchInterval: 60_000,
  });

  const { data: alerts = [] } = useQuery({
    queryKey: ["eval-alerts-unacked"],
    queryFn: () => orchestrationApi.listAlerts(false, 10),
    refetchInterval: 15_000,
  });

  const ackMut = useMutation({
    mutationFn: orchestrationApi.acknowledgeAlert,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval-alerts-unacked"] });
      qc.invalidateQueries({ queryKey: ["system-health"] });
    },
  });

  if (healthLoading) {
    return (
      <div className="page" style={{ display: "flex", justifyContent: "center", paddingTop: 80 }}>
        <Spinner size={36} />
      </div>
    );
  }

  const globalScore = health?.global_score ?? null;
  const globalPassRate = health?.global_pass_rate ?? null;

  return (
    <div className="page">
      {/* ── No-permissions popup ── */}
      {noPermissions && (
        <div
          ref={noPermBannerRef}
          style={{
            position: 'fixed', top: 20, right: 20, zIndex: 9999,
            display: 'flex', alignItems: 'flex-start', gap: 12,
            padding: '16px 20px',
            background: 'rgba(239,68,68,0.12)',
            border: '1px solid rgba(239,68,68,0.35)',
            borderRadius: 10,
            backdropFilter: 'blur(12px)',
            boxShadow: '0 8px 32px rgba(0,0,0,0.35)',
            maxWidth: 360,
            animation: 'slideInRight 0.3s ease',
          }}
        >
          <style>{`
            @keyframes slideInRight {
              from { opacity: 0; transform: translateX(40px); }
              to   { opacity: 1; transform: translateX(0); }
            }
          `}</style>
          <ShieldOff size={20} color="#ef4444" style={{ flexShrink: 0, marginTop: 1 }} />
          <div>
            <div style={{ fontWeight: 700, fontSize: 14, color: '#ef4444', marginBottom: 4 }}>
              Access Denied
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              You do not have permission to access the admin panel.
              Contact your administrator to request access.
            </div>
          </div>
          <button
            onClick={() => setSearchParams((prev) => {
              const next = new URLSearchParams(prev);
              next.delete('no_permissions');
              return next;
            })}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: 'var(--text-muted)', fontSize: 18, lineHeight: 1,
              padding: '0 0 0 8px', alignSelf: 'flex-start',
            }}
            title="Dismiss"
          >
            ×
          </button>
        </div>
      )}
      {/* ── Header ── */}
      <div className="page__header">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <h1 className="page__title" style={{ margin: 0 }}>Evaluation Control Center</h1>
            {health?.system_status && <SystemStatusIndicator status={health.system_status} />}
          </div>
          <p className="page__subtitle">
            Platform-wide evaluation monitoring · {health?.production_tables ?? 0} production tables · {health?.total_runs_today ?? 0} runs today
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {alerts.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 12px", background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.25)", borderRadius: 8 }}>
              <Bell size={13} style={{ color: "#ef4444" }} />
              <span style={{ fontSize: 12.5, fontWeight: 700, color: "#ef4444" }}>{alerts.length} active alert{alerts.length > 1 ? "s" : ""}</span>
            </div>
          )}
        </div>
      </div>

      {/* ── Section 1: System Health Metrics ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, marginBottom: 24 }}>
        <MetricCard
          label="Global Score"
          value={globalScore !== null ? `${Math.round(globalScore * 100)}%` : "—"}
          color={globalScore !== null ? (globalScore >= 0.5 ? "#10b981" : "#ef4444") : "var(--text-muted)"}
          icon={<TrendingUp size={16} />}
          sub="Avg across all completed runs"
        />
        <MetricCard
          label="Pass Rate"
          value={globalPassRate !== null ? `${Math.round(globalPassRate * 100)}%` : "—"}
          color={globalPassRate !== null ? (globalPassRate >= 0.5 ? "#10b981" : "#ef4444") : "var(--text-muted)"}
          icon={<CheckCircle size={16} />}
          sub="Questions passing across all runs"
        />
        <MetricCard
          label="Active Alerts"
          value={health?.active_alerts ?? 0}
          color={health?.critical_alerts ? "#ef4444" : health?.active_alerts ? "#f59e0b" : "#10b981"}
          icon={<AlertTriangle size={16} />}
          sub={health?.critical_alerts ? `${health.critical_alerts} critical` : "No critical alerts"}
        />
        <MetricCard
          label="Last Evaluation"
          value={health?.last_evaluation ? dayjs(health.last_evaluation).fromNow() : "—"}
          color="var(--accent-hover)"
          icon={<Clock size={16} />}
          sub={health?.last_evaluation ? dayjs(health.last_evaluation).format("MMM D, HH:mm") : "No runs yet"}
        />
      </div>

      {/* ── Main grid: Trend + Recent Runs ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 380px", gap: 18, marginBottom: 18 }}>
        {/* Trend chart */}
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: "var(--text-primary)" }}>Score Trend (14 days)</div>
              <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                {trends?.total_runs ?? 0} runs · Purple = avg score, Green = pass rate
              </div>
            </div>
            <Activity size={16} style={{ color: "var(--accent)", opacity: 0.7 }} />
          </div>
          {trendsLoading
            ? <div style={{ height: 220, display: "flex", alignItems: "center", justifyContent: "center" }}><Spinner /></div>
            : <TrendChart data={trends?.daily ?? []} height={220} />
          }
        </div>

        {/* Recent runs */}
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>Recent Runs</div>
            <Zap size={14} style={{ color: "var(--accent)", opacity: 0.7 }} />
          </div>
          {!health?.recent_runs?.length ? (
            <EmptySlate icon={<Activity size={28} />} title="No runs yet" sub="Trigger an evaluation to see history" />
          ) : (
            health.recent_runs.map(r => <RecentRunRow key={r.run_id} run={r} />)
          )}
        </div>
      </div>

      {/* ── Second row: Alerts + Failing Tables ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
        {/* Alert panel */}
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)", display: "flex", alignItems: "center", gap: 6 }}>
              <Bell size={14} style={{ color: alerts.length > 0 ? "#ef4444" : "var(--text-muted)" }} />
              Alerts Panel
            </div>
            {alerts.length > 0 && (
              <span style={{ fontSize: 11.5, color: "#ef4444", fontWeight: 700, background: "rgba(239,68,68,0.1)", padding: "2px 8px", borderRadius: 10 }}>
                {alerts.length} unresolved
              </span>
            )}
          </div>
          <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8, maxHeight: 300, overflowY: "auto" }}>
            {!alerts.length ? (
              <EmptySlate icon={<CheckCircle size={28} />} title="No active alerts" sub="System is running smoothly" />
            ) : (
              alerts.map(a => (
                <AlertBanner
                  key={a.id}
                  type={a.alert_type}
                  severity={a.severity}
                  message={a.message}
                  onAck={() => ackMut.mutate(a.id)}
                />
              ))
            )}
          </div>
        </div>

        {/* Failing tables */}
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 6 }}>
            <AlertTriangle size={14} style={{ color: "#f59e0b" }} />
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>Top Failing Tables</div>
          </div>
          {!health?.top_failing_tables?.length ? (
            <EmptySlate icon={<CheckCircle size={28} />} title="All tables healthy" sub="No tables below score threshold" />
          ) : (
            health.top_failing_tables.map(t => <FailingTableRow key={t.table_id} table={t} />)
          )}
        </div>
      </div>
    </div>
  );
}
