import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { sseResponse } from '@/test/handlers';
import { renderApp } from '@/test/render';
import { server } from '@/test/server';

const coolingSources = [
  {
    docId: 'DOC-SOP-003',
    title: 'Cooling Hot Foods (Two-Stage Cooling)',
    category: 'sop',
    station: 'prep',
    lastReviewed: '2026-02-03',
    daysSinceReview: 240,
    stale: true,
    score: 0.82,
    snippet: 'Cool from 135°F to 70°F within 2 hours…',
  },
  {
    docId: 'DOC-ONB-001',
    title: 'New Cook Orientation',
    category: 'onboarding',
    station: 'grill',
    lastReviewed: '2026-08-20',
    daysSinceReview: 42,
    stale: false,
    score: 0.61,
    snippet: 'Log every cooling batch…',
  },
];

function answerStream(question: string, extra: [string, unknown][] = []): [string, unknown][] {
  return [
    [
      'meta',
      {
        model: 'llama3.2:3b',
        reasoning: false,
        thinking: 'none',
        memoryTurns: 0,
        hiddenSources: 0,
        runId: 'RUN-0101',
        conversationId: 'c-test',
      },
    ],
    ['step', { key: 'retrieve', title: 'Search the knowledge base', status: 'loading' }],
    [
      'step',
      {
        key: 'retrieve',
        title: 'Search the knowledge base',
        status: 'success',
        description: `2 sources for “${question.slice(0, 20)}”`,
      },
    ],
    ['sources', coolingSources],
    ['content', 'Cool chili from 135°F to 70°F within 2 hours'],
    ['content', ', then to 41°F within 4 more hours [1]. Log each check [2].'],
    ...extra,
    ['done', { elapsed_ms: 2140, run_id: 'RUN-0101' }],
  ];
}

describe('Ask LineMate', () => {
  it('offers starter prompts and the models allowed for the role', async () => {
    renderApp('/ask', { as: 'cook' });
    expect(
      await screen.findByText('How do I cool a stockpot of chili safely?'),
    ).toBeInTheDocument();
    expect(screen.getByText(/Signed in as Line Cook/)).toBeInTheDocument();
    expect((await screen.findAllByText(/llama3\.2:3b/)).length).toBeGreaterThan(0);
  });

  it('streams a cited answer with sources and a stale-document warning', async () => {
    const user = userEvent.setup();
    let sent: Record<string, unknown> | null = null;
    server.use(
      http.post('/api/ask/stream', async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return sseResponse(answerStream(String(sent.question)));
      }),
    );
    renderApp('/ask', { as: 'cook' });
    await user.click(await screen.findByText('How do I cool a stockpot of chili safely?'));

    expect(await screen.findByText(/then to 41°F within 4 more hours/)).toBeInTheDocument();
    expect(sent).toMatchObject({
      question: 'How do I cool a stockpot of chili safely?',
      model: 'llama3.2:3b',
      reasoning: false,
      history: [],
    });
    expect(String(sent!.conversationId)).toMatch(/^c-/);
    expect(await screen.findByText('2 sources')).toBeInTheDocument();
    expect(screen.getByText(/DOC-SOP-003 was last reviewed 240 days ago/)).toBeInTheDocument();
    expect(screen.getByText(/run RUN-0101/)).toBeInTheDocument();
  });

  it('shows streamed reasoning when the model thinks', async () => {
    const user = userEvent.setup();
    server.use(
      http.post('/api/ask/stream', () =>
        sseResponse([
          [
            'meta',
            {
              model: 'qwen3:4b-q4_K_M',
              reasoning: true,
              thinking: 'toggle',
              memoryTurns: 0,
              hiddenSources: 0,
              runId: 'RUN-0102',
              conversationId: 'c-test',
            },
          ],
          ['reasoning', 'The cook needs the two-stage rule. '],
          ['reasoning', 'Cite the cooling SOP.'],
          ['sources', coolingSources.slice(0, 1)],
          ['content', 'Use shallow hotel pans and an ice bath [1].'],
          ['done', { elapsed_ms: 5200, reasoning_ms: 1800, run_id: 'RUN-0102' }],
        ]),
      ),
    );
    renderApp('/ask', { as: 'sous' });
    const box = await screen.findByPlaceholderText(/Ask about a procedure/);
    await user.type(box, 'Fastest way to cool rice?{Enter}');
    expect(await screen.findByText(/Use shallow hotel pans/)).toBeInTheDocument();
    expect(await screen.findByText(/Reasoning \(1\.8 ?s\)/)).toBeInTheDocument();
  });

  it('renders an approval card when the agent pauses for a supply order', async () => {
    const user = userEvent.setup();
    server.use(
      http.post('/api/ask/stream', () =>
        sseResponse(
          answerStream('fryer oil', [
            [
              'interrupt',
              {
                approvalId: 'APR-104',
                kind: 'draft_supply_order',
                title: 'Order 6 cases of canola frying oil',
                summary: 'Stock is 1 case (par 4) after a short delivery.',
                payload: {
                  supplier: 'Coastal Restaurant Supply',
                  item: 'Canola frying oil, 35 lb',
                  sku: 'CAN-OIL-35',
                  quantity: 6,
                  unit: 'case',
                  unitPrice: 42.5,
                  total: 255,
                  neededBy: '2026-10-03',
                  ticketId: 'TKT-005',
                },
                canApprove: false,
              },
            ],
          ]),
        ),
      ),
    );
    renderApp('/ask', { as: 'cook' });
    await user.click(await screen.findByText(/How often should we change the fryer oil/));
    expect(await screen.findByText('Order 6 cases of canola frying oil')).toBeInTheDocument();
    expect(screen.getByText('Only the Kitchen Manager can approve this.')).toBeInTheDocument();
  });

  it('turns a failed stream into a readable error message', async () => {
    const user = userEvent.setup();
    server.use(
      http.post('/api/ask/stream', () =>
        sseResponse([
          [
            'meta',
            {
              model: 'llama3.2:3b',
              reasoning: false,
              thinking: 'none',
              memoryTurns: 0,
              hiddenSources: 0,
              runId: 'RUN-0103',
              conversationId: 'c-test',
            },
          ],
          ['error', { message: 'Model llama3.2:3b is not installed in Ollama' }],
        ]),
      ),
    );
    renderApp('/ask', { as: 'cook' });
    const box = await screen.findByPlaceholderText(/Ask about a procedure/);
    await user.type(box, 'Knife safety basics?{Enter}');
    await waitFor(() => expect(screen.getByText(/is not installed in Ollama/)).toBeInTheDocument());
  });
});

