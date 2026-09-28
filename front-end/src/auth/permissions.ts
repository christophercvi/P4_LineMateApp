import type { DocCategory, LmDocument, Role, StationId, Ticket } from '@/api/types';

export interface Actor {
  role: Role;
  station: StationId | null;
  crewMemberId: string | null;
  userId: string;
}

export const ROLE_LABEL: Record<Role, string> = {
  line_cook: 'Line Cook',
  sous_chef: 'Sous Chef',
  kitchen_manager: 'Kitchen Manager',
  admin: 'Admin',
  service: 'Service (MCP)',
};

const kitchenRoles: Role[] = ['line_cook', 'sous_chef', 'kitchen_manager'];

export const can = {
  readDocuments: (_a: Actor) => true,
  ask: (a: Actor) => a.role !== 'service',
  seeIncidentReports: (a: Actor) => a.role !== 'line_cook',

  createTicket: (a: Actor) => kitchenRoles.includes(a.role),
  comment: (a: Actor) => kitchenRoles.includes(a.role),
  attachToTicket: (a: Actor) => kitchenRoles.includes(a.role),

  raisePriority: (a: Actor) => kitchenRoles.includes(a.role),
  lowerPriorityOrClose: (a: Actor, t: Ticket) =>
    a.role === 'kitchen_manager' || (a.role === 'sous_chef' && a.station === t.station),
  changeStatus: (a: Actor, t: Ticket) =>
    a.role === 'kitchen_manager' ||
    (a.role === 'sous_chef' && a.station === t.station) ||
    (a.role === 'line_cook' && t.assigneeId === a.crewMemberId),
  assign: (a: Actor, t: Ticket) =>
    a.role === 'kitchen_manager' || (a.role === 'sous_chef' && a.station === t.station),

  uploadCategory: (a: Actor, category: DocCategory, station: StationId | null) => {
    if (a.role === 'kitchen_manager') return true;
    if (a.role === 'sous_chef') return category !== 'sop' && station === a.station;
    return false;
  },
  uploadAny: (a: Actor) => a.role === 'kitchen_manager' || a.role === 'sous_chef',
  replaceDocument: (a: Actor, d: LmDocument) => can.uploadCategory(a, d.category, d.station),
  markReviewed: (a: Actor, d: LmDocument) =>
    a.role === 'kitchen_manager' || (a.role === 'sous_chef' && a.station === d.station),
  archiveDocument: (a: Actor) => a.role === 'kitchen_manager',

  viewAudits: (a: Actor) => ['sous_chef', 'kitchen_manager', 'admin'].includes(a.role),
  /** The ownership audit is for the people who reassign work, so Admins are excluded. */
  viewOwnershipAudit: (a: Actor) => a.role === 'sous_chef' || a.role === 'kitchen_manager',
  actOnAudits: (a: Actor) => a.role === 'sous_chef' || a.role === 'kitchen_manager',
  viewAnalytics: (a: Actor) => ['sous_chef', 'kitchen_manager', 'admin'].includes(a.role),
  analyticsAllStations: (a: Actor) => a.role === 'kitchen_manager' || a.role === 'admin',

  runTriage: (a: Actor) => a.role === 'sous_chef' || a.role === 'kitchen_manager',
  viewApprovals: (a: Actor) => ['sous_chef', 'kitchen_manager', 'admin'].includes(a.role),
  approve: (a: Actor) => a.role === 'kitchen_manager',
  requestApproval: (a: Actor) => a.role === 'sous_chef' || a.role === 'kitchen_manager',

  viewAgentRuns: (a: Actor) => ['sous_chef', 'kitchen_manager', 'admin'].includes(a.role),
  viewAllRuns: (a: Actor) => a.role === 'admin',
  viewMcp: (a: Actor) => ['sous_chef', 'kitchen_manager', 'admin'].includes(a.role),
  /** A tool may only be called by the roles it declares. */
  callMcpTool: (a: Actor, roles: readonly Role[]) => roles.includes(a.role),
  manageTokens: (a: Actor) => a.role === 'admin',
  admin: (a: Actor) => a.role === 'admin',
};

export function denyReason(
  kind: 'upload' | 'approve' | 'status' | 'lower' | 'assign' | 'review' | 'archive',
  a: Actor,
): string {
  const who = ROLE_LABEL[a.role];
  switch (kind) {
    case 'upload':
      return a.role === 'sous_chef'
        ? 'Sous Chefs can upload Recipes, Onboarding and Incident Reports for their own station. SOPs are published by the Kitchen Manager.'
        : `${who}s can't publish to the knowledge base — raise a ticket or comment instead.`;
    case 'approve':
      return a.role === 'admin'
        ? 'Separation of duties: admins run the system but do not approve kitchen actions.'
        : 'Only the Kitchen Manager can approve. You can view the request and add context.';
    case 'status':
      return 'You can change the status of tickets assigned to you. Sous Chefs manage their own station.';
    case 'lower':
      return 'Anyone can escalate, but only a Sous Chef (own station) or the Kitchen Manager can lower priority or close.';
    case 'assign':
      return 'Assigning is done by the Sous Chef of the station or the Kitchen Manager.';
    case 'review':
      return "Reviews are signed off by the station's Sous Chef or the Kitchen Manager.";
    case 'archive':
      return 'Only the Kitchen Manager can archive documents.';
  }
}
