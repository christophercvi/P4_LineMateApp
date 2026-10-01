import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { useSession } from '@/auth/store';
import { renderApp } from '@/test/render';
import { server } from '@/test/server';

const navLabels = () =>
  Array.from(document.querySelectorAll('.ant-menu-title-content')).map((n) =>
    n.textContent?.trim(),
  );

describe('sign in', () => {
  it('redirects anonymous visitors to the sign-in page', async () => {
    const { router } = renderApp('/tickets');
    await screen.findByRole('button', { name: 'Sign in' });
    expect(router.state.location.pathname).toBe('/login');
  });

  it('signs a user in and lands on their dashboard', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/login');
    await user.type(await screen.findByLabelText('Username'), 'marco');
    await user.type(screen.getByLabelText('Password'), 'Hearthline#2026');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    await screen.findByText(/Good (morning|afternoon|evening), Marco/);
    expect(router.state.location.pathname).toBe('/');
    expect(useSession.getState().claims?.role).toBe('line_cook');
  });

  it('shows the API error for wrong credentials', async () => {
    const user = userEvent.setup();
    renderApp('/login');
    await user.type(await screen.findByLabelText('Username'), 'marco');
    await user.type(screen.getByLabelText('Password'), 'wrong-password');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Incorrect username or password')).toBeInTheDocument();
    expect(useSession.getState().token).toBeNull();
  });
});

describe('create account', () => {
  it('validates the form, registers a Line Cook and signs them in', async () => {
    const user = userEvent.setup();
    let sent: Record<string, unknown> | null = null;
    server.use(
      http.post('/api/auth/register', async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        const { makeToken } = await import('@/test/users');
        const claims = {
          sub: 'u-rosa',
          username: 'rosa',
          name: 'Rosa Delgado',
          role: 'line_cook' as const,
          station: 'pastry' as const,
          crew: null,
        };
        return HttpResponse.json(
          { token: makeToken(claims), tokenType: 'bearer', expiresIn: 28800, user: claims },
          { status: 201 },
        );
      }),
    );
    const { router } = renderApp('/login');
    await user.click(await screen.findByRole('link', { name: /Create an account/ }));
    expect(router.state.location.pathname).toBe('/register');

    await user.click(screen.getByRole('button', { name: /Create account/ }));
    expect(await screen.findByText('Pick your station')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Full name'), 'Rosa Delgado');
    await user.type(screen.getByLabelText('Username'), 'rosa');
    await user.click(screen.getByLabelText('Station'));
    await user.click(await screen.findByTitle('Pastry'));
    await user.type(screen.getByLabelText('Work email'), 'rosa@hearthline-kitchen.com');
    await user.type(screen.getByLabelText('Password', { selector: 'input' }), 'Pastry-Shift-2026');
    await user.type(screen.getByLabelText('Confirm password'), 'Pastry-Shift-2027');
    await user.click(screen.getByRole('button', { name: /Create account/ }));
    expect(await screen.findByText(/do not match/i)).toBeInTheDocument();

    await user.clear(screen.getByLabelText('Confirm password'));
    await user.type(screen.getByLabelText('Confirm password'), 'Pastry-Shift-2026');
    await user.click(screen.getByRole('button', { name: /Create account/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(sent).toMatchObject({
      displayName: 'Rosa Delgado',
      username: 'rosa',
      station: 'pastry',
      email: 'rosa@hearthline-kitchen.com',
      password: 'Pastry-Shift-2026',
    });
    expect(useSession.getState().claims?.name).toBe('Rosa Delgado');
  });

  it('surfaces a taken username from the API', async () => {
    const user = userEvent.setup();
    renderApp('/register');
    await user.type(await screen.findByLabelText('Full name'), 'Marco Two');
    await user.type(screen.getByLabelText('Username'), 'marco');
    await user.click(screen.getByLabelText('Station'));
    await user.click(await screen.findByTitle('Grill'));
    await user.type(screen.getByLabelText('Work email'), 'marco2@hearthline-kitchen.com');
    await user.type(screen.getByLabelText('Password', { selector: 'input' }), 'Grill-Shift-2026');
    await user.type(screen.getByLabelText('Confirm password'), 'Grill-Shift-2026');
    await user.click(screen.getByRole('button', { name: /Create account/ }));
    expect(await screen.findByText('That username is taken')).toBeInTheDocument();
  });
});

describe('role-aware navigation and guards', () => {
  it('shows a line cook only the kitchen screens', async () => {
    renderApp('/', { as: 'cook' });
    await screen.findByText(/Good (morning|afternoon|evening), Marco/);
    expect(navLabels()).toEqual(['Dashboard', 'Ask LineMate', 'Tickets', 'Documents']);
  });

  it('gives the kitchen manager triage, approvals, audits and analytics, and admins the admin console', async () => {
    renderApp('/', { as: 'manager' });
    await screen.findByText(/Elena/);
    const labels = navLabels();
    for (const l of ['Shift triage', 'Approvals', 'Audits', 'Workload analytics', 'MCP console'])
      expect(labels).toContain(l);
    expect(labels).not.toContain('Admin');
  });

  it('blocks a screen the role cannot use with a clear reason', async () => {
    renderApp('/approvals', { as: 'cook' });
    expect(await screen.findByText('403 — not available for your role')).toBeInTheDocument();
    expect(screen.getByText(/You are signed in as Line Cook/)).toBeInTheDocument();
  });

  it('keeps the service account out of chat', async () => {
    renderApp('/ask', { as: 'service' });
    expect(await screen.findByText(/Service accounts use MCP tools, not chat/)).toBeInTheDocument();
  });

  it('renders the not-found page for unknown routes', async () => {
    renderApp('/walk-in-freezer', { as: 'sous' });
    expect(await screen.findByText(/page not found/)).toBeInTheDocument();
  });

  it('signs out from the user menu', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/', { as: 'sous' });
    const header = await screen.findByRole('banner');
    await user.click(within(header).getByText('Priya Nair'));
    await user.click(await screen.findByText(/Sign out/));
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'));
    expect(useSession.getState().token).toBeNull();
  });

  it('toggles dark mode from the header', async () => {
    const user = userEvent.setup();
    renderApp('/', { as: 'cook' });
    await screen.findByText(/Good (morning|afternoon|evening), Marco/);
    await user.click(screen.getByRole('button', { name: /dark mode|theme/i }));
    const { useUi } = await import('@/auth/store');
    expect(useUi.getState().mode).toBe('dark');
  });
});
