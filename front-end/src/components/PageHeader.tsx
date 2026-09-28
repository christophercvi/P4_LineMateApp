import type { ReactNode } from 'react';
import { Breadcrumb, Flex, Typography } from 'antd';
import { Link } from 'react-router-dom';

export function PageHeader({
  title,
  subtitle,
  extra,
  crumbs,
  tags,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  extra?: ReactNode;
  crumbs?: { title: string; to?: string }[];
  tags?: ReactNode;
}) {
  return (
    <div style={{ marginBottom: 16 }}>
      {crumbs && (
        <Breadcrumb
          style={{ marginBottom: 8 }}
          items={crumbs.map((c) => ({ title: c.to ? <Link to={c.to}>{c.title}</Link> : c.title }))}
        />
      )}
      <Flex justify="space-between" align="flex-start" gap={16} wrap>
        <div style={{ minWidth: 0, flex: '1 1 320px' }}>
          <Flex align="center" gap={10} wrap>
            <Typography.Title level={3} style={{ margin: 0 }}>
              {title}
            </Typography.Title>
            {tags}
          </Flex>
          {subtitle && (
            <Typography.Paragraph type="secondary" style={{ margin: '4px 0 0' }}>
              {subtitle}
            </Typography.Paragraph>
          )}
        </div>
        {extra && (
          <Flex gap={8} wrap>
            {extra}
          </Flex>
        )}
      </Flex>
    </div>
  );
}
