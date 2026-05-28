import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { evalApi, healthApi } from '../api/client';
import { orchestrationApi } from '../api/orchestration';
import { QUERY_CONFIG, QUERY_KEYS } from '../config/constants';

import type { EvalScheduleCreate, EvalScheduleUpdate } from '../api/orchestration';

// ── Trigger single table evaluation ──────────────────────────────────────────
export function useTriggerEval() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (tableId: string) => evalApi.triggerRun(tableId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.EVAL_RUNS] });
    },
  });
}

// ── Trigger multi-table orchestration run ──────────────────────────────────────
export function useTriggerOrchestrationRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tableIds, triggeredBy }: { tableIds: string[]; triggeredBy?: string }) =>
      orchestrationApi.triggerRun(tableIds, triggeredBy),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.EVAL_RUNS] });
    },
  });
}

// ── List all evaluation runs ───────────────────────────────────────────────────
export function useAllEvalRuns() {
  return useQuery({
    queryKey: [QUERY_KEYS.EVAL_RUNS, 'all'],
    queryFn: () => evalApi.listAllRuns(),
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── List runs for a specific table ─────────────────────────────────────────────
export function useTableEvalRuns(tableId: string) {
  return useQuery({
    queryKey: [QUERY_KEYS.EVAL_RUNS, 'table', tableId],
    queryFn: () => evalApi.listRuns(tableId),
    enabled: !!tableId,
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── List batch runs for a promotion run ────────────────────────────────────────
export function useBatchEvalRuns(promotionRunId: string) {
  return useQuery({
    queryKey: [QUERY_KEYS.EVAL_RUNS, 'batch', promotionRunId],
    queryFn: () => evalApi.listBatchRuns(promotionRunId),
    enabled: !!promotionRunId,
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Get a single run details ───────────────────────────────────────────────────
export function useEvalRunDetails(runId: string) {
  return useQuery({
    queryKey: [QUERY_KEYS.EVAL_REPORT, runId],
    queryFn: () => evalApi.getRun(runId),
    enabled: !!runId,
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Get run report ─────────────────────────────────────────────────────────────
export function useEvalRunReport(runId: string) {
  return useQuery({
    queryKey: [QUERY_KEYS.EVAL_REPORT, 'report', runId],
    queryFn: () => orchestrationApi.getRunReport(runId),
    enabled: !!runId,
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Get regression diff ────────────────────────────────────────────────────────
export function useRegressionDiff(runId: string, enabled = true) {
  return useQuery({
    queryKey: [QUERY_KEYS.REGRESSION_DIFF, runId],
    queryFn: () => orchestrationApi.getRegressionDiff(runId),
    enabled: !!runId && enabled,
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── List schedules ─────────────────────────────────────────────────────────────
export function useEvalSchedules() {
  return useQuery({
    queryKey: [QUERY_KEYS.SCOPES, 'schedules'],
    queryFn: () => orchestrationApi.listSchedules(),
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Create schedule ────────────────────────────────────────────────────────────
export function useCreateSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: EvalScheduleCreate) => orchestrationApi.createSchedule(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.SCOPES, 'schedules'] });
    },
  });
}

// ── Update schedule ────────────────────────────────────────────────────────────
export function useUpdateSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: EvalScheduleUpdate }) =>
      orchestrationApi.updateSchedule(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.SCOPES, 'schedules'] });
    },
  });
}

// ── Delete schedule ────────────────────────────────────────────────────────────
export function useDeleteSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => orchestrationApi.deleteSchedule(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.SCOPES, 'schedules'] });
    },
  });
}

// ── Get analytics trends ───────────────────────────────────────────────────────
export function useEvalTrends(days = 30, tableId?: string) {
  return useQuery({
    queryKey: [QUERY_KEYS.TRENDS, days, tableId],
    queryFn: () => orchestrationApi.getTrends(days, tableId),
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Get table analytics ────────────────────────────────────────────────────────
export function useTableAnalytics() {
  return useQuery({
    queryKey: [QUERY_KEYS.TABLE_ANALYTICS],
    queryFn: () => orchestrationApi.getTableAnalytics(),
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Compare two runs ───────────────────────────────────────────────────────────
export function useCompareRuns(run1: string, run2: string) {
  return useQuery({
    queryKey: [QUERY_KEYS.EVAL_REPORT, 'compare', run1, run2],
    queryFn: () => orchestrationApi.compareRuns(run1, run2),
    enabled: !!run1 && !!run2,
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── List alerts ────────────────────────────────────────────────────────────────
export function useEvalAlerts(acknowledged?: boolean, limit = 50) {
  return useQuery({
    queryKey: [QUERY_KEYS.ALERTS, acknowledged, limit],
    queryFn: () => orchestrationApi.listAlerts(acknowledged, limit),
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Acknowledge alert ──────────────────────────────────────────────────────────
export function useAcknowledgeAlert() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (alertId: string) => orchestrationApi.acknowledgeAlert(alertId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.ALERTS] });
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.SYSTEM_HEALTH] });
    },
  });
}

// ── Get overall system health ──────────────────────────────────────────────────
export function useSystemHealth() {
  return useQuery({
    queryKey: [QUERY_KEYS.SYSTEM_HEALTH],
    queryFn: () => orchestrationApi.getSystemHealth(),
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Get evaluations readiness status ───────────────────────────────────────────
export function useEvalReadiness() {
  return useQuery({
    queryKey: [QUERY_KEYS.TABLES, 'readiness'],
    queryFn: () => orchestrationApi.getReadiness(),
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Get table health ───────────────────────────────────────────────────────────
export function useTableHealth(tableId: string) {
  return useQuery({
    queryKey: [QUERY_KEYS.HEALTH, tableId],
    queryFn: () => healthApi.get(tableId),
    enabled: !!tableId,
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Recompute table health ─────────────────────────────────────────────────────
export function useRecomputeHealth() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (tableId: string) => healthApi.recompute(tableId),
    onSuccess: (_, tableId) => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.HEALTH, tableId] });
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.HEALTH, 'all'] });
    },
  });
}

// ── Get all tables health ──────────────────────────────────────────────────────
export function useAllTablesHealth() {
  return useQuery({
    queryKey: [QUERY_KEYS.HEALTH, 'all'],
    queryFn: () => healthApi.getAll(),
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}
