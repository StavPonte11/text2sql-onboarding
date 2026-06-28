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
  feedback?: string;
}

export interface ChatRequest {
  query?: string;
  thread_id?: string;
  resume_value?: QueryApproval | string;
  allowed_tables?: string[];
  allowed_statuses?: string[];
  extractors?: string[];
  hitl_enabled?: boolean;
}

export interface ChatResponse {
  thread_id: string;
  status: 'completed' | 'interrupted';
  interrupt_details?: Record<string, unknown>;
  summary?: string;
  raw_data_ref?: string;
  sql_query?: string;
  sql_explanation?: string;
  schema_plan?: string;
}

export const agentApi = {
  chat: (payload: ChatRequest): Promise<ChatResponse> =>
    api.post<ChatResponse>('/chat', payload).then((r) => r.data),
};
