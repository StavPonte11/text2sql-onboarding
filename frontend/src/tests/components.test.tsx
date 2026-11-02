import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { highlightJson } from '../components/TraceTimeline';
import { EvaluationsPage } from '../pages/EvaluationsPage';
import { renderWithProviders } from './setup';

// Mock useNavigate from react-router-dom
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = (await importOriginal()) as any;
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

describe('EvaluationsPage Integration Tests', () => {
  test('renders evaluations page tabs', async () => {
    renderWithProviders(<EvaluationsPage />);

    // Check header title exists
    expect(screen.getByText('Evaluations')).toBeInTheDocument();

    // Verify tabs are available
    expect(screen.getAllByText('Execution History')[0]).toBeInTheDocument();
    expect(screen.getByText('Scheduled Runs')).toBeInTheDocument();
    expect(screen.getByText('Run Controls')).toBeInTheDocument();
  });

  test('can switch to run controls tab and view evaluation readiness panel', async () => {
    const user = userEvent.setup();
    renderWithProviders(<EvaluationsPage />);

    // Click on Run Controls tab
    const runControlsTab = screen.getByText('Run Controls');
    await user.click(runControlsTab);

    // Verify Section Header is displayed
    expect(screen.getByText('Trigger Evaluation Run')).toBeInTheDocument();

    // Verify mock tables render inside readiness lists
    const ordersTable = await screen.findByText('orders');
    expect(ordersTable).toBeInTheDocument();

    // Incomplete tables check
    const usersTable = await screen.findByText('users');
    expect(usersTable).toBeInTheDocument();
    expect(await screen.findByText('⚠ Incomplete')).toBeInTheDocument();
  });
});

describe('JSON Syntax Highlight Tests', () => {
  test('correctly highlights keys, strings, booleans, and nulls in JSON text', () => {
    const rawJson = JSON.stringify(
      {
        key_str: 'hello',
        key_num: 42.5,
        key_bool: true,
        key_null: null,
      },
      null,
      2,
    );

    const highlighted = highlightJson(rawJson);

    expect(highlighted).toContain('<span class="json-key">&quot;key_str&quot;</span>:');
    expect(highlighted).toContain('<span class="json-key">&quot;key_num&quot;</span>:');
    expect(highlighted).toContain('<span class="json-key">&quot;key_bool&quot;</span>:');
    expect(highlighted).toContain('<span class="json-key">&quot;key_null&quot;</span>:');

    expect(highlighted).toContain('<span class="json-string">&quot;hello&quot;</span>');
    expect(highlighted).toContain('<span class="json-number">42.5</span>');
    expect(highlighted).toContain('<span class="json-boolean">true</span>');
    expect(highlighted).toContain('<span class="json-null">null</span>');
  });

  test('escapes HTML entities safely', () => {
    const rawJson = JSON.stringify(
      {
        malicious: "<script>alert('xss')</script>",
      },
      null,
      2,
    );

    const highlighted = highlightJson(rawJson);

    expect(highlighted).not.toContain('<script>');
    expect(highlighted).toContain('&lt;script&gt;alert(&#39;xss&#39;)&lt;&#x2F;script&gt;');
  });
});
