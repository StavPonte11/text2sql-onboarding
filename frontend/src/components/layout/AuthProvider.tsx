import { useEffect } from 'react';
import { useAuthStore } from '../../store/authStore';
import { authApi } from '../../api/auth';

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { setAuth, setLoading } = useAuthStore();

  useEffect(() => {
    let mounted = true;

    authApi.getMe()
      .then((user) => {
        if (mounted) setAuth(user);
      })
      .catch(() => {
        if (mounted) setAuth(null);
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, [setAuth, setLoading]);

  return <>{children}</>;
}
