import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';

import { AgentModelSelect } from '../components/common/AgentModelSelect';
import { API_BASE_URL as API } from '../config/constants';
import { useTriggerConnectionRun } from '../hooks/useEvaluations';
import { server } from './mocks/server';
import { renderWithProviders } from './setup';

function PreviousAgentLauncher() {
  const trigger = useTriggerConnectionRun();
  return (
    <button onClick={() => trigger.mutate({ connectionId: 6, agent: 'previous' })}>
      launch previous
    </button>
  );
}

function Launcher() {
  const trigger = useTriggerConnectionRun();
  return (
    <button onClick={() => trigger.mutate({ connectionId: 6, model: 'gpt-oss:20b' })}>
      launch
    </button>
  );
}

describe('LLM model choice', () => {
  test('lists the agent models with its default first', async () => {
    server.use(
      http.get(`${API}/evaluations/models`, () =>
        HttpResponse.json({ models: ['gemma4:e4b', 'gpt-oss:20b'], default: 'gemma4:e4b' }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AgentModelSelect value="" onChange={() => {}} />);
    expect(await screen.findByText('Default (gemma4:e4b)')).toBeInTheDocument();
    await user.click(screen.getByRole('combobox', { name: 'LLM model for the evaluation' }));
    expect((await screen.findAllByText('gpt-oss:20b')).length).toBeGreaterThan(0);
  });

  test('says why when the models cannot be listed', async () => {
    server.use(
      http.get(`${API}/evaluations/models`, () =>
        HttpResponse.json({ models: [], default: null, list_error: 'Agent is down' }),
      ),
    );
    renderWithProviders(<AgentModelSelect value="" onChange={() => {}} />);
    expect(await screen.findByText('Agent is down')).toBeInTheDocument();
    expect(screen.getByText('Default model')).toBeInTheDocument();
  });

  test('sends the chosen model with the run', async () => {
    let url: URL | undefined;
    server.use(
      http.post(`${API}/evaluations/run-connection`, ({ request }) => {
        url = new URL(request.url);
        return HttpResponse.json({ id: 'r1' });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<Launcher />);
    await user.click(screen.getByText('launch'));
    await waitFor(() => expect(url?.searchParams.get('model')).toBe('gpt-oss:20b'));
  });

  test('sends the previous agent with the run', async () => {
    let url: URL | undefined;
    server.use(
      http.post(`${API}/evaluations/run-connection`, ({ request }) => {
        url = new URL(request.url);
        return HttpResponse.json({ id: 'r1' });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<PreviousAgentLauncher />);
    await user.click(screen.getByText('launch previous'));
    await waitFor(() => expect(url?.searchParams.get('agent')).toBe('previous'));
    expect(url?.searchParams.get('model')).toBeNull();
  });

  test('the current agent is the default and is not sent', async () => {
    let url: URL | undefined;
    server.use(
      http.post(`${API}/evaluations/run-connection`, ({ request }) => {
        url = new URL(request.url);
        return HttpResponse.json({ id: 'r1' });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<Launcher />);
    await user.click(screen.getByText('launch'));
    await waitFor(() => expect(url).toBeDefined());
    expect(url?.searchParams.get('agent')).toBeNull();
  });

  test('choosing the previous agent hides the model choice', async () => {
    server.use(
      http.get(`${API}/evaluations/models`, () =>
        HttpResponse.json({ models: ['gpt-oss:20b'], default: 'gpt-oss:20b' }),
      ),
    );
    const { unmount } = renderWithProviders(
      <AgentModelSelect value="" onChange={() => {}} agent="current" onAgentChange={() => {}} />,
    );
    expect(screen.getByRole('combobox', { name: 'Agent for the evaluation' })).toBeInTheDocument();
    expect(
      screen.getByRole('combobox', { name: 'LLM model for the evaluation' }),
    ).toBeInTheDocument();
    unmount();
    renderWithProviders(
      <AgentModelSelect value="" onChange={() => {}} agent="previous" onAgentChange={() => {}} />,
    );
    expect(screen.queryByRole('combobox', { name: 'LLM model for the evaluation' })).toBeNull();
    expect(screen.getByText(/previous agent runs on its own model/)).toBeInTheDocument();
  });
});
