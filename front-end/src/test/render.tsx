import type { ReactElement } from 'react';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom';
import { routes } from '@/router';
import { ThemeProvider } from '@/theme/ThemeProvider';
import { signIn, type Who } from './users';

export const testQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  });

/** Mount the whole application (layout, guards, lazy pages) at `path`, optionally signed in. */
export function renderApp(path: string, { as }: { as?: Who } = {}) {
  if (as) signIn(as);
  const client = testQueryClient();
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const view = render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <RouterProvider router={router} />
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { ...view, router, client };
}

/** Mount a single component with providers (no route table). */
export function renderWithProviders(
  ui: ReactElement,
  { as, path = '/' }: { as?: Who; path?: string } = {},
) {
  if (as) signIn(as);
  const client = testQueryClient();
  const view = render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { ...view, client };
}
