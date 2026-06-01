import type { components } from '../api/schema';

type Schemas = components['schemas'];

// Domain types matching the strict schema
export type TableStatus = Schemas['TableStatus'];
export type DifficultyLevel = Schemas['DifficultyLevel'];
export type EvalStatus = Schemas['EvalStatus'];
export type QuestionType = Schemas['QuestionType'];
export type ProfilingStatus = Schemas['ProfilingStatus'];
export type FeedbackRating = Schemas['FeedbackRating'];
export type HealthStatus = Schemas['HealthStatus'];

// Interfaces
export type Table = Schemas['TableRead'];
export type TableCreate = Schemas['TableCreate'];

export type GoldenQuestion = Schemas['GoldenQuestionRead'];
export type GoldenQuestionCreate = Schemas['GoldenQuestionCreate'];

export type EvalRun = Schemas['EvalRunRead'];
export type EvalResult = Schemas['EvalResultRead'];

export type UserScope = Schemas['UserScopeRead'];
export type UserScopeCreate = Schemas['UserScopeCreate'];

export type AuditQuery = Schemas['AuditQueryRead'];

export type TableProfile = Omit<
  Schemas['TableProfileRead'],
  'sample_data' | 'profile_json' | 'auto_insights'
> & {
  sample_data?: any[];
  profile_json?: any;
  auto_insights?: any[];
};

export type ColumnProfile = Omit<Schemas['ColumnProfileRead'], 'stats_json' | 'top_values'> & {
  stats_json?: any;
  top_values?: any[];
};

export type CrossTableProfile = Schemas['CrossTableProfileRead'];

export type QueryFeedback = Schemas['QueryFeedbackRead'];
export type QueryFeedbackCreate = Schemas['QueryFeedbackCreate'];

export type TableHealth = Schemas['TableHealthRead'];

// Extra frontend-only definitions not mapped 1:1 in openapi
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

export interface PublishError {
  code: string;
  message: string;
}

export interface ForeignKeyMappingRead {
  id: string;
  table_id: string;
  source_column: string;
  target_table_id: string;
  target_column: string;
  created_at: string;
  updated_at: string;
}

export interface ForeignKeyMappingCreate {
  source_column: string;
  target_table_id: string;
  target_column: string;
}

export type ForeignKeyMapping = ForeignKeyMappingRead;

// Profiling-specific nested schemas not fully typed by OpenAPI ref (json columns)
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
