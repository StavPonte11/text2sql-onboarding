import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render } from '@testing-library/react';
import { App as AntApp, ConfigProvider } from 'antd';
import { afterAll, afterEach, beforeAll } from 'vitest';

import { server } from './mocks/server';

import '@testing-library/jest-dom';

// Ant Design (and some other component libraries) rely on window.matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {}, // Deprecated
    removeListener: () => {}, // Deprecated
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// Setup MSW Server lifecycle
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  cleanup(); // Clean up standard React Testing Library DOM trees
});
afterAll(() => server.close());

export function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <ConfigProvider>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>{ui}</MemoryRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
}
