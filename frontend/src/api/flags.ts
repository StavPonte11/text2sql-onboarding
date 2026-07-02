import api from './client';

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
  list: (): Promise<FeatureFlag[]> => api.get('/flags/').then((r) => r.data),

  update: (name: string, value: unknown): Promise<FeatureFlag> =>
    api.patch(`/flags/${encodeURIComponent(name)}`, { value }).then((r) => r.data),

  reset: (name: string): Promise<null> =>
    api.delete(`/flags/${encodeURIComponent(name)}`).then((r) => r.data),

  // ── Execution Modes ───────────────────────────────────────────────────────
  listModes: (): Promise<ExecutionMode[]> => api.get('/flags/modes/').then((r) => r.data),

  getMode: (name: string): Promise<ExecutionMode> =>
    api.get(`/flags/modes/${encodeURIComponent(name)}`).then((r) => r.data),

  upsertMode: (name: string, data: Partial<ExecutionMode>): Promise<ExecutionMode> =>
    api.put(`/flags/modes/${encodeURIComponent(name)}`, data).then((r) => r.data),

  deleteMode: (name: string): Promise<null> =>
    api.delete(`/flags/modes/${encodeURIComponent(name)}`).then((r) => r.data),
};
