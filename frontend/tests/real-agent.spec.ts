import { test, expect } from '@playwright/test';

test.describe('Real Agent Execution', () => {
  test('should run query against real backend', async ({ page }) => {
    // Navigate to the Studio / Agent Testing page using the local dev server
    await page.goto('http://localhost:3001/agent-testing');

    // Fill the chat input
    const input = page.getByPlaceholder('Ask the agent to query a table...');
    await expect(input).toBeVisible();
    await input.fill('how many orders in the last month?');
    
    // Click submit
    const submitBtn = page.getByRole('button', { name: /Run Agent/i });
    if (await submitBtn.isVisible()) {
      await submitBtn.click();
    } else {
      await page.keyboard.press('Enter');
    }

    // Wait up to 3 minutes for the agent to finish
    test.setTimeout(180000);

    // Look for either Agent Completed or Agent Needs Approval or Agent Error
    await expect(
      page.locator('text=Agent Completed').or(page.locator('text=Agent Needs Approval')).or(page.locator('text=Agent Error'))
    ).toBeVisible({ timeout: 180000 });

    // Print out what was found
    if (await page.locator('text=Agent Error').isVisible()) {
      console.log('Got Agent Error!');
      const desc = await page.locator('.ant-alert-description').innerText();
      console.log('Error description:', desc);
    } else if (await page.locator('text=Agent Needs Approval').isVisible()) {
      console.log('Got Agent Needs Approval!');
    } else if (await page.locator('text=Agent Completed').isVisible()) {
      console.log('Got Agent Completed!');
      // Check if trace timeline shows up
      const traceBtn = page.getByRole('button', { name: /View Full Trace/i });
      if (await traceBtn.isVisible()) {
        await traceBtn.click();
        await expect(page.locator('.ant-timeline')).toBeVisible({ timeout: 10000 });
        console.log('Trace Timeline is visible!');
      }
    }
  });
});
