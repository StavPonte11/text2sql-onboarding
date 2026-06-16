import { API_BASE_URL } from '../config/constants';
import { useAdminStore } from '../store/adminStore';

const fetchWithAdminEmail = async (url: string, options: RequestInit = {}) => {
  const user = useAdminStore.getState().user;
  if (!user?.email) throw new Error('Not authenticated');

  const headers = new Headers(options.headers || {});
  headers.set('X-Admin-Email', user.email);
  headers.set('Content-Type', 'application/json');

  const response = await fetch(`${API_BASE_URL}${url}`, { ...options, headers });

  if (response.status === 403) {
    useAdminStore.getState().logout();
    const err = await response.json().catch(() => null);
    throw new Error(err?.detail || 'Forbidden');
  }
  if (!response.ok) {
    const err = await response.json().catch(() => null);
    throw new Error(err?.detail || 'Request failed');
  }
  if (response.status === 204) return null;
  return response.json();
};

export type FlagType = 'bool' | 'int' | 'float' | 'string' | 'json';

export interface FeatureFlag {
  name: string;
  value: unknown;
  type: FlagType;
  description: string;
  owner: string;
  last_modified_by: string;
  last_modified_at: string;
}

export interface ExecutionMode {
  name: string;
  description: string;
  flag_overrides: Record<string, unknown>;
  is_active: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export const flagsApi = {
  // ── Feature Flags ─────────────────────────────────────────────────────────
  list: (): Promise<FeatureFlag[]> => fetchWithAdminEmail('/flags/'),

  update: (name: string, value: unknown): Promise<FeatureFlag> =>
    fetchWithAdminEmail(`/flags/${name}`, {
      method: 'PATCH',
      body: JSON.stringify({ value }),
    }),

  reset: (name: string): Promise<null> =>
    fetchWithAdminEmail(`/flags/${name}`, { method: 'DELETE' }),

  // ── Execution Modes ───────────────────────────────────────────────────────
  listModes: (): Promise<ExecutionMode[]> => fetchWithAdminEmail('/flags/modes/'),

  getMode: (name: string): Promise<ExecutionMode> =>
    fetchWithAdminEmail(`/flags/modes/${name}`),

  upsertMode: (name: string, data: Partial<ExecutionMode>): Promise<ExecutionMode> =>
    fetchWithAdminEmail(`/flags/modes/${name}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  deleteMode: (name: string): Promise<null> =>
    fetchWithAdminEmail(`/flags/modes/${name}`, { method: 'DELETE' }),
};
