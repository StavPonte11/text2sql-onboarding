import { useTranslation } from 'react-i18next';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider, theme } from 'antd';
import { Globe } from 'lucide-react';

import { ScopeBanner } from './components/layout/ScopeBanner';
import { Sidebar } from './components/layout/Sidebar';
import { MonitoringPage } from './components/monitoring/MonitoringPage';
import { TableDetails } from './components/tables/TableDetails';
import { TableList } from './components/tables/TableList';
import { OnboardingWizard } from './components/wizard/OnboardingWizard';
import { AdminLoginPage } from './pages/AdminLoginPage';
import { AdminPanelPage } from './pages/AdminPanelPage';
import { AnalyticsPage } from './pages/AnalyticsPage';
import { ControlCenterPage } from './pages/ControlCenterPage';
import { EvaluationsPage } from './pages/EvaluationsPage';
import { LandingPage } from './pages/LandingPage';
import { ScopesPage } from './pages/ScopesPage';
import { useAdminStore } from './store/adminStore';

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
  const isAuthenticated = useAdminStore((state) => state.isAuthenticated);
  if (!isAuthenticated) {
    return <Navigate to="/admin/login" replace />;
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
          <Route path="/control-center" element={<ControlCenterPage />} />
          <Route path="/evaluations" element={<EvaluationsPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/tables" element={<TableList />} />
          <Route path="/tables/:id" element={<Navigate to="overview" replace />} />
          <Route path="/tables/:id/:tab" element={<TableDetails />} />
          <Route path="/wizard" element={<OnboardingWizard />} />
          <Route path="/monitoring" element={<MonitoringPage />} />
          <Route path="/permissions" element={<ScopesPage />} />
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
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<LandingPage />} />
              <Route path="/admin/login" element={<AdminLoginPage />} />
              <Route path="/*" element={<AppLayout />} />
            </Routes>
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
