import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';

import { API_BASE_URL as API } from '../config/constants';
import ConnectionsPage from '../pages/ConnectionsPage';
import { server } from './mocks/server';
import { renderWithProviders } from './setup';

const question = (id: string, text: string, tags: string[]) => ({
  id,
  question: text,
  expected_sql: 'SELECT 1',
  difficulty: 'simple',
  question_type: 'simple',
  connection_id: 6,
  tags,
});

describe('connection page question tags', () => {
  test('shows each tag and launches only the selected tag', async () => {
    let launched = '';
    server.use(
      http.get('/api/agent/connections', () =>
        HttpResponse.json({ connections: [{ connection_id: 6, name: 'AdventureWorksDW' }] }),
      ),
      http.get('/api/agent/connections/6/tables', () => HttpResponse.json([])),
      http.get(`${API}/connections/6/questions`, () =>
        HttpResponse.json([
          question('q1', 'How many orders?', ['validation']),
          question('q2', 'Top customers?', ['test']),
          question('q3', 'Total revenue?', ['test', 'validation']),
        ]),
      ),
      http.get(`${API}/evaluations/runs`, () => HttpResponse.json([])),
      http.post(`${API}/evaluations/run-connection`, ({ request }) => {
        launched = new URL(request.url).search;
        return HttpResponse.json({ id: 'run-1' });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ConnectionsPage />);

    await user.click(await screen.findByRole('button', { name: /Golden Questions/ }));
    expect(await screen.findByText('How many orders?')).toBeInTheDocument();
    expect(screen.getAllByText('validation')).toHaveLength(2);
    expect(screen.getAllByText('test')).toHaveLength(2);

    await user.click(screen.getByRole('button', { name: /^Evaluation/ }));
    expect(await screen.findByText('Launch (3 Qs)')).toBeInTheDocument();
    expect(screen.getAllByText('validation')).toHaveLength(2);

    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Question tags to evaluate' }));
    await user.click(await screen.findByText('validation (2)'));

    expect(await screen.findByText('Launch (2 Qs)')).toBeInTheDocument();
    expect(screen.queryByText('Top customers?')).not.toBeInTheDocument();

    await user.click(screen.getByText('Launch (2 Qs)'));
    await waitFor(() => expect(launched).toContain('tags=validation'));
    expect(launched).toContain('connection_id=6');
  });
});
