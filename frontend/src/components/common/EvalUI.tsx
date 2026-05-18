// ── StatusBadge ────────────────────────────────────────────────────────────────
interface StatusBadgeProps {
  status: string;
  size?: "sm" | "md";
}

const STATUS_MAP: Record<string, { color: string; bg: string; label: string }> = {
  completed:   { color: "#10b981", bg: "rgba(16,185,129,0.12)", label: "Completed" },
  running:     { color: "#f59e0b", bg: "rgba(245,158,11,0.12)", label: "Running" },
  failed:      { color: "#ef4444", bg: "rgba(239,68,68,0.12)", label: "Failed" },
  healthy:     { color: "#10b981", bg: "rgba(16,185,129,0.12)", label: "Healthy" },
  warning:     { color: "#f59e0b", bg: "rgba(245,158,11,0.12)", label: "Warning" },
  critical:    { color: "#ef4444", bg: "rgba(239,68,68,0.12)", label: "Critical" },
  info:        { color: "#6366f1", bg: "rgba(99,102,241,0.12)", label: "Info" },
  improving:   { color: "#10b981", bg: "rgba(16,185,129,0.12)", label: "Improving" },
  stable:      { color: "#6366f1", bg: "rgba(99,102,241,0.12)", label: "Stable" },
  declining:   { color: "#ef4444", bg: "rgba(239,68,68,0.12)", label: "Declining" },
  production:  { color: "#10b981", bg: "rgba(16,185,129,0.12)", label: "Production" },
  draft:       { color: "#64748b", bg: "rgba(100,116,139,0.12)", label: "Draft" },
  sandbox:     { color: "#f59e0b", bg: "rgba(245,158,11,0.12)", label: "Sandbox" },
  verified:    { color: "#3b82f6", bg: "rgba(59,130,246,0.12)", label: "Verified" },
  degraded:    { color: "#ef4444", bg: "rgba(239,68,68,0.12)", label: "Degraded" },
  regression:  { color: "#ef4444", bg: "rgba(239,68,68,0.12)", label: "Regression" },
  improvement: { color: "#10b981", bg: "rgba(16,185,129,0.12)", label: "Improvement" },
};

export function StatusBadge({ status, size = "md" }: StatusBadgeProps) {
  const s = STATUS_MAP[status] ?? { color: "#94a3b8", bg: "rgba(148,163,184,0.12)", label: status };
  const fs = size === "sm" ? "11px" : "12px";
  const px = size === "sm" ? "6px 10px" : "3px 10px";
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: px, borderRadius: 20, fontSize: fs, fontWeight: 600,
      color: s.color, background: s.bg, letterSpacing: "0.02em",
      textTransform: "capitalize",
    }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: s.color, display: "inline-block" }} />
      {s.label}
    </span>
  );
}

// ── ScoreBar ───────────────────────────────────────────────────────────────────
interface ScoreBarProps {
  score: number;
  height?: number;
  showLabel?: boolean;
}

export function ScoreBar({ score, height = 6, showLabel = false }: ScoreBarProps) {
  const pct = Math.round(score * 100);
  const color = score >= 0.5 ? "#10b981" : "#ef4444";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ flex: 1, height, background: "rgba(255,255,255,0.06)", borderRadius: height / 2, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: height / 2,
          transition: "width 0.4s ease", boxShadow: `0 0 8px ${color}60` }} />
      </div>
      {showLabel && (
        <span style={{ fontSize: 12, fontWeight: 700, color, minWidth: 36 }}>{pct}%</span>
      )}
    </div>
  );
}

// ── MetricCard ─────────────────────────────────────────────────────────────────
interface MetricCardProps {
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
  icon?: React.ReactNode;
  trend?: "up" | "down" | "stable";
  trendValue?: string;
  onClick?: () => void;
}

