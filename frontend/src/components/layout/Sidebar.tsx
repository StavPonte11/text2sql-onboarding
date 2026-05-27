import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Database, Activity, Shield, LayoutDashboard, BarChart2, ClipboardList } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { orchestrationApi } from "../../api/orchestration";
import "./Sidebar.css";

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
  {
    label: "Administration",
    items: [
      { to: "/admin",          icon: Shield,           key: "nav.admin",         label: "Admin Panel" },
    ],
  },
];

function AlertDot({ count }: { count: number }) {
  if (!count) return null;
  const cls = count > 0 ? "alert-dot--active" : "alert-dot--dim";
  return (
    <span className={`alert-dot ${cls}`}>
      {count}
    </span>
  );
}

export function Sidebar() {
  const { t } = useTranslation();

  const { data: health } = useQuery({
    queryKey: ["system-health"],
    queryFn: orchestrationApi.getSystemHealth,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const activeAlerts = health?.active_alerts ?? 0;

  return (
    <aside className="layout__sidebar">
      <div className="sidebar-header">
        <img src="/jarvis-logo.png" alt="Jarvis Studio Logo" className="sidebar-logo" />
        <div>
          <div className="sidebar-logo-text">JARVIS</div>
          <div className="sidebar-logo-sub">STUDIO</div>
        </div>
      </div>

      {/* System health mini-indicator */}
      {health && (
        <div className={`system-health-banner system-health-banner--${health.system_status}`}>
          <span 
            className="health-dot" 
            style={{ background: health.system_status === "healthy" ? "#10b981" : health.system_status === "critical" ? "#ef4444" : "#f59e0b" }} 
          />
          <span className="health-text">
            {health.system_status === "healthy" ? "All systems healthy"
              : health.system_status === "critical" ? "Critical alerts" : "Warnings active"}
          </span>
        </div>
      )}

      <nav className="sidebar__nav sidebar-nav-container">
        {NAV_GROUPS.map(group => (
          <div key={group.label} className="sidebar-nav-group">
            <div className="sidebar-nav-label">
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

      <div className="sidebar-footer">
        <div className="sidebar-footer-stats">
          {health ? `${health.production_tables} prod · ${health.total_tables} total tables` : "Loading…"}
        </div>
      </div>
    </aside>
  );
}
