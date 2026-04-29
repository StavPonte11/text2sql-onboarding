import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Database, FlaskConical, Activity, Shield } from "lucide-react";

const NAV_ITEMS = [
  { to: "/tables", icon: Database, key: "nav.tables" },
  { to: "/sandbox", icon: FlaskConical, key: "nav.sandbox" },
  { to: "/monitoring", icon: Activity, key: "nav.monitoring" },
  { to: "/permissions", icon: Shield, key: "nav.permissions" },
];

export function Sidebar() {
  const { t } = useTranslation();

  return (
    <aside className="layout__sidebar">
      <div className="sidebar__logo">
        <div className="sidebar__logo-text">⚡ The Agency</div>
        <div className="sidebar__logo-sub">Data Intelligence Studio</div>
      </div>

      <nav className="sidebar__nav">
        {NAV_ITEMS.map(({ to, icon: Icon, key }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `sidebar__nav-item${isActive ? " sidebar__nav-item--active" : ""}`
            }
          >
            <Icon size={16} />
            {t(key)}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
