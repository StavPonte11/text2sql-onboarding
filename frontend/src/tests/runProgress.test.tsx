import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';

import { QuestionResultModal } from '../components/monitoring/QuestionResultModal';
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
  score: 0.5,
  score_breakdown: {
    questions: 2,
    time_shift: { count: 0, average: null },
    contains_accuracy: { count: 2, average: 0.5 },
    unscored: 0,
  },
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
    expect(screen.getByText('0.50 so far')).toBeInTheDocument(); // the score so far
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
    final_score: 0.88,
    score_breakdown: {
      questions: 2,
      time_shift: { count: 1, average: 0.75 },
      contains_accuracy: { count: 1, average: 1 },
      unscored: 0,
    },
  };
  const finishedReport = {
    ...runningReport,
    status: 'completed',
    duration_seconds: 8593.59,
    score: 0.88,
    score_breakdown: finishedRun.score_breakdown,
    per_question: [
      {
        question_id: 'q1',
        question: 'How many orders?',
        score: 0,
        status: 'fail',
        failure_type: null,
        scores: { contains_accuracy: 0, time_shift: 0.75 },
        final_score: 0.75,
        score_source: 'time_shift',
      },
      {
        question_id: 'q2',
        question: 'Top customers?',
        score: 1,
        status: 'pass',
        failure_type: null,
        scores: { contains_accuracy: 1 },
        final_score: 1,
        score_source: 'contains_accuracy',
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
          final_score: 0.75,
          score_source: 'time_shift',
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
              {
                name: 'llm_judge',
                value: 0,
                passed: false,
                applicable: false,
                error: null,
                details: { reason: 'generated_sql_did_not_run' },
              },
            ],
            metadata: { total_latency_ms: 90000, evaluation_config: { agent_dialect: 'trino' } },
          },
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<RunHistoryTable />);

    // one Score column: the combined score, not contains accuracy (1.00) or time shift (0.25)
    expect(await screen.findByText('Score')).toBeInTheDocument();
    expect(screen.queryByText('Contains Accuracy')).not.toBeInTheDocument();
    expect(screen.queryByText('Time Shift')).not.toBeInTheDocument();
    expect(screen.getByText('0.88')).toBeInTheDocument();

    // hovering (i) explains the calculation
    await user.hover(screen.getByRole('img', { name: 'How the score is calculated' }));
    expect(
      await screen.findByText('1 question (50%) scored by time shift: average 0.75'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('1 question (50%) scored by contains accuracy: average 1.00'),
    ).toBeInTheDocument();
    expect(screen.getByText('Score = (1 × 0.75 + 1 × 1.00) / 2 = 0.88')).toBeInTheDocument();
    await user.unhover(screen.getByRole('img', { name: 'How the score is calculated' }));

    await user.click(await screen.findByText('AdventureWorksDW'));
    expect(await screen.findByText('143.2 min')).toBeInTheDocument();
    // each row: time shift when it has one (q1: 75%, not its 0 contains), else contains (q2)
    expect(screen.getByTitle('Time shift score')).toHaveTextContent('75%');
    expect(
      screen.getByTitle('Contains accuracy (no time shift for this question)'),
    ).toHaveTextContent('100%');

    await user.click(await screen.findByRole('button', { name: /How many orders/ }));

    expect(await screen.findByText('Question Result')).toBeInTheDocument();
    expect(screen.getByText('contains accuracy')).toBeInTheDocument();
    expect(screen.getByText('generated sql execution failed')).toBeInTheDocument();
    expect(screen.getByText('N/A')).toBeInTheDocument(); // the popup's time shift
    expect(screen.getByText('order_total')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('Schema does not exist')).toBeInTheDocument();
    expect(screen.getByText(/= its time shift score/)).toBeInTheDocument();
    expect(screen.getByText('LLM Judge')).toBeInTheDocument();
    expect(screen.getByText(/Not used: the generated SQL didn't run/)).toBeInTheDocument();
    expect(screen.getByText('1.5 min')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open in Langfuse/ })).toHaveAttribute(
      'href',
      'https://langfuse.test/project/p/traces/t1',
    );

    // the question, each SQL and the result each copy on their own
    await user.click(screen.getByRole('button', { name: 'Copy question' }));
    expect(await navigator.clipboard.readText()).toBe('How many orders?');
    await user.click(screen.getByRole('button', { name: 'Copy Golden SQL' }));
    expect(await navigator.clipboard.readText()).toBe('SELECT 1');
    await user.click(screen.getByRole('button', { name: 'Copy Generated SQL' }));
    expect(await navigator.clipboard.readText()).toBe('SELECT bad');
    await user.click(screen.getByRole('button', { name: 'Copy Golden result' }));
    expect(await navigator.clipboard.readText()).toBe('order_total\n42');
    // the failed generated run has no table to copy
    expect(screen.queryByRole('button', { name: 'Copy Generated result' })).not.toBeInTheDocument();
  });
});

