import { screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, describe, vi } from 'vitest';
import { TableList } from '../components/tables/TableList';
import { EvaluationsPage } from '../pages/EvaluationsPage';
import { renderWithProviders } from './setup';

// Mock useNavigate from react-router-dom
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal() as any;
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

describe('TableList Component Integration Tests', () => {
  test('renders table list header and loads tables successfully', async () => {
    renderWithProviders(<TableList />);

    // Check table title exists
    expect(screen.getByText('tables.title')).toBeInTheDocument();

    // Verify mock tables are fetched and displayed in the document
    const ordersTable = await screen.findByText('orders');
    expect(ordersTable).toBeInTheDocument();

    const usersTable = await screen.findByText('users');
    expect(usersTable).toBeInTheDocument();

    // Verify table columns render properly
    expect(screen.getByText('tables.cols.name')).toBeInTheDocument();
    expect(screen.getByText('tables.cols.schema')).toBeInTheDocument();
    expect(screen.getByText('tables.cols.status')).toBeInTheDocument();
  });

  test('allows searching and filtering tables by status', async () => {
    renderWithProviders(<TableList />);

    // Search filter input interaction
    const searchInput = screen.getByPlaceholderText('tables.searchPlaceholder');
    fireEvent.change(searchInput, { target: { value: 'orders' } });
    expect(searchInput).toHaveValue('orders');

    // Status filter select dropdown interaction
    const selectDropdown = screen.getByRole('combobox');
    fireEvent.change(selectDropdown, { target: { value: 'sandbox' } });
    expect(selectDropdown).toHaveValue('sandbox');
  });

  test('can open add table modal and successfully create a new table', async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableList />);

    // Click on Add Table button
    const addButton = screen.getByText('tables.add');
    await user.click(addButton);

    // Verify modal overlay opens
    expect(screen.getByText('Create New Table')).toBeInTheDocument();
    expect(screen.getByText('Oasis Source ID')).toBeInTheDocument();

    // Type Oasis Source ID
    const input = screen.getByPlaceholderText('e.g. some-uuid-or-fqn');
    await user.type(input, 'test-new-oasis-id');

    // Click Create Table
    const submitButton = screen.getByText('Create Table');
    await user.click(submitButton);

    // Verify modal closes
    await waitFor(() => {
      expect(screen.queryByText('Create New Table')).not.toBeInTheDocument();
    });
  });
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
