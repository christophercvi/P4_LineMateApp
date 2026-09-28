import { lazy, type ComponentType, type LazyExoticComponent } from 'react';
import { createBrowserRouter, type RouteObject } from 'react-router-dom';
import AppLayout from '@/components/AppLayout';
import { RequireRole } from '@/auth/RequireRole';
import { can, type Actor } from '@/auth/permissions';
import Login from '@/pages/Login';
import Register from '@/pages/Register';
import Forbidden from '@/pages/Forbidden';
import NotFound from '@/pages/NotFound';

const Dashboard = lazy(() => import('@/pages/Dashboard'));
const DocumentList = lazy(() => import('@/pages/documents/DocumentList'));
const DocumentDetail = lazy(() => import('@/pages/documents/DocumentDetail'));
const TicketList = lazy(() => import('@/pages/tickets/TicketList'));
const TicketDetail = lazy(() => import('@/pages/tickets/TicketDetail'));
const StaleAudit = lazy(() => import('@/pages/audits/StaleAudit'));
const OwnershipAudit = lazy(() => import('@/pages/audits/OwnershipAudit'));
const Analytics = lazy(() => import('@/pages/Analytics'));
const Ask = lazy(() => import('@/pages/ask/Ask'));
const Triage = lazy(() => import('@/pages/Triage'));
const Approvals = lazy(() => import('@/pages/Approvals'));
const AgentGraph = lazy(() => import('@/pages/agent/AgentGraph'));
const AgentRuns = lazy(() => import('@/pages/agent/AgentRuns'));
const Mcp = lazy(() => import('@/pages/Mcp'));
const AdminUsers = lazy(() => import('@/pages/admin/AdminUsers'));
const AdminModels = lazy(() => import('@/pages/admin/AdminModels'));
const VectorStore = lazy(() => import('@/pages/admin/VectorStore'));

const guard = (
  C: LazyExoticComponent<ComponentType>,
  allow: (a: Actor) => boolean,
  reason: string,
) => (
  <RequireRole allow={allow} reason={reason}>
    <C />
  </RequireRole>
);

export const routes: RouteObject[] = [
  { path: '/login', element: <Login /> },
  { path: '/register', element: <Register /> },
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'ask', element: guard(Ask, can.ask, 'Service accounts use MCP tools, not chat.') },
      { path: 'documents', element: <DocumentList /> },
      { path: 'documents/:id', element: <DocumentDetail /> },
      { path: 'tickets', element: <TicketList /> },
      { path: 'tickets/:id', element: <TicketDetail /> },
      {
        path: 'audits/stale',
        element: guard(
          StaleAudit,
          can.viewAudits,
          'The stale-document audit is for Sous Chefs, the Kitchen Manager and Admins.',
        ),
      },
      {
        path: 'audits/ownership',
        element: guard(
          OwnershipAudit,
          can.viewOwnershipAudit,
          'The ownership audit is for Sous Chefs and the Kitchen Manager, who reassign tickets.',
        ),
      },
      {
        path: 'analytics',
        element: guard(
          Analytics,
          can.viewAnalytics,
          'Workload analytics are for Sous Chefs, the Kitchen Manager and Admins.',
        ),
      },
      {
        path: 'triage',
        element: guard(
          Triage,
          can.runTriage,
          'Triage is run by Sous Chefs and the Kitchen Manager.',
        ),
      },
      {
        path: 'approvals',
        element: guard(
          Approvals,
          can.viewApprovals,
          'Approvals are visible to Sous Chefs, the Kitchen Manager and Admins.',
        ),
      },
      {
        path: 'agent/graph',
        element: guard(
          AgentGraph,
          can.viewAgentRuns,
          'The agent inspector is for Sous Chefs, the Kitchen Manager and Admins.',
        ),
      },
      {
        path: 'agent/runs',
        element: guard(
          AgentRuns,
          can.viewAgentRuns,
          'The agent inspector is for Sous Chefs, the Kitchen Manager and Admins.',
        ),
      },
      {
        path: 'mcp',
        element: guard(
          Mcp,
          can.viewMcp,
          'The MCP console is for Sous Chefs, the Kitchen Manager and Admins.',
        ),
      },
      {
        path: 'admin/users',
        element: guard(AdminUsers, can.admin, 'User management is for Admins.'),
      },
      {
        path: 'admin/models',
        element: guard(AdminModels, can.admin, 'The model registry is for Admins.'),
      },
      {
        path: 'admin/vector-store',
        element: guard(VectorStore, can.admin, 'The vector store console is for Admins.'),
      },
      { path: '403', element: <Forbidden /> },
      { path: '*', element: <NotFound /> },
    ],
  },
];

export const router = createBrowserRouter(routes);
