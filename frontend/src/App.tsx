import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider, App as AntApp, theme } from "antd";
import { Sidebar } from "./components/layout/Sidebar";
import { ScopeBanner } from "./components/layout/ScopeBanner";
import { TableList } from "./components/tables/TableList";
import { TableDetails } from "./components/tables/TableDetails";
import { OnboardingWizard } from "./components/wizard/OnboardingWizard";
import { MonitoringPage } from "./components/monitoring/MonitoringPage";
import { ScopesPage } from "./pages/ScopesPage";
import { SandboxPage } from "./pages/SandboxPage";
import { ControlCenterPage } from "./pages/ControlCenterPage";
import { EvaluationsPage } from "./pages/EvaluationsPage";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { LandingPage } from "./pages/LandingPage";
import { useTranslation } from "react-i18next";
import { Globe } from "lucide-react";
import "./styles/globals.css";
import "./i18n";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000 },
  },
});

function LanguageToggle() {
  const { i18n } = useTranslation();
  const isHe = i18n.language === "he";
  return (
    <button
      className="lang-toggle"
      onClick={() => {
        const next = isHe ? "en" : "he";
        i18n.changeLanguage(next);
        document.documentElement.dir = next === "he" ? "rtl" : "ltr";
        document.documentElement.lang = next;
      }}
    >
      <Globe size={13} />
      {isHe ? "EN" : "עב"}
    </button>
  );
}

function AppLayout() {
  return (
    <div className="layout">
      <Sidebar />
      <div className="layout__content">
        <ScopeBanner />
        <div style={{ position: "absolute", top: 12, right: 16, zIndex: 100 }}>
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
          {/* Catch-all redirect for unmatched inner routes */}
          <Route path="*" element={<Navigate to="/control-center" replace />} />
        </Routes>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ConfigProvider theme={{ algorithm: theme.darkAlgorithm, token: { colorPrimary: '#0ea5e9', fontFamily: "'Plus Jakarta Sans', sans-serif", borderRadius: 6 } }}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<LandingPage />} />
              <Route path="/*" element={<AppLayout />} />
            </Routes>
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