export function MetricCard({ label, value, sub, color = "var(--accent-hover)", icon, trend, trendValue, onClick }: MetricCardProps) {
  return (
    <div
      className="card"
      onClick={onClick}
      style={{
        cursor: onClick ? "pointer" : "default",
        transition: "all 0.2s ease",
        position: "relative",
        overflow: "hidden",
      }}
      onMouseEnter={e => { if (onClick) (e.currentTarget as HTMLDivElement).style.borderColor = "var(--accent-glow)"; }}
      onMouseLeave={e => { if (onClick) (e.currentTarget as HTMLDivElement).style.borderColor = "var(--border)"; }}
    >
      {/* Gradient accent top bar */}
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 2, background: `linear-gradient(90deg, ${color}, transparent)` }} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          {label}
        </div>
        {icon && <div style={{ color, opacity: 0.7 }}>{icon}</div>}
      </div>
      <div style={{ fontSize: 32, fontWeight: 800, color, lineHeight: 1.1, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
      {(sub || trendValue) && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 8 }}>
          {sub && <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{sub}</div>}
          {trendValue && (
            <span style={{
              fontSize: 11, fontWeight: 700,
              color: trend === "up" ? "#10b981" : trend === "down" ? "#ef4444" : "#94a3b8",
              background: trend === "up" ? "rgba(16,185,129,0.12)" : trend === "down" ? "rgba(239,68,68,0.12)" : "rgba(148,163,184,0.08)",
              padding: "2px 6px", borderRadius: 6,
            }}>
              {trend === "up" ? "↑" : trend === "down" ? "↓" : "→"} {trendValue}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ── AlertBanner ────────────────────────────────────────────────────────────────
interface AlertBannerProps {
  type: "regression" | "failed_run" | "low_score" | string;
  severity: "info" | "warning" | "critical";
  message: string;
  detail?: string;
  onAck?: () => void;
}

const ALERT_STYLES = {
  critical: { border: "#ef4444", bg: "rgba(239,68,68,0.08)", icon: "🔴" },
  warning:  { border: "#f59e0b", bg: "rgba(245,158,11,0.08)", icon: "🟡" },
  info:     { border: "#6366f1", bg: "rgba(99,102,241,0.08)", icon: "🔵" },
};

export function AlertBanner({ severity, message, onAck }: AlertBannerProps) {
  const style = ALERT_STYLES[severity] ?? ALERT_STYLES.info;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10,
      padding: "10px 14px", borderRadius: 8,
      background: style.bg, border: `1px solid ${style.border}30`,
      borderLeft: `3px solid ${style.border}`,
    }}>
      <span>{style.icon}</span>
      <span style={{ flex: 1, fontSize: 13, color: "var(--text-primary)", fontWeight: 500 }}>{message}</span>
      {onAck && (
        <button onClick={onAck} style={{
          fontSize: 11, color: "var(--text-muted)", background: "none", border: "1px solid var(--border)",
          borderRadius: 4, padding: "3px 8px", cursor: "pointer", fontWeight: 600,
        }}>
          Dismiss
        </button>
      )}
    </div>
  );
}

// ── SectionHeader ──────────────────────────────────────────────────────────────
interface SectionHeaderProps {
  title: string;
  sub?: string;
  action?: React.ReactNode;
}

export function SectionHeader({ title, sub, action }: SectionHeaderProps) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 16 }}>
      <div>
        <div style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>{title}</div>
        {sub && <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginTop: 2 }}>{sub}</div>}
      </div>
      {action}
    </div>
  );
}

// ── Spinner ────────────────────────────────────────────────────────────────────
export function Spinner({ size = 18, color = "var(--accent)" }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" style={{ animation: "spin 0.8s linear infinite" }}>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      <circle cx="12" cy="12" r="10" stroke={color} strokeWidth="3" strokeDasharray="31 11" strokeLinecap="round" />
    </svg>
  );
}

// ── EmptySlate ──────────────────────────────────────────────────────────────────
export function EmptySlate({ icon, title, sub }: { icon: React.ReactNode; title: string; sub?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "48px 24px", gap: 10, color: "var(--text-muted)", textAlign: "center" }}>
      <div style={{ opacity: 0.35, marginBottom: 4 }}>{icon}</div>
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-secondary)" }}>{title}</div>
      {sub && <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{sub}</div>}
    </div>
  );
}
