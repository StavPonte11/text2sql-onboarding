import axios from 'axios';

import { API_BASE_URL } from '../config/constants';
import { useAppStore } from '../store/appStore';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.request.use((config) => {
  const scope = useAppStore.getState().activeScope;
  if (scope) config.headers['X-Scope-Id'] = scope.id;
  return config;
});

// ── Types ──────────────────────────────────────────────────────────────────────

export interface EvalRunFull {
  id: string;
  table_id: string;
  table_name?: string;
  dataset_id: string | null;
  score: number;
  pass_rate: number;
  fail_rate: number;
  total_questions: number;
  duration_seconds: number | null;
  triggered_by: string;
  status: 'running' | 'completed' | 'failed';
  started_at: string;
  completed_at: string | null;
  failure_breakdown: Record<string, number> | null;
  dimension_averages: Record<string, number> | null;
  regression_detected: boolean;
  regression_delta: number | null;
  promotion_run_id: string | null;
  created_at: string;
}

export interface EvalSchedule {
  id: string;
  dataset_id: string;
  table_scope: string[] | null;
  cron_expression: string;
  enabled: boolean;
  created_by: string;
  created_at: string;
  last_run_at: string | null;
  next_run_at: string | null;
}

export interface EvalScheduleCreate {
  dataset_id: string;
  table_scope?: string[];
  cron_expression: string;
  enabled: boolean;
  created_by?: string;
}

export interface EvalScheduleUpdate {
  dataset_id?: string;
  table_scope?: string[];
  cron_expression?: string;
  enabled?: boolean;
}

export interface EvalAlert {
  id: string;
  run_id: string | null;
  table_id: string | null;
  alert_type: string;
  severity: 'info' | 'warning' | 'critical';
  message: string;
  details: Record<string, unknown> | null;
  acknowledged: boolean;
  created_at: string;
}

export interface TrendPoint {
  run_id: string;
  table_id: string;
  date: string;
  timestamp: string;
  score: number;
  pass_rate: number;
  fail_rate: number;
  regression_detected: boolean;
}

export interface DailyTrend {
  date: string;
  avg_score: number;
  avg_pass_rate: number;
  run_count: number;
}

export interface TableAnalytics {
  table_id: string;
  table_name: string;
  status: string;
  latest_score: number | null;
  avg_score: number | null;
  pass_rate: number | null;
  run_count: number;
  trend: 'improving' | 'stable' | 'declining';
  last_run_at?: string;
  failure_breakdown: Record<string, number>;
}

export interface CompareResult {
  run1: { id: string; score: number; pass_rate: number; created_at: string; table_id: string };
  run2: { id: string; score: number; pass_rate: number; created_at: string; table_id: string };
  score_delta: number;
  pass_rate_delta: number;
  regression_count: number;
  improvement_count: number;
  regressions: { question_id: string; run1_score: number; run2_score: number; delta: number }[];
  improvements: { question_id: string; run1_score: number; run2_score: number; delta: number }[];
  verdict: 'regression' | 'improvement' | 'stable';
}

export interface RegressionDiffItem {
  question_id: string;
  question: string;
  baseline_score: number;
  regression_score: number;
  score_drop: number;
}

export interface RegressionDiff {
  baseline_run_id: string | null;
  regression_run_id: string;
  total_regressions: number;
  regressions: RegressionDiffItem[];
}

export interface SystemHealth {
  global_score: number | null;
  global_pass_rate: number | null;
  active_alerts: number;
  critical_alerts: number;
  last_evaluation: string | null;
  total_tables: number;
  production_tables: number;
  total_runs_today: number;
  top_failing_tables: {
    table_id: string;
    table_name: string;
    avg_score: number;
    failure_rate: number;
  }[];
  recent_runs: {
    run_id: string;
    table_id: string;
    score: number;
    pass_rate: number;
    status: string;
    triggered_by: string;
    created_at: string;
    regression_detected: boolean;
  }[];
  system_status: 'healthy' | 'warning' | 'critical';
}

// ── API ────────────────────────────────────────────────────────────────────────

export const orchestrationApi = {
  // Runs
  triggerRun: (table_ids: string[], triggered_by = 'user') =>
    api
      .post<EvalRunFull[]>('/evaluations/run', table_ids, { params: { triggered_by } })
      .then((r) => r.data),

  listRuns: (params?: { limit?: number; offset?: number; status?: string; table_id?: string }) =>
    api.get<EvalRunFull[]>('/evaluations/runs', { params }).then((r) => r.data),

  getRun: (run_id: string) =>
    api.get<EvalRunFull>(`/evaluations/runs/${run_id}`).then((r) => r.data),

  getRunReport: (run_id: string) =>
    api.get(`/evaluations/runs/${run_id}/report`).then((r) => r.data),

  getRegressionDiff: (run_id: string) =>
    api.get<RegressionDiff>(`/eval/${run_id}/regression-diff`).then((r) => r.data),

  // Schedules
  listSchedules: () => api.get<EvalSchedule[]>('/evaluations/schedules').then((r) => r.data),

  createSchedule: (payload: EvalScheduleCreate) =>
    api.post<EvalSchedule>('/evaluations/schedules', payload).then((r) => r.data),

  updateSchedule: (id: string, payload: EvalScheduleUpdate) =>
    api.put<EvalSchedule>(`/evaluations/schedules/${id}`, payload).then((r) => r.data),

  deleteSchedule: (id: string) => api.delete(`/evaluations/schedules/${id}`),

  // Analytics
  getTrends: (days = 30, table_id?: string) =>
    api
      .get<{
        runs: TrendPoint[];
        daily: DailyTrend[];
        total_runs: number;
      }>('/evaluations/analytics/trends', { params: { days, table_id } })
      .then((r) => r.data),

  getTableAnalytics: () =>
    api.get<TableAnalytics[]>('/evaluations/analytics/tables').then((r) => r.data),

  compareRuns: (run1: string, run2: string) =>
    api.get<CompareResult>('/evaluations/compare', { params: { run1, run2 } }).then((r) => r.data),

  // Alerts
  listAlerts: (acknowledged?: boolean, limit = 50) =>
    api
      .get<EvalAlert[]>('/evaluations/alerts', { params: { acknowledged, limit } })
      .then((r) => r.data),

  acknowledgeAlert: (alert_id: string) =>
    api.post<EvalAlert>(`/evaluations/alerts/${alert_id}/acknowledge`).then((r) => r.data),

  // System health
  getSystemHealth: () => api.get<SystemHealth>('/evaluations/system-health').then((r) => r.data),

  // Readiness
  getReadiness: () =>
    api
      .get<Record<string, { ready: boolean; missing: string[] }>>('/eval/readiness')
      .then((r) => r.data),
};
