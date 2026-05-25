// Domain types matching the strict schema
export type TableStatus = "draft" | "sandbox" | "verified" | "production" | "degraded";
export type DifficultyLevel = "simple" | "medium" | "complex";
export type EvalStatus = "running" | "completed" | "failed";
export type QuestionType = "simple" | "complex" | "join" | "geo" | "aggregate" | "time_series";
export type ProfilingStatus = "pending" | "running" | "completed" | "failed";
export type FeedbackRating = "positive" | "negative";
export type HealthStatus = "good" | "warning" | "critical";

export interface Table {
  id: string;
  name: string;
  schema_name: string;
  status: TableStatus;
  owner_id?: string;
  oasis_source_id?: string;
  created_at: string;
  updated_at: string;
}

export interface TableCreate {
  oasis_source_id: string;
}

export interface ColumnDef {
  name: string;
  description: string;
  dataType?: string;
  children?: ColumnDef[];
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
  question_type: QuestionType;
  coverage_tags?: string[];
  created_at: string;
}

export interface GoldenQuestionCreate {
  question: string;
  expected_sql: string;
  difficulty: DifficultyLevel;
  question_type?: QuestionType;
  coverage_tags?: string[];
}

export interface EvalRun {
  id: string;
  table_id: string;
  table_name?: string;
  score: number;
  pass_rate: number;
  fail_rate: number;
  total_questions: number;
  duration_seconds?: number;
  triggered_by: string;
  status: EvalStatus;
  started_at: string;
  completed_at?: string;
  failure_breakdown?: Record<string, number>;
  dimension_averages?: Record<string, number>;
  regression_detected: boolean;
  regression_delta?: number;
  promotion_run_id?: string;
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
  confidence_score?: number;
  explanation_text?: string;
  warnings_json?: string[];
  created_at: string;
}

export interface PublishError {
  code: string;
  message: string;
}

// ── Profiling ─────────────────────────────────────────────────────────────────

export interface TableProfile {
  id: string;
  table_id: string;
  status: ProfilingStatus;
  row_count?: number;
  column_count?: number;
  size_bytes?: number;
  null_rate_avg?: number;
  duplicate_rate?: number;
  sample_data?: Record<string, unknown>[];
  auto_insights?: string[];
  cached_until?: string;
  created_at: string;
  updated_at: string;
}

export interface RowFieldStats {
  type?: string;
  null_count?: number;
  null_rate?: number;
  distinct_count?: number;
  top_values?: { value: string; count: number }[];
  min?: string;
  max?: string;
  avg?: number;
  q25?: number;
  median?: number;
  q75?: number;
  stddev?: number;
  histogram?: { lo: number | null; hi: number | null; count: number; label: string }[];
  children?: RowField[];
  note?: string;
}

export interface RowField {
  name: string;
  data_type: string;
  semantic_type?: string;
  is_time?: boolean;
  is_geo?: boolean;
  null_count?: number;
  null_rate?: number;
  distinct_count?: number;
  top_values?: { value: string; count: number }[];
  min_value?: string;
  max_value?: string;
  stats?: RowFieldStats;
}

export interface ColumnProfile {
  id: string;
  table_id: string;
  profile_id: string;
  column_name: string;
  data_type?: string;
  semantic_type?: string;
  null_count?: number;
  null_rate?: number;
  distinct_count?: number;
  min_value?: string;
  max_value?: string;
  avg_value?: number;
  top_values?: { value: string; count: number }[];
  is_geo: boolean;
  is_time: boolean;
  stats_json?: {
    type?: string;
    children?: RowField[];
    histogram?: { lo: number | null; hi: number | null; count: number; label: string }[];
    [key: string]: unknown;
  };
  created_at: string;
}

export interface CrossTableProfile {
  id: string;
  source_table_id: string;
  target_table_id: string;
  join_suggestion?: string;
  match_strength: "strong" | "weak";
  common_columns?: string[];
  created_at: string;
}

// ── Feedback ──────────────────────────────────────────────────────────────────

export interface QueryFeedback {
  id: string;
  user_id: string;
  query_id: string;
  table_id?: string;
  rating: FeedbackRating;
  comment?: string;
  suggested_correction?: string;
  created_at: string;
}

export interface QueryFeedbackCreate {
  user_id: string;
  query_id: string;
  table_id?: string;
  rating: FeedbackRating;
  comment?: string;
  suggested_correction?: string;
}

// ── Table Health ──────────────────────────────────────────────────────────────

export interface TableHealth {
  id: string;
  table_id: string;
  health_score: number;
  health_status: HealthStatus;
  eval_success_rate?: number;
  feedback_ratio?: number;
  data_quality_score?: number;
  schema_drift_flag: boolean;
  failure_wrong_table: number;
  failure_wrong_sql: number;
  failure_empty_result: number;
  failure_execution_error: number;
  updated_at: string;
}
