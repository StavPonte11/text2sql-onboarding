/**
 * Centralized Application Configuration and Constants
 */

// API base URL configuration
export const API_BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// Query keys for React Query / TanStack Query
export const QUERY_KEYS = {
  TABLES: "tables",
  TABLE_DETAILS: "table-details",
  ENRICHMENT: "enrichment",
  QUESTIONS: "questions",
  EVAL_RUNS: "eval-runs",
  EVAL_REPORT: "eval-report",
  REGRESSION_DIFF: "regression-diff",
  SCOPES: "scopes",
  AUDIT_QUERIES: "audit-queries",
  TABLE_PROFILE: "table-profile",
  COLUMN_PROFILES: "column-profiles",
  CROSS_PROFILES: "cross-profiles",
  FEEDBACK: "feedback",
  HEALTH: "health",
  PENDING_TABLES: "pending-tables",
  SYSTEM_HEALTH: "system-health",
  ALERTS: "alerts",
  TRENDS: "trends",
  TABLE_ANALYTICS: "table-analytics",
} as const;

// Default pagination values
export const PAGINATION = {
  DEFAULT_LIMIT: 50,
  DEFAULT_PAGE_SIZE: 10,
  COMPACT_PAGE_SIZE: 5,
} as const;

// Query caching and retry configuration
export const QUERY_CONFIG = {
  DEFAULT_STALE_TIME: 1000 * 60 * 5, // 5 minutes
  DEFAULT_RETRY_COUNT: 1,
  POLLING_INTERVAL_SANDBOX: 15000,   // 15 seconds
} as const;

// Table statuses
export const TABLE_STATUS = {
  DRAFT: "draft",
  SANDBOX: "sandbox",
  VERIFIED: "verified",
  PRODUCTION: "production",
  DEGRADED: "degraded",
} as const;
