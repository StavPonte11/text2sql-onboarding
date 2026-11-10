import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';

import { RunHistoryTable } from '../components/monitoring/RunHistoryTable';
import { API_BASE_URL as API } from '../config/constants';
import { server } from './mocks/server';
import { renderWithProviders } from './setup';

const runningRun = {
  id: 'run-live',
  table_id: null,
  table_name: 'Connection: AdventureWorksDW',
  dataset_id: 'text2sql_connection_6',
  connection_id: 6,
  connection_name: 'AdventureWorksDW',
  score: 0,
  pass_rate: 0,
  fail_rate: 0,
  total_questions: 3,
  completed_questions: 2,
  duration_seconds: null,
  triggered_by: 'text2sql_production',
  status: 'running',
  started_at: '2026-10-21T18:00:00',
  completed_at: null,
  failure_breakdown: null,
  dimension_averages: null,
  regression_detected: false,
  regression_delta: null,
  promotion_run_id: null,
  created_at: '2026-10-21T18:00:00',
};

const runningReport = {
  run_id: 'run-live',
  status: 'running',
  overall_score: 0,
  pass_rate: 0,
  fail_rate: 0,
  total_questions: 3,
  completed_questions: 2,
  duration_seconds: null,
  triggered_by: 'text2sql_production',
  is_publishable: false,
  failure_breakdown: {},
  dimension_averages: {},
  started_at: '2026-10-21T18:00:00',
  completed_at: null,
  per_question: [
    {
      question_id: 'q1',
      question: 'Which products have never been sold?',
      score: 1,
      status: 'pass',
      failure_type: null,
    },
    {
      question_id: 'q2',
      question: 'What is the average order value?',
      score: 0,
      status: 'fail',
      failure_type: 'x',
    },
    {
      question_id: 'q3',
      question: 'Revenue by sales territory?',
      score: null,
      status: 'pending',
      failure_type: null,
    },
  ],
};

describe('live evaluation progress', () => {
  test('run list and run report show how many questions finished', async () => {
    server.use(
      http.get(`${API}/evaluations/runs`, () => HttpResponse.json([runningRun])),
      http.get(`${API}/evaluations/runs/run-live/report`, () => HttpResponse.json(runningReport)),
    );
    renderWithProviders(<RunHistoryTable limit={10} />);

    expect(await screen.findByText('2/3')).toBeInTheDocument(); // run list row

    await userEvent.click(screen.getByText('2/3')); // opens the run report
    await waitFor(() =>
      expect(screen.getByText('Evaluating 2 of 3 questions')).toBeInTheDocument(),
    );
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '2');
    expect(screen.getByText('0.50 so far')).toBeInTheDocument(); // 1 of 2 finished passed
    expect(screen.getByText('Question Results (3)')).toBeInTheDocument();
  });
});

describe('finished run details', () => {
  const finishedRun = {
    ...runningRun,
    status: 'completed',
    duration_seconds: 8593.59,
    score: 1,
    dimension_averages: { time_shift_score: 0.25 },
  };
  const finishedReport = {
    ...runningReport,
    status: 'completed',
    duration_seconds: 8593.59,
    per_question: [
      {
        question_id: 'q1',
        question: 'How many orders?',
        score: 0,
        status: 'fail',
        failure_type: null,
      },
    ],
  };

  test('shows the duration in minutes and opens a question with its metrics', async () => {
    server.use(
      http.get(`${API}/evaluations/runs`, () => HttpResponse.json([finishedRun])),
      http.get(`${API}/evaluations/runs/run-live/report`, () => HttpResponse.json(finishedReport)),
      http.get(`${API}/evaluations/runs/run-live/questions/q1`, () =>
        HttpResponse.json({
          run_id: 'run-live',
          question_id: 'q1',
          question: 'How many orders?',
          expected_sql: 'SELECT 1',
          score: 0,
          status: 'fail',
          failure_type: null,
          trace_unavailable_reason: null,
          trace: {
            trace_id: 't1',
            langfuse_url: 'https://langfuse.test/project/p/traces/t1',
            succeeded: false,
            error: 'sql_execution_failure',
            generated_sql: 'SELECT bad',
            executed_generated_sql: null,
            expected_sql: 'SELECT 1',
            agent_status: 'error',
            expected_run: {
              sql: 'SELECT 1',
              execution_time_ms: 2500,
              success: true,
              columns: ['order_total'],
              rows: [[42]],
              row_count: 1,
            },
            generated_run: {
              sql: 'SELECT bad',
              execution_time_ms: 10,
              success: false,
              columns: [],
              rows: [],
              row_count: 0,
              error: 'Schema does not exist',
            },
            scores: [
              {
                name: 'contains_accuracy',
                value: 0,
                passed: false,
                applicable: true,
                error: null,
                details: { reason: 'generated_sql_execution_failed' },
              },
              {
                name: 'time_shift',
                value: null,
                passed: false,
                applicable: false,
                error: null,
                details: {},
              },
            ],
            metadata: { total_latency_ms: 90000, evaluation_config: { agent_dialect: 'trino' } },
          },
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<RunHistoryTable />);

    expect(await screen.findByText('Contains Accuracy')).toBeInTheDocument();
    expect(screen.getByText('Time Shift')).toBeInTheDocument();
    expect(screen.getByText('1.00')).toBeInTheDocument();
    expect(screen.getByText('0.25')).toBeInTheDocument();

    await user.click(await screen.findByText('AdventureWorksDW'));
    expect(await screen.findByText('143.2 min')).toBeInTheDocument();

    await user.click(await screen.findByRole('button', { name: /How many orders/ }));

    expect(await screen.findByText('Question Result')).toBeInTheDocument();
    expect(screen.getByText('contains accuracy')).toBeInTheDocument();
    expect(screen.getByText('generated sql execution failed')).toBeInTheDocument();
    expect(screen.getByText('N/A')).toBeInTheDocument();
    expect(screen.getByText('order_total')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('Schema does not exist')).toBeInTheDocument();
    expect(screen.getByText('1.5 min')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open in Langfuse/ })).toHaveAttribute(
      'href',
      'https://langfuse.test/project/p/traces/t1',
    );
  });
});
