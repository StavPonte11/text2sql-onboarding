import { useState } from 'react';
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

function NamedLauncher() {
  const trigger = useTriggerConnectionRun();
  const [name, setName] = useState('');
  return (
    <>
      <AgentModelSelect value="" onChange={() => {}} runName={name} onRunNameChange={setName} />
      <button onClick={() => trigger.mutate({ connectionId: 6, name })}>launch named</button>
    </>
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

  test('names the default model and lists the others', async () => {
    server.use(
      http.get(`${API}/evaluations/models`, () =>
        HttpResponse.json({ models: ['gemma4:e4b', 'gpt-oss:20b'], default: 'gemma4:e4b' }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AgentModelSelect value="" onChange={() => {}} />);
    expect(await screen.findByText('gemma4:e4b')).toBeInTheDocument();
    expect(screen.queryByText('default')).toBeNull(); // the box shows the name alone
    await user.click(screen.getByRole('combobox', { name: 'LLM model for the evaluation' }));
    expect((await screen.findAllByText('gpt-oss:20b')).length).toBeGreaterThan(0);
    expect(await screen.findByText('default')).toBeInTheDocument(); // marked in the list
  });

  test("names the agent's model and warns when it can't list models", async () => {
    server.use(
      http.get(`${API}/evaluations/models`, () =>
        HttpResponse.json({
          models: ['openai/gpt-oss-120b'],
          default: 'openai/gpt-oss-120b',
          list_error: "This agent version can't switch models; it runs on openai/gpt-oss-120b.",
        }),
      ),
    );
    const user = userEvent.setup();
    const { container } = renderWithProviders(<AgentModelSelect value="" onChange={() => {}} />);
    expect(await screen.findByText('openai/gpt-oss-120b')).toBeInTheDocument();
    expect(container.querySelector('.ant-select-status-warning')).not.toBeNull();
    await user.hover(screen.getByText('openai/gpt-oss-120b'));
    expect(await screen.findByText(/can't switch models/)).toBeInTheDocument();
  });

  test('choosing the default model sends no model', async () => {
    server.use(
      http.get(`${API}/evaluations/models`, () =>
        HttpResponse.json({ models: ['gemma4:e4b', 'gpt-oss:20b'], default: 'gemma4:e4b' }),
      ),
    );
    const chosen: string[] = [];
    const user = userEvent.setup();
    renderWithProviders(<AgentModelSelect value="gpt-oss:20b" onChange={(m) => chosen.push(m)} />);
    await user.click(screen.getByRole('combobox', { name: 'LLM model for the evaluation' }));
    const options = await screen.findAllByText('gemma4:e4b');
    await user.click(options[options.length - 1]);
    expect(chosen).toEqual(['']);
  });

  test('the previous agent has no model choice', async () => {
    const picked: string[] = [];
    const user = userEvent.setup();
    const { unmount } = renderWithProviders(
      <AgentModelSelect
        value=""
        onChange={() => {}}
        agent="current"
        onAgentChange={(a) => picked.push(a)}
      />,
    );
    await user.click(screen.getByText('Previous (TextToSQL)'));
    expect(picked).toEqual(['previous']);
    unmount();
    renderWithProviders(
      <AgentModelSelect value="" onChange={() => {}} agent="previous" onAgentChange={() => {}} />,
    );
    expect(screen.getByRole('combobox', { name: 'LLM model for the evaluation' })).toBeDisabled();
    expect(screen.getByText(/previous agent runs on its own model/)).toBeInTheDocument();
  });

  test('sends the run name with the run', async () => {
    let url: URL | undefined;
    server.use(
      http.post(`${API}/evaluations/run-connection`, ({ request }) => {
        url = new URL(request.url);
        return HttpResponse.json({ id: 'r1' });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<NamedLauncher />);
    await user.type(screen.getByRole('textbox', { name: 'Run name' }), '  Baseline ');
    await user.click(screen.getByText('launch named'));
    await waitFor(() => expect(url?.searchParams.get('name')).toBe('Baseline'));
  });
});
