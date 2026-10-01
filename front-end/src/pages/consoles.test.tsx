import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { fixtures } from '@/test/handlers';
import { renderApp } from '@/test/render';
import { server } from '@/test/server';

describe('admin consoles', () => {
  it('lists Ollama models with capabilities and saves a changed per-role allowlist', async () => {
    const user = userEvent.setup();
    let saved: Record<string, string[]> | null = null;
    server.use(
      http.put('/api/admin/models/allowlist', async ({ request }) => {
        saved = ((await request.json()) as { allowlist: Record<string, string[]> }).allowlist;
        return HttpResponse.json({ ...fixtures.adminModels, allowlist: saved });
      }),
    );
    renderApp('/admin/models', { as: 'admin' });
    expect(await screen.findByText('Model registry')).toBeInTheDocument();
    for (const name of [/llama3\.2:3b/, /qwen3:4b-q4_K_M/, /gemma4:e2b/])
      expect((await screen.findAllByText(name)).length).toBeGreaterThan(0);
    // Embedding models cannot chat, so they are not offered in the allowlist.
    expect(screen.getByText(/Embedding models are not listed/)).toBeInTheDocument();

    const save = screen.getByRole('button', { name: 'Save allowlist' });
    expect(save).toBeDisabled();
    await user.click(screen.getByLabelText('gemma4:e2b for Line Cook'));
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.line_cook).not.toContain('gemma4:e2b');
    expect(saved!.sous_chef).toContain('gemma4:e2b');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save allowlist' })).toBeDisabled(),
    );
  });

  it('shows users with roles and stations', async () => {
    renderApp('/admin/users', { as: 'admin' });
    expect(await screen.findByText('Users & roles')).toBeInTheDocument();
    expect((await screen.findAllByText(/Elena Petrova/)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/Kitchen Manager/)).length).toBeGreaterThan(0);
  });

  it('shows separate Chroma collections for text and photo embeddings', async () => {
    renderApp('/admin/vector-store', { as: 'admin' });
    expect((await screen.findAllByText('documents__nomic-embed-text-v1.5')).length).toBeGreaterThan(
      0,
    );
    expect(screen.getAllByText('photos__nomic-embed-vision-v1.5').length).toBeGreaterThan(0);
  });
});

describe('MCP console', () => {
  it('lists exposed tools and runs one with the Try-it panel', async () => {
    const user = userEvent.setup();
    let input: unknown = null;
    server.use(
      http.post('/api/mcp/tools/:name/try', async ({ request, params }) => {
        input = { tool: params.name, ...((await request.json()) as object) };
        return HttpResponse.json({
          status: 'ok',
          requiresApproval: false,
          input: {},
          output: {
            results: [{ docId: 'DOC-SOP-003', title: 'Cooling Hot Foods (Two-Stage Cooling)' }],
          },
          latencyMs: 42,
        });
      }),
    );
    renderApp('/mcp', { as: 'sous' });
    expect((await screen.findAllByText('search_documents')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('escalate_incident').length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: /Call tool/ }));
    await waitFor(() =>
      expect(input).toMatchObject({ tool: 'search_documents', input: expect.any(Object) }),
    );
    expect(await screen.findByText(/42 ms/)).toBeInTheDocument();
  });

  it('lets an admin issue a service token and shows the secret once', async () => {
    const user = userEvent.setup();
    let body: unknown = null;
    server.use(
      http.post('/api/mcp/tokens', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(
          {
            id: 'TOK-9',
            name: 'pos-integration',
            client: 'POS sync',
            scopes: ['mcp:read'],
            prefix: 'lm_svc_ab12',
            createdAt: new Date().toISOString(),
            lastUsed: null,
            expiresAt: '2026-12-30T00:00:00Z',
            revoked: false,
            token: 'lm_svc_ab12.secret-value',
          },
          { status: 201 },
        );
      }),
    );
    renderApp('/mcp', { as: 'admin' });
    await user.click(await screen.findByRole('tab', { name: 'Service tokens' }));
    await user.click(await screen.findByRole('button', { name: /Issue token/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Token name'), 'pos-integration');
    await user.type(within(dialog).getByLabelText('Client'), 'POS sync');
    await user.click(within(dialog).getByRole('button', { name: /Issue token/ }));
    expect(await within(dialog).findByText('lm_svc_ab12.secret-value')).toBeInTheDocument();
    expect(body).toEqual({
      name: 'pos-integration',
      client: 'POS sync',
      scopes: ['mcp:read'],
      days: 90,
    });
  });

  it('tells non-admins that tokens are managed by admins', async () => {
    const user = userEvent.setup();
    renderApp('/mcp', { as: 'sous' });
    await user.click(await screen.findByRole('tab', { name: 'Service tokens' }));
    expect(await screen.findByText('Only Admins manage service tokens.')).toBeInTheDocument();
  });
});

describe('dashboards, audits and analytics', () => {
  it('shows the line cook dashboard with stale-citation alerts and assigned tickets', async () => {
    renderApp('/', { as: 'cook' });
    expect(await screen.findByText(/stale SOPs? (was|were) cited/)).toBeInTheDocument();
    expect(screen.getByText('Handle first')).toBeInTheDocument();
    expect(screen.getByText('Kitchen activity')).toBeInTheDocument();
  });

  it('shows the stale-document audit with review ages', async () => {
    renderApp('/audits/stale', { as: 'sous' });
    expect(await screen.findByText('Stale-document audit')).toBeInTheDocument();
    expect(screen.getAllByText('Grill Deep Clean & Degreasing').length).toBeGreaterThan(0);
  });

  it('shows the ownership audit with a suggested reassignment', async () => {
    renderApp('/audits/ownership', { as: 'manager' });
    expect(await screen.findByText('Ownership audit')).toBeInTheDocument();
    expect(screen.getAllByText(/Blast chiller not pulling down/).length).toBeGreaterThan(0);
  });

  it('shows workload analytics with charts per station', async () => {
    renderApp('/analytics', { as: 'manager' });
    expect(await screen.findByText('Workload analytics')).toBeInTheDocument();
    expect(await screen.findByText(/Station × priority heat map/)).toBeInTheDocument();
    expect(screen.getAllByTestId(/^chart-/).length).toBeGreaterThan(0);
  });

  it('lists agent runs with their final status', async () => {
    renderApp('/agent/runs', { as: 'admin' });
    expect(await screen.findByText('Agent runs')).toBeInTheDocument();
    expect((await screen.findAllByText(/RUN-0001/)).length).toBeGreaterThan(0);
  });

  it('loads the compiled LangGraph diagrams and offers the annotated overview', async () => {
    const user = userEvent.setup();
    let requested = 0;
    server.use(
      http.get('/api/agent/graphs', () => {
        requested += 1;
        return HttpResponse.json({
          ask: 'graph TD;\n\t__start__ --> classify_intent;\n\tclassify_intent --> retrieve;',
          triage: 'graph TD;\n\t__start__ --> load_tickets;',
        });
      }),
    );
    renderApp('/agent/graph', { as: 'sous' });
    expect(await screen.findByText('Agent graph')).toBeInTheDocument();
    expect(
      await screen.findByText(/Generated by LangGraph from the running back-end/),
    ).toBeInTheDocument();
    expect(requested).toBe(1);
    await user.click(screen.getByText('Overview'));
    expect(await screen.findByText(/Annotated overview of the same nodes/)).toBeInTheDocument();
    await user.click(screen.getByText('Triage graph'));
    expect(await screen.findByText(/triage_graph — shift triage agent/)).toBeInTheDocument();
  });
});
