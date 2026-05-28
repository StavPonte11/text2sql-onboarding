import { useAdminStore } from '../store/adminStore';
import { API_BASE_URL } from '../config/constants';


const fetchWithAdminEmail = async (url: string, options: RequestInit = {}) => {
  const user = useAdminStore.getState().user;

  if (!user?.email) {
    throw new Error('Not authenticated');
  }

  const headers = new Headers(options.headers || {});
  headers.set('X-Admin-Email', user.email);

  const response = await fetch(`${API_BASE_URL}${url}`, {
    ...options,
    headers,
  });

  if (response.status === 403) {
    useAdminStore.getState().logout();
    const errorData = await response.json().catch(() => null);
    throw new Error(errorData?.detail || 'Forbidden');
  }

  if (!response.ok) {
    const errorData = await response.json().catch(() => null);
    throw new Error(errorData?.detail || 'Request failed');
  }

  return response.json();
};

export const adminApi = {
  login: async (email: string) => {
    const response = await fetch(`${API_BASE_URL}/admin/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });

    if (response.status === 403) {
      const errorData = await response.json().catch(() => null);
      throw new Error(errorData?.detail || 'You do not have permission to access the admin panel');
    }

    if (!response.ok) {
      const errorData = await response.json().catch(() => null);
      throw new Error(errorData?.detail || 'Login failed');
    }

    return response.json();
  },

  getPendingTables: () => fetchWithAdminEmail('/admin/tables/pending'),

  approveTable: (tableId: string) =>
    fetchWithAdminEmail(`/admin/tables/${tableId}/approve`, { method: 'POST' }),

  rejectTable: (tableId: string, note: string) =>
    fetchWithAdminEmail(`/admin/tables/${tableId}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    }),
};
