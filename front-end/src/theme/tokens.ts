import { theme, type ThemeConfig } from 'antd';
import type { DocCategory, FileKind, Priority, TicketStatus } from '@/api/types';

export const LOGO_URL = '/logo.png';

export const EMBER = '#C2410C';

export const PRIORITY: Record<
  Priority,
  { label: string; color: string; hex: string; rank: number }
> = {
  critical: { label: 'Critical', color: 'red', hex: '#CF1322', rank: 4 },
  high: { label: 'High', color: 'orange', hex: '#D46B08', rank: 3 },
  medium: { label: 'Medium', color: 'gold', hex: '#D4A106', rank: 2 },
  low: { label: 'Low', color: 'cyan', hex: '#13A8A8', rank: 1 },
};
export const PRIORITIES: Priority[] = ['critical', 'high', 'medium', 'low'];

export const STATUS: Record<TicketStatus, { label: string; color: string }> = {
  open: { label: 'Open', color: 'blue' },
  in_progress: { label: 'In progress', color: 'purple' },
  blocked: { label: 'Blocked', color: 'magenta' },
  resolved: { label: 'Resolved', color: 'green' },
  closed: { label: 'Closed', color: 'default' },
};
export const STATUSES: TicketStatus[] = ['open', 'in_progress', 'blocked', 'resolved', 'closed'];
export const OPEN_STATUSES: TicketStatus[] = ['open', 'in_progress', 'blocked'];

export const CATEGORY: Record<DocCategory, { label: string; color: string }> = {
  recipe: { label: 'Recipe', color: 'volcano' },
  sop: { label: 'SOP', color: 'geekblue' },
  incident: { label: 'Incident Report', color: 'magenta' },
  onboarding: { label: 'Onboarding', color: 'green' },
};
export const CATEGORIES: DocCategory[] = ['recipe', 'sop', 'incident', 'onboarding'];

/** Ant Design X FileCard preset icons. */
export const FILE_ICON: Record<FileKind, 'pdf' | 'word' | 'ppt' | 'excel' | 'image' | 'markdown'> =
  {
    pdf: 'pdf',
    docx: 'word',
    pptx: 'ppt',
    xlsx: 'excel',
    png: 'image',
    jpg: 'image',
    md: 'markdown',
    txt: 'markdown',
  };

export const STALE_DAYS = 180;

const base: ThemeConfig['token'] = {
  colorPrimary: EMBER,
  colorInfo: '#1677FF',
  borderRadius: 8,
  fontFamily:
    "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
  fontSize: 14,
};

export function buildTheme(mode: 'light' | 'dark'): ThemeConfig {
  const dark = mode === 'dark';
  return {
    algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      ...base,
      colorPrimary: dark ? '#EA580C' : EMBER,
      colorBgLayout: dark ? '#12100E' : '#FAF8F5',
      colorBgContainer: dark ? '#1C1917' : '#FFFFFF',
      colorBgElevated: dark ? '#231F1C' : '#FFFFFF',
      colorText: dark ? 'rgba(250,247,242,0.90)' : '#1F1B16',
      colorBorderSecondary: dark ? '#2E2925' : '#EEE9E2',
    },
    components: {
      Layout: {
        siderBg: dark ? '#0E0C0B' : '#1C1917',
        headerBg: dark ? '#1C1917' : '#FFFFFF',
        headerHeight: 56,
        headerPadding: '0 20px',
        triggerBg: dark ? '#0E0C0B' : '#292524',
      },
      Menu: {
        darkItemBg: dark ? '#0E0C0B' : '#1C1917',
        darkSubMenuItemBg: dark ? '#0E0C0B' : '#1C1917',
        darkItemSelectedBg: dark ? '#EA580C' : EMBER,
        darkPopupBg: '#1C1917',
      },
      Table: { cellPaddingBlockSM: 6, headerBg: dark ? '#231F1C' : '#F7F4EF' },
      Card: { headerFontSize: 15 },
      Statistic: { contentFontSize: 26 },
    },
  };
}
