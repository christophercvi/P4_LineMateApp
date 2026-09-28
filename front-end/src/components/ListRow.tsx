import type { CSSProperties, Key, ReactNode } from 'react';
import { Empty, Flex, Listy, theme } from 'antd';

/** Row layout replacing the deprecated List.Item + List.Item.Meta (antd 6.6 deprecates List in favour of Listy). */
export function ListRow({
  avatar,
  title,
  description,
  extra,
  onClick,
  active,
  style,
}: {
  avatar?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  extra?: ReactNode;
  onClick?: () => void;
  active?: boolean;
  style?: CSSProperties;
}) {
  const { token } = theme.useToken();
  return (
    <Flex
      gap={12}
      align="flex-start"
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => (e.key === 'Enter' || e.key === ' ') && onClick() : undefined}
      style={{
        padding: '10px 8px',
        borderBottom: `1px solid ${token.colorSplit}`,
        cursor: onClick ? 'pointer' : undefined,
        background: active ? token.colorPrimaryBg : undefined,
        borderRadius: active ? token.borderRadiusSM : 0,
        ...style,
      }}
    >
      {avatar}
      <Flex vertical gap={2} style={{ flex: 1, minWidth: 0 }}>
        <div style={{ color: token.colorText, fontWeight: 500 }}>{title}</div>
        {description && (
          <div style={{ color: token.colorTextSecondary, fontSize: token.fontSizeSM + 1 }}>
            {description}
          </div>
        )}
      </Flex>
      {extra && <div style={{ flex: 'none' }}>{extra}</div>}
    </Flex>
  );
}

export function RowList<T extends object>({
  items,
  rowKey,
  render,
  empty,
  height,
}: {
  items: T[] | undefined;
  rowKey: (item: T) => Key;
  render: (item: T, index: number) => ReactNode;
  empty?: ReactNode;
  height?: number;
}) {
  if (!items?.length) return <>{empty ?? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}</>;
  return (
    <Listy<T>
      items={items}
      rowKey={rowKey}
      itemRender={render}
      height={height}
      virtual={!!height}
    />
  );
}
