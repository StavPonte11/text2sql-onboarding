import { useTranslation } from 'react-i18next';
import { NavLink } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  BarChart2,
  ClipboardList,
  Database,
  LayoutDashboard,
  Shield,
} from 'lucide-react';

import { orchestrationApi } from '../../api/orchestration';
import { useAuthStore } from '../../store/authStore';
import { authApi } from '../../api/auth';
import { LogOut } from 'lucide-react';

import './Sidebar.css';

const NAV_GROUPS = [
  {
    label: 'Overview',
    items: [
      {
        to: '/control-center',
        icon: LayoutDashboard,
        key: 'nav.controlCenter',
        label: 'Control Center',
      },
    ],
  },
  {
    label: 'Evaluation',
    items: [
      { to: '/evaluations', icon: ClipboardList, key: 'nav.evaluations', label: 'Evaluations' },
      { to: '/analytics', icon: BarChart2, key: 'nav.analytics', label: 'Analytics' },
    ],
  },
  {
    label: 'Data',
    items: [
      { to: '/tables', icon: Database, key: 'nav.tables', label: 'Tables' },
      { to: '/monitoring', icon: Activity, key: 'nav.monitoring', label: 'Audit Log' },
      { to: '/permissions', icon: Shield, key: 'nav.permissions', label: 'Permissions' },
    ],
  },
  {
    label: 'Administration',
    items: [{ to: '/admin', icon: Shield, key: 'nav.admin', label: 'Admin Panel' }],
  },
];

function AlertDot({ count }: { count: number }) {
  if (!count) return null;
  const cls = count > 0 ? 'alert-dot--active' : 'alert-dot--dim';
  return <span className={`alert-dot ${cls}`}>{count}</span>;
}

export function Sidebar() {
  const { t } = useTranslation();
  const { user } = useAuthStore();

  const { data: health } = useQuery({
    queryKey: ['system-health'],
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
            style={{
              background:
                health.system_status === 'healthy'
                  ? '#10b981'
                  : health.system_status === 'critical'
                    ? '#ef4444'
                    : '#f59e0b',
            }}
          />
          <span className="health-text">
            {health.system_status === 'healthy'
              ? 'All systems healthy'
              : health.system_status === 'critical'
                ? 'Critical alerts'
                : 'Warnings active'}
          </span>
        </div>
      )}

      <nav className="sidebar__nav sidebar-nav-container">
        {NAV_GROUPS.map((group) => {
          if (group.label === 'Administration' && !user?.is_admin) return null;
          return (
            <div key={group.label} className="sidebar-nav-group">
              <div className="sidebar-nav-label">{group.label}</div>
              {group.items.map(({ to, icon: Icon, key, label }) => (
                <NavLink
                  key={to}
                  to={to}
                  className={({ isActive }) =>
                    `sidebar__nav-item${isActive ? ' sidebar__nav-item--active' : ''}`
                  }
                >
                  <Icon size={15} />
                  {t(key, label)}
                  {to === '/control-center' && <AlertDot count={activeAlerts} />}
                </NavLink>
              ))}
            </div>
          );
        })}
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-footer-stats">
          {health
            ? `${health.production_tables} prod · ${health.total_tables} total tables`
            : 'Loading…'}
        </div>
        {user && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            padding: '10px',
            marginTop: '12px',
            background: 'rgba(255, 255, 255, 0.03)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '6px',
          }}>
            <div style={{
              width: '28px',
              height: '28px',
              borderRadius: '50%',
              background: 'linear-gradient(135deg, var(--accent), var(--accent-hover))',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#ffffff',
              fontWeight: 700,
              fontSize: '11px',
              flexShrink: 0,
            }}>
              {user.name ? user.name.slice(0, 2).toUpperCase() : 'U'}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0 }}>
              <div style={{
                fontSize: '12px',
                fontWeight: 600,
                color: 'var(--text-primary)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                lineHeight: 1.2,
              }}>
                {user.name}
              </div>
              <div style={{
                fontSize: '10px',
                color: 'var(--text-muted)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                marginTop: '1px',
              }}>
                {user.email}
              </div>
            </div>
            <button
              onClick={() => authApi.logout()}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: '#ef4444',
                opacity: 0.8,
                padding: '4px',
                borderRadius: '4px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'all 0.15s ease',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)';
                e.currentTarget.style.opacity = '1';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'none';
                e.currentTarget.style.opacity = '0.8';
              }}
              title="Sign Out"
            >
              <LogOut size={14} />
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
