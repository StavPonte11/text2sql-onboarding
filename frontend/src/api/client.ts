import axios from 'axios';

import { API_BASE_URL } from '../config/constants';
import { useAppStore } from '../store/appStore';
import { useAuthStore } from '../store/authStore';

import type {
  AuditQuery,
  ColumnProfile,
  CrossTableProfile,
  EnrichmentVersion,
  EvalResult,
  EvalRun,
  ForeignKeyMapping,
  ForeignKeyMappingCreate,
  GoldenQuestion,
  GoldenQuestionCreate,
  QueryFeedback,
  QueryFeedbackCreate,
  Table,
  TableCreate,
  TableHealth,
  TableProfile,
  UserScope,
  UserScopeCreate,
} from '../types';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
});

api.interceptors.request.use((config) => {
  const scope = useAppStore.getState().activeScope;
  if (scope) {
    config.headers['X-Scope-Id'] = scope.id;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Clear auth state to trigger React Router's ProtectedRoute redirect
      useAuthStore.getState().setAuth(null);
    }
    return Promise.reject(error);
  }
);

// ── Tables ────────────────────────────────────────────────────────────────────
export const tablesApi = {
  list: (params?: { status?: string; owner_id?: string; search?: string }) =>
    api.get<Table[]>('/tables', { params }).then((r) => r.data),
  get: (id: string) => api.get<Table>(`/tables/${id}`).then((r) => r.data),
  create: (payload: TableCreate) => api.post<Table>('/tables', payload).then((r) => r.data),
  updateStatus: (id: string, status: string) =>
    api.patch<Table>(`/tables/${id}/status`, null, { params: { status } }).then((r) => r.data),
  syncSchema: (id: string) => api.post<Table>(`/tables/${id}/sync-schema`).then((r) => r.data),
};

// ── Enrichment ────────────────────────────────────────────────────────────────
export const enrichmentApi = {
  getLatest: (tableId: string) =>
    api.get<EnrichmentVersion>(`/tables/${tableId}/enrichment/latest`).then((r) => r.data),
  create: (tableId: string, data: EnrichmentVersion['data']) =>
    api.post<EnrichmentVersion>(`/tables/${tableId}/enrichment`, { data }).then((r) => r.data),
};

// ── Golden Questions ──────────────────────────────────────────────────────────
export const questionsApi = {
  list: (tableId: string) =>
    api.get<GoldenQuestion[]>(`/tables/${tableId}/questions`).then((r) => r.data),
  create: (tableId: string, payload: GoldenQuestionCreate) =>
    api.post<GoldenQuestion>(`/tables/${tableId}/questions`, payload).then((r) => r.data),
  uploadQuestions: (tableId: string, file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return api
      .post(`/tables/${tableId}/questions/upload`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      .then((r) => r.data);
  },
  delete: (tableId: string, questionId: string) =>
    api.delete(`/tables/${tableId}/questions/${questionId}`),
};

// ── Evaluation ────────────────────────────────────────────────────────────────
export const evalApi = {
  triggerRun: (tableId: string) =>
    api.post<EvalRun>(`/tables/${tableId}/eval/run`).then((r) => r.data),
  listAllRuns: () => api.get<EvalRun[]>('/eval/runs/all').then((r) => r.data),
  listRuns: (tableId: string) =>
    api.get<EvalRun[]>(`/tables/${tableId}/eval/runs`).then((r) => r.data),
  listBatchRuns: (promotionRunId: string) =>
    api.get<EvalRun[]>(`/eval/batch/${promotionRunId}`).then((r) => r.data),
  getRun: (runId: string) => api.get<EvalRun>(`/eval/${runId}`).then((r) => r.data),
  getResults: (runId: string) =>
    api.get<EvalResult[]>(`/eval/${runId}/results`).then((r) => r.data),
};

// ── Publish ───────────────────────────────────────────────────────────────────
export const publishApi = {
  publish: (tableId: string) => api.post(`/tables/${tableId}/publish`).then((r) => r.data),
};

// ── Scopes ────────────────────────────────────────────────────────────────────
export const scopesApi = {
  list: () => api.get<UserScope[]>('/scopes').then((r) => r.data),
  create: (payload: UserScopeCreate) => api.post<UserScope>('/scopes', payload).then((r) => r.data),
  activate: (scopeId: string) =>
    api.post<UserScope>(`/scopes/${scopeId}/activate`).then((r) => r.data),
};

// ── Audit ─────────────────────────────────────────────────────────────────────
export const auditApi = {
  queries: (params?: { table_id?: string; user_id?: string; limit?: number }) =>
    api.get<AuditQuery[]>('/audit/queries', { params }).then((r) => r.data),
};

// ── Profiling ─────────────────────────────────────────────────────────────────
export const profilingApi = {
  get: (tableId: string) => api.get<TableProfile>(`/tables/${tableId}/profile`).then((r) => r.data),
  run: (tableId: string) =>
    api.post<TableProfile>(`/tables/${tableId}/profile/run`).then((r) => r.data),
  getColumns: (tableId: string) =>
    api.get<ColumnProfile[]>(`/tables/${tableId}/profile/columns`).then((r) => r.data),
  getCrossProfiles: (tableId: string) =>
    api.get<CrossTableProfile[]>(`/tables/${tableId}/cross-profile`).then((r) => r.data),
  runCrossProfile: (tableId: string) =>
    api.post<CrossTableProfile[]>(`/tables/${tableId}/cross-profile`).then((r) => r.data),
};

// ── Feedback ──────────────────────────────────────────────────────────────────
export const feedbackApi = {
  submit: (payload: QueryFeedbackCreate) =>
    api.post<QueryFeedback>('/feedback', payload).then((r) => r.data),
  getForTable: (tableId: string) =>
    api.get<QueryFeedback[]>(`/feedback/table/${tableId}`).then((r) => r.data),
  getForQuery: (queryId: string) =>
    api.get<QueryFeedback[]>(`/feedback/query/${queryId}`).then((r) => r.data),
};

// ── Health ────────────────────────────────────────────────────────────────────
export const healthApi = {
  get: (tableId: string) => api.get<TableHealth>(`/tables/${tableId}/health`).then((r) => r.data),
  recompute: (tableId: string) =>
    api.post<TableHealth>(`/tables/${tableId}/health/recompute`).then((r) => r.data),
  getAll: () => api.get<TableHealth[]>('/health/all').then((r) => r.data),
};

// ── Foreign Keys ──────────────────────────────────────────────────────────────
export const foreignKeysApi = {
  list: (tableId: string) =>
    api.get<ForeignKeyMapping[]>(`/tables/${tableId}/foreign-keys`).then((r) => r.data),
  create: (tableId: string, payload: ForeignKeyMappingCreate) =>
    api.post<ForeignKeyMapping>(`/tables/${tableId}/foreign-keys`, payload).then((r) => r.data),
  delete: (tableId: string, fkId: string) => api.delete(`/tables/${tableId}/foreign-keys/${fkId}`),
};

export default api;
