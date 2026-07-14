import axios from 'axios';

import { useAuthStore } from '../store/authStore';
const api = axios.create({
  baseURL: '/api/agent',
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Clear auth state to trigger React Router's ProtectedRoute redirect
      useAuthStore.getState().setAuth(null);
    }
    return Promise.reject(error);
  },
);

import type { components } from './schema';

export type QueryApproval = components['schemas']['QueryApproval'];
export type ChatRequest = components['schemas']['ChatRequest'];
export type ChatResponse = components['schemas']['ChatResponse'];

export const agentApi = {
  chat: (payload: ChatRequest): Promise<ChatResponse> =>
    api.post<ChatResponse>('/chat', payload).then((r) => r.data),
  suggestFixes: async (threadId: string, category: string): Promise<string[]> => {
    const response = await api.post<string[]>('/suggest_fixes', { thread_id: threadId, category });
    return response.data;
  },
};
