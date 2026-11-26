import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';

import {
  DEFAULT_TIME_SHIFT,
  isDefaultTimeShift,
  timeShiftLabel,
  TimeShiftSelect,
} from '../components/common/TimeShiftSelect';
import { QuestionResultModal } from '../components/monitoring/QuestionResultModal';
import {
  RateLimitedQuestions,
  RerunQuestionButton,
  RerunRateLimitedButton,
} from '../components/monitoring/RateLimitPanel';
import { API_BASE_URL as API } from '../config/constants';
import { useQuestionTagSelection, useTriggerConnectionRun } from '../hooks/useEvaluations';
import { server } from './mocks/server';
import { renderWithProviders } from './setup';

describe('time shift', () => {
  test('labels days back and event dates', () => {
    expect(timeShiftLabel('-1')).toBe('1 day back');
    expect(timeShiftLabel('-30')).toBe('30 days back');
    expect(timeShiftLabel('2023-10-07')).toBe('07.10.2023 · 7 באוקטובר — תחילת חרבות ברזל');
    expect(timeShiftLabel('2022-05-01')).toBe('01.05.2022');
  });

  test('the defaults in any order are the defaults', () => {
    expect(isDefaultTimeShift([...DEFAULT_TIME_SHIFT].reverse())).toBe(true);
    expect(isDefaultTimeShift(['-1', '-7'])).toBe(false);
    expect(isDefaultTimeShift([...DEFAULT_TIME_SHIFT, '2023-10-07'])).toBe(false);
  });

  test('starts with the five defaults picked, and offers to go back to them', async () => {
    const { rerender } = renderWithProviders(
      <TimeShiftSelect value={DEFAULT_TIME_SHIFT} onChange={() => {}} />,
    );
    for (const days of [1, 7, 14, 30, 60]) {
      expect(screen.getAllByText(days === 1 ? '1 day back' : `${days} days back`).length).toBe(1);
    }
    expect(screen.queryByRole('button', { name: 'Defaults' })).not.toBeInTheDocument();

    rerender(<TimeShiftSelect value={['-7', '2023-10-07']} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Defaults' })).toBeInTheDocument();
  });
});

describe('LLM rate limits', () => {
  const waiting = [
    {
      question_id: 'q-1',
      question: 'How many flights left France?',
      stage: 'agent',
      retry: 2,
      max_retries: 3,
      retry_at: new Date(Date.now() + 40_000).toISOString(),
      error: 'HTTP 429',
    },
  ];

  test('shows the waiting question and stops its retries', async () => {
    let stopped = '';
    server.use(
      http.post(`${API}/evaluations/runs/:runId/questions/:qid/stop-retry`, ({ params }) => {
        stopped = `${params.runId}/${params.qid}`;
        return HttpResponse.json({ stopped: true });
      }),
    );
    renderWithProviders(<RateLimitedQuestions runId="run-1" waiting={waiting} />);

    expect(screen.getByText(/retrying 1 question/)).toBeInTheDocument();
    expect(screen.getByText('How many flights left France?')).toBeInTheDocument();
    expect(
      screen.getByText(/the agent writing the SQL got HTTP 429 · retry 2 of 3 in \d+s/),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Stop retrying/ }));
    await waitFor(() => expect(stopped).toBe('run-1/q-1'));
  });

  test('nothing waiting: nothing shown', () => {
    renderWithProviders(<RateLimitedQuestions runId="run-1" waiting={[]} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  test('reruns all rate-limited questions, or one', async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post(`${API}/evaluations/runs/:runId/rerun`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ id: 'run-1', status: 'running' }, { status: 202 });
      }),
    );
    renderWithProviders(
      <>
        <RerunRateLimitedButton runId="run-1" count={3} />
        <RerunQuestionButton runId="run-1" questionId="q-2" />
      </>,
    );

    await userEvent.click(screen.getByRole('button', { name: 'Rerun rate-limited (3)' }));
    await userEvent.click(screen.getByRole('button', { name: 'Rerun' }));
    await waitFor(() => expect(bodies).toEqual([{}, { question_ids: ['q-2'] }]));
  });

  test('no rate-limited questions: no rerun-all button', () => {
    renderWithProviders(<RerunRateLimitedButton runId="run-1" count={0} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('question result popup', () => {
  const question = (id: string) =>
    HttpResponse.json({
      question_id: id,
      question: `question ${id}`,
      status: 'fail',
      failure_type: null,
      scores: {},
      trace: null,
    });

  test('steps to the previous and next question with the buttons and arrow keys', async () => {
    server.use(
      http.get(`${API}/evaluations/runs/:runId/questions/:qid`, ({ params }) =>
        question(String(params.qid)),
      ),
    );
    const moves: string[] = [];
    renderWithProviders(
      <QuestionResultModal
        runId="r1"
        questionId="q2"
        onClose={() => {}}
        position={{ index: 2, total: 3 }}
        onPrev={() => moves.push('prev')}
        onNext={() => moves.push('next')}
      />,
    );

    expect(screen.getByText('2 / 3')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Previous question' }));
    await userEvent.click(screen.getByRole('button', { name: 'Next question' }));
    await userEvent.keyboard('{ArrowRight}{ArrowLeft}');
    expect(moves).toEqual(['prev', 'next', 'next', 'prev']);
  });

  test('no previous at the first question, and no buttons without a list', () => {
    const { unmount } = renderWithProviders(
      <QuestionResultModal
        runId="r1"
        questionId="q1"
        onClose={() => {}}
        position={{ index: 1, total: 3 }}
        onNext={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: 'Previous question' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Next question' })).toBeEnabled();
    unmount();

    renderWithProviders(<QuestionResultModal runId="r1" questionId="q1" onClose={() => {}} />);
    expect(screen.queryByRole('button', { name: 'Next question' })).not.toBeInTheDocument();
  });
});

describe('choosing the questions to run', () => {
  const questions = [
    { id: 'a', question: 'A?', tags: ['x'] },
    { id: 'b', question: 'B?', tags: ['y'] },
    { id: 'c', question: 'C?', tags: ['x'] },
  ];

  function Picker({ onLaunch }: { onLaunch: (ids: string[] | undefined) => void }) {
    const { runQuestions, isChosen, toggleQuestion, chooseAll, chosenIds, setSelectedTags } =
      useQuestionTagSelection(questions);
    return (
      <>
        {questions.map((q) => (
          <label key={q.id}>
            <input type="checkbox" checked={isChosen(q.id)} onChange={() => toggleQuestion(q.id)} />
            {q.question}
          </label>
        ))}
        <span>{runQuestions.length} chosen</span>
        <button onClick={() => chooseAll(true)}>Select all</button>
        <button onClick={() => chooseAll(false)}>Clear</button>
        <button onClick={() => setSelectedTags(['x'])}>tag x</button>
        <button onClick={() => onLaunch(chosenIds)}>launch</button>
      </>
    );
  }

  test('all checked to start: no ids sent; unchecking sends only the checked ones', async () => {
    const launches: (string[] | undefined)[] = [];
    renderWithProviders(<Picker onLaunch={(ids) => launches.push(ids)} />);

    expect(screen.getByText('3 chosen')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'launch' }));
    await userEvent.click(screen.getByLabelText('B?'));
    expect(screen.getByText('2 chosen')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'launch' }));
    expect(launches).toEqual([undefined, ['a', 'c']]);
  });

  test('clear and select all; tags narrow the list first', async () => {
    const launches: (string[] | undefined)[] = [];
    renderWithProviders(<Picker onLaunch={(ids) => launches.push(ids)} />);

    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.getByText('0 chosen')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Select all' }));
    expect(screen.getByText('3 chosen')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'tag x' }));
    await userEvent.click(screen.getByLabelText('C?'));
    await userEvent.click(screen.getByRole('button', { name: 'launch' }));
    expect(launches).toEqual([['a']]);
  });

  test('the run request carries the chosen question ids', async () => {
    let url = '';
    server.use(
      http.post(`${API}/evaluations/run-connection`, ({ request }) => {
        url = request.url;
        return HttpResponse.json({ id: 'run-9' }, { status: 202 });
      }),
    );
    function Launcher() {
      const trigger = useTriggerConnectionRun();
      return (
        <button onClick={() => trigger.mutate({ connectionId: 6, questionIds: ['a', 'c'] })}>
          go
        </button>
      );
    }
    renderWithProviders(<Launcher />);
    await userEvent.click(screen.getByRole('button', { name: 'go' }));
    await waitFor(() => expect(url).toContain('question_ids=a&question_ids=c'));
  });
});
