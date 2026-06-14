import axios from 'axios';

const api = axios.create({
  baseURL: '/api/agent',
  headers: { 'Content-Type': 'application/json' },
});

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
