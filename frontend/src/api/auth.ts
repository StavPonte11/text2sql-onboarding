import api from './client';

import type { components } from './schema';

export type AuthConfig = components["schemas"]["AuthConfigRead"];

export type User = components["schemas"]["SecurityUserRead"];

export const authApi = {
  getMe: () => api.get<User>('/api/v1/auth/me').then((r) => r.data),
  getConfig: () => api.get<AuthConfig>('/api/v1/auth/config').then((r) => r.data),
  logout: () => window.location.href = `${api.defaults.baseURL}/api/v1/auth/logout`
};
