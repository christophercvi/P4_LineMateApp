import { describe, expect, it } from 'vitest';
import type { LmDocument, Ticket } from '@/api/types';
import { can, denyReason, ROLE_LABEL, type Actor } from '@/auth/permissions';

const cook: Actor = {
  role: 'line_cook',
  station: 'grill',
  crewMemberId: 'CM-01',
  userId: 'u-marco',
};
const sous: Actor = {
  role: 'sous_chef',
  station: 'grill',
  crewMemberId: 'CM-02',
  userId: 'u-priya',
};
const prepSous: Actor = {
  role: 'sous_chef',
  station: 'prep',
  crewMemberId: 'CM-06',
  userId: 'u-samuel',
};
const manager: Actor = {
  role: 'kitchen_manager',
  station: null,
  crewMemberId: 'CM-11',
  userId: 'u-elena',
};
const admin: Actor = { role: 'admin', station: null, crewMemberId: null, userId: 'u-admin' };
const service: Actor = { role: 'service', station: null, crewMemberId: null, userId: 'u-svc-mcp' };

const ticket = { id: 'TKT-001', station: 'grill', assigneeId: 'CM-01' } as Ticket;
const sop = { id: 'DOC-SOP-005', category: 'sop', station: 'grill' } as LmDocument;
const recipe = { id: 'DOC-REC-002', category: 'recipe', station: 'grill' } as LmDocument;

describe('role permissions', () => {
  it('lets every kitchen role chat, but not the MCP service account', () => {
    expect([cook, sous, manager, admin].every(can.ask)).toBe(true);
    expect(can.ask(service)).toBe(false);
  });

  it('hides incident reports from line cooks only', () => {
    expect(can.seeIncidentReports(cook)).toBe(false);
    expect([sous, manager, admin].every(can.seeIncidentReports)).toBe(true);
  });

  it('allows kitchen roles to open, comment and attach on tickets, but not admins', () => {
    for (const a of [cook, sous, manager]) {
      expect(can.createTicket(a) && can.comment(a) && can.attachToTicket(a)).toBe(true);
    }
    expect(can.createTicket(admin) || can.attachToTicket(admin)).toBe(false);
  });

  it('scopes status, priority and assignment changes', () => {
    expect(can.changeStatus(cook, ticket)).toBe(true);
    expect(can.changeStatus(cook, { ...ticket, assigneeId: 'CM-03' })).toBe(false);
    expect(can.lowerPriorityOrClose(cook, ticket)).toBe(false);
    expect(can.lowerPriorityOrClose(sous, ticket)).toBe(true);
    expect(can.lowerPriorityOrClose(prepSous, ticket)).toBe(false);
    expect(can.assign(manager, ticket) && can.assign(sous, ticket)).toBe(true);
    expect(can.assign(prepSous, ticket)).toBe(false);
  });

  it('restricts uploads: sous chefs publish non-SOP documents for their own station', () => {
    expect(can.uploadCategory(sous, 'recipe', 'grill')).toBe(true);
    expect(can.uploadCategory(sous, 'incident', 'grill')).toBe(true);
    expect(can.uploadCategory(sous, 'sop', 'grill')).toBe(false);
    expect(can.uploadCategory(sous, 'recipe', 'pastry')).toBe(false);
    expect(can.uploadCategory(manager, 'sop', 'pastry')).toBe(true);
    expect(
      can.uploadCategory(cook, 'recipe', 'grill') || can.uploadCategory(admin, 'recipe', 'grill'),
    ).toBe(false);
    expect(can.uploadAny(sous) && can.uploadAny(manager)).toBe(true);
    expect(can.replaceDocument(sous, recipe)).toBe(true);
    expect(can.replaceDocument(sous, sop)).toBe(false);
    expect(can.markReviewed(sous, sop) && !can.markReviewed(prepSous, sop)).toBe(true);
    expect(can.archiveDocument(manager) && !can.archiveDocument(sous)).toBe(true);
  });

  it('keeps approval with the kitchen manager (separation of duties for admins)', () => {
    expect(can.approve(manager)).toBe(true);
    expect([cook, sous, admin].some(can.approve)).toBe(false);
    expect(can.viewApprovals(admin) && !can.viewApprovals(cook)).toBe(true);
    expect(denyReason('approve', admin)).toMatch(/Separation of duties/);
    expect(denyReason('approve', sous)).toMatch(/Only the Kitchen Manager/);
  });

  it('gates audits, analytics, triage, agent runs, MCP and admin screens', () => {
    expect(can.viewOwnershipAudit(admin)).toBe(false);
    expect(can.viewAudits(admin) && can.viewAnalytics(admin)).toBe(true);
    expect(can.analyticsAllStations(sous)).toBe(false);
    expect(can.runTriage(sous) && can.runTriage(manager) && !can.runTriage(admin)).toBe(true);
    expect(can.viewAllRuns(admin) && !can.viewAllRuns(manager)).toBe(true);
    expect(can.callMcpTool(sous, ['sous_chef', 'kitchen_manager'])).toBe(true);
    expect(can.callMcpTool(admin, ['sous_chef'])).toBe(false);
    expect(can.manageTokens(admin) && can.admin(admin) && !can.admin(manager)).toBe(true);
  });

  it('explains every denial in plain language', () => {
    expect(denyReason('upload', sous)).toMatch(/own station/);
    expect(denyReason('upload', cook)).toContain(`${ROLE_LABEL.line_cook}s`);
    for (const kind of ['status', 'lower', 'assign', 'review', 'archive'] as const) {
      expect(denyReason(kind, cook).length).toBeGreaterThan(20);
    }
  });
});
