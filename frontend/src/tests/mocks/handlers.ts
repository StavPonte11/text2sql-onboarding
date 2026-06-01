import { http, HttpResponse } from 'msw';

import { API_BASE_URL } from '../../config/constants';

// Initial Mock State
const tables = [
  {
    id: 'table-1',
    name: 'orders',
    schema_name: 'ecommerce',
    catalog: 'minio',
    service: 'local_trino',
    description: 'E-commerce orders table',
    status: 'draft',
    owner_id: 'user-1',
    oasis_source_id: 'src-1',
    created_at: '2026-05-28T00:00:00Z',
    updated_at: '2026-05-28T00:00:00Z',
  },
  {
    id: 'table-2',
    name: 'users',
    schema_name: 'ecommerce',
    catalog: 'minio',
    service: 'local_trino',
    description: 'Registered users details',
    status: 'sandbox',
    owner_id: 'user-1',
    oasis_source_id: 'src-2',
    created_at: '2026-05-28T00:00:00Z',
    updated_at: '2026-05-28T00:00:00Z',
  },
];

let questions = [
  {
    id: 'q-1',
    table_id: 'table-1',
    question: 'Show all orders last month',
    expected_sql: 'SELECT * FROM orders WHERE date > now()',
    difficulty: 'simple',
    question_type: 'simple',
    created_at: '2026-05-28T00:00:00Z',
  },
];

const evalRuns = [
  {
    id: 'run-1',
    table_id: 'table-1',
    table_name: 'orders',
    dataset_id: null,
    score: 85.5,
    pass_rate: 0.85,
    fail_rate: 0.15,
    total_questions: 20,
    duration_seconds: 12.5,
    triggered_by: 'user',
    status: 'completed',
    started_at: '2026-05-28T00:01:00Z',
    completed_at: '2026-05-28T00:01:12.5Z',
    failure_breakdown: { syntax_error: 2, semantic_error: 1 },
    dimension_averages: { correctness: 8.5, performance: 9.0 },
    regression_detected: false,
    regression_delta: null,
    promotion_run_id: null,
    created_at: '2026-05-28T00:01:00Z',
  },
];

let schedules = [
  {
    id: 'sched-1',
    dataset_id: 'dataset-gold',
    table_scope: ['table-1'],
    cron_expression: '0 0 * * *',
    enabled: true,
    created_by: 'user-1',
    created_at: '2026-05-28T00:00:00Z',
    last_run_at: '2026-05-28T00:00:00Z',
    next_run_at: '2026-05-29T00:00:00Z',
  },
];

const alerts = [
  {
    id: 'alert-1',
    run_id: 'run-1',
    table_id: 'table-1',
    alert_type: 'regression',
    severity: 'critical',
    message: 'Score dropped significantly by 15% on table orders',
    details: { drop: 0.15 },
    acknowledged: false,
    created_at: '2026-05-28T00:02:00Z',
  },
];

