import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Avatar, Flex, Select, Switch, Table, Tag, Typography } from 'antd';
import dayjs from 'dayjs';
import { api, ApiError } from '@/api/client';
import type { Role, StationId } from '@/api/types';
import type { AdminUserView } from '@/api/views';
import { ROLE_LABEL } from '@/auth/permissions';
import { useActor } from '@/auth/useAuth';
import { PageHeader } from '@/components/PageHeader';
import { stations } from '@/api/lookups';

const ROLES: Role[] = ['line_cook', 'sous_chef', 'kitchen_manager', 'admin', 'service'];

export default function AdminUsers() {
  const actor = useActor();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const q = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => api<AdminUserView[]>('/api/admin/users'),
  });
  const patch = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: Partial<{ role: Role; station: StationId | null; active: boolean }>;
    }) => api(`/api/admin/users/${id}`, { method: 'PATCH', body }),
    onMutate: async ({ id, body }) => {
      await qc.cancelQueries({ queryKey: ['admin-users'] });
      const prev = qc.getQueryData<AdminUserView[]>(['admin-users']);
      qc.setQueryData<AdminUserView[]>(['admin-users'], (old) =>
        old?.map((u) => (u.id === id ? { ...u, ...body } : u)),
      );
      return { prev };
    },
    onError: (e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(['admin-users'], ctx.prev);
      message.error(e instanceof ApiError ? e.message : 'Update failed');
    },
    onSuccess: () => message.success('User updated'),
    onSettled: () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
  });

  return (
    <>
      <PageHeader
        title="Users & roles"
        subtitle="Role and station drive every permission check (JWT claims: role, station, crew_member_id)"
      />
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        title="Accounts and sign-in"
        description="New accounts join as Line Cooks. Role, station and active changes are enforced by the API immediately; the user's menus refresh at their next sign-in. Passwords are hashed with Argon2id and sessions use signed JWTs."
      />
      <Table<AdminUserView>
        rowKey="id"
        size="small"
        loading={q.isLoading}
        dataSource={q.data}
        pagination={false}
        columns={[
          {
            title: 'User',
            key: 'u',
            render: (_: unknown, u) => (
              <Flex gap={8} align="center">
                <Avatar size="small">
                  {u.displayName
                    .split(' ')
                    .map((p) => p[0])
                    .join('')
                    .slice(0, 2)}
                </Avatar>
                <Flex vertical>
                  <Typography.Text strong>
                    {u.displayName}
                    {u.id === actor.userId && <Tag style={{ marginLeft: 6 }}>you</Tag>}
                  </Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {u.username} · {u.email}
                  </Typography.Text>
                </Flex>
              </Flex>
            ),
          },
          {
            title: 'Role',
            dataIndex: 'role',
            width: 190,
            render: (r: Role, u) => (
              <Select
                size="small"
                value={r}
                style={{ width: 170 }}
                disabled={u.id === actor.userId}
                onChange={(role) => patch.mutate({ id: u.id, body: { role } })}
                options={ROLES.map((x) => ({ value: x, label: ROLE_LABEL[x] }))}
                aria-label={`Role for ${u.displayName}`}
              />
            ),
          },
          {
            title: 'Station',
            dataIndex: 'station',
            width: 170,
            render: (s: StationId | null, u) => (
              <Select
                size="small"
                value={s ?? ''}
                style={{ width: 150 }}
                onChange={(v) =>
                  patch.mutate({ id: u.id, body: { station: (v || null) as StationId | null } })
                }
                options={[
                  { value: '', label: 'All / none' },
                  ...stations().map((x) => ({ value: x.id, label: x.name })),
                ]}
                aria-label={`Station for ${u.displayName}`}
              />
            ),
          },
          {
            title: 'Crew',
            dataIndex: 'crewMemberId',
            render: (c: string | null, u) =>
              c ? (
                <span>
                  {c} <Typography.Text type="secondary">{u.crewTitle}</Typography.Text>
                </span>
              ) : (
                '—'
              ),
          },
          {
            title: 'Last login',
            dataIndex: 'lastLogin',
            render: (d: string | null) => (d ? dayjs(d).format('MMM D, HH:mm') : 'never'),
          },
          {
            title: 'Active',
            dataIndex: 'active',
            align: 'center',
            render: (a: boolean, u) => (
              <Switch
                size="small"
                checked={a}
                disabled={u.id === actor.userId}
                onChange={(active) => patch.mutate({ id: u.id, body: { active } })}
                aria-label={`Active ${u.displayName}`}
              />
            ),
          },
        ]}
      />
    </>
  );
}
