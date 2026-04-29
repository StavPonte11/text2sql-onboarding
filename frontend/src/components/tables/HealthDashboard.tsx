import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Activity, RefreshCw, AlertTriangle, CheckCircle, XCircle } from "lucide-react";
import { healthApi } from "../../api/client";
import type { TableHealth } from "../../types";

function ScoreGauge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = pct >= 75 ? "var(--status-production)" : pct >= 45 ? "var(--status-sandbox)" : "var(--status-degraded)";
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
      <div style={{
        width: 80, height: 80, borderRadius: "50%",
        background: `conic-gradient(${color} ${pct * 3.6}deg, var(--bg-base) 0deg)`,
        display: "flex", alignItems: "center", justifyContent: "center",
        boxShadow: `0 0 0 4px var(--bg-card)`,
        position: "relative",
      }}>
        <div style={{
          width: 60, height: 60, borderRadius: "50%",
          background: "var(--bg-card)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 18, fontWeight: 800, color,
        }}>
          {pct}
        </div>
      </div>
      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Health Score</span>
    </div>
  );
}

function FailureBar({ label, count, max }: { label: string; count: number; max: number }) {
  const pct = max > 0 ? (count / max) * 100 : 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
        <span style={{ color: "var(--text-muted)" }}>{label}</span>
        <span style={{ fontWeight: 600, color: count > 0 ? "var(--status-degraded)" : "var(--text-muted)" }}>{count}</span>
      </div>
      <div style={{ height: 6, background: "var(--bg-base)", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: "var(--status-degraded)", borderRadius: 3, transition: "width 0.4s" }} />
      </div>
    </div>
  );
}

export function HealthDashboard({ tableId }: { tableId: string }) {
  const qc = useQueryClient();
  const { data: health, isLoading, isError, refetch } = useQuery({
    queryKey: ["health", tableId],
    queryFn: () => healthApi.get(tableId),
  });

  const recompute = useMutation({
    mutationFn: () => healthApi.recompute(tableId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["health", tableId] }),
  });

  if (isLoading) return <div className="card"><div className="skeleton" style={{ height: 200 }} /></div>;
  if (isError || !health) return (
    <div className="card">
      <div className="empty-state">
        <Activity size={32} className="empty-state__icon" />
        <div className="empty-state__text">No health data yet</div>
        <div className="empty-state__sub">Run an evaluation to generate health scores.</div>
        <button className="btn btn--primary btn--sm" style={{ marginTop: 12 }} onClick={() => refetch()}>Retry</button>
      </div>
    </div>
  );

  const maxFailure = Math.max(health.failure_wrong_table, health.failure_wrong_sql, health.failure_empty_result, health.failure_execution_error, 1);
  const statusIcon = health.health_status === "good" ? <CheckCircle size={16} color="var(--status-production)" /> :
    health.health_status === "warning" ? <AlertTriangle size={16} color="var(--status-sandbox)" /> :
    <XCircle size={16} color="var(--status-degraded)" />;
  const statusColor = health.health_status === "good" ? "var(--status-production)" : health.health_status === "warning" ? "var(--status-sandbox)" : "var(--status-degraded)";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Header card */}
      <div className="card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <ScoreGauge score={health.health_score} />
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
              {statusIcon}
              <span style={{ fontWeight: 700, fontSize: 15, color: statusColor, textTransform: "capitalize" }}>
                {health.health_status}
              </span>
            </div>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Updated {new Date(health.updated_at).toLocaleString()}
            </div>
            {health.schema_drift_flag && (
              <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 6, fontSize: 12, color: "var(--status-degraded)" }}>
                <AlertTriangle size={12} /> Schema drift detected
              </div>
            )}
          </div>
        </div>
        <button className="btn btn--ghost btn--sm" style={{ display: "flex", alignItems: "center", gap: 6 }}
          onClick={() => recompute.mutate()} disabled={recompute.isPending}>
          <RefreshCw size={13} className={recompute.isPending ? "spin" : ""} />
          Recompute
        </button>
      </div>

      {/* Signal breakdown */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        {[
          { label: "Eval Success Rate", value: health.eval_success_rate != null ? `${Math.round(health.eval_success_rate * 100)}%` : "—", color: "var(--accent-hover)" },
          { label: "Feedback Ratio", value: health.feedback_ratio != null ? `${Math.round(health.feedback_ratio * 100)}% 👍` : "—", color: "var(--status-production)" },
          { label: "Data Quality", value: health.data_quality_score != null ? `${Math.round(health.data_quality_score * 100)}%` : "—", color: "var(--status-sandbox)" },
        ].map(({ label, value, color }) => (
          <div key={label} className="card" style={{ textAlign: "center" }}>
            <div style={{ fontSize: 22, fontWeight: 800, color }}>{value}</div>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Failure breakdown */}
      <div className="card">
        <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 16, color: "var(--text-secondary)" }}>
          Failure Intelligence
        </h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <FailureBar label="Wrong Table Selected" count={health.failure_wrong_table} max={maxFailure} />
          <FailureBar label="Wrong / Invalid SQL" count={health.failure_wrong_sql} max={maxFailure} />
          <FailureBar label="Empty Result" count={health.failure_empty_result} max={maxFailure} />
          <FailureBar label="Execution Error" count={health.failure_execution_error} max={maxFailure} />
        </div>
        {(health.failure_wrong_table + health.failure_wrong_sql + health.failure_empty_result + health.failure_execution_error) === 0 && (
          <div style={{ textAlign: "center", fontSize: 12, color: "var(--status-production)", marginTop: 12 }}>
            ✅ No failures recorded
          </div>
        )}
      </div>
    </div>
  );
}
