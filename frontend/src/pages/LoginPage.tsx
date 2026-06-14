import { useEffect, useState } from 'react';
import { Button, Card, Typography, Spin } from 'antd';
import { Navigate } from 'react-router-dom';
import { authApi, type AuthConfig } from '../api/auth';
import { useAuthStore } from '../store/authStore';
import { API_BASE_URL } from '../config/constants';

const { Title, Text } = Typography;

export function LoginPage() {
  const { isAuthenticated, isLoading } = useAuthStore();
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [configLoading, setConfigLoading] = useState(true);

  useEffect(() => {
    authApi.getConfig()
      .then(setConfig)
      .catch(console.error)
      .finally(() => setConfigLoading(false));
  }, []);

  if (isLoading || configLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" />
      </div>
    );
  }

  if (isAuthenticated) {
    return <Navigate to="/control-center" replace />;
  }

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#0f172a' }}>
      <Card
        style={{ width: 400, textAlign: 'center', background: '#1e293b', borderColor: '#334155' }}
        styles={{ body: { padding: '32px' } }}
      >
        <Title level={2} style={{ color: '#fff', marginBottom: '8px' }}>Sign In</Title>
        <Text style={{ color: '#94a3b8', display: 'block', marginBottom: '32px' }}>
          Welcome to Jarvis Studio
        </Text>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {config?.ENABLE_GOOGLE && (
            <Button
              type="primary"
              size="large"
              block
              onClick={() => {
                const nextUrl = encodeURIComponent(window.location.origin + '/control-center');
                window.location.href = `${API_BASE_URL}/v1/auth/login/google?next_url=${nextUrl}`;
              }}
            >
              Sign in with Google
            </Button>
          )}

          {config?.ENABLE_KEYCLOAK && (
            <Button
              size="large"
              block
              onClick={() => {
                const nextUrl = encodeURIComponent(window.location.origin + '/control-center');
                window.location.href = `${API_BASE_URL}/v1/auth/login/keycloak?next_url=${nextUrl}`;
              }}
            >
              Sign in with Keycloak
            </Button>
          )}

          {!config?.ENABLE_GOOGLE && !config?.ENABLE_KEYCLOAK && (
            <Text style={{ color: '#ef4444' }}>No authentication providers configured.</Text>
          )}
        </div>
      </Card>
    </div>
  );
}
