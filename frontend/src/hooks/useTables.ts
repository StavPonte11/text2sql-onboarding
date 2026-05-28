import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { tablesApi, enrichmentApi, questionsApi } from "../api/client";
import { QUERY_KEYS, QUERY_CONFIG } from "../config/constants";
import type { TableCreate, GoldenQuestionCreate } from "../types";

// ── Tables list ────────────────────────────────────────────────────────────────
export function useTables(params?: { status?: string; owner_id?: string; search?: string }) {
  return useQuery({
    queryKey: [QUERY_KEYS.TABLES, params],
    queryFn: () => tablesApi.list(params),
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Single table details ───────────────────────────────────────────────────────
export function useTableDetails(id: string) {
  return useQuery({
    queryKey: [QUERY_KEYS.TABLE_DETAILS, id],
    queryFn: () => tablesApi.get(id),
    enabled: !!id,
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Create table ───────────────────────────────────────────────────────────────
export function useCreateTable() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: TableCreate) => tablesApi.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.TABLES] });
    },
  });
}

// ── Update table status ────────────────────────────────────────────────────────
export function useUpdateTableStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      tablesApi.updateStatus(id, status),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.TABLES] });
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.TABLE_DETAILS, variables.id] });
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.PENDING_TABLES] });
    },
  });
}

// ── Sync table schema ──────────────────────────────────────────────────────────
export function useSyncTableSchema() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => tablesApi.syncSchema(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.TABLES] });
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.TABLE_DETAILS, id] });
    },
  });
}

// ── Table enrichment ───────────────────────────────────────────────────────────
export function useTableEnrichment(tableId: string) {
  return useQuery({
    queryKey: [QUERY_KEYS.ENRICHMENT, tableId],
    queryFn: () => enrichmentApi.getLatest(tableId),
    enabled: !!tableId,
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Create enrichment ──────────────────────────────────────────────────────────
export function useCreateEnrichment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tableId, data }: { tableId: string; data: any }) =>
      enrichmentApi.create(tableId, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.ENRICHMENT, variables.tableId] });
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.TABLES] });
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.TABLE_DETAILS, variables.tableId] });
    },
  });
}

// ── Table questions ────────────────────────────────────────────────────────────
export function useTableQuestions(tableId: string) {
  return useQuery({
    queryKey: [QUERY_KEYS.QUESTIONS, tableId],
    queryFn: () => questionsApi.list(tableId),
    enabled: !!tableId,
    staleTime: QUERY_CONFIG.DEFAULT_STALE_TIME,
  });
}

// ── Create question ────────────────────────────────────────────────────────────
export function useCreateQuestion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tableId, payload }: { tableId: string; payload: GoldenQuestionCreate }) =>
      questionsApi.create(tableId, payload),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.QUESTIONS, variables.tableId] });
    },
  });
}

// ── Upload questions file ──────────────────────────────────────────────────────
export function useUploadQuestions() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tableId, file }: { tableId: string; file: File }) =>
      questionsApi.uploadQuestions(tableId, file),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.QUESTIONS, variables.tableId] });
    },
  });
}

// ── Delete question ────────────────────────────────────────────────────────────
export function useDeleteQuestion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tableId, questionId }: { tableId: string; questionId: string }) =>
      questionsApi.delete(tableId, questionId),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEYS.QUESTIONS, variables.tableId] });
    },
  });
}
