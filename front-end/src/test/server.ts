import { setupServer } from 'msw/node';
import { handlers } from './handlers';

/** One MSW server for the whole run; tests add per-case overrides with `server.use(...)`. */
export const server = setupServer(...handlers);
