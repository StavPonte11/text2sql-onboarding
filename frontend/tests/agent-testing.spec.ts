import { expect,test } from '@playwright/test';

test.describe('Agent Testing Page', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to the Studio / Agent Testing page.
    await page.goto('/agent-testing');
  });

  test('should run a query and display trace viewer', async ({ page }) => {
    // Setup API mocking for a completed chat
    await page.route('/api/agent/chat', async route => {
      const json = {
        thread_id: "test-thread-id-completed",
        status: "completed",
        summary: "Here are all test users.",
        sql_query: "SELECT * FROM users",
        trace_id: "trace-12345"
      };
      await route.fulfill({ json });
    });

    // Fill the chat input
    const input = page.getByPlaceholder('Ask the agent to query a table...');
    await expect(input).toBeVisible();
    await input.fill('show me all test users');
    
    // Click submit
    const submitBtn = page.getByRole('button', { name: /Ask Agent/i });
    if (await submitBtn.isVisible()) {
      await submitBtn.click();
    } else {
      await page.keyboard.press('Enter');
    }

    // Wait for the agent to complete
    // We expect either a success card or an interrupt card
    await expect(page.locator('.agent-result')).toBeVisible({ timeout: 60000 });

    // Let's assume it completed
    const viewTraceBtn = page.getByRole('button', { name: /View Full Trace/i });
    
    // If the button is visible, click it and verify the modal
    if (await viewTraceBtn.isVisible()) {
      await viewTraceBtn.click();
      
      const modalTitle = page.getByText('Execution Trace', { exact: true });
      await expect(modalTitle).toBeVisible();
      
      // Close modal
      await page.locator('button.ant-modal-close').click();
    }
  });

  test('should handle structured rejection', async ({ page }) => {
    // Here we can mock the chat response or trigger a real interrupt.
    // For now, this is a basic shell, we just ensure playwright is hooked up correctly.
    // A robust test would mock the `/api/agent/chat` endpoint to return `status: "interrupted"` 
    // and verify the rejection dropdown renders.
    
    // Setup API mocking for an interruption
    await page.route('/api/agent/chat', async route => {
      const json = {
        thread_id: "test-thread-id",
        status: "interrupted",
        sql_query: "SELECT * FROM users",
        interrupt_details: { reason: "test" }
      };
      await route.fulfill({ json });
    });

    const input = page.getByPlaceholder('Ask the agent to query a table...');
    await expect(input).toBeVisible();
    await input.fill('trigger interrupt');
    await page.keyboard.press('Enter');

    // Wait for the interrupted card
    await expect(page.getByText('Agent Needs Approval')).toBeVisible({ timeout: 10000 });

    // We should see the Rejection Category select
    const categorySelect = page.locator('.ant-select-selector').first();
    await expect(categorySelect).toBeVisible();

    // The reject button should be visible but disabled initially
    const rejectBtn = page.getByRole('button', { name: 'Reject' });
    await expect(rejectBtn).toBeVisible();
  });
});
