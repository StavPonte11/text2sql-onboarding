// Domain types matching the strict schema
export type TableStatus = "draft" | "sandbox" | "verified" | "production" | "degraded";
export type DifficultyLevel = "simple" | "medium" | "complex";
export type EvalStatus = "running" | "completed" | "failed";

export interface Table {
  id: string;
  name: string;
  schema_name: string;
  status: TableStatus;
  owner_id: string;
  created_at: string;
  updated_at: string;
}

export interface TableCreate {
  name: string;
  schema_name: string;
  owner_id: string;
}

export interface ColumnDef {
  name: string;
  description: string;
  is_geo?: boolean;
  is_time?: boolean;
}

export interface EnrichmentData {
  table_description: string;
  columns: ColumnDef[];
}

export interface EnrichmentVersion {
  id: string;
  table_id: string;
  version: number;
  data: EnrichmentData;
  created_at: string;
}

export interface GoldenQuestion {
  id: string;
  table_id: string;
  question: string;
  expected_sql: string;
  difficulty: DifficultyLevel;
  created_at: string;
}

export interface GoldenQuestionCreate {
  question: string;
  expected_sql: string;
  difficulty: DifficultyLevel;
}

export interface EvalRun {
  id: string;
  table_id: string;
  score: number;
  status: EvalStatus;
  created_at: string;
}

export interface EvalResult {
  id: string;
  run_id: string;
  question_id: string;
  score: number;
  status: "pass" | "fail";
  error_type?: string;
}

export interface UserScope {
  id: string;
  user_id: string;
  name: string;
  is_active: boolean;
}

export interface UserScopeCreate {
  user_id: string;
  name: string;
}

export interface AuditQuery {
  id: string;
  table_id?: string;
  user_id: string;
  session_id?: string;
  raw_question: string;
  normalized_question?: string;
  tables_accessed?: string[];
  final_sql?: string;
  result_row_count?: number;
  result_columns?: string[];
  execution_time_ms?: number;
  refiner_iterations?: number;
  status: string;
  error_message?: string;
  langfuse_trace_id?: string;
  created_at: string;
}

export interface PublishError {
  code: string;
  message: string;
}
