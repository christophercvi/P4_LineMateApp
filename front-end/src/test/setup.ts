import '@testing-library/jest-dom/vitest';
import { createElement } from 'react';
import { cleanup, configure } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, vi } from 'vitest';
import { useLookups } from '@/api/lookups';
import { useSession, useUi } from '@/auth/store';
import { server } from './server';

// --- jsdom gaps that antd and Ant Design X rely on -------------------------------------------
/** Desktop viewport width used to answer antd's responsive breakpoint queries. */
export const VIEWPORT_WIDTH = 1440;
const mediaMatches = (query: string) => {
  const min = /min-width:\s*(\d+)px/.exec(query);
  const max = /max-width:\s*(\d+(?:\.\d+)?)px/.exec(query);
  if (!min && !max) return false;
  return (!min || VIEWPORT_WIDTH >= Number(min[1])) && (!max || VIEWPORT_WIDTH <= Number(max[1]));
};
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: mediaMatches(query),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});

class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}
window.ResizeObserver ??= NoopObserver as unknown as typeof ResizeObserver;
window.IntersectionObserver ??= NoopObserver as unknown as typeof IntersectionObserver;
window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
Element.prototype.scrollIntoView = vi.fn();
if (!URL.createObjectURL) URL.createObjectURL = vi.fn(() => 'blob:preview');
if (!URL.revokeObjectURL) URL.revokeObjectURL = vi.fn();

// antd asks for pseudo-element styles, which jsdom does not implement.
const realGetComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = ((elt: Element) =>
  realGetComputedStyle(elt)) as typeof window.getComputedStyle;

// Charts draw on <canvas>, which jsdom cannot render; replace them with labelled placeholders.
vi.mock('@ant-design/charts', () => {
  const stub = (name: string) => (props: { data?: unknown[] }) =>
    createElement('div', {
      'data-testid': `chart-${name}`,
      'data-points': Array.isArray(props.data) ? props.data.length : 0,
    });
  return {
    Bar: stub('bar'),
    Column: stub('column'),
    Heatmap: stub('heatmap'),
    Pie: stub('pie'),
    Sankey: stub('sankey'),
    Tiny: {
      Line: stub('tiny-line'),
      Column: stub('tiny-column'),
      Ring: stub('tiny-ring'),
      Progress: stub('tiny-progress'),
      Area: stub('tiny-area'),
    },
  };
});

// Ant Design X checks for the browser Notification API at import time.
class NotificationStub {
  static permission: NotificationPermission = 'default';
  static requestPermission = async (): Promise<NotificationPermission> => 'default';
  close() {}
}
Object.defineProperty(window, 'Notification', { writable: true, value: NotificationStub });

// Pages are lazy-loaded route chunks; the first import in a worker can take a few seconds.
configure({ asyncUtilTimeout: 5000 });

// --- MSW lifecycle ------------------------------------------------------------------------------
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  useSession.setState({ token: null, claims: null });
  useUi.setState({ mode: 'light', siderCollapsed: false });
  useLookups.setState({ stations: [], crew: [], loaded: false });
  localStorage.clear();
});
afterAll(() => server.close());
