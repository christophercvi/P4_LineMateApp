import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import triageStream from '@/test/fixtures/triage_stream.json';
import { fixtures, sseResponse } from '@/test/handlers';
import { renderApp } from '@/test/render';
import { server } from '@/test/server';

describe('shift triage', () => {
  it('streams graph nodes, the summary, risks and approval interrupts from a recorded run', async () => {
    const user = userEvent.setup();
    let body: unknown = null;
    server.use(
      http.post('/api/agent/triage/stream', async ({ request }) => {
        body = await request.json();
        return sseResponse(triageStream as [string, unknown][]);
      }),
    );
    renderApp('/triage', { as: 'sous' });
    await user.click(await screen.findByRole('button', { name: /Run (shift )?triage/ }));

    expect(
      await screen.findByRole('button', { name: /Run again/ }, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(body).toMatchObject({ model: expect.any(String) });
    // Every node of the LangGraph run is listed, including the risk check and the planner.
    for (const title of [
      'Load open tickets',
      'Score priority',
      'Check supplier stock',
      'Detect risks',
      'Propose actions',
      'Write the summary',
    ]) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }
    expect(screen.getAllByText(/Hood suppression inspection overdue/).length).toBeGreaterThan(0);
    // Risk kinds include a staffing gap reported through the shift-scheduler MCP server.
    expect(screen.getByText('Staffing gap')).toBeInTheDocument();
    expect(screen.getAllByText('Stale SOP').length).toBeGreaterThan(0);
    // Interrupts surface the paused write actions for the Kitchen Manager.
    expect(screen.getAllByText('Reassign TKT-009 to Priya Nair').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Order 6 cases of canola frying oil').length).toBeGreaterThan(0);
  });

  it('shows the back-end error when the model is unavailable', async () => {
    const user = userEvent.setup();
    server.use(
      http.post('/api/agent/triage/stream', () =>
        sseResponse([
          ['meta', { model: 'qwen3:4b-q4_K_M', station: 'grill', runId: 'RUN-0099' }],
          ['node', { node: 'load_tickets', title: 'Load open tickets', status: 'loading' }],
          ['error', { message: 'Ollama is not reachable at http://localhost:11434' }],
          ['done', { elapsed_ms: 12, run_id: 'RUN-0099' }],
        ]),
      ),
    );
    renderApp('/triage', { as: 'manager' });
    await user.click(await screen.findByRole('button', { name: /Run (shift )?triage/ }));
    expect(await screen.findByText(/Ollama is not reachable/)).toBeInTheDocument();
  });
});

describe('approvals', () => {
  it('lets the kitchen manager approve a supply order with an adjusted quantity', async () => {
    const user = userEvent.setup();
    let decision: Record<string, unknown> | null = null;
    server.use(
      http.post('/api/approvals/:id/decision', async ({ request, params }) => {
        decision = { id: params.id, ...((await request.json()) as object) };
        const a = fixtures.approvals.find((x) => x.id === params.id)!;
        return HttpResponse.json({ ...a, status: 'approved', decidedByName: 'Elena Petrova' });
      }),
    );
    renderApp('/approvals', { as: 'manager' });
    const card = (await screen.findByText('Order 6 cases of canola frying oil')).closest(
      '.ant-card',
    ) as HTMLElement;
    expect(within(card).getByText('Coastal Restaurant Supply')).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: /Approve/ }));

    const dialog = await screen.findByRole('dialog');
    const qty = within(dialog).getByRole('spinbutton');
    expect(qty).toHaveValue('6');
    await user.clear(qty);
    await user.type(qty, '5');
    await user.type(within(dialog).getByRole('textbox'), 'Five cases cover the weekend.');
    await user.click(within(dialog).getByRole('button', { name: /Approve/ }));

    await waitFor(() => expect(decision).not.toBeNull());
    expect(decision).toMatchObject({
      id: 'APR-104',
      decision: 'approved',
      quantity: 5,
      reason: 'Five cases cover the weekend.',
    });
  });

  it('is view-only for sous chefs and admins', async () => {
    renderApp('/approvals', { as: 'admin' });
    expect(await screen.findByText('View only')).toBeInTheDocument();
    await screen.findByText('Order 6 cases of canola frying oil');
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument();
    expect(screen.getByText('Decision history')).toBeInTheDocument();
    expect(screen.getByText('APR-102')).toBeInTheDocument();
  });
});
