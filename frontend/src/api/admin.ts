import { useAdminStore } from '../store/adminStore';

const API_BASE = 'http://localhost:8000';

const fetchWithAuth = async (url: string, options: RequestInit = {}) => {
  const token = useAdminStore.getState().token;
  
  const headers = new Headers(options.headers || {});
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  
  const response = await fetch(`${API_BASE}${url}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    useAdminStore.getState().logout();
    throw new Error('Unauthorized');
  }

  if (!response.ok) {
    const errorData = await response.json().catch(() => null);
    throw new Error(errorData?.detail || 'Request failed');
  }

  return response.json();
};

export const adminApi = {
  login: async (username: string, password: string) => {
    const formData = new URLSearchParams();
    formData.append('username', username);
    formData.append('password', password);

    const response = await fetch(`${API_BASE}/admin/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: formData.toString(),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => null);
      throw new Error(errorData?.detail || 'Login failed');
    }

    return response.json();
  },

  getMe: () => fetchWithAuth('/admin/me'),
  
  getPendingTables: () => fetchWithAuth('/admin/tables/pending'),
  
  approveTable: (tableId: string) => 
    fetchWithAuth(`/admin/tables/${tableId}/approve`, { method: 'POST' }),
    
  rejectTable: (tableId: string, note: string) => 
    fetchWithAuth(`/admin/tables/${tableId}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    }),
};
