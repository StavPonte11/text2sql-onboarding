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

export interface QueryApproval {
  approved: boolean;
  rejection_category?: string;
  feedback?: string;
  suggested_fix?: string;
}

export interface ChatRequest {
  query?: string;
  thread_id?: string;
  resume_value?: QueryApproval | string;
  allowed_tables?: string[];
  allowed_statuses?: string[];
  extractors?: string[];
  hitl_enabled?: boolean;
  connection_id?: number;
}

export interface ChatResponse {
  thread_id: string;
  status: 'completed' | 'interrupted';
  interrupt_details?: Record<string, unknown>;
  summary?: string;
  raw_data_ref?: string;
  sql_query?: string;
  sql_explanation?: string;
  trace_id?: string;
  execution_path?: string[];
  is_unanswerable?: boolean;
}

export const agentApi = {
  chat: (payload: ChatRequest): Promise<ChatResponse> =>
    api.post<ChatResponse>('/chat', payload).then((r) => r.data),
  suggestFixes: async (threadId: string, category: string): Promise<string[]> => {
    const response = await api.post<string[]>('/suggest_fixes', { thread_id: threadId, category });
    return response.data;
  },
  listConnections: async (): Promise<any> => {
    const response = await api.get<any>('/connections');
    return response.data;
  },
};