export const handlers = [
  // ── Tables API ─────────────────────────────────────────────────────────────
  http.get(`${API_BASE_URL}/tables`, ({ request }) => {
    const url = new URL(request.url);
    const status = url.searchParams.get('status');
    let filtered = tables;
    if (status) {
      filtered = tables.filter((t) => t.status === status);
    }
    return HttpResponse.json(filtered);
  }),

  http.get(`${API_BASE_URL}/tables/:id`, ({ params }) => {
    const { id } = params;
    const table = tables.find((t) => t.id === id);
    if (!table) {
      return new HttpResponse(null, { status: 404, statusText: 'Table not found' });
    }
    return HttpResponse.json(table);
  }),

  http.post(`${API_BASE_URL}/tables`, async ({ request }) => {
    const payload = (await request.json()) as any;
    const newTable = {
      id: `table-${Date.now()}`,
      name: payload.oasis_source_id ? `table_${payload.oasis_source_id}` : 'new_table',
      schema_name: 'ecommerce',
      catalog: 'minio',
      service: 'local_trino',
      description: 'Auto-created table from Oasis source',
      status: 'draft',
      owner_id: 'user-1',
      oasis_source_id: payload.oasis_source_id || 'src-new',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    tables.push(newTable);
    return HttpResponse.json(newTable, { status: 201 });
  }),

  http.patch(`${API_BASE_URL}/tables/:id/status`, ({ params, request }) => {
    const { id } = params;
    const url = new URL(request.url);
    const status = url.searchParams.get('status');
    const tableIndex = tables.findIndex((t) => t.id === id);
    if (tableIndex === -1) {
      return new HttpResponse(null, { status: 404, statusText: 'Table not found' });
    }
    if (status) {
      tables[tableIndex].status = status;
    }
    return HttpResponse.json(tables[tableIndex]);
  }),

  http.post(`${API_BASE_URL}/tables/:id/sync-schema`, ({ params }) => {
    const { id } = params;
    const table = tables.find((t) => t.id === id);
    if (!table) {
      return new HttpResponse(null, { status: 404, statusText: 'Table not found' });
    }
    table.updated_at = new Date().toISOString();
    return HttpResponse.json(table);
  }),

  // ── Enrichment API ─────────────────────────────────────────────────────────
  http.get(`${API_BASE_URL}/tables/:id/enrichment/latest`, ({ params }) => {
    return HttpResponse.json({
      id: 'enrich-1',
      table_id: params.id,
      version: 1,
      data: {
        table_description: 'Enriched description for table',
        columns: [
          { name: 'id', dataType: 'INTEGER', description: 'Unique row ID' },
          { name: 'value', dataType: 'VARCHAR', description: 'Row string value' },
        ],
      },
      created_at: '2026-05-28T00:00:00Z',
    });
  }),

  http.post(`${API_BASE_URL}/tables/:id/enrichment`, async ({ params, request }) => {
    const payload = (await request.json()) as any;
    return HttpResponse.json({
      id: `enrich-${Date.now()}`,
      table_id: params.id,
      version: 2,
      data: payload.data,
      created_at: new Date().toISOString(),
    });
  }),

  // ── Questions API ──────────────────────────────────────────────────────────
  http.get(`${API_BASE_URL}/tables/:id/questions`, ({ params }) => {
    const { id } = params;
    return HttpResponse.json(questions.filter((q) => q.table_id === id));
  }),

  http.post(`${API_BASE_URL}/tables/:id/questions`, async ({ params, request }) => {
    const payload = (await request.json()) as any;
    const newQ = {
      id: `q-${Date.now()}`,
      table_id: params.id as string,
      question: payload.question,
      expected_sql: payload.expected_sql,
      difficulty: payload.difficulty || 'simple',
      question_type: payload.question_type || 'simple',
      created_at: new Date().toISOString(),
    };
    questions.push(newQ);
    return HttpResponse.json(newQ, { status: 201 });
  }),

  http.delete(`${API_BASE_URL}/tables/:id/questions/:questionId`, ({ params }) => {
    const { questionId } = params;
    questions = questions.filter((q) => q.id !== questionId);
    return new HttpResponse(null, { status: 204 });
  }),

  // ── Evaluation API ─────────────────────────────────────────────────────────
  http.post(`${API_BASE_URL}/tables/:id/eval/run`, ({ params }) => {
    const { id } = params;
    const table = tables.find((t) => t.id === id);
    const newRun = {
      id: `run-${Date.now()}`,
      table_id: id as string,
      table_name: table ? table.name : 'unknown',
      dataset_id: null,
      score: 100.0,
      pass_rate: 1.0,
      fail_rate: 0.0,
      total_questions: 1,
      duration_seconds: 2.1,
      triggered_by: 'user',
      status: 'running',
      started_at: new Date().toISOString(),
      completed_at: null,
      failure_breakdown: null,
      dimension_averages: null,
      regression_detected: false,
      regression_delta: null,
      promotion_run_id: null,
      created_at: new Date().toISOString(),
    };
    evalRuns.push(newRun as any);
    return HttpResponse.json(newRun);
  }),

  http.get(`${API_BASE_URL}/eval/runs/all`, () => {
    return HttpResponse.json(evalRuns);
  }),

  http.get(`${API_BASE_URL}/evaluations/runs`, ({ request }) => {
    const url = new URL(request.url);
    const tableId = url.searchParams.get('table_id');
    let filtered = evalRuns;
    if (tableId) {
      filtered = evalRuns.filter((r) => r.table_id === tableId);
    }
    return HttpResponse.json(filtered);
  }),

  http.get('*/eval/readiness', () => {
    return HttpResponse.json({
      'table-1': { ready: true, missing: [] },
      'table-2': { ready: false, missing: ['Golden questions'] },
    });
  }),

  http.get(`${API_BASE_URL}/eval/:runId`, ({ params }) => {
    const { runId } = params;
    const run = evalRuns.find((r) => r.id === runId);
    if (!run) {
      return new HttpResponse(null, { status: 404, statusText: 'Run not found' });
    }
    return HttpResponse.json(run);
  }),

  http.get(`${API_BASE_URL}/eval/:runId/results`, ({ params }) => {
    return HttpResponse.json([
      {
        id: 'res-1',
        run_id: params.runId,
        question_id: 'q-1',
        question: 'Show all orders last month',
        generated_sql: 'SELECT * FROM orders WHERE date > now()',
        expected_sql: 'SELECT * FROM orders WHERE date > now()',
        is_correct: true,
        score: 100,
        latency_ms: 250,
        error_message: null,
      },
    ]);
  }),

  // ── Health API ─────────────────────────────────────────────────────────────
  http.get(`${API_BASE_URL}/health/all`, () => {
    return HttpResponse.json([
      {
        table_id: 'table-1',
        status: 'healthy',
        last_eval_score: 85.5,
        questions_count: 1,
        schema_synced: true,
        updated_at: '2026-05-28T00:00:00Z',
      },
    ]);
  }),

  http.get(`${API_BASE_URL}/tables/:id/health`, ({ params }) => {
    return HttpResponse.json({
      table_id: params.id,
      status: 'healthy',
      last_eval_score: 85.5,
      questions_count: 1,
      schema_synced: true,
      updated_at: '2026-05-28T00:00:00Z',
    });
  }),

  http.post(`${API_BASE_URL}/tables/:id/health/recompute`, ({ params }) => {
    return HttpResponse.json({
      table_id: params.id,
      status: 'healthy',
      last_eval_score: 95.0,
      questions_count: 1,
      schema_synced: true,
      updated_at: new Date().toISOString(),
    });
  }),

  // ── Orchestration / Schedules / System Health / Alerts ────────────────────
  http.get(`${API_BASE_URL}/evaluations/system-health`, () => {
    return HttpResponse.json({
      global_score: 85.5,
      global_pass_rate: 0.85,
      active_alerts: 1,
      critical_alerts: 1,
      last_evaluation: '2026-05-28T00:01:12.5Z',
      total_tables: 2,
      production_tables: 0,
      total_runs_today: 1,
      top_failing_tables: [],
      recent_runs: evalRuns,
      system_status: 'healthy',
    });
  }),

  http.get(`${API_BASE_URL}/evaluations/analytics/tables`, () => {
    return HttpResponse.json([
      {
        table_id: 'table-1',
        table_name: 'orders',
        status: 'draft',
        latest_score: 85.5,
        avg_score: 85.5,
        pass_rate: 0.85,
        run_count: 1,
        trend: 'stable',
        failure_breakdown: { syntax_error: 2 },
      },
    ]);
  }),

  http.get(`${API_BASE_URL}/evaluations/analytics/trends`, () => {
    return HttpResponse.json({
      runs: [
        {
          run_id: 'run-1',
          table_id: 'table-1',
          date: '2026-05-28',
          timestamp: '2026-05-28T00:01:00Z',
          score: 85.5,
          pass_rate: 0.85,
          fail_rate: 0.15,
          regression_detected: false,
        },
      ],
      daily: [
        {
          date: '2026-05-28',
          avg_score: 85.5,
          avg_pass_rate: 0.85,
          run_count: 1,
        },
      ],
      total_runs: 1,
    });
  }),

  http.get(`${API_BASE_URL}/evaluations/alerts`, () => {
    return HttpResponse.json(alerts);
  }),

  http.post(`${API_BASE_URL}/evaluations/alerts/:id/acknowledge`, ({ params }) => {
    const { id } = params;
    const alert = alerts.find((a) => a.id === id);
    if (alert) {
      alert.acknowledged = true;
    }
    return HttpResponse.json(alert);
  }),

  http.get(`${API_BASE_URL}/evaluations/schedules`, () => {
    return HttpResponse.json(schedules);
  }),

  http.post(`${API_BASE_URL}/evaluations/schedules`, async ({ request }) => {
    const payload = (await request.json()) as any;
    const newSchedule = {
      id: `sched-${Date.now()}`,
      dataset_id: payload.dataset_id,
      table_scope: payload.table_scope || null,
      cron_expression: payload.cron_expression,
      enabled: payload.enabled,
      created_by: payload.created_by || 'user-1',
      created_at: new Date().toISOString(),
      last_run_at: null,
      next_run_at: new Date().toISOString(),
    };
    schedules.push(newSchedule);
    return HttpResponse.json(newSchedule);
  }),

  http.delete(`${API_BASE_URL}/evaluations/schedules/:id`, ({ params }) => {
    const { id } = params;
    schedules = schedules.filter((s) => s.id !== id);
    return new HttpResponse(null, { status: 204 });
  }),
];
