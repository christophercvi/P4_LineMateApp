import { Suspense, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AutoComplete,
  Avatar,
  Badge,
  Button,
  Drawer,
  Dropdown,
  Flex,
  Grid,
  Input,
  Layout,
  Menu,
  Result,
  Skeleton,
  Spin,
  Tooltip,
  Typography,
  type MenuProps,
} from 'antd';
import {
  ApartmentOutlined,
  AuditOutlined,
  BarChartOutlined,
  BellOutlined,
  CheckSquareOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  MoonOutlined,
  NodeIndexOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SunOutlined,
  SwapOutlined,
  TagsOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { api } from '@/api/client';
import type { Approval, LmDocument, Ticket } from '@/api/types';
import { useLookups } from '@/api/lookups';
import { useAuth } from '@/auth/useAuth';
import { useUi } from '@/auth/store';
import { can, ROLE_LABEL, type Actor } from '@/auth/permissions';
import { LOGO_URL } from '@/theme/tokens';
import { StationTag } from './tags';

const { Header, Sider, Content } = Layout;

interface NavItem {
  key: string;
  label: string;
  icon: ReactNode;
  allow?: (a: Actor) => boolean;
  children?: NavItem[];
  badge?: boolean;
}

const NAV: NavItem[] = [
  { key: '/', label: 'Dashboard', icon: <DashboardOutlined /> },
  { key: '/ask', label: 'Ask LineMate', icon: <MessageOutlined />, allow: can.ask },
  { key: '/tickets', label: 'Tickets', icon: <TagsOutlined /> },
  { key: '/documents', label: 'Documents', icon: <FileTextOutlined /> },
  { key: '/triage', label: 'Shift triage', icon: <ThunderboltOutlined />, allow: can.runTriage },
  {
    key: '/approvals',
    label: 'Approvals',
    icon: <CheckSquareOutlined />,
    allow: can.viewApprovals,
    badge: true,
  },
  {
    key: 'audits',
    label: 'Audits',
    icon: <AuditOutlined />,
    allow: can.viewAudits,
    children: [
      { key: '/audits/stale', label: 'Stale documents', icon: <SafetyCertificateOutlined /> },
      {
        key: '/audits/ownership',
        label: 'Ownership',
        icon: <SwapOutlined />,
        allow: can.viewOwnershipAudit,
      },
    ],
  },
  {
    key: '/analytics',
    label: 'Workload analytics',
    icon: <BarChartOutlined />,
    allow: can.viewAnalytics,
  },
  {
    key: 'agent',
    label: 'Agent inspector',
    icon: <RobotOutlined />,
    allow: can.viewAgentRuns,
    children: [
      { key: '/agent/graph', label: 'Graph', icon: <ApartmentOutlined /> },
      { key: '/agent/runs', label: 'Runs', icon: <NodeIndexOutlined /> },
    ],
  },
  { key: '/mcp', label: 'MCP console', icon: <CloudServerOutlined />, allow: can.viewMcp },
  {
    key: 'admin',
    label: 'Admin',
    icon: <TeamOutlined />,
    allow: can.admin,
    children: [
      { key: '/admin/users', label: 'Users', icon: <UserOutlined /> },
      { key: '/admin/models', label: 'Models', icon: <RobotOutlined /> },
      { key: '/admin/vector-store', label: 'Vector store', icon: <DatabaseOutlined /> },
    ],
  },
];

function GlobalSearch() {
  const [q, setQ] = useState('');
  const navigate = useNavigate();
  const enabled = q.trim().length >= 2;
  const tickets = useQuery({
    queryKey: ['search', 'tickets', q],
    queryFn: () => api<Ticket[]>('/api/tickets', { query: { q } }),
    enabled,
  });
  const docs = useQuery({
    queryKey: ['search', 'docs', q],
    queryFn: () => api<{ items: LmDocument[] }>('/api/documents', { query: { q } }),
    enabled,
  });
  const options = enabled
    ? [
        {
          label: 'Tickets',
          options: (tickets.data ?? [])
            .slice(0, 5)
            .map((t) => ({ value: `/tickets/${t.id}`, label: `${t.id} · ${t.title}` })),
        },
        {
          label: 'Documents',
          options: (docs.data?.items ?? [])
            .slice(0, 5)
            .map((d) => ({ value: `/documents/${d.id}`, label: `${d.id} · ${d.title}` })),
        },
      ].filter((g) => g.options.length)
    : [];
  return (
    <AutoComplete
      style={{ width: 'min(360px, 42vw)' }}
      options={options}
      value={q}
      onChange={setQ}
      onSelect={(v: string) => {
        setQ('');
        navigate(v);
      }}
    >
      <Input prefix={<SearchOutlined />} placeholder="Search tickets and documents…" allowClear />
    </AutoComplete>
  );
}

export default function AppLayout() {
  const { claims, actor, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { mode, toggleMode, siderCollapsed, setSiderCollapsed } = useUi();
  const screens = Grid.useBreakpoint();
  const lookupsLoaded = useLookups((s) => s.loaded);
  const loadLookups = useLookups((s) => s.load);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [mobileNav, setMobileNav] = useState(false);

  useEffect(() => {
    if (actor && !lookupsLoaded) loadLookups().catch((e: Error) => setLookupError(e.message));
  }, [actor, lookupsLoaded, loadLookups]);

  const approvals = useQuery({
    queryKey: ['approvals'],
    queryFn: () => api<Approval[]>('/api/approvals'),
    enabled: !!actor && can.viewApprovals(actor),
    refetchInterval: 20_000,
  });
  const pending = approvals.data?.filter((a) => a.status === 'pending').length ?? 0;

  const items = useMemo(() => {
    if (!actor) return [] as MenuProps['items'];
    const toItem = (n: NavItem): NonNullable<MenuProps['items']>[number] | null => {
      if (n.allow && !n.allow(actor)) return null;
      const label =
        n.badge && pending ? (
          <Flex justify="space-between" align="center">
            {n.label}
            <Badge count={pending} size="small" />
          </Flex>
        ) : (
          n.label
        );
      return n.children
        ? {
            key: n.key,
            icon: n.icon,
            label,
            children: n.children.map(toItem).filter(Boolean) as MenuProps['items'],
          }
        : { key: n.key, icon: n.icon, label };
    };
    return NAV.map(toItem).filter(Boolean) as MenuProps['items'];
  }, [actor, pending]);

  if (!actor || !claims)
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (lookupError)
    return (
      <Result
        status="warning"
        title="LineMate can't reach the back-end"
        subTitle={lookupError}
        extra={
          <Button
            type="primary"
            onClick={() => {
              setLookupError(null);
              void loadLookups().catch((e: Error) => setLookupError(e.message));
            }}
          >
            Retry
          </Button>
        }
      />
    );
  if (!lookupsLoaded)
    return (
      <Flex align="center" justify="center" style={{ minHeight: '100vh' }}>
        <Spin size="large" description="Loading LineMate…">
          <div style={{ width: 120, height: 60 }} />
        </Spin>
      </Flex>
    );

  const path = location.pathname;
  const selected = NAV.flatMap((n) => (n.children ? n.children : [n]))
    .map((n) => n.key)
    .filter((k) => (k === '/' ? path === '/' : path === k || path.startsWith(`${k}/`)))
    .sort((a, b) => b.length - a.length)[0];
  const openKey = NAV.find((n) => n.children?.some((c) => c.key === selected))?.key;

  const userMenu: MenuProps['items'] = [
    {
      key: 'who',
      disabled: true,
      label: (
        <Typography.Text type="secondary">
          {claims.name} · {ROLE_LABEL[claims.role]}
        </Typography.Text>
      ),
    },
    { type: 'divider' },
    { key: 'logout', icon: <LogoutOutlined />, label: 'Sign out', danger: true },
  ];

  const onUserMenu: MenuProps['onClick'] = ({ key }) => {
    if (key === 'logout') {
      logout();
      qc.clear();
      useLookups.setState({ loaded: false });
      navigate('/login');
    }
  };

  const isMobile = !screens.md;
  const collapsed = isMobile ? false : siderCollapsed || !screens.lg;

  const brand = (
    <Flex align="center" gap={10} style={{ height: 56, padding: collapsed ? '0 16px' : '0 18px' }}>
      <img
        src={LOGO_URL}
        alt="LineMate"
        width={30}
        height={30}
        style={{ borderRadius: 8, flex: 'none' }}
      />
      {!collapsed && (
        <Flex vertical style={{ lineHeight: 1.1 }}>
          <Typography.Text strong style={{ color: '#FAF7F2', fontSize: 16 }}>
            LineMate
          </Typography.Text>
          <Typography.Text style={{ color: 'rgba(250,247,242,0.55)', fontSize: 11 }}>
            Hearthline kitchen ops
          </Typography.Text>
        </Flex>
      )}
    </Flex>
  );
  const nav = (
    <Menu
      theme="dark"
      mode="inline"
      items={items}
      selectedKeys={selected ? [selected] : []}
      defaultOpenKeys={openKey ? [openKey] : ['audits']}
      onClick={({ key }) => {
        navigate(key);
        setMobileNav(false);
      }}
      style={{ borderInlineEnd: 0 }}
    />
  );

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {isMobile ? (
        <Drawer
          placement="left"
          open={mobileNav}
          onClose={() => setMobileNav(false)}
          size={260}
          closable={false}
          styles={{ body: { padding: 0, background: '#1C1917' }, header: { display: 'none' } }}
        >
          {brand}
          {nav}
        </Drawer>
      ) : (
        <Sider
          width={232}
          collapsedWidth={64}
          collapsible
          trigger={null}
          collapsed={collapsed}
          style={{ position: 'sticky', top: 0, height: '100vh', overflow: 'auto' }}
        >
          {brand}
          {nav}
        </Sider>
      )}
      <Layout>
        <Header
          style={{
            position: 'sticky',
            top: 0,
            zIndex: 10,
            borderBottom: '1px solid rgba(0,0,0,0.06)',
            lineHeight: 'normal',
          }}
        >
          <Flex align="center" justify="space-between" style={{ height: '100%' }} gap={12}>
            <Flex align="center" gap={12}>
              <Button
                type="text"
                aria-label="Toggle navigation"
                icon={collapsed || isMobile ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                onClick={() => (isMobile ? setMobileNav(true) : setSiderCollapsed(!siderCollapsed))}
              />
              <GlobalSearch />
            </Flex>
            <Flex align="center" gap={8}>
              <span className="lm-hide-mobile">
                <StationTag value={actor.station} />
              </span>
              {can.viewApprovals(actor) && (
                <Tooltip title={`${pending} pending approval${pending === 1 ? '' : 's'}`}>
                  <Badge count={pending} size="small" offset={[-4, 4]}>
                    <Button
                      type="text"
                      aria-label="Approvals"
                      icon={<BellOutlined />}
                      onClick={() => navigate('/approvals')}
                    />
                  </Badge>
                </Tooltip>
              )}
              <Tooltip title={mode === 'light' ? 'Dark mode' : 'Light mode'}>
                <Button
                  type="text"
                  aria-label="Toggle theme"
                  icon={mode === 'light' ? <MoonOutlined /> : <SunOutlined />}
                  onClick={toggleMode}
                />
              </Tooltip>
              <Dropdown menu={{ items: userMenu, onClick: onUserMenu }} trigger={['click']}>
                <Flex align="center" gap={8} style={{ cursor: 'pointer' }}>
                  <Avatar style={{ background: '#C2410C' }}>
                    {claims.name
                      .split(' ')
                      .map((p) => p[0])
                      .join('')
                      .slice(0, 2)}
                  </Avatar>
                  <Flex vertical className="lm-hide-mobile" style={{ lineHeight: 1.2 }}>
                    <Typography.Text strong style={{ fontSize: 13 }}>
                      {claims.name}
                    </Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {ROLE_LABEL[claims.role]}
                    </Typography.Text>
                  </Flex>
                </Flex>
              </Dropdown>
            </Flex>
          </Flex>
        </Header>
        <Content
          style={{ padding: screens.md ? 24 : 12, maxWidth: 1480, width: '100%', margin: '0 auto' }}
        >
          <Suspense fallback={<Skeleton active paragraph={{ rows: 10 }} />}>
            <Outlet />
          </Suspense>
        </Content>
      </Layout>
    </Layout>
  );
}
