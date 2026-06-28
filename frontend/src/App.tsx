import { useTranslation } from 'react-i18next';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider, theme } from 'antd';
import { Globe } from 'lucide-react';

import { AuthProvider } from './components/layout/AuthProvider';
import { ScopeBanner } from './components/layout/ScopeBanner';
import { Sidebar } from './components/layout/Sidebar';
import { MonitoringPage } from './components/monitoring/MonitoringPage';
import { TableDetails } from './components/tables/TableDetails';
import { TableList } from './components/tables/TableList';
import { OnboardingWizard } from './components/wizard/OnboardingWizard';
import { AdminPanelPage } from './pages/AdminPanelPage';
import { AgentTestingPage } from './pages/AgentTestingPage';
import { AnalyticsPage } from './pages/AnalyticsPage';
import { ControlCenterPage } from './pages/ControlCenterPage';
import { EvaluationsPage } from './pages/EvaluationsPage';
import { LandingPage } from './pages/LandingPage';
import { LoginPage } from './pages/LoginPage';
import { ScopesPage } from './pages/ScopesPage';
import { useAuthStore } from './store/authStore';

import './styles/globals.css';

import './i18n';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000 },
  },
});

function LanguageToggle() {
  const { i18n } = useTranslation();
  const isHe = i18n.language === 'he';
  return (
    <button
      className="lang-toggle"
      onClick={() => {
        const next = isHe ? 'en' : 'he';
        i18n.changeLanguage(next);
        document.documentElement.dir = next === 'he' ? 'rtl' : 'ltr';
        document.documentElement.lang = next;
      }}
    >
      <Globe size={13} />
      {isHe ? 'EN' : 'עב'}
    </button>
  );
}

function ProtectedAdminRoute({ children }: { children: React.ReactNode }) {
  const { user, isAuthenticated, isLoading } = useAuthStore();

  if (isLoading) return <div style={{ padding: '2rem', textAlign: 'center' }}>Loading...</div>;
  if (!isAuthenticated || !user?.is_admin) {
    return <Navigate to="/control-center" replace />;
  }
  return <>{children}</>;
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore();

  if (isLoading) return <div style={{ padding: '2rem', textAlign: 'center' }}>Loading...</div>;
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

function AppLayout() {
  return (
    <div className="layout">
      <Sidebar />
      <div className="layout__content">
        <ScopeBanner />
        <div className="layout__lang-toggle">
          <LanguageToggle />
        </div>
        <Routes>
          <Route
            path="/control-center"
            element={
              <ProtectedRoute>
                <ControlCenterPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/evaluations"
            element={
              <ProtectedRoute>
                <EvaluationsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/analytics"
            element={
              <ProtectedRoute>
                <AnalyticsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/tables"
            element={
              <ProtectedRoute>
                <TableList />
              </ProtectedRoute>
            }
          />
          <Route path="/tables/:id" element={<Navigate to="overview" replace />} />
          <Route
            path="/tables/:id/:tab"
            element={
              <ProtectedRoute>
                <TableDetails />
              </ProtectedRoute>
            }
          />
          <Route
            path="/wizard"
            element={
              <ProtectedRoute>
                <OnboardingWizard />
              </ProtectedRoute>
            }
          />
          <Route
            path="/monitoring"
            element={
              <ProtectedRoute>
                <MonitoringPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/permissions"
            element={
              <ProtectedRoute>
                <ScopesPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/agent-testing"
            element={
              <ProtectedRoute>
                <AgentTestingPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin"
            element={
              <ProtectedAdminRoute>
                <AdminPanelPage />
              </ProtectedAdminRoute>
            }
          />
          {/* Catch-all redirect for unmatched inner routes */}
          <Route path="*" element={<Navigate to="/control-center" replace />} />
        </Routes>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ConfigProvider
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#0ea5e9',
          fontFamily: "'Plus Jakarta Sans', sans-serif",
          borderRadius: 6,
        },
      }}
    >
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <BrowserRouter>
              <Routes>
                <Route path="/" element={<LandingPage />} />
                <Route path="/login" element={<LoginPage />} />
                <Route path="/*" element={<AppLayout />} />
              </Routes>
            </BrowserRouter>
          </AuthProvider>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