describe('LLM judge in the question popup', () => {
  test('shows an overturned verdict with its reasoning', async () => {
    server.use(
      http.get(`${API}/evaluations/runs/r1/questions/q9`, () =>
        HttpResponse.json({
          run_id: 'r1',
          question_id: 'q9',
          question: 'Passengers per airport?',
          expected_sql: 'SELECT 1',
          score: 1,
          status: 'pass',
          failure_type: null,
          final_score: 1,
          score_source: 'contains_accuracy',
          trace_unavailable_reason: null,
          trace: {
            trace_id: 't9',
            langfuse_url: null,
            succeeded: true,
            error: null,
            generated_sql: 'SELECT 2',
            executed_generated_sql: null,
            expected_sql: 'SELECT 1',
            agent_status: 'success',
            expected_run: null,
            generated_run: null,
            scores: [
              {
                name: 'time_shift',
                value: 1,
                passed: true,
                applicable: true,
                error: null,
                details: {
                  shift_results: [
                    { offset_days: -1, status: 'passed' },
                    {
                      offset_days: -7,
                      status: 'passed',
                      llm_judge: {
                        overturned: true,
                        is_false_negative: true,
                        confidence: 90,
                        reasoning: 'Rounding only.',
                      },
                    },
                    {
                      offset_days: -30,
                      status: 'failed',
                      llm_judge: {
                        overturned: false,
                        is_false_negative: false,
                        confidence: 95,
                        reasoning: 'A year is missing.',
                      },
                    },
                  ],
                },
              },
              {
                name: 'llm_judge',
                value: 1,
                passed: true,
                applicable: true,
                error: null,
                details: {
                  is_false_negative: true,
                  overturned: true,
                  confidence: 92,
                  min_confidence: 80,
                  reasoning: 'NULL and 0 both mean no passengers.',
                  key_differences: [
                    {
                      difference: 'NULL instead of 0',
                      prevents_correct_answer: false,
                      explanation: 'Same meaning',
                    },
                  ],
                  judged_failure_reason: 'row_count_mismatch',
                  model: 'gpt-4.1-mini',
                  prompt_name: 'judge',
                  prompt_version: 1,
                },
              },
            ],
            metadata: {},
          },
        }),
      ),
    );
    renderWithProviders(<QuestionResultModal runId="r1" questionId="q9" onClose={() => {}} />);

    expect(
      await screen.findByText('Overturned: judged correct, so the question passes'),
    ).toBeInTheDocument();
    expect(screen.getByText('confidence 92% (needs 80%)')).toBeInTheDocument();
    expect(screen.getByText('NULL and 0 both mean no passengers.')).toBeInTheDocument();
    expect(screen.getByText('NULL instead of 0')).toBeInTheDocument();
    expect(screen.getByText(/model gpt-4.1-mini · prompt judge v1/)).toBeInTheDocument();
    // each failed time shift has its own verdict
    expect(screen.getByText('Time shifts: judged 2 failed shifts, passed 1')).toBeInTheDocument();
    expect(screen.getByText('Rounding only.')).toBeInTheDocument();
    expect(screen.getByText('A year is missing.')).toBeInTheDocument();
    expect(screen.getByText(/overturned, judged correct/)).toBeInTheDocument();
    expect(screen.getByText(/upheld, wrong/)).toBeInTheDocument();
  });
});