describe('Ask LineMate conversations and models', () => {
  const saved = [
    {
      id: 'c-mo2x9-fryer',
      title: 'Fryer oil changes',
      model: 'llama3.2:3b',
      createdAt: '2026-09-30T18:00:00Z',
      updatedAt: new Date().toISOString(),
      messages: 2,
    },
  ];

  it('lists every chat-capable Ollama model, including multimodal ones', async () => {
    const user = userEvent.setup();
    renderApp('/ask', { as: 'cook' });
    await user.click(await screen.findByRole('combobox', { name: 'Model' }));
    for (const name of ['llama3.2:3b', 'qwen3:4b-q4_K_M', 'gemma4:e2b']) {
      expect(
        screen.getAllByTitle(name).some((el) => el.classList.contains('ant-select-item-option')),
      ).toBe(true);
    }
  });

  it('reopens a saved chat with its stored history', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('/api/conversations', () => HttpResponse.json(saved)),
      http.get('/api/conversations/:id', ({ params }) =>
        HttpResponse.json({
          ...saved[0],
          id: params.id,
          memorySummary: null,
          history: [
            {
              role: 'user',
              content: 'When do we change the fryer oil?',
              extra: {},
              createdAt: '2026-09-30T18:00:00Z',
            },
            {
              role: 'assistant',
              content: 'Change it when the test strip reads 24% polar compounds [1].',
              extra: { model: 'llama3.2:3b', sources: coolingSources.slice(0, 1) },
              createdAt: '2026-09-30T18:00:05Z',
            },
          ],
        }),
      ),
    );
    renderApp('/ask', { as: 'cook' });
    await user.click(await screen.findByText('Fryer oil changes'));
    expect(await screen.findByText(/24% polar compounds/)).toBeInTheDocument();
    expect(screen.getByText('When do we change the fryer oil?')).toBeInTheDocument();
  });

  it('renames a saved chat on the server', async () => {
    const user = userEvent.setup();
    let renamed: unknown = null;
    server.use(
      http.get('/api/conversations', () => HttpResponse.json(saved)),
      http.patch('/api/conversations/:id', async ({ request, params }) => {
        renamed = { id: params.id, ...((await request.json()) as object) };
        return HttpResponse.json({ ...saved[0], title: 'Fryer oil schedule' });
      }),
    );
    renderApp('/ask', { as: 'cook' });
    const item = (await screen.findByText('Fryer oil changes')).closest('li') as HTMLElement;
    await user.hover(item);
    await user.click(within(item).getByRole('img', { name: /ellipsis/ }));
    await user.click(await screen.findByText('Rename'));
    const input = await screen.findByRole('textbox', { name: 'Chat name' });
    await user.clear(input);
    await user.type(input, 'Fryer oil schedule{Enter}');
    await waitFor(() =>
      expect(renamed).toEqual({ id: 'c-mo2x9-fryer', title: 'Fryer oil schedule' }),
    );
    expect(await screen.findByText('Fryer oil schedule')).toBeInTheDocument();
  });

  it('lets an admin compare two models side by side', async () => {
    const user = userEvent.setup();
    const asked: string[] = [];
    server.use(
      http.post('/api/ask/stream', async ({ request }) => {
        const body = (await request.json()) as { model: string; question: string };
        asked.push(body.model);
        return sseResponse([
          [
            'meta',
            {
              model: body.model,
              reasoning: false,
              thinking: 'none',
              memoryTurns: 0,
              hiddenSources: 0,
              runId: `RUN-${asked.length}`,
              conversationId: 'c-test',
            },
          ],
          ['content', `Answer from ${body.model}`],
          ['done', { elapsed_ms: 900, run_id: `RUN-${asked.length}` }],
        ]);
      }),
    );
    renderApp('/ask', { as: 'admin' });
    await user.click(await screen.findByRole('switch', { name: 'Compare models' }));
    await user.type(
      screen.getByPlaceholderText(/Ask about a procedure/),
      'Allergen matrix for the financiers?{Enter}',
    );
    expect(await screen.findByText('Answer from llama3.2:3b')).toBeInTheDocument();
    expect(await screen.findByText('Answer from qwen3:4b-q4_K_M')).toBeInTheDocument();
    expect(asked.sort()).toEqual(['llama3.2:3b', 'qwen3:4b-q4_K_M']);
  });
});
