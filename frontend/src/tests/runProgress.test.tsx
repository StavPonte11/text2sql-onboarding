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
    expect(screen.getByText('50% so far')).toBeInTheDocument(); // 1 of 2 finished passed
    expect(screen.getByText('Question Results (3)')).toBeInTheDocument();
  });
});
