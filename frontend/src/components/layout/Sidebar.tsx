import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Database, FlaskConical, Activity, Shield, LayoutDashboard, BarChart2, ClipboardList } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { orchestrationApi } from "../../api/orchestration";

const NAV_GROUPS = [
  {
    label: "Overview",
    items: [
      { to: "/control-center", icon: LayoutDashboard, key: "nav.controlCenter", label: "Control Center" },
    ],
  },
  {
    label: "Evaluation",
    items: [
      { to: "/evaluations",    icon: ClipboardList,   key: "nav.evaluations",   label: "Evaluations" },
      { to: "/analytics",      icon: BarChart2,        key: "nav.analytics",     label: "Analytics" },
    ],
  },
  {
    label: "Data",
    items: [
      { to: "/tables",         icon: Database,         key: "nav.tables",        label: "Tables" },
      { to: "/monitoring",     icon: Activity,         key: "nav.monitoring",    label: "Audit Log" },
      { to: "/permissions",    icon: Shield,           key: "nav.permissions",   label: "Permissions" },
    ],
  },
];

function AlertDot({ count }: { count: number }) {
  if (!count) return null;
  return (
    <span style={{
      marginLeft: "auto", minWidth: 18, height: 18,
      background: count > 0 ? "#ef4444" : "var(--accent-dim)",
      color: "#fff", borderRadius: 9, fontSize: 10.5, fontWeight: 700,
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      padding: "0 5px",
    }}>
      {count}
    </span>
  );
}

export function Sidebar() {
  const { t, i18n } = useTranslation();

  const { data: health } = useQuery({
    queryKey: ["system-health"],
    queryFn: orchestrationApi.getSystemHealth,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const activeAlerts = health?.active_alerts ?? 0;

  return (
    <aside className="layout__sidebar">
      <div className="sidebar__logo" style={{ padding: "16px 12px", display: "flex", alignItems: "center", gap: "10px", borderBottom: "1px solid var(--border-subtle)", marginBottom: "8px" }}>
        <img src="/jarvis-logo.png" alt="Jarvis Studio Logo" style={{ height: "32px", width: "auto" }} />
        <div>
          <div className="sidebar__logo-text" style={{ fontSize: "14px", fontWeight: "700", letterSpacing: "0.5px" }}>JARVIS</div>
          <div className="sidebar__logo-sub" style={{ fontSize: "11px", color: "var(--text-muted)", letterSpacing: "1px" }}>STUDIO</div>
        </div>
      </div>

      {/* System health mini-indicator */}
      {health && (
        <div style={{
          margin: "8px 12px 0",
          padding: "6px 10px",
          borderRadius: 6,
          background: health.system_status === "healthy" ? "rgba(16,185,129,0.08)"
            : health.system_status === "critical" ? "rgba(239,68,68,0.08)"
            : "rgba(245,158,11,0.08)",
          border: `1px solid ${health.system_status === "healthy" ? "rgba(16,185,129,0.2)"
            : health.system_status === "critical" ? "rgba(239,68,68,0.2)"
            : "rgba(245,158,11,0.2)"}`,
          display: "flex", alignItems: "center", gap: 6,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
            background: health.system_status === "healthy" ? "#10b981"
              : health.system_status === "critical" ? "#ef4444" : "#f59e0b",
          }} />
          <span style={{
            fontSize: 11, fontWeight: 600,
            color: health.system_status === "healthy" ? "#10b981"
              : health.system_status === "critical" ? "#ef4444" : "#f59e0b",
          }}>
            {health.system_status === "healthy" ? "All systems healthy"
              : health.system_status === "critical" ? "Critical alerts" : "Warnings active"}
          </span>
        </div>
      )}

      <nav className="sidebar__nav" style={{ paddingTop: 12 }}>
        {NAV_GROUPS.map(group => (
          <div key={group.label} style={{ marginBottom: 8 }}>
            <div style={{
              fontSize: 10.5, fontWeight: 700, color: "var(--text-muted)",
              textTransform: "uppercase", letterSpacing: "0.08em",
              padding: "6px 10px 4px",
            }}>
              {group.label}
            </div>
            {group.items.map(({ to, icon: Icon, key, label }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  `sidebar__nav-item${isActive ? " sidebar__nav-item--active" : ""}`
                }
              >
                <Icon size={15} />
                {t(key, label)}
                {to === "/control-center" && <AlertDot count={activeAlerts} />}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div style={{ padding: "10px 12px", borderTop: "1px solid var(--border-subtle)", margin: "4px 0 0" }}>
        <div style={{ fontSize: 10.5, color: "var(--text-muted)" }}>
          {health ? `${health.production_tables} prod · ${health.total_tables} total tables` : "Loading…"}
        </div>
      </div>
    </aside>
  );
}
